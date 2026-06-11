"""ChromaDB 向量存储封装

提供 4 个 Collection 的 CRUD 操作：
  - ih_jd_history       : JD 全文
  - ih_question_bank    : 题目内容
  - ih_interview_sessions: 面试记录全文
  - ih_candidate_profiles: 候选人简历摘要

Embedding 策略：本地 sentence-transformers/all-MiniLM-L6-v2
连接失败时优雅降级，不影响核心面试流程。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from config import config

logger = logging.getLogger(__name__)


# ── 4 个 Collection 名称 ──────────────────────────────────

COLLECTION_JD_HISTORY = "ih_jd_history"
COLLECTION_QUESTION_BANK = "ih_question_bank"
COLLECTION_INTERVIEW_SESSIONS = "ih_interview_sessions"
COLLECTION_CANDIDATE_PROFILES = "ih_candidate_profiles"

ALL_COLLECTIONS = [
    COLLECTION_JD_HISTORY,
    COLLECTION_QUESTION_BANK,
    COLLECTION_INTERVIEW_SESSIONS,
    COLLECTION_CANDIDATE_PROFILES,
]


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
        try:
            import os as _os

            # 必须在 import sentence_transformers 之前设置 endpoint
            if not _os.environ.get("HF_ENDPOINT"):
                _os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

            from sentence_transformers import SentenceTransformer

            model_name = config.embedding_model or "sentence-transformers/all-MiniLM-L6-v2"

            # 先试当前 endpoint，失败则回退到默认 huggingface.co
            for attempt, endpoint in enumerate([
                _os.environ.get("HF_ENDPOINT", ""),
                "https://huggingface.co",
            ]):
                if not endpoint:
                    continue
                try:
                    _os.environ["HF_ENDPOINT"] = endpoint
                    self._embedding_model = SentenceTransformer(model_name)
                    break
                except Exception:
                    if attempt == 1:
                        raise
                    continue

            self._embedding_fn = self._embedding_model.encode
            logger.info("Embedding 模型加载成功: %s", model_name)
        except Exception as e:
            logger.warning("Embedding 模型加载失败，降级为无记忆模式: %s", e)
            self._available = False
            self._client = None

    # ── 内部工具 ───────────────────────────────────────────

    def _ensure(self, name: str):
        """获取或创建 Collection。"""
        try:
            return self._client.get_or_create_collection(name)
        except Exception:
            return self._client.create_collection(name)

    def _embed(self, texts: list[str]):
        """将文本列表转为 embedding 向量列表。"""
        if not self._available or not self._embedding_fn:
            return None
        embeddings = self._embedding_fn(texts)
        return embeddings.tolist()

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
            results = col.query(
                query_embeddings=query_embedding,
                n_results=n_results,
            )
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

    def update_candidate_profile(self, name: str, profile_json: str, extra_meta: Optional[dict] = None) -> bool:
        """写入或更新候选人画像。"""
        metadata = extra_meta or {}
        metadata["name"] = name
        return self.add(
            COLLECTION_CANDIDATE_PROFILES,
            documents=[profile_json],
            metadatas=[metadata],
            ids=[f"profile_{name}"],
        )

    @property
    def available(self) -> bool:
        return self._available
