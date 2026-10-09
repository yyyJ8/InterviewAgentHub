"""题库沉淀测试 — 质量过滤、去重、追加写入、缓存重载。

注意：本文件会临时替换 data/seed_questions.json，测试后恢复原内容。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_servers import question_bank_server as qb  # noqa: E402


@pytest.fixture
def temp_seed(tmp_path, monkeypatch):
    """把题库路径指向临时文件，避免污染真实题库。"""
    seed_file = tmp_path / "seed_questions.json"
    seed_file.write_text(
        json.dumps([
            {
                "skill": "Python",
                "difficulty": "basic",
                "content": "已存在的题目：Python 中列表和元组的区别是什么？",
                "context": "",
                "expected_answer_points": ["可变性不同", "语法不同"],
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(qb, "_SEED_PATH", seed_file)
    monkeypatch.setattr(qb, "_seed_cache", None)
    yield seed_file
    monkeypatch.setattr(qb, "_seed_cache", None)


def _q(content="这是一道足够长的面试题，用于测试沉淀逻辑是否正常工作。",
       skill="Python", points=None, difficulty="basic"):
    return {
        "skill": skill,
        "difficulty": difficulty,
        "content": content,
        "context": "",
        "expected_answer_points": points if points is not None else ["要点一足够长", "要点二足够长"],
    }


# ── 质量门槛 ──────────────────────────────────────────

def test_rejects_missing_skill():
    ok, reason = qb.is_promotable(_q(skill=""))
    assert not ok and "skill" in reason


def test_rejects_short_content():
    ok, reason = qb.is_promotable(_q(content="太短了"))
    assert not ok and "过短" in reason


def test_rejects_insufficient_answer_points():
    ok, reason = qb.is_promotable(_q(points=["只有一条足够长的要点"]))
    assert not ok and "作答要点不足" in reason


def test_rejects_short_answer_points():
    ok, reason = qb.is_promotable(_q(points=["短", "也短"]))
    assert not ok and "作答要点不足" in reason


def test_accepts_quality_question():
    ok, reason = qb.is_promotable(_q())
    assert ok, reason


def test_accepts_when_points_absent():
    """来源缺少 expected_answer_points 字段时，不作为硬性拒绝条件。"""
    ok, reason = qb.is_promotable(_q(points=None))
    assert ok, reason


# ── 沉淀行为 ──────────────────────────────────────────

def test_promote_appends_and_persists(temp_seed):
    stats = qb.promote_questions([_q(content="全新的一道关于装饰器的问题，长度足够。")])
    assert stats["added"] == 1
    assert stats["total"] == 2

    saved = json.loads(temp_seed.read_text(encoding="utf-8"))
    assert len(saved) == 2
    assert any("装饰器" in q["content"] for q in saved)


def test_promote_deduplicates_by_normalized_content(temp_seed):
    """去重应忽略空白与大小写差异。"""
    same = "已存在的题目：Python 中列表和元组的区别是什么？"
    variants = [
        _q(content=same),
        _q(content=same.replace("：", "： ")),      # 多一个空格
        _q(content=same.upper()),                    # 大小写
    ]
    stats = qb.promote_questions(variants)
    assert stats["added"] == 0
    assert stats["skipped_duplicate"] == 3


def test_promote_skips_low_quality_with_reason(temp_seed):
    stats = qb.promote_questions([_q(content="短"), _q(points=["仅一条够长的要点"])])
    assert stats["added"] == 0
    assert stats["skipped_quality"] == 2
    assert len(stats["reasons"]) == 2


def test_promote_respects_limit(temp_seed):
    many = [_q(content=f"第 {i} 道长度足够的测试题目，用于验证 limit 生效。") for i in range(5)]
    stats = qb.promote_questions(many, limit=2)
    assert stats["added"] == 2
    assert stats["total"] == 3


def test_promote_reloads_external_changes(temp_seed):
    """关键：外部写入文件后，缓存必须能重载，否则会覆盖别人的写入。"""
    # 模拟另一个进程往文件里追加了一道题
    data = json.loads(temp_seed.read_text(encoding="utf-8"))
    data.append({
        "skill": "Go", "difficulty": "basic",
        "content": "外部进程写入的一道关于 goroutine 的题目，长度足够。",
        "context": "", "expected_answer_points": ["要点一足够长", "要点二足够长"],
    })
    temp_seed.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    stats = qb.promote_questions([_q(content="本次新增的一道关于 channel 的题目，长度足够。")])
    saved = json.loads(temp_seed.read_text(encoding="utf-8"))
    contents = [q["content"] for q in saved]

    assert stats["total"] == 3, f"应保留外部写入，实际 {stats['total']}"
    assert any("goroutine" in c for c in contents), "外部写入被覆盖了"


def test_promote_no_candidates_is_noop(temp_seed):
    stats = qb.promote_questions([])
    assert stats["added"] == 0
    assert stats["total"] == 1


# ── search_seed_bank 的缓存污染修复 ───────────────────

def test_search_does_not_mutate_seed_order(temp_seed):
    """回归：random.shuffle 曾就地打乱模块级缓存，导致题库顺序被永久改变。"""
    original = [q["content"] for q in qb._load_seed(reload=True)]
    for _ in range(5):
        qb.search_seed_bank(skill="", difficulty="", count=5)
    after = [q["content"] for q in qb._load_seed()]
    assert after == original, "search_seed_bank 不应改变题库顺序"


# ── 面试结束时的沉淀集成 ──────────────────────────────

def test_promote_interview_questions_from_state(temp_seed):
    """从面试 state 的 rounds 中提取题目并沉淀。"""
    from orchestration.supervisor import promote_interview_questions
    from models.question import Question, Difficulty, JudgeResult, RoundRecord

    def round_of(content: str):
        q = Question(
            skill="Python", difficulty=Difficulty.BASIC, content=content,
            expected_answer_points=["要点一足够长", "要点二足够长"],
        )
        return RoundRecord(
            round_number=1, skill="Python", question=q, answer="回答",
            judge=JudgeResult(score=80, comment="ok", next_action="switch"),
        )

    state = {"rounds": [
        round_of("这是一道从面试流程沉淀下来的题目，长度足够。"),
        round_of("这是一道长度不足的题"),
    ]}
    stats = promote_interview_questions(state)
    assert stats["added"] == 1, stats
    assert stats["skipped_quality"] == 1


def test_promote_interview_questions_handles_empty_state():
    from orchestration.supervisor import promote_interview_questions

    stats = promote_interview_questions({"rounds": []})
    assert stats["added"] == 0
