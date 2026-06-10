from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent
_CACHE: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """加载 prompt 模板（按文件名，不带 .md 后缀）"""
    if name in _CACHE:
        return _CACHE[name]

    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {path}")

    content = path.read_text(encoding="utf-8")
    _CACHE[name] = content
    return content


def list_prompts() -> list[str]:
    """列出所有可用的 prompt 名称"""
    return sorted(p.stem for p in _PROMPTS_DIR.glob("*.md") if p.stem != "__init__")
