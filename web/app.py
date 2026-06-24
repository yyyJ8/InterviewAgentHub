"""AI 面试官 — Gradio Web UI（Phase 6 前后端分离）

基于 Gradio 5 Blocks，三步流程：
  1. 上传 JD + 简历 → 点击开始
  2. 面试对话（流式题目 ↔ 回答 ↔ 评分 → 下一题）
  3. 面试报告展示

Phase 6 改进：
  - 前端不再直接调 Agent/supervisor，所有编排走 Gateway HTTP API
  - 全部题目支持 SSE 流式输出
  - 前端只负责界面展示，不持有业务逻辑
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path

import gradio as gr
import httpx

from config import config

logger = logging.getLogger("web.ui")

GATEWAY_BASE = f"http://127.0.0.1:{config.gateway_port}"

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)


def _gateway_url(path: str) -> str:
    return f"{GATEWAY_BASE}{path}"


def _extract_file_path(file_obj) -> str:
    """从 Gradio 5 的各种返回类型中提取文件路径。"""
    if isinstance(file_obj, str):
        return file_obj
    path = getattr(file_obj, "path", None) or getattr(file_obj, "name", None)
    if path:
        return str(path)
    raise ValueError(f"无法提取文件路径: {type(file_obj).__name__}")


# ── 显示辅助函数 ───────────────────────────────────────────

def _build_info_text(jd: dict, resume: dict, gap: dict) -> str:
    """构建能力缺口分析文本。"""
    jd_title = jd.get("title", "") if jd else ""
    resume_name = resume.get("name", "") if resume else ""
    resume_exp = resume.get("experience_years", "未知") if resume else "未知"

    info_text = (
        f"## 🎯 {jd_title}\n\n"
        f"**候选人**: {resume_name}  |  "
        f"**经验**: {resume_exp} 年\n\n---\n"
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
    """构建进度信息。"""
    rounds_cache = state.get("rounds_cache", [])
    completed = len(rounds_cache)
    skills_ordered = state.get("skills_ordered", [])
    total_skills = len(skills_ordered)
    return (
        f"---\n"
        f"📊 **已完成 {completed} 轮  |  "
        f"技能 {min(completed, total_skills)}/{total_skills}**\n"
        f"---"
    )


def _build_chat_history(state: dict) -> list:
    """构建 Gradio 5 Chatbot 格式: [(role, content), ...]。"""
    chat = []
    for r in state.get("rounds_cache", []):
        if not isinstance(r, dict):
            continue
        q = r.get("question", {}) or {}
        a_text = r.get("answer", "")
        j = r.get("judge", {}) or {}

        q_text = q.get("content", "") if isinstance(q, dict) else ""
        score = j.get("score", 0) if isinstance(j, dict) else 0
        comment = j.get("comment", "") if isinstance(j, dict) else ""

        if q_text:
            chat.append(("🤖 面试官", q_text))
        if a_text:
            chat.append(("🧑‍💻 你", a_text))
        if comment:
            chat.append(("📊 评分", f"**{score}/100** — {comment}"))
    return chat


def _render_report_md(state: dict) -> str:
    """渲染面试报告为 Markdown。"""
    if state is None or state.get("report") is None:
        return "暂无报告"

    report = state["report"]
    rounds = state.get("rounds_cache", [])

    lines = ["# 📋 面试报告\n"]
    jd = state.get("jd", {})
    resume = state.get("resume", {})
    jd_title = jd.get("title", "") if jd else ""
    resume_name = resume.get("name", "") if resume else ""
    if jd_title or resume_name:
        lines.append(f"**岗位**: {jd_title}  |  **候选人**: {resume_name}\n")

    score = report.get("total_score", 0) if isinstance(report, dict) else 0
    emoji = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")
    lines.append(f"## {emoji} 总分: {score:.0f}/100\n")

    rec = report.get("hiring_recommendation", "") if isinstance(report, dict) else ""
    rec_map = {
        "strong_yes": "✅ 强烈推荐", "yes": "👍 推荐录用",
        "hesitate": "🤔 待定", "no": "❌ 不推荐",
    }
    lines.append(f"**录用建议**: {rec_map.get(rec, rec)}\n")

    overall = report.get("overall_assessment", "") if isinstance(report, dict) else ""
    if overall:
        lines.append(f"**总体评价**: {overall}\n")

    dims = report.get("dimension_scores", {}) if isinstance(report, dict) else {}
    if dims:
        lines.append("### 🎯 五维度评估\n")
        for dim, s in dims.items():
            bar = "█" * int(s / 5) + "░" * (20 - int(s / 5))
            lines.append(f"- **{dim}**: {bar} {s:.0f}/100")

    strengths = report.get("strengths", []) if isinstance(report, dict) else []
    weaknesses = report.get("weaknesses", []) if isinstance(report, dict) else []
    if strengths:
        lines.append("\n### 🌟 亮点\n")
        for s_item in strengths:
            lines.append(f"- ✅ {s_item}")
    if weaknesses:
        lines.append("\n### 📈 待改进\n")
        for w in weaknesses:
            lines.append(f"- ⚠️ {w}")

    suggestions = report.get("suggestions", []) if isinstance(report, dict) else []
    if suggestions:
        lines.append("\n### 💡 改进建议\n")
        for s_item in suggestions:
            lines.append(f"- {s_item}")

    lines.append("\n---\n### 💬 面试全程回顾\n")
    for i, r in enumerate(rounds, 1):
        if not isinstance(r, dict):
            continue
        q = r.get("question", {}) or {}
        a_text = r.get("answer", "")
        j = r.get("judge", {}) or {}

        q_content = q.get("content", "") if isinstance(q, dict) else ""
        s = j.get("score", 0) if isinstance(j, dict) else 0
        diff_val = q.get("difficulty", "") if isinstance(q, dict) else ""
        skill_name = q.get("skill", "") if isinstance(q, dict) else r.get("skill", "")

        lines.append(f"**第 {i} 轮** — {skill_name} ({diff_val}) — {s}/100")
        lines.append(f"> 🤖 {q_content[:150]}...")
        lines.append(f"> 🧑‍💻 {a_text[:150] if a_text else '(未作答)'}")
        lines.append("")

    return "\n".join(lines)


# ── Gradio 回调 ────────────────────────────────────────────

async def on_start(jd_file, resume_file):
    """如  Generator: 逐步 yield 进度，UI 实时更新。"""
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

        # 复制文件到持久化目录（gradio 临时文件可能被清理）
        persistent_jd = UPLOADS_DIR / f"jd_{uuid.uuid4().hex[:8]}{Path(jd_path).suffix}"
        persistent_resume = UPLOADS_DIR / f"resume_{uuid.uuid4().hex[:8]}{Path(resume_path).suffix}"
        shutil.copy2(jd_path, persistent_jd)
        shutil.copy2(resume_path, persistent_resume)

        # ── Step 1: 解析文件进度提示 ──
        yield (
            None, "📄 正在解析文件...",
            [("系统", "📄 正在解析 JD 和简历文件...")], "",
            gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=False),
        )

        # ── Step 2: 初始化面试 ──
        yield (
            None, "🤖 正在分析岗位需求与候选人背景...",
            [("系统", "🤖 AI 正在提取 JD 技能、分析简历、匹配缺口...")], "",
            gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=False),
        )
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                _gateway_url("/api/v1/interview"),
                json={
                    "jd_path": str(persistent_jd),
                    "resume_path": str(persistent_resume),
                    "candidate_name": "匿名",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        interview_id = data["interview_id"]
        jd = data.get("jd", {})
        resume = data.get("resume", {})
        gap_analysis = data.get("gap_analysis", {})
        skills_ordered = data.get("skills_ordered", [])

        state = {
            "interview_id": interview_id,
            "candidate_name": data.get("candidate_name", "匿名"),
            "jd": jd,
            "resume": resume,
            "gap_analysis": gap_analysis,
            "skills_ordered": skills_ordered,
            "rounds_cache": [],
            "terminated": False,
            "report": None,
        }

        info_text = _build_info_text(jd, resume, gap_analysis)

        if not skills_ordered:
            yield (
                state, info_text,
                [("系统", "未找到需要考察的技能维度，请检查 JD 内容")], "",
                gr.update(visible=False), gr.update(visible=True),
                gr.update(visible=False),
            )
            return

        # ── Step 3: 流式出第一题 ──
        question_text = ""

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "GET", _gateway_url(f"/api/v1/interview/{interview_id}/stream-question"),
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"流式出题失败: HTTP {response.status_code}")

                current_event = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        current_event = line[7:]
                    elif line.startswith("data: "):
                        payload = line[6:]
                        if current_event == "token":
                            question_text += payload
                            yield (
                                state, info_text,
                                [("🤖 面试官", question_text + " ▌")],
                                "",
                                gr.update(visible=False), gr.update(visible=True),
                                gr.update(visible=False),
                            )
                        elif current_event == "complete":
                            try:
                                question_obj = json.loads(payload)
                                question_text = question_obj.get("content", question_text)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        elif current_event == "error":
                            raise RuntimeError(payload)

        chat = [("🤖 面试官", question_text)]
        progress = _build_progress_text(state)
        if progress:
            chat.append(("📊 进度", progress))

        yield (
            state, info_text, chat, "",
            gr.update(visible=False), gr.update(visible=True),
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
    """提交回答 → 评判 → 出下一题 / 生成报告。"""
    if state is None:
        yield (
            state, [("系统", "请先开始面试")], "", "",
            gr.update(visible=True), gr.update(visible=False),
        )
        return

    interview_id = state.get("interview_id", "")
    if not interview_id:
        yield (
            state, [("系统", "面试 ID 丢失，请重新开始")], "", "",
            gr.update(visible=True), gr.update(visible=False),
        )
        return

    try:
        # ── 提交评判 ──
        async with httpx.AsyncClient(timeout=300.0) as client:
            judge_resp = await client.post(
                _gateway_url(f"/api/v1/interview/{interview_id}/judge"),
                json={"answer": answer or ""},
            )
            judge_resp.raise_for_status()
            judge_data = judge_resp.json()

        state["terminated"] = judge_data.get("terminated", False)
        if "rounds" in judge_data:
            state["rounds_cache"] = judge_data["rounds"]

        chat = _build_chat_history(state)
        progress = _build_progress_text(state)
        if progress:
            chat.append(("📊 进度", progress))

        # ── 终止 → 获取报告 ──
        if state["terminated"]:
            async with httpx.AsyncClient(timeout=60.0) as client:
                report_resp = await client.get(
                    _gateway_url(f"/api/v1/interview/{interview_id}/report"),
                )
                report_resp.raise_for_status()
                report_data = report_resp.json()

            state["report"] = report_data.get("report")
            if "rounds" in report_data:
                state["rounds_cache"] = report_data["rounds"]

            chat = _build_chat_history(state)
            progress = _build_progress_text(state)
            if progress:
                chat.append(("📊 进度", progress))
            report_text = _render_report_md(state)
            yield (
                state, chat, "", report_text,
                gr.update(visible=False), gr.update(visible=True),
            )
            return

        # ── 继续 → 流式出下一题 ──
        question_text = ""
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "GET", _gateway_url(f"/api/v1/interview/{interview_id}/stream-question"),
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"流式出题失败: HTTP {response.status_code}")

                current_event = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        current_event = line[7:]
                    elif line.startswith("data: "):
                        payload = line[6:]
                        if current_event == "token":
                            question_text += payload
                            display_chat = list(chat)
                            display_chat.append(("🤖 面试官", question_text + " ▌"))
                            yield (
                                state, display_chat, "", "",
                                gr.update(visible=True), gr.update(visible=False),
                            )
                        elif current_event == "complete":
                            try:
                                question_obj = json.loads(payload)
                                question_text = question_obj.get("content", question_text)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        elif current_event == "error":
                            raise RuntimeError(payload)

        if question_text:
            chat.append(("🤖 面试官", question_text))
            new_progress = _build_progress_text(state)
            if new_progress:
                chat.append(("📊 进度", new_progress))

        yield (
            state, chat, "", "",
            gr.update(visible=True), gr.update(visible=False),
        )

    except Exception as e:
        logger.exception("面试对话失败")
        chat = _build_chat_history(state)
        chat.append(("系统", f"❌ 出错: {e}"))
        yield (
            state, chat, "", "",
            gr.update(visible=True), gr.update(visible=False),
        )


async def _end_interview(state):
    """手动结束面试 → 获取报告。"""
    if state is None:
        return (
            state, [], "", "",
            gr.update(visible=True), gr.update(visible=False),
        )

    interview_id = state.get("interview_id", "")
    if not interview_id:
        return (
            state, [], "", "",
            gr.update(visible=True), gr.update(visible=False),
        )

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            report_resp = await client.get(
                _gateway_url(f"/api/v1/interview/{interview_id}/report"),
            )
            report_resp.raise_for_status()
            report_data = report_resp.json()

        state["report"] = report_data.get("report")
        state["terminated"] = True
        if "rounds" in report_data:
            state["rounds_cache"] = report_data["rounds"]
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
