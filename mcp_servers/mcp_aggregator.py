"""把多个 FastMCP Server 的工具聚合到一个对外暴露的 FastMCP 实例。

为什么需要这个模块
------------------
项目里有 3 个独立的 FastMCP Server（JD / 简历 / 题库），每个自带 `@mcp.tool()`。
但 Gateway 作为统一入口，应该只对外暴露**一个** MCP 端点，让客户端一次
`initialize` 就能看到全部工具。本模块负责把三个 Server 的工具汇入一个
聚合实例，并配置好 Streamable HTTP transport。

关键实现约束（均已实测验证）
---------------------------
1. `FastMCP.streamable_http_app()` 返回的 Starlette 子应用，其 routes 里用的是
   `settings.streamable_http_path`。因此**挂载前缀会被拼接**：
   mount("/mcp") + 内部 "/mcp" → 实际端点是 /mcp/mcp。
   为了让对外端点正好是 `/mcp/`，必须把内部路径设为 "/"。
2. Starlette 的 `Mount` **不会执行子应用的 lifespan**，而 session manager 必须
   在 lifespan 中运行。所以调用方要显式 `async with session_manager.run()`；
   本模块提供 `session_lifespan()` 封装这一点。
3. `session_manager.run()` **每个实例只能调用一次**，重复进入会抛
   RuntimeError。测试中请用 `build_aggregate_mcp()` 新建实例。
4. `TransportSecuritySettings.allowed_hosts` 默认空列表 = 拒绝所有 Host
   （返回 421）。且**不支持 `"*"` 通配**，只支持精确匹配或 `"host:*"` 形式。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger("mcp.aggregator")

# 对外端点：mount 在 /mcp，内部路径设为 "/" 后实际端点为 <prefix>/
MCP_MOUNT_PREFIX = "/mcp"


def _load_source_servers() -> list[tuple[str, FastMCP]]:
    """惰性导入三个源 Server（避免模块导入期就加载全部依赖）。"""
    from mcp_servers.jd_server import app as jd_server
    from mcp_servers.question_bank_server import app as qb_server
    from mcp_servers.resume_server import app as resume_server

    return [
        ("jd-server", jd_server),
        ("resume-server", resume_server),
        ("question-bank-server", qb_server),
    ]


def build_transport_security(extra_hosts: Iterable[str] | None = None) -> TransportSecuritySettings:
    """构造传输层安全设置。

    DNS-rebinding 防护保持开启（这是官方推荐），但必须显式列出允许的 Host，
    否则所有请求都会被 421 拒绝。注意不支持 "*"，只支持精确值或 "host:*"。
    """
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    for h in extra_hosts or ():
        if h and h not in hosts:
            hosts.append(h)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
    )


def build_aggregate_mcp(
    name: str = "interview-hub",
    extra_hosts: Iterable[str] | None = None,
) -> FastMCP:
    """构造聚合 FastMCP 实例，汇入全部源 Server 的工具。

    Args:
        name: 对客户端展示的 server 名称（出现在 initialize 的 serverInfo 中）
        extra_hosts: 额外允许的 Host 值

    Returns:
        已注册全部工具、并配置好 Streamable HTTP transport 的 FastMCP 实例。
    """
    aggregate = FastMCP(name)

    # 内部路径设为 "/"，配合 mount("/mcp") 使对外端点为 /mcp/
    aggregate.settings.streamable_http_path = "/"
    aggregate.settings.transport_security = build_transport_security(extra_hosts)

    total = 0
    for server_label, server in _load_source_servers():
        tools = server._tool_manager._tools
        for tool_name, tool in tools.items():
            # structured_output=False：统一用 content 文本数组返回。
            # 源工具的返回注解不完全准确（如 parse_jd 注解 dict 实际返回 JSON 字符串），
            # 交给自动探测可能失败，显式关闭最稳。
            aggregate.add_tool(
                tool.fn,
                name=tool_name,
                title=tool.title,
                description=tool.description,
                structured_output=False,
            )
            logger.debug("聚合工具 %s ← %s", tool_name, server_label)
            total += 1
        logger.info("已聚合 %s 的 %d 个工具", server_label, len(tools))

    logger.info("MCP 聚合完成：共 %d 个工具，对外端点 %s/", total, MCP_MOUNT_PREFIX)
    return aggregate


@asynccontextmanager
async def session_lifespan(mcp: FastMCP) -> AsyncIterator[None]:
    """运行 FastMCP 的 Streamable HTTP session manager。

    必须由外层 ASGI 应用的 lifespan 进入，因为 Starlette 的 Mount 不会执行
    子应用自身的 lifespan。
    """
    async with mcp.session_manager.run():
        yield
