from __future__ import annotations

from typing import Optional

from agents.base import BaseAgent
from models.resume import Resume
from models.llm import LLM
from prompts import load_prompt


class ResumeAnalyzerAgent(BaseAgent):
    """简历分析 Agent：从简历文本中提取候选人画像"""

    def __init__(self, llm: Optional[LLM] = None):
        super().__init__(llm)
        self._system_prompt = load_prompt("resume_analyzer")

    async def run(
        self,
        resume_text: str,
    ) -> Resume:
        """解析简历文本，返回结构化 Resume 对象"""
        # 截断过长的输入，给 LLM 输出留足 token 空间
        trimmed = resume_text[:3000] if len(resume_text) > 3000 else resume_text
        resume = await super().run(
            user_prompt=trimmed,
            response_model=Resume,
            system_prompt=self._system_prompt,
        )
        # 保留原始文本用于调试
        resume.raw_text = resume_text[:2000]
        return resume
