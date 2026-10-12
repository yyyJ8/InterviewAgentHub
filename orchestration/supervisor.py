"""多轮面试编排 — 自研状态机

节点（纯函数式，输入状态、返回增量）：
    parse_jd → parse_resume → match_skills → generate_question
             → judge_answer → decide_next（条件路由）

**为什么不使用 LangGraph 的图执行**（架构决策，详见 docs/技术手册.md「架构演进」）：

本项目的交互模型是「一次 HTTP 请求 = 一轮面试」，答案要等用户提交、状态要跨进程
持久化。而 LangGraph 的执行模型是「一次 invoke 跑完整个流程，需要输入处用
interrupt 挂起」。实测结论：

  - 单次 Command(resume=...) 会让图继续按边流转（实测一次就推进了 3 轮）
  - 设置多个挂起点时，resume 值会被错误的挂起点消费
  - 默认检查点 MemorySaver 是进程内内存，无法跨请求/重启存活

因此这里**只沿用 LangGraph 的状态类型约定（TypedDict）与节点函数式设计**，
去掉图的调度与检查点：由调用方（Gateway）按请求逐轮直接调用节点函数，
状态持久化交给 memory/session_store.py。

注意：状态合并是**显式**的（调用方 state.update(node 返回值)）。
"""

from __future__ import annotations

import logging
from typing import Optional, TypedDict

from models.jd import JD
from models.resume import Resume
from models.question import Question, JudgeResult, RoundRecord
from agents.jd_parser import JDParserAgent
from agents.resume_analyzer import ResumeAnalyzerAgent
from agents.interviewer import InterviewerAgent
from orchestration.matcher import generate_gap_map
from tools import parse_file
from config import config

logger = logging.getLogger("supervisor")


# ── State ─────────────────────────────────────────────

class InterviewState(TypedDict):
    """面试状态 — 自研状态机多轮版"""
    # 会话标识（必须持久化，否则长期记忆钩子读不到候选人）
    interview_id: str
    candidate_name: str

    # 输入
    jd_path: str
    resume_path: str

    # 原始文本
    jd_raw: str
    resume_raw: str

    # 解析结果
    jd: Optional[JD]
    resume: Optional[Resume]

    # 匹配结果
    gap_map: Optional[dict]
    ordered_skills: list[dict]
    current_skill_index: int

    # 多轮面试。注意：这里刻意不用 Annotated[..., add] —— reducer 只在
    # LangGraph 图合并状态时生效；本项目不走图，累加由调用方显式完成
    # （judge_and_decide 中 state.update(节点返回值)）。
    rounds: list[RoundRecord]
    current_round_number: int
    question: Optional[Question]
    answer: str
    judge_result: Optional[JudgeResult]

    # 终止条件追踪
    consecutive_empty: int
    terminated: bool

    # 错误
    report: Optional[dict]
    error: Optional[str]


def initial_state(jd_path: str, resume_path: str) -> InterviewState:
    """创建初始状态"""
    return {
        "interview_id": "",
        "candidate_name": "",
        "jd_path": jd_path,
        "resume_path": resume_path,
        "jd_raw": "",
        "resume_raw": "",
        "jd": None,
        "resume": None,
        "gap_map": None,
        "ordered_skills": [],
        "current_skill_index": 0,
        "rounds": [],
        "current_round_number": 0,
        "question": None,
        "answer": "",
        "judge_result": None,
        "consecutive_empty": 0,
        "terminated": False,
        "report": None,
        "error": None,
    }


# ── 辅助函数 ──────────────────────────────────────────

def _get_current_skill(state: InterviewState) -> tuple[str, str, str]:
    """获取当前技能及其缺口的描述（索引越界时夹取到有效范围）"""
    ordered = state["ordered_skills"]
    if not ordered:
        return "", "", ""
    idx = min(max(state.get("current_skill_index", 0), 0), len(ordered) - 1)
    target = ordered[idx]
    return target["skill"], target.get("gap", ""), target.get("reason", "")


def _skill_difficulty(item: dict) -> str:
    """根据技能缺口决定初始难度"""
    gap = item.get("gap", "")
    if gap == "有项目经验":
        return "intermediate"
    elif gap == "有技能无项目":
        return "basic"
    else:
        return "basic"


def _next_action_label(state: InterviewState) -> str:
    """根据评判结果和终止条件决定下一动作"""
    judge = state.get("judge_result")
    if not judge:
        return "continue"

    ordered = state.get("ordered_skills", [])
    total_skills = len(ordered)

    # 1. 连续空回答检测
    if not state.get("answer", "").strip():
        empty_count = state.get("consecutive_empty", 0) + 1
        if empty_count >= config.max_consecutive_empty:
            return "end"

    # 2. 轮次上限
    if state.get("current_round_number", 0) >= config.max_rounds:
        return "end"

    # 3. 所有技能已覆盖
    if state.get("current_skill_index", 0) >= total_skills:
        return "end"

    # 4. 根据评判结果的 next_action（带安全兜底）
    action = judge.next_action.strip().lower()

    # 如果 judge 说 end 但还有未考察的技能 → 强制 switch
    if action == "end" and state.get("current_skill_index", 0) < total_skills - 1:
        return "switch"

    if action in ("deepen", "clarify", "switch"):
        return action

    return "end"


# ── 节点函数 ─────────────────────────────────────────

async def parse_jd_node(state: InterviewState) -> dict:
    """解析 JD 文件"""
    try:
        jd_raw = parse_file(state["jd_path"])
        agent = JDParserAgent()
        jd = await agent.run(jd_raw)
        return {"jd_raw": jd_raw, "jd": jd, "error": None}
    except Exception as e:
        return {"error": f"JD 解析失败: {e}"}


async def parse_resume_node(state: InterviewState) -> dict:
    """解析简历文件"""
    try:
        resume_raw = parse_file(state["resume_path"])
        agent = ResumeAnalyzerAgent()
        resume = await agent.run(resume_raw)
        return {"resume_raw": resume_raw, "resume": resume, "error": None}
    except Exception as e:
        return {"error": f"简历解析失败: {e}"}


async def match_skills_node(state: InterviewState) -> dict:
    """JD + 简历交叉匹配"""
    try:
        jd = state["jd"]
        resume = state["resume"]
        if not jd or not resume:
            return {"error": "JD 或简历未解析，无法匹配"}

        gap_map = generate_gap_map(jd, resume)
        ordered = gap_map["ordered_skills"]
        return {
            "gap_map": gap_map,
            "ordered_skills": ordered,
            "current_skill_index": 0,
            "error": None,
        }
    except Exception as e:
        return {"error": f"技能匹配失败: {e}"}


async def generate_question_node(state: InterviewState) -> dict:
    """生成下一道面试题（支持首次出题 / 追问加深 / 澄清 / 换维度）"""
    try:
        jd = state["jd"]
        resume = state["resume"]
        ordered = state["ordered_skills"]
        if not jd or not resume or not ordered:
            return {"error": "缺少 JD、简历或技能列表"}

        skill_name, gap, reason = _get_current_skill(state)
        last_round = state["rounds"][-1] if state["rounds"] else None
        agent = InterviewerAgent()

        if last_round and last_round.judge:
            judge = last_round.judge
            action = judge.next_action.strip().lower()

            if action == "deepen":
                # 答得好 → 追问加深
                question = await agent.generate_deepen_question(
                    jd=jd, resume=resume,
                    target_skill=skill_name,
                    difficulty=last_round.question.difficulty.value,
                    previous_question=last_round.question.content,
                    previous_answer=last_round.answer,
                )
            elif action == "clarify":
                # 答得模糊 → 要求澄清
                question = await agent.generate_clarify_question(
                    jd=jd, resume=resume,
                    target_skill=skill_name,
                    difficulty=last_round.question.difficulty.value,
                    previous_question=last_round.question.content,
                    previous_answer=last_round.answer,
                )
            elif action == "switch":
                # 换下一技能：索引已在 decide_next_node 推进，这里只读不写，
                # 避免一次 switch 跳两个技能（曾被重复自增）。
                idx = min(state.get("current_skill_index", 0), len(ordered) - 1)
                item = ordered[idx]
                question = await agent.generate_switch_question(
                    jd=jd, resume=resume,
                    target_skill=item["skill"],
                    difficulty=_skill_difficulty(item),
                )
                return {
                    "question": question,
                    "error": None,
                }
            else:
                # 默认：首次或继续
                question = await agent.generate_question(
                    jd=jd, resume=resume,
                    target_skill=skill_name,
                    difficulty=_skill_difficulty(ordered[state["current_skill_index"]]),
                    intent=reason,
                    candidate_name=state.get("candidate_name", ""),
                )
        else:
            # 首次出题
            item = ordered[min(state["current_skill_index"], len(ordered) - 1)]
            question = await agent.generate_question(
                jd=jd, resume=resume,
                target_skill=item["skill"],
                difficulty=_skill_difficulty(item),
                intent=item.get("reason", f"考察 {item['skill']}"),
                candidate_name=state.get("candidate_name", ""),
            )

        return {"question": question, "error": None}
    except Exception as e:
        return {"error": f"出题失败: {e}"}


async def judge_answer_node(state: InterviewState) -> dict:
    """评判候选人回答"""
    try:
        question = state["question"]
        answer = state["answer"]
        if not question:
            return {"error": "没有题目可评判"}

        agent = InterviewerAgent()
        result = await agent.judge_answer(question, answer)

        # 检测空回答
        is_empty = not answer.strip()
        empty_count = state.get("consecutive_empty", 0) + (1 if is_empty else 0)
        round_number = state.get("current_round_number", 0) + 1

        # 构建 RoundRecord
        record = RoundRecord(
            round_number=round_number,
            skill=question.skill,
            question=question,
            answer=answer,
            judge=result,
        )

        return {
            "judge_result": result,
            "rounds": [record],  # 通过 add reducer 追加
            "current_round_number": round_number,
            "consecutive_empty": empty_count,
            "error": None,
        }
    except Exception as e:
        return {"error": f"评判失败: {e}"}


async def decide_next_node(state: InterviewState) -> dict:
    """决定下一步动作。技能索引的推进**只在这里发生**。"""
    action = _next_action_label(state)
    terminated = action == "end"

    result = {
        "terminated": terminated,
    }

    # switch → 推进到下一个技能（唯一自增点）。当前技能可能是最后一项，此时
    # 保持不动，由 _next_action_label 的技能覆盖判定结束面试。
    if action == "switch":
        ordered = state.get("ordered_skills", [])
        idx = state.get("current_skill_index", 0)
        result["current_skill_index"] = min(idx + 1, max(len(ordered) - 1, 0))

    return result


# 说明：这里原先定义过 LangGraph 的图（build_interview_graph / interview_graph）
# 与它的条件路由函数 decide_routing。二者均已删除 —— 图从未被调用（真实链路按
# 请求逐轮直接调用节点函数），保留会误导读者以为存在图调度。
# 需要参考当时的实现：git show 8aa2cbc:orchestration/supervisor.py


# ── 交互式帮助函数（供 Web UI 使用） ─────────────────

async def init_interview(jd_path: str, resume_path: str) -> dict:
    """初始化面试：解析 JD + 简历 + 匹配，返回中间状态"""
    state = initial_state(jd_path, resume_path)

    # 手动执行 setup 节点
    for node_fn in (parse_jd_node, parse_resume_node, match_skills_node):
        result = await node_fn(state)
        state.update(result)
        if state.get("error"):
            raise RuntimeError(state["error"])

    return state


async def generate_next_question(state: dict) -> dict:
    """生成下一道题（基于当前技能和上一轮评判结果）"""
    result = await generate_question_node(state)
    state.update(result)
    if state.get("error"):
        raise RuntimeError(state["error"])
    return state


async def generate_next_question_stream(state: dict):
    """流式生成下一道面试题，yield token 字符串，最后 yield Question 对象。

    按 judge.next_action 路由到相应的流式 agent 方法。
    """
    from agents.interviewer import InterviewerAgent

    jd = state.get("jd")
    resume = state.get("resume")
    ordered = state.get("ordered_skills", [])
    if not jd or not resume or not ordered:
        raise RuntimeError("缺少 JD、简历或技能列表")

    skill_name, gap, reason = _get_current_skill(state)
    rounds = state.get("rounds", [])
    last_round = rounds[-1] if rounds else None
    agent = InterviewerAgent()

    if last_round and last_round.judge:
        judge = last_round.judge
        action = judge.next_action.strip().lower() if hasattr(judge, "next_action") else "continue"

        if action == "deepen":
            async for delta, done, result in agent.generate_deepen_question_stream(
                jd=jd, resume=resume,
                target_skill=skill_name,
                difficulty=(last_round.question.difficulty.value
                            if hasattr(last_round.question, "difficulty")
                            and last_round.question.difficulty
                            else "intermediate"),
                previous_question=(last_round.question.content
                                   if hasattr(last_round.question, "content")
                                   else ""),
                previous_answer=last_round.answer or "",
            ):
                if not done:
                    yield delta
                else:
                    if result:
                        state["question"] = result
                    yield result

        elif action == "clarify":
            async for delta, done, result in agent.generate_clarify_question_stream(
                jd=jd, resume=resume,
                target_skill=skill_name,
                difficulty=(last_round.question.difficulty.value
                            if hasattr(last_round.question, "difficulty")
                            and last_round.question.difficulty
                            else "intermediate"),
                previous_question=(last_round.question.content
                                   if hasattr(last_round.question, "content")
                                   else ""),
                previous_answer=last_round.answer or "",
            ):
                if not done:
                    yield delta
                else:
                    if result:
                        state["question"] = result
                    yield result

        elif action == "switch":
            # 索引已在 decide_next_node 推进，这里只读不写
            new_idx = min(state.get("current_skill_index", 0), len(ordered) - 1)
            item = ordered[new_idx]
            async for delta, done, result in agent.generate_switch_question_stream(
                jd=jd, resume=resume,
                target_skill=item["skill"],
                difficulty=_skill_difficulty(item),
            ):
                if not done:
                    yield delta
                else:
                    if result:
                        state["question"] = result
                    yield result

        else:
            # 默认：生成当前技能的新题
            item = ordered[min(state.get("current_skill_index", 0), len(ordered) - 1)]
            async for delta, done, result in agent.generate_question_stream(
                jd=jd, resume=resume,
                target_skill=item["skill"],
                difficulty=_skill_difficulty(item),
                intent=item.get("reason", f"考察 {item['skill']}"),
                candidate_name=state.get("candidate_name", ""),
            ):
                if not done:
                    yield delta
                else:
                    if result:
                        state["question"] = result
                    yield result
    else:
        # 首次出题
        item = ordered[min(state.get("current_skill_index", 0), len(ordered) - 1)]
        async for delta, done, result in agent.generate_question_stream(
            jd=jd, resume=resume,
            target_skill=item["skill"],
            difficulty=_skill_difficulty(item),
            intent=item.get("reason", f"考察 {item['skill']}"),
            candidate_name=state.get("candidate_name", ""),
        ):
            if not done:
                yield delta
            else:
                if result:
                    state["question"] = result
                yield result


async def judge_and_decide(state: dict, answer: str) -> dict:
    """评判回答并决定下一步"""
    state["answer"] = answer
    result = await judge_answer_node(state)
    state.update(result)
    if state.get("error"):
        raise RuntimeError(state["error"])

    # 决定下一步
    result2 = await decide_next_node(state)
    state.update(result2)
    return state


# ── 记忆钩子（Phase 3: VectorStore 集成） ─────────────

def store_interview_memory(state: dict) -> bool:
    """面试结束时，将面试记录写入向量库。

    在 Gateway 的 talk 端点中、面试终止时调用。
    失败时静默降级，不抛出异常。
    """
    try:
        from memory.vector_store import VectorStore

        vs = VectorStore()
        if not vs.available:
            return False

        import json

        candidate_name = state.get("candidate_name", "匿名")
        interview_id = state.get("interview_id", "")
        jd = state.get("jd")
        rounds = state.get("rounds", [])

        # 序列化面试轮次
        rounds_json = []
        for r in rounds:
            if hasattr(r, "model_dump"):
                d = r.model_dump(mode="json")
            elif isinstance(r, dict):
                d = r
            else:
                continue
            rounds_json.append(d)

        interview_doc = json.dumps({
            "interview_id": interview_id,
            "candidate_name": candidate_name,
            "jd_title": jd.title if jd else "",
            "rounds": rounds_json,
        }, ensure_ascii=False, default=str)

        vs.store_interview_session(
            interview_doc,
            metadata={
                "interview_id": interview_id,
                "candidate_name": candidate_name,
                "jd_title": jd.title if jd else "",
                "round_count": len(rounds),
                "total_score": _calc_total_score(rounds),
            },
        )

        return True
    except Exception:
        return False


def promote_interview_questions(state: dict) -> dict:
    """面试结束时，把本场出过的题沉淀进种子题库（去重 + 质量过滤）。

    与 store_interview_memory 的区别：后者把整场面试记录写入向量库（供候选人
    历史检索），本函数把**题目本身**沉淀进 data/seed_questions.json —— 让题库
    随面试增长，而不是永远只有初始种子题。

    失败时静默降级，不影响面试流程。返回统计信息（失败时为空统计）。
    """
    empty = {"added": 0, "skipped_duplicate": 0, "skipped_quality": 0,
             "reasons": [], "total": 0}
    try:
        from mcp_servers.question_bank_server import promote_questions

        candidates: list[dict] = []
        for r in state.get("rounds", []):
            question = r.get("question") if isinstance(r, dict) else getattr(r, "question", None)
            if question is None:
                continue
            if hasattr(question, "model_dump"):
                candidates.append(question.model_dump(mode="json"))
            elif isinstance(question, dict):
                candidates.append(question)

        if not candidates:
            return empty

        stats = promote_questions(candidates)
        if stats.get("added"):
            logger.info(
                "题库沉淀：新增 %s 道（跳过重复 %s / 质量不足 %s），当前共 %s 道",
                stats["added"], stats["skipped_duplicate"],
                stats["skipped_quality"], stats["total"],
            )
        return stats
    except Exception as e:  # noqa: BLE001 - 沉淀失败不应影响面试
        logger.warning("题库沉淀失败（已忽略）: %s", e)
        return empty


def get_candidate_history_summary(candidate_name: str) -> str:
    """查询候选人历史面试，返回可注入 Prompt 的摘要。

    无历史记录时返回空字符串，异常时静默返回空。
    用于面试出题时提供上下文。
    """
    try:
        from memory.vector_store import VectorStore

        vs = VectorStore()
        if not vs.available:
            return ""
        results = vs.search_candidate_history(candidate_name)
        if not results:
            return ""
        parts = [
            f"### 候选人 {candidate_name} 的历史面试记录",
            f"共 {len(results)} 条记录：",
        ]
        for i, r in enumerate(results[:3], 1):  # 最多取最近 3 条
            meta = r.get("metadata", {})
            parts.append(
                f"{i}. 岗位 {meta.get('jd_title', '未知')}，"
                f"{meta.get('round_count', 0)} 轮，"
                f"均分 {meta.get('total_score', 0):.0f}"
            )
        return "\n".join(parts)
    except Exception:
        return ""


def _calc_total_score(rounds: list) -> float:
    """计算面试总分（平均分）。兼容对象与 dict 两种 round 形态。"""
    scores = []
    for r in rounds:
        judge = r.get("judge") if isinstance(r, dict) else getattr(r, "judge", None)
        if not judge:
            continue
        score = judge.get("score") if isinstance(judge, dict) else getattr(judge, "score", None)
        if score is not None:
            scores.append(score)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)
