"""AI 面试官 — Gradio Web UI（Phase 4）

基于 Gradio 5 Blocks，三步流程：
  1. 上传 JD + 简历 → 点击开始
  2. 面试对话（题目 ↔ 回答 ↔ 评分 → 下一题）
  3. 面试报告展示
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path

import gradio as gr

from agents.jd_parser import JDParserAgent
from agents.resume_analyzer import ResumeAnalyzerAgent
from agents.interviewer import InterviewerAgent
from agents.feedback import FeedbackAgent
from orchestration.matcher import generate_gap_map
from tools import parse_file, ParseError
from config import config

logger = logging.getLogger("web.ui")

# ── 异步辅助 ─────────────────────────────────────────────


def _async(coro):
    """用独立事件循环运行 coroutine，自动抑制清理阶段的噪音错误。"""

    async def _with_cleanup():
        try:
            return await coro
        finally:
            # 主动关闭 httpx 客户端，避免事件循环关闭后才触发清理
            pass

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_with_cleanup())
        finally:
            # 清理所有待处理任务，抑制 "Event loop is closed" 噪音
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()
    except RuntimeError:
        import concurrent.futures

        def _run():
            return _async(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run).result()


def _extract_file_path(file_obj) -> str:
    """从 Gradio 5 的各种返回类型中提取文件路径。"""
    if isinstance(file_obj, str):
        return file_obj
    path = getattr(file_obj, 'path', None) or getattr(file_obj, 'name', None)
    if path:
        return str(path)
    raise ParseError(f"无法提取文件路径: {type(file_obj).__name__}")


# ── 面试逻辑 ─────────────────────────────────────────────


def _parse_and_match(jd_path: str, resume_path: str) -> dict:
    jd_raw = parse_file(jd_path)
    resume_raw = parse_file(resume_path)

    jd_agent = JDParserAgent()
    resume_agent = ResumeAnalyzerAgent()
    jd = _async(jd_agent.run(jd_raw))
    resume = _async(resume_agent.run(resume_raw))

    gap_map = generate_gap_map(jd, resume)

    return {
        "jd": jd,
        "resume": resume,
        "gap_map": gap_map,
        "ordered_skills": gap_map["ordered_skills"],
        "skill_index": 0,
        "rounds": [],
        "question": None,
        "terminated": False,
        "report": None,
        "interview_id": uuid.uuid4().hex[:8],
    }


def _generate_next_question(state: dict) -> dict:
    interviewer = InterviewerAgent()
    rounds = state["rounds"]
    ordered = state["ordered_skills"]
    skill_idx = state["skill_index"]
    jd = state["jd"]
    resume = state["resume"]

    if not ordered or skill_idx >= len(ordered):
        state["terminated"] = True
        return state

    current = ordered[skill_idx]
    skill_name = current["skill"]
    last_round = rounds[-1] if rounds else None

    try:
        if last_round and last_round.get("judge"):
            judge = last_round["judge"]
            action = judge.next_action.strip().lower() if hasattr(judge, "next_action") else ""
            last_q = last_round["question"]
            last_a = last_round.get("answer", "")

            if action == "deepen":
                question = _async(interviewer.generate_deepen_question(
                    jd=jd, resume=resume, target_skill=skill_name,
                    difficulty=last_q.difficulty.value,
                    previous_question=last_q.content,
                    previous_answer=last_a,
                ))
            elif action == "clarify":
                question = _async(interviewer.generate_clarify_question(
                    jd=jd, resume=resume, target_skill=skill_name,
                    difficulty=last_q.difficulty.value,
                    previous_question=last_q.content,
                    previous_answer=last_a,
                ))
            elif action == "switch":
                new_idx = skill_idx + 1
                if new_idx < len(ordered):
                    state["skill_index"] = new_idx
                    skill_name = ordered[new_idx]["skill"]
                    question = _async(interviewer.generate_switch_question(
                        jd=jd, resume=resume, target_skill=skill_name,
                        difficulty=_skill_difficulty(ordered[new_idx]),
                    ))
                else:
                    state["terminated"] = True
                    return state
            else:
                question = _async(interviewer.generate_question(
                    jd=jd, resume=resume, target_skill=skill_name,
                    difficulty=_skill_difficulty(current),
                    intent=current.get("reason", ""),
                ))
        else:
            question = _async(interviewer.generate_question(
                jd=jd, resume=resume, target_skill=skill_name,
                difficulty=_skill_difficulty(current),
                intent=current.get("reason", f"考察 {skill_name}"),
            ))
        state["question"] = question
    except Exception as e:
        state["error"] = str(e)

    return state


def _judge_answer(answer: str, state: dict) -> dict:
    try:
        interviewer = InterviewerAgent()
        question = state["question"]
        result = _async(interviewer.judge_answer(question, answer))

        state["rounds"].append({
            "question": question,
            "answer": answer,
            "judge": result,
        })

        if not answer.strip():
            empty_count = sum(1 for r in state["rounds"] if not r.get("answer", "").strip())
            if empty_count >= config.max_consecutive_empty:
                state["terminated"] = True
                return state

        if len(state["rounds"]) >= config.max_rounds:
            state["terminated"] = True
            return state

        next_action = result.next_action.strip().lower() if result.next_action else ""

        if next_action == "switch":
            state["skill_index"] += 1
            if state["skill_index"] >= len(state["ordered_skills"]):
                state["terminated"] = True

    except Exception as e:
        state["error"] = str(e)

    return state


def _generate_report(state: dict) -> dict:
    try:
        agent = FeedbackAgent()
        report = _async(agent.generate_report(
            jd=state["jd"],
            resume=state["resume"],
            rounds=state["rounds"],
        ))
        state["report"] = report
    except Exception as e:
        state["error"] = str(e)
    return state


def _skill_difficulty(item: dict) -> str:
    gap = item.get("gap", "")
    return "intermediate" if gap == "有项目经验" else "basic"


# ── 界面构建器 ───────────────────────────────────────────


def _build_info_text(jd, resume, gap) -> str:
    info_text = f"## 🎯 {jd.title}\n\n**候选人**: {resume.name}  |  **经验**: {resume.experience_years or '未知'} 年\n\n---\n"
    info_text += "### 📊 能力缺口分析\n\n"
    for item in gap.get("ordered_skills", []):
        icon = "✅" if item["gap"] == "有项目经验" else ("⚠️" if item["gap"] == "有技能无项目" else "❌")
        info_text += f"- {icon} **{item['skill']}** (权重 {item['weight']}): {item['reason']}\n"
    return info_text


def _build_progress_text(state: dict) -> str:
    completed = len(state.get("rounds", []))
    total_skills = len(state.get("ordered_skills", []))
    current_idx = state.get("skill_index", 0) + 1
    return (
        f"---\n"
        f"📊 **已完成 {completed} 轮  |  技能 {min(current_idx, total_skills)}/{total_skills}**\n"
        f"---"
    )


def _build_chat_history(state: dict) -> list:
    """Gradio 5 Chatbot 格式: [(role, content), ...]"""
    chat = []
    for r in state.get("rounds", []):
        q = r.get("question")
        q_text = q.content if hasattr(q, "content") else q.get("content", "") if q else ""
        a_text = r.get("answer", "")
        j = r.get("judge")
        score = j.score if hasattr(j, "score") else j.get("score", 0) if j else 0
        comment = j.comment if hasattr(j, "comment") else j.get("comment", "") if j else ""
        if q_text:
            chat.append(("🤖 面试官", q_text))
        if a_text:
            chat.append(("🧑‍💻 你", a_text))
        if comment:
            chat.append(("📊 评分", f"**{score}/100** — {comment}"))
    return chat


# ── 回调 ─────────────────────────────────────────────────


def on_start(jd_file, resume_file):
    """generator: 逐步 yield 进度，UI 实时更新"""
    if jd_file is None or resume_file is None:
        yield (
            None, None,
            [("系统", "请先上传 JD 和简历文件")], "",
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
        )
        return

    try:
        jd_path = _extract_file_path(jd_file)
        resume_path = _extract_file_path(resume_file)

        # Step 1: 解析文件
        yield (
            None, "📄 正在解析文件...",
            [("系统", "📄 正在解析 JD 和简历文件...")], "",
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
        )
        jd_raw = parse_file(jd_path)
        resume_raw = parse_file(resume_path)

        # Step 2: LLM 分析 JD
        yield (
            None, "🤖 正在分析岗位需求...",
            [("系统", "🤖 AI 正在提取 JD 中的技能和权重...")], "",
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
        )
        jd_agent = JDParserAgent()
        jd = _async(jd_agent.run(jd_raw))

        # Step 3: LLM 分析简历
        yield (
            None, "📋 正在分析候选人简历...",
            [("系统", "📋 AI 正在提取候选人的技能和项目经历...")], "",
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
        )
        resume_agent = ResumeAnalyzerAgent()
        resume = _async(resume_agent.run(resume_raw))

        # Step 4: 缺口匹配
        yield (
            None, "🔍 正在匹配能力缺口...",
            [("系统", "🔍 正在对比 JD 与简历，排序面试技能...")], "",
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
        )
        gap_map = generate_gap_map(jd, resume)

        state = {
            "jd": jd,
            "resume": resume,
            "gap_map": gap_map,
            "ordered_skills": gap_map["ordered_skills"],
            "skill_index": 0,
            "rounds": [],
            "question": None,
            "terminated": False,
            "report": None,
            "interview_id": uuid.uuid4().hex[:8],
        }

        # Step 5: LLM 出题
        yield (
            state, _build_info_text(jd, resume, gap_map),
            [("系统", "✍️ AI 正在针对候选人背景生成面试题...")], "",
            gr.update(visible=False), gr.update(visible=True), gr.update(visible=False),
        )
        state = _generate_next_question(state)

        q = state.get("question")
        info_text = _build_info_text(jd, resume, gap_map)
        chat = [("🤖 面试官", q.content)] if q else [("系统", "出题失败，请重试")]
        progress = _build_progress_text(state)
        if progress and q:
            chat.append(("📊 进度", progress))

        yield (
            state, info_text, chat, "",
            gr.update(visible=False), gr.update(visible=True), gr.update(visible=False),
        )

    except ParseError as e:
        yield (
            None, f"❌ 文件解析失败: {e}",
            [("系统", f"❌ {e}")], "",
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
        )
    except Exception as e:
        logger.exception("初始化失败")
        yield (
            None, f"❌ 初始化失败: {e}",
            [("系统", f"❌ {e}")], "",
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
        )


def on_submit(answer, state):
    if state is None:
        return (
            state, [("系统", "请先开始面试")], "", "",
            gr.update(visible=True), gr.update(visible=False),
        )

    if not answer.strip():
        answer = "（跳过）"

    state = _judge_answer(answer, state)
    chat = _build_chat_history(state)

    progress = _build_progress_text(state)
    if progress:
        chat.append(("📊 进度", progress))

    if state.get("terminated"):
        state = _generate_report(state)
        report_text = _render_report_md(state)
        return (
            state, chat, "", report_text,
            gr.update(visible=False), gr.update(visible=True),
        )
    else:
        state = _generate_next_question(state)
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


def _end_interview(state):
    if state is None:
        return state, [], "", "", gr.update(visible=True), gr.update(visible=False)
    state["terminated"] = True
    state = _generate_report(state)
    chat = _build_chat_history(state)
    progress = _build_progress_text(state)
    if progress:
        chat.append(("📊 进度", progress))
    report_text = _render_report_md(state)
    return state, chat, "", report_text, gr.update(visible=False), gr.update(visible=True)


def _restart():
    return (
        None, "",
        [("系统", "请上传新的 JD 和简历文件开始新面试")], "", "",
        gr.update(visible=True), gr.update(visible=False), gr.update(visible=False),
    )


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
        lines.append(f"**岗位**: {jd.title}  |  **候选人**: {resume.name}\n")

    score = report.total_score if hasattr(report, "total_score") else report.get("total_score", 0)
    emoji = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")
    lines.append(f"## {emoji} 总分: {score:.0f}/100\n")

    rec = report.hiring_recommendation if hasattr(report, "hiring_recommendation") else report.get("hiring_recommendation", "")
    rec_map = {"strong_yes": "✅ 强烈推荐", "yes": "👍 推荐录用", "hesitate": "🤔 待定", "no": "❌ 不推荐"}
    lines.append(f"**录用建议**: {rec_map.get(rec, rec)}\n")

    overall = report.overall_assessment if hasattr(report, "overall_assessment") else report.get("overall_assessment", "")
    if overall:
        lines.append(f"**总体评价**: {overall}\n")

    dims = report.dimension_scores if hasattr(report, "dimension_scores") else report.get("dimension_scores", {})
    if dims:
        lines.append("### 🎯 五维度评估\n")
        for dim, s in dims.items():
            bar = "█" * int(s / 5) + "░" * (20 - int(s / 5))
            lines.append(f"- **{dim}**: {bar} {s:.0f}/100")

    strengths = report.strengths if hasattr(report, "strengths") else report.get("strengths", [])
    weaknesses = report.weaknesses if hasattr(report, "weaknesses") else report.get("weaknesses", [])
    if strengths:
        lines.append("\n### 🌟 亮点\n")
        for s_item in strengths:
            lines.append(f"- ✅ {s_item}")
    if weaknesses:
        lines.append("\n### 📈 待改进\n")
        for w in weaknesses:
            lines.append(f"- ⚠️ {w}")

    suggestions = report.suggestions if hasattr(report, "suggestions") else report.get("suggestions", [])
    if suggestions:
        lines.append("\n### 💡 改进建议\n")
        for s_item in suggestions:
            lines.append(f"- {s_item}")

    lines.append("\n---\n### 💬 面试全程回顾\n")
    for i, r in enumerate(rounds, 1):
        q = r["question"]
        j = r.get("judge")
        q_content = q.content if hasattr(q, "content") else q["content"]
        s = j.score if hasattr(j, "score") else j.get("score", 0) if j else 0
        lines.append(f"**第 {i} 轮** — {q.skill} ({q.difficulty.value}) — {s}/100")
        lines.append(f"> 🤖 {q_content[:150]}...")
        lines.append(f"> 🧑‍💻 {r['answer'][:150] if r.get('answer') else '(未作答)'}")
        lines.append("")

    return "\n".join(lines)


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
                jd_file = gr.File(label="岗位描述 (JD)", file_types=[".pdf", ".docx", ".txt"])
                resume_file = gr.File(label="简历 (Resume)", file_types=[".pdf", ".docx", ".txt"])
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

        # ── 回调 ──

        start_btn.click(
            fn=on_start,
            inputs=[jd_file, resume_file],
            outputs=[interview_state, info_md, chatbot, answer_input, upload_col, interview_col, report_col],
        )

        submit_btn.click(
            fn=on_submit,
            inputs=[answer_input, interview_state],
            outputs=[interview_state, chatbot, answer_input, report_md, interview_col, report_col],
        )

        end_btn.click(
            fn=_end_interview,
            inputs=[interview_state],
            outputs=[interview_state, chatbot, answer_input, report_md, interview_col, report_col],
        )

        restart_btn.click(
            fn=_restart,
            inputs=[],
            outputs=[interview_state, init_info, chatbot, answer_input, report_md, upload_col, interview_col, report_col],
        )

    return demo


demo = build_ui()
