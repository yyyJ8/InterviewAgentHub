"""集成测试 — Gateway 在 dev / prod 两种环境下的真实行为。

覆盖：
  - lifespan 正常注册 3 个 MCP Server（替代已弃用的 on_event）
  - dev 环境放行、prod 环境强制 Bearer Token（401 路径）
  - 空 answer 被拒绝为 400
  - /health 报告工具与熔断器状态
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from config import config  # noqa: E402
from mcp_servers.gateway import app  # noqa: E402


def test_lifespan_registers_tools_and_health():
    """lifespan 启动后应注册 3 个 Server 的 6 个工具，/health 无需鉴权。"""
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        tools = set(body["tools"])
        expected = {
            "parse_jd", "parse_resume",
            "generate_questions", "search_seed_bank",
            "add_to_seed_bank", "get_seed_bank_stats",
        }
        assert expected.issubset(tools), f"缺少工具: {expected - tools}"
        assert isinstance(body["breakers"], list)
    print(f"  [OK] test_lifespan_registers_tools_and_health (已注册 {len(expected)} 个工具)")


def test_dev_mode_allows_request_without_token():
    """dev 环境（默认）不校验 Token。"""
    original = config.gateway_require_auth
    config.gateway_require_auth = False
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/interview", json={})
            # 缺少 jd_path/resume_path → 400（说明请求已通过鉴权中间件）
            assert r.status_code == 400, f"dev 环境不应 401，实际 {r.status_code}"
    finally:
        config.gateway_require_auth = original
    print("  [OK] test_dev_mode_allows_request_without_token")


def test_prod_mode_requires_bearer_token():
    """prod 环境：无 Token → 401，错误 Token → 401，正确 Token → 通过鉴权。"""
    original = config.gateway_require_auth
    config.gateway_require_auth = True
    try:
        with TestClient(app) as client:
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
    print("  [OK] test_prod_mode_requires_bearer_token")


def test_judge_rejects_empty_answer():
    """空回答必须被拒绝（修复前 `if answer is None` 恒假）。"""
    original = config.gateway_require_auth
    config.gateway_require_auth = False
    try:
        with TestClient(app) as client:
            for payload in ({"answer": ""}, {"answer": "   "}, {}):
                r = client.post("/api/v1/interview/nonexistent-id/judge", json=payload)
                assert r.status_code == 400, (
                    f"payload={payload} 应 400，实际 {r.status_code}: {r.text[:120]}"
                )
    finally:
        config.gateway_require_auth = original
    print("  [OK] test_judge_rejects_empty_answer")


def test_unknown_session_returns_404():
    """不存在的会话返回 404 而非 500。"""
    original = config.gateway_require_auth
    config.gateway_require_auth = False
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/interview/does-not-exist")
            assert r.status_code == 404, f"应 404，实际 {r.status_code}"
    finally:
        config.gateway_require_auth = original
    print("  [OK] test_unknown_session_returns_404")


if __name__ == "__main__":
    print("Gateway 集成测试\n" + "=" * 30)
    test_lifespan_registers_tools_and_health()
    test_dev_mode_allows_request_without_token()
    test_prod_mode_requires_bearer_token()
    test_judge_rejects_empty_answer()
    test_unknown_session_returns_404()
    print("\n[OK] 全部集成测试通过")
