"""面试会话持久化存储 — JSON 文件方案

轻量级存储，不依赖 ChromaDB。每场面试一个 JSON 文件，存储在 data/sessions/ 目录下。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config
from models.interview import InterviewState

logger = logging.getLogger("memory.session_store")


class SessionStore:
    """面试会话的 JSON 文件持久化存储"""

    def __init__(self, store_dir: Optional[Path] = None):
        self._dir = store_dir or (config.data_dir / "sessions")
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── 路径工具 ──────────────────────────────────────────

    def _path(self, interview_id: str) -> Path:
        return self._dir / f"{interview_id}.json"

    # ── CRUD ──────────────────────────────────────────────

    def save(self, state: InterviewState) -> str:
        """保存面试状态。若 interview_id 为空则自动生成 UUID。返回 interview_id。

        写入失败会记日志并向上抛出：这是面试进度的唯一持久化路径，
        静默失败等于丢数据，必须让调用方（HTTP 层 → 500）感知。
        """
        if not state.interview_id:
            state.interview_id = uuid.uuid4().hex[:12]
        state.updated_at = datetime.now()
        data = state.model_dump(mode="json")
        try:
            self._path(state.interview_id).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error(
                "面试状态写入失败 [%s]: %s（该轮进度可能丢失）", state.interview_id, e
            )
            raise
        return state.interview_id

    def load(self, interview_id: str) -> Optional[InterviewState]:
        """按 ID 加载面试状态。不存在则返回 None。

        文件损坏时记日志并返回 None（调用方会按"会话不存在"处理），
        避免把损坏当正常却又毫无痕迹。
        """
        p = self._path(interview_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return InterviewState(**data)
        except (OSError, ValueError, TypeError) as e:
            logger.warning("面试状态读取/解析失败 [%s]: %s", interview_id, e)
            return None

    def delete(self, interview_id: str) -> bool:
        """按 ID 删除。成功返回 True。"""
        p = self._path(interview_id)
        if p.exists():
            p.unlink()
            return True
        return False

    # ── 查询 ──────────────────────────────────────────────

    def find_by_candidate(self, name: str) -> list[InterviewState]:
        """按候选人姓名搜索（子串匹配）。结果按创建时间倒序。"""
        results: list[InterviewState] = []
        for p in self._dir.glob("*.json"):
            state = self.load(p.stem)   # load 内部已处理损坏文件与日志
            if state and name.lower() in (state.candidate_name or "").lower():
                results.append(state)
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results

    def list_all(self) -> list[InterviewState]:
        """列出全部面试记录，按创建时间倒序。

        注意：文件名是随机 UUID，字典序与创建时间无关，
        因此必须按 created_at 排序（否则 --last 取到的不是最新一场）。
        """
        results: list[InterviewState] = []
        for p in self._dir.glob("*.json"):
            state = self.load(p.stem)
            if state:
                results.append(state)
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results

    def count(self) -> int:
        """返回存储的面试数量。"""
        return len(list(self._dir.glob("*.json")))
