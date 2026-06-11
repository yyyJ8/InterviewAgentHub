# LineageAgent 项目

## 项目总览

高级 Agent 开发项目，定位为面试作品：

**AI 面试官** — 基于 LangGraph + MCP 的多 Agent 编排系统，展示应用层能力。

> 后续计划：**迷你 Agent 框架** — 从零实现 Agent 核心循环，展示底层原理（下一项目再做）

---

## 技术栈

| 技术 | 用途 |
|---|---|
| Python 3.12 | 开发语言 |
| DeepSeek V4 Pro | LLM（定价极低，随意调试） |
| LangGraph | AI 面试官项目的编排框架 |
| MCP SDK | 模型上下文协议实现 |
| ChromaDB | 长期记忆存储（本地向量库） |
| 本地虚拟机 | 部署环境，面试时当场 demo |

---

## 项目：AI 面试官

### 定位

基于 LangGraph + MCP 的多 Agent 面试系统。

### 架构

```
请求
  ↓
MCP Gateway（注册 / 鉴权 / 限流 / 路由）
  ↓
JD Server → 简历 Server → 题库 Server  ← 独立 MCP Server
  ↓
Supervisor（LangGraph 编排）
  ├── JD 解析 Agent
  ├── 简历分析 Agent
  ├── 面试官 Agent（多轮追问）
  └── 反馈 Agent（评分 + 报告）
```

### 核心实现思路

1. **DAG 编排**：强依赖串行（定位→修复→验证），弱依赖并行（多个角度同时研究）
2. **多轮对话**：面试官根据候选人回答动态出下一题，状态管理
3. **长期记忆**：向量库存储面试记录，下次面试可参考历史
4. **MCP Gateway**：统一管理多个 Server，加鉴权和限流

### 面试价值

"从 JD 解析到简历匹配到面试到反馈，完整闭环"——展示你能用框架做出完整的业务系统。

---

## 开发顺序

```
Phase 1（第 1 周）：MCP Server + 基础 Agent
  - 搭建 MCP Server（JD、简历、题库）
  - Supervisor + 2 个子 Agent
  - 简单问→答流程跑通

Phase 2（第 1-2 周）：多轮面试流程
  - 面试官 Agent 多轮追问
  - 对话状态管理
  - 全部 4 个子 Agent 完成

Phase 3（第 2-3 周）：MCP Gateway + 长期记忆
  - MCP Gateway（注册、鉴权、限流）
  - ChromaDB 长期记忆存储
  - 历史面试参考能力

Phase 4（第 3 周）：工程化 + Demo 准备
  - Agent 行为测试
  - Docker + 虚拟机部署
  - Demo 脚本和面试话术准备
```

---

## 后续项目：迷你 Agent 框架

> 完成 AI 面试官后开启，从零手写 Agent 核心循环，不依赖 LangGraph 等框架。

### 定位

展示对 Agent 原理的底层理解。与 AI 面试官互补：一个偏应用（做业务），一个偏底层（写框架）。

### 目录结构

```
mini_framework/
├── core/
│   ├── agent.py          # Agent 基类（ReAct 循环）
│   ├── llm.py            # LLM 调用封装
│   ├── tool_registry.py  # 插件式工具注册（装饰器模式）
│   ├── memory.py         # 短期（滑动窗口）+ 长期（向量检索）
│   └── orchestrator.py   # 多 Agent 编排器（串行/并行/条件路由）
├── examples/
│   └── calculator_agent.py
└── tests/
```

### 核心实现思路

1. **ReAct 循环**：thought → action → observation → thought... 直到 final answer
2. **工具注册**：装饰器模式注册工具，支持动态加载
3. **记忆系统**：短期（滑动窗口）+ 长期（ChromaDB 向量检索）
4. **编排器**：支持串行、并行、条件路由三种模式

### 面试价值

"我从零写了一个 agent 框架，LangGraph 能做的我都能做"——展示你不是框架套壳使用者。

### 启动命令

```bash
# 新项目初始化
mkdir -p mini_framework/{core,examples,tests}
touch mini_framework/core/__init__.py
touch mini_framework/examples/__init__.py
touch mini_framework/tests/__init__.py
```

---

## 记忆索引

- 暂无（后续面试官项目开发过程中的关键决策/踩坑记录存放于此）
- requirements.txt 位于项目根目录，记录了项目二的全部依赖
