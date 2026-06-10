"""AI 面试官 — Streamlit Web UI（Phase 2: 多轮面试 + 完整报告）"""

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


# ── 导入 ─────────────────────────────────────────────

from agents.jd_parser import JDParserAgent
from agents.resume_analyzer import ResumeAnalyzerAgent
from agents.interviewer import InterviewerAgent
from agents.feedback import FeedbackAgent
from orchestration.matcher import generate_gap_map
from tools import parse_file
from config import config

# ── 初始化 Session State ─────────────────────────────

_DEFAULT = {
    "page": "upload",
    "jd": None,
    "resume": None,
    "gap_map": None,
    "ordered_skills": [],
    "skill_index": 0,
    "rounds": [],                # list of dict: {question, answer, judge}
    "current_round": 1,
    "question": None,
    "answer": "",
    "judge_result": None,
    "interview_id": "",
    "terminated": False,
    "report": None,              # InterviewReport from FeedbackAgent
    "report_loading": False,
    "error": None,
}

for key, val in _DEFAULT.items():
    if key not in st.session_state:
        st.session_state[key] = val


# ── 常量 ──────────────────────────────────────────────

DIFFICULTY_ORDER = ["basic", "intermediate", "advanced", "deep"]

def _skill_difficulty(item: dict) -> str:
    gap = item.get("gap", "")
    if gap == "有项目经验":
        return "intermediate"
    return "basic"

def _next_difficulty(current: str) -> str:
    idx = DIFFICULTY_ORDER.index(current) if current in DIFFICULTY_ORDER else 0
    return DIFFICULTY_ORDER[min(idx + 1, len(DIFFICULTY_ORDER) - 1)]


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


def _render_progress():
    """显示面试进度"""
    total = len(st.session_state.ordered_skills)
    current_skill = st.session_state.ordered_skills[st.session_state.skill_index]["skill"] if st.session_state.ordered_skills else "—"
    rounds_done = len(st.session_state.rounds)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("轮次", f"{rounds_done} / {config.max_rounds}")
    col2.metric("当前技能", current_skill)
    col3.metric("待考察技能", max(0, total - st.session_state.skill_index))
    col4.metric("技能总数", total)


def _render_interview_history():
    """渲染面试对话历史"""
    for i, r in enumerate(st.session_state.rounds, 1):
        q = r["question"]
        judge = r["judge"]

        with st.container(border=True):
            col_round, col_skill, col_score = st.columns([1, 3, 1])
            col_round.markdown(f"**第 {i} 轮**")
            col_skill.markdown(f"技能: **{q.skill}** | 难度: **{q.difficulty.value}**")
            score = judge["score"] if judge else 0
            color = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")
            col_score.markdown(f"{color} **{score}/100**")

            with st.expander(f"📝 题目 & 回答", expanded=(i == len(st.session_state.rounds))):
                st.markdown(f"**🤖 面试官**: {q['content']}")
                if q.get("context"):
                    st.caption(f"💡 {q['context']}")
                st.markdown(f"**🧑‍💻 你**: {r['answer'] or '（未作答）'}")
                if judge:
                    st.markdown(f"**📊 评价**: {judge['comment']}")
                    if judge.get("strength_points"):
                        st.markdown("**💪 亮点**: " + ", ".join(judge["strength_points"]))
                    if judge.get("weakness_points"):
                        st.markdown("**📈 待改进**: " + ", ".join(judge["weakness_points"]))

    # 当前题目（如果还未提交）
    question = st.session_state.question
    if question and not st.session_state.judge_result:
        st.markdown("---")
        cols = st.columns([1, 5, 1])
        cols[1].info(f"🎯 当前题目（第 {len(st.session_state.rounds) + 1} 轮）")
        st.markdown(f"**🤖 面试官**\n\n{question.content}")
        if question.context:
            st.caption(f"💡 出题背景: {question.context}")
        st.caption(f"考察技能: **{question.skill}** | 难度: **{question.difficulty.value}**")


# ── 面试逻辑函数 ────────────────────────────────────

def _generate_report():
    """使用 FeedbackAgent 生成完整评分报告"""
    if st.session_state.report_loading:
        return
    st.session_state.report_loading = True

    try:
        agent = FeedbackAgent()
        report = run_async(agent.generate_report(
            jd=st.session_state.jd,
            resume=st.session_state.resume,
            rounds=st.session_state.rounds,
        ))
        st.session_state.report = report
    except Exception as e:
        st.error(f"报告生成失败: {e}")
    finally:
        st.session_state.report_loading = False


def _init_interview(jd_file, resume_file):
    """上传 → 解析 → 匹配 → 生成第一题"""
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
    _generate_question()


def _generate_question():
    """根据当前策略生成下一道题"""
    interviewer = InterviewerAgent()
    rounds = st.session_state.rounds
    ordered = st.session_state.ordered_skills
    skill_idx = st.session_state.skill_index
    jd = st.session_state.jd
    resume = st.session_state.resume

    if not ordered or skill_idx >= len(ordered):
        st.session_state.terminated = True
        return

    current_skill = ordered[skill_idx]
    skill_name = current_skill["skill"]
    last_round = rounds[-1] if rounds else None

    try:
        if last_round and last_round.get("judge"):
            judge = last_round["judge"]
            action = judge.get("next_action", "").strip().lower()
            last_q = last_round["question"]
            last_a = last_round["answer"]

            if action == "deepen":
                question = run_async(interviewer.generate_deepen_question(
                    jd=jd, resume=resume,
                    target_skill=skill_name,
                    difficulty=last_q.difficulty.value,
                    previous_question=last_q.content,
                    previous_answer=last_a,
                ))
                st.info(f"🔍 答得不错，追问加深「{skill_name}」的难度 → {question.difficulty.value}")
            elif action == "clarify":
                question = run_async(interviewer.generate_clarify_question(
                    jd=jd, resume=resume,
                    target_skill=skill_name,
                    difficulty=last_q.difficulty.value,
                    previous_question=last_q.content,
                    previous_answer=last_a,
                ))
                st.info(f"💬 需要更具体一些，引导澄清「{skill_name}」")
            elif action == "switch":
                new_idx = skill_idx + 1
                if new_idx < len(ordered):
                    st.session_state.skill_index = new_idx
                    skill_name = ordered[new_idx]["skill"]
                    question = run_async(interviewer.generate_switch_question(
                        jd=jd, resume=resume,
                        target_skill=skill_name,
                        difficulty=_skill_difficulty(ordered[new_idx]),
                    ))
                    st.info(f"🔄 换下一技能 → 考察「{skill_name}」")
                else:
                    st.session_state.terminated = True
                    return
            else:
                # 默认继续
                question = run_async(interviewer.generate_question(
                    jd=jd, resume=resume,
                    target_skill=skill_name,
                    difficulty=_skill_difficulty(current_skill),
                    intent=current_skill.get("reason", ""),
                ))
        else:
            # 首轮出题
            question = run_async(interviewer.generate_question(
                jd=jd, resume=resume,
                target_skill=skill_name,
                difficulty=_skill_difficulty(current_skill),
                intent=current_skill.get("reason", f"考察 {skill_name}"),
            ))

        st.session_state.question = question
        st.session_state.judge_result = None
        st.session_state.answer = ""

    except Exception as e:
        st.error(f"出题失败: {e}")
        st.session_state.error = str(e)


def _judge_answer(answer: str):
    """评判回答并记录轮次"""
    try:
        interviewer = InterviewerAgent()
        question = st.session_state.question
        result = run_async(interviewer.judge_answer(question, answer))

        # 记录这一轮
        round_data = {
            "question": question,
            "answer": answer,
            "judge": result,
        }
        st.session_state.rounds.append(round_data)
        st.session_state.judge_result = result

        # 检查终止条件
        is_empty = not answer.strip()
        if is_empty:
            # 计算累计空回答
            empty_rounds = sum(1 for r in st.session_state.rounds if not r["answer"].strip())
            if empty_rounds >= config.max_consecutive_empty:
                st.session_state.terminated = True
                return

        if len(st.session_state.rounds) >= config.max_rounds:
            st.session_state.terminated = True
            return

        next_action = result.next_action.strip().lower() if result.next_action else "continue"

        if next_action == "switch":
            new_idx = st.session_state.skill_index + 1
            if new_idx >= len(st.session_state.ordered_skills):
                st.session_state.terminated = True
                return
            st.session_state.skill_index = new_idx

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
                _init_interview(jd_file, resume_file)
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
        col1, col2 = st.columns(2)
        col1.metric("岗位", jd.title)
        col2.metric("候选人", resume.name)

    # 进度指示
    _render_progress()

    with st.expander("📊 能力缺口分析", expanded=False):
        _render_gap_map()

    st.markdown("---")

    # 面试对话历史
    _render_interview_history()

    # ── 回答区域 ──
    if st.session_state.question and not st.session_state.terminated:
        if st.session_state.judge_result:
            # 刚刚评判完，显示结果 + 继续按钮
            judge = st.session_state.judge_result
            score = judge.score
            color = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")
            st.markdown(f"### {color} 本轮评分: {score}/100")
            st.markdown(f"**评价**: {judge.comment}")

            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**💪 亮点**")
                for p in judge.strength_points:
                    st.markdown(f"- {p}")
            with col2:
                st.markdown("**📈 待改进**")
                for p in judge.weakness_points:
                    st.markdown(f"- {p}")

            st.markdown(f"**下一步**: {judge.next_action}")

            st.markdown("---")
            col_next, col_end = st.columns([3, 1])
            with col_next:
                next_text = "🔍 继续追问" if judge.next_action in ("deepen", "clarify") else \
                            "🔄 换下一技能" if judge.next_action == "switch" else \
                            "📋 查看报告" if judge.next_action == "end" else "继续面试"
                if st.button(next_text, type="primary", use_container_width=True):
                    # 已达终止条件
                    if judge.next_action == "end" or st.session_state.terminated:
                        st.session_state.page = "result"
                        st.rerun()
                    else:
                        with st.spinner("正在生成下一题..."):
                            _generate_question()
                        st.rerun()
            with col_end:
                if st.button("⏹ 结束面试"):
                    st.session_state.terminated = True
                    st.session_state.page = "result"
                    st.rerun()
        else:
            # 等待用户作答
            st.markdown("---")
            st.markdown("### 💬 请回答")

            skill = st.session_state.question.skill
            diff = st.session_state.question.difficulty.value
            st.info(f"考察技能: **{skill}** | 难度: **{diff}**")

            answer = st.text_area(
                "你的回答",
                value=st.session_state.answer,
                placeholder="请在此输入你的回答...",
                height=200,
                key=f"answer_input_{len(st.session_state.rounds)}",
            )

            if st.button("📨 提交答案", type="primary"):
                if not answer.strip():
                    st.warning("请先输入回答（或输入「跳过」跳过此题）")
                else:
                    with st.spinner("正在评判答案..."):
                        _judge_answer(answer)
                    st.rerun()

    elif st.session_state.terminated:
        # 面试结束
        st.success("✅ 面试已结束")
        _render_interview_summary()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("📋 生成完整评分报告", type="primary", use_container_width=True):
                if not st.session_state.report:
                    with st.spinner("正在生成综合评分报告..."):
                        _generate_report()
                st.session_state.page = "result"
                st.rerun()
        with col2:
            if st.button("📝 查看基础记录", use_container_width=True):
                st.session_state.page = "result"
                st.rerun()


def _render_interview_summary():
    """显示面试简要总结"""
    rounds = st.session_state.rounds
    if not rounds:
        return

    avg_score = sum(r["judge"].score for r in rounds if r.get("judge")) / len(rounds) if rounds else 0
    covered_skills = list(set(r["question"].skill for r in rounds if r.get("question")))

    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    col1.metric("总轮次", len(rounds))
    col2.metric("平均分", f"{avg_score:.0f}/100")
    col3.metric("覆盖技能", len(covered_skills))

    with st.expander("📝 完整面试记录", expanded=True):
        for i, r in enumerate(rounds, 1):
            q = r["question"]
            judge = r["judge"]
            score = judge.score if judge else 0
            score_icon = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")
            st.markdown(f"**第 {i} 轮** — {q.skill} ({q.difficulty.value}) {score_icon} {score}/100")
            st.markdown(f"> 🤖 {q.content}")
            st.markdown(f"> 🧑‍💻 {r['answer']}")
            st.markdown(f"> 📊 {judge.comment}" if judge else "")
            st.markdown("---")


def render_result_page():
    st.header("📋 面试报告")

    jd = st.session_state.jd
    resume = st.session_state.resume
    rounds = st.session_state.rounds
    report = st.session_state.report  # InterviewReport from FeedbackAgent

    # ── 基本信息 ──
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

    # ── 报告内容 ──
    if report:
        _render_full_report(report, rounds)
    else:
        _render_basic_scores(rounds)
        if st.button("📊 生成完整评分报告", type="primary"):
            with st.spinner("正在生成综合评分报告..."):
                _generate_report()
            st.rerun()

    # ── 能力缺口 ──
    with st.expander("📊 完整能力缺口分析", expanded=False):
        _render_gap_map()

    # ── 面试全程回顾 ──
    _render_all_rounds_detail(rounds)

    # ── 操作按钮 ──
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 再来一场面试", type="primary", use_container_width=True):
            for key in _DEFAULT:
                st.session_state[key] = _DEFAULT[key]
            st.rerun()
    with col2:
        if st.button("📝 继续当前面试", use_container_width=True):
            st.session_state.page = "interview"
            st.rerun()


def _render_full_report(report, rounds):
    """渲染反馈 Agent 生成的完整报告（5 维度评分）"""
    st.subheader("📊 综合评分报告")

    score = report.total_score
    color = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")
    st.markdown(f"### {color} 总分: {score:.1f}/100")

    rec_map = {
        "strong_yes": ("✅ 强烈推荐", "green"),
        "yes": ("👍 推荐录用", "blue"),
        "hesitate": ("🤔 待定", "orange"),
        "no": ("❌ 不推荐", "red"),
    }
    rec_label, _ = rec_map.get(report.hiring_recommendation, ("—", "gray"))
    st.markdown(f"**录用建议**: {rec_label}")
    st.markdown(f"**总体评价**: {report.overall_assessment}")

    # 5 维度得分 + 柱状图
    st.markdown("---")
    st.subheader("🎯 五维度能力评估")
    dim_data = report.dimension_scores
    if dim_data:
        try:
            import pandas as pd
            df = pd.DataFrame({
                "维度": list(dim_data.keys()),
                "得分": list(dim_data.values()),
            })
            st.bar_chart(df.set_index("维度"), height=300)
        except ImportError:
            pass

        dim_cols = st.columns(len(dim_data))
        for i, (dim, s) in enumerate(dim_data.items()):
            dim_cols[i].metric(dim, f"{s:.0f}/100")

    # 技能评估明细
    st.markdown("---")
    st.subheader("💪 技能评估明细")
    if report.skill_scores:
        for item in report.skill_scores:
            s = item.get("score", 0)
            st.markdown(f"**{item.get('skill', '?')}**: {s:.0f}/100")
            st.progress(s / 100)
            if item.get("comment"):
                st.caption(f"💬 {item['comment']}")
            st.markdown("---")

    # 亮点与不足
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🌟 亮点")
        if report.strengths:
            for s in report.strengths:
                st.markdown(f"- ✅ {s}")
        else:
            st.info("暂无数据")
    with col2:
        st.subheader("📈 待改进")
        if report.weaknesses:
            for w in report.weaknesses:
                st.markdown(f"- ⚠️ {w}")
        else:
            st.info("暂无数据")

    # 改进建议
    st.markdown("---")
    st.subheader("💡 改进建议")
    if report.suggestions:
        for s in report.suggestions:
            st.markdown(f"- {s}")
    else:
        st.info("暂无数据")


def _render_basic_scores(rounds):
    """报告未生成时显示基础评分概览"""
    if not rounds:
        return

    scores = [r["judge"].score for r in rounds if r.get("judge")]
    avg_score = sum(scores) / len(scores) if scores else 0
    max_score = max(scores) if scores else 0
    min_score = min(scores) if scores else 0

    color = "🟢" if avg_score >= 70 else ("🟡" if avg_score >= 50 else "🔴")
    st.subheader(f"{color} 综合评分")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("平均分", f"{avg_score:.0f}/100")
    col2.metric("最高分", f"{max_score}/100")
    col3.metric("最低分", f"{min_score}/100")
    col4.metric("总轮次", len(rounds))

    st.subheader("📊 技能评估明细")
    skill_scores: dict[str, list[int]] = {}
    for r in rounds:
        if r.get("judge"):
            skill_scores.setdefault(r["question"].skill, []).append(r["judge"].score)
    for skill, sc in sorted(skill_scores.items()):
        avg_s = sum(sc) / len(sc)
        st.markdown(f"**{skill}** — 平均 {avg_s:.0f}/100")
        st.progress(avg_s / 100)


def _render_all_rounds_detail(rounds):
    """展示面试全程详细回顾"""
    if not rounds:
        return

    st.markdown("---")
    st.subheader("💬 面试全程回顾")
    for i, r in enumerate(rounds, 1):
        q = r["question"]
        judge = r["judge"]
        score = judge.score if judge else 0
        score_icon = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")

        with st.expander(f"第 {i} 轮 — {q.skill} ({q.difficulty.value}) {score_icon} {score}/100"):
            st.markdown(f"**🤖 问题**: {q.content}")
            if q.get("context"):
                st.caption(f"💡 背景: {q['context']}")
            if q.get("expected_answer_points"):
                st.markdown("**📋 预期得分点**:")
                for p in q.expected_answer_points:
                    st.markdown(f"- {p}")
            st.markdown(f"**🧑‍💻 回答**: {r['answer'] or '（未作答）'}")
            if judge:
                st.markdown(f"**📊 评分**: {judge.score}/100")
                st.markdown(f"**评价**: {judge.comment}")
                if judge.strength_points:
                    st.markdown("**💪 亮点**: " + ", ".join(judge.strength_points))
                if judge.weakness_points:
                    st.markdown("**📈 待改进**: " + ", ".join(judge.weakness_points))
                st.markdown(f"**下一步建议**: {judge.next_action}")


# ── 页面路由 ─────────────────────────────────────────

def main():
    with st.sidebar:
        st.title("🎯 AI 面试官")
        st.markdown("---")
        st.markdown("**从 JD 解析到面试到反馈，完整闭环**")
        st.markdown("---")

        rounds_count = len(st.session_state.rounds)
        if st.session_state.page == "upload":
            st.info("📤 上传文件开始面试")
        elif st.session_state.page == "interview":
            if st.session_state.terminated:
                st.success(f"✅ 面试完成 ({rounds_count} 轮)")
            else:
                st.warning(f"🎙️ 面试进行中 (第 {rounds_count + 1} 轮)")
        elif st.session_state.page == "result":
            st.success(f"✅ 报告就绪 ({rounds_count} 轮)")

        if st.button("🔄 重新开始"):
            for key in _DEFAULT:
                st.session_state[key] = _DEFAULT[key]
            st.rerun()

        st.markdown("---")
        st.caption(f"Phase 2 · 多轮面试 (上限 {config.max_rounds} 轮)")

    # 路由
    if st.session_state.page == "upload":
        render_upload_page()
    elif st.session_state.page == "interview":
        render_interview_page()
    elif st.session_state.page == "result":
        render_result_page()


if __name__ == "__main__":
    main()
