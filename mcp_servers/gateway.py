"""MCP Gateway — FastAPI 统一入口

职责：
  1. 注册 3 个 MCP Server（in-process），按工具名路由
  2. 提供 4 个 REST API 端点（面试业务）
  3. 挂载 Gradio Web UI（/ui）
  4. 鉴权中间件（Bearer Token）
  5. 限流中间件（令牌桶，60 req/min）
  6. SSE transport（MCP 协议兼容）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount

from config import config
from mcp_servers.mcp_aggregator import (
    MCP_MOUNT_PREFIX,
    allowed_hosts_for,
    build_aggregate_mcp,
    session_lifespan,
)
from memory.session_store import SessionStore
from models.interview import InterviewState, InterviewStatus

logger = logging.getLogger("gateway")

# 单一版本来源，避免多处取值不一致
VERSION = "0.5.0"


# ═══════════════════════════════════════════════════════════
# 已知第三方噪音抑制
# ═══════════════════════════════════════════════════════════
#
# 现象：客户端（桌面 Agent 等）在初始化后发送 DELETE 终止 session 时，
# 可能仍有并发的 POST 在途。MCP 的 terminate() 会关闭 read stream，
# 在途 POST 写该流即抛 anyio.ClosedResourceError。
#
# 库自身的异常处理（mcp/server/streamable_http.py）有两处缺口：
#   1. 第 656 行 `await writer.send(Exception(err))` 未做保护，
#      而 writer 此时已关闭 → 抛出第二个 ClosedResourceError
#   2. 该二次异常无人捕获，冒泡到 ASGI 层，uvicorn 打印整段 traceback
#
# 客户端此刻已经断开，服务端无法再送出任何响应，因此这条 traceback
# 既不影响功能也不可修复。此处定向过滤，只匹配该已知签名，
# 其他 ASGI 异常照常打印。
_MCP_NOISE_SIGNATURE = "Error handling POST request"


class _SuppressMcpTransportNoise(logging.Filter):
    """过滤 MCP 传输层向已关闭流回传结果时的已知异常噪音。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 格式化异常不应影响日志
            return True
        return not ("ClosedResourceError" in message and _MCP_NOISE_SIGNATURE in message)


# 需要挂过滤器的日志器。这两条 traceback 来自**两个不同的 logger**，
# 必须同时覆盖，只挂一个只能拦住一半：
#   ① mcp.server.streamable_http —— 库自己 logger.exception("Error handling POST request")
#      （见 mcp/server/streamable_http.py 的 `logger = logging.getLogger(__name__)`）
#   ② uvicorn.error —— 二次异常冒泡到 ASGI 后由 uvicorn 打印
_NOISE_FILTER_LOGGERS = ("mcp.server.streamable_http", "uvicorn.error")


def install_mcp_noise_filter() -> None:
    """把噪音过滤器挂到所有相关日志器上（幂等）。

    uvicorn 通过 disable_existing_loggers=False 预先创建了日志器，
    因此这里 addFilter 能直接生效。
    """
    for name in _NOISE_FILTER_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(f, _SuppressMcpTransportNoise) for f in target.filters):
            target.addFilter(_SuppressMcpTransportNoise())
    logger.debug("已安装 MCP 传输层噪音过滤器: %s", _NOISE_FILTER_LOGGERS)


# ═══════════════════════════════════════════════════════════
# 基础设施：鉴权 / 限流
# ═══════════════════════════════════════════════════════════

security = HTTPBearer(auto_error=False)


def verify_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """验证 Bearer Token。可通过配置关闭。"""
    if config.gateway_require_auth:
        if credentials is None:
            raise HTTPException(status_code=401, detail="缺少 Authorization header")
        token = credentials.credentials
        if token != config.gateway_api_key:
            raise HTTPException(status_code=401, detail="无效的 API Key")
    return True


class RateLimiter:
    """基于 IP 的令牌桶限流"""

    def __init__(self, max_requests: int = 60, window: float = 60.0):
        self._max = max_requests
        self._window = window
        self._buckets: dict[str, tuple[int, float]] = {}  # ip → (tokens, last_refill)

    def _cleanup(self):
        """清理过期条目"""
        now = time.time()
        stale = [ip for ip, (_, t) in self._buckets.items() if now - t > self._window * 2]
        for ip in stale:
            del self._buckets[ip]


    def allow(self, ip: str) -> bool:
        """检查 IP 是否允许通过。True = 允许。"""
        now = time.time()
        tokens, last = self._buckets.get(ip, (self._max, now))

        # 按时间比例补充 token
        elapsed = now - last
        refill = int(elapsed / self._window * self._max)
        tokens = min(self._max, tokens + refill)
        if refill > 0:
            last = now

        if tokens > 0:
            self._buckets[ip] = (tokens - 1, last)
            if len(self._buckets) > 1000:
                self._cleanup()
            return True
        else:
            self._buckets[ip] = (0, last)
            return False


rate_limiter = RateLimiter(max_requests=config.gateway_rate_limit)


# ── 熔断器 ──────────────────────────────────────────────

class CircuitBreaker:
    """熔断器：连续失败 N 次后打开，冷却期过后进入半开状态试探。

    三态模型：
      CLOSED    → 正常调用，失败计数
      OPEN      → 快速失败（503），不调用实际服务
      HALF-OPEN → 冷却期过后，允许一次试探调用
    """

    def __init__(self, name: str, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.name = name
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._failures = 0
        self._last_failure_time = 0.0
        self._total_failures = 0
        self._total_successes = 0

    @property
    def is_open(self) -> bool:
        """熔断器是否打开（快速失败）。"""
        if self._failures >= self._threshold:
            if time.time() - self._last_failure_time < self._cooldown:
                return True
            # 冷却期过 → 半开（重置计数，允许一次探测）
            self._failures = 0
        return False

    def success(self):
        """记录成功调用。"""
        self._failures = 0
        self._total_successes += 1

    def failure(self):
        """记录失败调用。"""
        self._failures += 1
        self._last_failure_time = time.time()
        self._total_failures += 1

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": "OPEN" if self.is_open else "CLOSED",
            "consecutive_failures": self._failures,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
        }


# ── Server 注册中心 ─────────────────────────────────────

class ServerRegistry:
    """管理 MCP Server 实例与其工具函数的映射（含熔断保护）。"""

    def __init__(self):
        self._servers: dict[str, object] = {}      # name → FastMCP instance
        self._tool_map: dict[str, Callable] = {}   # tool_name → callable
        self._breakers: dict[str, CircuitBreaker] = {}  # server_name → breaker

    def register(self, server, name: str):
        """注册一个 FastMCP server 实例。遍历其工具列表并注册。"""
        self._servers[name] = server
        self._breakers[name] = CircuitBreaker(name=name)
        # FastMCP 的工具存储在 server._tool_manager._tools 中
        try:
            tools = server._tool_manager._tools
            count = 0
            for tool_name, tool_obj in tools.items():
                self._tool_map[tool_name] = (tool_obj.fn, name)
                logger.info("注册工具: %s → %s", tool_name, name)
                count += 1
            logger.info("Server [%s] 注册完成，%d 个工具", name, count)
        except Exception as e:
            logger.warning("Server [%s] 注册工具列表失败: %s", name, e)

    async def call_tool(self, tool_name: str, **kwargs):
        """按工具名称路由并调用（含熔断保护）。"""
        entry = self._tool_map.get(tool_name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"工具 '{tool_name}' 未注册")

        fn, server_name = entry
        breaker = self._breakers.get(server_name)

        # 熔断检查
        if breaker is not None and breaker.is_open:
            raise HTTPException(
                status_code=503,
                detail=f"MCP Server [{server_name}] 暂时不可用（熔断保护），请稍后重试",
            )

        try:
            result = fn(**kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            if breaker is not None:
                breaker.success()
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error("工具调用失败 [%s]: %s", tool_name, e)
            if breaker is not None:
                breaker.failure()
            raise HTTPException(status_code=500, detail=str(e))

    @property
    def tool_names(self) -> list[str]:
        return list(self._tool_map.keys())

    def breaker_stats(self) -> list[dict]:
        """返回所有熔断器状态（用于健康检查）。"""
        return [b.stats for b in self._breakers.values()]


registry = ServerRegistry()

# REST 端点统一注册到 router，由 create_app() 挂到应用实例。
# 这样端点可在 app 实例创建之前定义，避免装配顺序问题。
router = APIRouter()


# ── 限流中间件（在 create_app() 中以 add_middleware 装配）──

# 不需要限流的路径前缀
_RATE_LIMIT_SKIP_PREFIXES = (
    "/health",
    "/ui/assets/",
    "/ui/static/",
    "/ui/",
)


async def rate_limit_middleware(request: Request, call_next):
    """全局请求限流（跳过健康检查和前端静态资源）"""
    client_ip = request.client.host if request.client else "127.0.0.1"
    path = request.url.path
    # 健康检查 + Gradio 静态资源不限流
    if path.startswith(_RATE_LIMIT_SKIP_PREFIXES):
        return await call_next(request)
    if not rate_limiter.allow(client_ip):
        return JSONResponse(
            status_code=429,
            content={"detail": f"请求过于频繁，请稍后再试（限制 {config.gateway_rate_limit} 次/分钟）"},
        )
    return await call_next(request)


# ── MCP 端点说明 ────────────────────────────────────────
# POST /mcp/        → MCP Streamable HTTP 协议端点（FastMCP 原生提供，
#                     由 create_app() 挂载，见 mcp_servers/mcp_aggregator.py）
# POST /mcp/{tool}  → 运行时直调（私有约定，非 MCP 协议）

@router.post("/mcp/{tool_name}")
async def call_mcp_tool_runtime(tool_name: str, body: dict, _auth=Depends(verify_auth)):
    """按工具名直接调用对应 Server 的工具（非 MCP 协议）。"""
    result = await registry.call_tool(tool_name, **body)
    if isinstance(result, dict):
        return result
    return {"result": result}


# ═══════════════════════════════════════════════════════════
# REST API 端点
# ═══════════════════════════════════════════════════════════

# ── 健康检查 ────────────────────────────────────────────

@router.get("/health")
async def health():
    return {
        "status": "ok",
        "version": VERSION,
        "tools": registry.tool_names,
        "breakers": registry.breaker_stats(),
    }


# ── MCP 端点说明 ────────────────────────────────────────
# POST /mcp/          → MCP Streamable HTTP 协议端点（由 FastMCP 原生提供，
#                       挂载于 create_app()，见 mcp_servers/mcp_aggregator.py）
# POST /mcp/{tool}    → 运行时直调（私有约定，非 MCP 协议，注册于 create_app()）
#
# 旧版此处有一个 GET /mcp/sse：它只推送 endpoint 事件指向并不存在的 /mcp，
# 客户端照做会 404。已由上面真正的协议端点取代，故删除。

# ── 面试 REST API ───────────────────────────────────────

@router.post("/api/v1/interview")
async def create_interview(request: Request, body: dict, _auth=Depends(verify_auth)):
    """创建面试会话（仅解析+匹配，不出题）。

    Request:  {"jd_path": "...", "resume_path": "...", "candidate_name": "..."}
    Response: {"interview_id": "...", "jd": {...}, "resume": {...},
               "gap_analysis": {...}, "skills_ordered": [...], "state_summary": {...}}
    """
    from orchestration.supervisor import init_interview

    jd_path = body.get("jd_path", "")
    resume_path = body.get("resume_path", "")
    if not jd_path or not resume_path:
        raise HTTPException(status_code=400, detail="需要 jd_path 和 resume_path")

    try:
        # 仅解析 + 匹配，不出题
        state = await init_interview(jd_path, resume_path)

        state["candidate_name"] = body.get("candidate_name", "匿名")

        # 保存到 SessionStore
        store: SessionStore = request.app.state.session_store
        interview_id = store.save(_state_to_pydantic(state))
        state["interview_id"] = interview_id

        jd = state.get("jd")
        resume = state.get("resume")
        gap_map = state.get("gap_map", {})

        return {
            "interview_id": interview_id,
            "candidate_name": state["candidate_name"],
            "jd": _serialize_model(jd) if jd else None,
            "resume": _serialize_model(resume) if resume else None,
            "gap_analysis": gap_map,
            "skills_ordered": [s["skill"] for s in gap_map.get("ordered_skills", [])],
            "state_summary": _state_summary(state),
        }
    except Exception as e:
        logger.exception("创建面试失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/interview/{interview_id}/stream-question")
async def stream_question(interview_id: str, request: Request, _auth=Depends(verify_auth)):
    """SSE 流式出题端点。

    自动判断路由：无轮次 → 首题；有最后一轮 judge → 按 next_action 出题。
    """
    from orchestration.supervisor import generate_next_question_stream

    store: SessionStore = request.app.state.session_store
    pydantic_state = store.load(interview_id)
    if pydantic_state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    state = _pydantic_to_state(pydantic_state)

    async def event_stream():
        try:
            async for chunk in generate_next_question_stream(state):
                if isinstance(chunk, str):
                    yield {"event": "token", "data": chunk}
                elif chunk is not None:
                    q_data = chunk.model_dump(mode="json") if hasattr(chunk, "model_dump") else chunk
                    yield {"event": "complete", "data": json.dumps(q_data, ensure_ascii=False)}
                    # 流式完成后保存 state
                    pydantic_state2 = _state_to_pydantic(state)
                    pydantic_state2.interview_id = interview_id
                    store.save(pydantic_state2)
                    return
                else:
                    # chunk is None → stream failed
                    yield {"event": "error", "data": "流式出题失败：LLM 未返回有效结果"}
                    return
        except Exception as e:
            logger.exception("流式出题失败")
            yield {"event": "error", "data": str(e)}

    return EventSourceResponse(event_stream())


@router.post("/api/v1/interview/{interview_id}/judge")
async def judge_answer(interview_id: str, request: Request, body: dict, _auth=Depends(verify_auth)):
    """评判候选人回答（不出题）。

    Request:  {"answer": "..."}
    Response: {"judge": {...}, "terminated": bool, "rounds": [...], "progress": {...}}
    """
    from orchestration.supervisor import (
        judge_and_decide,
        promote_interview_questions,
        store_interview_memory,
    )

    answer = body.get("answer")
    if answer is None or not str(answer).strip():
        raise HTTPException(status_code=400, detail="需要非空的 answer 字段")

    store: SessionStore = request.app.state.session_store
    pydantic_state = store.load(interview_id)
    if pydantic_state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    state = _pydantic_to_state(pydantic_state)

    try:
        # 评判 + 决策
        state = await judge_and_decide(state, answer)

        # 检查是否终止
        terminated = state.get("terminated", False)
        pydantic_state = _state_to_pydantic(state)
        pydantic_state.interview_id = interview_id
        if terminated:
            pydantic_state.status = InterviewStatus.COMPLETED
        # 每轮都落盘：技能进度 (current_skill_index) 和空回答计数
        # (consecutive_empty) 必须跨请求存活，否则每轮都从首个技能重开。
        store.save(pydantic_state)
        if terminated:
            store_interview_memory(state)
            # 本场出过的题沉淀进种子题库（让题库随面试增长）
            promote_interview_questions(state)

        judge_result = state.get("judge_result")
        ordered = state.get("ordered_skills", [])
        current_idx = state.get("current_skill_index", 0)

        return {
            "judge": _serialize_model(judge_result) if judge_result else None,
            "terminated": terminated,
            "rounds": _build_rounds_list(state.get("rounds", [])),
            "progress": {
                "completed_rounds": state.get("current_round_number", 0),
                "current_skill": ordered[current_idx]["skill"] if ordered and current_idx < len(ordered) else "",
                "total_skills": len(ordered),
                "skills_ordered": [s.get("skill", s) if isinstance(s, dict) else s for s in ordered],
            },
            "state_summary": _state_summary(state),
        }
    except Exception as e:
        logger.exception("评判失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/interview/{interview_id}/talk")
async def interview_talk(interview_id: str, request: Request, body: dict, _auth=Depends(verify_auth)):
    """[已弃用] 提交回答 + 出题。请改用 /judge + /stream-question。"""
    from orchestration.supervisor import (
        generate_next_question,
        judge_and_decide,
        promote_interview_questions,
        store_interview_memory,
    )

    logger.warning("DEPRECATED: /talk 已弃用，请改用 /judge + /stream-question")
    answer = body.get("answer")
    if answer is None or not str(answer).strip():
        raise HTTPException(status_code=400, detail="需要非空的 answer 字段")

    store: SessionStore = request.app.state.session_store
    pydantic_state = store.load(interview_id)
    if pydantic_state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    state = _pydantic_to_state(pydantic_state)

    try:
        state = await judge_and_decide(state, answer)

        if state.get("terminated"):
            pydantic_state = _state_to_pydantic(state)
            pydantic_state.interview_id = interview_id
            pydantic_state.status = InterviewStatus.COMPLETED
            store.save(pydantic_state)
            store_interview_memory(state)
            promote_interview_questions(state)
            return {
                "judge": state.get("judge_result").model_dump() if state.get("judge_result") else None,
                "next_question": None,
                "terminated": True,
                "state_summary": _state_summary(state),
            }

        state = await generate_next_question(state)

        pydantic_state = _state_to_pydantic(state)
        pydantic_state.interview_id = interview_id
        store.save(pydantic_state)

        return {
            "judge": state.get("judge_result").model_dump() if state.get("judge_result") else None,
            "next_question": state.get("question").model_dump() if state.get("question") else None,
            "terminated": False,
            "state_summary": _state_summary(state),
        }
    except Exception as e:
        logger.exception("面试对话失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/interview/{interview_id}")
async def get_interview_state(interview_id: str, request: Request, _auth=Depends(verify_auth)):
    """获取会话当前状态。"""
    store: SessionStore = request.app.state.session_store
    pydantic_state = store.load(interview_id)
    if pydantic_state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    return {
        "interview_id": interview_id,
        "status": pydantic_state.status.value,
        "current_round": pydantic_state.current_round,
        "total_rounds": len(pydantic_state.rounds),
        "candidate_name": pydantic_state.candidate_name,
        "created_at": pydantic_state.created_at.isoformat(),
        "updated_at": pydantic_state.updated_at.isoformat(),
        "state_summary": _state_summary(_pydantic_to_state(pydantic_state)),
    }


@router.get("/api/v1/interview/{interview_id}/report")
async def get_interview_report(interview_id: str, request: Request, _auth=Depends(verify_auth)):
    """获取面试报告。"""
    from agents.feedback import FeedbackAgent

    store: SessionStore = request.app.state.session_store
    pydantic_state = store.load(interview_id)
    if pydantic_state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    state = _pydantic_to_state(pydantic_state)

    try:
        agent = FeedbackAgent()
        report = await agent.generate_report(
            jd=state.get("jd"),
            resume=state.get("resume"),
            rounds=state.get("rounds", []),
        )
        # 标记完成
        pydantic_state.status = InterviewStatus.COMPLETED
        store.save(pydantic_state)

        return {
            "interview_id": interview_id,
            "report": _serialize_model(report) if hasattr(report, "model_dump") else report,
            "candidate_name": pydantic_state.candidate_name,
            "jd_title": pydantic_state.jd.title if pydantic_state.jd else "",
            "rounds": _build_rounds_list(state.get("rounds", [])),
        }
    except Exception as e:
        logger.exception("生成报告失败")
        raise HTTPException(status_code=500, detail=str(e))


# ── 状态转换辅助 ────────────────────────────────────────

def _serialize_model(obj) -> dict:
    """安全序列化 Pydantic 模型为 dict。"""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _build_rounds_list(rounds: list) -> list[dict]:
    """将 RoundRecord 列表转为纯 JSON 安全的 dict 列表。"""
    result = []
    for r in rounds:
        if hasattr(r, "model_dump"):
            d = r.model_dump(mode="json")
        elif isinstance(r, dict):
            d = dict(r)
            if "question" in d and hasattr(d["question"], "model_dump"):
                d["question"] = d["question"].model_dump(mode="json")
            if "judge" in d and hasattr(d["judge"], "model_dump"):
                d["judge"] = d["judge"].model_dump(mode="json")
        else:
            continue
        result.append(d)
    return result


def _state_to_pydantic(state: dict) -> InterviewState:
    """将 TypedDict 状态转为 Pydantic InterviewState（用于序列化存储）。"""
    from models.interview import RoundState as PyRoundState
    from models.question import Answer

    rounds = []
    for r in state.get("rounds", []):
        # r 可能是 RoundRecord 或 dict
        if hasattr(r, "model_dump"):
            d = r.model_dump()
        elif isinstance(r, dict):
            d = dict(r)
        else:
            continue

        # answer 字段：RoundRecord 中是 str，RoundState 中需要 Answer 对象
        if isinstance(d.get("answer"), str):
            d["answer"] = Answer(content=d["answer"])

        rounds.append(PyRoundState(**d))

    return InterviewState(
        interview_id=state.get("interview_id", ""),
        status=InterviewStatus.COMPLETED if state.get("terminated") else InterviewStatus.IN_PROGRESS,
        jd=state.get("jd"),
        resume=state.get("resume"),
        gap_analysis=state.get("gap_map"),
        rounds=rounds,
        current_round=state.get("current_round_number", 0),
        question=state.get("question"),
        answer=state.get("answer", ""),
        candidate_name=state.get("candidate_name", "匿名"),
        current_skill_index=state.get("current_skill_index", 0),
        consecutive_empty=state.get("consecutive_empty", 0),
    )


def _pydantic_to_state(ps: InterviewState) -> dict:
    """将 Pydantic InterviewState 转回 dict（供 supervisor 函数使用）。"""
    from models.question import RoundRecord as DictRoundRecord

    rounds = []
    for r in ps.rounds:
        # answer 字段：RoundState 中是 Answer 对象，RoundRecord 中需要 str
        answer_raw = r.answer
        if answer_raw is None:
            answer_str = ""
        elif hasattr(answer_raw, "content"):
            answer_str = answer_raw.content
        else:
            answer_str = str(answer_raw)

        rounds.append(DictRoundRecord(
            round_number=r.round_number,
            skill=r.skill,
            question=r.question,
            answer=answer_str,
            judge=r.judge,
        ))

    ordered_skills = []
    gap_map = ps.gap_analysis
    if gap_map and isinstance(gap_map, dict):
        ordered_skills = gap_map.get("ordered_skills", [])

    return {
        "interview_id": ps.interview_id,
        "candidate_name": ps.candidate_name or "匿名",
        "jd_path": "",
        "resume_path": "",
        "jd_raw": "",
        "resume_raw": "",
        "jd": ps.jd,
        "resume": ps.resume,
        "gap_map": gap_map,
        "ordered_skills": ordered_skills,
        "current_skill_index": ps.current_skill_index,
        "rounds": rounds,
        "current_round_number": ps.current_round,
        "question": ps.question or (rounds[-1].question if rounds else None),
        "answer": ps.answer or "",
        "judge_result": rounds[-1].judge if rounds else None,
        "consecutive_empty": ps.consecutive_empty,
        "terminated": ps.status in (InterviewStatus.COMPLETED, InterviewStatus.TERMINATED),
        "report": None,
        "error": None,
    }


def _state_summary(state: dict) -> dict:
    """生成状态摘要。"""
    return {
        "rounds_completed": state.get("current_round_number", 0),
        "skills_ordered": [s.get("skill", "") for s in state.get("ordered_skills", [])],
        "terminated": state.get("terminated", False),
        "candidate_name": state.get("candidate_name", "匿名"),
    }


# ═══════════════════════════════════════════════════════════
# 装配：lifespan + 应用工厂 + 模块级实例
# ═══════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动时注册 MCP Server 与协议端点，关闭时清理。

    MCP 的 Streamable HTTP session manager 必须在外层 lifespan 中运行：
    Starlette 的 Mount 不会执行子应用自身的 lifespan。
    """
    from mcp_servers.jd_server import app as jd_server
    from mcp_servers.question_bank_server import app as qb_server
    from mcp_servers.resume_server import app as resume_server

    logger.info("=" * 50)
    logger.info("MCP Gateway 启动中...")
    install_mcp_noise_filter()

    # 三个源 Server 注册进 registry（供运行时直调与熔断保护使用）
    for server, label in (
        (jd_server, "jd-server"),
        (resume_server, "resume-server"),
        (qb_server, "question-bank-server"),
    ):
        try:
            registry.register(server, label)
        except Exception as e:  # noqa: BLE001 - 单个 Server 失败不应阻断启动
            logger.error("%s 注册失败: %s", label, e)

    app.state.session_store = SessionStore()
    logger.info("Gradio Web UI 由 main.py 独立启动（端口 %s），不走 mount", config.gradio_ui_port)
    logger.info("已注册工具: %s", registry.tool_names)

    # MCP 协议端点随本应用一起存活
    async with session_lifespan(app.state.mcp):
        logger.info("MCP 协议端点已就绪: POST %s/", MCP_MOUNT_PREFIX)
        # 如实展示客户端可用的地址：绑定地址 0.0.0.0 不是可访问地址，
        # 且 MCP 的 Host 白名单只放行回环地址，写错了会误导排查。
        hosts = allowed_hosts_for([config.gateway_host])
        reachable = [h for h in hosts if not h.endswith(":*") and h not in ("0.0.0.0", "::")]
        logger.info(
            "MCP 端点地址: %s",
            " / ".join(f"http://{h}:{config.gateway_port}{MCP_MOUNT_PREFIX}/" for h in reachable),
        )
        logger.info("MCP Host 白名单: %s", hosts)
        logger.info("Gateway 启动完成，绑定 %s:%s", config.gateway_host, config.gateway_port)
        logger.info("=" * 50)
        yield

    logger.info("MCP Gateway 关闭")


def create_app() -> FastAPI:
    """构造 Gateway 应用（REST API + MCP 协议端点）。

    独立成工厂函数的原因：FastMCP 的 session_manager.run() 每个实例只能调用一次，
    测试需要构造互不干扰的新实例。
    """
    aggregate_mcp = build_aggregate_mcp(extra_hosts=[config.gateway_host])
    mcp_sub_app = aggregate_mcp.streamable_http_app()

    application = FastAPI(
        title="AI 面试官 Gateway",
        version=VERSION,
        description="MCP Gateway — 统一管理 JD/简历/题库 Server，提供面试 REST API 与 MCP 协议端点",
        lifespan=lifespan,
    )
    application.state.mcp = aggregate_mcp
    application.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)
    application.include_router(router)

    # FastMCP 的 Streamable HTTP 子应用挂到 /mcp。
    # 子应用内部路径已设为 "/"，因此对外端点正好是 /mcp/；
    # /mcp/{tool_name} 运行时直调端点（注册在 router 上）与其共存。
    application.router.routes.append(Mount(MCP_MOUNT_PREFIX, app=mcp_sub_app))

    return application


# uvicorn 指向 "mcp_servers.gateway:app"，测试也可直接导入。
app = create_app()
