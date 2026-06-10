from __future__ import annotations

from models.jd import JD, Skill
from models.resume import Resume, SkillProficiency


def rank_skills(jd: JD, resume: Resume) -> list[dict]:
    """将 JD 技能按面试考察优先级排序

    排序策略（DESIGN.md §4）：
    1. 先排有项目经验支撑的技能（候选人能展开聊）
    2. 再排有技能但无项目经验的
    3. 再排缺口技能（JD 要求但简历未提及的）
    4. 加分技能排在最后

    返回列表，每项包含：
        skill, weight, level, gap, reason, priority[, is_bonus]
    """
    resume_skill_map: dict[str, SkillProficiency] = {
        s.name.lower(): s for s in resume.skills
    }
    resume_project_techs: set[str] = set()
    for p in resume.projects:
        for t in p.tech_stack:
            resume_project_techs.add(t.lower())

    ranked: list[dict] = []

    for skill in jd.required_skills:
        level, gap, reason = _assess_gap(skill, resume_skill_map, resume_project_techs)
        ranked.append({
            "skill": skill.name,
            "weight": skill.weight,
            "level": level,
            "gap": gap,
            "reason": reason,
        })

    for skill in jd.bonus_skills:
        level, gap, reason = _assess_gap(skill, resume_skill_map, resume_project_techs)
        ranked.append({
            "skill": skill.name,
            "weight": skill.weight,
            "level": level,
            "gap": gap,
            "reason": reason,
            "is_bonus": True,
        })

    # 排序：(有项目经验? 0:1, 缺口? 1:0, weight desc)
    ranked.sort(key=_sort_key)
    for i, item in enumerate(ranked, 1):
        item["priority"] = i

    return ranked


def _sort_key(item: dict) -> tuple:
    has_project = item.get("gap") in ("有项目经验",)
    is_gap = item.get("gap") == "缺口"
    return (0 if has_project else 1, 1 if is_gap else 0, -item["weight"])


def _assess_gap(
    skill: Skill,
    skill_map: dict[str, SkillProficiency],
    project_techs: set[str],
) -> tuple[str, str, str]:
    key = skill.name.lower()
    if key in skill_map:
        rs = skill_map[key]
        if key in project_techs:
            return rs.level, "有项目经验", f"简历中有 {rs.name}({rs.level})，且有项目实践"
        return rs.level, "有技能无项目", f"简历中有 {rs.name}({rs.level})，但无项目实践"
    return "未提及", "缺口", f"JD 要求 {skill.name}，简历中未提及"


def generate_gap_map(jd: JD, resume: Resume) -> dict:
    """生成完整的能力缺口 Map（供 Agent / 报告使用）"""
    ranked = rank_skills(jd, resume)
    return {
        "total_skills": len(ranked),
        "skill_count": len(ranked),
        "strengths": [r for r in ranked if r["gap"] == "有项目经验"],
        "weaknesses": [r for r in ranked if r["gap"] in ("有技能无项目",)],
        "gaps": [r for r in ranked if r["gap"] == "缺口"],
        "bonus": [r for r in ranked if r.get("is_bonus")],
        "ordered_skills": ranked,
    }
