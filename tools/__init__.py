from __future__ import annotations

import logging
from pathlib import Path

from tools.pdf_parser import parse_pdf_simple
from tools.docx_parser import parse_docx_simple
from tools.text_cleaner import clean_text

logger = logging.getLogger("tools")

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}


class ParseError(Exception):
    """文件解析错误（包含用户友好的中文提示）"""

    def __init__(self, message: str, filename: str = "", detail: str = ""):
        self.filename = filename
        self.detail = detail
        super().__init__(message)


def parse_file(path: str | Path) -> str:
    """统一解析入口：自动识别扩展名，返回清洗后的纯文本

    支持格式:
        - .pdf  → pdfplumber 解析
        - .docx → python-docx 解析
        - .txt  → 直接读取

    Raises:
        ParseError: 文件格式不支持、文件不存在、解析失败
    """
    path = Path(path)

    # ── 格式检查 ──
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(
            f"不支持的格式「{ext}」 — 请上传 PDF、DOCX 或 TXT 文件",
            filename=path.name,
            detail=f"上传文件扩展名为 {ext}，仅支持 {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    # ── 存在性检查 ──
    if not path.exists():
        raise ParseError(
            f"文件「{path.name}」不存在，请重新上传",
            filename=path.name,
            detail=f"路径: {path}",
        )

    # ── 空文件检查 ──
    if path.stat().st_size == 0:
        raise ParseError(
            f"文件「{path.name}」为空，请上传有效文件",
            filename=path.name,
        )

    # ── 分派解析 ──
    try:
        if ext == ".pdf":
            raw_text = parse_pdf_simple(path)
        elif ext == ".docx":
            raw_text = parse_docx_simple(path)
        elif ext == ".txt":
            raw_text = path.read_text(encoding="utf-8", errors="replace")
        else:
            raise ParseError(
                f"无法处理的扩展名: {ext}",
                filename=path.name,
            )
    except ParseError:
        raise
    except Exception as e:
        logger.exception("解析文件失败: %s", path.name)
        raise ParseError(
            f"解析「{path.name}」失败 — 文件可能已损坏或格式异常",
            filename=path.name,
            detail=str(e),
        )

    # ── 空内容检查 ──
    if not raw_text or not raw_text.strip():
        raise ParseError(
            f"文件「{path.name}」解析后无有效文本内容，请检查文件是否包含可读文字",
            filename=path.name,
        )

    # ── 清洗 ──
    cleaned = clean_text(raw_text)
    if not cleaned:
        raise ParseError(
            f"文件「{path.name}」清洗后无有效内容",
            filename=path.name,
        )

    logger.info("解析成功: %s (%s, %d 字符)", path.name, ext, len(cleaned))
    return cleaned


def parse_file_with_info(path: str | Path) -> dict:
    """解析文件并返回结构化信息"""
    text = parse_file(path)
    path_obj = Path(path)
    return {
        "filename": path_obj.name,
        "extension": path_obj.suffix.lower(),
        "size_bytes": path_obj.stat().st_size,
        "content": text,
        "char_count": len(text),
    }
