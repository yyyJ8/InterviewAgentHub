# Phase 2 实现总结 — AI 面试官 多轮面试 + 完整闭环

> 对应 ROADMAP.md 第 3-4 周目标：从单轮面试升级为完整的闭环面试，包含多轮追问状态机、追问策略、题库 Server、反馈 Agent

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
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             用户界面层                                            │
│  Streamlit Web UI (web/app.py) — 多轮面试 + 5 维度报告                           │
└──────────────────────────────┬───────────────────────────────────────────────────┘
                               │ 上传文件 / 输入回答 / 查看报告
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             编排层                                                │
│  LangGraph 循环图 (orchestration/supervisor.py)                                 │
│                                                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                                    │
│  │ parse_jd │ → │parse_res │ → │ match_   │  ←── 一次性（初始化阶段）              │
│  │          │   │ume       │   │ skills   │                                    │
│  └──────────┘   └──────────┘   └─────┬────┘                                    │
│                                      ▼                                          │
│              ┌──────────────────────────────────────────────┐                   │
│              │             循环阶段（多轮）                    │                   │
│              │                                               │                   │
│              │  ┌────────────────┐                           │                   │
│              │  │ generate_      │  ←── 首次出题 / deepen    │                   │
│              │  │ question      │       / clarify / switch   │                   │
│              │  └───────┬────────┘                           │                   │
│              │          │ question                            │                   │
│              │          ▼                                    │                   │
│              │  ┌────────────────┐                           │                   │
│              │  │ judge_answer   │  ←── LLM 评判回答          │                   │
│              │  └───────┬────────┘                           │                   │
│              │          │ judge_result                        │                   │
│              │          ▼                                    │                   │
│              │  ┌────────────────┐    deepen/clarify ────────┼───┐             │
│              │  │ decide_next    │ → switch ────────────────┼───┼──┐          │
│              │  │ (终止条件检查)   │    end ────────→ FEEDBACK  │  │            │
│              │  └────────────────┘                           │  │  │            │
│              └──────────────────────────────────────────────┘  │  │            │
│                                                                ▼  ▼            │
│                                                         回 generate_question    │
│                                                                                  │
│  State 管理：InterviewState（轮次记录 + 技能索引 + 终止条件）                      │
└──────────────────────────────┬───────────────────────────────────────────────────┘
                               │ 调用 Agent
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             Agent 层                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐     │
│  │JDParserAgent│  │ResumeAnalyzer│  │InterviewerAgent  │  │FeedbackAgent │     │
│  │  JD → 结构化 │  │ 简历 → 画像  │  │ 出题+评判+追问    │  │ 评分+报告     │     │
│  └──────┬──────┘  └──────┬───────┘  └────────┬─────────┘  └──────┬───────┘     │
│         │                │                    │                   │              │
│         └────────────────┴────────────────────┴───────────────────┘              │
│                                          │                                      │
│                                          ▼                                      │
│                                 BaseAgent (agents/base.py)                      │
│                                 LLM 调用 + JSON 解析 + 重试机制                  │
└──────────────────────────────┬───────────────────────────────────────────────────┘
                               │ 调 DeepSeek API
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         基础设施层                                                │
│  ┌──────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐        │
│  │  models/llm.py   │  │ 题库 MCP Server      │  │  data/seed_questions │        │
│  │  DeepSeek 封装    │  │  question_bank_     │  │  种子题库 12 题       │        │
│  │                  │  │  server.py           │  │                      │        │
│  └──────────────────┘  └─────────────────────┘  └──────────────────────┘        │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 分层职责

| 层级 | 目录 | 职责 |
|------|------|------|
| 界面层 | `web/` | Streamlit 多轮对话 UI、5 维度评分报告、雷达图 |
| 编排层 | `orchestration/` | LangGraph 循环图、状态机、终止条件判断 |
| Agent 层 | `agents/` | 四个 Agent：JD 解析、简历分析、面试官、反馈 |
| 工具层 | `tools/` | 文件解析（PDF/DOCX/TXT）、文本清洗 |
| 模型层 | `models/` | Pydantic 数据模型（含多轮记录 + 报告模型） |
| 提示词层 | `prompts/` | 5 个 Prompt 模板（含追问加深 + 澄清 + 反馈） |
| 基础设施 | `mcp_servers/`, `data/` | 题库 MCP Server、种子数据 |
| 配置层 | `config.py` | 全局配置（含多轮参数） |

---

## 2. 目录结构

```
d:\lineageAgent\
│
├── config.py                 # 配置管理（单例模式）
├── main.py                   # CLI 入口（3 个子命令）
├── .env                      # 环境变量（API Key 等）
├── requirements.txt          # 依赖列表
│
├── agents/                   # Agent 层
│   ├── __init__.py
│   ├── base.py               # — BaseAgent 基类
│   ├── jd_parser.py          # — JD 解析 Agent
│   ├── resume_analyzer.py    # — 简历分析 Agent
│   ├── interviewer.py        # — 面试官 Agent（Phase 2 新增方法）
│   └── feedback.py           # — ★ 反馈 Agent（Phase 2 新增）
│
├── models/                   # 模型层
│   ├── __init__.py
│   ├── llm.py                # — DeepSeek LLM 封装
│   ├── jd.py                 # — JD / Skill Pydantic 模型
│   ├── resume.py             # — Resume / Project / SkillProficiency
│   ├── question.py           # — ★ 新增 RoundRecord / InterviewReport
│   └── interview.py          # — InterviewState / RoundState
│
├── orchestration/            # 编排层
│   ├── __init__.py
│   ├── matcher.py            # — 交叉匹配 + 能力缺口分析
│   └── supervisor.py         # — ★ 重写为循环图（多轮+反馈）
│
├── mcp_servers/              # MCP Server
│   ├── __init__.py
│   ├── jd_server.py          # — JD MCP Server
│   ├── resume_server.py      # — 简历 MCP Server
│   └── question_bank_server.py # — ★ 题库 MCP Server（Phase 2 新增）
│
├── tools/                    # 工具层
│   ├── __init__.py           # — parse_file() 统一入口
│   ├── pdf_parser.py         # — PDF 解析（pdfplumber）
│   ├── docx_parser.py        # — DOCX 解析（python-docx）
│   └── text_cleaner.py       # — 文本清洗
│
├── prompts/                  # 提示词层
│   ├── __init__.py           # — load_prompt() 加载器
│   ├── jd_parser.md          # — JD 提取 prompt
│   ├── resume_analyzer.md    # — 简历提取 prompt
│   ├── interviewer.md        # — 出题 prompt
│   ├── interviewer_deepen.md # — ★ 追问加深 prompt（Phase 2 新增）
│   ├── interviewer_clarify.md# — ★ 澄清追问 prompt（Phase 2 新增）
│   ├── judge.md              # — 评判 prompt
│   └── feedback.md           # — ★ 反馈评分 prompt（Phase 2 新增）
│
├── data/                     # ★ 数据目录（Phase 2 新增）
│   └── seed_questions.json   # — ★ 种子题库 12 题
│
├── web/                      # 界面层
│   ├── __init__.py
│   └── app.py                # — ★ Streamlit Web UI（多轮重写）
│
├── tests/                    # 测试
│   ├── __init__.py
│   ├── test_parse_pipeline.py
│   ├── test_agents.py
│   ├── test_interviewer.py   # — ★ 新增 4 个测试用例
│   └── fixtures/
│       ├── sample_jd.txt
│       └── sample_resume.txt
│
├── doc/
│   ├── phase1-overview.md    # Phase 1 文档
│   └── phase2-overview.md    # ← 本文档
│
└── ROADMAP.md                # 完整路线图
```

---

## 3. 核心数据流

### 3.1 多轮面试循环

```
                    用户上传 JD + 简历
                         │
                         ▼
              ┌──────────────────────┐
              │  tools/parse_file     │  解析 JD + 简历为纯文本
              └─────────┬────────────┘
                        │
              ┌──────────────────────┐
              │  JDParserAgent.run   │  LLM → JD 模型（技能+权重）
              │  ResumeAnalyzer.run  │  LLM → Resume 模型（技能+项目）
              └─────────┬────────────┘
                        │
              ┌──────────────────────┐
              │  matcher.rank_skills │  按「有项目 > 缺口 > 加分」排序
              │  generate_gap_map    │  生成完整能力缺口 Map
              └─────────┬────────────┘
                        │
              ┌─────────────────────────────────────────────────────┐
              │                 多轮循环开始                          │
              │                                                     │
              │  ┌──────────────────────────────────────────────┐   │
              │  │  1. generate_question_node()                  │   │
              │  │     ├─ 首次出题 →  skill[0], basic/intermediate│   │
              │  │     ├─ deepen   →  同一技能 +1 难度，追问细节   │   │
              │  │     ├─ clarify  →  同一技能，要求具体举例        │   │
              │  │     └─ switch   →  下一技能（skillIndex+1）    │   │
              │  └──────────────────┬───────────────────────────┘   │
              │                     │ question                      │
              │                     ▼                               │
              │  ┌──────────────────────────────────────────────┐   │
              │  │  2. 用户输入回答（Web UI 层）                   │   │
              │  └──────────────────┬───────────────────────────┘   │
              │                     │ answer                        │
              │                     ▼                               │
              │  ┌──────────────────────────────────────────────┐   │
              │  │  3. judge_answer_node()                       │   │
              │  │     LLM 4 维度评分 → JudgeResult               │   │
              │  │     {score, comment, next_action}             │   │
              │  │     构建 RoundRecord → 追加到 rounds[]         │   │
              │  └──────────────────┬───────────────────────────┘   │
              │                     │ judge_result                  │
              │                     ▼                               │
              │  ┌──────────────────────────────────────────────┐   │
              │  │  4. decide_next_node()                        │   │
              │  │     检查终止条件：                              │   │
              │  │     ├─ 轮次 ≥ max_rounds (10) → END          │   │
              │  │     ├─ 连续空回答 ≥ 3 次 → END                │   │
              │  │     ├─ 所有技能已覆盖 → END                    │   │
              │  │     └─ next_action 判断：                      │   │
              │  │        deepen/clarify → 回步骤 1（本技能）     │   │
              │  │        switch → 回步骤 1（下一技能）            │   │
              │  │        end → 进入反馈阶段                       │   │
              │  └──────────────────────────────────────────────┘   │
              └─────────────────────────────────────────────────────┘
                         │
            decide_next = end
                         │
                         ▼
              ┌──────────────────────┐
              │  FeedbackAgent       │  LLM → InterviewReport
              │  .generate_report()  │  5 维度评分 + 录用建议
              └─────────┬────────────┘
                        │
                        ▼
              ┌──────────────────────┐
              │  Streamlit 报告页     │
              │  柱状图 + 技能明细     │
              │  亮点/不足/建议        │
              └──────────────────────┘
```

### 3.2 追问策略决策树

```
                   judge_result.next_action
                           │
          ┌────────────────┼────────────────┐
          │                │                │
      deepen            clarify          switch
    （答得好）         （答得模糊）      （答不上）
          │                │                │
          ▼                ▼                ▼
  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │ 难度 +1 级    │ │ 要求举具体    │ │ 标记弱点      │
  │ 追问技术细节   │ │ 例子/STAR    │ │ skillIndex+1  │
  │ 结合项目追问   │ │ 耐心引导     │ │ 切下一技能    │
  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
         │                │                │
         └────────────────┴────────────────┘
                          │
                    同一技能出题
                          │
                          ▼
                  generate_question_node()
```

---

## 4. 模块详解

### 4.1 config.py — 配置管理（Phase 2 新增参数）

```python
# Phase 1 原有字段（略）
config.llm_api_key
config.llm_base_url
config.llm_model           # deepseek-v4-pro

# Phase 2 新增
config.max_rounds           # 10       — 最大面试轮次
config.max_consecutive_empty # 3       — 连续空回答终止阈值
```

- `max_rounds`：控制面试不会无限进行，达到上限自动进入报告阶段
- `max_consecutive_empty`：候选人连续跳过/空答达到阈值，自动结束面试

### 4.2 agents/interviewer.py — 面试官 Agent（Phase 2 增强）

Phase 1 只有 2 个方法，Phase 2 扩展到 **5 个核心方法**：

| 方法 | 触发条件 | 说明 |
|------|----------|------|
| `generate_question()` | 首次出题 / 默认 | 根据 JD + 简历 + 目标技能出题 |
| `generate_deepen_question()` | 上一轮 next_action == "deepen" | 基于上一问答，难度+1，追问技术细节 |
| `generate_clarify_question()` | 上一轮 next_action == "clarify" | 引导候选人举具体例子 / STAR 原则 |
| `generate_switch_question()` | 上一轮 next_action == "switch" | 切换到下一技能，出基础题评估 |
| `judge_answer()` | 每轮提交回答后 | 4 维度评分 + next_action 决策 |

**追问加深流程**：

```
generate_deepen_question()
  输入：上一轮的 question + answer + 当前 skill + 难度
  步骤：
    1. 难度升级（basic → intermediate → advanced → deep）
    2. 加载 prompts/interviewer_deepen.md
       - 包含 {previous_question} 和 {previous_answer} 占位符
    3. LLM 生成基于上一回答的具体追问
  输出：Question（难度已提升一级）
```

**澄清追问流程**：

```
generate_clarify_question()
  输入：上一轮的 question + answer + 当前 skill + 难度
  步骤：
    1. 难度不变（depth 不升级，因为还没答清楚）
    2. 加载 prompts/interviewer_clarify.md
       - 引导 "能举一个具体例子吗？"
       - STAR 原则引导
    3. LLM 生成追问
  输出：Question（同难度）
```

### 4.3 models/question.py — Phase 2 新增模型

```python
class RoundRecord(BaseModel):
    """单轮面试记录"""
    round_number: int          # 第几轮（从1开始）
    skill: str                 # 本轮考察技能
    question: Question         # 题目
    answer: str                # 候选人回答
    judge: JudgeResult         # 评判结果

class InterviewReport(BaseModel):
    """面试报告 — 5 维度评分模型"""
    total_score: float                              # 总分 0-100
    dimension_scores: dict[str, float]              # 5 维度得分
    skill_scores: list[dict]                        # 各技能得分明细
    strengths: list[str]                            # 亮点
    weaknesses: list[str]                           # 不足
    suggestions: list[str]                          # 改进建议
    overall_assessment: str                         # 总体评价
    hiring_recommendation: str                      # 录用建议
```

`RoundRecord` 是 LangGraph 中 `rounds` 列表的成员，通过 `operator.add` reducer 自动累积。

### 4.4 orchestration/supervisor.py — Phase 2 循环图

#### State 定义

```python
class InterviewState(TypedDict):
    # 输入
    jd_path: str
    resume_path: str

    # 解析结果
    jd_raw: str
    resume_raw: str
    jd: Optional[JD]
    resume: Optional[Resume]

    # 匹配结果
    gap_map: Optional[dict]
    ordered_skills: list[dict]        # 排序后技能列表
    current_skill_index: int          # 当前技能索引

    # 多轮面试
    rounds: Annotated[list[RoundRecord], add]  # 自动累积
    current_round_number: int         # 当前轮次
    question: Optional[Question]      # 当前题目
    answer: str                       # 当前回答
    judge_result: Optional[JudgeResult]

    # 终止条件
    consecutive_empty: int            # 连续空回答计数
    terminated: bool                  # 是否已终止

    # 批量模式
    all_answers: list[str]            # 预填答案（测试用）

    # 输出
    report: Optional[dict]
    error: Optional[str]
```

#### 图结构

```python
# 注册节点
builder.add_node("parse_jd", parse_jd_node)
builder.add_node("parse_resume", parse_resume_node)
builder.add_node("match_skills", match_skills_node)
builder.add_node("generate_question", generate_question_node)  # 循环入口
builder.add_node("judge_answer", judge_answer_node)
builder.add_node("decide_next", decide_next_node)              # 路由决策

# 边
set_entry_point("parse_jd")
parse_jd → parse_resume → match_skills → generate_question
generate_question → judge_answer → decide_next

# 条件路由
decide_next → ("continue" → generate_question) | ("end" → END)
```

#### 终止条件判断

```python
def _next_action_label(state):
    """检查 4 个终止条件"""

    # 1. 连续空回答检测
    if empty_count >= config.max_consecutive_empty:
        return "end"

    # 2. 轮次上限
    if current_round >= config.max_rounds:
        return "end"

    # 3. 所有技能已覆盖
    if skill_index >= len(ordered_skills):
        return "end"

    # 4. 评判结果建议
    action = judge_result.next_action  # deepen / clarify / switch / end
    return action
```

#### 交互式 Helper（供 Web UI 使用）

```python
async def init_interview(jd_path, resume_path) -> dict:
    """初始化面试：解析 JD + 简历 + 匹配"""

async def generate_next_question(state) -> dict:
    """生成下一道题（基于当前技能和上一轮评判）"""

async def judge_and_decide(state, answer) -> dict:
    """评判回答 + 决定下一步"""
```

这三个函数是 Web UI 驱动多轮循环的核心接口。Web UI 不直接使用 LangGraph 的 stream/ainvoke，而是通过这三个 helper 手动控制循环节奏，每轮：生成问题 → 显示 → 等待回答 → 评判 → 决定 → 循环。

### 4.5 agents/feedback.py — 反馈 Agent（Phase 2 新增）

```python
class FeedbackAgent(BaseAgent):
    async def generate_report(jd, resume, rounds) -> InterviewReport:
        """生成完整评分报告

        步骤：
        1. 构建面试记录文本（_build_transcript）
           - 逐轮：问题 → 回答 → 评分 → 亮点 → 不足
        2. 填充 prompts/feedback.md 模板
           - 包含岗位信息、候选人信息、面试记录
        3. LLM 按 5 维度评分
           - 技术匹配度 40%
           - 项目经验 30%
           - 沟通表达 15%
           - 学习能力 10%
           - 文化匹配 5%
        4. 输出 InterviewReport 模型
        """
```

### 4.6 mcp_servers/question_bank_server.py — 题库 Server（Phase 2 新增）

4 个工具方法：

| 工具 | 说明 |
|------|------|
| `generate_questions(jd_json, skill, difficulty, count)` | LLM 动态出题，委托 InterviewerAgent |
| `search_seed_bank(skill, difficulty, count)` | 种子题库检索，支持按技能/难度过滤 |
| `add_to_seed_bank(question_json)` | 反哺入库，自动去重（按内容） |
| `get_seed_bank_stats()` | 统计信息：总量 / 按技能分布 / 按难度分布 |

数据存储：`data/seed_questions.json` — 纯 JSON 文件，零依赖，可手动编辑。

种子题库初始 **12 道题**，覆盖 9 个技能领域：

| 技能 | 题数 | 难度分布 |
|------|------|----------|
| Python | 3 | basic + intermediate + advanced |
| Redis | 2 | intermediate + advanced |
| Django | 1 | intermediate |
| MySQL | 1 | intermediate |
| RESTful API | 1 | basic |
| Kubernetes | 1 | basic |
| Go | 1 | basic |
| 沟通能力 | 1 | basic |
| 学习能力 | 1 | basic |

### 4.7 web/app.py — Streamlit Web UI（Phase 2 重写）

#### 状态管理

```python
_DEFAULT = {
    "page": "upload",           # 页面路由
    "jd": None,                 # JD 模型
    "resume": None,             # Resume 模型
    "gap_map": None,            # 能力缺口
    "ordered_skills": [],       # 排序技能
    "skill_index": 0,           # 当前技能索引
    "rounds": [],               # 面试轮次记录
    "question": None,            # 当前题目
    "answer": "",               # 当前回答
    "judge_result": None,       # 评判结果
    "terminated": False,        # 是否结束
    "report": None,             # InterviewReport
    "report_loading": False,    # 报告加载状态
    "error": None,              # 错误信息
}
```

#### 多轮对话驱动逻辑

```
用户提交答案
    │
    ▼
_judge_answer(answer)
    │
    ├── 调用 InterviewerAgent.judge_answer()
    ├── 构建 round_data {question, answer, judge}
    ├── 追加到 st.session_state.rounds
    │
    ├── 检查终止条件：
    │   ├── 连续空回答 ≥ max_consecutive_empty → terminated=True
    │   ├── 轮次 ≥ max_rounds → terminated=True
    │   └── next_action == "switch" → skill_index += 1
    │
    └── st.rerun()
        │
        ▼
    页面显示评判结果 + "继续追问" / "换技能" / "查看报告" 按钮
        │
        ▼
    用户点击"继续追问"
        │
        ▼
    _generate_question()
        │
        ├── 根据 last_round.judge.next_action 选择策略：
        │   ├── deepen   → generate_deepen_question()
        │   ├── clarify  → generate_clarify_question()
        │   ├── switch   → skill_index+1, generate_switch_question()
        │   └── 默认      → generate_question()
        │
        └── st.rerun() → 显示新题目 → 等待回答
```

#### 5 维度报告展示

```
分数 ≥ 70 → 🟢 绿色
分数 ≥ 50 → 🟡 黄色
分数 < 50 → 🔴 红色

柱状图（pandas + st.bar_chart）
  维度 | 得分
  技术匹配度 | 85
  项目经验   | 72
  沟通表达   | 68
  学习能力   | 90
  文化匹配   | 75

录用建议：
  strong_yes → ✅ 强烈推荐
  yes       → 👍 推荐录用
  hesitate  → 🤔 待定
  no        → ❌ 不推荐
```

### 4.8 prompts/ — Phase 2 新增 Prompt 模板

| 文件 | 核心变量 | 用途 |
|------|----------|------|
| `interviewer_deepen.md` | `{previous_question}`, `{previous_answer}` | 追问技术细节，难度+1 |
| `interviewer_clarify.md` | `{previous_question}`, `{previous_answer}` | 引导具体化/STAR |
| `feedback.md` | `{interview_transcript}`, `{round_count}` | 5 维度评分 |

---

## 5. 代码执行流程（逐步骤）

下面是从上传文件到生成完整报告的 **完整代码执行路径**：

### 步骤 1：上传文件 + 初始化

```
web/app.py → render_upload_page()
  ↓ st.file_uploader 接收 JD + 简历
  ↓ 点击 "开始面试"
  ↓
_init_interview(jd_file, resume_file)
  ↓
tools/__init__.py → parse_file(jd_path) + parse_file(resume_path)
  ↓ PDF / DOCX / TXT → 纯文本
```

### 步骤 2：LLM 解析 JD + 简历

```
JDParserAgent.run(jd_raw)       → JD 模型（技能+权重）
ResumeAnalyzerAgent.run(resume_raw) → Resume 模型（技能+项目）
```

### 步骤 3：交叉匹配

```
orchestration/matcher.py → rank_skills(jd, resume)
  → generate_gap_map(jd, resume)
  → 排序后技能列表 + 能力缺口 Map
```

### 步骤 4：生成第一题

```
InterviewerAgent.generate_question(
    jd=jd, resume=resume,
    target_skill=ordered_skills[0]["skill"],
    difficulty="intermediate"（有项目经验）或 "basic"（缺口）,
)
  ↓ LLM → Question 模型
```

### 步骤 5：多轮循环（重复执行直到终止）

```
轮次 1:
  render_interview_page() → 显示第一题
  用户输入回答 → 点击 "提交答案"
  → _judge_answer(answer)
    → InterviewerAgent.judge_answer() → JudgeResult{score=85, next_action="deepen"}
    → 记录 RoundRecord → st.session_state.rounds.append(...)
  → 页面显示评判结果 + "继续追问" 按钮

轮次 2:
  用户点击 "继续追问"
  → _generate_question()
    → last_round.judge.next_action == "deepen"
    → InterviewerAgent.generate_deepen_question(
        previous_question=..., previous_answer=..., difficulty="advanced"
      )
    → LLM → 更深的问题
  → 显示新题目 → 用户输入... → 评判...

轮次 3:
  用户回答模糊，next_action="clarify"
  → generate_clarify_question() → 引导具体化

轮次 4:
  用户答不上，next_action="switch"
  → skill_index += 1 → 切到下一技能
  → generate_switch_question() → 新技能出基础题

... 直到终止条件触发 ...

终止:
  terminated=True
  → 显示 "面试已结束" + 简要总结
  → 点击 "生成完整评分报告"
```

### 步骤 6：反馈 Agent 生成报告

```
FeedbackAgent.generate_report(jd, resume, rounds)
  ↓ _build_transcript() → 构建面试记录文本
  ↓ 填充 prompts/feedback.md
  ↓ LLM → InterviewReport 模型
    ├── total_score: 82.5
    ├── dimension_scores: {技术匹配: 85, 项目经验: 72, ...}
    ├── strengths: ["基础扎实", "有实际项目经验"]
    ├── weaknesses: ["深度不足", "缺少系统设计经验"]
    ├── suggestions: ["多阅读开源项目源码"]
    └── hiring_recommendation: "yes"
```

### 步骤 7：展示完整报告

```
render_result_page()
  ↓ 检测到 report != None
  ↓ _render_full_report(report, rounds)
    ├── 总分 + 录用建议
    ├── 5 维度柱状图（pandas + st.bar_chart）
    ├── 各技能得分明细（progress bar）
    ├── 亮点 vs 不足
    └── 改进建议
  ↓ _render_all_rounds_detail(rounds)
    └── 逐轮展开：问题 → 回答 → 评分 → 评价
```

---

## 6. 测试覆盖

### 测试文件

| 文件 | 测试内容 | 用例数 |
|------|----------|--------|
| `tests/test_parse_pipeline.py` | 文件解析管道 | 5 |
| `tests/test_agents.py` | Agent 基类 + JSON 解析 + MCP Server 导入 | 8 |
| `tests/test_interviewer.py` | 面试官 + 技能排序 + 多轮模型 + 状态管理 | 11 |
| **合计** | **3 个文件** | **24** |

### Phase 2 新增测试（test_interviewer.py 增加 4 个）

| 测试 | 验证内容 |
|------|----------|
| `test_round_record_model()` | RoundRecord Pydantic 模型序列化 |
| `test_interview_report_model()` | InterviewReport 5 维度报告模型 |
| `test_round_state_model()` | RoundState 模型（models/interview.py） |
| `test_interviewer_agent_has_multi_round_methods()` | 验证 deepen/clarify/switch 方法存在 |
| `test_supervisor_state()` | 验证 supervisor 初始状态含多轮字段 |

### 测试类型

- **单元测试**：验证函数逻辑、模型实例化、JSON 解析
- **结构测试**：验证导入路径、模块可实例化
- **排序算法测试**：验证 rank_skills 三种场景（有项目/缺口/加分）
- **多轮模型测试**：验证 RoundRecord、InterviewReport 数据完整性

---

## 7. 启动方式

```bash
# 1. 激活虚拟环境
cd d:/lineageAgent
source .venv/Scripts/activate

# 2. 启动 Web UI
python main.py web

# 3. 启动题库 MCP Server（可选，独立使用）
python -m mcp_servers.question_bank_server

# 4. 运行测试
.venv/Scripts/python.exe tests/test_parse_pipeline.py
.venv/Scripts/python.exe tests/test_agents.py
.venv/Scripts/python.exe tests/test_interviewer.py
```

### 依赖清单

| 包 | 用途 | Phase |
|----|------|-------|
| `openai>=1.30.0` | DeepSeek API 调用 | 1 |
| `langgraph>=0.2.0` | 多 Agent 编排框架 | 1 |
| `mcp>=1.0.0` | MCP 协议 | 1 |
| `chromadb>=0.5.0` | 向量数据库 | 3 |
| `pydantic>=2.0.0` | 数据模型校验 | 1 |
| `pdfplumber>=0.10.0` | PDF 文件解析 | 1 |
| `python-docx>=1.1.0` | DOCX 文件解析 | 1 |
| `fastapi>=0.110.0` | MCP Gateway | 3 |
| `streamlit>=1.30.0` | Web UI 框架 | 1 |
| `python-dotenv>=1.0.0` | .env 环境变量加载 | 1 |
| `typer>=0.9.0` | CLI 命令行框架 | 1 |
| `rich>=13.0.0` | 终端彩色输出 | 1 |

---

## 附录：关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 多轮循环方式 | Web UI 手动驱动，而不是 LangGraph interrupt | Web UI 已经直接调用 Agent，LangGraph 图作为"规范状态机"保持一致 |
| 追问策略实现 | 3 个独立方法（deepen/clarify/switch）而非通用 prompt | 每个策略的 prompt 差异大，独立方法更清晰可维护 |
| 评分报告时机 | 先展示基础分数，再异步生成完整报告 | 避免面试结束后等 LLM 生成报告的白屏时间 |
| 题库实现 | LLM 动态生成为主 + JSON 种子库为辅 | 零依赖、无数据库运维成本 |
| 种子数据格式 | 纯 JSON 文件 | 可版本管理、可手动编辑、运行时反哺 |
| 5 维度评分 | LLM 一次性生成全部维度 | 维度之间有关联性，分开多次调用会丢失整体判断 |
| 终止条件优先级 | 空回答 > 轮次上限 > 技能覆盖 > next_action | 保护系统稳定性优先，用户体感其次 |
| Web UI 与 LangGraph | 部分分离（UI 调 Agent，Graph 存定义） | Phase 1 延续，减少重构风险，留 Phase 3 Gateway 统一 |
