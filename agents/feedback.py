from __future__ import annotations

from typing import Optional

from agents.base import BaseAgent
from models.jd import JD
from models.resume import Resume
from models.question import InterviewReport
from models.llm import LLM
from prompts import load_prompt


class FeedbackAgent(BaseAgent):
    """反馈 Agent：根据完整面试记录生成评分报告"""

    def __init__(self, llm: Optional[LLM] = None):
        super().__init__(llm)
        self._feedback_prompt_tpl = load_prompt("feedback")

    async def generate_report(
        self,
        jd: JD,
        resume: Resume,
        rounds: list[dict],
    ) -> InterviewReport:
        """根据面试记录生成完整评分报告

        Args:
            jd: 结构化 JD
            resume: 结构化简历
            rounds: 面试轮次列表，每轮包含 question, answer, judge

        Returns:
            InterviewReport 包含总分、维度分、亮点不足等
        """
        transcript = self._build_transcript(rounds)

        candidate_skills = ", ".join(
            f"{s.name}({s.level})" for s in resume.skills
        )
        project_descs = []
        for p in resume.projects[:3]:
            techs = ", ".join(p.tech_stack)
            project_descs.append(f"- {p.name}({techs}): {p.description}")
        candidate_projects_str = "\n".join(project_descs) or "无"
        required_skills_str = ", ".join(
            f"{s.name}(权重{s.weight})" for s in jd.required_skills
        ) or "无"

        user_prompt = self._feedback_prompt_tpl.format(
            job_title=jd.title,
            company=jd.company or "未知",
            required_skills=required_skills_str,
            candidate_name=resume.name,
            candidate_title=resume.title or "未知",
            experience_years=resume.experience_years or "未知",
            candidate_skills=candidate_skills or "无",
            candidate_projects=candidate_projects_str,
            round_count=len(rounds),
            interview_transcript=transcript,
        )

        report = await super().run(
            user_prompt=user_prompt,
            response_model=InterviewReport,
            system_prompt=(
                "你是一个资深的面试反馈分析师，擅长根据面试记录生成客观、"
                "具体的评分报告。请严格按照 5 维度评分体系进行评估。"
            ),
        )
        return report

    def _build_transcript(self, rounds: list[dict]) -> str:
        """构建面试记录文本"""
        lines = []
        for i, r in enumerate(rounds, 1):
            q = r.get("question", {})
            judge = r.get("judge", {})

            # Support both object and dict access
            q_content = q.content if hasattr(q, "content") else q.get("content", "")
            q_skill = q.skill if hasattr(q, "skill") else q.get("skill", "")
            q_diff = q.difficulty.value if hasattr(q.difficulty, "value") else q.get("difficulty", "")

            answer = r.get("answer", "")
            score = judge.score if hasattr(judge, "score") else judge.get("score", 0)
            comment = judge.comment if hasattr(judge, "comment") else judge.get("comment", "")
            strength = judge.strength_points if hasattr(judge, "strength_points") else judge.get("strength_points", [])
            weakness = judge.weakness_points if hasattr(judge, "weakness_points") else judge.get("weakness_points", [])

            lines.append(f"--- 第 {i} 轮 (技能: {q_skill}, 难度: {q_diff}) ---")
            lines.append(f"题目: {q_content}")
            lines.append(f"回答: {answer}")
            lines.append(f"评分: {score}/100")
            lines.append(f"评价: {comment}")
            if strength:
                lines.append(f"亮点: {'; '.join(strength)}")
            if weakness:
                lines.append(f"不足: {'; '.join(weakness)}")
            lines.append("")

        return "\n".join(lines)
