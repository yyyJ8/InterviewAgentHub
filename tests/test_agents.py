"""Agent 基类 + JD/简历 Agent 结构测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_prompt_loader():
    from prompts import load_prompt, list_prompts

    names = list_prompts()
    assert "jd_parser" in names
    assert "resume_analyzer" in names

    jd_prompt = load_prompt("jd_parser")
    assert "required_skills" in jd_prompt

    resume_prompt = load_prompt("resume_analyzer")
    assert "projects" in resume_prompt

    print("  [OK] test_prompt_loader")


def test_jd_agent_instantiate():
    from agents.jd_parser import JDParserAgent
    from models.jd import JD

    agent = JDParserAgent()
    assert hasattr(agent, "_system_prompt")
    assert len(agent._system_prompt) > 100
    assert hasattr(agent, "llm")

    print("  [OK] test_jd_agent_instantiate")


def test_resume_agent_instantiate():
    from agents.resume_analyzer import ResumeAnalyzerAgent
    from models.resume import Resume

    agent = ResumeAnalyzerAgent()
    assert hasattr(agent, "_system_prompt")
    assert len(agent._system_prompt) > 100

    print("  [OK] test_resume_agent_instantiate")


def test_parse_response_json():
    import json
    from agents.jd_parser import JDParserAgent
    from models.jd import JD

    agent = JDParserAgent()
    data = {
        "title": "Python工程师",
        "company": "某科技公司",
        "required_skills": [
            {"name": "Python", "weight": 90, "is_bonus": False},
            {"name": "Django", "weight": 75, "is_bonus": False},
        ],
        "bonus_skills": [],
        "experience_years": 3,
        "education": "本科",
        "soft_skills": ["沟通能力"],
        "raw_text": "",
    }
    jd = agent._parse_response(json.dumps(data), JD)
    assert jd.title == "Python工程师"
    assert len(jd.required_skills) == 2
    assert jd.required_skills[0].name == "Python"
    assert jd.required_skills[0].weight == 90

    print("  [OK] test_parse_response_json")


def test_parse_response_code_block():
    import json
    from agents.jd_parser import JDParserAgent
    from models.jd import JD

    agent = JDParserAgent()
    raw = '说明文字\n```json\n{"title": "后端", "required_skills": [], "bonus_skills": [], "soft_skills": [], "raw_text": ""}\n```\n结尾'
    jd = agent._parse_response(raw, JD)
    assert jd.title == "后端"

    print("  [OK] test_parse_response_code_block")


def test_parse_response_braces():
    from agents.jd_parser import JDParserAgent
    from models.jd import JD

    agent = JDParserAgent()
    raw = '一些文本{"title": "前端开发", "required_skills": [], "bonus_skills": [], "soft_skills": [], "raw_text": ""}结尾'
    jd = agent._parse_response(raw, JD)
    assert jd.title == "前端开发"

    print("  [OK] test_parse_response_braces")


def test_mcp_servers_import():
    from mcp_servers.jd_server import app as jd_app
    from mcp_servers.resume_server import app as resume_app

    assert jd_app.name == "jd-server"
    assert resume_app.name == "resume-server"

    print("  [OK] test_mcp_servers_import")


def test_resume_model_dump():
    from models.resume import Resume, SkillProficiency, Project

    resume = Resume(
        name="张三",
        title="后端工程师",
        skills=[SkillProficiency(name="Python", level="expert", years=5)],
        projects=[
            Project(
                name="电商平台",
                role="后端开发",
                description="订单系统",
                tech_stack=["Python", "Django"],
                highlights=["性能优化"],
            )
        ],
        experience_years=5,
    )
    d = resume.model_dump()
    assert d["name"] == "张三"
    assert d["skills"][0]["level"] == "expert"

    print("  [OK] test_resume_model_dump")


if __name__ == "__main__":
    print("Agent 测试\n" + "=" * 20)
    test_prompt_loader()
    test_jd_agent_instantiate()
    test_resume_agent_instantiate()
    test_parse_response_json()
    test_parse_response_code_block()
    test_parse_response_braces()
    test_mcp_servers_import()
    test_resume_model_dump()
    print("\n[OK] 全部通过")
