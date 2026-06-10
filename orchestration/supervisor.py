from __future__ import annotations

from typing import Annotated, Optional, TypedDict
from operator import add

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from models.jd import JD
from models.resume import Resume
from models.question import Question, JudgeResult
from agents.jd_parser import JDParserAgent
from agents.resume_analyzer import ResumeAnalyzerAgent
from agents.interviewer import InterviewerAgent
from orchestration.matcher import rank_skills, generate_gap_map
from tools import parse_file


# ── State ─────────────────────────────────────────────

class InterviewState(TypedDict):
    """面试状态 — LangGraph 版"""
    # 输入
    jd_path: str                          # JD 文件路径
    resume_path: str                      # 简历文件路径

    # 解析结果
    jd_raw: str                           # JD 原始文本
    resume_raw: str                       # 简历原始文本
    jd: Optional[JD]                      # 结构化 JD
    resume: Optional[Resume]              # 结构化简历

    # 匹配结果
    gap_map: Optional[dict]               # 能力缺口 Map
    ordered_skills: list[dict]            # 排序后的技能列表
    current_skill_index: int              # 当前考察的技能索引

    # 单轮问答
    question: Optional[Question]          # 当前题目
    answer: str                           # 候选人回答
    judge_result: Optional[JudgeResult]   # 评判结果

    # 错误信息
    error: Optional[str]


def initial_state(jd_path: str, resume_path: str) -> InterviewState:
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
        "question": None,
        "answer": "",
        "judge_result": None,
        "error": None,
    }


# ── 节点函数 ─────────────────────────────────────────

async def parse_jd_node(state: InterviewState) -> dict:
    """解析 JD 文件 → 结构化 JD"""
    try:
        jd_raw = parse_file(state["jd_path"])
        agent = JDParserAgent()
        jd = await agent.run(jd_raw)
        return {"jd_raw": jd_raw, "jd": jd, "error": None}
    except Exception as e:
        return {"error": f"JD 解析失败: {e}"}


async def parse_resume_node(state: InterviewState) -> dict:
    """解析简历文件 → 结构化简历"""
    try:
        resume_raw = parse_file(state["resume_path"])
        agent = ResumeAnalyzerAgent()
        resume = await agent.run(resume_raw)
        return {"resume_raw": resume_raw, "resume": resume, "error": None}
    except Exception as e:
        return {"error": f"简历解析失败: {e}"}


async def match_skills_node(state: InterviewState) -> dict:
    """JD + 简历 交叉匹配 → 能力缺口分析"""
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
    """根据当前技能生成面试题"""
    try:
        jd = state["jd"]
        resume = state["resume"]
        ordered = state["ordered_skills"]
        idx = state["current_skill_index"]

        if not jd or not resume or not ordered:
            return {"error": "缺少 JD、简历或技能列表"}

        target = ordered[idx] if idx < len(ordered) else ordered[-1]
        skill_name = target["skill"]
        intent = target.get("reason", f"考察 {skill_name}")

        agent = InterviewerAgent()
        # 第一个技能出 intermediate，后续可加深
        question = await agent.generate_question(
            jd=jd,
            resume=resume,
            target_skill=skill_name,
            difficulty="intermediate",
            intent=intent,
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
        return {"judge_result": result, "error": None}
    except Exception as e:
        return {"error": f"评判失败: {e}"}


# ── 条件边 ────────────────────────────────────────────

def check_error(state: InterviewState) -> str:
    """有错误时提前结束"""
    if state.get("error"):
        return "end"
    return "continue"


# ── 构建图 ────────────────────────────────────────────

def build_interview_graph():
    """构建面试流程图（Phase 1 单轮版本）"""
    builder = StateGraph(InterviewState)

    # 注册节点
    builder.add_node("parse_jd", parse_jd_node)
    builder.add_node("parse_resume", parse_resume_node)
    builder.add_node("match_skills", match_skills_node)
    builder.add_node("generate_question", generate_question_node)
    builder.add_node("judge_answer", judge_answer_node)

    # 入口：JD 和简历可以并行解析
    builder.set_entry_point("parse_jd")
    builder.add_edge("parse_jd", "parse_resume")
    builder.add_edge("parse_resume", "match_skills")
    builder.add_edge("match_skills", "generate_question")

    # 出题后等待用户回答（在 UI 层处理），然后判题
    builder.add_edge("generate_question", "judge_answer")
    builder.add_edge("judge_answer", END)

    # 错误处理
    builder.add_conditional_edges(
        "parse_jd",
        check_error,
        {"end": END, "continue": "parse_resume"},
    )
    builder.add_conditional_edges(
        "parse_resume",
        check_error,
        {"end": END, "continue": "match_skills"},
    )

    # 编译
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)
    return graph


# ── 便捷入口 ──────────────────────────────────────────

interview_graph = build_interview_graph()


async def run_interview(
    jd_path: str,
    resume_path: str,
    answer: str = "",
    thread_id: str = "default",
) -> InterviewState:
    """运行一次完整的单轮面试

    调用方式：
        result = await run_interview("jd.pdf", "resume.pdf", "候选人的回答")
    """
    state = initial_state(jd_path, resume_path)
    state["answer"] = answer

    config = {"configurable": {"thread_id": thread_id}}
    async for event in interview_graph.astream(state, config):
        # 最后一个 event 包含最终状态
        if "__end__" in event:
            return event["__end__"]

    return state
