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
# 编辑 .env → 填入 DEEPSEEK_API_KEY

# 3. （可选）下载本地 Embedding 模型（中文效果更好，跳过则使用 HuggingFace 在线模型）
# HF_ENDPOINT=https://hf-mirror.com hf download BAAI/bge-base-zh-v1.5 --local-dir D:/model/bge-base-zh-v1.5

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
           ├── /api/v1/*     面试 REST API (CRUD + talk)
           ├── /mcp/*         MCP 工具调用
           └── /health       健康检查 + 熔断器状态
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
      ├── 面试官 Agent (多轮追问)                              (BGE embedding)
      └── 反馈 Agent (5 维度报告)
                  │
           记忆层
           ├── SessionStore (JSON → 即将迁移 SQLite)
           └── VectorStore (ChromaDB, 4 Collections, 优雅降级)
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| LLM | DeepSeek V4 Pro | OpenAI 兼容 API，指数退避重试 + 流式输出 |
| 编排 | LangGraph | StateGraph + 条件边 + MemorySaver Checkpoint |
| Embedding | BGE-base-zh-v1.5 | 768 维，中文 SOTA，本地部署零费用 |
| 向量库 | ChromaDB | 本地持久化，4 个 Collection，优雅降级 |
| 后端 | FastAPI | Gateway + REST API + MCP SSE |
| 前端 | Gradio 5 | 独立端口，原生 async，流式打字机效果 |
| MCP | FastMCP SDK | 3 个独立 Server，按工具名路由 |
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
```

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 + 熔断器状态 |
| `POST` | `/api/v1/interview` | 创建面试会话（上传 JD + 简历路径） |
| `POST` | `/api/v1/interview/{id}/talk` | 提交回答，返回评判 + 下一题 |
| `GET` | `/api/v1/interview/{id}` | 获取会话状态 |
| `GET` | `/api/v1/interview/{id}/report` | 获取面试报告 |
| `POST` | `/mcp/{tool_name}` | MCP 工具通用调用 |
| `GET` | `/mcp/sse` | MCP SSE transport |

> 鉴权：`Authorization: Bearer <GATEWAY_API_KEY>`（dev 环境自动放行）

---

## 配置参考

全部配置项见 `.env.example`，核心项：

```bash
ENV=dev                                    # dev | prod
DEEPSEEK_API_KEY=sk-your-key-here          # DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com # API 地址
LLM_MODEL=deepseek-v4-pro                  # 模型名
EMBEDDING_MODEL=D:/model/bge-base-zh-v1.5  # 本地路径或 HF 模型名
GATEWAY_API_KEY=dev-key-change-me          # Gateway 鉴权 Token
LOG_LEVEL=INFO                             # DEBUG | INFO | WARNING | ERROR
```

---

## 项目结构

```
├── agents/              # 4 个 Agent（JD / 简历 / 面试官 / 反馈）
│   └── base.py          #   Agent 基类（重试 / JSON 解析 / 流式）
├── orchestration/       # LangGraph 编排
│   ├── supervisor.py    #   StateGraph + 条件路由 + 节点函数
│   └── matcher.py       #   JD ↔ 简历技能交叉匹配
├── mcp_servers/         # MCP Server + Gateway
│   └── gateway.py       #   FastAPI（鉴权 / 限流 / 熔断 / 路由）
├── memory/              # 记忆系统
│   ├── session_store.py #   会话持久化（JSON）
│   └── vector_store.py  #   ChromaDB 向量库（4 Collections, 降级）
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
| Phase 5 | 原生 async + 统一状态机 + 流式出题 + 环境区分 + BGE Embedding | ✅ |

> 下一步详见 [docs/optimization-roadmap.md](docs/optimization-roadmap.md)

---

## Demo

```bash
# Demo 数据位于 data/demo/
├── Agent开发实习生_JD.txt   # 岗位 JD（Agent 开发实习）
├── 张明远_简历.txt          # 候选人 A（履历型）
├── 王一龙_简历.docx         # 候选人 B（实战型）
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
