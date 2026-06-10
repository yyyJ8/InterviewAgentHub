from __future__ import annotations

import json
import re
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from models.llm import LLM

T = TypeVar("T", bound=BaseModel)


class BaseAgent:
    """Agent 基类

    封装 LLM 调用 + JSON 响应解析 + 重试机制。
    继承后只需实现 _build_system_prompt() 和 _build_user_prompt()。
    """

    def __init__(self, llm: Optional[LLM] = None):
        self.llm = llm or LLM()

    async def run(
        self,
        user_prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        max_retries: int = 2,
    ) -> T:
        """调用 LLM 并解析为结构化模型

        Args:
            user_prompt: 用户输入文本（如 JD 原文 / 简历原文）
            response_model: 期望的 Pydantic 模型类
            system_prompt: 可覆盖默认 system prompt
            max_retries: 解析失败时重试次数

        Returns:
            解析后的 Pydantic 模型实例
        """
        system = system_prompt or self._default_system_prompt()

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                response_text = await self.llm.generate(
                    system_prompt=system,
                    user_prompt=user_prompt,
                    temperature=0.3,  # 解析场景用低温度保证稳定性
                )
                return self._parse_response(response_text, response_model)
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                continue

        raise RuntimeError(
            f"LLM 响应解析失败，重试 {max_retries} 次后放弃: {last_error}\n"
            f"最后响应: {response_text}"
        )

    def _default_system_prompt(self) -> str:
        """子类可覆盖"""
        return "你是一个专业的 AI 面试官助手，请严格按照 JSON 格式输出。"

    def _parse_response(self, text: str, model_class: Type[T]) -> T:
        """从 LLM 响应中提取 JSON 并解析为 Pydantic 模型"""
        # 尝试直接解析
        try:
            return model_class.model_validate_json(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # 尝试从 ```json ... ``` 代码块中提取
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if json_match:
            try:
                return model_class.model_validate_json(json_match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        # 尝试从 { ... } 中提取最外层 JSON
        brace_match = re.search(r"(\{[\s\S]*\})", text)
        if brace_match:
            try:
                return model_class.model_validate_json(brace_match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        raise ValueError(f"无法从响应中提取有效 JSON:\n{text[:500]}")
