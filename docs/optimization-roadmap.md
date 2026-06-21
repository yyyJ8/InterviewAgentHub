# AI 面试官 — 优化升级方案

> 从 Demo 到半生产级的完整演进路线图  
> 2026-06-20 | 当前版本 v0.4.0

---

## 目录

- [一、现状诊断](#一现状诊断)
- [二、近期优化（1-2 周，高收益低风险）](#二近期优化12-周高收益低风险)
- [三、中期重构（1-2 月，架构升级）](#三中期重构12-月架构升级)
- [四、远期畅想（3-6 月，产品化）](#四远期畅想3-6-月产品化)
- [五、实施路线图](#五实施路线图)

---

## 一、现状诊断

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

## 二、近期优化（1-2 周，高收益低风险）

### 2.1 消除 `_async()` 反模式

**当前代码**（[web/app.py:32-64](web/app.py#L32-L64)）：

```python
def _async(coro):
    """用独立事件循环运行 coroutine"""
    loop = asyncio.new_event_loop()     # ← 每次调用创建新 loop
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_with_cleanup())
    finally:
        loop.close()                     # ← 关闭时可能报噪音错误
```

**问题**：
- 每次 LLM 调用都创建+销毁一个完整的事件循环，浪费资源
- `loop.close()` 时如果有未清理的 httpx 连接，会抛 "Event loop is closed" 警告
- 线程池 fallback 进一步增加了复杂度

**方案**：升级到 Gradio 5 原生 async 支持。

```python
# 改后：直接用 async def
async def on_start(jd_file, resume_file):
    """逐步 yield 进度"""
    if jd_file is None or resume_file is None:
        yield (None, None, [("系统", "请先上传 JD 和简历文件")], "", ...)
        return

    jd_path = _extract_file_path(jd_file)
    resume_path = _extract_file_path(resume_file)

    yield (None, "📄 正在解析文件...", [("系统", "📄 正在...")], "", ...)

    jd_raw = parse_file(jd_path)
    resume_raw = parse_file(resume_path)
    jd_agent = JDParserAgent()
    jd = await jd_agent.run(jd_raw)          # ← 直接 await
    ...
```

**影响范围**：
- `web/app.py`：`on_start`、`on_submit`、`_end_interview` 改为 `async def`
- 删除 `_async()` 函数及其线程池 fallback
- 约 30 行删除，40 行修改

**收益**：稳定性大幅提升，代码量减少，不再有事件循环警告。

---

### 2.2 统一面试状态机

**当前问题**：

| 功能 | supervisor.py (LangGraph) | web/app.py (手动状态) |
|------|:---:|:---:|
| 解析 JD | `parse_jd_node` | `on_start` 内联 |
| 解析简历 | `parse_resume_node` | `on_start` 内联 |
| 技能匹配 | `match_skills_node` | `on_start` 内联 |
| 出题 | `generate_question_node` | `_generate_next_question` |
| 评判 | `judge_answer_node` | `_judge_answer` |
| 终止判断 | `_next_action_label` | 分散在 `_judge_answer` 中 |
| 状态管理 | LangGraph StateGraph | 手动 dict |

两套代码逻辑几乎一样但互不共享。修一个 bug 要改两处，极易出现行为不一致。

**方案**：Gradio 直接调用 supervisor 函数，Gateway 已经在这么做了。

```python
# web/app.py 改后
from orchestration.supervisor import (
    init_interview,
    generate_next_question,
    judge_and_decide,
    store_interview_memory,
)

async def on_start(jd_file, resume_file):
    ...
    # 直接用 supervisor 的初始化函数
    state = await init_interview(jd_path, resume_path)
    state["candidate_name"] = resume.name
    state = await generate_next_question(state)
    ...

async def on_submit(answer, state):
    ...
    state = await judge_and_decide(state, answer)
    if state.get("terminated"):
        store_interview_memory(state)
        state = await _generate_report(state)
        ...
    else:
        state = await generate_next_question(state)
    ...
```

**改动内容**：
- 删除 `web/app.py` 中的：`_parse_and_match`、`_generate_next_question`、`_judge_answer`
- 保留 `_generate_report`（supervisor 目前没有）
- 删除 `_async()` 包装
- 约 80 行删除，20 行新增

**收益**：逻辑唯一，supervisor 的改进自动惠及 Gradio。

---

### 2.3 Web UI 流式输出

**现状**：[agents/interviewer.py:67-112](agents/interviewer.py#L67-L112) 已实现 `generate_question_stream`，但 Gradio 没调用。

```python
# interviewer.py 已就绪
async def generate_question_stream(self, jd, resume, target_skill, ...):
    async for delta, done, result in super().run_streaming(...):
        if done and result is not None:
            result.skill = target_skill
        yield (delta, done, result)
```

**方案**：在 Gradio 的 `on_start` 和 `on_submit` 中，出题环节改用流式。

```python
async def on_start(jd_file, resume_file):
    ...
    # 流式出题
    interviewer = InterviewerAgent()
    question_text = ""
    async for delta, done, result in interviewer.generate_question_stream(
        jd=jd, resume=resume, target_skill=skill_name,
        difficulty=difficulty, intent=intent,
    ):
        if not done:
            question_text += delta
            # 实时更新聊天区
            yield (state, info_text,
                   [("🤖 面试官", question_text + "▌")],  # 光标动画
                   "", ...)
        else:
            state["question"] = result
            yield (state, info_text,
                   [("🤖 面试官", question_text)],
                   "", ...)
```

**收益**：用户看到题目逐字生成，体验质变（从等 5 秒看结果 → 即时反馈）。

---

### 2.4 其他顺手修复

#### 2.4.1 Prompt 变量校验

```python
# prompts/__init__.py 改后
import re

_VAR_PATTERN = re.compile(r'\{(\w+)\}')

def load_prompt(name: str) -> PromptTemplate:
    """加载 prompt 模板，返回带校验的模板对象"""
    if name in _CACHE:
        return _CACHE[name]
    path = _PROMPTS_DIR / f"{name}.md"
    content = path.read_text(encoding="utf-8")
    expected = set(_VAR_PATTERN.findall(content))
    tmpl = PromptTemplate(content, name, expected)
    _CACHE[name] = tmpl
    return tmpl


class PromptTemplate:
    __slots__ = ('_template', 'name', 'expected_vars')
    def __init__(self, template: str, name: str, expected: set[str]):
        self._template = template
        self.name = name
        self.expected_vars = expected

    def format(self, **kwargs) -> str:
        given = set(kwargs.keys())
        missing = self.expected_vars - given
        if missing:
            raise KeyError(f"Prompt '{self.name}' 缺少变量: {missing}")
        extra = given - self.expected_vars
        if extra:
            logger.warning(f"Prompt '{self.name}' 多余变量: {extra}")
        return self._template.format(**kwargs)
```

#### 2.4.2 环境区分

```python
# config.py 新增
env: str = field(default_factory=lambda: os.getenv("ENV", "dev"))

def __post_init__(self):
    if self.env == "dev":
        self.gateway_require_auth = False
        self.log_level = "DEBUG"
    ...
```

---

## 三、中期重构（1-2 月，架构升级）

### 3.1 语义技能匹配（利用 BGE Embedding）

**现状**：`matcher.py` 用纯字符串小写匹配技能名：

```python
# 当前逻辑
resume_skill_map = {s.name.lower(): s for s in resume.skills}
if key in skill_map:  # "React.js" vs "React" → False ❌
```

**方案**：基于 BGE embedding 的余弦相似度匹配。

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

如果后续多个项目都用 BGE embedding，可以抽成独立服务：

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ 项目 A       │  │ 项目 B       │  │ 项目 C       │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │ HTTP :7997
                ┌────────┴────────┐
                │ Infinity Server │
                │ BGE-base-zh     │
                │ (~400MB RAM)    │
                └─────────────────┘
```

当前项目在 [vector_store.py](memory/vector_store.py) 中加一个 `InfinityEmbeddingClient` 即可：

```python
class InfinityEmbeddingClient:
    """本地 Infinity embedding 服务客户端"""

    def __init__(self, base_url: str = "http://localhost:7997"):
        self._base = base_url

    def encode(self, texts: list[str]) -> list[list[float]]:
        resp = requests.post(
            f"{self._base}/embeddings",
            json={"input": texts, "model": "bge"},
        )
        return [e["embedding"] for e in resp.json()["data"]]
```

---

### 3.5 LLM Provider 抽象

**现状**：[models/llm.py](models/llm.py) 硬编码 `AsyncOpenAI`。

**方案**：轻量抽象，不和 LangChain 耦合。

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
            base_url="https://api.deepseek.com",
        )
    ...

class OpenAIProvider(BaseLLMProvider):
    def __init__(self):
        self._client = AsyncOpenAI(
            api_key=config.llm_api_key,
            base_url="https://api.openai.com/v1",
        )
    ...

class OllamaProvider(BaseLLMProvider):
    """本地 Ollama 模型"""
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
│  seed_questions.json (当前 10 道种子题)            │
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

```
                    现在                         未来
                    ────                        ────
Web 框架           Gradio 5                    Gradio 5 / Next.js 前端
API                FastAPI + MCP               FastAPI + GraphQL
状态机             LangGraph                   LangGraph + 持久化 Checkpoint
LLM                DeepSeek API                DeepSeek + 本地 Qwen (混合推理)
Embedding          BGE-base-zh (本地)          BGE-base-zh (Infinity 服务)
向量库             ChromaDB                    ChromaDB / Milvus Lite
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
