# Phase 1 实现总结 — AI 面试官 MVP

> 对应 ROADMAP.md 第 1-2 周目标：跑通「上传文件 → 解析 → 出题 → 回答 → 评分」最小闭环

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
┌──────────────────────────────────────────────────────────────────────┐
│                         用户界面层                                    │
│  Streamlit Web UI (web/app.py)  /  CLI (main.py)                    │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ 上传文件 / 输入回答
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         编排层                                        │
│  LangGraph 图 (orchestration/supervisor.py)                        │
│  定义节点执行顺序：parse_jd → parse_resume → match → generate → judge│
│  状态管理 (InterviewState)                                           │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ 调用 Agent
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Agent 层                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │JDParserAgent│  │ResumeAnalyzer│  │Interviewer   │               │
│  │  JD → 结构化 │  │ 简历 → 画像  │  │ 出题 + 评判   │               │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘               │
│         │                │                  │                        │
│         └────────────────┴──────────────────┘                        │
│                          │                                            │
│                          ▼                                            │
│                 BaseAgent (agents/base.py)                            │
│                 LLM 调用 + JSON 解析 + 重试机制                        │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ 调 DeepSeek API
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         LLM 层                                        │
│  models/llm.py — DeepSeek V4 Pro 封装                                │
│  流式 / 非流式、OpenAI-compatible API                                 │
└──────────────────────────────────────────────────────────────────────┘
```

### 分层职责

| 层级 | 目录 | 职责 |
|------|------|------|
| 界面层 | `web/` | Streamlit 页面路由、文件上传、对话展示、评分报告 |
| 编排层 | `orchestration/` | LangGraph 图定义、状态管理、技能匹配排序 |
| Agent 层 | `agents/` | 具体业务 Agent：JD 解析、简历分析、出题、评判 |
| 工具层 | `tools/` | 文件解析（PDF/DOCX/TXT）、文本清洗 |
| 模型层 | `models/` | Pydantic 数据模型、LLM 调用封装 |
| 提示词层 | `prompts/` | 各 Agent 的 System Prompt 模板 |
| 配置层 | `config.py` | 全局配置（API Key、路径、参数） |

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
│   └── interviewer.py        # — 面试官 Agent（出题+评判）
│
├── models/                   # 模型层
│   ├── __init__.py
│   ├── llm.py                # — DeepSeek LLM 封装
│   ├── jd.py                 # — JD / Skill Pydantic 模型
│   ├── resume.py             # — Resume / Project / SkillProficiency
│   ├── question.py           # — Question / Answer / JudgeResult
│   └── interview.py          # — InterviewState / RoundState
│
├── orchestration/            # 编排层
│   ├── __init__.py
│   ├── matcher.py            # — 交叉匹配 + 能力缺口分析
│   └── supervisor.py         # — LangGraph 图 + 状态管理
│
├── mcp_servers/              # MCP Server（预留 Phase 3）
│   ├── __init__.py
│   ├── jd_server.py          # — JD MCP Server
│   └── resume_server.py      # — 简历 MCP Server
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
│   └── judge.md              # — 评判 prompt
│
├── web/                      # 界面层
│   ├── __init__.py
│   └── app.py                # — Streamlit Web UI
│
├── tests/                    # 测试
│   ├── __init__.py
│   ├── test_parse_pipeline.py
│   ├── test_agents.py
│   ├── test_interviewer.py
│   └── fixtures/
│       ├── sample_jd.txt
│       └── sample_resume.txt
│
└── doc/
    └── phase1-overview.md    # ← 本文档
```

---

## 3. 核心数据流

```
                    用户上传文件
                         │
                         ▼
              ┌─────────────────────┐
              │   tools/parse_file  │  自动识别 .pdf / .docx / .txt
              │   返回清洗后纯文本   │
              └─────────┬───────────┘
                        │ jd_raw, resume_raw
                        ▼
              ┌─────────────────────┐
              │  JDParserAgent.run  │  LLM 解析 → JD 模型
              │  结构化：技能+权重   │
              └─────────┬───────────┘
                        │
              ┌─────────────────────┐
              │ ResumeAnalyzerAgent │  LLM 解析 → Resume 模型
              │  .run               │  结构化：技能熟练度+项目
              └─────────┬───────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │ matcher.rank_skills │  纯逻辑代码（不调 LLM）
              │  -> [排序后技能列表] │  按「有项目 > 缺口 > 加分」排序
              └─────────┬───────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │ InterviewerAgent    │  LLM 出题 → Question 模型
              │ .generate_question  │  结合 JD + 简历 + 目标技能
              └─────────┬───────────┘
                        │
                  用户手动回答
                        │
                        ▼
              ┌─────────────────────┐
              │ InterviewerAgent    │  LLM 评判 → JudgeResult
              │ .judge_answer       │  评分 + 亮点 + 不足 + 建议
              └─────────┬───────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │  Streamlit UI 展示   │  评分报告 + 能力缺口图
              │  评分 / 亮点 / 不足  │
              └─────────────────────┘
```

---

## 4. 模块详解

### 4.1 config.py — 配置管理

```python
# 核心字段
config.llm_api_key       # DeepSeek API Key
config.llm_base_url       # https://api.deepseek.com
config.llm_model          # deepseek-v4-pro
config.llm_temperature    # 0.7（结构化任务用低温度）
config.max_rounds         # 10（Phase 2 多轮上限）
```

- 单例模式，全项目共享
- 环境变量由 `.env` 文件加载（`python-dotenv`）
- 自动创建 `data/`、`logs/`、`uploads/` 目录

### 4.2 models/llm.py — LLM 封装

```python
llm = LLM()

# 简单场景（出题/评分）：system + user
text = await llm.generate(system_prompt, user_prompt)

# 多轮对话：传入完整消息列表
text = await llm.generate_with_messages(messages)

# 流式输出
stream = await llm.generate(system, user, stream=True)
async for chunk in stream:
    print(chunk)
```

- 延迟初始化（`__init__` 时不创建 client，首次调用才创建）
- 兼容 OpenAI SDK 格式，只需改 `base_url`
- 支持流式与非流式两种模式

### 4.3 agents/base.py — Agent 基类

```python
class BaseAgent:
    async def run(self, user_prompt, response_model, system_prompt, max_retries=2):
        # 1. 调 LLM.generate()
        # 2. 尝试 3 种方式解析 JSON：
        #    a. 直接解析
        #    b. ```json ... ``` 代码块提取
        #    c. { ... } 大括号提取
        # 3. 解析为目标 Pydantic 模型
        # 4. 失败自动重试（最多 2 次）
```

所有 Agent 继承 `BaseAgent`，只需覆盖 `run()` 方法即可。

### 4.4 agents/jd_parser.py — JD 解析 Agent

```
输入：JD 文本
↓
LLM 调用（prompt: jd_parser.md）
↓
输出：JD 模型
  ├── title: str               # 岗位名称
  ├── required_skills: list    # 必备技能 + 权重(1-100)
  ├── bonus_skills: list       # 加分技能
  ├── experience_years: int    # 经验要求
  ├── education: str           # 学历要求
  └── soft_skills: list        # 软技能
```

### 4.5 agents/resume_analyzer.py — 简历分析 Agent

```
输入：简历文本
↓
LLM 调用（prompt: resume_analyzer.md）
↓
输出：Resume 模型
  ├── name: str                # 候选人姓名
  ├── skills: list             # 技能熟练度 (expert/proficient/familiar/basic)
  ├── projects: list           # 项目经历（名称/角色/技术栈/亮点）
  └── experience_years: int    # 工作经验
```

### 4.6 agents/interviewer.py — 面试官 Agent

两个核心方法：

**generate_question()** — 出题
```
输入：JD 模型 + Resume 模型 + 目标技能 + 难度
↓
填充 prompt 模板（interviewer.md）
  - 岗位信息、候选人技能、项目背景、考察意图
↓
LLM 调用
↓
输出：Question 模型
  ├── skill: str               # 考察技能
  ├── difficulty: enum         # basic / intermediate / advanced / deep
  ├── content: str             # 题目内容
  ├── context: str             # 出题背景
  └── expected_answer_points   # 预期得分点
```

**judge_answer()** — 评判
```
输入：Question 模型 + 回答文本
↓
填充 prompt 模板（judge.md）
  - 题目内容、预期得分点、实际回答
↓
LLM 调用（4 维度评分）
  - 技术准确性 0-40
  - 深度与广度 0-30
  - 结构化表达 0-20
  - 实践结合 0-10
↓
输出：JudgeResult 模型
  ├── score: int               # 0-100
  ├── comment: str             # 综合评价
  ├── strength_points          # 亮点列表
  ├── weakness_points          # 不足列表
  └── next_action              # deepen/clarify/switch/end
```

### 4.7 orchestration/matcher.py — 能力缺口分析

**纯逻辑代码**，不调用 LLM。核心函数 `rank_skills()`：

```
输入：JD 模型 + Resume 模型
↓
对每个 JD 技能：
  1. 在简历技能列表中查找
  2. 在简历项目技术栈中查找
  3. 标记为：有项目经验 / 有技能无项目 / 缺口
↓
排序规则：
  1. 有项目经验的 → 先问（能展开聊）
  2. 有技能无项目 → 其次（验证真实水平）
  3. 缺口技能 → 再问（重点考察）
  4. 加分技能 → 最后
↓
输出：[排序后技能列表]（带 priority）
```

### 4.8 orchestration/supervisor.py — LangGraph 编排

```python
# Graph 节点（线性链）
parse_jd → parse_resume → match_skills → generate_question → judge_answer → END

# 状态管理（InterviewState）
{
    "jd_path": "xxx.pdf",
    "resume_path": "xxx.pdf",
    "jd": JD | None,           # JD 解析结果
    "resume": Resume | None,   # 简历解析结果
    "gap_map": {...},           # 能力缺口
    "ordered_skills": [...],    # 排序技能列表
    "question": Question,       # 当前题目
    "answer": "...",            # 候选人回答
    "judge_result": JudgeResult,# 评判结果
    "error": "..." | None,      # 错误信息
}
```

### 4.9 web/app.py — Streamlit Web UI

三个页面状态切换：

```
upload ──(文件上传 + 点"开始面试")──→ interview ──(点"查看报告")──→ result
  ↑                                                                    │
  └─────────────────────(点"重新开始")──────────────────────────────────┘
```

**upload 页面**：
- 左右两列：上传 JD 文件 + 上传简历文件
- 点击"开始面试"触发完整后端链路
- 加载中 spinner 展示进度

**interview 页面**：
- 顶部：岗位名称 / 候选人姓名 / 技能总数（metric 卡片）
- 可展开：能力缺口分析
- 对话历史展示（面试官 ↔ 候选人）
- 底部：文本输入框 + "提交答案"按钮
- 提交后展示评判结果 + "查看报告"按钮

**result 页面**：
- 岗位信息 vs 候选人信息
- 评分结果（颜色编码：🟢≥70 / 🟡≥50 / 🔴<50）
- 亮点 vs 待改进
- 能力缺口完整分析
- 题目回顾 + 完整面试记录
- "再来一轮"按钮重置

---

## 5. 代码执行流程（逐步骤）

下面是从用户上传文件到看到评分的 **完整代码执行路径**：

### 步骤 1：上传文件

```
web/app.py → render_upload_page()
  ↓ st.file_uploader 接收文件
  ↓ 点击 "开始面试" 按钮
  ↓
_run_interview_setup(jd_file, resume_file)
```

### 步骤 2：保存文件 → 解析为纯文本

```
tools/__init__.py → parse_file(path)
  ↓ 判断扩展名
  ↓ .pdf → tools/pdf_parser.py → pdfplumber.open() → extract_text()
  ↓ .docx → tools/docx_parser.py → Document() → 段落 + 表格
  ↓ .txt → path.read_text()
  ↓
tools/text_cleaner.py → clean_text()
  ↓ 去空白、去零宽字符、压缩换行
  ↓ 返回纯文本字符串
```

### 步骤 3：LLM 解析 JD

```
agents/jd_parser.py → JDParserAgent.run(jd_raw)
  ↓ 继承 BaseAgent.run()
  ↓ 加载 prompts/jd_parser.md 作为 system prompt
  ↓ 调 models/llm.py → LLM.generate(system, jd_raw)
  ↓ DeepSeek API → 返回 JSON
  ↓ BaseAgent._parse_response() → JD 模型
```

### 步骤 4：LLM 解析简历

```
agents/resume_analyzer.py → ResumeAnalyzerAgent.run(resume_raw)
  ↓ 同理，加载 prompts/resume_analyzer.md
  ↓ LLM → Resume 模型
```

### 步骤 5：交叉匹配

```
orchestration/matcher.py → rank_skills(jd, resume)
  ↓ 逐技能对比简历 + 项目技术栈
  ↓ 标记 4 种状态：有项目经验 / 有技能无项目 / 缺口 / 加分
  ↓ 按优先级排序
  ↓ 返回 [skill, weight, level, gap, reason, priority]
```

### 步骤 6：LLM 出题

```
agents/interviewer.py → InterviewerAgent.generate_question()
  ↓ 填充 prompts/interviewer.md 模板（5 个变量）
  ↓ 调 LLM → Question 模型（content + difficulty + expected_answer_points）
```

### 步骤 7：用户输入回答

```
web/app.py → render_interview_page()
  ↓ st.text_area 接收回答文本
  ↓ 点击 "提交答案"
  ↓ 保存到 st.session_state
```

### 步骤 8：LLM 评判

```
agents/interviewer.py → InterviewerAgent.judge_answer()
  ↓ 填充 prompts/judge.md 模板（题目 + 回答 + 预期得分点）
  ↓ 调 LLM → 4 维度评分 → JudgeResult 模型
  ↓ score / comment / strength_points / weakness_points / next_action
```

### 步骤 9：展示报告

```
web/app.py → render_result_page()
  ↓ 岗位信息 + 候选人信息
  ↓ 评分（颜色编码）
  ↓ 亮点 vs 待改进
  ↓ 能力缺口完整分析
  ↓ 题目回顾 + 完整面试记录
```

---

## 6. 测试覆盖

### 测试文件

| 文件 | 测试内容 | 用例数 |
|------|----------|--------|
| `tests/test_parse_pipeline.py` | 文件解析管道 | 5 |
| `tests/test_agents.py` | Agent 基类 + JSON 解析 | 8 |
| `tests/test_interviewer.py` | 面试官 + 技能排序 | 6 |
| **合计** | **19 个测试用例** | **19** |

### 测试类型

- **单元测试**：验证函数逻辑、模型实例化、JSON 解析
- **结构测试**：验证导入路径、模块可实例化
- **排序算法测试**：验证 rank_skills 三种场景
- **LLM 直调测试**：调 DeepSeek API 出题+评判（需要通过 `python main.py web` 端到端验证）

---

## 7. 启动方式

```bash
# 1. 配置 API Key（已创建）
#   编辑 .env 中的 DEEPSEEK_API_KEY

# 2. 启动 Web UI
cd d:/lineageAgent
source .venv/Scripts/activate
python main.py web

# 3. 启动 MCP Gateway（Phase 3 功能，预留接口）
python main.py gateway

# 4. 查看历史面试记录（Phase 3 功能）
python main.py history --candidate "张三"

# 5. 运行测试
python tests/test_parse_pipeline.py
python tests/test_agents.py
python tests/test_interviewer.py
```

### 依赖清单

| 包 | 用途 |
|----|------|
| `openai>=1.30.0` | DeepSeek API 调用（兼容 OpenAI SDK） |
| `langgraph>=0.2.0` | 多 Agent 编排框架 |
| `mcp>=1.0.0` | MCP 协议（Phase 3 启用） |
| `chromadb>=0.5.0` | 向量数据库（Phase 3 启用） |
| `pydantic>=2.0.0` | 数据模型校验 |
| `pdfplumber>=0.10.0` | PDF 文件解析 |
| `python-docx>=1.1.0` | DOCX 文件解析 |
| `fastapi>=0.110.0` | MCP Gateway（Phase 3 启用） |
| `streamlit>=1.30.0` | Web UI 框架 |
| `python-dotenv>=1.0.0` | .env 环境变量加载 |
| `typer>=0.9.0` | CLI 命令行框架 |
| `rich>=13.0.0` | 终端彩色输出 |

---

## 附录：关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| LLM 调用方式 | 直调 LLM，不包装 MCP | Phase 1 以跑通为主，避免过度工程化 |
| Agent 基类 | 继承模式 | 统一 JSON 解析 + 重试，子类只需关心业务 |
| 技能排序 | 纯逻辑代码 | 不需要 LLM 参与，减少 API 调用成本 |
| Web UI | Streamlit | 快速出 MVP，单文件搞定 |
| 状态管理 | LangGraph + st.session_state | LangGraph 留架构接口，UI 层用 session_state 简化 |
| 模型命名 | deepseek-v4-pro | 对应 CLAUDE.md 技术栈 DeepSeek V4 Pro |
