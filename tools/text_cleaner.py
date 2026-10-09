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
