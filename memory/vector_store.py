"""ChromaDB 向量存储封装

提供 2 个 Collection 的 CRUD 操作：
  - ih_question_bank     : 已出题目（语义检索避免重复）
  - ih_interview_sessions: 面试记录全文（历史参考）

Embedding 策略：SiliconFlow API 的 BAAI/bge-m3（1024 维，OpenAI 兼容接口）；
API 不可用时回退本地 SentenceTransformer，本地也不可用则降级为无记忆模式。

注意：bge-m3 与旧版本地 bge-base-zh-v1.5（768 维）维度不同，
换模型后旧向量不可用，_add / query 会自动检测维度冲突并重建 Collection。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import requests

from config import config

logger = logging.getLogger(__name__)


# ── 2 个 Collection 名称 ──────────────────────────────────
# 名称由 config.chroma_collection_prefix 派生（默认 "ih_"），改前缀即可换命名空间。

QUESTION_BANK_SUFFIX = "question_bank"
INTERVIEW_SESSIONS_SUFFIX = "interview_sessions"

COLLECTION_QUESTION_BANK = f"{config.chroma_collection_prefix}{QUESTION_BANK_SUFFIX}"
COLLECTION_INTERVIEW_SESSIONS = f"{config.chroma_collection_prefix}{INTERVIEW_SESSIONS_SUFFIX}"


# ── Embedding 提供者 ───────────────────────────────────────

class _ApiEmbedder:
    """SiliconFlow Embeddings API（OpenAI 兼容）。"""

    label = "siliconflow-api"

    def __init__(self, model: str, api_key: str, base_url: str, batch_size: int):
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._batch_size = max(1, batch_size)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，按 index 排序保证与入参顺序一致。"""
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start:start + self._batch_size]
            try:
                resp = requests.post(
                    f"{self._base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"input": batch, "model": self.model},
                    timeout=60,
                )
                resp.raise_for_status()
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status == 402:
                    raise RuntimeError(
                        "SiliconFlow 账户余额不足 (402)，无法调用 embedding 模型。"
                        "请充值，或在 .env 设置 EMBEDDING_PROVIDER=local 改用本地模型。"
                    ) from e
                if status in (401, 403):
                    raise RuntimeError(
                        f"SiliconFlow 鉴权失败 ({status})，请检查 SILICONFLOW_API_KEY。"
                    ) from e
                raise
            data = resp.json().get("data") or []
            data.sort(key=lambda d: d.get("index", 0))
            out.extend(d["embedding"] for d in data)
        if len(out) != len(texts):
            raise RuntimeError(
                f"Embedding 返回数量不匹配: 期望 {len(texts)}，实际 {len(out)}"
            )
        return out


class _LocalEmbedder:
    """本地 SentenceTransformer（兜底）。"""

    label = "local-sentence-transformers"

    def __init__(self, model_name: str):
        import os as _os

        # 必须在 import sentence_transformers 之前设置 endpoint
        if not _os.environ.get("HF_ENDPOINT"):
            _os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        last_error: Exception | None = None
        for endpoint in [_os.environ.get("HF_ENDPOINT", ""), "https://huggingface.co"]:
            if not endpoint:
                continue
            try:
                _os.environ["HF_ENDPOINT"] = endpoint
                self._model = SentenceTransformer(model_name)
                break
            except Exception as e:  # noqa: BLE001 - 逐个 endpoint 尝试
                last_error = e
        else:
            raise RuntimeError(f"本地 Embedding 模型加载失败: {last_error}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts).tolist()


class _LazyEmbedder:
    """惰性初始化包装：真正需要时才构造被包装的 Embedder。

    用途：本地兜底模型（SentenceTransformer）构造需数十秒，若在初始化时就建好，
    即使 API 全程正常也要白付这份启动开销。包装后只在 API 首次失败时才加载。
    """

    def __init__(self, factory, label: str):
        self._factory = factory
        self._inner = None
        self.label = label

    def _get(self):
        if self._inner is None:
            self._inner = self._factory()
            # 加载完成后 label 反映真实生效的提供者
            self.label = getattr(self._inner, "label", self.label)
        return self._inner

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._get().embed(texts)


class _FallbackEmbedder:
    """API 优先，遇到瞬时故障时切换到本地模型并保持。"""

    def __init__(self, primary, secondary):
        self._primary = primary
        self._secondary = secondary
        self._active = primary
        self.label = f"{primary.label}(fallback:{getattr(secondary, 'label', 'local')})"

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._active.embed(texts)
        except Exception as e:  # noqa: BLE001 - 需要按异常类型决定是否兜底
            if self._active is self._secondary or not _is_transient(e):
                # 永久性错误（401/403/402 余额不足、model 不存在等）不兜底：
                # 本地模型维度不同，静默切换会让向量库维度反复横跳。
                raise
            logger.warning(
                "Embedding API 暂时不可用，回退本地模型 [%s]: %s",
                getattr(self._secondary, "label", "local"), e,
            )
            self._active = self._secondary
            self.label = getattr(self._secondary, "label", "local")
            return self._secondary.embed(texts)


def _is_transient(e: Exception) -> bool:
    """判断是否为可回退的瞬时故障（网络 / 超时 / 5xx / 429）。"""
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return e.response.status_code >= 500 or e.response.status_code == 429
    return False


_embedder_cache: Any = None
_embedder_resolved = False


def get_embedder():
    """构造（并缓存）Embedding 提供者；无可用提供者时返回 None。"""
    global _embedder_cache, _embedder_resolved
    if _embedder_resolved:
        return _embedder_cache
    _embedder_resolved = True

    local: Any = None

    def _local():
        nonlocal local
        if local is None:
            local = _LocalEmbedder(config.local_embedding_model)
        return local

    provider = (getattr(config, "embedding_provider", "") or "").strip().lower()
    if provider == "local":
        try:
            _embedder_cache = _local()
            logger.info("Embedding 使用本地模型: %s", config.embedding_model)
        except Exception as e:  # noqa: BLE001
            logger.warning("本地 Embedding 不可用: %s", e)
        return _embedder_cache

    if config.embedding_model and config.siliconflow_api_key:
        api = _ApiEmbedder(
            model=config.embedding_model,
            api_key=config.siliconflow_api_key,
            base_url=config.siliconflow_base_url,
            batch_size=config.embedding_batch_size,
        )
        if config.embedding_use_local_fallback:
            # 惰性包装：本地模型（数十秒加载）只在 API 首次瞬时失败时才真正构造
            _embedder_cache = _FallbackEmbedder(
                api,
                _LazyEmbedder(
                    lambda: _LocalEmbedder(config.local_embedding_model),
                    label=f"local:{config.local_embedding_model}",
                ),
            )
        else:
            _embedder_cache = api
        logger.info(
            "Embedding 就绪: %s @ %s（本地兜底: %s）",
            config.embedding_model, config.siliconflow_base_url,
            "按需加载" if config.embedding_use_local_fallback else "已禁用",
        )
        return _embedder_cache

    if config.embedding_use_local_fallback:
        try:
            _embedder_cache = _local()
            logger.info("Embedding 回退本地模型: %s", config.local_embedding_model)
        except Exception as e:  # noqa: BLE001
            logger.warning("无可用 Embedding 提供者: %s", e)
    else:
        logger.warning("未配置 Embedding 提供者（缺少 SILICONFLOW_API_KEY）")

    return _embedder_cache



class VectorStore:
    """ChromaDB 向量存储，封装 Collection 的 CRUD 与语义检索。"""

    def __init__(self, persist_dir: Optional[Path] = None):
        self._available = False
        self._client = None
        self._embedding_fn = None

        # ── 初始化 ChromaDB ──
        try:
            from chromadb import PersistentClient

            self._client = PersistentClient(
                path=str(persist_dir or config.chroma_persist_dir)
            )
            self._available = True
            logger.info("ChromaDB 初始化成功: %s", config.chroma_persist_dir)
        except Exception as e:
            logger.warning("ChromaDB 不可用，降级为无记忆模式: %s", e)
            return

        # ── 初始化 Embedding ──
        self._embedder = get_embedder()
        if self._embedder is None:
            logger.warning("无可用 Embedding 提供者，降级为无记忆模式")
            self._available = False
            self._client = None

    # ── 内部工具 ───────────────────────────────────────────

    @property
    def embedder_label(self) -> str:
        """当前生效的 Embedding 提供者标识（用于诊断 / 健康检查）。"""
        return getattr(self._embedder, "label", "none")

    def _ensure(self, name: str):
        """获取或创建 Collection。"""
        try:
            return self._client.get_or_create_collection(name)
        except Exception:
            return self._client.create_collection(name)

    def _reset_collection(self, name: str):
        """删除并重建 Collection（换 Embedding 模型导致维度变化时使用）。"""
        try:
            self._client.delete_collection(name)
        except Exception:  # noqa: BLE001 - 不存在时忽略
            pass
        logger.warning(
            "已重建 Collection [%s]：Embedding 模型变更导致维度不兼容，旧向量已丢弃",
            name,
        )
        return self._ensure(name)

    @staticmethod
    def _is_dim_mismatch(e: Exception) -> bool:
        """判断异常是否为向量维度不匹配。"""
        msg = str(e).lower()
        return "dimension" in msg or "dimensionality" in msg

    def _embed(self, texts: list[str]):
        """将文本列表转为 embedding 向量列表。"""
        if not self._available or not self._embedder:
            return None
        return self._embedder.embed(texts)

    # ── 通用 CRUD ──────────────────────────────────────────

    def add(
        self,
        collection_name: str,
        documents: list[str],
        metadatas: Optional[list[dict]] = None,
        ids: Optional[list[str]] = None,
    ) -> bool:
        """写入文档。不可用时静默返回 False。"""
        if not self._available:
            return False
        try:
            col = self._ensure(collection_name)
            embeddings = self._embed(documents)
            try:
                col.add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids,
                    embeddings=embeddings,
                )
            except Exception as e:
                if not self._is_dim_mismatch(e):
                    raise
                col = self._reset_collection(collection_name)
                col.add(
                    documents=documents,
                    metadatas=metadatas,
                    ids=ids,
                    embeddings=embeddings,
                )
            return True
        except Exception as e:
            logger.warning("向量写入失败 [%s]: %s", collection_name, e)
            return False

    def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 5,
    ) -> list[dict]:
        """语义检索。不可用时返回空列表。"""
        if not self._available:
            return []
        try:
            col = self._ensure(collection_name)
            query_embedding = self._embed([query_text])
            if not query_embedding:
                return []
            try:
                results = col.query(
                    query_embeddings=query_embedding,
                    n_results=n_results,
                )
            except Exception as e:
                if not self._is_dim_mismatch(e):
                    raise
                # 换过 Embedding 模型：旧集合维度不兼容，重建后本次无结果
                self._reset_collection(collection_name)
                return []
            # 将 Chroma 返回的原始结构转为 dict 列表
            out: list[dict] = []
            ids_list = results.get("ids", [[]])[0] if results.get("ids") else []
            docs_list = results.get("documents", [[]])[0] if results.get("documents") else []
            metas_list = results.get("metadatas", [[]])[0] if results.get("metadatas") else []
            dists_list = results.get("distances", [[]])[0] if results.get("distances") else []
            for i in range(max(len(ids_list), len(docs_list))):
                item = {}
                if i < len(ids_list):
                    item["id"] = ids_list[i]
                if i < len(docs_list):
                    item["document"] = docs_list[i]
                if i < len(metas_list):
                    item["metadata"] = metas_list[i]
                if i < len(dists_list):
                    item["distance"] = dists_list[i]
                out.append(item)
            return out
        except Exception as e:
            logger.warning("向量检索失败 [%s]: %s", collection_name, e)
            return []

    def get(self, collection_name: str, doc_id: str) -> Optional[dict]:
        """按 ID 获取单条记录。"""
        if not self._available:
            return None
        try:
            col = self._ensure(collection_name)
            result = col.get(ids=[doc_id])
            ids_list = result.get("ids", [])
            if not ids_list:
                return None
            return {
                "id": ids_list[0] if ids_list else "",
                "document": (result.get("documents") or [""])[0],
                "metadata": (result.get("metadatas") or [{}])[0],
            }
        except Exception as e:
            logger.warning("向量查询失败 [%s]: %s", collection_name, e)
            return None

    def delete(self, collection_name: str, doc_id: str) -> bool:
        """按 ID 删除。"""
        if not self._available:
            return False
        try:
            col = self._ensure(collection_name)
            col.delete(ids=[doc_id])
            return True
        except Exception as e:
            logger.warning("向量删除失败 [%s]: %s", collection_name, e)
            return False

    def list_all(self, collection_name: str) -> list[dict]:
        """列出某 Collection 的全部记录。"""
        if not self._available:
            return []
        try:
            col = self._ensure(collection_name)
            result = col.get()
            ids_list = result.get("ids", [])
            docs_list = result.get("documents", [])
            metas_list = result.get("metadatas", [])
            out: list[dict] = []
            for i in range(len(ids_list)):
                out.append({
                    "id": ids_list[i],
                    "document": docs_list[i] if i < len(docs_list) else "",
                    "metadata": metas_list[i] if i < len(metas_list) else {},
                })
            return out
        except Exception as e:
            logger.warning("向量全量获取失败 [%s]: %s", collection_name, e)
            return []

    # ── 便捷方法 ───────────────────────────────────────────

    def store_interview_session(self, interview_json: str, metadata: dict) -> bool:
        """存储一次面试记录。"""
        import uuid

        doc_id = metadata.get("interview_id") or uuid.uuid4().hex[:12]
        return self.add(
            COLLECTION_INTERVIEW_SESSIONS,
            documents=[interview_json],
            metadatas=[metadata],
            ids=[doc_id],
        )

    def search_similar_questions(self, skill: str, n: int = 3) -> list[dict]:
        """搜索相似题目用于出题参考。"""
        return self.query(COLLECTION_QUESTION_BANK, skill, n_results=n)

    def search_candidate_history(self, candidate_name: str) -> list[dict]:
        """搜索候选人的历史面试记录。"""
        return self.query(
            COLLECTION_INTERVIEW_SESSIONS, candidate_name, n_results=5
        )

    def health_check(self) -> dict:
        """真实探测 Embedding 链路（config.available 只反映 ChromaDB）。"""
        info = {
            "collection_ok": self._available,
            "embedder": self.embedder_label,
            "model": config.embedding_model,
        }
        if not self._available or not self._embedder:
            info["embedding_ok"] = False
            info["error"] = "无可用 Embedding 提供者"
            return info
        try:
            vecs = self._embed(["健康检查"])
            info["embedding_ok"] = bool(vecs)
            info["dim"] = len(vecs[0]) if vecs else None
        except Exception as e:  # noqa: BLE001 - 诊断用途，返回而非抛出
            info["embedding_ok"] = False
            info["error"] = f"{type(e).__name__}: {e}"
        return info

    @property
    def available(self) -> bool:
        return self._available
