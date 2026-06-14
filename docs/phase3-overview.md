# Phase 3 实现总结 — AI 面试官 MCP Gateway + 长期记忆

> 对应 ROADMAP.md 第 5-6 周目标：统一 Gateway 入口（鉴权/限流/路由）、ChromaDB 长期记忆、会话持久化、Gradio 前端挂载

---

## 目录

- [1. 整体架构](#1-整体架构)
- [2. 目录结构](#2-目录结构)
- [3. 核心数据流](#3-核心数据流)
- [4. 模块详解](#4-模块详解)
- [5. 代码执行流程（逐步骤）](#5-代码执行流程逐步骤)
- [6. 测试覆盖](#6-测试覆盖)
- [7. 启动方式](#7-启动方式)

---

## 1. 整体架构

```
                   浏览器 (Gradio Web UI)
                         │
                         │  HTTP (同进程内调用 Agent)
                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         Gateway 层 ★ 核心                                          │
│  FastAPI (:8000) — mcp_servers/gateway.py                                        │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                     │
│  │ 鉴权中间件      │  │ 限流中间件      │  │ ServerRegistry  │                     │
│  │ Bearer Token   │  │ 令牌桶 60/min  │  │ 工具名→Server  │                     │
│  └────────────────┘  └────────────────┘  └────────────────┘                     │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │ /ui                          Gradio Web UI (gr.mount_gradio_app)          │   │
│  │ /api/v1/interview            POST  创建面试会话                           │   │
│  │ /api/v1/interview/{id}/talk  POST  提交回答 → 评判 + 下一题               │   │
│  │ /api/v1/interview/{id}       GET   获取会话状态                           │   │
│  │ /api/v1/interview/{id}/report GET  获取面试报告                           │   │
│  │ /health                      GET   健康检查                               │   │
│  │ /mcp/{tool_name}             POST  通用 MCP 工具调用                       │   │
│  │ /mcp/sse                     GET   MCP SSE transport                      │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└──────────────┬───────────────────────────────────────────────────────────────────┘
               │ 内部调用（同进程，无网络开销）
               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             编排层                                                │
│  LangGraph 循环图 (orchestration/supervisor.py)                                  │
│  ★ 新增记忆钩子: store_interview_memory / retrieve_candidate_history /           │
│                   retrieve_similar_questions                                     │
└──────────────────────────────┬───────────────────────────────────────────────────┘
                               │ 调用 Agent
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             Agent 层                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐     │
│  │JDParserAgent│  │ResumeAnalyzer│  │InterviewerAgent  │  │FeedbackAgent │     │
│  │  JD → 结构化 │  │ 简历 → 画像  │  │ ★ 历史题库参考    │  │ 评分+报告     │     │
│  └─────────────┘  └──────────────┘  └──────────────────┘  └──────────────┘     │
└──────────────────────────────────────────────────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
│  记忆层 ★ 新增    │ │  MCP Server  │ │  基础设施          │
│                  │ │              │ │                  │
│ SessionStore     │ │ jd_server    │ │ models/llm.py    │
│ JSON 会话持久化   │ │ resume_server│ │ DeepSeek 封装     │
│                  │ │ question_    │ │                  │
│ VectorStore      │ │ bank_server  │ │ data/            │
│ ChromaDB 向量存储 │ │              │ │ seed_questions   │
│ 4 个 Collection  │ │              │ │                  │
│ 优雅降级          │ │              │ │                  │
└──────────────────┘ └──────────────┘ └──────────────────┘
```

### Phase 3 核心升级

| 维度 | Phase 2 | Phase 3 |
|------|---------|---------|
| 入口 | 3 个 MCP Server 独立暴露 | **统一 Gateway** 鉴权/限流/路由 |
| 前端 | Streamlit 独立进程 | **Gradio 挂载 FastAPI** 单进程 |
| 记忆 | 无持久化，重启丢失 | **SessionStore** (JSON) + **VectorStore** (ChromaDB) |
| 调用方式 | Web UI 直调 Agent | Gradio → FastAPI 同进程 → Agent |
| 出题 | 纯 LLM 生成 | LLM 生成 + **历史题库参考**（避免重复） |
| 面试结束 | 无存储 | 自动写入 **SessionStore + VectorStore** |

---

## 2. 目录结构

```
d:\InterviewAgentHub\
│
├── config.py                 # ★ 新增 8 个配置字段
├── main.py                   # CLI 入口（history 命令就绪）
├── .env                      # 环境变量
├── requirements.txt          # ★ 新增 sentence-transformers, sse-starlette, requests
│
├── agents/                   # Agent 层
│   ├── __init__.py
│   ├── base.py               # — BaseAgent 基类
│   ├── jd_parser.py          # — JD 解析 Agent
│   ├── resume_analyzer.py    # — 简历分析 Agent
│   ├── interviewer.py        # — ★ 新增 _get_similar_questions_hint()
│   └── feedback.py           # — 反馈 Agent
│
├── models/                   # 模型层
│   ├── __init__.py
│   ├── llm.py                # — DeepSeek LLM 封装
│   ├── jd.py                 # — JD / Skill Pydantic 模型
│   ├── resume.py             # — Resume / Project / SkillProficiency
│   ├── question.py           # — Question / JudgeResult / RoundRecord / InterviewReport
│   └── interview.py          # — InterviewState / RoundState / InterviewStatus
│
├── orchestration/            # 编排层
│   ├── __init__.py
│   ├── matcher.py            # — 交叉匹配 + 能力缺口分析
│   └── supervisor.py         # — ★ 新增 3 个记忆钩子函数
│
├── mcp_servers/              # MCP Server
│   ├── __init__.py
│   ├── jd_server.py          # — JD MCP Server (parse_jd)
│   ├── resume_server.py      # — 简历 MCP Server (parse_resume)
│   ├── question_bank_server.py # — 题库 MCP Server (4 个工具)
│   └── gateway.py            # — ★ MCP Gateway (Phase 3 新增)
│
├── memory/                   # ★ 记忆层 (Phase 3 新增)
│   ├── __init__.py
│   ├── session_store.py      # — ★ 会话 JSON 持久化存储
│   └── vector_store.py       # — ★ ChromaDB 向量存储封装
│
├── tools/                    # 工具层
│   ├── __init__.py           # — parse_file() 统一入口
│   ├── pdf_parser.py         # — PDF 解析
│   ├── docx_parser.py        # — DOCX 解析
│   └── text_cleaner.py       # — 文本清洗
│
├── prompts/                  # 提示词层
│   ├── __init__.py           # — load_prompt() 加载器
│   ├── jd_parser.md          # — JD 提取 prompt
│   ├── resume_analyzer.md    # — 简历提取 prompt
│   ├── interviewer.md        # — 出题 prompt
│   ├── interviewer_deepen.md # — 追问加深 prompt
│   ├── interviewer_clarify.md# — 澄清追问 prompt
│   ├── judge.md              # — 评判 prompt
│   └── feedback.md           # — 反馈评分 prompt
│
├── tests/                    # 测试 (24 个用例)
│   ├── __init__.py
│   ├── test_parse_pipeline.py
│   ├── test_agents.py
│   ├── test_interviewer.py
│   └── fixtures/
│
├── docs/                     # 项目文档
│   ├── DESIGN.md              # 架构设计文档
│   ├── ROADMAP.md             # 实现路线图
│   ├── CLAUDE.md              # 项目总览（AI 可读）
│   ├── phase1-overview.md     # Phase 1 总结
│   ├── phase2-overview.md     # Phase 2 总结
│   ├── phase3-overview.md     # ← 本文档
│   └── ticklish-imagining-key.md  # Phase 3 实施规划
│
├── data/                     # 数据目录
│   ├── seed_questions.json   # — 种子题库 12 题
│   ├── sessions/             # — ★ SessionStore 存储目录 (gitignored)
│   └── chroma/               # — ★ ChromaDB 持久化目录 (gitignored)
│
├── web/                      # 界面层
│   ├── __init__.py
│   └── app.py                # — ★ Gradio Web UI (~280 行)
```

---

## 3. 核心数据流

### 3.1 完整面试流程（Gradio + FastAPI 同进程）

```
  用户浏览器 → http://localhost:8000/ui
         │
         ▼
  ┌─────────────────────────────────────────┐
  │  Gradio Web UI (web/app.py)             │
  │                                          │
  │  1. 上传 JD + 简历文件                    │
  │  2. 点击「开始面试」                       │
  │     → _parse_and_match()                 │
  │       ├── parse_file(jd_path)            │
  │       ├── JDParserAgent.run() → JD       │
  │       ├── ResumeAnalyzerAgent.run()      │
  │       └── generate_gap_map()             │
  │     → _generate_next_question()          │
  │       └── InterviewerAgent (含历史题库参考)│
  │  3. 显示第一题 + 能力缺口分析              │
  └──────────────┬──────────────────────────┘
                 │
  用户输入回答 → 点击「提交答案」
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │  _judge_answer() + _generate_next_question() │
  │                                          │
  │  1. InterviewerAgent.judge_answer()      │
  │     → JudgeResult {score, next_action}   │
  │  2. 检查终止条件                           │
  │     ├── 连续空回答 ≥ 3 → terminated       │
  │     ├── 轮次 ≥ max_rounds → terminated    │
  │     └── next_action == "switch" → 切技能  │
  │  3. 若未终止: _generate_next_question()   │
  │     ├── deepen → generate_deepen_question │
  │     ├── clarify → generate_clarify_question│
  │     └── switch → generate_switch_question│
  │  4. 若终止: _generate_report()            │
  │     └── FeedbackAgent (5 维度评分)        │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  Gradio Chatbot 展示对话历史 + 评分
  终止后展示完整报告（面试全程回顾）
```

### 3.2 REST API 模式（外部调用）

```
  外部客户端
         │
         │  POST /api/v1/interview  {"jd_path", "resume_path"}
         ▼
  ┌─────────────────────────────────────────┐
  │  FastAPI Gateway                        │
  │  1. 限流检查 (令牌桶 60/min)             │
  │  2. 鉴权验证 (Bearer Token)             │
  │  3. supervisor.init_interview()         │
  │  4. supervisor.generate_next_question() │
  │  5. SessionStore.save()                 │
  │  6. 返回 {interview_id, question}       │
  └──────────────┬──────────────────────────┘
                 │
                 ▼
  POST /api/v1/interview/{id}/talk  {"answer"}
                 │
                 ▼
  ┌─────────────────────────────────────────┐
  │  1. SessionStore.load()                 │
  │  2. supervisor.judge_and_decide()       │
  │  3. supervisor.generate_next_question() │
  │  4. SessionStore.save()                 │
  │  5. 若 terminated:                      │
  │     → store_interview_memory()          │
  │     → VectorStore 写入                  │
  │  6. 返回 {judge, next_question, terminated} │
  └─────────────────────────────────────────┘
```

### 3.3 长期记忆写入

```
  面试终止 (terminated=True)
         │
         ▼
  store_interview_memory(state)
         │
         ├── 1. 序列化面试记录 (rounds → JSON)
         │     → VectorStore.add("ih_interview_sessions")
         │     metadata: {candidate_name, jd_title, round_count, total_score}
         │
         └── 2. 更新候选人画像
               → VectorStore.add("ih_candidate_profiles")
               metadata: {name, last_interview_at}
```

### 3.4 历史检索（出题参考）

```
  InterviewerAgent.generate_question()
         │
         ▼
  _get_similar_questions_hint(target_skill)
         │
         ├── config.use_vector_memory == True?
         │
         ├── Yes → VectorStore.query("ih_question_bank", skill, n=3)
         │         → 格式化为 "历史类似题目参考（避免重复，可借鉴风格）"
         │         → 追加到 user_prompt
         │
         └── No / 不可用 → 跳过（不影响核心出题流程）
```

---

## 4. 模块详解

### 4.1 mcp_servers/gateway.py — MCP Gateway ★ 新增

**定位**：FastAPI 统一入口，作为反向代理管理 3 个 MCP Server，提供 REST API。

```python
app = FastAPI(title="AI 面试官 Gateway", version="0.3.0")

# ── 鉴权中间件 ──
def verify_auth(credentials):
    """从 Authorization: Bearer <token> 提取并验证"""
    if config.gateway_require_auth:
        token == config.gateway_api_key → 200 / 401

# ── 限流中间件 ──
class RateLimiter:
    """基于 IP 的令牌桶算法，默认 60 请求/分钟"""
    def allow(ip) -> bool → 200 / 429

# ── 注册中心 ──
class ServerRegistry:
    """管理 tool_name → FastMCP server 的映射"""
    def register(server, name)  # 遍历 server._tool_manager._tools
    async def call_tool(name, **kwargs)  # 按名称路由调用
```

#### REST API 端点

| 端点 | 方法 | 功能 | 内部调用 |
|------|------|------|----------|
| `/health` | GET | 健康检查 | — |
| `/mcp/{tool_name}` | POST | 通用 MCP 工具调用 | `registry.call_tool()` |
| `/mcp/sse` | GET | MCP SSE transport | sse-starlette |
| `/api/v1/interview` | POST | 创建面试会话 | `init_interview()` + `generate_next_question()` |
| `/api/v1/interview/{id}/talk` | POST | 提交回答 | `judge_and_decide()` + `generate_next_question()` + `store_interview_memory()` |
| `/api/v1/interview/{id}` | GET | 获取会话状态 | `SessionStore.load()` |
| `/api/v1/interview/{id}/report` | GET | 获取面试报告 | `FeedbackAgent.generate_report()` |

#### 生命周期

```python
@app.on_event("startup")
async def startup():
    # 1. 注册 jd_server (parse_jd)
    # 2. 注册 resume_server (parse_resume)
    # 3. 注册 question_bank_server (generate_questions, search_seed_bank, add_to_seed_bank, get_seed_bank_stats)
    # 4. 初始化 SessionStore

# 共注册 6 个工具，按工具名自动路由
```

#### 状态序列化

```python
_state_to_pydantic(state: dict) -> InterviewState
# TypedDict → Pydantic 模型（含 RoundState 列表），用于 JSON 序列化存储

_pydantic_to_state(ps: InterviewState) -> dict
# Pydantic → TypedDict（含 RoundRecord 列表），供 supervisor 函数使用
```

### 4.2 memory/session_store.py — 会话存储 ★ 新增

**定位**：轻量级 JSON 文件持久化，零依赖，每场面试一个文件。

```python
class SessionStore:
    def __init__(self, store_dir=None)
        # 存储路径：data/sessions/*.json

    def save(state: InterviewState) -> str
        # 序列化 InterviewState 为 JSON
        # 若 interview_id 为空则自动生成 UUID
        # 返回 interview_id

    def load(interview_id: str) -> Optional[InterviewState]
        # 按 ID 加载，反序列化为 Pydantic 模型

    def delete(interview_id: str) -> bool

    def find_by_candidate(name: str) -> list[InterviewState]
        # 子串匹配候选人姓名

    def list_all() -> list[InterviewState]
        # 全部记录，按文件修改时间倒序

    def count() -> int
```

**存储格式**（`data/sessions/{interview_id}.json`）：

```json
{
  "interview_id": "a1b2c3d4e5f6",
  "status": "completed",
  "jd": { "title": "Python 后端开发", "required_skills": [...] },
  "resume": { "name": "张三", "skills": [...] },
  "gap_analysis": { "ordered_skills": [...] },
  "rounds": [
    {
      "round_number": 1,
      "skill": "Python",
      "question": { "content": "...", "difficulty": "intermediate" },
      "answer": "...",
      "judge": { "score": 85, "next_action": "deepen" }
    }
  ],
  "current_round": 3,
  "candidate_name": "张三",
  "created_at": "2026-06-12T01:00:00",
  "updated_at": "2026-06-12T01:05:00"
}
```

### 4.3 memory/vector_store.py — ChromaDB 向量存储 ★ 新增

**定位**：ChromaDB 持久化客户端封装，4 个 Collection，本地 embedding，优雅降级。

```python
class VectorStore:
    def __init__(self, persist_dir=None):
        # 1. 初始化 ChromaDB PersistentClient
        #    → 失败：self._available = False，全部操作静默跳过
        # 2. 初始化 SentenceTransformer embedding
        #    → 优先使用 HF_ENDPOINT 环境变量
        #    → 其次尝试 hf-mirror.com 镜像
        #    → 失败：self._available = False

    # ── 通用 CRUD ──
    def add(collection_name, documents, metadatas, ids) -> bool
    def query(collection_name, query_text, n_results) -> list[dict]
    def get(collection_name, doc_id) -> Optional[dict]
    def delete(collection_name, doc_id) -> bool
    def list_all(collection_name) -> list[dict]

    # ── 便捷方法 ──
    def store_interview_session(interview_json, metadata) -> bool
    def search_similar_questions(skill, n) -> list[dict]
    def search_candidate_history(candidate_name) -> list[dict]
    def update_candidate_profile(name, profile_json, extra_meta) -> bool

    @property
    def available(self) -> bool  # 是否可用
```

#### 4 个 Collection

| Collection 名 | 文档内容 | metadata | 用途 |
|--------------|----------|----------|------|
| `ih_jd_history` | JD 全文 | `{title, company, created_at}` | JD 历史检索 |
| `ih_question_bank` | 题目内容 | `{skill, difficulty, created_at}` | 出题参考，避免重复 |
| `ih_interview_sessions` | 面试记录全文 | `{candidate_name, jd_title, round_count, total_score}` | 历史面试检索 |
| `ih_candidate_profiles` | 候选人简历摘要 | `{name, title, experience_years, last_interview_at}` | 候选人画像 |

#### Embedding 策略

```
首选：本地 sentence-transformers/all-MiniLM-L6-v2（384 维）
  ├── 优先使用 HF_ENDPOINT 环境变量
  ├── 其次使用 hf-mirror.com 镜像（国内网络友好）
  └── 失败 → 降级为无记忆模式

特性：
  - 纯本地推理，无额外 API 调用
  - 首次下载 ~80MB，之后缓存
  - 降级不影响核心面试流程
```

#### 降级策略

```
ChromaDB 连接失败 → self._available = False
  ├── 写操作：静默跳过，返回 False
  ├── 读操作：返回空列表 / None
  └── 核心面试：不受任何影响
```

### 4.4 config.py — Phase 3 新增字段

```python
# ── Paths (Phase 3 新增) ──
config.session_dir           # data/sessions    — SessionStore 存储目录

# ── Embedding (Phase 3 新增) ──
config.embedding_model       # sentence-transformers/all-MiniLM-L6-v2

# ── Gateway (Phase 3 新增) ──
config.gateway_api_key       # GATEWAY_API_KEY 环境变量，默认 "dev-key-change-me"
config.gateway_require_auth  # 是否开启鉴权，默认 True（可通过 GATEWAY_NO_AUTH=1 关闭）
config.gateway_rate_limit    # 每分钟最大请求数，默认 60

# ── Feature flags (Phase 3 新增) ──
config.use_vector_memory     # 是否启用 ChromaDB 记忆，默认 True
```

### 4.5 orchestration/supervisor.py — 记忆钩子 ★ 新增

```python
def store_interview_memory(state: dict) -> bool:
    """面试结束时，将面试记录写入向量库 + 更新候选人画像。
    在 Gateway 的 talk 端点中、面试终止时调用。
    失败时静默降级，不抛出异常。
    """
    # 1. 序列化 rounds → JSON
    # 2. VectorStore.store_interview_session()
    # 3. VectorStore.update_candidate_profile()

def retrieve_candidate_history(candidate_name: str) -> list[dict]:
    """检索候选人的历史面试记录（出题参考）。"""

def retrieve_similar_questions(skill: str, n: int = 3) -> list[dict]:
    """从历史题库中检索相似题目（出题参考）。"""
```

### 4.6 agents/interviewer.py — 历史题库参考 ★ 新增

```python
def _get_similar_questions_hint(self, skill: str, n: int = 3) -> str:
    """从历史题库检索相似题目，作为出题参考提示。静默失败。"""
    # 1. 检查 config.use_vector_memory
    # 2. VectorStore.search_similar_questions(skill, n)
    # 3. 格式化为提示文本，追加到 user_prompt
    # 失败 → 返回空字符串（不影响出题）

# 在 generate_question() 中调用：
hint = self._get_similar_questions_hint(target_skill)
if hint:
    user_prompt += "\n" + hint  # 历史类似题目参考（避免重复，可借鉴风格）
```

### 4.7 web/app.py — Gradio Web UI ★ 新增

**定位**：Gradio Blocks 构建的三步面试界面，挂载在 FastAPI 的 `/ui` 路径下，同进程内直接调用 Agent。

#### 三步流程

```
上传 JD + 简历 → 点击开始 → 面试对话 → 提交回答 → 评分 → 下一题 → ... → 报告
  (upload_col)     (interview_col + Chatbot)              (report_col)
```

#### 核心组件

```python
demo = gr.Blocks(title="AI 面试官")

# 三层 Column，通过 visible 属性切换显示
upload_col    # 文件上传（JD + Resume）+ 开始按钮
interview_col # Chatbot 对话 + 回答输入框 + 提交/结束按钮
report_col    # Markdown 报告 + 重新开始按钮

# 状态管理
interview_state = gr.State()  # 存储完整面试状态字典
```

#### 关键回调函数

```python
on_start(jd_file, resume_file) -> (state, info, chat, answer, upload_col, interview_col, report_col)
    """上传 → 解析 → 匹配 → 出第一题，切换到面试区"""

on_submit(answer, state) -> (state, chat, answer, report_md, interview_col, report_col)
    """提交回答 → 评判 → 下一题（或终止 → 生成报告），直接返回渲染好的报告 Markdown"""

_end_interview(state) -> (...)
    """手动结束面试，生成报告并切换到报告区"""
```

#### 与旧版（Streamlit）对比

| 维度 | Streamlit (Phase 2) | Gradio (Phase 3) |
|------|---------------------|------------------|
| 代码量 | ~740 行 | ~280 行 |
| 运行方式 | 独立进程 `streamlit run` | 挂载 FastAPI 同进程 |
| 状态管理 | `st.session_state` 字典 | `gr.State()` 对象 |
| 双模式切换 | `use_gateway` toggle + Gateway 客户端函数 | 不需要（同进程直调 Agent） |
| 部署 | 需额外端口 | 共用 8000 端口 |
| 学习成本 | 需学 Streamlit 概念 | 用户已有 Gradio 经验 |

---

## 5. 代码执行流程（逐步骤）

### 5.1 Gateway 启动流程

```
python main.py web
  │
  ▼
uvicorn.run("mcp_servers.gateway:app")
  │
  ▼
@app.on_event("startup")
  ├── 1. 导入 jd_server → registry.register(jd_app, "jd-server")
  │     └── 注册工具: parse_jd
  ├── 2. 导入 resume_server → registry.register(resume_app, "resume-server")
  │     └── 注册工具: parse_resume
  ├── 3. 导入 question_bank_server → registry.register(qb_app, "question-bank-server")
  │     └── 注册工具: generate_questions, search_seed_bank, add_to_seed_bank, get_seed_bank_stats
  └── 4. 初始化 app.state.session_store = SessionStore()

Gateway 就绪，监听 0.0.0.0:8000
  共注册 6 个工具
```

### 5.2 创建面试（Gateway 模式）

```
POST /api/v1/interview  {"jd_path": "uploads/jd.pdf", "resume_path": "uploads/resume.pdf"}
  │
  ▼
Gateway: create_interview()
  ├── 1. 调用 supervisor.init_interview(jd_path, resume_path)
  │     ├── tools.parse_file(jd_path) → jd_raw
  │     ├── tools.parse_file(resume_path) → resume_raw
  │     ├── JDParserAgent.run(jd_raw) → JD 模型
  │     ├── ResumeAnalyzerAgent.run(resume_raw) → Resume 模型
  │     └── generate_gap_map(jd, resume) → 排序技能列表
  │
  ├── 2. 调用 supervisor.generate_next_question(state)
  │     └── InterviewerAgent.generate_question() [含历史题库参考]
  │
  ├── 3. _state_to_pydantic(state) → InterviewState (Pydantic)
  │
  ├── 4. SessionStore.save(pydantic_state) → interview_id
  │     └── 写入 data/sessions/{interview_id}.json
  │
  └── 5. 返回 {interview_id, question, state_summary}
```

### 5.3 多轮对话（Gateway 模式）

```
POST /api/v1/interview/{id}/talk  {"answer": "我认为 Python 的 GIL..."}
  │
  ▼
Gateway: interview_talk()
  ├── 1. SessionStore.load(interview_id) → InterviewState
  │
  ├── 2. _pydantic_to_state(pydantic_state) → dict (supervisor 兼容)
  │
  ├── 3. supervisor.judge_and_decide(state, answer)
  │     ├── InterviewerAgent.judge_answer(question, answer) → JudgeResult
  │     └── decide_next_node(state) → {terminated, current_skill_index}
  │
  ├── 4. 若 terminated == False:
  │     ├── supervisor.generate_next_question(state) → 新题
  │     ├── _state_to_pydantic(state) → SessionStore.save()
  │     └── 返回 {judge, next_question, terminated: false}
  │
  ├── 5. 若 terminated == True:
  │     ├── _state_to_pydantic(state)
  │     ├── InterviewStatus → COMPLETED
  │     ├── SessionStore.save()
  │     ├── supervisor.store_interview_memory(state)  ← ★ 写入向量库
  │     │     ├── VectorStore.store_interview_session()
  │     │     └── VectorStore.update_candidate_profile()
  │     └── 返回 {judge, next_question: null, terminated: true}
```

### 5.4 生成报告（Gateway 模式）

```
GET /api/v1/interview/{id}/report
  │
  ▼
Gateway: get_interview_report()
  ├── 1. SessionStore.load(interview_id) → InterviewState
  │
  ├── 2. _pydantic_to_state(pydantic_state) → dict
  │
  ├── 3. FeedbackAgent.generate_report(jd, resume, rounds)
  │     ├── _build_transcript(rounds) → 面试记录文本
  │     ├── 填充 prompts/feedback.md
  │     └── LLM → InterviewReport (5 维度评分)
  │
  ├── 4. InterviewStatus → COMPLETED
  │     SessionStore.save()
  │
  └── 5. 返回 {interview_id, report, candidate_name}
```

### 5.5 CLI 历史查询

```
python main.py history
  │
  ▼
main.py: history()
  ├── SessionStore()
  ├── 若有 --candidate: store.find_by_candidate(name)
  └── 否则: store.list_all()
  │
  ▼
终端输出：
  张三 | 2026-06-12 01:00 | completed
    面试 ID: a1b2c3d4e5f6

  李四 | 2026-06-12 01:30 | completed
    面试 ID: f6e5d4c3b2a1
```

---

## 6. 测试覆盖

### 现有测试全部通过

| 文件 | 用例数 | Phase 3 状态 |
|------|--------|-------------|
| `tests/test_parse_pipeline.py` | 5 | ✅ 全部通过 |
| `tests/test_agents.py` | 8 | ✅ 全部通过 |
| `tests/test_interviewer.py` | 11 | ✅ 全部通过 |
| **合计** | **24** | **全部通过** |

### Phase 3 验证方式

```bash
# 1. SessionStore 验证
python main.py history                          # → "暂无面试记录"
# 完成一次面试后：
python main.py history                          # → 显示面试记录
python main.py history -c "张三"                 # → 按候选人搜索

# 2. VectorStore 验证
python -c "
from memory.vector_store import VectorStore
vs = VectorStore()
print(vs.available)                              # → True
vs.add('ih_test', ['test doc'], [{'k': 'v'}], ['id1'])
results = vs.query('ih_test', 'test', n_results=1)
print(results)                                   # → 检索结果
vs.delete('ih_test', 'id1')
"

# 3. Gateway 启动验证
python main.py web
curl http://localhost:8000/health                # → {"status": "ok", "tools": [...]}

# 4. Gateway REST API 验证
curl -X POST http://localhost:8000/api/v1/interview \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"jd_path":"tests/fixtures/sample_jd.txt","resume_path":"tests/fixtures/sample_resume.txt"}'
# → {"interview_id": "...", "question": {...}}

# 5. 端到端验证
# python main.py web → 浏览器打开 http://localhost:8000/ui → 上传文件 → 完整面试 → 查看历史
```

---

## 7. 启动方式

```bash
# 1. 激活虚拟环境
cd d:/InterviewAgentHub
source .venv/Scripts/activate

# 2. 一键启动（Gateway + REST API + Gradio UI 全部在一个进程）
python main.py web
# → http://localhost:8000/ui      Gradio Web UI
# → http://localhost:8000/docs    FastAPI Swagger 文档
# → http://localhost:8000/health  健康检查

# ── 开发环境选项 ──

# 关闭 Gateway 鉴权
GATEWAY_NO_AUTH=1 python main.py web

# 关闭向量记忆（纯内存模式）
NO_VECTOR_MEMORY=1 python main.py web

# ── CLI 工具 ──

python main.py history                # 查看所有历史面试
python main.py history -c "张三"      # 按候选人搜索

# ── 运行测试 ──

python -m pytest tests/ -v            # 24 个测试
```

### 依赖清单

| 包 | 用途 | Phase |
|----|------|-------|
| `openai>=1.30.0` | DeepSeek API 调用 | 1 |
| `langgraph>=0.2.0` | 多 Agent 编排框架 | 1 |
| `mcp>=1.0.0` | MCP 协议 | 1 |
| `chromadb>=0.5.0` | 向量数据库 | **3** |
| `sentence-transformers>=2.7.0` | 本地 embedding 模型 | **3** |
| `pydantic>=2.0.0` | 数据模型校验 | 1 |
| `pdfplumber>=0.10.0` | PDF 文件解析 | 1 |
| `python-docx>=1.1.0` | DOCX 文件解析 | 1 |
| `fastapi>=0.110.0` | MCP Gateway | **3** |
| `sse-starlette>=1.8.0` | MCP SSE transport | **3** |
| `requests>=2.28.0` | Gateway HTTP 客户端 | **3** |
| `gradio>=5.0.0` | Web UI 框架（挂载 FastAPI） | **3** |
| `python-dotenv>=1.0.0` | .env 环境变量加载 | 1 |
| `typer>=0.9.0` | CLI 命令行框架 | 1 |
| `rich>=13.0.0` | 终端彩色输出 | 1 |

---

## 附录：关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| Gateway 实现方式 | FastAPI + in-process 加载 MCP Server | 单体部署，无需跨进程通信，简化运维 |
| 前端框架 | Gradio 挂载 FastAPI (`gr.mount_gradio_app`) | 用户有使用经验，单进程零网络开销，代码量减半 |
| MCP 路由策略 | 按工具名前缀路由到 Server 实例 | FastMCP 自带工具注册表，直接读取 _tool_manager |
| Embedding 选择 | sentence-transformers 本地模型 | 零 API 调用成本，离线可用，384 维轻量 |
| Embedding 网络策略 | 自动回退到 hf-mirror.com | 国内网络环境 SSL 问题，确保可用性 |
| 会话存储格式 | JSON 文件（非 ChromaDB） | 轻量、人类可读、版本管理友好、零依赖 |
| 降级策略 | 全部静默降级 | ChromaDB/Embedding 失败不影响核心面试流程 |
| 前端状态管理 | `gr.State()` 存储完整面试字典 | 简单直接，比 Streamlit session_state 更直观 |
| 面试记忆写入 | terminated 时调用 | 确保只写入完整面试，不写入半截数据 |
| 历史题库参考 | 追加到出题 prompt 末尾 | 非侵入式，不影响 LLM 原有出题逻辑 |
| 鉴权开关 | 通过 `GATEWAY_NO_AUTH` 环境变量控制 | 开发环境跳过鉴权，生产环境强制验证 |
