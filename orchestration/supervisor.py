from __future__ import annotations

from typing import Annotated, Optional, TypedDict
from operator import add

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from models.jd import JD
from models.resume import Resume
from models.question import Question, JudgeResult, RoundRecord
from agents.jd_parser import JDParserAgent
from agents.resume_analyzer import ResumeAnalyzerAgent
from agents.interviewer import InterviewerAgent
from orchestration.matcher import rank_skills, generate_gap_map
from tools import parse_file
from config import config


# ── State ─────────────────────────────────────────────

class InterviewState(TypedDict):
    """面试状态 — LangGraph 多轮版"""
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

    # 多轮面试
    rounds: Annotated[list[RoundRecord], add]  # 积累的面试轮次
    current_round_number: int
    question: Optional[Question]
    answer: str
    judge_result: Optional[JudgeResult]

    # 终止条件追踪
    consecutive_empty: int
    terminated: bool

    # 批量模式：预填所有回答
    all_answers: list[str]
    answer_index: int

    # 错误
    report: Optional[dict]
    error: Optional[str]


def initial_state(
    jd_path: str,
    resume_path: str,
    all_answers: list[str] | None = None,
) -> InterviewState:
    """创建初始状态"""
    return {
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
        "all_answers": all_answers or [],
        "answer_index": 0,
        "report": None,
        "error": None,
    }


# ── 辅助函数 ──────────────────────────────────────────

def _get_current_skill(state: InterviewState) -> tuple[str, str, str]:
    """获取当前技能及其缺口的描述"""
    ordered = state["ordered_skills"]
    idx = state["current_skill_index"]
    if not ordered:
        return "", "", ""
    if idx >= len(ordered):
        idx = len(ordered) - 1
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

    # 1. 连续空回答检测
    if not state.get("answer", "").strip():
        empty_count = state.get("consecutive_empty", 0) + 1
        if empty_count >= config.max_consecutive_empty:
            return "end"

    # 2. 轮次上限
    if state.get("current_round_number", 0) >= config.max_rounds:
        return "end"

    # 3. 所有技能已覆盖
    if state.get("current_skill_index", 0) >= len(state.get("ordered_skills", [])):
        return "end"

    # 4. 根据评判结果的 next_action
    action = judge.next_action.strip().lower()
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
                # 答不上 → 换下一技能
                new_idx = state["current_skill_index"] + 1
                new_skill = ordered[new_idx]["skill"] if new_idx < len(ordered) else skill_name
                question = await agent.generate_switch_question(
                    jd=jd, resume=resume,
                    target_skill=new_skill,
                    difficulty=_skill_difficulty(ordered[min(new_idx, len(ordered)-1)]),
                )
                return {
                    "question": question,
                    "current_skill_index": new_idx,
                    "error": None,
                }
            else:
                # 默认：首次或继续
                question = await agent.generate_question(
                    jd=jd, resume=resume,
                    target_skill=skill_name,
                    difficulty=_skill_difficulty(ordered[state["current_skill_index"]]),
                    intent=reason,
                )
        else:
            # 首次出题
            item = ordered[state["current_skill_index"]]
            question = await agent.generate_question(
                jd=jd, resume=resume,
                target_skill=item["skill"],
                difficulty=_skill_difficulty(item),
                intent=item.get("reason", f"考察 {item['skill']}"),
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
    """决定下一步动作"""
    action = _next_action_label(state)
    terminated = action == "end"

    result = {
        "terminated": terminated,
    }

    # 如果切换技能，递增 skill_index
    if action == "switch":
        new_idx = state["current_skill_index"] + 1
        result["current_skill_index"] = new_idx

    return result


# ── 条件路由 ─────────────────────────────────────────

def decide_routing(state: InterviewState) -> str:
    """路由判断：继续循环还是结束"""
    if state.get("terminated") or state.get("error"):
        return "end"
    return "continue"


# ── 构建图 ───────────────────────────────────────────

def build_interview_graph(with_interrupt: bool = False):
    """构建多轮面试流程图

    Args:
        with_interrupt: 如果 True，在 judge_answer 前中断（适合交互式）
    """
    builder = StateGraph(InterviewState)

    # 注册节点
    builder.add_node("parse_jd", parse_jd_node)
    builder.add_node("parse_resume", parse_resume_node)
    builder.add_node("match_skills", match_skills_node)
    builder.add_node("generate_question", generate_question_node)
    builder.add_node("judge_answer", judge_answer_node)
    builder.add_node("decide_next", decide_next_node)

    # 入口 → 解析
    builder.set_entry_point("parse_jd")
    builder.add_edge("parse_jd", "parse_resume")
    builder.add_edge("parse_resume", "match_skills")

    # 匹配 → 出题 → 评判 → 决策
    builder.add_edge("match_skills", "generate_question")
    builder.add_edge("generate_question", "judge_answer")
    builder.add_edge("judge_answer", "decide_next")

    # 条件循环：继续 → 回到出题；结束 → END
    builder.add_conditional_edges(
        "decide_next",
        decide_routing,
        {"continue": "generate_question", "end": END},
    )

    # 编译
    kwargs = {"checkpointer": MemorySaver()}
    if with_interrupt:
        kwargs["interrupt_before"] = ["judge_answer"]

    graph = builder.compile(**kwargs)
    return graph


interview_graph = build_interview_graph(with_interrupt=False)


# ── 便捷入口 ─────────────────────────────────────────

async def run_interview(
    jd_path: str,
    resume_path: str,
    answers: list[str] | None = None,
) -> dict:
    """运行一次完整的批量面试（一次性执行所有轮次）

    Args:
        jd_path: JD 文件路径
        resume_path: 简历文件路径
        answers: 预填的回答列表（每个元素是一轮的回答文本）

    Returns:
        最终的面试状态（包含 rounds, report 等）
    """
    state = initial_state(jd_path, resume_path, all_answers=answers or [])

    configurable = {"configurable": {"thread_id": "batch_run"}}
    async for event in interview_graph.astream(state, configurable):
        if "__end__" in event:
            return event["__end__"]

    return state


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
    """面试结束时，将面试记录写入向量库 + 更新候选人画像。

    在 Gateway 的 talk 端点中、面试终止时调用。
    失败时静默降级，不抛出异常。
    """
    try:
        from memory.vector_store import VectorStore

        vs = VectorStore()
        if not vs.available:
            return False

        import json
        from models.question import RoundRecord

        candidate_name = state.get("candidate_name", "匿名")
        interview_id = state.get("interview_id", "")
        jd = state.get("jd")
        rounds = state.get("rounds", [])

        # 1. 存储面试记录
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

        # 2. 更新候选人画像
        resume = state.get("resume")
        profile_doc = json.dumps({
            "name": candidate_name,
            "title": resume.title if resume else "",
            "skills": [s.model_dump() if hasattr(s, "model_dump") else s for s in (resume.skills if resume else [])],
        }, ensure_ascii=False, default=str)

        vs.update_candidate_profile(
            candidate_name,
            profile_doc,
            extra_meta={
                "last_interview_at": interview_id,
                "interview_count": 1,  # 后续可累积
            },
        )

        return True
    except Exception:
        return False


def retrieve_candidate_history(candidate_name: str) -> list[dict]:
    """检索候选人的历史面试记录（出题参考）。"""
    try:
        from memory.vector_store import VectorStore

        vs = VectorStore()
        if not vs.available:
            return []
        return vs.search_candidate_history(candidate_name)
    except Exception:
        return []


def retrieve_similar_questions(skill: str, n: int = 3) -> list[dict]:
    """从历史题库中检索相似题目（出题参考）。"""
    try:
        from memory.vector_store import VectorStore

        vs = VectorStore()
        if not vs.available:
            return []
        return vs.search_similar_questions(skill, n=n)
    except Exception:
        return []


def _calc_total_score(rounds: list) -> float:
    """计算面试总分（平均分）。"""
    scores = []
    for r in rounds:
        if hasattr(r, "judge") and r.judge:
            scores.append(r.judge.score)
        elif isinstance(r, dict) and r.get("judge"):
            scores.append(r["judge"].score)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)
