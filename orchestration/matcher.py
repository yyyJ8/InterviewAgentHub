from __future__ import annotations

from models.jd import JD, Skill
from models.resume import Resume, SkillProficiency

# ── 工具/基础设施关键词（这些技能排在核心开发技能之后）──
_TOOL_KEYWORDS: set[str] = {
    "docker", "kubernetes", "k8s",
    "git", "svn",
    "linux", "unix", "shell",
    "jenkins", "ci/cd", "github actions", "gitlab ci",
    "nginx", "apache", "tomcat",
    "maven", "gradle", "npm", "pip", "yarn",
    "idea", "intellij", "eclipse", "vscode", "visual studio",
    "postman", "swagger",
    "jira", "confluence",
}


def _is_tool_skill(name: str) -> bool:
    """判断是否为工具链/基础设施技能（非核心开发技能）。"""
    return name.strip().lower() in _TOOL_KEYWORDS


def rank_skills(jd: JD, resume: Resume) -> list[dict]:
    """将 JD 技能按面试考察优先级排序

    排序策略：
    1. 核心开发技能 + 有项目经验（候选人能展开聊）
    2. 核心开发技能 + 有技能无项目
    3. 核心开发技能 + 缺口
    4. 工具/基础设施技能（Docker/Git/Linux 等，降低优先级）
    5. 加分技能排在最后

    返回列表，每项包含：
        skill, weight, level, gap, reason, priority[, is_bonus, is_tool]
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
            "is_tool": _is_tool_skill(skill.name),
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
            "is_tool": _is_tool_skill(skill.name),
        })

    # 排序：核心优先于工具，有项目经验优先于缺口，权重降序
    ranked.sort(key=_sort_key)
    for i, item in enumerate(ranked, 1):
        item["priority"] = i

    return ranked


def _sort_key(item: dict) -> tuple:
    """排序键：(is_tool, is_gap_or_bonus, has_project, -weight)

    is_tool=1 的工具技能排在 is_tool=0 的核心技能之后。
    """
    is_tool = 1 if item.get("is_tool") else 0
    is_bonus = 1 if item.get("is_bonus") else 0
    has_project = 0 if item.get("gap") in ("有项目经验",) else 1
    is_gap = 1 if item.get("gap") == "缺口" else 0
    return (is_tool, is_bonus, is_gap, has_project, -item["weight"])


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
