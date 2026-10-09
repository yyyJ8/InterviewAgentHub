"""MCP 协议集成测试 — 验证 Gateway 暴露真正可用的 MCP Streamable HTTP 端点。

覆盖：
  - initialize 握手（协议版本 / capabilities / serverInfo / session id）
  - notifications/initialized（通知，应 202）
  - tools/list 返回全部 6 个工具且带真实 inputSchema
  - tools/call 真正调用工具并返回 content 数组
  - 缺少 session id 时被正确拒绝
  - 与运行时直调端点 /mcp/{tool_name} 共存不冲突
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.testclient import TestClient  # noqa: E402

from mcp_servers.gateway import create_app  # noqa: E402

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

EXPECTED_TOOLS = {
    "parse_jd",
    "parse_resume",
    "generate_questions",
    "search_seed_bank",
    "add_to_seed_bank",
    "get_seed_bank_stats",
}


@pytest.fixture(scope="module")
def client():
    """全局共享一个 app：session_manager.run() 每实例只能调用一次。

    base_url 用 127.0.0.1 而非默认的 testserver：MCP 的 DNS-rebinding 防护会校验
    Host 头，而白名单里是 localhost / 127.0.0.1（这也更贴近真实客户端场景）。
    """
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _rpc(client, method, params=None, req_id=1, session_id=None):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    headers = dict(MCP_HEADERS)
    if session_id:
        headers["mcp-session-id"] = session_id
    return client.post("/mcp/", json=payload, headers=headers)


def _parse_body(text: str) -> dict:
    """兼容 SSE（data: 行）与纯 JSON 两种响应体。"""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


@pytest.fixture(scope="module")
def session_id(client) -> str:
    r = _rpc(client, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    })
    assert r.status_code == 200, f"initialize 失败: {r.status_code} {r.text[:300]}"
    sid = r.headers.get("mcp-session-id")
    assert sid, "initialize 未返回 mcp-session-id"
    # 通知客户端已就绪（无 id → 通知，服务端不回响应体）
    _rpc(client, "notifications/initialized", req_id=None, session_id=sid)
    return sid


def test_initialize_handshake(client, session_id):
    """握手应返回协议版本、能力与 serverInfo。"""
    assert session_id
    r = _rpc(client, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    })
    body = _parse_body(r.text)
    result = body["result"]
    assert result["protocolVersion"]
    assert "tools" in result["capabilities"], "应声明 tools 能力"
    assert result["serverInfo"]["name"]
    print(f"  [OK] initialize: {result['serverInfo']} proto={result['protocolVersion']}")


def test_tools_list_returns_all_tools(client, session_id):
    """tools/list 必须返回聚合后的全部 6 个工具，且带可用 inputSchema。"""
    r = _rpc(client, "tools/list", {}, req_id=2, session_id=session_id)
    assert r.status_code == 200, r.text[:300]
    tools = _parse_body(r.text)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == EXPECTED_TOOLS, f"工具集合不符: 缺 {EXPECTED_TOOLS - names}, 多 {names - EXPECTED_TOOLS}"

    for t in tools:
        schema = t.get("inputSchema")
        assert isinstance(schema, dict), f"{t['name']} 缺少 inputSchema"
        assert schema.get("type") == "object", f"{t['name']} 的 inputSchema 不是 object"
        assert isinstance(t.get("description"), str) and t["description"].strip(), (
            f"{t['name']} 缺少 description"
        )
    print(f"  [OK] tools/list 返回 {len(tools)} 个工具，inputSchema 齐全")


def test_tools_call_invokes_real_tool(client, session_id):
    """tools/call 应真正调用工具并返回 content 文本数组。"""
    r = _rpc(client, "tools/call",
             {"name": "get_seed_bank_stats", "arguments": {}},
             req_id=3, session_id=session_id)
    assert r.status_code == 200, r.text[:300]
    result = _parse_body(r.text)["result"]
    assert "content" in result, f"返回体缺少 content: {result}"
    assert isinstance(result["content"], list) and result["content"]
    text = result["content"][0].get("text", "")
    assert "total" in text, f"工具返回内容不符合预期: {text[:200]}"
    assert result.get("isError") is False
    print(f"  [OK] tools/call get_seed_bank_stats → {text[:80]}")


def test_tools_call_unknown_tool_reports_error(client, session_id):
    """调用不存在的工具应返回协议层错误，而不是 500。"""
    r = _rpc(client, "tools/call",
             {"name": "no_such_tool", "arguments": {}},
             req_id=4, session_id=session_id)
    assert r.status_code == 200, f"不应是 HTTP 错误: {r.status_code} {r.text[:200]}"
    body = _parse_body(r.text)
    payload = body.get("result") or body.get("error") or {}
    assert payload, f"应返回错误信息: {body}"
    print(f"  [OK] 未知工具被正确拒绝: {json.dumps(payload, ensure_ascii=False)[:80]}")


def test_missing_session_id_is_rejected(client):
    """缺少 mcp-session-id 的后续请求应被拒绝（400/404/421），而不是被当成新会话。"""
    r = _rpc(client, "tools/list", {}, req_id=5)
    assert r.status_code != 200, "缺少 session id 不应成功"
    assert r.status_code in (400, 404, 421), f"应被拒绝，实际 {r.status_code}"
    print(f"  [OK] 缺少 session id 被拒绝: HTTP {r.status_code}")


def test_runtime_dispatch_endpoint_still_works(client, session_id):
    """运行时直调端点 /mcp/{tool_name} 与协议端点共存。"""
    r = client.post("/mcp/get_seed_bank_stats", json={})
    assert r.status_code == 200, f"直调端点失效: {r.status_code} {r.text[:200]}"
    print(f"  [OK] 运行时直调 /mcp/get_seed_bank_stats → {r.text[:60]}")


def test_health_reports_tools(client):
    """/health 应列出注册的工具（不依赖 MCP 会话）。"""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert EXPECTED_TOOLS.issubset(set(body["tools"]))
    print(f"  [OK] /health 报告 {len(body['tools'])} 个工具")


if __name__ == "__main__":
    print("MCP 协议集成测试\n" + "=" * 30)
    app = create_app()
    with TestClient(app) as c:
        r = _rpc(c, "initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "manual", "version": "1"},
        })
        assert r.status_code == 200, r.text[:300]
        sid = r.headers["mcp-session-id"]
        print(f"  [OK] initialize → session {sid}")
        _rpc(c, "notifications/initialized", req_id=None, session_id=sid)
        r = _rpc(c, "tools/list", {}, req_id=2, session_id=sid)
        tools = _parse_body(r.text)["result"]["tools"]
        print(f"  [OK] tools/list → {[t['name'] for t in tools]}")
        r = _rpc(c, "tools/call", {"name": "get_seed_bank_stats", "arguments": {}},
                 req_id=3, session_id=sid)
        print(f"  [OK] tools/call → {_parse_body(r.text)['result']['content'][0]['text'][:80]}")
    print("\n[OK] 手工验证通过")
