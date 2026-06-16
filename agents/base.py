from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator, Optional, Type, TypeVar

from pydantic import BaseModel

from models.llm import LLM

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger("agents.base")


class BaseAgent:
    """Agent 基类

    封装 LLM 调用 + JSON 响应解析 + 指数退避重试 + 流式输出。
    继承后只需实现具体业务逻辑。
    """

    def __init__(self, llm: Optional[LLM] = None):
        self.llm = llm or LLM()

    # ── 非流式调用（带指数退避重试）──────────────────────

    async def run(
        self,
        user_prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        max_retries: int = 2,
        backoff_base: float = 1.0,
    ) -> T:
        """调用 LLM 并解析为结构化模型（带指数退避重试）

        Args:
            user_prompt: 用户输入文本（如 JD 原文 / 简历原文）
            response_model: 期望的 Pydantic 模型类
            system_prompt: 可覆盖默认 system prompt
            max_retries: 最大重试次数（不含首次调用）
            backoff_base: 退避基数秒，实际延迟 = base * 2^attempt

        Returns:
            解析后的 Pydantic 模型实例

        Raises:
            RuntimeError: 所有重试均失败
        """
        system = system_prompt or self._default_system_prompt()

        last_error = None
        response_text = ""

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
                if attempt < max_retries:
                    delay = backoff_base * (2 ** attempt)
                    logger.warning(
                        "LLM 解析失败 (第 %d/%d 次): %s，%.1f 秒后重试...",
                        attempt + 1, max_retries + 1, e, delay,
                    )
                    await asyncio.sleep(delay)

            except Exception as e:
                # 网络超时 / API 错误 / 连接中断 等
                last_error = e
                if attempt < max_retries:
                    delay = backoff_base * (2 ** attempt)
                    logger.warning(
                        "LLM 调用失败 (第 %d/%d 次): %s，%.1f 秒后重试...",
                        attempt + 1, max_retries + 1, e, delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"LLM 调用失败，重试 {max_retries} 次后放弃: {last_error}\n"
            f"最后响应（截断）: {response_text[:300] if response_text else '(无)'}"
        )

    # ── 流式调用 ───────────────────────────────────────

    async def run_streaming(
        self,
        user_prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_retries: int = 1,
    ) -> AsyncIterator[tuple[str, bool, Optional[T]]]:
        """流式调用 LLM，边生成边返回。

        每步 yield (delta_text, is_complete, parsed_model):
        - 生成中: (chunk_text, False, None)
        - 完成:    ("", True, parsed_model)

        Args:
            user_prompt: 用户输入
            response_model: 期望的 Pydantic 模型类
            system_prompt: 系统提示词
            temperature: 温度（流式出题用高温度增加多样性）
            max_retries: 解析失败时的重试次数
        """
        system = system_prompt or self._default_system_prompt()

        full_text = ""
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                text_stream = await self.llm.generate(
                    system_prompt=system,
                    user_prompt=user_prompt,
                    stream=True,
                    temperature=temperature,
                )
                full_text = ""
                async for chunk in text_stream:
                    full_text += chunk
                    yield (chunk, False, None)

                # 流结束，尝试解析
                result = self._parse_response(full_text, response_model)
                yield ("", True, result)
                return

            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = e
                if attempt < max_retries:
                    delay = 1.0 * (2 ** attempt)
                    logger.warning("流式解析失败 (第 %d 次): %s，重试...", attempt + 1, e)
                    await asyncio.sleep(delay)
                    full_text = ""

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    delay = 1.0 * (2 ** attempt)
                    logger.warning("流式调用失败 (第 %d 次): %s，重试...", attempt + 1, e)
                    await asyncio.sleep(delay)
                    full_text = ""

        raise RuntimeError(
            f"流式 LLM 调用失败，重试 {max_retries} 次后放弃: {last_error}\n"
            f"最后累积文本（截断）: {full_text[:300] if full_text else '(无)'}"
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

        raise ValueError(
            f"无法从响应中提取有效 JSON (共 {len(text)} 字符):\n"
            f"---前 300 字符---\n{text[:300]}\n"
            f"---后 200 字符---\n{text[-200:]}"
        )
