from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

import httpx
from openai import AsyncOpenAI

from config import config

logger = logging.getLogger("llm")


class EmptyResponseError(Exception):
    """LLM 返回空响应——通常是服务端瞬时过载，重试可能恢复。"""


class LLM:
    """DeepSeek LLM 封装（OpenAI-compatible API，延迟初始化 client）"""

    def __init__(self):
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=config.llm_api_key,
                base_url=config.llm_base_url,
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
        return self._client

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str | AsyncIterator[str]:
        """非流式返回完整文本，流式返回 AsyncIterator[str]"""
        messages = [
            {"role": "system", "content": str(system_prompt)},
            {"role": "user", "content": str(user_prompt)},
        ]
        response = await self.client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=temperature or config.llm_temperature,
            max_tokens=max_tokens or config.llm_max_tokens,
            stream=stream,
        )

        if stream:
            return self._stream_handler(response)
        else:
            content = response.choices[0].message.content
            finish = response.choices[0].finish_reason

            if not content or not content.strip():
                logger.warning(
                    "LLM 返回空响应 (finish_reason=%s, model=%s)",
                    finish, config.llm_model,
                )
                raise EmptyResponseError(
                    f"模型返回空内容 (finish_reason={finish})"
                )
            return content

    async def _stream_handler(self, response) -> AsyncIterator[str]:
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    async def generate_with_messages(
        self,
        messages: list[dict],
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str | AsyncIterator[str]:
        """直接传入完整消息列表（用于多轮对话历史）"""
        response = await self.client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=temperature or config.llm_temperature,
            max_tokens=max_tokens or config.llm_max_tokens,
            stream=stream,
        )
        if stream:
            return self._stream_handler(response)
        return response.choices[0].message.content or ""
