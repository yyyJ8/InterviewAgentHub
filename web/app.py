"""AI 面试官 — Streamlit Web UI（Phase 1 MVP）"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import streamlit as st

# ── 页面配置（必须第一行） ──────────────────────────

st.set_page_config(
    page_title="AI 面试官",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 异步运行辅助 ─────────────────────────────────────

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── 模型 / Agent 导入 ───────────────────────────────

from agents.jd_parser import JDParserAgent
from agents.resume_analyzer import ResumeAnalyzerAgent
from agents.interviewer import InterviewerAgent
from orchestration.matcher import generate_gap_map
from tools import parse_file

# ── 初始化 Session State ─────────────────────────────

_DEFAULT = {
    "page": "upload",
    "jd": None,
    "resume": None,
    "gap_map": None,
    "ordered_skills": [],
    "skill_index": 0,
    "question": None,
    "answer": "",
    "judge_result": None,
    "history": [],
    "interview_id": "",
    "error": None,
}

for key, val in _DEFAULT.items():
    if key not in st.session_state:
        st.session_state[key] = val


# ── 辅助渲染函数 ─────────────────────────────────────

def _render_gap_map():
    """展示能力缺口分析"""
    gap_map = st.session_state.gap_map
    if not gap_map:
        st.info("暂无数据")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("技能总数", gap_map.get("skill_count", 0))
    col2.metric("优势技能", len(gap_map.get("strengths", [])))
    col3.metric("缺口技能", len(gap_map.get("gaps", [])))

    for item in gap_map.get("ordered_skills", []):
        gap = item["gap"]
        if gap == "有项目经验":
            st.success(f"✅ {item['skill']} (权重{item['weight']}) — {item['reason']}")
        elif gap == "缺口":
            st.error(f"❌ {item['skill']} (权重{item['weight']}) — {item['reason']}")
        else:
            st.warning(f"⚠️ {item['skill']} (权重{item['weight']}) — {item['reason']}")


def _run_interview_setup(jd_file, resume_file):
    """上传 → 解析 → 匹配 → 出题"""
    upload_dir = Path("uploads")
    upload_dir.mkdir(exist_ok=True)

    jd_path = upload_dir / jd_file.name
    resume_path = upload_dir / resume_file.name
    jd_path.write_bytes(jd_file.getvalue())
    resume_path.write_bytes(resume_file.getvalue())

    # 1. 解析文件
    jd_raw = parse_file(str(jd_path))
    resume_raw = parse_file(str(resume_path))

    # 2. LLM 解析 JD + 简历
    jd_agent = JDParserAgent()
    resume_agent = ResumeAnalyzerAgent()
    jd = run_async(jd_agent.run(jd_raw))
    resume = run_async(resume_agent.run(resume_raw))

    st.session_state.jd = jd
    st.session_state.resume = resume
    st.session_state.interview_id = uuid.uuid4().hex[:8]

    # 3. 交叉匹配
    gap_map = generate_gap_map(jd, resume)
    st.session_state.gap_map = gap_map
    st.session_state.ordered_skills = gap_map["ordered_skills"]
    st.session_state.skill_index = 0

    # 4. 生成第一题
    interviewer = InterviewerAgent()
    target = gap_map["ordered_skills"][0]
    question = run_async(
        interviewer.generate_question(
            jd=jd,
            resume=resume,
            target_skill=target["skill"],
            difficulty="intermediate",
            intent=target.get("reason", ""),
        )
    )
    st.session_state.question = question
    st.session_state.history = [
        ("assistant", f"📋 岗位: {jd.title} | 候选人: {resume.name}"),
        ("assistant", f"🎯 第一题（考察 {question.skill}）:\n\n{question.content}"),
    ]


def _run_judge(answer: str):
    """执行评判"""
    try:
        interviewer = InterviewerAgent()
        result = run_async(
            interviewer.judge_answer(
                question=st.session_state.question,
                answer=answer,
            )
        )
        st.session_state.judge_result = result
        st.session_state.history.append(
            ("assistant", f"📊 **评分**: {result.score}/100\n\n{result.comment}")
        )
    except Exception as e:
        st.error(f"评判失败: {e}")
        st.session_state.error = str(e)


# ── 页面组件 ─────────────────────────────────────────

def render_upload_page():
    st.header("📄 上传 JD 与简历")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("岗位描述 (JD)")
        jd_file = st.file_uploader(
            "上传 JD 文件", type=["pdf", "docx", "txt"], key="jd_uploader"
        )
        if jd_file:
            st.success(f"已上传: {jd_file.name}")

    with col2:
        st.subheader("简历 (Resume)")
        resume_file = st.file_uploader(
            "上传简历文件", type=["pdf", "docx", "txt"], key="resume_uploader"
        )
        if resume_file:
            st.success(f"已上传: {resume_file.name}")

    st.markdown("---")

    can_start = jd_file is not None and resume_file is not None
    if st.button("🚀 开始面试", type="primary", disabled=not can_start):
        with st.spinner("正在解析文件并生成面试题..."):
            try:
                _run_interview_setup(jd_file, resume_file)
                st.session_state.page = "interview"
                st.rerun()
            except Exception as e:
                st.error(f"初始化失败: {e}")
                st.session_state.error = str(e)


def render_interview_page():
    st.header("🎙️ 面试进行中")

    jd = st.session_state.jd
    resume = st.session_state.resume
    if jd and resume:
        col1, col2, col3 = st.columns(3)
        col1.metric("岗位", jd.title)
        col2.metric("候选人", resume.name)
        col3.metric("技能总数", len(st.session_state.ordered_skills))

    with st.expander("📊 能力缺口分析", expanded=False):
        _render_gap_map()

    st.markdown("---")

    # 对话历史
    for role, content in st.session_state.history:
        if role == "assistant":
            st.markdown(f"**🤖 面试官**\n\n{content}")
        else:
            st.markdown(f"**🧑‍💻 你**\n\n{content}")
        st.markdown("---")

    # 回答区
    question = st.session_state.question
    if question and not st.session_state.judge_result:
        st.markdown("### 💬 请回答")
        st.info(f"考察技能: **{question.skill}** | 难度: **{question.difficulty.value}**")

        answer = st.text_area(
            "你的回答",
            value=st.session_state.answer,
            placeholder="请在此输入你的回答...",
            height=200,
            key="answer_input",
        )

        if st.button("📨 提交答案", type="primary"):
            if not answer.strip():
                st.warning("请先输入回答")
            else:
                st.session_state.answer = answer
                st.session_state.history.append(("user", answer))
                with st.spinner("正在评判答案..."):
                    _run_judge(answer)
                st.rerun()

    # 查看报告按钮
    if st.session_state.judge_result:
        if st.button("📋 查看完整评分报告", type="primary"):
            st.session_state.page = "result"
            st.rerun()


def render_result_page():
    st.header("📋 面试报告")

    jd = st.session_state.jd
    resume = st.session_state.resume
    result = st.session_state.judge_result

    if jd and resume:
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("📌 岗位信息")
            st.markdown(f"**岗位**: {jd.title}")
            if jd.company:
                st.markdown(f"**公司**: {jd.company}")
            st.markdown(f"**技能要求**: {', '.join(s.name for s in jd.required_skills[:5])}")

        with col2:
            st.subheader("👤 候选人")
            st.markdown(f"**姓名**: {resume.name}")
            if resume.title:
                st.markdown(f"**职位**: {resume.title}")
            st.markdown(f"**经验**: {resume.experience_years or '未知'} 年")

    st.markdown("---")

    if result:
        score = result.score
        color = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")

        st.subheader(f"{color} 评分结果")
        st.markdown(f"### {score}/100")

        st.markdown(f"**综合评价**\n{result.comment}")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**💪 亮点**")
            for p in result.strength_points:
                st.markdown(f"- {p}")
        with col2:
            st.markdown("**📈 待改进**")
            for p in result.weakness_points:
                st.markdown(f"- {p}")

        st.markdown(f"**下一步**: {result.next_action}")

    # 能力缺口
    with st.expander("📊 完整能力缺口分析", expanded=True):
        _render_gap_map()

    # 题目回顾
    if st.session_state.question:
        with st.expander("💬 面试题目回顾", expanded=False):
            q = st.session_state.question
            st.markdown(f"**技能**: {q.skill} | **难度**: {q.difficulty.value}")
            st.markdown(f"**问题**: {q.content}")
            if q.expected_answer_points:
                st.markdown("**预期得分点**:")
                for p in q.expected_answer_points:
                    st.markdown(f"- {p}")

    # 面试记录
    if st.session_state.history:
        with st.expander("💬 完整面试记录", expanded=False):
            for role, content in st.session_state.history:
                speaker = "🤖 面试官" if role == "assistant" else "🧑‍💻 你"
                st.markdown(f"**{speaker}**: {content}")
                st.markdown("---")

    st.markdown("---")
    if st.button("🔄 再来一轮", type="primary"):
        for key in _DEFAULT:
            st.session_state[key] = _DEFAULT[key]
        st.rerun()


# ── 页面路由 ─────────────────────────────────────────

def main():
    with st.sidebar:
        st.title("🎯 AI 面试官")
        st.markdown("---")
        st.markdown("**从 JD 解析到面试到反馈，完整闭环**")
        st.markdown("---")

        if st.session_state.page == "upload":
            st.info("📤 上传文件开始面试")
        elif st.session_state.page == "interview":
            st.warning("🎙️ 面试进行中")
        elif st.session_state.page == "result":
            st.success("✅ 面试已完成")

        if st.button("🔄 重新开始"):
            for key in _DEFAULT:
                st.session_state[key] = _DEFAULT[key]
            st.rerun()

        st.markdown("---")
        st.caption("Phase 1 MVP · 单轮面试")

    # 路由到对应页面
    if st.session_state.page == "upload":
        render_upload_page()
    elif st.session_state.page == "interview":
        render_interview_page()
    elif st.session_state.page == "result":
        render_result_page()


if __name__ == "__main__":
    main()
