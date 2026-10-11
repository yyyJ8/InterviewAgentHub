from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path

from mcp.server import FastMCP

from models.jd import JD
from models.question import Question
from agents.interviewer import InterviewerAgent
from models.llm import LLM

logger = logging.getLogger("mcp.question_bank")

app = FastMCP("question-bank-server")

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_questions.json"
_seed_cache: list[dict] | None = None
_agent: InterviewerAgent | None = None


def _get_agent() -> InterviewerAgent:
    global _agent
    if _agent is None:
        _agent = InterviewerAgent(llm=LLM())
    return _agent


class SeedBankCorruptedError(RuntimeError):
    """种子题库文件存在但无法解析。

    刻意与"文件不存在（返回空列表）"区分：如果损坏时也返回空列表，
    promote_questions 会把整个题库覆盖成只剩本次新增的题，导致原有数据全丢。
    因此破坏性写入路径必须能在这种情况下中止。
    """


def _load_seed(reload: bool = False) -> list[dict]:
    """加载种子题库。

    Args:
        reload: True 时强制从文件重新读取（CLI 与面试流程可能分别写同一文件，
                缓存在多写入方场景下会过期）。

    Raises:
        SeedBankCorruptedError: 文件存在但内容无法解析为 JSON 列表。
    """
    global _seed_cache
    if _seed_cache is None or reload:
        if not _SEED_PATH.exists():
            _seed_cache = []
        else:
            try:
                data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                logger.error("种子题库无法解析 %s: %s", _SEED_PATH, e)
                raise SeedBankCorruptedError(f"{_SEED_PATH} 解析失败: {e}") from e
            if not isinstance(data, list):
                raise SeedBankCorruptedError(
                    f"{_SEED_PATH} 顶层应为 JSON 数组，实际是 {type(data).__name__}"
                )
            _seed_cache = data
    return _seed_cache


def _save_seed(seed: list[dict]):
    """持久化种子题库"""
    _SEED_PATH.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 题库沉淀 ─────────────────────────────────────────────
#
# 设计与边界：
#   - seed_questions.json 是唯一真相来源（source of truth），向量库只是检索索引
#   - 沉淀是"入账"而非替换：只追加，content 去重（忽略空白与大小写）
#   - 质量门槛见 is_promotable()；不达标的题不写入，避免污染题库

# 质量门槛
MIN_CONTENT_CHARS = 15          # 题干过短的多为无效生成
MIN_ANSWER_POINTS = 2           # 作答要点少于 2 条说明 LLM 未给全
MIN_POINT_CHARS = 5             # 单条要点过短视为无意义


def _normalize(text: str) -> str:
    """用于去重的归一化：去除所有空白并小写。"""
    return re.sub(r"\s+", "", str(text)).lower()


def is_promotable(question: dict) -> tuple[bool, str]:
    """判断一道题是否够格沉淀进题库。

    Returns:
        (是否合格, 不合格原因)
    """
    content = str(question.get("content", "")).strip()
    skill = str(question.get("skill", "")).strip()

    if not skill:
        return False, "缺少 skill"
    if len(content) < MIN_CONTENT_CHARS:
        return False, f"题干过短（{len(content)} < {MIN_CONTENT_CHARS} 字符）"

    # 作答要点是质量的主要信号；来源缺少该字段时不作为硬性拒绝条件
    points = question.get("expected_answer_points")
    if points is not None:
        substantive = [p for p in points if len(str(p).strip()) >= MIN_POINT_CHARS]
        if len(substantive) < MIN_ANSWER_POINTS:
            return False, (
                f"作答要点不足（有效 {len(substantive)} < {MIN_ANSWER_POINTS} 条）"
            )
    return True, ""


def promote_questions(questions: list[dict], limit: int = 10) -> dict:
    """把一批题目沉淀进种子题库（去重 + 质量过滤）。

    Args:
        questions: 候选题目 dict 列表，字段同 Question（skill/difficulty/content/
                   context/expected_answer_points）
        limit: 单次最多新增数量，避免一次写入过多

    Returns:
        统计信息 {"added": int, "skipped_duplicate": int, "skipped_quality": int,
                 "reasons": [...], "total": int}
    """
    try:
        seed = _load_seed(reload=True)
    except SeedBankCorruptedError as e:
        # 拒绝在读到损坏文件时写入：否则会把整个题库覆盖成只剩本次新增的题
        logger.error("题库文件损坏，已中止沉淀以避免覆盖原有数据: %s", e)
        return {
            "added": 0, "skipped_duplicate": 0, "skipped_quality": 0,
            "reasons": [f"题库文件损坏，已中止: {e}"], "total": 0, "aborted": True,
        }
    existing = {_normalize(q.get("content", "")) for q in seed}

    stats = {"added": 0, "skipped_duplicate": 0, "skipped_quality": 0, "reasons": []}
    added: list[dict] = []

    for q in questions:
        if len(added) >= limit:
            break

        ok, reason = is_promotable(q)
        if not ok:
            stats["skipped_quality"] += 1
            stats["reasons"].append(f"{str(q.get('content', ''))[:30]}… → {reason}")
            continue

        key = _normalize(q.get("content", ""))
        if key in existing:
            stats["skipped_duplicate"] += 1
            continue

        entry = {
            "skill": str(q.get("skill", "")).strip()[:50],
            "difficulty": str(q.get("difficulty", "intermediate")),
            "content": str(q.get("content", "")).strip(),
            "context": str(q.get("context") or ""),
            "expected_answer_points": [
                str(p).strip() for p in (q.get("expected_answer_points") or [])
            ],
        }
        added.append(entry)
        existing.add(key)

    if added:
        seed.extend(added)
        _save_seed(seed)
        stats["added"] = len(added)

    try:
        stats["total"] = len(_load_seed(reload=True))
    except SeedBankCorruptedError:
        # 理论上不会走到（写入前已校验过），兜底避免统计失败影响返回值
        stats["total"] = -1
    return stats


@app.tool()
async def generate_questions(
    jd_json: str,
    skill: str,
    difficulty: str = "intermediate",
    count: int = 1,
) -> str:
    """LLM 动态生成面试题

    Args:
        jd_json: JD 结构化 JSON 字符串
        skill: 目标技能名称
        difficulty: 难度级别 (basic/intermediate/advanced/deep)
        count: 生成题目数量 (最多 3)

    Returns:
        题目列表的 JSON 字符串
    """
    try:
        jd = JD.model_validate_json(jd_json)
        agent = _get_agent()

        questions = []
        for _ in range(min(count, 3)):
            # 创建一个最简简历以允许出题
            from models.resume import Resume
            resume = Resume(name="候选人", skills=[])

            question = await agent.generate_question(
                jd=jd,
                resume=resume,
                target_skill=skill,
                difficulty=difficulty,
                intent=f"考察 {skill} 的掌握程度",
            )
            questions.append(question.model_dump())

        return json.dumps(questions, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


def find_seed_by_skill(skill: str, limit: int = 3) -> list[dict]:
    """按技能名从种子题库取题（供出题时参考）。

    匹配规则：技能名归一化后相等，或互为子串（如 "MySQL" 与 "MySQL/SQL"）。
    返回至多 limit 条，优先返回带作答要点的（质量更高）。

    Args:
        skill: 目标技能名
        limit: 最多返回条数

    Returns:
        题目 dict 列表（同一份题库对象，调用方不应就地修改）
    """
    if not skill or not str(skill).strip():
        return []

    target = _normalize(skill)
    matched = []
    for q in _load_seed():
        q_skill = _normalize(q.get("skill", ""))
        if not q_skill:
            continue
        if q_skill == target or q_skill in target or target in q_skill:
            matched.append(q)

    # 带作答要点的排前面（信息量更大，更利于 LLM 参考考察角度）
    matched.sort(key=lambda q: -len(q.get("expected_answer_points") or []))
    return matched[:max(1, limit)]


@app.tool()
def search_seed_bank(
    skill: str = "",
    difficulty: str = "",
    count: int = 5,
) -> str:
    """从种子题库检索题目

    Args:
        skill: 技能名称（空字符串则返回所有）
        difficulty: 难度级别（空字符串则返回所有）
        count: 返回数量

    Returns:
        匹配题目的 JSON 字符串
    """
    seed = _load_seed()
    matched = seed

    if skill:
        matched = [q for q in matched if q["skill"].lower() == skill.lower()]
    if difficulty:
        matched = [q for q in matched if q["difficulty"] == difficulty]

    # 随机打乱后取 count 条。注意必须拷贝：seed 是模块级缓存，
    # 就地 shuffle 会永久改变题库顺序（跨请求累积的状态污染）。
    matched = list(matched)
    random.shuffle(matched)
    result = matched[:count]

    return json.dumps(result, ensure_ascii=False)


@app.tool()
def add_to_seed_bank(question_json: str) -> bool:
    """将优质题目加入种子题库

    Args:
        question_json: Question 对象的 JSON 字符串

    Returns:
        是否成功
    """
    try:
        question = Question.model_validate_json(question_json)
        seed = _load_seed()

        # 去重：检查 content 是否已存在
        for existing in seed:
            if existing["content"].strip() == question.content.strip():
                return False  # 已存在，跳过

        seed.append(question.model_dump())
        _save_seed(seed)
        return True
    except Exception as e:  # noqa: BLE001 - MCP 工具需返回 bool 而非抛出
        logger.warning("题目写入种子题库失败: %s", e)
        return False


@app.tool()
def get_seed_bank_stats() -> str:
    """获取种子题库统计信息"""
    seed = _load_seed()
    skill_counts: dict[str, int] = {}
    diff_counts: dict[str, int] = {}
    for q in seed:
        s = q.get("skill", "unknown")
        d = q.get("difficulty", "unknown")
        skill_counts[s] = skill_counts.get(s, 0) + 1
        diff_counts[d] = diff_counts.get(d, 0) + 1

    return json.dumps({
        "total": len(seed),
        "by_skill": dict(sorted(skill_counts.items(), key=lambda x: -x[1])),
        "by_difficulty": dict(sorted(diff_counts.items(), key=lambda x: -x[1])),
    }, ensure_ascii=False)


if __name__ == "__main__":
    app.run()
