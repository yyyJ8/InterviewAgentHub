"""Interviewer Agent 测试（结构 + 排序逻辑 + 多轮模型，不调 LLM）"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.jd import JD, Skill
from models.resume import Resume, SkillProficiency, Project
from models.question import (
    Question, JudgeResult, Difficulty,
    RoundRecord, InterviewReport,
)
from models.interview import RoundState, InterviewStatus


def test_rank_skills_basic():
    """基本排序：JD 有多项技能，简历有部分匹配"""
    jd = JD(
        title="Python后端工程师",
        required_skills=[
            Skill(name="Python", weight=90),
            Skill(name="Django", weight=80),
            Skill(name="MySQL", weight=70),
            Skill(name="Redis", weight=60),
            Skill(name="Kubernetes", weight=50),
        ],
    )
    resume = Resume(
        name="张三",
        skills=[
            SkillProficiency(name="Python", level="expert", years=5),
            SkillProficiency(name="Django", level="proficient", years=3),
            SkillProficiency(name="MySQL", level="proficient", years=4),
        ],
        projects=[
            Project(
                name="电商平台",
                role="后端",
                description="订单系统",
                tech_stack=["Python", "Django", "MySQL"],
                highlights=["性能优化"],
            )
        ],
        experience_years=5,
    )

    from agents.interviewer import InterviewerAgent

    ranked = InterviewerAgent.rank_skills(jd, resume)

    assert len(ranked) == 5

    # 1. 有项目经验的排前面：Python, Django, MySQL
    assert ranked[0]["skill"] == "Python"
    assert ranked[0]["gap"] == "有项目经验"
    assert ranked[1]["skill"] == "Django"
    assert ranked[1]["gap"] == "有项目经验"
    assert ranked[2]["skill"] == "MySQL"
    assert ranked[2]["gap"] == "有项目经验"

    # 2. 缺口技能排后面
    assert ranked[3]["gap"] == "缺口"
    assert ranked[4]["gap"] == "缺口"

    # 3. 权重高的缺口排前面
    assert ranked[3]["skill"] == "Redis"
    assert ranked[4]["skill"] == "Kubernetes"

    print("  [OK] test_rank_skills_basic")


def test_rank_skills_no_match():
    """JD 技能全部缺口"""
    jd = JD(
        title="Go工程师",
        required_skills=[Skill(name="Go", weight=90)],
    )
    resume = Resume(
        name="李四",
        skills=[SkillProficiency(name="Python", level="expert")],
        projects=[],
    )

    from agents.interviewer import InterviewerAgent

    ranked = InterviewerAgent.rank_skills(jd, resume)
    assert len(ranked) == 1
    assert ranked[0]["gap"] == "缺口"
    print("  [OK] test_rank_skills_no_match")


def test_rank_skills_bonus():
    """加分技能排在最后"""
    jd = JD(
        title="后端工程师",
        required_skills=[Skill(name="Python", weight=90)],
        bonus_skills=[Skill(name="Go", weight=50, is_bonus=True)],
    )
    resume = Resume(
        name="王五",
        skills=[
            SkillProficiency(name="Python", level="expert"),
            SkillProficiency(name="Go", level="familiar"),
        ],
        projects=[Project(name="平台", role="后端", description="", tech_stack=["Python"], highlights=[])],
    )

    from agents.interviewer import InterviewerAgent

    ranked = InterviewerAgent.rank_skills(jd, resume)
    assert len(ranked) == 2
    assert ranked[0]["skill"] == "Python"
    assert ranked[0]["gap"] == "有项目经验"
    # 加分技能 Go 虽然有技能，但排最后
    assert ranked[1]["skill"] == "Go"
    assert ranked[1].get("is_bonus") is True
    print("  [OK] test_rank_skills_bonus")


def test_question_model():
    """Question 模型验证"""
    q = Question(
        skill="Python",
        difficulty=Difficulty.INTERMEDIATE,
        content="解释 Python 装饰器的工作原理",
        context="基于简历中的项目经验",
        expected_answer_points=["闭包概念", "语法糖", "常见应用场景"],
    )
    assert q.difficulty == Difficulty.INTERMEDIATE
    d = q.model_dump()
    assert d["skill"] == "Python"
    assert len(d["expected_answer_points"]) == 3
    print("  [OK] test_question_model")


def test_judge_result_model():
    """JudgeResult 模型验证"""
    jr = JudgeResult(
        score=85,
        comment="表现良好",
        strength_points=["回答准确", "有项目实例"],
        weakness_points=["深度不足"],
        next_action="deepen",
    )
    assert jr.score == 85
    assert jr.next_action == "deepen"
    d = jr.model_dump()
    assert d["score"] == 85
    assert len(d["strength_points"]) == 2
    print("  [OK] test_judge_result_model")


def test_round_record_model():
    """RoundRecord 模型验证（多轮面试记录）"""
    question = Question(
        skill="Python", difficulty=Difficulty.INTERMEDIATE,
        content="解释装饰器", expected_answer_points=[],
    )
    judge = JudgeResult(
        score=85, comment="好", next_action="deepen",
    )
    record = RoundRecord(
        round_number=1,
        skill="Python",
        question=question,
        answer="装饰器是...",
        judge=judge,
    )
    assert record.round_number == 1
    assert record.skill == "Python"
    assert record.judge.score == 85
    d = record.model_dump()
    assert d["round_number"] == 1
    assert d["answer"] == "装饰器是..."
    print("  [OK] test_round_record_model")


def test_interview_report_model():
    """InterviewReport 模型验证"""
    report = InterviewReport(
        total_score=78.5,
        dimension_scores={"技术匹配": 80, "项目经验": 75, "沟通表达": 70},
        skill_scores=[{"skill": "Python", "score": 85}],
        strengths=["基础扎实"],
        weaknesses=["深度不足"],
        suggestions=["多阅读源码"],
        overall_assessment="整体表现良好",
        hiring_recommendation="yes",
    )
    assert report.total_score == 78.5
    assert report.hiring_recommendation == "yes"
    assert len(report.dimension_scores) == 3
    print("  [OK] test_interview_report_model")


def test_round_state_model():
    """RoundState Pydantic 模型验证（models/interview.py）"""
    from datetime import datetime
    question = Question(
        skill="Redis", difficulty=Difficulty.ADVANCED,
        content="Redis 持久化机制", expected_answer_points=[],
    )
    rs = RoundState(
        round_number=1,
        skill="Redis",
        question=question,
    )
    assert rs.round_number == 1
    assert rs.skill == "Redis"
    assert isinstance(rs.created_at, datetime)
    print("  [OK] test_round_state_model")


def test_prompt_loader():
    """验证 prompt 模板是否存在且包含关键标记"""
    from prompts import load_prompt

    # 原有 prompt
    interviewer = load_prompt("interviewer")
    assert "{target_skill}" in interviewer
    assert "{difficulty}" in interviewer
    assert "{job_title}" in interviewer
    assert "{candidate_skills}" in interviewer

    judge = load_prompt("judge")
    assert "{question_content}" in judge
    assert "{answer}" in judge
    assert "{expected_points}" in judge

    # 新增 prompt：追问加深
    deepen = load_prompt("interviewer_deepen")
    assert "{previous_question}" in deepen
    assert "{previous_answer}" in deepen
    assert "{target_skill}" in deepen

    # 新增 prompt：澄清追问
    clarify = load_prompt("interviewer_clarify")
    assert "{previous_question}" in clarify
    assert "{previous_answer}" in clarify
    assert "{target_skill}" in clarify

    print("  [OK] test_prompt_loader")


def test_interviewer_agent_has_multi_round_methods():
    """验证 InterviewerAgent 有多轮追问方法"""
    from agents.interviewer import InterviewerAgent

    agent = InterviewerAgent()
    assert hasattr(agent, "generate_question")
    assert hasattr(agent, "generate_deepen_question")
    assert hasattr(agent, "generate_clarify_question")
    assert hasattr(agent, "generate_switch_question")
    assert hasattr(agent, "judge_answer")
    print("  [OK] test_interviewer_agent_has_multi_round_methods")


def test_supervisor_state():
    """验证 supervisor 中的 InterviewState 结构"""
    from orchestration.supervisor import initial_state

    state = initial_state("jd.pdf", "resume.pdf")
    assert state["jd_path"] == "jd.pdf"
    assert state["resume_path"] == "resume.pdf"
    assert state["rounds"] == []
    assert state["current_round_number"] == 0
    assert state["terminated"] is False
    assert state["consecutive_empty"] == 0
    assert state["all_answers"] == []
    print("  [OK] test_supervisor_state")


if __name__ == "__main__":
    print("Interviewer Agent 测试\n" + "=" * 20)
    test_question_model()
    test_judge_result_model()
    test_round_record_model()
    test_interview_report_model()
    test_round_state_model()
    test_prompt_loader()
    test_interviewer_agent_has_multi_round_methods()
    test_supervisor_state()
    test_rank_skills_basic()
    test_rank_skills_no_match()
    test_rank_skills_bonus()
    print("\n[OK] 全部通过")
