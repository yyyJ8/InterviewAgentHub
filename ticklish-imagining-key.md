# Phase 3 实施方案：MCP Gateway + 长期记忆

---

## 一、Context

Phase 1 & 2 已完成：实现了完整的面试闭环（文件上传 → JD/简历解析 → 多轮追问 → 反馈报告），
Web UI 可用，三个 MCP Server 独立运行。但当前存在三个问题：

1. **三个 MCP Server 各自独立暴露端口**，没有统一入口管理（鉴权/限流/路由）
2. **没有长期记忆**：面试记录只在内存中，重启丢失，无法参考历史
3. **Web UI 直调 Agent**，未通过 Gateway API 解耦

Phase 3 解决这三个问题，将架构升级为生产级。

---

## 二、实施概览

| 块 | 子任务 | 文件 |
|----|--------|------|
| **A: MCP Gateway** | A1-A6 | `mcp_servers/gateway.py` |
| **B: ChromaDB 集成** | B1-B6 | `memory/vector_store.py` |
| **C: 会话存储** | C1-C2 | `memory/session_store.py` |
| **D: API + 多候选人 + 解耦** | D1-D4 | `web/`, `main.py`, `orchestration/` |

---

## 三、详细设计

### 块 A：MCP Gateway（`mcp_servers/gateway.py`）

**目标**：FastAPI 统一入口，注册/鉴权/限流/路由，暴露 4 个 REST API + MCP SSE 端点。

**关键模式**：使用 FastAPI + `sse-starlette` 实现 MCP 的 SSE transport。Gateway 不重新实现 MCP 协议，
而是作为**反向代理**：接收 MCP 请求 → 按工具名路由到对应 Server 进程 → 返回结果。

#### A1 — Gateway 框架 + SSE transport（新增文件 `mcp_servers/gateway.py`）

- FastAPI app 实例，`/mcp` 端点提供 SSE transport
- 使用 `sse-starlette` 的 `EventSourceResponse`
- 在 Gateway 启动时，通过 subprocess 或 in-process 启动三个 MCP Server
- 定义 Gateway 的 `startup` / `shutdown` 事件

**实现方式选择**：考虑到项目是单体部署（非微服务），三个 MCP Server 都以 in-process 方式
加载。Gateway 内部持有三个 FastMCP app 实例的引用，根据工具名前缀路由：
- `parse_jd` / `search_similar_jd` → jd_server
- `parse_resume` / `compare_profiles` → resume_server
- `generate_questions` / `search_seed_bank` / `add_to_seed_bank` / `get_seed_bank_stats` → question_bank_server

#### A2 — Server 注册机制

- 类 `ServerRegistry`：管理 `{tool_name: server_instance}` 映射
- 方法 `register(server: FastMCP, prefix: str)`：遍历 server 的 tool 列表，注册到映射表
- 方法 `route(tool_name: str) -> callable`：按名称查找并调用

#### A3 — 路由转发

- `POST /mcp/{tool_name}`：接收 tool 参数，路由到对应 Server 执行
- 返回 JSON 结果

#### A4 — 鉴权中间件

- 从 `Authorization: Bearer <token>` 头中提取 token
- 与 `config.gateway_api_key` 比对（config.py 新增字段）
- 不匹配则返回 401
- 可通过配置关闭（开发环境）

#### A5 — 限流中间件

- 基于 IP 的令牌桶算法
- 默认 60 请求/分钟
- 超过限制返回 429
- 实现类 `RateLimiter`（内存 dict + 时间戳）

#### A6 — Gateway REST API 定义

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/v1/interview` | POST | 创建面试会话（上传 JD/简历路径） |
| `/api/v1/interview/{id}/talk` | POST | 提交回答，返回下一题 |
| `/api/v1/interview/{id}` | GET | 获取会话当前状态 |
| `/api/v1/interview/{id}/report` | GET | 获取面试报告 |
| `/health` | GET | 健康检查 |

每个 REST API 内部调用 supervisor.py 中的函数，而非直接调 Agent。

---

### 块 B：ChromaDB 集成（`memory/vector_store.py`）

**目标**：提供 4 个 Collection 的 CRUD 封装，面试记录持久化，历史检索，优雅降级。

#### B1 — 向量库封装（新增 `memory/vector_store.py`）

- 类 `VectorStore`：
  - `__init__`: 初始化 ChromaDB 持久化客户端，persist_dir 从 config 读取
  - `_get_collection(name)`: 获取或创建 Collection
  - `add(collection, documents, metadatas, ids)`: 写入
  - `query(collection, query_text, n_results)`: 语义检索
  - `get(collection, id)`: 按 ID 获取
  - `delete(collection, id)`: 按 ID 删除
  - `list_all(collection)`: 列出所有记录

#### B2 — 4 个 Collection 定义

| Collection 名 | 文档内容 | metadata |
|--------------|----------|----------|
| `ih_jd_history` | JD 全文 | `{title, company, created_at}` |
| `ih_question_bank` | 题目内容 | `{skill, difficulty, created_at}` |
| `ih_interview_sessions` | 面试记录全文 | `{candidate_name, jd_title, round_count, total_score, created_at}` |
| `ih_candidate_profiles` | 候选人简历摘要 | `{name, title, experience_years, last_interview_at}` |

#### B3 — Embedding 策略

- 首选：DeepSeek embeddings API（`text-embedding-3` 或类似）
- 备选：`sentence-transformers/all-MiniLM-L6-v2`（本地，无需 API）
- 在 `config.py` 中配置 `embedding_model` 字段

**选择**：使用 `sentence-transformers` 本地模型，避免额外 API 调用和延迟。
首次加载会下载模型，之后缓存到本地。

#### B4 — 面试存储

- 面试结束时（`supervisor.py` 的 `END` 节点或 Web UI 的结果页），
  调用 `vector_store.add("ih_interview_sessions", ...)`
- 同时更新 `candidate_profiles`

#### B5 — 历史检索

- 出题时（`generate_question_node` 或 `InterviewerAgent`），
  可选调用 `vector_store.query("ih_question_bank", skill)` 检索相似题
- 新面试开始时，检索同候选人的历史记录

#### B6 — 降级策略

- `VectorStore` 初始化时 try/except ChromaDB 连接
- 连接失败 → 设置 `self._available = False`
- 所有写操作在不可用时静默跳过
- 读操作返回空列表
- 不影响核心面试流程

---

### 块 C：会话存储（`memory/session_store.py`）

**目标**：轻量级面试会话管理（JSON 文件持久化，不需要 ChromaDB 也能用）。

#### C1 — 文件结构（新增 `memory/session_store.py`）

- 类 `SessionStore`：
  - 存储路径：`data/sessions/` 目录下的 JSON 文件，每场面试一个文件
  - `save(interview_state)`: 将面试状态序列化为 JSON 保存
  - `load(interview_id)`: 按 ID 加载
  - `find_by_candidate(name)`: 按候选人搜索
  - `list_all()`: 列出所有记录

#### C2 — 集成 main.py

- `main.py` 的 `history` 命令调 `SessionStore`（已存在代码骨架）
- 补充 `SessionStore` 中 `InterviewState` 的序列化/反序列化逻辑

---

### 块 D：API + 多候选人 + 解耦

#### D1 — 会话管理

- Gateway 的 `POST /api/v1/interview` 生成 `interview_id`（UUID）
- 每个 `interview_id` 对应一个独立的 `InterviewState`
- 状态存储到 `SessionStore`（JSON 文件），支持 Web 服务重启后恢复

#### D2 — Gateway API 完整对接

四个 REST API 的实现：
1. `POST /api/v1/interview` — 接收 `jd_path, resume_path` → 调用 `init_interview()` → 返回 `interview_id, question`
2. `POST /api/v1/interview/{id}/talk` — 接收 `answer` → 调用 `judge_and_decide()` + `generate_next_question()` → 返回评判结果 + 下一题
3. `GET /api/v1/interview/{id}` — 从 SessionStore 加载状态 → 返回当前进度、历史轮次
4. `GET /api/v1/interview/{id}/report` — 调用 `FeedbackAgent` 生成报告 → 返回完整报告

#### D3 — Gateway→UI 解耦

- 修改 `web/app.py`：增加 `use_gateway` 配置选项
- 当 `use_gateway=True` 时，Streamlit 通过 HTTP 请求调用 `localhost:8000/api/v1/...`
- 当 `use_gateway=False` 时，保持当前直调模式（开发/调试用）
- 默认 `use_gateway=True`

#### D4 — CLI 历史查询补全

- `main.py` 的 `history` 子命令已定义，但依赖的 `SessionStore` 尚未实现
- 现在实现后即可正常工作

---

## 四、实现顺序

按依赖关系排列：

```
第一步：块 C（SessionStore）— 无依赖，但被 Gateway 和 CLI 依赖
  └── 第二步：块 B（ChromaDB VectorStore）— 独立
        └── 第三步：块 A（MCP Gateway）— 依赖 SessionStore
              └── 第四步：块 D（API + 解耦）— 依赖 A + B + C
```

### Step 1：SessionStore（memory/session_store.py）
- 实现 SessionStore 类
- JSON 文件读写
- 候选人搜索
- 集成到 main.py history 命令

### Step 2：VectorStore（memory/vector_store.py）
- ChromaDB CRUD 封装
- 4 个 Collection 定义
- Embedding 策略
- 降级逻辑

### Step 3：MCP Gateway（mcp_servers/gateway.py）
- FastAPI app + SSE transport
- ServerRegistry 路由
- 鉴权 + 限流中间件
- 4 个 REST API 端点

### Step 4：集成与解耦
- config.py 补充新字段
- web/app.py 增加 Gateway 模式
- 面试存储钩子（supervisor.py 或 UI 层）
- 历史检索集成到出题环节

---

## 五、config.py 新增字段

```python
# MCP Gateway
gateway_api_key: str = field(default_factory=lambda: os.getenv("GATEWAY_API_KEY", ""))
gateway_rate_limit: int = 60  # 每分钟最大请求数

# Embedding
embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))

# ChromaDB
chroma_host: str = field(default_factory=lambda: os.getenv("CHROMA_HOST", "localhost"))
chroma_port: int = int(os.getenv("CHROMA_PORT", "8000"))
chroma_use_http: bool = bool(os.getenv("CHROMA_USE_HTTP", ""))

# Session
session_dir: Path = data_dir / "sessions"

# Feature flags
use_gateway: bool = True  # Web UI 是否通过 Gateway 调用
```

---

## 六、文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `memory/session_store.py` | 会话 JSON 存储 |
| 新增 | `memory/vector_store.py` | ChromaDB 向量存储 |
| 新增 | `mcp_servers/gateway.py` | FastAPI MCP Gateway + REST API |
| 修改 | `config.py` | 新增 8 个配置字段 |
| 修改 | `main.py` | history 命令完善（依赖 SessionStore 就绪） |
| 修改 | `web/app.py` | 可选 Gateway 模式 |
| 修改 | `orchestration/supervisor.py` | 面试结束时触发记忆存储 |
| 修改 | `agents/interviewer.py` | 出题时可选用历史检索 |

---

## 七、验证方式

1. **SessionStore**：执行 `python main.py history`，输出 "暂无面试记录"。完成一次面试后再查，能看到记录
2. **VectorStore**：Python REPL 中 import `memory.vector_store`，CRUD 测试
3. **Gateway**：`python main.py gateway` 启动 → `curl http://localhost:8000/health` → 200 OK
4. **REST API**：`curl -X POST http://localhost:8000/api/v1/interview -H "Authorization: Bearer xxx" -d '{"jd_path":"...", "resume_path":"..."}'`
5. **端到端**：Gateway 模式启动 Streamlit → 上传文件 → 完整面试 → 查看历史
6. **降级**：ChromaDB 未安装或连接失败 → 系统仍然正常运行，不影响面试核心流程
