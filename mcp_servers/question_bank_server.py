from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

from mcp.server import FastMCP

from models.jd import JD
from models.question import Question, Difficulty
from agents.interviewer import InterviewerAgent
from models.llm import LLM

app = FastMCP("question-bank-server")

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_questions.json"
_seed_cache: list[dict] | None = None
_agent: InterviewerAgent | None = None


def _get_agent() -> InterviewerAgent:
    global _agent
    if _agent is None:
        _agent = InterviewerAgent(llm=LLM())
    return _agent


def _load_seed() -> list[dict]:
    """加载种子题库"""
    global _seed_cache
    if _seed_cache is None:
        if _SEED_PATH.exists():
            _seed_cache = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        else:
            _seed_cache = []
    return _seed_cache


def _save_seed(seed: list[dict]):
    """持久化种子题库"""
    _SEED_PATH.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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

    # 随机打乱后取 count 条
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
    except Exception:
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
