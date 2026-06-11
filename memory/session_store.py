"""面试会话持久化存储 — JSON 文件方案

轻量级存储，不依赖 ChromaDB。每场面试一个 JSON 文件，存储在 data/sessions/ 目录下。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config
from models.interview import InterviewState, InterviewStatus


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
        """保存面试状态。若 interview_id 为空则自动生成 UUID。返回 interview_id。"""
        if not state.interview_id:
            state.interview_id = uuid.uuid4().hex[:12]
        state.updated_at = datetime.now()
        data = state.model_dump(mode="json")
        self._path(state.interview_id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return state.interview_id

    def load(self, interview_id: str) -> Optional[InterviewState]:
        """按 ID 加载面试状态。不存在则返回 None。"""
        p = self._path(interview_id)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return InterviewState(**data)

    def delete(self, interview_id: str) -> bool:
        """按 ID 删除。成功返回 True。"""
        p = self._path(interview_id)
        if p.exists():
            p.unlink()
            return True
        return False

    # ── 查询 ──────────────────────────────────────────────

    def find_by_candidate(self, name: str) -> list[InterviewState]:
        """按候选人姓名搜索（子串匹配）。"""
        results: list[InterviewState] = []
        for p in sorted(self._dir.glob("*.json")):
            try:
                state = self.load(p.stem)
                if state and name.lower() in (state.candidate_name or "").lower():
                    results.append(state)
            except Exception:
                continue
        return results

    def list_all(self) -> list[InterviewState]:
        """列出全部面试记录，按创建时间倒序。"""
        results: list[InterviewState] = []
        for p in sorted(self._dir.glob("*.json"), reverse=True):
            try:
                state = self.load(p.stem)
                if state:
                    results.append(state)
            except Exception:
                continue
        return results

    def count(self) -> int:
        """返回存储的面试数量。"""
        return len(list(self._dir.glob("*.json")))
