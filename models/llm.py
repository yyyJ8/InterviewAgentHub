from __future__ import annotations

from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from config import config


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
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
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
            return response.choices[0].message.content or ""

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
