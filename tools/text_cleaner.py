from __future__ import annotations

import re


def clean_text(text: str) -> str:
    """清洗文本：去除多余空白、特殊字符、空行"""
    if not text:
        return ""

    # 1. 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. 去除零宽字符
    text = re.sub(r"[​‌‍﻿]", "", text)

    # 3. 连续空白符（空格/制表符）压缩为单个空格
    text = re.sub(r"[ \t]+", " ", text)

    # 4. 连续空行（3 个以上换行）压缩为 2 个
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 5. 去除行首行尾空白
    text = "\n".join(line.strip() for line in text.split("\n"))

    # 6. 去除首尾空白
    text = text.strip()

    return text


def truncate(text: str, max_chars: int = 8000) -> str:
    """截断文本到指定长度，在最后一个完整句子处截断"""
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    # 找最后一个句号/换行处截断
    last_end = max(
        truncated.rfind("。"),
        truncated.rfind("\n"),
        truncated.rfind(". "),
    )
    if last_end > max_chars // 2:
        truncated = truncated[: last_end + 1]

    return truncated + "\n\n[文本已截断，超出 token 限制]"
