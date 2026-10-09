"""集成测试 — Gateway 在 dev / prod 两种环境下的真实行为。

覆盖：
  - lifespan 正常注册 3 个 MCP Server 的 6 个工具
  - dev 环境放行、prod 环境强制 Bearer Token（401 路径）
  - 空 answer 被拒绝为 400
  - /health 报告工具与熔断器状态

注意：MCP 的 StreamableHTTPSessionManager.run() **每个实例只能调用一次**，
因此这里用 module 级 fixture 共享同一个 TestClient，而不是每个测试各建一个。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from config import config  # noqa: E402
from mcp_servers.gateway import create_app  # noqa: E402

EXPECTED_TOOLS = {
    "parse_jd", "parse_resume",
    "generate_questions", "search_seed_bank",
    "add_to_seed_bank", "get_seed_bank_stats",
}


@pytest.fixture(scope="module")
def client():
    """共享单个 app 实例与其 lifespan（session manager 只能运行一次）。"""
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
def no_auth():
    """临时关闭鉴权（dev 行为），结束后恢复。"""
    original = config.gateway_require_auth
    config.gateway_require_auth = False
    try:
        yield
    finally:
        config.gateway_require_auth = original


def test_lifespan_registers_tools_and_health(client):
    """lifespan 启动后应注册 6 个工具，/health 无需鉴权。"""
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert EXPECTED_TOOLS.issubset(set(body["tools"])), (
        f"缺少工具: {EXPECTED_TOOLS - set(body['tools'])}"
    )
    assert isinstance(body["breakers"], list)
    print(f"  [OK] /health 已注册 {len(body['tools'])} 个工具")


def test_dev_mode_allows_request_without_token(client, no_auth):
    """dev 环境（默认）不校验 Token。"""
    r = client.post("/api/v1/interview", json={})
    # 缺少 jd_path/resume_path → 400（说明请求已通过鉴权）
    assert r.status_code == 400, f"dev 环境不应 401，实际 {r.status_code}"
    print("  [OK] dev 环境无需 Token")


def test_prod_mode_requires_bearer_token(client):
    """prod 环境：无 Token → 401，错误 Token → 401，正确 Token → 通过鉴权。"""
    original = config.gateway_require_auth
    config.gateway_require_auth = True
    try:
        r = client.post("/api/v1/interview", json={})
        assert r.status_code == 401, f"无 Token 应 401，实际 {r.status_code}"

        r = client.post(
            "/api/v1/interview", json={},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401, f"错误 Token 应 401，实际 {r.status_code}"

        r = client.post(
            "/api/v1/interview", json={},
            headers={"Authorization": f"Bearer {config.gateway_api_key}"},
        )
        assert r.status_code == 400, (
            f"正确 Token 应通过鉴权并因缺少路径返回 400，实际 {r.status_code}"
        )
    finally:
        config.gateway_require_auth = original
    print("  [OK] prod 环境强制 Bearer Token")


def test_judge_rejects_empty_answer(client, no_auth):
    """空回答必须被拒绝（修复前 `if answer is None` 恒假）。"""
    for payload in ({"answer": ""}, {"answer": "   "}, {}):
        r = client.post("/api/v1/interview/nonexistent-id/judge", json=payload)
        assert r.status_code == 400, (
            f"payload={payload} 应 400，实际 {r.status_code}: {r.text[:120]}"
        )
    print("  [OK] 空回答被拒绝为 400")


def test_unknown_session_returns_404(client, no_auth):
    """不存在的会话返回 404 而非 500。"""
    r = client.get("/api/v1/interview/does-not-exist")
    assert r.status_code == 404, f"应 404，实际 {r.status_code}"
    print("  [OK] 未知会话返回 404")
