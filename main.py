from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

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

    console.print(f"[green]🚀 启动服务[/green]")
    console.print(f"[bold]   Gradio UI: http://{config.gateway_host}:{config.gradio_ui_port}[/bold]")
    console.print(f"[dim]   API:       http://{config.gateway_host}:{config.gateway_port}/api/v1/[/dim]")
    console.print(f"[dim]   Health:    http://{config.gateway_host}:{config.gateway_port}/health[/dim]")

    t = threading.Thread(
        target=_run_gradio,
        args=(config.gateway_host, config.gradio_ui_port),
        daemon=True,
    )
    t.start()

    import uvicorn
    uvicorn.run(
        "mcp_servers.gateway:app",
        host=config.gateway_host,
        port=config.gateway_port,
        reload=False,
    )


@app.command()
def gateway():
    """仅启动 MCP Gateway (FastAPI)，不启动 Web UI"""
    import uvicorn
    from config import config

    console.print(f"[green]🚀 启动 Gateway → http://{config.gateway_host}:{config.gateway_port}[/green]")
    console.print(f"[dim]   API:    http://{config.gateway_host}:{config.gateway_port}/api/v1/[/dim]")
    console.print(f"[dim]   Health: http://{config.gateway_host}:{config.gateway_port}/health[/dim]")
    uvicorn.run(
        "mcp_servers.gateway:app",
        host=config.gateway_host,
        port=config.gateway_port,
    )


# ── history 命令辅助 ───────────────────────────────────

def _print_detail(state) -> None:
    """打印一场面试的详细信息。"""
    from models.interview import InterviewStatus

    # 状态映射
    status_map = {
        InterviewStatus.CREATED: "已创建",
        InterviewStatus.IN_PROGRESS: "进行中",
        InterviewStatus.COMPLETED: "已完成",
        InterviewStatus.TERMINATED: "已终止",
    }
    status_text = status_map.get(state.status, state.status.value)

    # 计算总分
    scores = []
    for r in state.rounds:
        j = r.judge
        if j:
            s = j.score if hasattr(j, "score") else j.get("score", 0)
            scores.append(s)
    avg_score = sum(scores) / len(scores) if scores else 0

    # ── 摘要面板 ──
    summary = (
        f"[bold]面试 ID:[/bold] {state.interview_id}\n"
        f"[bold]候选人:[/bold]   {state.candidate_name or '匿名'}\n"
        f"[bold]岗位:[/bold]     {state.jd.title if state.jd else '未知'}\n"
        f"[bold]状态:[/bold]     {status_text}\n"
        f"[bold]时间:[/bold]     {state.created_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"[bold]轮次:[/bold]     {len(state.rounds)} 轮\n"
        f"[bold]均分:[/bold]     {avg_score:.1f}/100"
    )
    console.print(Panel(summary, title="📋 面试详情", border_style="blue"))

    # ── 逐轮详情 ──
    if not state.rounds:
        console.print("[dim]（无面试轮次记录）[/dim]")
        return

    for i, r in enumerate(state.rounds, 1):
        q = r.question
        q_content = q.content if hasattr(q, "content") else q.get("content", "")
        q_skill = q.skill if hasattr(q, "skill") else q.get("skill", "")
        q_diff = (
            q.difficulty.value if hasattr(q.difficulty, "value")
            else q.get("difficulty", "")
        )
        a_text = r.answer if hasattr(r, "answer") else r.get("answer", "")
        j = r.judge
        score = j.score if hasattr(j, "score") else j.get("score", 0) if j else 0
        comment = j.comment if hasattr(j, "comment") else j.get("comment", "") if j else ""

        emoji = "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")
        title = f"第 {i} 轮 — {q_skill} ({q_diff}) — {emoji} {score}/100"

        body = (
            f"[bold]🤖 题目:[/bold]\n{q_content[:300]}\n\n"
            f"[bold]🧑‍💻 回答:[/bold]\n{a_text[:300] if a_text else '(未作答)'}"
        )
        if comment:
            body += f"\n\n[bold]📊 评价:[/bold] {comment}"

        console.print(Panel(body, title=title, border_style="green" if score >= 70 else "yellow"))


def _print_list(records: list) -> None:
    """打印面试历史列表。"""
    if not records:
        console.print("[yellow]暂无面试记录[/yellow]")
        return

    table = Table(title="📋 面试历史记录", show_lines=False)
    table.add_column("面试 ID", style="dim", width=12)
    table.add_column("候选人", style="bold")
    table.add_column("岗位")
    table.add_column("轮次", justify="center")
    table.add_column("均分", justify="center")
    table.add_column("时间")
    table.add_column("状态")

    for r in records:
        scores = []
        for rd in r.rounds:
            j = rd.judge
            s = j.score if hasattr(j, "score") else j.get("score", 0) if j else 0
            scores.append(s)
        avg = f"{sum(scores)/len(scores):.0f}" if scores else "-"

        status_map = {"created": "已创建", "in_progress": "进行中", "completed": "已完成", "terminated": "已终止"}
        status = status_map.get(r.status.value, r.status.value)

        table.add_row(
            r.interview_id[:8],
            r.candidate_name or "匿名",
            (r.jd.title if r.jd else "未知")[:15],
            str(len(r.rounds)),
            avg,
            r.created_at.strftime("%m-%d %H:%M"),
            status,
        )
    console.print(table)


def _print_chroma_records(collection: str) -> None:
    """打印 ChromaDB 集合中的记录。"""
    from memory.vector_store import VectorStore

    vs = VectorStore()
    if not vs.available:
        console.print("[yellow]ChromaDB 不可用[/yellow]")
        return

    records = vs.list_all(collection)
    if not records:
        console.print(f"[yellow]{collection} 中暂无记录[/yellow]")
        return

    console.print(f"\n[bold]{collection}[/bold]: {len(records)} 条\n")

    if collection == "ih_interview_sessions":
        for r in records:
            meta = r.get("metadata", {})
            console.print(
                f"  [bold]{meta.get('candidate_name', '?')}[/bold] | "
                f"{meta.get('jd_title', '?')} | "
                f"{meta.get('round_count', 0)} 轮 | "
                f"均分 {meta.get('total_score', 0):.0f} | "
                f"ID: {meta.get('interview_id', '?')[:8]}"
            )
    elif collection == "ih_question_bank":
        for r in records:
            meta = r.get("metadata", {})
            doc = r.get("document", "")[:100]
            console.print(
                f"  [{meta.get('skill', '?')}] "
                f"{meta.get('difficulty', '?')} | "
                f"{doc}..."
            )
    console.print()


# ── CLI 命令 ───────────────────────────────────────────

@app.command()
def history(
    candidate: str = typer.Option("", "--candidate", "-c", help="按候选人姓名过滤"),
    interview_id: str = typer.Option("", "--id", "-i", help="查看指定面试详情"),
    last: bool = typer.Option(False, "--last", "-l", help="查看最新一场面试详情"),
    chroma: bool = typer.Option(False, "--chroma", help="查询 ChromaDB 长期记忆"),
):
    """查看历史面试记录。

    默认列出 SessionStore 中所有记录。
    使用 -i <id> 查看某场详情，-l 查看最新一场。
    """
    from memory.session_store import SessionStore

    # ── ChromaDB 模式 ──
    if chroma:
        _print_chroma_records("ih_interview_sessions")
        _print_chroma_records("ih_question_bank")
        return

    store = SessionStore()

    # ── 按 ID 查看详情 ──
    if interview_id:
        state = store.load(interview_id)
        if state is None:
            # 尝试模糊匹配（前缀）
            for f in store._dir.glob("*.json"):
                if f.stem.startswith(interview_id):
                    state = store.load(f.stem)
                    break
        if state is None:
            console.print(f"[yellow]未找到面试 ID: {interview_id}[/yellow]")
            raise typer.Exit()
        _print_detail(state)
        return

    # ── 最新一场 ──
    if last:
        records = store.list_all()
        if not records:
            console.print("[yellow]暂无面试记录[/yellow]")
            raise typer.Exit()
        _print_detail(records[0])  # list_all 按时间倒序
        return

    # ── 列表模式 ──
    records = store.find_by_candidate(candidate) if candidate else store.list_all()
    _print_list(records)


@app.command()
def clean_memory(
    sessions: bool = typer.Option(True, "--sessions", help="清空 SessionStore (JSON 文件)"),
    chroma: bool = typer.Option(True, "--chroma", help="清空 ChromaDB 向量数据"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
):
    """清空长期记忆数据（SessionStore + ChromaDB）。"""
    if not yes:
        targets = []
        if sessions:
            targets.append("SessionStore (data/sessions/)")
        if chroma:
            targets.append("ChromaDB (data/chroma/)")
        if not targets:
            console.print("[yellow]未指定任何清理目标[/yellow]")
            return
        console.print(f"[red]即将清空:[/red] {', '.join(targets)}")
        confirm = typer.confirm("确认清空？此操作不可撤销")
        if not confirm:
            console.print("[dim]已取消[/dim]")
            return

    # 清空 SessionStore
    if sessions:
        from config import config
        session_dir = config.session_dir
        count = 0
        for f in session_dir.glob("*.json"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
        console.print(f"[green]✓[/green] SessionStore 已清空 ({count} 个文件)")

    # 清空 ChromaDB
    if chroma:
        from memory.vector_store import VectorStore
        vs = VectorStore()
        if vs.available:
            for col_name in ["ih_question_bank", "ih_interview_sessions"]:
                records = vs.list_all(col_name)
                for r in records:
                    vs.delete(col_name, r["id"])
            console.print(f"[green]✓[/green] ChromaDB 已清空")
        else:
            console.print("[yellow]ChromaDB 不可用，跳过[/yellow]")


if __name__ == "__main__":
    app()
