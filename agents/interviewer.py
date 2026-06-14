from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from agents.base import BaseAgent
from models.jd import JD
from models.resume import Resume
from models.question import Question, JudgeResult, Difficulty, RoundRecord
from models.llm import LLM
from orchestration.matcher import rank_skills
from prompts import load_prompt

logger = logging.getLogger("agents.interviewer")


class InterviewerAgent(BaseAgent):
    """面试官 Agent（多轮版）

    职责：
    1. 出题：根据 JD + 简历 + 技能维度生成面试题
    2. 追问：根据回答质量决定 deepen / clarify / switch
    3. 评判：对候选人回答评分并给出下一步建议
    """

    def __init__(self, llm: Optional[LLM] = None):
        super().__init__(llm)
        self._interviewer_prompt_tpl = load_prompt("interviewer")
        self._deepen_prompt_tpl = load_prompt("interviewer_deepen")
        self._clarify_prompt_tpl = load_prompt("interviewer_clarify")
        self._judge_prompt_tpl = load_prompt("judge")

    # ── 公共方法 ──────────────────────────────────────

    async def generate_question(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str = "intermediate",
        intent: str = "",
    ) -> Question:
        """根据 JD + 候选人 + 目标技能生成一道面试题"""
        ctx = self._build_context(jd, resume)
        user_prompt = self._interviewer_prompt_tpl.format(
            job_title=jd.title,
            required_skills=ctx["required_skills_str"],
            candidate_skills=ctx["candidate_skills_str"],
            candidate_projects=ctx["candidate_projects_str"],
            target_skill=target_skill,
            difficulty=difficulty,
            intent=intent or f"考察 {target_skill} 的掌握程度",
        )
        # Phase 3: 附带历史题库参考（可选）
        hint = self._get_similar_questions_hint(target_skill)
        if hint:
            user_prompt += "\n" + hint
        question = await super().run(
            user_prompt=user_prompt,
            response_model=Question,
            system_prompt="你是一个专业的 AI 面试官，擅长根据候选人背景出题。",
        )
        question.skill = target_skill
        question.difficulty = Difficulty(difficulty)
        return question

    async def generate_question_stream(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str = "intermediate",
        intent: str = "",
    ) -> AsyncIterator[tuple[str, bool, Optional[Question]]]:
        """流式出题：边生成边返回文字块。

        每步 yield (delta_text, is_complete, question_or_none):
        - 生成中: ("文字块", False, None)
        - 完成:   ("", True, Question)

        成功后 question.skill / question.difficulty 已自动设置。
        失败时最后一步 yield ("", True, None)。
        """
        ctx = self._build_context(jd, resume)
        user_prompt = self._interviewer_prompt_tpl.format(
            job_title=jd.title,
            required_skills=ctx["required_skills_str"],
            candidate_skills=ctx["candidate_skills_str"],
            candidate_projects=ctx["candidate_projects_str"],
            target_skill=target_skill,
            difficulty=difficulty,
            intent=intent or f"考察 {target_skill} 的掌握程度",
        )
        # Phase 3: 附带历史题库参考
        hint = self._get_similar_questions_hint(target_skill)
        if hint:
            user_prompt += "\n" + hint

        try:
            async for delta, done, result in super().run_streaming(
                user_prompt=user_prompt,
                response_model=Question,
                system_prompt="你是一个专业的 AI 面试官，擅长根据候选人背景出题。",
                temperature=0.7,
            ):
                if done and result is not None:
                    result.skill = target_skill
                    result.difficulty = Difficulty(difficulty)
                yield (delta, done, result)
        except Exception as e:
            logger.error("流式出题失败: %s", e)
            yield ("", True, None)

    async def generate_deepen_question(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str,
        previous_question: str,
        previous_answer: str,
    ) -> Question:
        """答得好 → 追问加深：基于上一轮的问答追问技术细节"""
        ctx = self._build_context(jd, resume)
        # 难度升级
        difficulty_levels = ["basic", "intermediate", "advanced", "deep"]
        next_level = min(difficulty_levels.index(difficulty) + 1, len(difficulty_levels) - 1)
        next_difficulty = difficulty_levels[next_level]

        user_prompt = self._deepen_prompt_tpl.format(
            job_title=jd.title,
            required_skills=ctx["required_skills_str"],
            candidate_skills=ctx["candidate_skills_str"],
            candidate_projects=ctx["candidate_projects_str"],
            target_skill=target_skill,
            difficulty=next_difficulty,
            previous_question=previous_question,
            previous_answer=previous_answer,
        )
        question = await super().run(
            user_prompt=user_prompt,
            response_model=Question,
            system_prompt="你是一个专业的 AI 面试官，擅长追问技术细节考察深度。",
        )
        question.skill = target_skill
        question.difficulty = Difficulty(next_difficulty)
        return question

    async def generate_clarify_question(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str,
        previous_question: str,
        previous_answer: str,
    ) -> Question:
        """答得模糊 → 要求澄清：引导候选人给出更具体的回答"""
        ctx = self._build_context(jd, resume)
        user_prompt = self._clarify_prompt_tpl.format(
            job_title=jd.title,
            required_skills=ctx["required_skills_str"],
            candidate_skills=ctx["candidate_skills_str"],
            candidate_projects=ctx["candidate_projects_str"],
            target_skill=target_skill,
            difficulty=difficulty,
            previous_question=previous_question,
            previous_answer=previous_answer,
        )
        question = await super().run(
            user_prompt=user_prompt,
            response_model=Question,
            system_prompt="你是一个专业的 AI 面试官，擅长引导候选人澄清回答。",
        )
        question.skill = target_skill
        question.difficulty = Difficulty(difficulty)
        return question

    async def generate_switch_question(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str = "intermediate",
    ) -> Question:
        """答不上 → 换维度：切换到一个新的技能出基础题"""
        ctx = self._build_context(jd, resume)
        user_prompt = self._interviewer_prompt_tpl.format(
            job_title=jd.title,
            required_skills=ctx["required_skills_str"],
            candidate_skills=ctx["candidate_skills_str"],
            candidate_projects=ctx["candidate_projects_str"],
            target_skill=target_skill,
            difficulty=difficulty,
            intent=f"考察 {target_skill} 的基础掌握程度（切换新维度）",
        )
        question = await super().run(
            user_prompt=user_prompt,
            response_model=Question,
            system_prompt="你是一个专业的 AI 面试官，请出基础难度的题目评估候选人的真实水平。",
        )
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

    # ── 辅助方法 ──────────────────────────────────────

    def _build_context(self, jd: JD, resume: Resume) -> dict:
        """构建公共上下文字符串"""
        candidate_skills_str = ", ".join(
            f"{s.name}({s.level})" for s in resume.skills
        )
        project_descs = []
        for p in resume.projects[:2]:
            techs = ", ".join(p.tech_stack)
            highlight = p.highlights[0] if p.highlights else p.description
            project_descs.append(f"- {p.name}({techs}): {highlight}")
        candidate_projects_str = "\n".join(project_descs)
        required_skills_str = ", ".join(
            f"{s.name}(权重{s.weight})" for s in jd.required_skills
        )
        return {
            "candidate_skills_str": candidate_skills_str or "无",
            "candidate_projects_str": candidate_projects_str or "无项目经历",
            "required_skills_str": required_skills_str or "无",
        }

    def _get_similar_questions_hint(self, skill: str, n: int = 3) -> str:
        """从历史题库检索相似题目，作为出题参考提示。静默失败。"""
        try:
            from memory.vector_store import VectorStore
            from config import config

            if not config.use_vector_memory:
                return ""
            vs = VectorStore()
            if not vs.available:
                return ""
            results = vs.search_similar_questions(skill, n=n)
            if not results:
                return ""
            hints = []
            for r in results:
                doc = r.get("document", "")
                if doc:
                    hints.append(f"  - {doc[:200]}")
            if hints:
                return "\n历史类似题目参考（避免重复，可借鉴风格）：\n" + "\n".join(hints)
        except Exception:
            pass
        return ""

    @staticmethod
    def rank_skills(jd: JD, resume: Resume) -> list[dict]:
        """委托给 orchestration.matcher.rank_skills"""
        return rank_skills(jd, resume)
