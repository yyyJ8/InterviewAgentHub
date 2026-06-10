"""Interviewer Agent 测试（结构 + 排序逻辑，不调 LLM）"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.jd import JD, Skill
from models.resume import Resume, SkillProficiency, Project
from models.question import Question, JudgeResult, Difficulty


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
    assert ranked[3]["gap"] == "缺口"  # Redis 或 Kubernetes
    assert ranked[4]["gap"] == "缺口"

    # 3. 权重高的缺口排前面
    assert ranked[3]["skill"] == "Redis"  # weight 60 > 50
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


def test_prompt_loader():
    """验证 prompt 模板是否存在且包含关键标记"""
    from prompts import load_prompt

    interviewer = load_prompt("interviewer")
    assert "{target_skill}" in interviewer
    assert "{difficulty}" in interviewer
    assert "{job_title}" in interviewer
    assert "{candidate_skills}" in interviewer

    judge = load_prompt("judge")
    assert "{question_content}" in judge
    assert "{answer}" in judge
    assert "{expected_points}" in judge

    print("  [OK] test_prompt_loader")


if __name__ == "__main__":
    print("Interviewer Agent 测试\n" + "=" * 20)
    test_question_model()
    test_judge_result_model()
    test_prompt_loader()
    test_rank_skills_basic()
    test_rank_skills_no_match()
    test_rank_skills_bonus()
    print("\n[OK] 全部通过")
