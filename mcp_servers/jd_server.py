from __future__ import annotations

from mcp.server import FastMCP

from models.jd import JD
from agents.jd_parser import JDParserAgent
from models.llm import LLM

app = FastMCP("jd-server")

_agent: JDParserAgent | None = None


def _get_agent() -> JDParserAgent:
    global _agent
    if _agent is None:
        _agent = JDParserAgent(llm=LLM())
    return _agent


@app.tool()
async def parse_jd(text: str) -> dict:
    """解析 JD 文本为结构化 JSON

    Args:
        text: JD 文本内容
    """
    jd: JD = await _get_agent().run(text)
    return jd.model_dump()


if __name__ == "__main__":
    app.run()
