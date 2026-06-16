from __future__ import annotations

import logging
import threading

import typer
from rich.console import Console

# 抑制 httpx/anyio 在事件循环关闭后的清理噪音
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

app = typer.Typer(
    name="interview-hub",
    help="AI 面试官 — 从 JD 解析到面试到反馈的完整闭环",
)
console = Console()


def _run_gradio(host: str, port: int):
    """在独立线程中启动 Gradio（避免 mount_gradio_app 通信 bug）"""
    from web.app import demo
    console.print(f"[dim]   Gradio UI 启动中 → http://{host}:{port}[/dim]")
    demo.launch(
        server_name=host,
        server_port=port,
        share=False,
        show_error=True,
        quiet=True,
    )


@app.command()
def web():
    """启动 Web UI（Gradio 独立端口 :7860 + FastAPI Gateway :8000）"""
    from config import config

    console.print(
        f"[green]🚀 启动服务[/green]"
    )
    console.print(f"[bold]   Gradio UI: http://{config.gateway_host}:{config.gradio_ui_port}[/bold]")
    console.print(f"[dim]   API:       http://{config.gateway_host}:{config.gateway_port}/api/v1/[/dim]")
    console.print(f"[dim]   Health:    http://{config.gateway_host}:{config.gateway_port}/health[/dim]")

    # Gradio 在后台线程独立跑（不用 mount，消除通信层 bug）
    t = threading.Thread(
        target=_run_gradio,
        args=(config.gateway_host, config.gradio_ui_port),
        daemon=True,
    )
    t.start()

    # FastAPI 在主线程跑
    import uvicorn
    uvicorn.run(
        "mcp_servers.gateway:app",
        host=config.gateway_host,
        port=config.gateway_port,
        reload=False,  # 双进程时 reload 会冲突
    )


@app.command()
def gateway():
    """仅启动 MCP Gateway (FastAPI)，不启动 Web UI"""
    import uvicorn
    from config import config

    console.print(
        f"[green]🚀 启动 Gateway → http://{config.gateway_host}:{config.gateway_port}[/green]"
    )
    console.print(f"[dim]   API:    http://{config.gateway_host}:{config.gateway_port}/api/v1/[/dim]")
    console.print(f"[dim]   Health: http://{config.gateway_host}:{config.gateway_port}/health[/dim]")
    uvicorn.run(
        "mcp_servers.gateway:app",
        host=config.gateway_host,
        port=config.gateway_port,
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
