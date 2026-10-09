"""Embedding 链路验收工具（SiliconFlow bge-m3）。

检查项：
  1. 账户 / 模型可达性（402 余额不足、401 鉴权失败会明确报出）
  2. VectorStore 是否真的走 API（而不是静默回退本地模型）
  3. 写入 → 语义检索的真实闭环
  4. 真实题库当前维度

用法:
    python tools/verify_embedding.py

返回码 0 = 全部通过；1 = 未通过。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许以 `python tools/verify_embedding.py` 直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb
import requests

from config import config
from memory.vector_store import COLLECTION_QUESTION_BANK, VectorStore


def _check_api_reachable(key: str, base: str, model: str) -> tuple[bool, str]:
    """调用 /embeddings，返回 (是否成功, 说明)。"""
    try:
        r = requests.post(
            f"{base}/embeddings",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"input": ["验收测试"], "model": model},
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001 - 诊断工具，需要报出任何网络异常
        return False, f"{type(e).__name__}: {e}"

    if r.status_code == 200:
        dim = len(r.json()["data"][0]["embedding"])
        return True, f"维度 = {dim}"

    try:
        body = r.json()
        code = body.get("code")
        msg = body.get("message")
    except Exception:  # noqa: BLE001
        code, msg = None, r.text[:200]

    hint = ""
    if r.status_code == 402:
        hint = " → 账户余额不足：请完成实名认证并确认余额非负"
    elif r.status_code == 401:
        hint = " → 鉴权失败：请检查 SILICONFLOW_API_KEY"
    elif r.status_code == 400:
        hint = " → 参数/模型名错误：模型 id 必须写全，如 BAAI/bge-m3"

    return False, f"HTTP {r.status_code} code={code} {msg}{hint}"


def main() -> int:
    key = config.siliconflow_api_key
    base = config.siliconflow_base_url.rstrip("/")
    model = config.embedding_model

    print("=" * 62)
    print("Embedding 链路验收（SiliconFlow bge-m3）")
    print("=" * 62)
    print(f"base_url = {base}")
    print(f"model    = {model}")
    print(f"api_key  = {key[:6]}...{key[-4:]}" if key else "api_key  = (未配置)")

    failures: list[str] = []

    # ── 1. API 可达性 ────────────────────────────────────
    print("\n[1] 调用 /embeddings")
    api_ok = False
    if not key:
        print("    FAIL 未配置 SILICONFLOW_API_KEY")
        failures.append("未配置 API Key")
    else:
        api_ok, detail = _check_api_reachable(key, base, model)
        print(f"    {'OK  ' if api_ok else 'FAIL'} {detail}")
        if not api_ok:
            failures.append("API 不可达")
        elif "1024" not in detail:
            print("    WARN 期望 1024 维")

    # ── 2. VectorStore 实际提供者 ────────────────────────
    print("\n[2] VectorStore 提供者")
    vs = VectorStore()
    health = vs.health_check()
    print(f"    available      = {vs.available}")
    print(f"    embedder_label = {vs.embedder_label}")
    print(f"    health         = {health}")

    using_api = bool(health.get("embedding_ok")) and health.get("dim") == 1024
    print("    判定:", "PASS" if using_api else "FAIL（未走 API 或维度不符）")
    if not using_api:
        failures.append("VectorStore 未真正走 bge-m3 API")

    # ── 3. 写入 → 检索闭环 ───────────────────────────────
    print("\n[3] 真实闭环：写入 → 检索")
    test_col = "ih_verify_tmp"
    client = chromadb.PersistentClient(path=str(config.chroma_persist_dir))
    try:
        client.delete_collection(test_col)
    except Exception:  # noqa: BLE001 - 不存在则忽略
        pass

    closed = False
    try:
        vs.add(
            test_col,
            documents=["请解释 Java 中的自动装箱和拆箱机制", "Python 的装饰器有什么作用"],
            metadatas=[{"skill": "Java"}, {"skill": "Python"}],
            ids=["verify-1", "verify-2"],
        )
        got = client.get_collection(test_col).get(include=["embeddings"])
        dim = len(got["embeddings"][0]) if len(got.get("embeddings", [])) else None
        print(f"    写入 dim = {dim}")

        hits = vs.query(test_col, "Java 装箱拆箱", n_results=2)
        for h in hits:
            print(f"    dist={h.get('distance'):.4f}  {h.get('document')}")

        closed = dim == 1024 and bool(hits) and "Java" in str(hits[0].get("document", ""))
        print("    判定:", "PASS" if closed else "FAIL")
        if not closed:
            failures.append("写入/检索闭环失败")
    finally:
        try:
            client.delete_collection(test_col)
        except Exception:  # noqa: BLE001
            pass

    # ── 4. 真实题库维度 ──────────────────────────────────
    print("\n[4] 真实题库状态")
    try:
        qb = client.get_collection(COLLECTION_QUESTION_BANK)
        got = qb.get(include=["embeddings"])
        qdim = len(got["embeddings"][0]) if len(got.get("embeddings", [])) else None
        print(f"    {COLLECTION_QUESTION_BANK}: count={qb.count()}, dim={qdim}")
        if qdim == 1024:
            print("    → 已是 bge-m3 维度，无需处理")
        elif qdim is not None:
            print("    → 维度不一致：首次检索会自动重建为 1024 维")
    except Exception as e:  # noqa: BLE001
        print("    读取失败:", type(e).__name__, e)

    # ── 总结 ─────────────────────────────────────────────
    ok = api_ok and using_api and closed
    print("\n" + "=" * 62)
    print("总结:", "全部通过，bge-m3 已真正接入" if ok else "未通过：" + "；".join(failures))
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
