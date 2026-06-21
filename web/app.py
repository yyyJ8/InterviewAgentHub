"""AI 面试官 — Gradio Web UI（Phase 5 优化版）

基于 Gradio 5 Blocks，三步流程：
  1. 上传 JD + 简历 → 点击开始
  2. 面试对话（题目 ↔ 回答 ↔ 评分 → 下一题）
  3. 面试报告展示

Phase 5 改进：
  - 原生 async def 回调，消除 _async() 反模式
  - 统一使用 orchestration/supervisor 的状态机（与 Gateway 一致）
  - 首道面试题流式输出，打字机效果
"""

from __future__ import annotations

import logging
import uuid

import gradio as gr

from agents.interviewer import InterviewerAgent
from agents.feedback import FeedbackAgent
from memory.session_store import SessionStore
from orchestration.supervisor import (
    init_interview,
    generate_next_question,
    judge_and_decide,
    store_interview_memory,
)
from tools import parse_file, ParseError
from config import config

logger = logging.getLogger("web.ui")


# ── 工具函数 ─────────────────────────────────────────────

def _extract_file_path(file_obj) -> str:
    """从 Gradio 5 的各种返回类型中提取文件路径。"""
    if isinstance(file_obj, str):
        return file_obj
    path = getattr(file_obj, 'path', None) or getattr(file_obj, 'name', None)
    if path:
        return str(path)
    raise ParseError(f"无法提取文件路径: {type(file_obj).__name__}")


def _skill_difficulty(item: dict) -> str:
    """根据技能缺口决定初始难度。"""
    gap = item.get("gap", "")
    return "intermediate" if gap == "有项目经验" else "basic"


# ── 界面文本构建 ─────────────────────────────────────────

def _build_info_text(jd, resume, gap) -> str:
    info_text = (
        f"## 🎯 {jd.title}\n\n"
        f"**候选人**: {resume.name}  |  "
        f"**经验**: {resume.experience_years or '未知'} 年\n\n---\n"
        "### 📊 能力缺口分析\n\n"
    )
    for item in gap.get("ordered_skills", []):
        icon = (
            "✅" if item["gap"] == "有项目经验"
            else ("⚠️" if item["gap"] == "有技能无项目" else "❌")
        )
        info_text += (
            f"- {icon} **{item['skill']}** "
            f"(权重 {item['weight']}): {item['reason']}\n"
        )
    return info_text


def _build_progress_text(state: dict) -> str:
    completed = len(state.get("rounds", []))
    total_skills = len(state.get("ordered_skills", []))
    current_idx = state.get("current_skill_index", 0) + 1
    return (
        f"---\n"
        f"📊 **已完成 {completed} 轮  |  "
        f"技能 {min(current_idx, total_skills)}/{total_skills}**\n"
        f"---"
    )


def _build_chat_history(state: dict) -> list:
    """Gradio 5 Chatbot 格式: [(role, content), ...]

    兼容 RoundRecord (Pydantic) 和 dict 两种格式。
    """
    chat = []
    for r in state.get("rounds", []):
        # 兼容 Pydantic 对象和 dict
        if hasattr(r, "question"):
            q, a_text, j = r.question, r.answer, r.judge
        elif isinstance(r, dict):
            q, a_text, j = r.get("question"), r.get("answer", ""), r.get("judge")
        else:
            continue

        q_text = q.content if hasattr(q, "content") else q.get("content", "") if q else ""
        score = j.score if hasattr(j, "score") else j.get("score", 0) if j else 0
        comment = j.comment if hasattr(j, "comment") else j.get("comment", "") if j else ""

        if q_text:
            chat.append(("🤖 面试官", q_text))
        if a_text:
            chat.append(("🧑‍💻 你", a_text))
        if comment:
            chat.append(("📊 评分", f"**{score}/100** — {comment}"))
    return chat


# ── 报告渲染 ─────────────────────────────────────────────

def _render_report_md(state: dict) -> str:
    if state is None or state.get("report") is None:
        return "暂无报告"

    report = state["report"]
    rounds = state.get("rounds", [])

    lines = ["# 📋 面试报告\n"]
    jd = state.get("jd")
    resume = state.get("resume")
    if jd and resume:
        lines.append(
            f"**岗位**: {jd.title}  |  **候选人**: {resume.name}\n"
        )

    score = (
        report.total_score if hasattr(report, "total_score")
        else report.get("total_score", 0)
    )
    emoji = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")
    lines.append(f"## {emoji} 总分: {score:.0f}/100\n")

    rec = (
        report.hiring_recommendation if hasattr(report, "hiring_recommendation")
        else report.get("hiring_recommendation", "")
    )
    rec_map = {
        "strong_yes": "✅ 强烈推荐", "yes": "👍 推荐录用",
        "hesitate": "🤔 待定", "no": "❌ 不推荐",
    }
    lines.append(f"**录用建议**: {rec_map.get(rec, rec)}\n")

    overall = (
        report.overall_assessment if hasattr(report, "overall_assessment")
        else report.get("overall_assessment", "")
    )
    if overall:
        lines.append(f"**总体评价**: {overall}\n")

    dims = (
        report.dimension_scores if hasattr(report, "dimension_scores")
        else report.get("dimension_scores", {})
    )
    if dims:
        lines.append("### 🎯 五维度评估\n")
        for dim, s in dims.items():
            bar = "█" * int(s / 5) + "░" * (20 - int(s / 5))
            lines.append(f"- **{dim}**: {bar} {s:.0f}/100")

    strengths = (
        report.strengths if hasattr(report, "strengths")
        else report.get("strengths", [])
    )
    weaknesses = (
        report.weaknesses if hasattr(report, "weaknesses")
        else report.get("weaknesses", [])
    )
    if strengths:
        lines.append("\n### 🌟 亮点\n")
        for s_item in strengths:
            lines.append(f"- ✅ {s_item}")
    if weaknesses:
        lines.append("\n### 📈 待改进\n")
        for w in weaknesses:
            lines.append(f"- ⚠️ {w}")

    suggestions = (
        report.suggestions if hasattr(report, "suggestions")
        else report.get("suggestions", [])
    )
    if suggestions:
        lines.append("\n### 💡 改进建议\n")
        for s_item in suggestions:
            lines.append(f"- {s_item}")

    lines.append("\n---\n### 💬 面试全程回顾\n")
    for i, r in enumerate(rounds, 1):
        # 兼容 Pydantic 和 dict
        if hasattr(r, "question"):
            q, a_text, j = r.question, r.answer, r.judge
        elif isinstance(r, dict):
            q, a_text, j = r["question"], r.get("answer", ""), r.get("judge")
        else:
            continue

        q_content = q.content if hasattr(q, "content") else q.get("content", "")
        s = j.score if hasattr(j, "score") else j.get("score", 0) if j else 0
        diff_val = (
            q.difficulty.value if hasattr(q.difficulty, "value")
            else q.get("difficulty", "")
        )
        lines.append(
            f"**第 {i} 轮** — {q.skill if hasattr(q, 'skill') else q.get('skill', '')} "
            f"({diff_val}) — {s}/100"
        )
        lines.append(f"> 🤖 {q_content[:150]}...")
        lines.append(
            f"> 🧑‍💻 {a_text[:150] if a_text else '(未作答)'}"
        )
        lines.append("")

    return "\n".join(lines)


# ── 异步业务函数 ─────────────────────────────────────────

def _save_session(state: dict) -> None:
    """将面试状态保存到 SessionStore（JSON 文件持久化）。"""
    try:
        from models.interview import InterviewState, InterviewStatus
        from models.question import RoundState as PyRoundState

        rounds = []
        for r in state.get("rounds", []):
            if hasattr(r, "model_dump"):
                rounds.append(PyRoundState(**r.model_dump()))
            elif isinstance(r, dict):
                rounds.append(PyRoundState(**r))

        pydantic_state = InterviewState(
            interview_id=state.get("interview_id", ""),
            status=(
                InterviewStatus.COMPLETED if state.get("terminated")
                else InterviewStatus.IN_PROGRESS
            ),
            jd=state.get("jd"),
            resume=state.get("resume"),
            gap_analysis=state.get("gap_map"),
            rounds=rounds,
            current_round=state.get("current_round_number", 0),
            candidate_name=state.get("candidate_name", "匿名"),
        )
        SessionStore().save(pydantic_state)
    except Exception:
        pass  # 存储失败不影响面试流程


async def _generate_report(state: dict) -> dict:
    """生成面试报告。"""
    agent = FeedbackAgent()
    report = await agent.generate_report(
        jd=state["jd"],
        resume=state["resume"],
        rounds=state["rounds"],
    )
    state["report"] = report
    return state


# ── Gradio 回调 ──────────────────────────────────────────

async def on_start(jd_file, resume_file):
    """Generator: 逐步 yield 进度，UI 实时更新。

    流程：
      1. 解析文件 → 2. 初始化面试（supervisor）→ 3. 流式出第一题
    """
    if jd_file is None or resume_file is None:
        yield (
            None, None,
            [("系统", "请先上传 JD 和简历文件")], "",
            gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=False),
        )
        return

    try:
        jd_path = _extract_file_path(jd_file)
        resume_path = _extract_file_path(resume_file)

        # ── Step 1: 解析文件 ──
        yield (
            None, "📄 正在解析文件...",
            [("系统", "📄 正在解析 JD 和简历文件...")], "",
            gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=False),
        )

        # ── Step 2: 初始化面试（LLM 解析 JD + 简历 + 技能匹配）──
        yield (
            None, "🤖 正在分析岗位需求与候选人背景...",
            [("系统", "🤖 AI 正在提取 JD 技能、分析简历、匹配缺口...")], "",
            gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=False),
        )
        state = await init_interview(jd_path, resume_path)

        jd = state["jd"]
        resume = state["resume"]
        gap_map = state["gap_map"]
        ordered = state["ordered_skills"]

        state["candidate_name"] = resume.name
        state["interview_id"] = uuid.uuid4().hex[:8]

        if not ordered:
            yield (
                state, _build_info_text(jd, resume, gap_map),
                [("系统", "未找到需要考察的技能维度，请检查 JD 内容")], "",
                gr.update(visible=False), gr.update(visible=True),
                gr.update(visible=False),
            )
            return

        # ── Step 3: 流式出第一题 ──
        skill_item = ordered[0]
        skill_name = skill_item["skill"]
        difficulty = _skill_difficulty(skill_item)
        intent = skill_item.get("reason", f"考察 {skill_name}")

        interviewer = InterviewerAgent()
        question_text = ""
        question_obj = None

        async for delta, done, result in interviewer.generate_question_stream(
            jd=jd, resume=resume,
            target_skill=skill_name,
            difficulty=difficulty,
            intent=intent,
            candidate_name=resume.name,
        ):
            if not done:
                question_text += delta
                # 流式：实时更新聊天区，模拟打字效果
                yield (
                    state,
                    _build_info_text(jd, resume, gap_map),
                    [("🤖 面试官", question_text + " ▌")],
                    "",
                    gr.update(visible=False), gr.update(visible=True),
                    gr.update(visible=False),
                )
            else:
                question_obj = result

        if question_obj is None:
            raise RuntimeError("流式出题失败：LLM 未返回有效结果")

        state["question"] = question_obj

        # ── 最终状态 ──
        info_text = _build_info_text(jd, resume, gap_map)
        chat = [("🤖 面试官", question_obj.content)]
        progress = _build_progress_text(state)
        if progress:
            chat.append(("📊 进度", progress))

        yield (
            state, info_text, chat, "",
            gr.update(visible=False), gr.update(visible=True),
            gr.update(visible=False),
        )

    except ParseError as e:
        yield (
            None, f"❌ 文件解析失败: {e}",
            [("系统", f"❌ {e}")], "",
            gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=False),
        )
    except Exception as e:
        logger.exception("初始化失败")
        yield (
            None, f"❌ 初始化失败: {e}",
            [("系统", f"❌ 初始化失败，请重试: {e}")], "",
            gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=False),
        )


async def on_submit(answer, state):
    """提交回答 → 评判 → 出下一题 / 生成报告。

    使用 supervisor 的 judge_and_decide + generate_next_question。
    """
    if state is None:
        return (
            state, [("系统", "请先开始面试")], "", "",
            gr.update(visible=True), gr.update(visible=False),
        )

    if not answer.strip():
        answer = "（跳过）"

    try:
        # ── 评判 + 决策 ──
        state = await judge_and_decide(state, answer)
        chat = _build_chat_history(state)

        progress = _build_progress_text(state)
        if progress:
            chat.append(("📊 进度", progress))

        # ── 终止？→ 生成报告 ──
        if state.get("terminated"):
            state = await _generate_report(state)
            _save_session(state)
            store_interview_memory(state)
            report_text = _render_report_md(state)
            return (
                state, chat, "", report_text,
                gr.update(visible=False), gr.update(visible=True),
            )

        # ── 继续 → 生成下一题 ──
        state = await generate_next_question(state)
        q = state.get("question")
        if q:
            q_text = q.content if hasattr(q, "content") else str(q)
            chat.append(("🤖 面试官", q_text))
            new_progress = _build_progress_text(state)
            if new_progress:
                chat.append(("📊 进度", new_progress))

        return (
            state, chat, "", "",
            gr.update(visible=True), gr.update(visible=False),
        )

    except Exception as e:
        logger.exception("面试对话失败")
        chat = _build_chat_history(state)
        chat.append(("系统", f"❌ 出错: {e}"))
        return (
            state, chat, "", "",
            gr.update(visible=True), gr.update(visible=False),
        )


async def _end_interview(state):
    """手动结束面试 → 生成报告。"""
    if state is None:
        return (
            state, [], "", "",
            gr.update(visible=True), gr.update(visible=False),
        )

    state["terminated"] = True

    try:
        state = await _generate_report(state)
        _save_session(state)
        store_interview_memory(state)
    except Exception as e:
        logger.error("生成报告失败: %s", e)

    chat = _build_chat_history(state)
    progress = _build_progress_text(state)
    if progress:
        chat.append(("📊 进度", progress))
    report_text = _render_report_md(state)

    return (
        state, chat, "", report_text,
        gr.update(visible=False), gr.update(visible=True),
    )


def _restart():
    """重置为初始状态。"""
    return (
        None, "",
        [("系统", "请上传新的 JD 和简历文件开始新面试")], "", "",
        gr.update(visible=True), gr.update(visible=False),
        gr.update(visible=False),
    )


# ── 构建 Gradio 界面 ─────────────────────────────────────

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="AI 面试官") as demo:

        interview_state = gr.State()

        gr.Markdown("# 🎯 AI 面试官")
        gr.Markdown("*从 JD 解析到面试到反馈，完整闭环*")

        # ── 第一步：上传区 ──
        with gr.Column(visible=True) as upload_col:
            gr.Markdown("### 📄 上传 JD 与简历")
            with gr.Row():
                jd_file = gr.File(
                    label="岗位描述 (JD)",
                    file_types=[".pdf", ".docx", ".txt"],
                )
                resume_file = gr.File(
                    label="简历 (Resume)",
                    file_types=[".pdf", ".docx", ".txt"],
                )
            start_btn = gr.Button("🚀 开始面试", variant="primary", size="lg")
            init_info = gr.Markdown("")

        # ── 第二步：面试区 ──
        with gr.Column(visible=False) as interview_col:
            info_md = gr.Markdown("")
            chatbot = gr.Chatbot(label="面试对话", height=450, type="tuples")
            with gr.Row():
                answer_input = gr.Textbox(
                    label="你的回答",
                    placeholder="请在此输入你的回答...（输入「跳过」可跳过此题）",
                    scale=4,
                    lines=3,
                )
                with gr.Column(scale=1):
                    submit_btn = gr.Button("📨 提交答案", variant="primary", size="lg")
                    end_btn = gr.Button("⏹ 结束面试", variant="stop")

        # ── 第三步：报告区 ──
        with gr.Column(visible=False) as report_col:
            report_md = gr.Markdown("")
            restart_btn = gr.Button("🔄 再来一场面试", variant="primary")

        # ── 回调绑定 ──

        start_btn.click(
            fn=on_start,
            inputs=[jd_file, resume_file],
            outputs=[
                interview_state, info_md, chatbot, answer_input,
                upload_col, interview_col, report_col,
            ],
        )

        submit_btn.click(
            fn=on_submit,
            inputs=[answer_input, interview_state],
            outputs=[
                interview_state, chatbot, answer_input, report_md,
                interview_col, report_col,
            ],
        )

        end_btn.click(
            fn=_end_interview,
            inputs=[interview_state],
            outputs=[
                interview_state, chatbot, answer_input, report_md,
                interview_col, report_col,
            ],
        )

        restart_btn.click(
            fn=_restart,
            inputs=[],
            outputs=[
                interview_state, init_info, chatbot, answer_input, report_md,
                upload_col, interview_col, report_col,
            ],
        )

    return demo


demo = build_ui()
