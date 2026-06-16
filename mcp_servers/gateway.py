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
import logging
import time
import uuid
from typing import Optional

import gradio as gr
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sse_starlette.sse import EventSourceResponse

from config import config
from memory.session_store import SessionStore
from models.interview import InterviewState, InterviewStatus

logger = logging.getLogger("gateway")

# ── FastAPI 实例 ────────────────────────────────────────

app = FastAPI(
    title="AI 面试官 Gateway",
    version="0.3.0",
    description="MCP Gateway — 统一管理 JD/简历/题库 Server，提供面试 REST API",
)

security = HTTPBearer(auto_error=False)

# ── 限流器 ──────────────────────────────────────────────

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
        self._tool_map: dict[str, callable] = {}   # tool_name → callable
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

# ── 鉴权依赖 ────────────────────────────────────────────

def verify_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """验证 Bearer Token。可通过配置关闭。"""
    if config.gateway_require_auth:
        if credentials is None:
            raise HTTPException(status_code=401, detail="缺少 Authorization header")
        token = credentials.credentials
        if token != config.gateway_api_key:
            raise HTTPException(status_code=401, detail="无效的 API Key")
    return True


# ── 限流中间件 ──────────────────────────────────────────

# ── 不需要限流的路径前缀 ──
_RATE_LIMIT_SKIP_PREFIXES = (
    "/health",
    "/ui/assets/",
    "/ui/static/",
    "/ui/",
)


@app.middleware("http")
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


# ── 生命周期 ────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """启动时注册三个 MCP Server"""
    logger.info("=" * 50)
    logger.info("MCP Gateway 启动中...")

    # JD Server
    try:
        from mcp_servers.jd_server import app as jd_app
        registry.register(jd_app, "jd-server")
    except Exception as e:
        logger.error("JD Server 注册失败: %s", e)

    # Resume Server
    try:
        from mcp_servers.resume_server import app as resume_app
        registry.register(resume_app, "resume-server")
    except Exception as e:
        logger.error("Resume Server 注册失败: %s", e)

    # Question Bank Server
    try:
        from mcp_servers.question_bank_server import app as qb_app
        registry.register(qb_app, "question-bank-server")
    except Exception as e:
        logger.error("Question Bank Server 注册失败: %s", e)

    # Session Store
    app.state.session_store = SessionStore()
    logger.info("Gradio Web UI 由 main.py 独立启动（端口 %s），不走 mount", config.gradio_ui_port)

    logger.info("已注册工具: %s", registry.tool_names)
    logger.info("Gateway 启动完成，监听 %s:%s", config.gateway_host, config.gateway_port)
    logger.info("=" * 50)


@app.on_event("shutdown")
async def shutdown():
    logger.info("MCP Gateway 关闭")


# ═══════════════════════════════════════════════════════════
# REST API 端点
# ═══════════════════════════════════════════════════════════

# ── 健康检查 ────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "0.4.0",
        "tools": registry.tool_names,
        "breakers": registry.breaker_stats(),
    }


# ── MCP 工具调用端点 ────────────────────────────────────

@app.post("/mcp/{tool_name}")
async def call_mcp_tool(tool_name: str, body: dict, _auth=Depends(verify_auth)):
    """通用 MCP 工具调用端点。按 tool_name 路由到对应 Server。"""
    result = await registry.call_tool(tool_name, **body)
    if isinstance(result, dict):
        return result
    return {"result": result}


# ── MCP SSE 端点 ────────────────────────────────────────

@app.get("/mcp/sse")
async def mcp_sse_endpoint(request: Request, _auth=Depends(verify_auth)):
    """MCP 协议的 SSE transport 端点。"""
    async def event_stream():
        yield {"event": "endpoint", "data": "/mcp"}
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(30)

    return EventSourceResponse(event_stream())


# ── 面试 REST API ───────────────────────────────────────

@app.post("/api/v1/interview")
async def create_interview(body: dict, _auth=Depends(verify_auth)):
    """创建面试会话。

    Request:  {"jd_path": "...", "resume_path": "..."}
    Response: {"interview_id": "...", "question": {...}, "state": {...}}
    """
    from orchestration.supervisor import init_interview, generate_next_question

    jd_path = body.get("jd_path", "")
    resume_path = body.get("resume_path", "")
    if not jd_path or not resume_path:
        raise HTTPException(status_code=400, detail="需要 jd_path 和 resume_path")

    try:
        # Phase 1: 解析 + 匹配
        state = await init_interview(jd_path, resume_path)

        # Phase 2: 生成第一题
        state["candidate_name"] = body.get("candidate_name", "匿名")
        state = await generate_next_question(state)

        # 保存到 SessionStore
        store: SessionStore = app.state.session_store
        interview_id = store.save(_state_to_pydantic(state))
        state["interview_id"] = interview_id

        question = state.get("question")
        return {
            "interview_id": interview_id,
            "question": question.model_dump() if question else None,
            "state_summary": _state_summary(state),
        }
    except Exception as e:
        logger.exception("创建面试失败")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/interview/{interview_id}/talk")
async def interview_talk(interview_id: str, body: dict, _auth=Depends(verify_auth)):
    """提交回答，返回评判结果 + 下一题。

    Request:  {"answer": "..."}
    Response: {"judge": {...}, "next_question": {...} or null, "terminated": bool}
    """
    from orchestration.supervisor import judge_and_decide, generate_next_question, store_interview_memory

    answer = body.get("answer", "")
    if answer is None:
        raise HTTPException(status_code=400, detail="需要 answer 字段")

    store: SessionStore = app.state.session_store
    pydantic_state = store.load(interview_id)
    if pydantic_state is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    state = _pydantic_to_state(pydantic_state)

    try:
        # 评判 + 决策
        state = await judge_and_decide(state, answer)

        # 检查是否终止
        if state.get("terminated"):
            pydantic_state = _state_to_pydantic(state)
            pydantic_state.interview_id = interview_id
            pydantic_state.status = InterviewStatus.COMPLETED
            store.save(pydantic_state)
            # 写入长期记忆（异步降级，不阻塞响应）
            store_interview_memory(state)
            return {
                "judge": state.get("judge_result").model_dump() if state.get("judge_result") else None,
                "next_question": None,
                "terminated": True,
                "state_summary": _state_summary(state),
            }

        # 生成下一题
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


@app.get("/api/v1/interview/{interview_id}")
async def get_interview_state(interview_id: str, _auth=Depends(verify_auth)):
    """获取会话当前状态。"""
    store: SessionStore = app.state.session_store
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


@app.get("/api/v1/interview/{interview_id}/report")
async def get_interview_report(interview_id: str, _auth=Depends(verify_auth)):
    """获取面试报告。"""
    from agents.feedback import FeedbackAgent

    store: SessionStore = app.state.session_store
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
            "report": report,
            "candidate_name": pydantic_state.candidate_name,
        }
    except Exception as e:
        logger.exception("生成报告失败")
        raise HTTPException(status_code=500, detail=str(e))


# ── 状态转换辅助 ────────────────────────────────────────

def _state_to_pydantic(state: dict) -> InterviewState:
    """将 TypedDict 状态转为 Pydantic InterviewState（用于序列化存储）。"""
    from models.question import RoundState as PyRoundState

    rounds = []
    for r in state.get("rounds", []):
        # r 可能是 RoundRecord 或 dict
        if hasattr(r, "model_dump"):
            rounds.append(PyRoundState(**r.model_dump()))
        elif isinstance(r, dict):
            rounds.append(PyRoundState(**r))

    return InterviewState(
        interview_id=state.get("interview_id", ""),
        status=InterviewStatus.COMPLETED if state.get("terminated") else InterviewStatus.IN_PROGRESS,
        jd=state.get("jd"),
        resume=state.get("resume"),
        gap_analysis=state.get("gap_map"),
        rounds=rounds,
        current_round=state.get("current_round_number", 0),
        candidate_name=state.get("candidate_name", "匿名"),
    )


def _pydantic_to_state(ps: InterviewState) -> dict:
    """将 Pydantic InterviewState 转回 dict（供 supervisor 函数使用）。"""
    from models.question import RoundRecord as DictRoundRecord

    rounds = []
    for r in ps.rounds:
        rounds.append(DictRoundRecord(
            round_number=r.round_number,
            skill=r.skill,
            question=r.question,
            answer=r.answer or "",
            judge=r.judge,
        ))

    ordered_skills = []
    gap_map = ps.gap_analysis
    if gap_map and isinstance(gap_map, dict):
        ordered_skills = gap_map.get("ordered_skills", [])

    return {
        "jd_path": "",
        "resume_path": "",
        "jd_raw": "",
        "resume_raw": "",
        "interview_id": ps.interview_id,
        "jd": ps.jd,
        "resume": ps.resume,
        "gap_map": gap_map,
        "ordered_skills": ordered_skills,
        "current_skill_index": 0,
        "rounds": rounds,
        "current_round_number": ps.current_round,
        "question": rounds[-1].question if rounds else None,
        "answer": "",
        "judge_result": rounds[-1].judge if rounds else None,
        "consecutive_empty": 0,
        "terminated": ps.status in (InterviewStatus.COMPLETED, InterviewStatus.TERMINATED),
        "all_answers": [],
        "answer_index": 0,
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
