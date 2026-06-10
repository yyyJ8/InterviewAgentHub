from __future__ import annotations

from pathlib import Path

from tools.pdf_parser import parse_pdf_simple
from tools.docx_parser import parse_docx_simple
from tools.text_cleaner import clean_text

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def parse_file(path: str | Path) -> str:
    """统一解析入口：自动识别扩展名，返回清洗后的纯文本

    支持格式:
        - .pdf  → pdfplumber 解析
        - .docx → python-docx 解析
        - .txt  → 直接读取
    """
    path = Path(path)

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {ext}，仅支持 {SUPPORTED_EXTENSIONS}")

    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    # 分派解析
    if ext == ".pdf":
        raw_text = parse_pdf_simple(path)
    elif ext == ".docx":
        raw_text = parse_docx_simple(path)
    elif ext == ".txt":
        raw_text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"无法处理的扩展名: {ext}")

    # 清洗
    cleaned = clean_text(raw_text)
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
