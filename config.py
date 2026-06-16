from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent


@dataclass
class Config:
    # ── LLM ──
    llm_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-v4-pro"))
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    llm_streaming: bool = True

    # ── Paths ──
    data_dir: Path = ROOT_DIR / "data"
    logs_dir: Path = ROOT_DIR / "logs"
    uploads_dir: Path = ROOT_DIR / "uploads"
    session_dir: Path = data_dir / "sessions"

    # ── ChromaDB ──
    chroma_persist_dir: Path = data_dir / "chroma"
    chroma_collection_prefix: str = "ih_"

    # ── Embedding ──
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))

    # ── Interview ──
    max_rounds: int = 10
    max_consecutive_empty: int = 3

    # ── Logging ──
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # ── Gateway ──
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    gradio_ui_port: int = 7860
    gateway_api_key: str = field(default_factory=lambda: os.getenv("GATEWAY_API_KEY", "dev-key-change-me"))
    gateway_require_auth: bool = not bool(os.getenv("GATEWAY_NO_AUTH", ""))  # 默认开启鉴权
    gateway_rate_limit: int = int(os.getenv("GATEWAY_RATE_LIMIT", "60"))  # 每分钟最大请求数

    # ── Feature flags ──
    use_gateway: bool = not bool(os.getenv("NO_GATEWAY", ""))  # Web UI 是否通过 Gateway 调用
    use_vector_memory: bool = not bool(os.getenv("NO_VECTOR_MEMORY", ""))  # 是否启用 ChromaDB 记忆

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)


config = Config()  # 单例，全局导入使用
