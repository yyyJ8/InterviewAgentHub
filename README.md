# 🎯 AI 面试官

基于 **LangGraph + FastAPI + MCP + ChromaDB** 的多 Agent 面试系统。

> 上传 JD + 简历 → AI 解析匹配 → 多轮追问面试 → 五维度评分报告，完整闭环。

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-blue)](https://langchain-ai.github.io/langgraph/)
[![Gradio](https://img.shields.io/badge/Gradio-5.0+-orange?logo=gradio)](https://www.gradio.app/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-0.5+-brightgreen)](https://www.trychroma.com/)

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（复制模板并填入 API Key）
cp .env.example .env
# 编辑 .env → 填入 DEEPSEEK_API_KEY（LLM）和 SILICONFLOW_API_KEY（Embedding）

# 3. Embedding 默认走 SiliconFlow API 的 BAAI/bge-m3（1024 维，免费，无需下载任何模型）
#    本地模型只是 API 不可用时的降级兜底，可选安装：
# HF_ENDPOINT=https://hf-mirror.com hf download BAAI/bge-base-zh-v1.5 --local-dir D:/model/bge-base-zh-v1.5
# 提示：SiliconFlow 免费模型需账户完成实名认证且余额非负，否则返回 402 (code 30001)

# 4. 启动
python main.py web

# 浏览器打开 http://localhost:7860   ← Gradio Web UI
# API 文档 http://localhost:8000/docs ← FastAPI Swagger
```

---

## 架构

```
浏览器 (Gradio :7860)     外部客户端 (curl / 其他服务)
      │                           │
      │ async def 直调             │ REST API
      │ (原生 async, 无事件循环包装) │ Bearer Token 鉴权
      │                           │
      └───────────┬───────────────┘
                  │
           FastAPI Gateway (:8000)
           ├── 鉴权中间件 (Bearer Token, dev 环境自动关闭)
           ├── 限流中间件 (令牌桶, 60 req/min)
           ├── 熔断保护 (CircuitBreaker, 三态模型)
           ├── /api/v1/*     面试 REST API (CRUD + judge + SSE 出题)
           ├── /mcp/         MCP 协议端点 (Streamable HTTP)
           ├── /mcp/{tool}   运行时直调 (非协议)
           └── /health       健康检查 + 熔断器状态
                  │
           MCP 聚合层 (mcp_aggregator: 3 Server → 单一 MCP 端点)
                  │
           MCP Server 注册中心
           ├── JD Server (parse_jd)
           ├── Resume Server (parse_resume)
           └── Question Bank Server (generate / search / difficulty / categories)
                  │
           ──── orchestration/supervisor (LangGraph StateGraph) ────
           │                                                        │
      Agent 层                                                匹配层
      ├── JD 解析 Agent                                       matcher.py
      ├── 简历分析 Agent                                      规则 + 语义
      ├── 面试官 Agent (多轮追问)                              (bge-m3 embedding)
      └── 反馈 Agent (5 维度报告)
                  │
           记忆层
           ├── SessionStore (JSON → 即将迁移 SQLite)
           └── VectorStore (ChromaDB, 2 Collections, 优雅降级)
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| LLM | deepseek-flash（DeepSeek-V4.1-Flash） | OpenAI 兼容 API，指数退避重试 + 流式输出 |
| 编排 | LangGraph | StateGraph + 条件边 + MemorySaver Checkpoint |
| Embedding | BAAI/bge-m3 | 1024 维，SiliconFlow API（`https://api.siliconflow.cn/v1`，免费） |
| 向量库 | ChromaDB | 本地持久化，2 个 Collection，优雅降级 |
| 后端 | FastAPI | Gateway + REST API + MCP Streamable HTTP |
| 前端 | Gradio 5 | 独立端口，原生 async，流式打字机效果 |
| MCP | FastMCP SDK | 3 个独立 Server，聚合为单一 MCP 端点 |
| 文件解析 | pdfplumber + python-docx | PDF/DOCX/TXT 全格式，中文友好错误提示 |
| 存储 | JSON (→ SQLite) | 会话持久化，每场面试一个文件 |

---

## 核心特性

### 面试引擎

| 特性 | 说明 |
|------|------|
| **JD ↔ 简历交叉匹配** | 按技能缺口排序：有项目经验 → 有技能无项目 → 完全缺口 → 加分项 |
| **多轮追问策略** | deepen（深挖技术细节）→ clarify（引导澄清）→ switch（换下一个技能） |
| **五维度评分** | 技术深度 / 问题解决 / 沟通表达 / 学习能力 / 项目经验 |
| **智能终止** | 连续空回答 / 达到最大轮次 / 技能全部覆盖 → 自动生成报告 |
| **流式出题** | 面试题逐字生成，打字机效果，不再干等 |
| **弹性难度** | 从 basic → intermediate → advanced → deep，根据回答质量自动升降 |

### 工程能力

| 特性 | 说明 |
|------|------|
| **指数退避重试** | LLM 调用失败自动重试（1s → 2s → 4s），最多 3 次 |
| **熔断保护** | 三态模型（CLOSED / OPEN / HALF-OPEN），连续 3 次失败自动熔断，30s 冷却后半开探测 |
| **令牌桶限流** | 60 req/min，健康检查和静态资源自动豁免 |
| **优雅降级** | ChromaDB 不可用 → 降级为无记忆模式，核心面试流程不受影响 |
| **环境区分** | `ENV=dev` 自动关闭鉴权 + DEBUG 日志；`ENV=prod` 全开 |
| **Prompt 校验** | 模板加载时提取变量，调用时检测缺失/多余参数，第一时间报错 |
| **原生 async** | 全链路 async/await，无事件循环包装反模式 |

---

## CLI

```bash
python main.py web                        # 启动全部服务（Gradio + Gateway）
python main.py gateway                    # 仅启动 API Gateway
python main.py history                    # 查看所有历史面试
python main.py history -c 张三            # 按候选人姓名搜索
python main.py seed --dry-run             # 预览：向量库里有哪些题可沉淀进题库
python main.py seed                       # 把向量库积累的题目沉淀进种子题库
python main.py clean_memory               # 清理会话与向量记忆（交互确认）
python main.py clean_memory -y            # 同上，跳过确认
```

### 题库从哪来

题库 `data/seed_questions.json` 有**两个来源**，并且会随面试增长：

| 来源 | 说明 |
|------|------|
| 初始种子题 | 仓库自带，覆盖 Python / Django / MySQL / Redis / Go 等 |
| **面试沉淀** | 面试结束时自动把本场出过的题写回题库（去重 + 质量过滤），也可用 `python main.py seed` 把向量库里的历史积累一次性导入 |

质量门槛（不达标不入库）：题干 ≥15 字符、作答要点 ≥2 条且每条 ≥5 字符、必须有 skill；
去重按「忽略空白与大小写」比对题干。

> 说明：`seed_questions.json` 是题库的唯一真相来源；向量库 `ih_question_bank`
> 只是「已出题目」的检索索引（用于语义检索避免重复出题），两者职责不同。

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 + 熔断器状态 |
| `POST` | `/api/v1/interview` | 创建面试会话（上传 JD + 简历路径） |
| `GET` | `/api/v1/interview/{id}/stream-question` | SSE 流式出题 |
| `POST` | `/api/v1/interview/{id}/judge` | 提交回答，返回评判 + 进度 |
| `POST` | `/api/v1/interview/{id}/talk` | 提交回答 + 出题（已弃用，请用 judge + stream-question） |
| `GET` | `/api/v1/interview/{id}` | 获取会话状态 |
| `GET` | `/api/v1/interview/{id}/report` | 获取面试报告 |
| `POST` | `/mcp/` | **MCP 协议端点**（Streamable HTTP，见下节） |
| `POST` | `/mcp/{tool_name}` | 运行时直调（私有约定，非 MCP 协议） |

> 鉴权：`Authorization: Bearer <GATEWAY_API_KEY>`（dev 环境自动放行）

---

## MCP 接入

Gateway 是一个**标准 MCP Server**，通过 FastMCP 原生 Streamable HTTP transport 暴露
全部 6 个工具，任何兼容 MCP 的客户端（Claude Desktop、Cursor、自研客户端）都能直接连接。

**端点**：`http://127.0.0.1:8000/mcp/`

已实测：官方 MCP Python 客户端 SDK 连接后协议版本协商到 `2025-11-25`，
`tools/list` 返回 6 个工具并带完整 `inputSchema`，`tools/call` 可正常执行。

| 工具 | 来源 Server | 必填参数 |
|------|------------|---------|
| `parse_jd` | jd-server | `text` |
| `parse_resume` | resume-server | `text` |
| `generate_questions` | question-bank-server | `jd_json`, `skill` |
| `search_seed_bank` | question-bank-server | — |
| `add_to_seed_bank` | question-bank-server | `question_json` |
| `get_seed_bank_stats` | question-bank-server | — |

**客户端配置示例**（以 Cursor / Claude Desktop 的 `mcpServers` 为例）：

```json
{
  "mcpServers": {
    "interview-hub": {
      "url": "http://127.0.0.1:8000/mcp/"
    }
  }
}
```

先用 `python main.py gateway` 或 `python main.py web` 启动服务，再让客户端连接。

> 实现细节见 `mcp_servers/mcp_aggregator.py`：三个独立 FastMCP Server 的工具被聚合
> 到单个实例，便于客户端一次握手即可看到全部工具。

---

## 配置参考

全部配置项见 `.env.example`，核心项：

```bash
ENV=dev                                    # dev | prod
DEEPSEEK_API_KEY=sk-your-key-here          # DeepSeek API Key（LLM）
DEEPSEEK_BASE_URL=https://api.deepseek.com # API 地址
LLM_MODEL=deepseek-flash                   # 模型 API 标识（DeepSeek-V4.1-Flash）
EMBEDDING_PROVIDER=api                     # api（默认，走 SiliconFlow）| local（强制本地）
EMBEDDING_MODEL=BAAI/bge-m3                # 必须写全 id，写成 bge-m3 会返回 400
SILICONFLOW_API_KEY=sk-your-key-here       # SiliconFlow API Key（Embedding）
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1  # Embedding 接口地址
LOCAL_EMBEDDING_MODEL=D:/model/bge-base-zh-v1.5     # API 不可用时的本地兜底模型
GATEWAY_API_KEY=dev-key-change-me          # Gateway 鉴权 Token
LOG_LEVEL=INFO                             # DEBUG | INFO | WARNING | ERROR
```

> `BAAI/bge-m3` 在 SiliconFlow 上免费（0 元/K tokens），输出 1024 维，单条文本上限 8192 tokens；
> 免费模型仍需账户完成实名认证且余额非负，否则返回 `402 (code 30001)`。
> 本地 `bge-base-zh-v1.5`（768 维）仅作 API 不可用时的降级兜底；它与 API 向量维度不同，
> 换模型后旧向量不可用，代码会自动检测维度冲突并重建 Collection。

---

## 项目结构

```
├── agents/              # 4 个 Agent（JD / 简历 / 面试官 / 反馈）
│   └── base.py          #   Agent 基类（重试 / JSON 解析 / 流式）
├── orchestration/       # LangGraph 编排
│   ├── supervisor.py    #   StateGraph + 条件路由 + 节点函数
│   └── matcher.py       #   JD ↔ 简历技能交叉匹配
├── mcp_servers/         # MCP Server + Gateway
│   ├── gateway.py       #   FastAPI（鉴权 / 限流 / 熔断 / MCP 协议端点）
│   ├── mcp_aggregator.py#   把 3 个 FastMCP Server 聚合为单一端点
│   ├── jd_server.py     #   JD 解析 Server
│   ├── resume_server.py #   简历解析 Server
│   └── question_bank_server.py  # 题库 Server
├── memory/              # 记忆系统
│   ├── session_store.py #   会话持久化（JSON）
│   └── vector_store.py  #   ChromaDB 向量库（2 Collections, 降级）
├── web/
│   └── app.py           # Gradio Web UI（三步流程，流式出题）
├── models/              # Pydantic 数据模型
├── tools/               # PDF / DOCX / TXT 文件解析
├── prompts/             # 7 个 Prompt 模板（变量校验）
├── data/                # 种子题库 + Demo 数据 + 运行时数据
├── docs/                # 项目文档 + 优化路线图
├── tests/               # 单元测试 + fixtures
├── config.py            # 全局配置（dataclass 单例）
└── main.py              # CLI 入口（Typer）
```

---

## 开发阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | MCP Server + 基础 Agent + 简单问答 | ✅ |
| Phase 2 | 多轮面试 + 追问策略 + 反馈报告 | ✅ |
| Phase 3 | MCP Gateway + ChromaDB 长期记忆 | ✅ |
| Phase 4 | Gradio Web UI + 工程化 + Demo 数据 | ✅ |
| Phase 5 | 原生 async + 统一状态机 + 流式出题 + 环境区分 + bge-m3 Embedding（SiliconFlow API） | ✅ |

> 下一步详见 [docs/optimization-roadmap.md](docs/optimization-roadmap.md)

---

## Demo

```bash
# Demo 数据位于 data/demo/
├── Agent开发实习生_JD.txt   # 岗位 JD（Agent 开发实习）
├── Java开发实习生_JD.txt    # 岗位 JD（Java 开发实习）
├── 张明远_简历.txt          # 候选人 A（履历型，TXT）
├── 江豪-后端.pdf            # 候选人 B（实战型，PDF）
└── DEMO_剧本.md             # 演示流程 + 预设回答
```

---

## 测试

```bash
pytest tests/ -v          # 运行全部测试
pytest tests/ --cov       # 带覆盖率
```

---

## 文档

- [CLAUDE.md](docs/CLAUDE.md) — 项目总览 + 技术栈 + 开发阶段
- [blog-ai-interviewer.md](docs/blog-ai-interviewer.md) — 全栈实战文章（对外展示）
- [optimization-roadmap.md](docs/optimization-roadmap.md) — 优化升级方案 + 远期路线图
