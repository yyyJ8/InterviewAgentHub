from __future__ import annotations

from typing import Optional

from agents.base import BaseAgent
from models.jd import JD
from models.llm import LLM
from prompts import load_prompt


class JDParserAgent(BaseAgent):
    """JD 解析 Agent：从 JD 文本中提取结构化信息"""

    def __init__(self, llm: Optional[LLM] = None):
        super().__init__(llm)
        self._system_prompt = load_prompt("jd_parser")

    async def run(
        self,
        jd_text: str,
    ) -> JD:
        """解析 JD 文本，返回结构化 JD 对象"""
        # 截断过长的输入，给 LLM 输出留足 token 空间
        trimmed = jd_text[:3000] if len(jd_text) > 3000 else jd_text
        jd = await super().run(
            user_prompt=trimmed,
            response_model=JD,
            system_prompt=self._system_prompt,
        )
        # 保留原始文本用于调试
        jd.raw_text = jd_text[:2000]
        return jd
