"""AI 面试官 — Gradio Web UI（Phase 4: 流式输出 + 进度指示）

基于 Gradio Blocks，三步流程：
  1. 上传 JD + 简历 → 点击开始（流式展示出题过程）
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
    """同步桥接：在单次调用中运行一个 coroutine 并返回结果。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _async_iter(async_gen):
    """同步桥接：消耗一个 async generator，逐项 yield。

    用法：
        for chunk, done, result in _async_iter(async_gen_func()):
            ...
    """
    loop = asyncio.new_event_loop()
    try:
        agen = loop.run_until_complete(async_gen) if asyncio.iscoroutine(async_gen) else async_gen

        async def _consume():
            results = []
            async for item in agen:
                results.append(item)
            return results

        results = loop.run_until_complete(_consume())
        for item in results:
            yield item
    finally:
        loop.close()


def _extract_streaming_text(partial_json: str) -> str:
    """从部分 JSON 中提取可显示的文字内容（用于流式进度展示）。

    优先提取 "content" 字段内容；失败则返回去噪原始文本。
    """
    # 尝试匹配 "content": "...(可能未闭合)"
    match = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"?', partial_json)
    if match:
        return match.group(1).replace('\\"', '"').replace('\\n', '\n')
    # 回退：显示原始文本（去除 JSON 噪声）
    cleaned = partial_json.replace('{', '').replace('}', '').replace('"', '')
    return cleaned.strip()[:200]


# ── 面试逻辑 ─────────────────────────────────────────────


def _parse_and_match(jd_path: str, resume_path: str) -> dict:
    """解析 JD + 简历，执行交叉匹配，返回状态字典"""
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
    """根据当前状态生成下一题"""
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
    """评判回答，返回更新后的 state"""
    try:
        interviewer = InterviewerAgent()
        question = state["question"]
        result = _async(interviewer.judge_answer(question, answer))

        state["rounds"].append({
            "question": question,
            "answer": answer,
            "judge": result,
        })

        # 终止条件检查
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
    """生成最终报告"""
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


# ── 进度信息构建 ─────────────────────────────────────────


def _build_info_text(jd, resume, gap) -> str:
    """构建面试信息 Markdown（含进度指示）"""
    info_text = f"## 🎯 {jd.title}\n\n**候选人**: {resume.name}  |  **经验**: {resume.experience_years or '未知'} 年\n\n---\n"
    info_text += "### 📊 能力缺口分析\n\n"
    for item in gap.get("ordered_skills", []):
        icon = "✅" if item["gap"] == "有项目经验" else ("⚠️" if item["gap"] == "有技能无项目" else "❌")
        info_text += f"- {icon} **{item['skill']}** (权重 {item['weight']}): {item['reason']}\n"
    return info_text


def _build_progress_text(state: dict) -> str:
    """构建进度指示文本"""
    completed = len(state.get("rounds", []))
    total_skills = len(state.get("ordered_skills", []))
    current_idx = state.get("skill_index", 0) + 1
    hr = "—" * 20
    return (
        f"{hr}\n"
        f"📊 **面试进度**  \n"
        f"已完成 {completed} 轮  |  当前技能 {min(current_idx, total_skills)}/{total_skills}  \n"
        f"{hr}"
    )


# ── Gradio 界面逻辑 ──────────────────────────────────────


def on_start(jd_file, resume_file):
    """点击「开始面试」→ 解析 + 匹配 + 流式出第一题（generator）

    每个 yield 返回 (state, info_md, chatbot, answer, upload_col, interview_col, report_col)
    """
    if jd_file is None or resume_file is None:
        yield None, None, [("系统", "请先上传 JD 和简历文件")], "", \
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)
        return

    # ── Phase 1: 解析文件 ──
    yield None, "⏳ 正在解析文件...", [("系统", "📄 正在解析 JD 和简历文件...")], "", \
        gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)

    try:
        state = _parse_and_match(jd_file.name, resume_file.name)
    except ParseError as e:
        yield None, f"❌ 文件解析失败: {e}", [("系统", f"❌ {e}")], "", \
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)
        return
    except Exception as e:
        yield None, f"❌ 初始化失败: {e}", [("系统", f"❌ 初始化失败: {e}")], "", \
            gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)
        return

    jd = state["jd"]
    resume = state["resume"]
    gap = state["gap_map"]
    info_text = _build_info_text(jd, resume, gap)

    # ── Phase 2: 流式出题 ──
    yield state, info_text, [("系统", "🤔 正在生成第一道面试题...")], "", \
        gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)

    ordered = state["ordered_skills"]
    if not ordered:
        state["terminated"] = True
        yield state, info_text, [("系统", "未找到可考察的技能维度")], "", \
            gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)
        return

    current = ordered[0]
    skill_name = current["skill"]
    interviewer = InterviewerAgent()

    try:
        accumulated = ""
        last_display = ""

        for delta, done, question in _async_iter(
            interviewer.generate_question_stream(
                jd=jd,
                resume=resume,
                target_skill=skill_name,
                difficulty=_skill_difficulty(current),
                intent=current.get("reason", f"考察 {skill_name}"),
            )
        ):
            if done:
                if question is not None:
                    state["question"] = question
                    display_text = question.content
                else:
                    # 流式失败，回退到非流式
                    state = _generate_next_question(state)
                    display_text = state["question"].content if state.get("question") else "面试开始"
                chat = [("🤖 面试官", display_text)]
                yield state, info_text, chat, "", \
                    gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)
            else:
                accumulated += delta
                # 尝试从部分 JSON 提取可视文字
                display_text = _extract_streaming_text(accumulated)
                if display_text and display_text != last_display:
                    last_display = display_text
                    chat = [("🤖 面试官", display_text + " ▌")]
                    yield state, info_text, chat, "", \
                        gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)

    except Exception as e:
        logger.exception("流式出题异常")
        # 回退：用非流式方式出题
        state = _generate_next_question(state)
        q = state.get("question")
        chat = [("🤖 面试官", q.content if q else "面试开始")]
        yield state, info_text, chat, "", \
            gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)


def _render_report_md(state: dict) -> str:
    """将报告渲染为 Markdown 字符串"""
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


def on_submit(answer, state):
    """提交回答 → 评判 → 下一题 或 结束（generator，支持进度更新）"""
    if state is None:
        yield state, [("系统", "请先开始面试")], "", "", \
            gr.update(visible=True), gr.update(visible=False)
        return

    if not answer.strip():
        answer = "（跳过）"

    # ── 评判 ──
    yield state, _build_chat_with_status(state, "📊 正在评分..."), "", "", \
        gr.update(visible=True), gr.update(visible=False)

    state = _judge_answer(answer, state)

    # 构建聊天历史
    chat = _build_chat_history(state)

    # ── 进度指示 ──
    progress = _build_progress_text(state)
    if progress and chat:
        chat.append(("📊 进度", progress))

    if state.get("terminated"):
        yield state, chat, "", "", \
            gr.update(visible=True), gr.update(visible=False)
        # 生成报告
        state = _generate_report(state)
        report_text = _render_report_md(state)
        chat = _build_chat_history(state)
        chat.append(("📊 进度", "✅ 面试完成，正在生成报告..."))
        yield state, chat, "", report_text, \
            gr.update(visible=False), gr.update(visible=True)
    else:
        # ── 流式生成下一题 ──
        interviewer = InterviewerAgent()
        ordered = state["ordered_skills"]
        skill_idx = state["skill_index"]

        if skill_idx < len(ordered):
            current = ordered[skill_idx]
            skill_name = current["skill"]
            rounds = state["rounds"]
            last_round = rounds[-1] if rounds else None

            accumulated = ""
            last_display = ""

            # 构建流式调用
            async def _stream_next():
                if last_round and last_round.get("judge"):
                    judge = last_round["judge"]
                    action = judge.next_action.strip().lower() if hasattr(judge, "next_action") else ""
                    last_q = last_round["question"]
                    last_a = last_round.get("answer", "")

                    if action == "deepen":
                        async for item in interviewer.generate_deepen_question(
                            jd=state["jd"], resume=state["resume"],
                            target_skill=skill_name,
                            difficulty=last_q.difficulty.value,
                            previous_question=last_q.content,
                            previous_answer=last_a,
                        ):
                            yield item
                        return
                    elif action == "clarify":
                        async for item in interviewer.generate_clarify_question(
                            jd=state["jd"], resume=state["resume"],
                            target_skill=skill_name,
                            difficulty=last_q.difficulty.value,
                            previous_question=last_q.content,
                            previous_answer=last_a,
                        ):
                            yield item
                        return
                    elif action == "switch":
                        new_idx = skill_idx + 1
                        if new_idx < len(ordered):
                            state["skill_index"] = new_idx
                            skill_name = ordered[new_idx]["skill"]
                            async for item in interviewer.generate_switch_question(
                                jd=state["jd"], resume=state["resume"],
                                target_skill=skill_name,
                                difficulty=_skill_difficulty(ordered[new_idx]),
                            ):
                                yield item
                            return
                        else:
                            yield ("", True, None)
                            return

                # 默认：标准出题
                async for item in interviewer.generate_question_stream(
                    jd=state["jd"], resume=state["resume"],
                    target_skill=skill_name,
                    difficulty=_skill_difficulty(current),
                    intent=current.get("reason", f"考察 {skill_name}"),
                ):
                    yield item

            try:
                for delta, done, question in _async_iter(_stream_next()):
                    if done:
                        if question is not None:
                            state["question"] = question
                            display_text = question.content
                        else:
                            state = _generate_next_question(state)
                            display_text = state["question"].content if state.get("question") else ""
                        chat.append(("🤖 面试官", display_text))
                        progress = _build_progress_text(state)
                        if progress:
                            chat.append(("📊 进度", progress))
                        yield state, chat, "", "", \
                            gr.update(visible=True), gr.update(visible=False)
                    else:
                        accumulated += delta
                        display_text = _extract_streaming_text(accumulated)
                        if display_text and display_text != last_display:
                            last_display = display_text
                            temp_chat = chat + [("🤖 面试官", display_text + " ▌")]
                            yield state, temp_chat, "", "", \
                                gr.update(visible=True), gr.update(visible=False)

            except Exception:
                logger.exception("流式出题异常（on_submit）")
                state = _generate_next_question(state)
                q = state.get("question")
                if q:
                    chat.append(("🤖 面试官", q.content if hasattr(q, "content") else str(q)))
                yield state, chat, "", "", \
                    gr.update(visible=True), gr.update(visible=False)
        else:
            state["terminated"] = True
            state = _generate_report(state)
            report_text = _render_report_md(state)
            yield state, chat, "", report_text, \
                gr.update(visible=False), gr.update(visible=True)


def _build_chat_history(state: dict) -> list:
    """从 state.rounds 构建聊天历史"""
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


def _build_chat_with_status(state: dict, status: str) -> list:
    """构建聊天历史 + 状态提示"""
    chat = _build_chat_history(state)
    chat.append(("系统", status))
    return chat


# ── 结束面试回调 ─────────────────────────────────────────


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
    return None, "", [("系统", "请上传新的 JD 和简历文件开始新面试")], "", "", \
           gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)


# ── 构建 Gradio 界面 ─────────────────────────────────────


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="AI 面试官") as demo:

        # 状态存储
        interview_state = gr.State()

        gr.Markdown("# 🎯 AI 面试官")
        gr.Markdown("*从 JD 解析到面试到反馈，完整闭环 — 支持流式出题*")

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
            chatbot = gr.Chatbot(label="面试对话", height=450)
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
            outputs=[interview_state, info_md, chatbot, answer_input, report_md, upload_col, interview_col, report_col],
        )

    return demo


# ── Gradio 实例（供 Gateway 挂载）─────────────────────────

demo = build_ui()
