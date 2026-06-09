# AI 面试官 — 头脑风暴 / 设计思路

## 一句话概述

用户上传 JD + 简历 → 系统解析两者 → AI 面试官针对匹配度出题 → 多轮追问 → 综合评分 + 报告。完整面试闭环。

---

## 1. 核心链路

```
上传 JD（PDF/DOCX/文本）
   +
上传简历（PDF/DOCX/文本）
        ↓
   JD 解析 Agent     简历分析 Agent
   （提取技能/经验/   （提取候选人画像）
    岗位要求）
        ↓                ↓
         交叉匹配 → 生成 "能力缺口 Map"
                         ↓
              面试官 Agent（核心）
              ┌─ 轮次 1：根据缺口出首题
              │   ↓ 候选人回答
              │   评判答案 → 决定下一题方向
              ├─ 轮次 2：追问 / 换维度
              │   ↓
              ├─ 轮次 3...
              │   ↓
              └─ 达到终止条件 → 结束
                         ↓
               反馈 Agent
               ├─ 逐维度评分（技能/经验/沟通/逻辑）
               ├─ 面试总结
               └─ 改进建议
```

---

## 2. 四个 Agent 职责拆解

### 2.1 JD 解析 Agent

| 项目 | 内容 |
|---|---|
| 输入 | JD 文件（PDF/DOCX/TXT），同样走解析管道提取纯文本 |
| 输出 | 结构化 JD（JSON）：岗位名称、必备技能（weight 权重）、加分技能、经验年限、学历要求、软技能要求 |
| 调用的工具 | 无（纯 LLM 提取） |
| 关键点 | 技能要带权重，否则后续匹配没有依据 |

### 2.2 简历分析 Agent

| 项目 | 内容 |
|---|---|
| 输入 | 简历文件（PDF/DOCX/TXT），通过 pdfplumber / python-docx 解析为文本后再送入 LLM |
| 输出 | 候选人画像（JSON）：姓名、技能列表及熟练度、工作经历（公司/时长/职责）、项目经历、学历 |
| 调用的工具 | 无（纯 LLM 提取） |
| 关键点 | 输出格式要和 JD 解析结果对齐，方便做匹配 |

### 2.3 面试官 Agent（最复杂）

| 项目 | 内容 |
|---|---|
| 输入 | JD 结构化数据 + 候选人画像 + 能力缺口 Map + 历史面试记录（来自 ChromaDB） |
| 输出 | 每轮：面试题 + 对回答的评判 + 下一题方向调整 |
| 调用的工具 | `search_question_bank(query, dimension)` — 从题库检索相关题目；`save_round(round_data)` — 存入记忆 |
| 状态机 | `开场 → 出题 → 等待回答 → 评判 → 决定追问/换维度/结束` |

**多轮追问策略：**
- 答得好 → 加深难度，追问细节（"能说说这个项目的并发你是怎么处理的？"）
- 答得模糊 → 要求具体化（"能举一个具体例子吗？"）
- 答不上来 → 标记弱点，换下一个技能维度
- 覆盖完所有维度 → 结束面试

**终止条件：**
- 所有技能维度都被覆盖
- 轮次达到上限（如 10 轮）
- 候选人连续 3 道题答不上来（降级终止）
- 主动结束（"今天的面试到此结束"）

### 2.4 反馈 Agent

| 项目 | 内容 |
|---|---|
| 输入 | 完整面试记录（所有轮次） + JD 要求 + 候选人画像 |
| 输出 | 面试报告（JSON/Markdown）：总分、各维度得分、亮点、不足、录用建议、后续面试重点 |
| 调用的工具 | 无（纯 LLM 汇总） |

---

## 3. MCP Server 设计

三个独立 MCP Server，通过 Gateway 统一暴露：

### 3.1 JD Server
```
Tools:
  - parse_jd(text: str) -> StructuredJD
  - search_similar_jd(keywords: str) -> list[JD]     # 从历史 JD 库里找相似的
```

### 3.2 简历 Server
```
Tools:
  - parse_resume(text: str) -> CandidateProfile
  - compare_profiles(profile1, profile2) -> diff     # 候选人对比
```

### 3.3 题库 Server

**当前策略：LLM 动态生成为主，种子题库为辅**

```
Tools:
  - generate_questions(jd: StructuredJD, skill: str, difficulty: str, count: int) -> list[Question]
    # 核心方法：LLM 根据 JD 实时出题，保证题目与岗位高度相关
  - search_seed_bank(skill: str, difficulty: str, count: int) -> list[Question]
    # 从种子题库检索（RAG），作为补充
  - add_to_seed_bank(question: Question) -> bool
    # 将优质 LLM 生成的题目反哺回种子库，逐步积累
  - get_similar_questions(question_id: str) -> list[Question]
```

**出题优先级**：LLM 动态生成为主力，种子题库 RAG 为辅助（去重 + 兜底）。后续可引入 RAG 混合策略。

### MCP Gateway
- 统一入口，负责注册 / 鉴权 / 限流 / 路由
- FastAPI 实现
- SSE transport（MCP 推荐方式）

---

## 4. LangGraph 编排设计

### 图结构

```
                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           ↓
              ┌────────────────────────┐
              │  PARSE_JD_AND_RESUME   │  ← 两个纯 LLM Agent 可并行
              │  (JD Agent | Resume Ag) │
              └───────────┬────────────┘
                          ↓
              ┌────────────────────────┐
              │   MATCH_AND_ANALYZE    │  ← 交叉对比，生成缺口 Map
              └───────────┬────────────┘
                          ↓
              ┌────────────────────────┐
              │   INTERVIEW_LOOP       │  ← 核心循环节点
              │   ┌─────────────────┐  │
              │   │ 出题 → 等待→评判 │  │
              │   │  ↓           ↑  │  │
              │   │  决定下一步 ────┘  │
              │   └─────────────────┘  │
              └───────────┬────────────┘
                          ↓
              ┌────────────────────────┐
              │   FEEDBACK             │  ← 最终评分 + 报告
              └───────────┬────────────┘
                          ↓
                    ┌─────────────┐
                    │    END      │
                    └─────────────┘
```

### Supervisor 路由逻辑

Supervisor 不直接做业务逻辑，只做路由决策：
- 判断当前状态
- 决定下一个调用哪个 Agent
- 管理 LangGraph checkpoint（断点续跑 / 状态持久化）

### 面试维度排序策略

出题顺序不是按 JD 权重死板排，而是：

1. **先从简历出发**：提取候选人项目经历中涉及的技术栈
2. **再结合 JD**：找出 JD 要求的技能中，候选人简历里**有项目支撑**的点 → 优先问（考察真实掌握深度）
3. **最后问缺口**：JD 要求但简历里找不到的 → 确认是否真的不会
4. **软技能穿插**：沟通 / 学习能力贯穿在每轮追问中自然评估

这样面试体验更自然，不是"背题式"面试。

---

## 5. 状态设计

### 全局 State（LangGraph 共享状态）

```python
class InterviewState(TypedDict):
    # === 输入 ===
    jd_raw: str
    resume_raw: str

    # === JD 解析结果 ===
    structured_jd: dict          # 岗位名、技能列表[名/权重]、经验要求...

    # === 简历解析结果 ===
    candidate_profile: dict      # 姓名、技能[名/等级]、经历...

    # === 匹配结果 ===
    skill_gaps: list[dict]       # [{"skill": "Redis", "gap": "weak", "priority": "high"}, ...]

    # === 面试过程 ===
    current_round: int
    rounds: list[dict]           # [{"question": "", "answer": "", "judgment": ""}, ...]
    covered_dimensions: list[str] # 已覆盖的技能维度
    next_action: str             # "ask" | "follow_up" | "switch_skill" | "end"

    # === 输出 ===
    report: dict                 # 最终报告
```

### 面试轮次状态

```python
class RoundState(TypedDict):
    round_number: int
    dimension: str               # 本轮考察的技能
    question: str
    candidate_answer: str
    judgment: str                # 对回答的评价
    score: float                 # 本轮单项得分
    next_suggestion: str         # "deepen" | "clarify" | "move_on" | "end"
```

---

## 6. 长期记忆（ChromaDB）

### 存储内容

| Collection | 存储什么 | 用途 |
|---|---|---|
| `jd_history` | 历史 JD 的 embedding | 相似岗位快速匹配 |
| `question_bank` | 题库的 embedding | 语义检索题目 |
| `interview_sessions` | 每次面试的完整记录 | 下次面试参考、候选人对比 |
| `candidate_profiles` | 候选人画像 + 历次面试摘要 | 回头客面试有记忆 |

### 检索时机

- 面试官 Agent 出题时：先从 `question_bank` 检索相关题目
- 新面试开始时：从 `interview_sessions` 查看该候选人上次表现
- 反馈 Agent 评分时：从 `candidate_profiles` 拿历史对比数据

---

## 7. 多轮追问的状态机

```
              ┌──────────┐
              │  IDLE    │
              └─────┬────┘
                    │ 开始面试
                    ↓
              ┌──────────┐
              │  ASK     │ ←────────── 出题
              └─────┬────┘
                    │ 候选人回答
                    ↓
              ┌──────────┐
              │  JUDGE   │ ← 评判回答 + 决定下一步
              └─────┬────┘
                    │
          ┌─────────┼─────────┐
          ↓         ↓          ↓
      deepen    clarify    switch_skill
      (追问)    (澄清)      (换维度)
          │         │          │
          └─────────┼──────────┘
                    ↓
              所有维度覆盖？
              ├─ 是 → END
              └─ 否 → ASK
```

---

## 8. 评分体系

### 维度

| 维度 | 权重 | 评分依据 |
|---|---|---|
| 技术匹配度 | 40% | 技能掌握深度、广度 |
| 项目经验 | 30% | 项目复杂度、角色贡献 |
| 沟通表达 | 15% | 逻辑清晰度、结构化表达 |
| 学习能力 | 10% | 对新技术的理解、举一反三 |
| 文化匹配 | 5% | 价值观、工作风格 |

### 评分流程

每轮出分 → 面试结束加权汇总 → LLM 二次校验（防止评分偏斜）→ 生成最终报告

---

## 9. 接口设计

### Web UI（主要交互方式）

简单 Web UI，技术选型 **Streamlit** 或 **Gradio**：
- 上传 JD / 简历文件
- 实时显示面试对话流
- 面试结束后展示评分报告

### CLI 入口（MVP 快速调试）

```bash
# 启动 Web UI（主要入口）
python main.py web

# 启动 MCP Gateway
python main.py gateway

# 查看历史面试记录
python main.py history --candidate "张三"
```

### Gateway API（供 Web UI 和后端消费）

```
POST /api/v1/interview           # 创建面试会话
POST /api/v1/interview/{id}/talk # 发送消息
GET  /api/v1/interview/{id}      # 获取会话状态
GET  /api/v1/interview/{id}/report  # 获取面试报告
```

---

## 10. 目录结构（草案）

```
interview_hub/
├── agents/
│   ├── __init__.py
│   ├── base.py              # Agent 基类（LLM + tool calling）
│   ├── jd_parser.py         # JD 解析 Agent
│   ├── resume_analyzer.py   # 简历分析 Agent
│   ├── interviewer.py       # 面试官 Agent（核心）
│   └── feedback.py          # 反馈 Agent
│
├── mcp_servers/
│   ├── __init__.py
│   ├── gateway.py           # MCP Gateway（FastAPI）
│   ├── jd_server.py         # JD MCP Server
│   ├── resume_server.py     # 简历 MCP Server
│   └── question_bank_server.py  # 题库 MCP Server
│
├── orchestration/
│   ├── __init__.py
│   ├── supervisor.py        # LangGraph Supervisor + 图定义
│   └── state.py             # State 类型定义
│
├── memory/
│   ├── __init__.py
│   ├── vector_store.py      # ChromaDB 封装
│   └── session_store.py     # 面试会话存储（短期）
│
├── models/
│   ├── __init__.py
│   ├── jd.py                # JD 相关 Pydantic 模型
│   ├── resume.py            # 简历相关模型
│   ├── question.py          # 题目模型
│   └── interview.py         # 面试/状态模型
│
├── tools/
│   ├── __init__.py
│   ├── pdf_parser.py        # PDF 简历/JD 解析（pdfplumber）
│   ├── docx_parser.py       # DOCX 简历/JD 解析（python-docx）
│   └── text_cleaner.py      # 文本预处理
│
├── prompts/
│   ├── jd_parser.md         # JD 解析 prompt 模板
│   ├── resume_analyzer.md   # 简历分析 prompt
│   ├── interviewer.md       # 面试官 system prompt
│   ├── judge.md             # 答案评判 prompt
│   └── feedback.md          # 评分报告 prompt
│
├── web/
│   ├── __init__.py
│   └── app.py               # Streamlit/Gradio Web UI
│
├── main.py                  # CLI 入口
├── config.py                # 配置管理（API Key / DB 路径等）
└── requirements.txt
```

---

## 11. 实现优先级（已调整）

```
Sprint 1（MVP — 跑通基本链路）
  ✅ PDF/DOCX 文件解析管道
  ✅ JD 解析 + 简历分析 Agent（并行）
  ✅ 交叉匹配 → 按"简历项目→JD"策略排序维度
  ✅ LLM 动态出题（种子题库接口预留）
  ✅ 单轮问答（出题 → 回答 → 评分）
  ✅ Streamlit Web UI 启动

Sprint 2（多轮 + 记忆）
  ✅ 面试官多轮追问状态机
  ✅ 题库种子数据 + RAG 检索
  ✅ ChromaDB 存储面试记录
  ✅ 反馈 Agent + 报告生成

Sprint 3（完整闭环）
  ✅ MCP Gateway + 三个 Server 全部上线
  ✅ FastAPI 接口暴露
  ✅ 多候选人并发支持

Sprint 4（打磨）
  ✅ 错误处理 / 重试
  ✅ 流式输出
  ✅ 面试回放
  ✅ 候选人多次面试对比
```

---

## 12. 开放问题（已拍板）

1. ~~简历格式~~ → **支持 PDF + DOCX + 纯文本**，通过 pdfplumber + python-docx 解析
2. ~~题库初始化~~ → **LLM 动态生成为主**，种子题库接口预留，后续接 RAG
3. ~~面试维度顺序~~ → **简历项目 → 技术栈 → JD 交叉比对**，按此策略排序
4. ~~多候选人并发~~ → **后续迭代**，MVP 先单会话
5. ~~前端~~ → **Streamlit 或 Gradio 简单 Web UI**，不搞重型前端

---

## 13. Docker 部署（虚拟机）

### 容器架构

```
┌─────────────────────────────────────┐
│            docker-compose            │
│                                     │
│  ┌───────────┐    ┌──────────────┐  │
│  │    app    │    │   chromadb   │  │
│  │  :8501    │◄──►│   :8000      │  │
│  │ Streamlit │    │  (内部)       │  │
│  │ FastAPI   │    │              │  │
│  │ MCP Srv   │    │              │  │
│  └─────┬─────┘    └──────┬───────┘  │
│        │                 │          │
│  ┌─────┴─────────────────┴───────┐  │
│  │        named volumes          │  │
│  │  chroma_data / uploads / logs │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

两个容器：
- `app` — 主应用（Web UI + API + MCP Servers 全在一起，MVP 不拆微服务）
- `chromadb` — 独立向量数据库（数据持久化到 named volume）

### 文件清单

| 文件 | 用途 |
|---|---|
| `Dockerfile` | 主应用镜像构建 |
| `docker-compose.yml` | 多容器编排 |
| `.dockerignore` | 排除不需要打入镜像的文件 |
| `.env.example` | 环境变量模板（部署时复制为 `.env`） |

### 部署指令

```bash
# 1. 拷贝项目到虚拟机
scp -r InterviewAgentHub/ user@vm:/opt/

# 2. 在虚拟机上
cd /opt/InterviewAgentHub
cp .env.example .env
vim .env                      # 填入 DEEPSEEK_API_KEY

# 3. 构建 + 启动
docker compose up -d --build

# 4. 验证
curl http://localhost:8501    # Streamlit Web UI
curl http://localhost:8000/health  # FastAPI Gateway
```

### 环境变量

| 变量 | 说明 | 示例 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | `sk-xxx` |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com/v1` |
| `CHROMA_HOST` | ChromaDB 地址 | `chromadb`（容器内 DNS） |
| `CHROMA_PORT` | ChromaDB 端口 | `8000` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
