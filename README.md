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
pip install -r requirements.txt
cp .env.example .env          # 填入 DEEPSEEK_API_KEY 与 SILICONFLOW_API_KEY
python main.py web            # UI: http://localhost:7860   API: http://localhost:8000/docs
```

Embedding 默认走 SiliconFlow 的 `BAAI/bge-m3`（免费），无需下载模型。

---

## 架构

```
浏览器 (Gradio :7860)     外部客户端 (curl / 其他服务)
      │                           │
      └───────────┬───────────────┘
                  │
           FastAPI Gateway (:8000)
           ├── 鉴权 / 限流 / 熔断
           ├── /api/v1/*     面试 REST API（含 SSE 流式出题）
           ├── /mcp/         MCP 协议端点（Streamable HTTP）
           └── /health       健康检查 + 熔断器状态
                  │
           MCP 聚合层 ── 3 个 Server 的 6 个工具 → 单一 MCP 端点
                  │
           orchestration/supervisor（LangGraph StateGraph）
           ├── Agent 层：JD 解析 / 简历分析 / 面试官（多轮追问）/ 反馈
           └── 匹配层：matcher.py（规则排序 + bge-m3 语义检索）
                  │
           记忆层：SessionStore（JSON）+ VectorStore（ChromaDB, 2 Collections）
```

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| LLM | deepseek-flash（DeepSeek-V4.1-Flash） | OpenAI 兼容 API，指数退避重试 + 流式输出 |
| 编排 | LangGraph | StateGraph + 条件边 + MemorySaver Checkpoint |
| Embedding | BAAI/bge-m3 | 1024 维，SiliconFlow API（免费） |
| 向量库 | ChromaDB | 本地持久化，2 个 Collection，优雅降级 |
| 后端 | FastAPI | Gateway + REST API + MCP Streamable HTTP |
| 前端 | Gradio 5 | 独立端口，原生 async，流式打字机效果 |
| MCP | FastMCP SDK | 3 个独立 Server，聚合为单一 MCP 端点 |
| 文件解析 | pdfplumber + python-docx | PDF/DOCX/TXT 全格式 |
| 存储 | JSON | 会话持久化，每场面试一个文件 |

---

## 核心特性

**面试引擎**

| 特性 | 说明 |
|------|------|
| JD ↔ 简历交叉匹配 | 按技能缺口排序：有项目经验 → 有技能无项目 → 完全缺口 → 加分项 |
| 多轮追问策略 | deepen（深挖）→ clarify（澄清）→ switch（换技能） |
| 五维度评分 | 技术深度 / 问题解决 / 沟通表达 / 学习能力 / 项目经验 |
| 智能终止 | 连续空回答 / 达到最大轮次 / 技能全部覆盖 → 自动生成报告 |
| 流式出题 | 面试题逐字生成，打字机效果 |
| 弹性难度 | basic → intermediate → advanced → deep，按回答质量自动升降 |

**工程能力**

| 特性 | 说明 |
|------|------|
| 指数退避重试 | LLM 调用失败自动重试（1s → 2s → 4s），最多 3 次 |
| 熔断保护 | 三态模型，连续 3 次失败熔断，30s 冷却后半开探测 |
| 令牌桶限流 | 60 req/min，健康检查与静态资源豁免 |
| 优雅降级 | ChromaDB 不可用 → 无记忆模式，核心流程不受影响 |
| 环境区分 | `ENV=dev` 关闭鉴权 + DEBUG 日志；`ENV=prod` 全开 |
| Prompt 校验 | 模板加载时提取变量，调用时检测缺失/多余参数 |
| 原生 async | 全链路 async/await，无事件循环包装反模式 |

---

## CLI

```bash
python main.py web              # 启动全部服务（Gradio + Gateway）
python main.py gateway          # 仅启动 API Gateway
python main.py history          # 查看历史面试（-c 姓名 / -i id / -l 最近）
python main.py seed             # 把向量库积累的题目沉淀进种子题库
python main.py clean-memory -y  # 清理会话与向量记忆
```

**题库从哪来**：`data/seed_questions.json` 是唯一真相来源，有两个入口 —— 仓库自带的
初始种子题，以及**面试结束时自动沉淀**本场出过的题（去重 + 质量过滤：题干 ≥15 字符、
作答要点 ≥2 条）。向量库 `ih_question_bank` 只是「已出题目」的检索索引，用于避免重复出题。

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/interview` | 创建面试会话 |
| `GET` | `/api/v1/interview/{id}/stream-question` | SSE 流式出题 |
| `POST` | `/api/v1/interview/{id}/judge` | 提交回答，返回评判 + 进度 |
| `GET` | `/api/v1/interview/{id}` | 会话状态 |
| `GET` | `/api/v1/interview/{id}/report` | 面试报告 |
| `POST` | `/mcp/` | MCP 协议端点（Streamable HTTP） |
| `GET` | `/health` | 健康检查 + 熔断器状态 |

> 鉴权：`Authorization: Bearer <GATEWAY_API_KEY>`（dev 环境自动放行）

---

## MCP 接入

Gateway 是标准 MCP Server，通过 FastMCP 原生 Streamable HTTP transport 暴露 6 个工具：
`parse_jd`、`parse_resume`、`generate_questions`、`search_seed_bank`、`add_to_seed_bank`、`get_seed_bank_stats`。

**端点**：`http://127.0.0.1:8000/mcp/`

```json
{ "mcpServers": { "interview-hub": { "url": "http://127.0.0.1:8000/mcp/" } } }
```

先用 `python main.py gateway` 启动服务，再让客户端连接。已实测官方 MCP Python 客户端
连接后协议版本协商到 `2025-11-25`，`tools/list` 返回 6 个工具并带完整 `inputSchema`。

---

## 配置

全部配置项见 `.env.example`，核心项：

```bash
ENV=dev                                    # dev | prod
DEEPSEEK_API_KEY=sk-your-key-here          # LLM
LLM_MODEL=deepseek-flash
SILICONFLOW_API_KEY=sk-your-key-here       # Embedding
EMBEDDING_MODEL=BAAI/bge-m3                # 必须写全 id，写成 bge-m3 会 400
GATEWAY_API_KEY=dev-key-change-me          # Gateway 鉴权 Token
LOG_LEVEL=INFO
```

> SiliconFlow 免费模型需账户完成实名认证且余额非负，否则返回 `402`。

---

## 项目结构

```
├── agents/              # 4 个 Agent（JD / 简历 / 面试官 / 反馈）
├── orchestration/       # LangGraph 编排（supervisor.py + matcher.py）
├── mcp_servers/         # 3 个 MCP Server + Gateway + 聚合层
├── memory/              # 会话持久化（JSON）+ 向量库（ChromaDB）
├── web/                 # Gradio UI
├── models/              # Pydantic 数据模型
├── tools/               # 文件解析 + embedding 验收脚本
├── prompts/             # 7 个 Prompt 模板（变量校验）
├── data/                # 种子题库 + 运行时数据
├── docs/                # 技术手册 + 系统设计
├── tests/               # 测试
├── config.py            # 全局配置
└── main.py              # CLI 入口
```

---

## 测试

```bash
pytest tests/ -v
```

---

## 文档

- [技术手册.md](docs/技术手册.md) — 全栈实现手册（数据模型 → 编排 → MCP → UI 的完整细节）
- [结构化系统设计-完整版.md](docs/结构化系统设计-完整版.md) — 数据流视角的系统设计（E-R 图 / 表设计 / PAD 图 / UI 状态图）
