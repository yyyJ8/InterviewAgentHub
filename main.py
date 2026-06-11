from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="interview-hub",
    help="AI 面试官 — 从 JD 解析到面试到反馈的完整闭环",
)
console = Console()


@app.command()
def web():
    """启动 Web UI（Gradio + FastAPI Gateway）"""
    import uvicorn
    from config import config

    console.print(
        f"[green]🚀 启动 Gateway + Web UI → http://{config.gateway_host}:{config.gateway_port}[/green]"
    )
    console.print(f"[dim]   Web UI: http://{config.gateway_host}:{config.gateway_port}/ui[/dim]")
    console.print(f"[dim]   API:    http://{config.gateway_host}:{config.gateway_port}/api/v1/[/dim]")
    console.print(f"[dim]   Health: http://{config.gateway_host}:{config.gateway_port}/health[/dim]")
    uvicorn.run(
        "mcp_servers.gateway:app",
        host=config.gateway_host,
        port=config.gateway_port,
        reload=True,
    )


@app.command()
def gateway():
    """启动 MCP Gateway + Web UI (FastAPI + Gradio)"""
    import uvicorn
    from config import config

    console.print(
        f"[green]🚀 启动 Gateway + Web UI → http://{config.gateway_host}:{config.gateway_port}[/green]"
    )
    console.print(f"[dim]   Web UI: http://{config.gateway_host}:{config.gateway_port}/ui[/dim]")
    console.print(f"[dim]   API:    http://{config.gateway_host}:{config.gateway_port}/api/v1/[/dim]")
    uvicorn.run(
        "mcp_servers.gateway:app",
        host=config.gateway_host,
        port=config.gateway_port,
        reload=True,
    )


@app.command()
def history(
    candidate: str = typer.Option(
        "", "--candidate", "-c", help="候选人姓名"
    ),
):
    """查看历史面试记录"""
    from memory.session_store import SessionStore

    store = SessionStore()
    records = store.find_by_candidate(candidate) if candidate else store.list_all()
    if not records:
        console.print("[yellow]暂无面试记录[/yellow]")
        raise typer.Exit()
    for r in records:
        console.print(
            f"[bold]{r.candidate_name or '匿名'}[/bold] | "
            f"{r.created_at.strftime('%Y-%m-%d %H:%M')} | "
            f"{r.status.value}"
        )
        console.print(f"  面试 ID: {r.interview_id}")
        console.print()


if __name__ == "__main__":
    app()
