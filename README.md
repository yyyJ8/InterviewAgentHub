# 🎯 AI 面试官

基于 **LangGraph + FastAPI + MCP** 的多 Agent 面试系统。

> 从 JD 解析 → 简历匹配 → 多轮追问 → 综合评分报告，完整闭环。

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（编辑 .env）
# DEEPSEEK_API_KEY=your-key

# 3. 启动
python main.py web
# 浏览器打开 http://localhost:7860   ← Gradio Web UI
# API 文档 http://localhost:8000/docs ← FastAPI Swagger
```

---

## 架构

```
浏览器 (Gradio :7860)     外部客户端
      ↓                        ↓
   Agent 直调           REST API (:8000)
      ↓                        ↓
      └──────────┬─────────────┘
                 ↓
          FastAPI Gateway
          ├── 鉴权中间件 (Bearer Token)
          ├── 限流中间件 (令牌桶, 60/min)
          ├── 熔断保护 (CircuitBreaker, 3 态)
          ├── /api/v1/*    面试 REST API
          ├── /mcp/*        MCP 工具调用
          └── /health      健康检查 + 熔断器状态
                 ↓
          MCP Server 注册中心
          ├── JD Server (parse_jd)
          ├── Resume Server (parse_resume)
          └── Question Bank Server (4 tools)
                 ↓
          Agent 层 (指数退避重试 + 流式出题)
          ├── JD 解析 Agent
          ├── 简历分析 Agent
          ├── 面试官 Agent（多轮追问 + 策略切换）
          └── 反馈 Agent（5 维度评分 + 报告）
                 ↓
          记忆层
          ├── SessionStore (JSON 会话持久化)
          └── VectorStore (ChromaDB 长期记忆, 优雅降级)
```

---

## CLI

```bash
python main.py web                        # 启动全部服务
python main.py gateway                    # 仅启动 API Gateway
python main.py history                    # 查看历史面试
python main.py history -c 张三            # 按候选人搜索
```

---

## 项目结构

```
├── agents/           # 4 个 Agent（JD/简历/面试官/反馈）
├── orchestration/    # LangGraph 编排 + 技能匹配
├── mcp_servers/      # MCP Server + Gateway（鉴权/限流/熔断）
├── memory/           # SessionStore + VectorStore（降级策略）
├── web/              # Gradio Web UI（generator 流式进度）
├── models/           # Pydantic 数据模型
├── tools/            # PDF/DOCX 文件解析（友好错误提示）
├── prompts/          # Prompt 模板（支持 .format() 变量）
├── data/             # 种子题库 + Demo 数据 + 运行时数据
├── docs/             # 阶段总结 + 路线图
├── tests/            # 24 个单元测试
├── config.py         # 全局配置
└── main.py           # CLI 入口
```

---

## 技术栈

| 技术 | 用途 |
|------|------|
| Python 3.12 | 开发语言 |
| DeepSeek API | LLM（指数退避重试） |
| LangGraph | Agent 编排 |
| FastAPI | Gateway + REST API |
| Gradio 5 | Web UI（独立端口，generator 流式进度） |
| ChromaDB | 长期记忆（优雅降级） |
| MCP SDK | MCP 协议 |

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 多轮追问 | 4 种策略：出题 → deepen（深挖）→ clarify（澄清）→ switch（换技能） |
| 流式进度 | `on_start` generator 分步 yield，用户不再白屏干等 |
| 指数退避重试 | LLM 调用失败后 1s → 2s → 4s 自动重试 |
| 熔断保护 | CircuitBreaker 三态模型，连续失败 3 次自动熔断，30s 冷却后半开探测 |
| 令牌桶限流 | 60 req/min，静态资源自动豁免 |
| 友好错误提示 | 空文件/坏文件/格式不支持 → 中文 ParseError |
| 优雅降级 | ChromaDB 不可用时自动切纯内存模式，不影响核心流程 |

---

## 开发阶段

| 阶段 | 状态 |
|------|------|
| Phase 1 — MVP 核心链路 | ✅ |
| Phase 2 — 多轮面试 | ✅ |
| Phase 3 — Gateway + 长期记忆 | ✅ |
| Phase 4 — 工程化 + Demo | ✅ |

详见 [docs/ROADMAP.md](docs/ROADMAP.md)

---

## Demo

```bash
# Demo 数据位于 data/demo/
├── Agent开发实习生_JD.txt   # 岗位 JD（Agent 开发实习）
├── 张明远_简历.txt          # 候选人 A（履历型）
├── 王一龙_简历.docx         # 候选人 B（实战型）
└── DEMO_剧本.md             # 演示流程 + 预设回答
```
