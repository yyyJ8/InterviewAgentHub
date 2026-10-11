from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from agents.base import BaseAgent
from models.jd import JD
from models.resume import Resume
from models.question import Question, JudgeResult, Difficulty
from models.llm import LLM
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
        candidate_name: str = "",
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
        # Phase 5: 附加长期记忆参考（历史题库 + 候选人历史）
        hints = []
        q_hint = self._get_similar_questions_hint(target_skill)
        if q_hint:
            hints.append(q_hint)
        if candidate_name:
            c_hint = self._get_candidate_history_hint(candidate_name)
            if c_hint:
                hints.append(c_hint)
        if hints:
            user_prompt += "\n\n" + "\n\n".join(hints)

        question = await super().run(
            user_prompt=user_prompt,
            response_model=Question,
            system_prompt="你是一个专业的 AI 面试官，擅长根据候选人背景出题。",
        )
        question.skill = target_skill
        question.difficulty = Difficulty(difficulty)
        self._store_question(question)
        return question

    async def generate_question_stream(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str = "intermediate",
        intent: str = "",
        candidate_name: str = "",
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
        # Phase 5: 附加长期记忆参考（历史题库 + 候选人历史）
        hints = []
        q_hint = self._get_similar_questions_hint(target_skill)
        if q_hint:
            hints.append(q_hint)
        if candidate_name:
            c_hint = self._get_candidate_history_hint(candidate_name)
            if c_hint:
                hints.append(c_hint)
        if hints:
            user_prompt += "\n\n" + "\n\n".join(hints)

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
                    self._store_question(result)
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
        self._store_question(question)
        return question

    async def generate_deepen_question_stream(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str,
        previous_question: str,
        previous_answer: str,
    ) -> AsyncIterator[tuple[str, bool, Optional[Question]]]:
        """流式追问加深：边生成边返回文字块。"""
        ctx = self._build_context(jd, resume)
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
        try:
            async for delta, done, result in super().run_streaming(
                user_prompt=user_prompt,
                response_model=Question,
                system_prompt="你是一个专业的 AI 面试官，擅长追问技术细节考察深度。",
            ):
                if done and result is not None:
                    result.skill = target_skill
                    result.difficulty = Difficulty(next_difficulty)
                    self._store_question(result)
                yield (delta, done, result)
        except Exception as e:
            logger.error("流式追问失败: %s", e)
            yield ("", True, None)

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
        self._store_question(question)
        return question

    async def generate_clarify_question_stream(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str,
        previous_question: str,
        previous_answer: str,
    ) -> AsyncIterator[tuple[str, bool, Optional[Question]]]:
        """流式澄清：边生成边返回文字块。"""
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
        try:
            async for delta, done, result in super().run_streaming(
                user_prompt=user_prompt,
                response_model=Question,
                system_prompt="你是一个专业的 AI 面试官，擅长引导候选人澄清回答。",
            ):
                if done and result is not None:
                    result.skill = target_skill
                    result.difficulty = Difficulty(difficulty)
                    self._store_question(result)
                yield (delta, done, result)
        except Exception as e:
            logger.error("流式澄清失败: %s", e)
            yield ("", True, None)

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
        self._store_question(question)
        return question

    async def generate_switch_question_stream(
        self,
        jd: JD,
        resume: Resume,
        target_skill: str,
        difficulty: str = "intermediate",
    ) -> AsyncIterator[tuple[str, bool, Optional[Question]]]:
        """流式换维度出题：边生成边返回文字块。"""
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
        try:
            async for delta, done, result in super().run_streaming(
                user_prompt=user_prompt,
                response_model=Question,
                system_prompt="你是一个专业的 AI 面试官，请出基础难度的题目评估候选人的真实水平。",
            ):
                if done and result is not None:
                    result.skill = target_skill
                    result.difficulty = Difficulty(difficulty)
                    self._store_question(result)
                yield (delta, done, result)
        except Exception as e:
            logger.error("流式换题失败: %s", e)
            yield ("", True, None)

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
        """汇总出题参考提示，来自**两个独立来源**：

        1. 向量库 `ih_question_bank` —— 过往面试中已出过的题（语义检索）
        2. 种子题库 `seed_questions.json` —— 人工维护 + 面试沉淀的题（按技能匹配）

        两者职责不同、此前只查了前者，导致沉淀进题库的题对出题环节毫无帮助
        （"只写不读"）。此处合并，并附上题库题的作答要点作为质量参考。

        任一来源失败都不影响出题（降级为无参考），但会记录日志而非静默吞掉。
        """
        sections: list[str] = []

        # ── 来源 1：向量库中已出过的题 ──
        try:
            from config import config
            from memory.vector_store import VectorStore

            if config.use_vector_memory:
                vs = VectorStore()
                if vs.available:
                    asked = [
                        r.get("document", "")
                        for r in vs.search_similar_questions(skill, n=n)
                        if r.get("document")
                    ]
                    if asked:
                        lines = "\n".join(f"  - {d[:200]}" for d in asked)
                        sections.append(
                            "历史已出过的题（同一道不要重复问，可换角度）：\n" + lines
                        )
        except Exception as e:  # noqa: BLE001 - 参考信息失败不应影响出题
            logger.warning("检索历史已出题目失败（跳过该来源）: %s", e)

        # ── 来源 2：种子题库中同技能的题 ──
        try:
            from mcp_servers.question_bank_server import find_seed_by_skill

            seed_items = find_seed_by_skill(skill, limit=n)
            if seed_items:
                lines = []
                for q in seed_items:
                    line = f"  - {q.get('content', '')[:200]}"
                    points = q.get("expected_answer_points") or []
                    if points:
                        line += "\n      参考考察点：" + "；".join(str(p) for p in points[:3])
                    lines.append(line)
                sections.append(
                    "题库中已有的同技能题（优先复用其考察角度，也可据此加深）：\n"
                    + "\n".join(lines)
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("检索种子题库失败（跳过该来源）: %s", e)

        if not sections:
            return ""
        return "\n\n" + "\n\n".join(sections)

    def _get_candidate_history_hint(self, candidate_name: str) -> str:
        """查询候选人历史面试记录，返回 Prompt 摘要。静默失败。"""
        try:
            from orchestration.supervisor import get_candidate_history_summary
            return get_candidate_history_summary(candidate_name)
        except Exception:
            return ""

    def _store_question(self, question: Question) -> None:
        """将已出题目写入向量库，供后续出题时语义检索参考。

        写入失败静默忽略，不影响核心面试流程。
        """
        try:
            from memory.vector_store import COLLECTION_QUESTION_BANK, VectorStore
            from config import config
            import uuid

            if not config.use_vector_memory:
                return
            vs = VectorStore()
            if not vs.available:
                return
            # 元数据同时保留作答要点：沉淀回种子题库时这些是质量判断的依据，
            # 不存的话沉淀出来的题会缺少 expected_answer_points。
            # Chroma 元数据只接受标量，list 需序列化为 JSON 字符串。
            import json as _json

            vs.add(
                COLLECTION_QUESTION_BANK,
                documents=[question.content],
                metadatas=[{
                    "skill": question.skill,
                    "difficulty": question.difficulty.value,
                    "expected_answer_points": _json.dumps(
                        question.expected_answer_points, ensure_ascii=False
                    ),
                    "context": question.context or "",
                }],
                ids=[uuid.uuid4().hex[:8]],
            )
        except Exception:
            pass
