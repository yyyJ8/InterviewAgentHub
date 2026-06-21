from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent


def _env() -> str:
    """运行环境：dev | prod。"""
    v = os.getenv("ENV", "dev").strip().lower()
    if v not in ("dev", "prod"):
        v = "dev"
    return v


def _is_dev() -> bool:
    return _env() == "dev"


@dataclass
class Config:
    # ── 环境 ──
    env: str = field(default_factory=_env)

    # ── LLM ──
    llm_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-v4-pro"))
    llm_temperature: float = 0.7
    llm_max_tokens: int = 8192
    llm_streaming: bool = True

    # ── Paths ──
    data_dir: Path = ROOT_DIR / "data"
    logs_dir: Path = ROOT_DIR / "logs"
    uploads_dir: Path = ROOT_DIR / "uploads"
    session_dir: Path = data_dir / "sessions"
    cache_dir: Path = data_dir / "cache"

    # ── ChromaDB ──
    chroma_persist_dir: Path = data_dir / "chroma"
    chroma_collection_prefix: str = "ih_"

    # ── Embedding ──
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "D:/model/bge-base-zh-v1.5"))

    # ── Interview ──
    max_rounds: int = 10
    max_consecutive_empty: int = 3

    # ── Logging ──
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "DEBUG" if _is_dev() else "INFO"))

    # ── Gateway ──
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    gradio_ui_port: int = 7860
    gateway_api_key: str = field(default_factory=lambda: os.getenv("GATEWAY_API_KEY", "dev-key-change-me"))
    gateway_require_auth: bool = field(default_factory=lambda: (
        not _is_dev() and not bool(os.getenv("GATEWAY_NO_AUTH", ""))
    ))
    gateway_rate_limit: int = int(os.getenv("GATEWAY_RATE_LIMIT", "60"))

    # ── Feature flags ──
    use_gateway: bool = not bool(os.getenv("NO_GATEWAY", ""))
    use_vector_memory: bool = not bool(os.getenv("NO_VECTOR_MEMORY", ""))

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_dev(self) -> bool:
        """是否为开发环境。"""
        return self.env == "dev"


config = Config()  # 单例，全局导入使用
