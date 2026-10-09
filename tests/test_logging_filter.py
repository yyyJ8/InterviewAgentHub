"""日志噪音过滤器测试。

关键：这条噪音来自**两个不同 logger**，必须同时覆盖：
  ① mcp.server.streamable_http —— 库自己的 logger.exception
  ② uvicorn.error             —— 二次异常冒泡后由 uvicorn 打印
只挂一个只能拦一半（这是此前的实际缺陷）。
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_servers.gateway import (  # noqa: E402
    _NOISE_FILTER_LOGGERS,
    _SuppressMcpTransportNoise,
    install_mcp_noise_filter,
)

# 真实噪音记录：两段 traceback 合并后的文本特征
NOISE_TEXT = (
    "Error handling POST request\n"
    "Traceback (most recent call last):\n"
    "  File \".../mcp/server/streamable_http.py\", line 520, in _handle_post_request\n"
    "    await writer.send(session_message)\n"
    "anyio.ClosedResourceError\n"
    "During handling of the above exception, another exception occurred:\n"
    "  File \".../mcp/server/streamable_http.py\", line 656, in _handle_post_request\n"
    "    await writer.send(Exception(err))\n"
    "anyio.ClosedResourceError"
)


def _record(msg: str, name: str = "mcp.server.streamable_http") -> logging.LogRecord:
    return logging.LogRecord(name, logging.ERROR, __file__, 1, msg, None, None)


def test_install_covers_both_loggers():
    """过滤器必须同时挂在 mcp 库与 uvicorn 两个 logger 上。"""
    install_mcp_noise_filter()
    for name in _NOISE_FILTER_LOGGERS:
        filters = [
            f for f in logging.getLogger(name).filters
            if isinstance(f, _SuppressMcpTransportNoise)
        ]
        assert filters, f"{name} 上未安装噪音过滤器"


def test_install_is_idempotent():
    install_mcp_noise_filter()
    install_mcp_noise_filter()
    for name in _NOISE_FILTER_LOGGERS:
        filters = [
            f for f in logging.getLogger(name).filters
            if isinstance(f, _SuppressMcpTransportNoise)
        ]
        assert len(filters) == 1, f"{name} 上重复安装（{len(filters)} 个）"


def test_filter_drops_known_mcp_noise():
    f = _SuppressMcpTransportNoise()
    assert f.filter(_record(NOISE_TEXT)) is False


def test_filter_also_drops_uvicorn_copy():
    """uvicorn 那份（二次异常）同样应被过滤。"""
    f = _SuppressMcpTransportNoise()
    assert f.filter(_record(NOISE_TEXT, name="uvicorn.error")) is False


def test_filter_keeps_other_asgi_errors():
    """其他 ASGI 异常不能被误杀。"""
    f = _SuppressMcpTransportNoise()
    other = _record(
        "Exception in ASGI application\nValueError: 数据库连接串配置错误", name="uvicorn.error"
    )
    assert f.filter(other) is True


def test_filter_keeps_closed_resource_without_signature():
    """只出现 ClosedResourceError 但没有 MCP 签名时，不应过滤（可能是别处的问题）。"""
    f = _SuppressMcpTransportNoise()
    assert f.filter(_record("anyio.ClosedResourceError", name="uvicorn.error")) is True


def test_filter_keeps_normal_logs():
    f = _SuppressMcpTransportNoise()
    assert f.filter(_record("Gateway 启动完成", name="gateway")) is True


def test_noise_record_is_actually_suppressed_end_to_end():
    """端到端：向 mcp logger 发一条噪音，经 handler 链后不应被记录。"""
    install_mcp_noise_filter()
    captured: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    mcp_logger = logging.getLogger("mcp.server.streamable_http")
    old_level = mcp_logger.level
    mcp_logger.setLevel(logging.ERROR)
    handler = Capture()
    mcp_logger.addHandler(handler)
    try:
        mcp_logger.error(NOISE_TEXT)
        mcp_logger.error("一条应该保留的真实错误")
    finally:
        mcp_logger.removeHandler(handler)
        mcp_logger.setLevel(old_level)

    assert not any("Error handling POST request" in m for m in captured), (
        f"噪音未被过滤: {captured}"
    )
    assert any("应该保留" in m for m in captured), "真实错误被误杀"
