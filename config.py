from __future__ import annotations

import logging
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


def _env_int(name: str, default: int) -> int:
    """读取整数型环境变量；非法值回退默认值而不是让 import 崩溃。"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logging.getLogger("config").warning(
            "环境变量 %s=%r 不是合法整数，回退默认值 %s", name, raw, default
        )
        return default


def _is_dev() -> bool:
    return _env() == "dev"


@dataclass
class Config:
    # ── 环境 ──
    env: str = field(default_factory=_env)

    # ── LLM ──
    llm_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-flash"))
    llm_temperature: float = 0.7
    # deepseek-flash 默认推理强度 high，推理 token 也计入 max_tokens，
    # 预算过小会导致空响应 (finish_reason=length)，故给足余量。
    llm_max_tokens: int = 32768
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

    # ── Embedding (SiliconFlow API，OpenAI 兼容) ──
    # BAAI/bge-m3：官方免费（0 元/K tokens），输出 1024 维，单条上限 8192 tokens
    # 模型 id 必须是 "BAAI/bge-m3"；写成 "bge-m3" 会返回 400 Model does not exist
    # 免费模型仍需完成实名认证；账户欠费会返回 402 (code 30001)
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"))
    siliconflow_api_key: str = field(default_factory=lambda: (
        os.getenv("SILICONFLOW_API_KEY") or os.getenv("EMBEDDING_API_KEY", "")
    ))
    siliconflow_base_url: str = field(default_factory=lambda: os.getenv(
        "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
    ))
    embedding_batch_size: int = field(default_factory=lambda: _env_int("EMBEDDING_BATCH_SIZE", 32))
    # api（默认，走 SiliconFlow）| local（强制本地模型，不发网络请求）
    embedding_provider: str = field(default_factory=lambda: (
        os.getenv("EMBEDDING_PROVIDER", "").strip().lower() or "api"
    ))
    # API 不可用时的兜底：本地 SentenceTransformer 模型路径 / HF 名
    local_embedding_model: str = field(default_factory=lambda: os.getenv(
        "LOCAL_EMBEDDING_MODEL", "D:/model/bge-base-zh-v1.5"
    ))
    embedding_use_local_fallback: bool = field(default_factory=lambda: (
        not bool(os.getenv("NO_LOCAL_EMBEDDING_FALLBACK", ""))
    ))

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
    gateway_rate_limit: int = field(default_factory=lambda: _env_int("GATEWAY_RATE_LIMIT", 60))

    # ── Feature flags ──
    # 说明：Gateway 是 UI 与外部客户端的统一入口，此开关保留给仅需本地编排的场景
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
