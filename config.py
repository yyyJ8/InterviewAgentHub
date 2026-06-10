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

    # ── ChromaDB ──
    chroma_persist_dir: Path = data_dir / "chroma"
    chroma_collection_prefix: str = "ih_"

    # ── Interview ──
    max_rounds: int = 10
    max_consecutive_empty: int = 3

    # ── Logging ──
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # ── Server ──
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)


config = Config()  # 单例，全局导入使用
