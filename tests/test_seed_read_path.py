"""补充测试：题库只写不读的修复、损坏保护、会话排序。

覆盖本轮改动：
  1. find_seed_by_skill —— 让出题环节能读到种子题库（闭合"只写不读"）
  2. 出题提示同时来自向量库与种子题库
  3. 题库文件损坏时 promote_questions 拒绝写入（避免覆盖丢数据）
  4. SessionStore.list_all / find_by_candidate 按创建时间倒序
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_servers import question_bank_server as qb  # noqa: E402


# ══════════════════════════════════════════════════════════
# find_seed_by_skill
# ══════════════════════════════════════════════════════════

@pytest.fixture
def seed_with_skills(tmp_path, monkeypatch):
    seed_file = tmp_path / "seed_questions.json"
    seed_file.write_text(json.dumps([
        {
            "skill": "Python", "difficulty": "advanced",
            "content": "Python 的 GIL 是什么？它对多线程有什么影响？",
            "context": "", "expected_answer_points": ["要点一足够长", "要点二足够长", "要点三足够长"],
        },
        {
            "skill": "Python", "difficulty": "basic",
            "content": "列表和元组的区别是什么，分别在什么场景使用？",
            "context": "", "expected_answer_points": ["要点一足够长"],
        },
        {
            "skill": "MySQL/SQL", "difficulty": "intermediate",
            "content": "请解释 MySQL 的索引类型及各自适用场景。",
            "context": "", "expected_answer_points": [],
        },
        {
            "skill": "Redis", "difficulty": "advanced",
            "content": "Redis 的持久化机制 RDB 与 AOF 有什么区别？",
            "context": "", "expected_answer_points": [],
        },
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(qb, "_SEED_PATH", seed_file)
    monkeypatch.setattr(qb, "_seed_cache", None)
    yield seed_file
    monkeypatch.setattr(qb, "_seed_cache", None)


def test_find_seed_exact_skill(seed_with_skills):
    hits = qb.find_seed_by_skill("Redis")
    assert len(hits) == 1
    assert "Redis" in hits[0]["content"]


def test_find_seed_prefers_richer_answer_points(seed_with_skills):
    """同技能多题时，作答要点多的应排前面（信息量更大）。"""
    hits = qb.find_seed_by_skill("Python", limit=2)
    assert len(hits) == 2
    assert len(hits[0]["expected_answer_points"]) >= len(hits[1]["expected_answer_points"])


def test_find_seed_substring_match(seed_with_skills):
    """技能名互为子串应能匹配（MySQL 与 MySQL/SQL 这类写法差异）。"""
    assert qb.find_seed_by_skill("MySQL")
    assert qb.find_seed_by_skill("MySQL/SQL")


def test_find_seed_respects_limit(seed_with_skills):
    assert len(qb.find_seed_by_skill("Python", limit=1)) == 1


def test_find_seed_empty_skill_returns_empty(seed_with_skills):
    assert qb.find_seed_by_skill("") == []
    assert qb.find_seed_by_skill("   ") == []


def test_find_seed_no_match(seed_with_skills):
    assert qb.find_seed_by_skill("Kubernetes") == []


# ══════════════════════════════════════════════════════════
# 出题提示同时来自两个来源
# ══════════════════════════════════════════════════════════

def test_question_hint_includes_seed_bank(seed_with_skills, monkeypatch):
    """回归：此前出题只查向量库，题库里攒的题对出题毫无帮助。"""
    from agents.interviewer import InterviewerAgent

    # 屏蔽向量库来源，单独验证种子题库是否被读到
    monkeypatch.setattr(
        "memory.vector_store.VectorStore",
        lambda *a, **k: type("VS", (), {"available": False})(),
    )

    agent = InterviewerAgent()
    hint = agent._get_similar_questions_hint("Redis")
    assert hint, "应能给出提示"
    assert "Redis" in hint
    assert "题库中已有的同技能题" in hint


def test_question_hint_includes_answer_points(seed_with_skills, monkeypatch):
    """题库题的作答要点应注入提示，作为考察角度参考。"""
    from agents.interviewer import InterviewerAgent

    monkeypatch.setattr(
        "memory.vector_store.VectorStore",
        lambda *a, **k: type("VS", (), {"available": False})(),
    )
    hint = InterviewerAgent()._get_similar_questions_hint("Python")
    assert "参考考察点" in hint


def test_question_hint_degrades_when_both_sources_fail(monkeypatch):
    """两个来源都失败时应返回空串，而不是抛异常。"""
    from agents.interviewer import InterviewerAgent

    def boom(*a, **k):
        raise RuntimeError("模拟向量库不可用")

    monkeypatch.setattr("memory.vector_store.VectorStore", boom)
    monkeypatch.setattr(qb, "find_seed_by_skill", boom)

    assert InterviewerAgent()._get_similar_questions_hint("Python") == ""


# ══════════════════════════════════════════════════════════
# 题库损坏保护
# ══════════════════════════════════════════════════════════

def test_corrupted_seed_raises_specific_error(tmp_path, monkeypatch):
    bad = tmp_path / "seed_questions.json"
    bad.write_text("{ 这不是合法 JSON ", encoding="utf-8")
    monkeypatch.setattr(qb, "_SEED_PATH", bad)
    monkeypatch.setattr(qb, "_seed_cache", None)
    with pytest.raises(qb.SeedBankCorruptedError):
        qb._load_seed(reload=True)


def test_corrupted_seed_aborts_promotion_without_overwriting(tmp_path, monkeypatch):
    """核心保护：损坏时拒绝写入，否则会把题库覆盖成只剩新增的题。"""
    bad = tmp_path / "seed_questions.json"
    original = "{ 损坏的内容，但有价值 "
    bad.write_text(original, encoding="utf-8")
    monkeypatch.setattr(qb, "_SEED_PATH", bad)
    monkeypatch.setattr(qb, "_seed_cache", None)

    stats = qb.promote_questions([{
        "skill": "Python", "difficulty": "basic",
        "content": "这是一道长度足够的新题目，用于验证损坏保护。",
        "context": "", "expected_answer_points": ["要点一足够长", "要点二足够长"],
    }])

    assert stats.get("aborted") is True
    assert stats["added"] == 0
    assert bad.read_text(encoding="utf-8") == original, "损坏文件被覆盖了！"


def test_missing_seed_file_is_not_corruption(tmp_path, monkeypatch):
    """文件不存在应视为空题库，而非损坏。"""
    monkeypatch.setattr(qb, "_SEED_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(qb, "_seed_cache", None)
    assert qb._load_seed(reload=True) == []


def test_non_list_seed_is_corruption(tmp_path, monkeypatch):
    p = tmp_path / "seed_questions.json"
    p.write_text('{"not": "a list"}', encoding="utf-8")
    monkeypatch.setattr(qb, "_SEED_PATH", p)
    monkeypatch.setattr(qb, "_seed_cache", None)
    with pytest.raises(qb.SeedBankCorruptedError):
        qb._load_seed(reload=True)


# ══════════════════════════════════════════════════════════
# 会话排序（按创建时间，而非 UUID 字典序）
# ══════════════════════════════════════════════════════════

def test_list_all_sorted_by_created_at(tmp_path, monkeypatch):
    """回归：文件名是随机 UUID，字典序与时间无关，必须按 created_at 排序。"""
    from memory.session_store import SessionStore
    from models.interview import InterviewState

    store = SessionStore(store_dir=tmp_path)
    base = datetime(2026, 1, 1, 12, 0, 0)

    # 刻意让"UUID 字典序"与"创建时间"相反：最早创建的文件名反而更大
    cases = [
        ("zzz_oldest", base),
        ("mmm_middle", base + timedelta(hours=1)),
        ("aaa_newest", base + timedelta(hours=2)),
    ]
    for iid, created in cases:
        st = InterviewState(interview_id=iid, candidate_name="张三")
        st.created_at = created
        store.save(st)

    result = store.list_all()
    names = [s.interview_id for s in result]
    assert names == ["aaa_newest", "mmm_middle", "zzz_oldest"], (
        f"应按创建时间倒序，实际 {names}"
    )


def test_find_by_candidate_sorted_and_filtered(tmp_path):
    from memory.session_store import SessionStore
    from models.interview import InterviewState

    store = SessionStore(store_dir=tmp_path)
    base = datetime(2026, 1, 1, 12, 0, 0)
    for i, (iid, name) in enumerate([
        ("zzz", "张三"), ("mmm", "李四"), ("aaa", "张三"),
    ]):
        st = InterviewState(interview_id=iid, candidate_name=name)
        st.created_at = base + timedelta(hours=i)
        store.save(st)

    hits = store.find_by_candidate("张三")
    assert [s.interview_id for s in hits] == ["aaa", "zzz"]


def test_load_corrupted_session_returns_none(tmp_path):
    """损坏的会话文件应返回 None 并记日志，而不是抛给调用方。"""
    from memory.session_store import SessionStore

    store = SessionStore(store_dir=tmp_path)
    (tmp_path / "broken.json").write_text("{ 坏文件", encoding="utf-8")
    assert store.load("broken") is None
    # list_all 也不应因单个损坏文件而整体失败
    assert store.list_all() == []
