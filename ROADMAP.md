# AI 面试官 — 实现路线图

> 基于 [DESIGN.md](DESIGN.md) 制定的详细实施计划，包含时间线、任务分解、依赖关系和里程碑。

---

## 总览

| 阶段 | 时间 | 目标 | 产出 |
|------|------|------|------|
| **Phase 1** | 第 1-2 周 | MVP 跑通核心链路 | CLI + Web UI 最小可用版本 |
| **Phase 2** | 第 3-4 周 | 多轮面试流程 | 完整的面试官 Agent + 状态机 |
| **Phase 3** | 第 5-6 周 | MCP Gateway + 长期记忆 | 生产级 Gateway + ChromaDB 集成 |
| **Phase 4** | 第 7-8 周 | 工程化 + Demo 准备 | Docker 部署 + 面试话术 + 演示 |

---

## 依赖关系图

```
Phase 1 ─────────────────────────────────────────────────
  │
  ├── 1.1 项目脚手架 ────────────────────────────────────
  ├── 1.2 文件解析管道 ───→ 1.3 JD 解析 Agent ──┐
  │                          └── 1.4 简历分析 Agent ─┤
  │                                                  ↓
  │                                   1.5 交叉匹配 + 出题 ──→ 1.6 单轮问答 ──→ 1.7 Web UI
  │
Phase 2 ─────────────────────────────────────────────────
  │
  ├── 2.1 多轮状态机 ──→ 2.2 追问策略 ──→ 2.3 题库 Server ──→ 2.4 反馈 Agent
  │
Phase 3 ─────────────────────────────────────────────────
  │
  ├── 3.1 MCP Gateway ──→ 3.2 ChromaDB 集成 ──→ 3.3 API 接口
  │
Phase 4 ─────────────────────────────────────────────────
  │
  ├── 4.1 错误处理 / 重试 ──→ 4.2 流式输出 ──→ 4.3 Docker 部署 ──→ 4.4 Demo 准备
```

---

## Phase 1：MVP — 跑通核心链路（第 1-2 周）

### 目标

从零搭建项目骨架，跑通「上传文件 → 解析 → 出题 → 回答 → 评分」最小闭环。

### 1.1 项目脚手架（Day 1-2）

| 任务 | 详情 | 产出 |
|------|------|------|
| 1.1.1 创建目录结构 | 按 DESIGN.md §10 创建 `interview_hub/` 完整目录 | `interview_hub/` 骨架 |
| 1.1.2 依赖安装 | `pip install -r requirements.txt`，验证可导入 | 运行环境就绪 |
| 1.1.3 配置管理 | 实现 `config.py`（API Key / DB 路径 / 日志级别等） | `config.py` |
| 1.1.4 LLM 封装 | 实现 `models/llm.py`（DeepSeek API 调用，支持流式） | `models/llm.py` |
| 1.1.5 Pydantic 模型 | 实现 `models/jd.py`、`models/resume.py`、`models/question.py`、`models/interview.py` | 核心数据模型 |
| 1.1.6 CLI 入口 | 实现 `main.py`（typer，`main.py web` / `gateway` / `history` 三个子命令） | `main.py` |

**依赖**：无  
**验证方式**：`python main.py --help` 正常输出三个子命令

### 1.2 文件解析管道（Day 3-4）

| 任务 | 详情 | 产出 |
|------|------|------|
| 1.2.1 PDF 解析 | `tools/pdf_parser.py` — 用 pdfplumber 提取文本 + 基础元数据 | `tools/pdf_parser.py` |
| 1.2.2 DOCX 解析 | `tools/docx_parser.py` — 用 python-docx 提取文本 | `tools/docx_parser.py` |
| 1.2.3 文本清洗 | `tools/text_cleaner.py` — 多余空白、特殊字符清理 | `tools/text_cleaner.py` |
| 1.2.4 统一解析入口 | 统一函数 `parse_file(path) -> str`，自动根据扩展名分发 | 文件解析完成 |

**依赖**：1.1  
**验证方式**：用 PDF/DOCX 测试文件跑通解析，输出纯文本

### 1.3 JD 解析 Agent（Day 5-6）

| 任务 | 详情 | 产出 |
|------|------|------|
| 1.3.1 Agent 基类 | `agents/base.py` — LLM + tool calling 封装，统一 Agent 接口 | `agents/base.py` |
| 1.3.2 JD Prompt | `prompts/jd_parser.md` — 技能权重提取 prompt 模板 | prompt 模板 |
| 1.3.3 JD Server | `mcp_servers/jd_server.py` — `parse_jd(text)` MCP Server | MCP Server |
| 1.3.4 JD Agent | `agents/jd_parser.py` — 调用 jd_server.parse_jd，返回结构化 JD | Agent 可用 |

**依赖**：1.1, 1.2  
**验证方式**：输入 JD 文本，输出结构化 JSON（技能含权重）

### 1.4 简历分析 Agent（Day 5-6）

| 任务 | 详情 | 产出 |
|------|------|------|
| 1.4.1 简历 Prompt | `prompts/resume_analyzer.md` — 技能熟练度 + 项目经历提取 | prompt 模板 |
| 1.4.2 简历 Server | `mcp_servers/resume_server.py` — `parse_resume(text)` | MCP Server |
| 1.4.3 简历 Agent | `agents/resume_analyzer.py` — 调用 resume_server.parse_resume | Agent 可用 |

**依赖**：1.1, 1.2  
**并行**：可与 1.3 同时进行（两个 Agent 设计对称）  
**验证方式**：输入简历文本，输出候选人画像 JSON

### 1.5 交叉匹配 + 能力缺口分析（Day 7-8）

| 任务 | 详情 | 产出 |
|------|------|------|
| 1.5.1 匹配逻辑 | 按「简历项目→JD」策略排序维度（DESIGN.md §4） | `orchestration/matcher.py` |
| 1.5.2 缺口 Map 生成 | 技能差距分析（强弱/缺失/优先级） | 缺口 JSON |
| 1.5.3 出题排序策略 | 先问有项目支撑的技能，再问缺口 | 排序算法 |

**依赖**：1.3, 1.4  
**验证方式**：给定 JD + 简历，得到排序后的面试维度列表

### 1.6 单轮问答 + 评判（Day 7-9）

| 任务 | 详情 | 产出 |
|------|------|------|
| 1.6.1 LLM 动态出题 | 根据 JD + 技能维度 + 难度出题（调 LLM） | 出题逻辑 |
| 1.6.2 答案评判 | `prompts/judge.md` — 对候选人回答评分 | 评判 Prompt |
| 1.6.3 Interviewer Agent v1 | `agents/interviewer.py` — 单轮版本（出题→评判→结束） | 单轮 Agent |
| 1.6.4 State 定义 | `orchestration/state.py` — InterviewState + RoundState | 状态模型 |

**依赖**：1.5  
**验证方式**：JD + 简历 → 出一题 → 手动输入答案 → 出评分

### 1.7 Streamlit Web UI（Day 10-12）

| 任务 | 详情 | 产出 |
|------|------|------|
| 1.7.1 UI 骨架 | `web/app.py` — 文件上传 + 对话窗口 + 评分展示 | Web 界面 |
| 1.7.2 文件上传组件 | 上传 JD/简历（PDF/DOCX/TXT），调用解析管道 | 上传功能 |
| 1.7.3 对话流展示 | 流式展示面试过程（问题 → 输入答案 → 下一题） | 对话界面 |
| 1.7.4 LangGraph 图定义 | `orchestration/supervisor.py` — 串起整个 MVP 流程 | 可执行图 |
| 1.7.5 端到端联调 | JD→简历→匹配→出题→回答→评分，走通整条链路 | MVP 可用 |

**依赖**：1.2, 1.6（不需要 1.3/1.4 的 MCP Server 版本，可直接调 LLM）  
**验证方式**：`python main.py web` → 上传文件 → 完成一次完整面试

### Phase 1 里程碑

> ✅ **MVP 可用**：上传 JD + 简历 → AI 出题 → 手动回答 → 评分展示  
> 验收标准：能在 Streamlit UI 中完成一次完整的单轮面试流程。

---

## Phase 2：多轮面试流程（第 3-4 周）

### 目标

实现面试官多轮追问状态机，完善题库服务，添加反馈 Agent。

### 2.1 多轮追问状态机（Day 13-15）

| 任务 | 详情 | 产出 |
|------|------|------|
| 2.1.1 状态机核心 | 实现 ASK → JUDGE → (deepen / clarify / switch_skill / end) 循环 | `interviewer.py` 状态机 |
| 2.1.2 面试 Loop 节点 | LangGraph 中实现循环图节点 | Graph 更新 |
| 2.1.3 终止条件判断 | 维度覆盖 / 轮次上限(10) / 连续 3 题答不上 / 主动结束 | 终止逻辑 |
| 2.1.4 轮次状态持久化 | 每轮存入 InterviewState.rounds | 状态管理 |

**依赖**：1.6  
**验证方式**：多轮追问流程跑通，能在 3-5 轮后正常结束

### 2.2 追问策略细化（Day 15-17）

| 任务 | 详情 | 产出 |
|------|------|------|
| 2.2.1 「答得好→加深」策略 | 追问技术细节、考察深度 | 追问逻辑 |
| 2.2.2 「答得模糊→澄清」策略 | 要求举具体例子、STAR 原则 | 澄清逻辑 |
| 2.2.3 「答不上→换维度」策略 | 标记弱点，切换下一个技能 | 切换逻辑 |
| 2.2.4 软技能穿插评估 | 在每轮对话中自然考察沟通/学习能力 | 评估逻辑 |

**依赖**：2.1  
**验证方式**：模拟不同回答质量，观察面试官策略切换是否正确

### 2.3 题库 Server + 种子数据（Day 17-19）

| 任务 | 详情 | 产出 |
|------|------|------|
| 2.3.1 题库 Server | `mcp_servers/question_bank_server.py` — CRUD 接口 | MCP Server |
| 2.3.2 LLM 动态出题 | `generate_questions()` — 核心方法，LLM 实时生成 | 动态出题 |
| 2.3.3 种子题库（可选） | 初始化少量种子数据，RAG 检索兜底 | 种子 JSON |
| 2.3.4 反哺机制 | `add_to_seed_bank()` — 优质题目自动入库 | 自增题库 |

**依赖**：1.3（JD 结构化数据）  
**验证方式**：调用 generate_questions 返回合理面试题

### 2.4 反馈 Agent + 报告生成（Day 19-21）

| 任务 | 详情 | 产出 |
|------|------|------|
| 2.4.1 反馈 Prompt | `prompts/feedback.md` — 5 维度评分 + 报告模板 | Prompt |
| 2.4.2 反馈 Agent | `agents/feedback.py` — 遍历所有轮次生成报告 | `feedback.py` |
| 2.4.3 评分加权汇总 | 技术匹配 40% + 项目经验 30% + 沟通 15% + 学习 10% + 文化 5% | 评分引擎 |
| 2.4.4 UI 报告展示 | Streamlit 报告页面（雷达图 + 文字详情） | UI 更新 |

**依赖**：2.1（需要完整面试记录）  
**验证方式**：面试结束后自动生成报告，包含评分 + 亮点 + 不足 + 建议

### Phase 2 里程碑

> ✅ **完整面试闭环**：JD/简历上传 → 多轮追问面试 → 综合评分报告  
> 验收标准：能进行一次 5-8 轮的自然对话面试，结束后自动生成带评分的面试报告。

---

## Phase 3：MCP Gateway + 长期记忆（第 5-6 周）

### 目标

实现 MCP Gateway 统一管理服务，接入 ChromaDB 实现长期记忆。

### 3.1 MCP Gateway（Day 22-25）

| 任务 | 详情 | 产出 |
|------|------|------|
| 3.1.1 Gateway 框架 | `mcp_servers/gateway.py` — FastAPI + SSE transport | Gateway |
| 3.1.2 Server 注册 | 动态注册 JD / 简历 / 题库三个 Server | 注册机制 |
| 3.1.3 路由转发 | 按工具名路由到对应 Server | 路由逻辑 |
| 3.1.4 鉴权中间件 | API Key 验证（简单 Bearer token） | 鉴权 |
| 3.1.5 限流中间件 | 基于 IP / API Key 的令牌桶限流 | 限流 |
| 3.1.6 Gateway API 定义 | `POST /api/v1/interview` 等 4 个接口（DESIGN.md §9） | REST API |

**依赖**：1.3, 1.4, 2.3（三个 MCP Server）  
**验证方式**：通过 Gateway 调用三个 Server 的 all tools

### 3.2 ChromaDB 集成（Day 25-28）

| 任务 | 详情 | 产出 |
|------|------|------|
| 3.2.1 向量库封装 | `memory/vector_store.py` — ChromaDB CRUD 封装 | 存储层 |
| 3.2.2 4 个 Collection | jd_history / question_bank / interview_sessions / candidate_profiles | 集合定义 |
| 3.2.3 Embedding 策略 | 对接 DeepSeek embeddings 或 sentence-transformers | 向量化 |
| 3.2.4 面试存储 | 每次面试结束自动存入 interview_sessions | 写入逻辑 |
| 3.2.5 历史检索 | 出题时从 question_bank 检索相似题 | 检索逻辑 |
| 3.2.6 降级策略 | ChromaDB 不可用时降级为纯内存模式 | 降级 |

**依赖**：2.4（需要面试记录数据）  
**验证方式**：完成两次面试，验证第二次能检索到第一次的记录

### 3.3 API + 多候选人支持（Day 28-30）

| 任务 | 详情 | 产出 |
|------|------|------|
| 3.3.1 会话管理 | 多候选人面试会话隔离 | 会话层 |
| 3.3.2 API 完整实现 | 4 个 REST API 对接完整业务流程 | API |
| 3.3.3 Gateway→UI 对接 | Streamlit 改为通过 Gateway API 调用，不直接调 Agent | 解耦 |
| 3.3.4 CLI 历史查询 | `python main.py history --candidate "张三"` | 历史查询 |

**依赖**：3.1, 3.2  
**验证方式**：同时开两个浏览器窗口，两个独立面试互不干扰

### Phase 3 里程碑

> ✅ **生产级架构**：MCP Gateway 统一管理 + ChromaDB 长期记忆 + REST API  
> 验收标准：通过 Gateway API 完成面试全流程，历史面试记录可查。

---

## Phase 4：工程化 + Demo 准备（第 7-8 周）

### 目标

完善错误处理、流式输出、Docker 部署，准备面试 Demo。

### 4.1 错误处理 + 健壮性（Day 31-33）

| 任务 | 详情 | 产出 |
|------|------|------|
| 4.1.1 LLM 调用重试 | 指数退避重试 2 次，失败跳过该轮 | `base.py` 重试 |
| 4.1.2 文件解析失败处理 | 明确错误提示 + 格式要求 | 异常处理 |
| 4.1.3 MCP Server 熔断 | Gateway 503 返回 + 前端提示 | 熔断机制 |
| 4.1.4 空回答检测 | 连续 3 轮空回答 → 自动结束面试 | 检测逻辑 |
| 4.1.5 ChromaDB 降级 | 连接失败 → 纯内存模式，不影响核心流程 | 降级 |

**依赖**：Phase 3 完成  
**验证方式**：模拟 LLM 超时、空回答、ChromaDB 断开，系统正常降级

### 4.2 流式输出 + 交互优化（Day 33-35）

| 任务 | 详情 | 产出 |
|------|------|------|
| 4.2.1 LLM 流式输出 | 问题逐字显示，提升体验 | SSE 流 |
| 4.2.2 面试进度指示 | 当前轮次 / 总轮次 / 已覆盖维度展示 | UI 优化 |
| 4.2.3 打字机效果 | 前端逐字渲染（Streamlit 原生或自定义） | 前端优化 |

**依赖**：2.1, 1.7  
**验证方式**：面试问题以流式方式逐字显示

### 4.3 Docker 部署（Day 35-37）

| 任务 | 详情 | 产出 |
|------|------|------|
| 4.3.1 Dockerfile | 主应用多阶段构建 | `Dockerfile` |
| 4.3.2 docker-compose.yml | app + chromadb 两个容器编排 | `docker-compose.yml` |
| 4.3.3 .dockerignore | 排除不需要的文件 | `.dockerignore` |
| 4.3.4 .env.example | 环境变量模板 | `.env.example` |
| 4.3.5 虚拟机部署验证 | scp 上传 → 启动 → 健康检查 | 部署脚本 |

**依赖**：4.1, 4.2  
**验证方式**：`docker compose up -d --build` → curl 验证 Web UI + Gateway

### 4.4 面试 Demo 准备（Day 37-40）

| 任务 | 详情 | 产出 |
|------|------|------|
| 4.4.1 演示脚本 | 从 JD 解析到面试到报告的完整演示流程 | `demo/script.md` |
| 4.4.2 Demo 数据 | 准备 2-3 组 JD + 简历作为演示数据 | 测试数据 |
| 4.4.3 面试话术 | 准备 Q&A：架构设计、技术选型、难点攻克 | 话术卡 |
| 4.4.4 极限测试 | 测试边界情况：超大简历、空文件、高并发 | 测试报告 |

**依赖**：4.3  
**验证方式**：根据脚本完整演示一次，记录时长和体验

### Phase 4 里程碑

> ✅ **Demo Ready**：Docker 部署 + 完整演示流程 + 面试话术  
> 验收标准：在虚拟机上 `docker compose up` 后，能向面试官完整演示整个产品。

---

## 详细周计划表

| 周次 | 时间段 | 重点工作 | 产出 |
|------|--------|----------|------|
| **W1** | Day 1-3 | 项目脚手架 + 文件解析 | 目录结构、配置、Pydantic 模型、PDF/DOCX 解析 |
| **W1** | Day 4-7 | JD Agent + 简历 Agent + 交叉匹配 | 两个解析 Agent + 缺口分析 |
| **W2** | Day 8-10 | 单轮问答 + LangGraph 图 | Interviewer Agent v1 + State 定义 |
| **W2** | Day 11-12 | Streamlit Web UI + 端到端联调 | MVP 可用版本 |
| **W3** | Day 13-16 | 多轮状态机 + 追问策略 | 面试官核心循环 |
| **W3** | Day 17-19 | 题库 Server + 种子数据 | 动态出题系统 |
| **W4** | Day 19-21 | 反馈 Agent + 报告生成 | 完整面试闭环 |
| **W4** | Day 22-23 | 缓冲时间，补前面进度 | — |
| **W5** | Day 24-27 | MCP Gateway | Gateway 框架 + 注册/鉴权/限流 |
| **W5** | Day 27-28 | ChromaDB 封装 + Collection 定义 | 存储层 |
| **W6** | Day 29-31 | 记忆读写 + 历史检索 + 降级 | 长期记忆集成 |
| **W6** | Day 31-33 | API 接口 + 多候选人支持 | 生产级 API |
| **W7** | Day 34-37 | 错误处理 + 流式输出 + 健壮性 | 容错 + 体验优化 |
| **W7** | Day 37-38 | Docker + docker-compose | 容器化 |
| **W8** | Day 39-40 | 虚拟机部署 | 可演示环境 |
| **W8** | Day 41-42 | Demo 脚本 + 话术 + 排练 | 面试演示就绪 |

---

## 关键里程碑总览

```
Phase 1 ─── Day 12 ─── ✅ MVP 可用（单轮面试）
Phase 2 ─── Day 21 ─── ✅ 完整面试闭环（多轮 + 报告）
Phase 3 ─── Day 30 ─── ✅ 生产级架构（Gateway + 记忆）
Phase 4 ─── Day 40 ─── ✅ Demo Ready（部署 + 演示）
```

---

## 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| DeepSeek API 不稳定 | LLM 调用失败，面试中断 | 重试机制 + 降级跳过 |
| ChromaDB 配置复杂 | 记忆功能开发超时 | 先纯内存，Phase 3 再加 |
| LangGraph 循环图调试困难 | 多轮状态机开发卡住 | 先实现单轮，再改为循环 |
| Streamlit 流式输出限制 | 打字机效果难实现 | 接受 SSE 模拟，不强求完美 |
| 虚拟机资源不足 | Docker 运行缓慢 | 减小模型调用，降低并发重试 |

---

## 阶段依赖一览

```
1.2 文件解析 ─┬─→ 1.3 JD Agent ──→ 1.5 匹配 ──→ 1.6 单轮问答 ──→ 1.7 Web UI
              │                                                      │
              └─→ 1.4 简历 Agent ──→ 1.5 ────────────────────────────┘
                                                                      │
                                                                      ↓
                                                              2.1 多轮状态机
                                                                      │
                                                     2.3 题库 Server ─┤
                                                                      ↓
                                                                     2.4 反馈 Agent
                                                                      │
                                                                      ↓
                                                              3.1 MCP Gateway
                                                                      │
                                                              3.2 ChromaDB ──→ 3.3 API
                                                                      │
                                                                      ↓
                                                              4.1 错误处理 → 4.2 流式
                                                                      ↓
                                                              4.3 Docker → 4.4 Demo
```

---

## 尾注

- 本项目定位为**面试作品**（AI 面试官系统本身），而非面试工具
- 每周预留 1 天缓冲时间应对意外卡点
- 核心原则：**先跑通再优化** — 不要在第一阶段追求完美架构
- 每个 Agent 先走 LLM 直调，再封装 MCP Server，避免过度工程化
