from __future__ import annotations

from pathlib import Path
from typing import Optional

import pdfplumber


def parse_pdf(path: str | Path) -> tuple[str, dict]:
    """解析 PDF 文件，返回 (提取文本, 元数据字典)"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {path}")

    pages_text: list[str] = []
    metadata: dict = {}

    with pdfplumber.open(path) as pdf:
        # 元数据
        meta = pdf.metadata or {}
        metadata = {
            "pages": len(pdf.pages),
            "author": meta.get("Author", ""),
            "title": meta.get("Title", ""),
            "subject": meta.get("Subject", ""),
        }

        # 逐页提取文本
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text:
                pages_text.append(text)

    full_text = "\n".join(pages_text)
    return full_text, metadata


def parse_pdf_simple(path: str | Path) -> str:
    """仅返回 PDF 文本（忽略元数据），方便外部调用"""
    text, _ = parse_pdf(path)
    return text
