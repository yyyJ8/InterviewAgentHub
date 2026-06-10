from __future__ import annotations

from mcp.server import FastMCP

from models.resume import Resume
from agents.resume_analyzer import ResumeAnalyzerAgent
from models.llm import LLM

app = FastMCP("resume-server")

_agent: ResumeAnalyzerAgent | None = None


def _get_agent() -> ResumeAnalyzerAgent:
    global _agent
    if _agent is None:
        _agent = ResumeAnalyzerAgent(llm=LLM())
    return _agent


@app.tool()
async def parse_resume(text: str) -> dict:
    """解析简历文本为结构化 JSON

    Args:
        text: 简历文本内容
    """
    resume: Resume = await _get_agent().run(text)
    return resume.model_dump()


if __name__ == "__main__":
    app.run()
