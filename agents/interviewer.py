from __future__ import annotations

from typing import Optional

from agents.base import BaseAgent
from models.jd import JD
from models.resume import Resume, SkillProficiency, Project
from models.question import Question, JudgeResult, Difficulty
from models.llm import LLM
from orchestration.matcher import rank_skills
from prompts import load_prompt


class InterviewerAgent(BaseAgent):
    """面试官 Agent（单轮版）

    职责：
    1. 出题：根据 JD + 简历 + 技能维度生成面试题
    2. 评判：对候选人回答评分并给出下一步建议
    3. 技能排序：将 JD 技能按考察优先级排序
    """

    def __init__(self, llm: Optional[LLM] = None):
        super().__init__(llm)
        self._interviewer_prompt_tpl = load_prompt("interviewer")
        self._judge_prompt_tpl = load_prompt("judge")

    # ── 核心方法 ──────────────────────────────────────

    async def generate_question(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str = "intermediate",
        intent: str = "",
    ) -> Question:
        """根据 JD + 候选人 + 目标技能生成一道面试题"""
        # 构建候选人技能描述
        candidate_skills_str = ", ".join(
            f"{s.name}({s.level})" for s in resume.skills
        )
        # 构建候选人项目描述（取 top-2 项目）
        project_descs = []
        for p in resume.projects[:2]:
            techs = ", ".join(p.tech_stack)
            project_descs.append(f"- {p.name}({techs}): {p.highlights[0] if p.highlights else p.description}")
        candidate_projects_str = "\n".join(project_descs)

        # 构建 JD 技能描述
        required_skills_str = ", ".join(
            f"{s.name}(权重{s.weight})" for s in jd.required_skills
        )

        # 填充 prompt 模板
        user_prompt = self._interviewer_prompt_tpl.format(
            job_title=jd.title,
            required_skills=required_skills_str or "无",
            candidate_skills=candidate_skills_str or "无",
            candidate_projects=candidate_projects_str or "无项目经历",
            target_skill=target_skill,
            difficulty=difficulty,
            intent=intent or f"考察 {target_skill} 的掌握程度",
        )

        question = await super().run(
            user_prompt=user_prompt,
            response_model=Question,
            system_prompt="你是一个专业的 AI 面试官，擅长根据候选人背景出题。",
        )
        # 填充 skill 和 difficulty 确保一致
        question.skill = target_skill
        question.difficulty = Difficulty(difficulty)
        return question

    async def judge_answer(
        self,
        question: Question,
        answer: str,
    ) -> JudgeResult:
        """对候选人回答进行评判"""
        expected_points = "\n".join(
            f"- {p}" for p in question.expected_answer_points
        )
        user_prompt = self._judge_prompt_tpl.format(
            question_content=question.content,
            skill=question.skill,
            difficulty=question.difficulty.value,
            expected_points=expected_points or "未提供",
            answer=answer or "（候选人未作答）",
        )

        result = await super().run(
            user_prompt=user_prompt,
            response_model=JudgeResult,
            system_prompt="你是一个专业的面试评分助手，请客观公正地评分。",
        )
        return result

    # ── 技能排序（委托给 orchestration/matcher.py） ──

    @staticmethod
    def rank_skills(jd: JD, resume: Resume) -> list[dict]:
        """委托给 orchestration.matcher.rank_skills"""
        return rank_skills(jd, resume)
