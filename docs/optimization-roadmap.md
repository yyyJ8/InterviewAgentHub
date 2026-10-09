# AI 面试官 — 优化升级方案

> 从 Demo 到半生产级的完整演进路线图  
> 2026-06-20 起草（当时版本 v0.4.0）| 当前版本 v0.5.0（Phase 5 已落地）
>
> **阅读提示**：本文写于 Phase 5 之前。第一章「现状诊断」与第二章各节描述的是**当时的待优化状态**，
> 其中 2.1–2.4 已在 Phase 5 落地（详见 [blog-ai-interviewer.md](blog-ai-interviewer.md) 的 Phase 5 改动日志）。
> 第三章及以后的内容属于**未来设想**，文中的示例代码均为方案草图，不代表当前实现；
> 当前实现请以 `config.py`、`models/llm.py`、`memory/vector_store.py` 为准。

---

## 目录

- [一、现状诊断](#一现状诊断)
- [二、近期优化（1-2 周，高收益低风险）](#二近期优化12-周高收益低风险)
- [三、中期重构（1-2 月，架构升级）](#三中期重构12-月架构升级)
- [四、远期畅想（3-6 月，产品化）](#四远期畅想3-6-月产品化)
- [五、实施路线图](#五实施路线图)

---

## 一、现状诊断

> 本章记录的是 **Phase 5 之前（v0.4.0）** 的诊断结论，表中问题多数已在 Phase 5 修复，保留原文以便对照演进过程。

### 1.1 架构总览

```
┌──────────────────────────────────────────────────┐
│                   main.py (typer CLI)              │
│          gradio_thread      uvicorn (主线程)        │
│              │                    │                 │
│    ┌─────────┴────────┐  ┌───────┴────────┐       │
│    │  web/app.py      │  │  gateway.py    │       │
│    │  (Gradio UI)     │  │  (FastAPI)      │       │
│    │  ⚠ 重复状态机    │  │  ✅ 用supervisor │       │
│    └──────────────────┘  └───────┬────────┘       │
│                                  │                 │
│                   ┌──────────────┴──────────┐     │
│                   │  orchestration/          │     │
│                   │  supervisor.py           │     │
│                   │  (LangGraph 状态机)       │     │
│                   └──────────────┬──────────┘     │
│                                  │                 │
│      ┌───────────────┬───────────┼───────────┐    │
│      │               │           │           │     │
│  agents/         models/     memory/     tools/   │
│  (LLM 调用)      (Pydantic)  (存储层)    (解析)    │
└──────────────────────────────────────────────────┘
```

### 1.2 核心问题清单

| # | 问题 | 严重程度 | 所在文件 |
|---|------|----------|----------|
| 1 | `_async()` 每次创建新事件循环 | 🔴 高 | [web/app.py:32](web/app.py#L32) |
| 2 | 面试状态机重复实现两套 | 🔴 高 | web/app.py + supervisor.py |
| 3 | 流式输出已实现但 UI 未接入 | 🟡 中 | interviewer.py → web/app.py |
| 4 | 技能匹配是纯字符串规则 | 🟡 中 | [orchestration/matcher.py](orchestration/matcher.py) |
| 5 | Prompt 变量无校验，运行时才报错 | 🟡 中 | [prompts/__init__.py](prompts/__init__.py) |
| 6 | LLM 解析结果无缓存 | 🟢 低 | agents/jd_parser.py 等 |
| 7 | SessionStore JSON 文件无并发保护 | 🟢 低 | [memory/session_store.py](memory/session_store.py) |
| 8 | 无环境区分（dev/prod） | 🟢 低 | [config.py](config.py) |
| 9 | LLM provider 硬编码 OpenAI 协议 | 🟢 低 | [models/llm.py](models/llm.py) |
| 10 | 测试覆盖不足 | 🟢 低 | tests/ |

---

## 二、近期优化（1-2 周，高收益低风险）— ✅ 已全部完成

> 本章原列的 4 项已在 **Phase 5** 全部落地并合入 `master`，原方案草图已删除，
> 避免与当前实现混淆。逐项对照如下：

| 项 | 原目标 | 落地情况 |
|----|--------|---------|
| 2.1 | 消除 `_async()` 反模式 | ✅ 全链路原生 `async/await`，不再用事件循环包装 |
| 2.2 | 统一面试状态机 | ✅ Gradio 与 Gateway 共用 `orchestration/supervisor.py` 的 StateGraph |
| 2.3 | Web UI 流式输出 | ✅ `GET /api/v1/interview/{id}/stream-question`（SSE 逐 token 出题） |
| 2.4 | 其他顺手修复 | ✅ Prompt 模板变量校验、dev/prod 环境区分、Embedding 接入 |

补充：Phase 5 之后还完成了两项本章未列的工作 ——

- **MCP 协议端点**：用 FastMCP 原生 Streamable HTTP transport 暴露 `POST /mcp/`
  （聚合 3 个 Server 的 6 个工具），见 `mcp_servers/mcp_aggregator.py`
- **Embedding 迁移**：本地 `bge-base-zh-v1.5`（768 维）→ SiliconFlow API 的
  `BAAI/bge-m3`（1024 维，免费），本地模型降级为兜底

---

## 三、中期重构（1-2 月，架构升级）

### 3.1 语义技能匹配（利用 bge-m3 Embedding）

**现状**：`matcher.py` 用纯字符串小写匹配技能名：

```python
# 当前逻辑
resume_skill_map = {s.name.lower(): s for s in resume.skills}
if key in skill_map:  # "React.js" vs "React" → False ❌
```

**方案**（设想）：基于 `BAAI/bge-m3` embedding（SiliconFlow API，1024 维）的余弦相似度匹配，直接复用 `VectorStore` 的 embedding 能力。

```
JD 技能列表                简历技能列表
┌──────────┐              ┌──────────┐
│ Kubernetes│──┐        ┌──│ K8s      │
│ React.js  │──┤ cos    ├──│ React    │
│ 微服务     │──┤ sim >  ├──│ Spring   │
│ CI/CD     │──┘  0.75? └──│ Jenkins  │
└──────────┘              └──────────┘
         匹配成功            匹配失败
         (候选)              (缺口)
```

```python
# matcher.py 改后
from memory.vector_store import VectorStore

def _semantic_match(jd_skills: list[Skill], resume_skills: list[Skill]) -> dict:
    """用 embedding 做模糊匹配"""
    vs = VectorStore()
    if not vs.available:
        return _fallback_string_match(jd_skills, resume_skills)  # 降级

    jd_emb = vs.embed_batch([s.name for s in jd_skills])
    resume_emb = vs.embed_batch([s.name for s in resume_skills])

    matches = {}
    for i, jd_vec in enumerate(jd_emb):
        best_score, best_idx = 0, -1
        for j, resume_vec in enumerate(resume_emb):
            score = cosine_similarity(jd_vec, resume_vec)
            if score > best_score:
                best_score, best_idx = score, j
        if best_score > 0.80:
            matches[jd_skills[i].name] = (resume_skills[best_idx], best_score)

    return matches
```

**收益**：同义词、缩写、中英文混写自动识别，匹配准确率从 ~60% → ~90%。

---

### 3.2 LLM 结果缓存层

**问题**：同一个 JD 文件每次面试都重新让 LLM 解析，浪费时间和 token。

**方案**：

```python
# agents/base.py 新增缓存装饰器
import hashlib
import json
from pathlib import Path
from functools import wraps

CACHE_DIR = Path("data/cache/llm")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def cached_parse(prefix: str, ttl: int = 86400 * 7):
    """LLM 解析结果缓存（基于输入 hash + 模型名），7 天过期"""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(self, input_text: str, *args, **kwargs):
            # 缓存 key = prefix + SHA256(input) + model
            key = hashlib.sha256(
                f"{prefix}:{config.llm_model}:{input_text}".encode()
            ).hexdigest()[:16]
            cache_file = CACHE_DIR / f"{prefix}_{key}.json"

            # 命中且未过期 → 直接返回
            if cache_file.exists():
                age = time.time() - cache_file.stat().st_mtime
                if age < ttl:
                    data = json.loads(cache_file.read_text())
                    return response_model.model_validate(data)

            # 未命中 → 调 LLM → 写入缓存
            result = await fn(self, input_text, *args, **kwargs)
            cache_file.write_text(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            )
            return result
        return wrapper
    return decorator

# 使用
class JDParserAgent(BaseAgent):
    @cached_parse("jd")
    async def run(self, jd_raw: str) -> JD:
        ...
```

**收益**：反复调试时同一 JD 不再重复消耗 token。

---

### 3.3 存储层升级：JSON → SQLite

**现状**：`SessionStore` 每场面试一个 JSON 文件，无事务、无并发保护。

**方案**：用 Python 内置 `sqlite3`，零额外依赖。

```sql
-- 面试会话表
CREATE TABLE interview_sessions (
    id TEXT PRIMARY KEY,
    candidate_name TEXT NOT NULL DEFAULT '匿名',
    jd_title TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress',
    state_json TEXT NOT NULL,       -- 完整状态 JSON
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 候选人画像表（长期记忆）
CREATE TABLE candidate_profiles (
    name TEXT PRIMARY KEY,
    profile_json TEXT,
    total_interviews INTEGER DEFAULT 0,
    avg_score REAL DEFAULT 0.0,
    last_interview_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_sessions_candidate ON interview_sessions(candidate_name);
CREATE INDEX idx_sessions_status ON interview_sessions(status);
```

**收益**：并发安全、支持 SQL 查询筛选、ACID 事务。

---

### 3.4 Embedding 服务化（可选）

> 当前实现已经**默认走 SiliconFlow 在线 API**（`BAAI/bge-m3`，1024 维），无需本地部署；
> 本节设想的是进一步把 embedding 抽成**自建**的独立 HTTP 服务，供多个项目共用。

如果后续多个项目都用 bge-m3 embedding，可以抽成独立服务：

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ 项目 A       │  │ 项目 B       │  │ 项目 C       │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │ HTTP :7997
                ┌────────┴────────┐
                │ Infinity Server │
                │ bge-m3 (1024 维) │
                └─────────────────┘
```

当前项目在 [vector_store.py](memory/vector_store.py) 中加一个 `InfinityEmbeddingClient` 即可（以下为**设想代码**，尚未实现；当前实现是 `_ApiEmbedder` 调 SiliconFlow）：

```python
class InfinityEmbeddingClient:
    """本地 Infinity embedding 服务客户端（设想）"""

    def __init__(self, base_url: str = "http://localhost:7997"):
        self._base = base_url

    def encode(self, texts: list[str]) -> list[list[float]]:
        resp = requests.post(
            f"{self._base}/embeddings",
            json={"input": texts, "model": "bge-m3"},   # 1024 维
        )
        return [e["embedding"] for e in resp.json()["data"]]
```

---

### 3.5 LLM Provider 抽象

> **本节的 provider 示例均为设想代码，尚未实现。** 当前实现是 [models/llm.py](models/llm.py) 中一个薄的 `LLM` 封装，
> 用 `AsyncOpenAI` 直连 `config.llm_base_url`（默认 `https://api.deepseek.com`，模型 `deepseek-flash`），没有 Provider 层。

**现状**：[models/llm.py](models/llm.py) 硬编码 `AsyncOpenAI`。

**方案（设想）**：轻量抽象，不和 LangChain 耦合。

```python
from abc import ABC, abstractmethod

class BaseLLMProvider(ABC):
    @abstractmethod
    async def generate(self, system: str, user: str, **kwargs) -> str: ...
    @abstractmethod
    async def generate_stream(self, system: str, user: str, **kwargs) -> AsyncIterator[str]: ...

class DeepSeekProvider(BaseLLMProvider):
    def __init__(self):
        self._client = AsyncOpenAI(
            api_key=config.llm_api_key,
            base_url="https://api.deepseek.com",   # 当前实现的默认值
        )
    ...

class OpenAIProvider(BaseLLMProvider):
    def __init__(self):
        self._client = AsyncOpenAI(
            api_key=config.llm_api_key,
            base_url="https://api.openai.com/v1",  # 设想：切换到 OpenAI
        )
    ...

class OllamaProvider(BaseLLMProvider):
    """本地 Ollama 模型（设想）"""
    def __init__(self, model: str = "qwen2.5:7b"):
        self._client = AsyncOpenAI(
            api_key="ollama",
            base_url="http://localhost:11434/v1",
        )
        self._model = model
    ...
```

**收益**：随时切换 DeepSeek / OpenAI / 本地 Ollama，`config.py` 加一行即可。

---

## 四、远期畅想（3-6 月，产品化）

### 4.1 智能自适应面试

```
┌─────────────────────────────────────────────┐
│              自适应面试引擎                    │
│                                              │
│  候选人回答 → 实时分析 → 动态调整：            │
│    • 答得好 → 自动升级难度                     │
│    • 答得差 → 降级 + 给提示                    │
│    • 答案偏了 → 引导回正轨                      │
│    • 暴露新技能 → 插入即兴题目                   │
│                                              │
│  面试结束后，每道题的真实难度、区分度            │
│  自动统计，持续优化出题策略。                    │
└─────────────────────────────────────────────┘
```

技术基础：LangGraph 的状态机已经支持条件路由，只需丰富 `_next_action_label` 的决策逻辑。

### 4.2 多模态面试

- 🎤 **语音输入**：候选人用语音回答，Whisper 转文字后送入评判流程
- 📹 **视频分析**：可选的表情/眼神检测（注意力评分），但需谨慎使用（伦理边界）
- 📊 **代码编辑**：技术岗直接嵌入在线 IDE（Monaco Editor），候选人写代码，系统自动运行测试

### 4.3 面试题库生态

```
┌─────────────────────────────────────────────────┐
│                  面试题库系统                      │
│                                                  │
│  seed_questions.json (当前 12 道种子题)            │
│       │                                          │
│       ▼                                          │
│  ┌─────────────┐     ┌──────────────┐           │
│  │  题目生成器   │────▶│  人工审核后台  │           │
│  │  (LLM 批量)  │     │  (标星/拒绝)  │           │
│  └─────────────┘     └──────┬───────┘           │
│                             │                    │
│              ┌──────────────┴────────┐          │
│              ▼                      ▼           │
│      ┌──────────────┐     ┌──────────────┐     │
│      │  高质量题库    │     │  已淘汰题目    │     │
│      │  (生产可用)    │     │  (归档)       │     │
│      └──────────────┘     └──────────────┘     │
│                                                  │
│  每道题记录：                                      │
│    • 被使用次数                                   │
│    • 平均得分分布                                 │
│    • 区分度（高分候选 vs 低分候选）                  │
│    • 候选人反馈                                   │
└─────────────────────────────────────────────────┘
```

### 4.4 候选人画像网络

```
候选人 A                   候选人 B
│                          │
│  面试 1: 后端开发         面试 1: 前端开发
│  面试 2: 架构师           面试 2: 全栈开发
│                          │
└──────────┬───────────────┘
           │
           ▼
┌─────────────────────┐
│   人才图谱            │
│                      │
│   • 技能雷达图        │
│   • 成长曲线          │
│   • 团队匹配度         │
│   • 适合岗位推荐       │
│   • 潜力评估           │
└─────────────────────┘
```

基于多场面试结果的综合画像，跨时间追踪候选人成长。

### 4.5 企业级功能

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 面试回放 | 完整对话回放 + 逐题评分明细 | 🟡 |
| 多面试官 | 多个 AI 面试官角色（技术/行为/管理）| 🟡 |
| 自定义评分规则 | 企业按岗位自定义评分权重 | 🟢 |
| ATS 集成 | 对接飞书/Greenhouse/Workday 等招聘系统 | 🟢 |
| 权限管理 | 面试官/HR/管理员三级权限 | 🟢 |
| 数据看板 | 面试通过率、岗位竞争比、招聘漏斗 | 🟢 |
| 合规审计 | 面试过程留痕、公平性分析（防歧视）| 🟢 |
| i18n | 中英双语，后续扩展日/韩 | 🟢 |

### 4.6 技术栈前瞻

> 「现在」列已按当前代码（v0.5.0）校正；「未来」列是设想。

```
                    现在                         未来
                    ────                        ────
Web 框架           Gradio 5                    Gradio 5 / Next.js 前端
API                FastAPI + MCP               FastAPI + GraphQL
状态机             LangGraph                   LangGraph + 持久化 Checkpoint
LLM                DeepSeek API (deepseek-flash)  DeepSeek + 本地 Qwen (混合推理)
Embedding          BAAI/bge-m3 (SiliconFlow API, 1024维)  bge-m3 (自建 Infinity 服务)
向量库             ChromaDB (2 Collections)    ChromaDB / Milvus Lite
存储               JSON → SQLite              SQLite → PostgreSQL
部署               单机                         Docker Compose → K8s
监控               print/logger                OpenTelemetry + Grafana
```

---

## 五、实施路线图

```
Week 1-2          Week 3-4          Month 2-3          Month 4-6
─────────         ─────────         ──────────         ──────────
│ 2.1 _async()    │ 3.1 语义匹配    │ 3.3 SQLite       │ 4.1 自适应引擎
│ 2.2 统一状态机   │ 3.4 Embedding  │ 3.5 Provider     │ 4.2 多模态
│ 2.3 流式 UI     │    服务化        │    抽象           │ 4.3 题库生态
│ 2.4 Prompt校验  │ 3.2 LLM 缓存   │ 10  补充测试      │ 4.4 候选人画像
│ 2.4 环境区分    │                 │                   │ 4.5 企业功能
─────────         ─────────         ──────────         ──────────
    ▲                  ▲                  ▲                  ▲
    │                  │                  │                  │
 近期优化           中期重构            架构夯实           产品化
 (Demo → 可用)     (可用 → 好用)      (好用 → 可靠)     (可靠 → 产品)
```

### 检查清单

- [ ] Week 1: 删除 `_async()`，全部改为 `async def`
- [ ] Week 1: Gradio 直接调用 supervisor，删除重复状态机
- [ ] Week 1: 流式出题接入 Gradio UI
- [ ] Week 2: Prompt 模板校验 + 环境区分
- [ ] Week 3: 语义匹配替代字符串匹配
- [ ] Week 4: LLM 结果缓存
- [ ] Month 2: SQLite 替代 JSON 存储
- [ ] Month 2: LLM Provider 抽象
- [ ] Month 2: 补充核心单元测试
- [ ] Month 3-6: 产品化功能按优先级逐个迭代

---

> **原则**：每一步改动都不影响当前可用的 Demo 功能。每个阶段的产出都是可运行的，不搞大爆炸式重构。
