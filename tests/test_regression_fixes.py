"""回归测试 — 覆盖本次修复的核心 bug。

对应修复：
  1. 一次 switch 不再跳两个技能（技能索引双增）
  2. current_skill_index / consecutive_empty 能跨请求持久化
  3. candidate_name / interview_id 能进入状态并落库
  4. 技能索引越界时被夹取，不再 IndexError
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ordered_skills(n: int = 3) -> list[dict]:
    return [
        {"skill": f"技能{i}", "gap": "缺口", "reason": f"考察技能{i}", "weight": 80 - i}
        for i in range(n)
    ]


def _state_with_judge(idx: int = 0, action: str = "switch", total: int = 3) -> dict:
    """构造一个「已评判、judge 建议 switch」的状态。"""
    from models.question import JudgeResult

    return {
        "interview_id": "t1",
        "candidate_name": "张三",
        "jd_path": "",
        "resume_path": "",
        "jd_raw": "",
        "resume_raw": "",
        "jd": None,
        "resume": None,
        "gap_map": None,
        "ordered_skills": _ordered_skills(total),
        "current_skill_index": idx,
        "rounds": [],
        "current_round_number": 1,
        "question": None,
        "answer": "这是我的回答",
        "judge_result": JudgeResult(
            score=60,
            next_action=action,
            comment="一般",
            expected_points=[],
        ),
        "consecutive_empty": 0,
        "terminated": False,
        "report": None,
        "error": None,
    }


def test_switch_increments_index_once():
    """一次 switch 只能前进一个技能（修复前会前进两个）。"""
    import asyncio

    from orchestration.supervisor import decide_next_node

    state = _state_with_judge(idx=0, action="switch")
    result = asyncio.run(decide_next_node(state))
    assert result["current_skill_index"] == 1, (
        f"switch 应只前进 1 步，实际 {result['current_skill_index']}"
    )
    print("  [OK] test_switch_increments_index_once")


def test_switch_at_last_skill_is_clamped():
    """最后一个技能上 switch 不能越界。"""
    import asyncio

    from orchestration.supervisor import decide_next_node

    state = _state_with_judge(idx=2, action="switch", total=3)
    result = asyncio.run(decide_next_node(state))
    assert result["current_skill_index"] == 2, "索引不应超过技能总数-1"
    print("  [OK] test_switch_at_last_skill_is_clamped")


def test_deepen_does_not_advance_index():
    """deepen / clarify 不应改变技能索引。"""
    import asyncio

    from orchestration.supervisor import decide_next_node

    for action in ("deepen", "clarify"):
        state = _state_with_judge(idx=1, action=action)
        result = asyncio.run(decide_next_node(state))
        assert "current_skill_index" not in result, f"{action} 不应推进索引"
    print("  [OK] test_deepen_does_not_advance_index")


def test_get_current_skill_clamps_out_of_range():
    """索引越界时 _get_current_skill 必须夹取而不是抛 IndexError。"""
    from orchestration.supervisor import _get_current_skill

    state = _state_with_judge(idx=99)
    skill, gap, reason = _get_current_skill(state)
    assert skill == "技能2", f"越界应夹取到最后一个技能，实际 {skill}"
    print("  [OK] test_get_current_skill_clamps_out_of_range")


def test_skill_index_persists_through_pydantic_roundtrip():
    """current_skill_index / consecutive_empty / candidate_name 必须能往返持久化。"""
    from mcp_servers.gateway import _pydantic_to_state, _state_to_pydantic

    state = _state_with_judge(idx=2)
    state["consecutive_empty"] = 2
    state["candidate_name"] = "李四"
    state["interview_id"] = "abc123"

    ps = _state_to_pydantic(state)
    assert ps.current_skill_index == 2, "落库时技能索引进度丢失"
    assert ps.consecutive_empty == 2, "落库时空回答计数丢失"
    assert ps.candidate_name == "李四", "候选人姓名丢失"

    back = _pydantic_to_state(ps)
    assert back["current_skill_index"] == 2, "重建时技能索引进度丢失"
    assert back["consecutive_empty"] == 2, "重建时空回答计数丢失"
    assert back["candidate_name"] == "李四", "重建时候选人姓名丢失"
    assert back["interview_id"] == "abc123", "重建时 interview_id 丢失"
    print("  [OK] test_skill_index_persists_through_pydantic_roundtrip")


def test_initial_state_has_session_fields():
    """初始状态必须带 interview_id / candidate_name，供记忆钩子读取。"""
    from orchestration.supervisor import initial_state

    state = initial_state("jd.pdf", "resume.pdf")
    assert "interview_id" in state
    assert "candidate_name" in state
    assert state["current_skill_index"] == 0
    assert state["consecutive_empty"] == 0
    print("  [OK] test_initial_state_has_session_fields")


def test_prompt_template_len_and_contains():
    """PromptTemplate 支持 len() 与 in（修复测试报 TypeError 的根因）。"""
    from prompts import load_prompt

    tmpl = load_prompt("interviewer")
    assert len(tmpl) > 100
    assert "{target_skill}" in tmpl
    assert "{difficulty}" in tmpl
    assert "不存在的变量占位符" not in tmpl
    print("  [OK] test_prompt_template_len_and_contains")


def test_collection_names_follow_prefix():
    """集合名由 chroma_collection_prefix 派生，默认保持向后兼容。"""
    from memory.vector_store import (
        COLLECTION_INTERVIEW_SESSIONS,
        COLLECTION_QUESTION_BANK,
    )

    assert COLLECTION_QUESTION_BANK == "ih_question_bank"
    assert COLLECTION_INTERVIEW_SESSIONS == "ih_interview_sessions"
    print("  [OK] test_collection_names_follow_prefix")


def test_seed_questions_json_is_valid():
    """种子题库必须是合法 JSON（曾因尾逗号损坏导致 3 个 MCP 工具全部 500）。"""
    import json

    p = Path(__file__).resolve().parent.parent / "data" / "seed_questions.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) > 0
    for q in data:
        assert "skill" in q and "content" in q
    print(f"  [OK] test_seed_questions_json_is_valid ({len(data)} 道题)")


def test_config_int_parsing_is_safe():
    """非法整数环境变量应回退默认值，而不是让 config import 崩溃。"""
    from config import _env_int

    import os

    os.environ["__DSH_TEST_INT__"] = "not-a-number"
    try:
        assert _env_int("__DSH_TEST_INT__", 42) == 42
        os.environ["__DSH_TEST_INT__"] = "7"
        assert _env_int("__DSH_TEST_INT__", 42) == 7
    finally:
        os.environ.pop("__DSH_TEST_INT__", None)
    print("  [OK] test_config_int_parsing_is_safe")


if __name__ == "__main__":
    print("回归测试（本次修复）\n" + "=" * 30)
    test_switch_increments_index_once()
    test_switch_at_last_skill_is_clamped()
    test_deepen_does_not_advance_index()
    test_get_current_skill_clamps_out_of_range()
    test_skill_index_persists_through_pydantic_roundtrip()
    test_initial_state_has_session_fields()
    test_prompt_template_len_and_contains()
    test_collection_names_follow_prefix()
    test_seed_questions_json_is_valid()
    test_config_int_parsing_is_safe()
    print("\n[OK] 全部回归测试通过")
