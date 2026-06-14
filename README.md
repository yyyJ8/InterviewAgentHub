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
# 浏览器打开 http://localhost:8000/ui
```

---

## 架构

```
浏览器 (Gradio UI)
  ↓
FastAPI Gateway (:8000)
  ├── /ui              Web UI
  ├── /api/v1/*        REST API
  └── /mcp/*           MCP 工具调用
  ↓
Supervisor (LangGraph 编排)
  ├── JD 解析 Agent
  ├── 简历分析 Agent
  ├── 面试官 Agent（多轮追问）
  └── 反馈 Agent（评分 + 报告）
  ↓
记忆层
  ├── SessionStore (JSON 会话持久化)
  └── VectorStore (ChromaDB 长期记忆)
```

---

## CLI

```bash
python main.py web           # 启动服务
python main.py history       # 查看历史面试
python main.py history -c 张三  # 按候选人搜索
```

---

## 项目结构

```
├── agents/           # 4 个 Agent（JD/简历/面试官/反馈）
├── orchestration/    # LangGraph 编排 + 技能匹配
├── mcp_servers/      # MCP Server + Gateway
├── memory/           # SessionStore + VectorStore
├── web/              # Gradio Web UI
├── models/           # Pydantic 数据模型
├── tools/            # PDF/DOCX 文件解析
├── prompts/          # Prompt 模板
├── data/             # 种子题库 + 运行时数据
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
| DeepSeek V4 | LLM |
| LangGraph | Agent 编排 |
| FastAPI | Gateway + API |
| Gradio | Web UI |
| ChromaDB | 长期记忆 |
| MCP SDK | MCP 协议 |

---

## 开发阶段

| 阶段 | 状态 |
|------|------|
| Phase 1 — MVP 核心链路 | ✅ |
| Phase 2 — 多轮面试 | ✅ |
| Phase 3 — Gateway + 长期记忆 | ✅ |
| Phase 4 — 工程化 + Demo | 🔄 |

详见 [docs/ROADMAP.md](docs/ROADMAP.md)
