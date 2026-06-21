# 🎯 AI 面试官：基于 LangGraph + MCP 的多 Agent 面试系统 — 全栈技术手册

> 这是一份写给自己的项目技术手册。覆盖从底层数据模型到顶层 UI 交付的每一个细节。
>
> 上传 JD + 简历 → AI 自动解析匹配 → 多轮追问面试 → 五维度评分报告。用 LangGraph 做编排、MCP 协议做工具解耦、ChromaDB 做长期记忆，一套完整的 AI 面试闭环。

---

## 目录

- [项目概览](#项目概览)
- [Part 1：数据模型 — Pydantic 类型体系](#part-1数据模型--pydantic-类型体系)
- [Part 2：LLM 调用层 — DeepSeek 封装](#part-2llm-调用层--deepseek-封装)
- [Part 3：配置系统 — 环境区分 + 全局单例](#part-3配置系统--环境区分--全局单例)
- [Part 4：文件解析工具 — 友好错误提示](#part-4文件解析工具--友好错误提示)
- [Part 5：Prompt 工程 — 模板管理与变量校验](#part-5prompt-工程--模板管理与变量校验)
- [Part 6：Agent 基类 — 类型安全 + 重试 + 流式](#part-6agent-基类--类型安全--重试--流式)
- [Part 7：四个业务 Agent](#part-7四个业务-agent)
- [Part 8：技能匹配与面试排序](#part-8技能匹配与面试排序)
- [Part 9：LangGraph 编排 — 状态机与条件路由](#part-9langgraph-编排--状态机与条件路由)
- [Part 10：MCP Gateway — 企业级工程化](#part-10mcp-gateway--企业级工程化)
- [Part 11：Gradio 前端 — 三步流程](#part-11gradio-前端--三步流程)
- [Part 12：记忆系统 — 短期 + 长期](#part-12记忆系统--短期--长期)
- [Part 13：MCP Server 详解](#part-13mcp-server-详解)
- [Part 14：部署与启动](#part-14部署与启动)
- [Part 15：Phase 5 改动日志](#part-15phase-5-改动日志)
- [Part 16：设计亮点与感悟](#part-16设计亮点与感悟)

---

## 项目概览

**AI 面试官（InterviewAgentHub）** 是一个基于 LangGraph + MCP 协议的多 Agent 面试系统。模拟真实技术面试的完整流程：

```
上传 JD + 简历
    ↓
文件解析（PDF / DOCX / TXT）
    ↓
LLM 提取 JD 技能 + 权重
    ↓
LLM 提取候选人技能 + 项目经历
    ↓
交叉匹配 → 能力缺口 Map → 技能排序
    ↓
按优先级逐轮出题 ←──────── 长期记忆参考
    ↓                         (ChromaDB)
候选人回答
    ↓
LLM 评判 → 决策下一步
    ├─ deepen (答得好 → 深挖)
    ├─ clarify (答得模糊 → 澄清)
    ├─ switch (答不上 → 换技能)
    └─ end (触发终止条件)
    ↓
五维度评分报告 + 录用建议
```

**项目定位**：展示"能用主流 Agent 框架做出完整业务闭环"，而非玩具 Demo。

### 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| LLM | DeepSeek V4 Pro | OpenAI 兼容 API，定价极低 |
| 编排 | LangGraph | StateGraph + 条件边 + Checkpoint |
| Embedding | BAAI/bge-base-zh-v1.5 | 768 维，中文 SOTA，本地部署 |
| 向量库 | ChromaDB | 本地持久化，2 个 Collection |
| 后端 | FastAPI | Gateway + REST API + MCP SSE |
| MCP | FastMCP SDK | 3 个独立 Server |
| 前端 | Gradio 5 | 独立端口，原生 async |
| 文件解析 | pdfplumber + python-docx | 全格式支持 |
| 存储 | JSON 文件 | 会话持久化 |
| 语言 | Python 3.12 | 全栈 |

### 项目结构

```
InterviewAgentHub/
├── agents/                     # 4 个 Agent
│   ├── base.py                 # Agent 基类（重试 + 解析 + 流式）
│   ├── jd_parser.py            # JD 解析
│   ├── resume_analyzer.py      # 简历分析
│   ├── interviewer.py          # 面试官（核心，多轮追问）
│   └── feedback.py             # 反馈（评分报告）
├── models/                     # Pydantic 数据模型
│   ├── jd.py                   # JD / Skill
│   ├── resume.py               # Resume / Project / SkillProficiency
│   ├── question.py             # Question / JudgeResult / RoundRecord / InterviewReport
│   ├── interview.py            # InterviewState（持久化版）
│   └── llm.py                  # DeepSeek LLM 封装
├── orchestration/              # 编排层
│   ├── supervisor.py           # LangGraph StateGraph + 节点 + 条件路由
│   └── matcher.py              # JD ↔ 简历交叉匹配 + 技能排序
├── mcp_servers/                # MCP 层
│   ├── gateway.py              # FastAPI Gateway（鉴权/限流/熔断）
│   ├── jd_server.py            # JD MCP Server
│   ├── resume_server.py        # 简历 MCP Server
│   └── question_bank_server.py # 题库 MCP Server
├── memory/                     # 记忆层
│   ├── vector_store.py         # ChromaDB 向量库（2 Collections，降级）
│   └── session_store.py        # 会话持久化（JSON）
├── tools/                      # 工具层
│   ├── __init__.py             # 统一入口 parse_file()
│   ├── pdf_parser.py           # PDF 解析
│   ├── docx_parser.py          # DOCX 解析
│   └── text_cleaner.py         # 文本清洗
├── prompts/                    # 7 个 Prompt 模板（Markdown）
│   ├── interviewer.md          # 出题
│   ├── interviewer_deepen.md   # 追问加深
│   ├── interviewer_clarify.md  # 追问澄清
│   ├── judge.md                # 答案评判
│   ├── jd_parser.md            # JD 解析
│   ├── resume_analyzer.md      # 简历分析
│   └── feedback.md             # 评分报告
├── web/
│   └── app.py                  # Gradio UI
├── docs/                       # 3 个文档
│   ├── CLAUDE.md               # 项目总览
│   ├── blog-ai-interviewer.md  # 本文件
│   └── optimization-roadmap.md # 优化路线图
├── config.py                   # 全局配置（dataclass 单例）
├── main.py                     # CLI 入口（Typer）
└── .env / .env.example         # 环境变量
```

---

## Part 1：数据模型 — Pydantic 类型体系

整个系统的数据流建立在 Pydantic 模型之上。LLM 输出直接解析为结构化对象，后续代码全部通过 `.` 访问字段，不存在字符串拼接和 KeyError。

### 1.1 JD 相关（`models/jd.py`）

```python
class Skill(BaseModel):
    name: str                          # 技能名，如 "Python"
    weight: int = Field(ge=1, le=100)  # 权重 1-100
    is_bonus: bool = False             # True = 加分项

class JD(BaseModel):
    title: str                         # 岗位名称
    company: Optional[str] = None
    required_skills: list[Skill] = []   # 必需技能
    bonus_skills: list[Skill] = []      # 加分技能
    experience_years: Optional[int] = None
    education: Optional[str] = None
    soft_skills: list[str] = []
    raw_text: str = ""                 # 保留原始文本，调试用
```

- `weight` 带约束 `ge=1, le=100`，Pydantic 自动校验
- `is_bonus` 标记加分项，在排序时加权靠后

### 1.2 简历相关（`models/resume.py`）

```python
class SkillProficiency(BaseModel):
    name: str
    level: str = "familiar"   # expert / proficient / familiar / basic
    years: Optional[float] = None

class Project(BaseModel):
    name: str
    role: str                  # 在项目中的角色
    description: str
    tech_stack: list[str] = [] # 使用的技术栈
    highlights: list[str] = [] # 项目亮点

class Resume(BaseModel):
    name: str
    title: Optional[str] = None    # 当前/最近职位
    skills: list[SkillProficiency] = []
    projects: list[Project] = []
    experience_years: Optional[float] = None
    education: Optional[str] = None
    raw_text: str = ""
```

- `SkillProficiency` 区分了技能**名称**和**水平**，match 时优先匹配技能名
- `Project` 拆出 `tech_stack` 独立字段，供 matcher 精确匹配

### 1.3 面试相关（`models/question.py`）

```python
class Difficulty(str, Enum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    DEEP = "deep"

class Question(BaseModel):
    skill: str
    difficulty: Difficulty
    content: str                          # 题目正文
    context: Optional[str] = None         # 出题背景（基于哪个项目/缺口）
    expected_answer_points: list[str] = [] # 期望得分点

class JudgeResult(BaseModel):
    score: int = Field(ge=0, le=100)
    comment: str
    strength_points: list[str] = []
    weakness_points: list[str] = []
    next_action: str                     # deepen / clarify / switch / end
    soft_skills_assessment: Optional[str] = None

class RoundRecord(BaseModel):
    round_number: int
    skill: str
    question: Question
    answer: str
    judge: JudgeResult

class InterviewReport(BaseModel):
    total_score: float = Field(ge=0, le=100)
    dimension_scores: dict[str, float] = {}  # 五维度分项
    skill_scores: list[dict] = []            # 逐技能评分
    strengths: list[str] = []
    weaknesses: list[str] = []
    suggestions: list[str] = []
    overall_assessment: str = ""
    hiring_recommendation: str = ""          # strong_yes / yes / hesitate / no
```

关键设计：
- `Difficulty` 是 `str, Enum`——既可以用字符串比较，又有枚举的类型安全
- `JudgeResult.next_action` 是整个追问策略的**唯一动力源**。四个字符串值（deepen / clarify / switch / end）直接决定 LangGraph 状态机的下一个节点
- `Question.expected_answer_points` 是 LLM 出题时自动生成的评分标准，评判时喂回 LLM 参考，形成自洽闭环

### 1.4 会话状态（`models/interview.py`）

```python
class InterviewStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    TERMINATED = "terminated"

class InterviewState(BaseModel):
    """Pydantic 持久化版 — 供 SessionStore 序列化"""
    interview_id: str = ""
    status: InterviewStatus = InterviewStatus.CREATED
    jd: Optional[JD] = None
    resume: Optional[Resume] = None
    gap_analysis: Optional[dict] = None
    rounds: list[RoundState] = []
    current_round: int = 0
    candidate_name: str = "匿名"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

LangGraph 用的是 `TypedDict` 版（在 supervisor.py 中），这个是持久化版。两者通过 `_state_to_pydantic()` / `_pydantic_to_state()` 互转。

---

## Part 2：LLM 调用层 — DeepSeek 封装

`models/llm.py` 极其精简——只是对 `openai.AsyncOpenAI` 的最薄封装：

```python
class LLM:
    def __init__(self):
        self._client: AsyncOpenAI | None = None  # 延迟初始化

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=config.llm_api_key,
                base_url=config.llm_base_url,
            )
        return self._client

    async def generate(
        self, system_prompt: str, user_prompt: str,
        stream: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str | AsyncIterator[str]:
        """非流式返回完整文本，流式返回 AsyncIterator[str]"""
        response = await self.client.chat.completions.create(
            model=config.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature or config.llm_temperature,
            max_tokens=max_tokens or config.llm_max_tokens,
            stream=stream,
        )
        if stream:
            return self._stream_handler(response)
        return response.choices[0].message.content or ""

    async def _stream_handler(self, response) -> AsyncIterator[str]:
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
```

设计决策：
- **延迟初始化**：`_client` 在第一次调用时才创建，模块 import 时不会尝试连接
- **流式与非流式同一接口**：返回类型随 `stream` 参数变化（`str | AsyncIterator[str]`）
- **OpenAI 兼容协议**：DeepSeek API 完全兼容 OpenAI SDK，换模型只需改 `base_url` + `model`
- **`generate_with_messages()`**：额外提供多轮对话接口（传入完整 messages 列表）

---

## Part 3：配置系统 — 环境区分 + 全局单例

`config.py` 用 `dataclass` 实现，全项目通过 `from config import config` 导入同一个单例：

```python
@dataclass
class Config:
    # ── 环境 ──
    env: str = "dev"               # 读取 ENV 环境变量

    # ── LLM ──
    llm_api_key: str               # DEEPSEEK_API_KEY
    llm_base_url: str              # 默认 https://api.deepseek.com
    llm_model: str                 # 默认 deepseek-v4-pro
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    llm_streaming: bool = True

    # ── Paths ──
    data_dir: Path = ROOT_DIR / "data"
    logs_dir: Path = ROOT_DIR / "logs"
    session_dir: Path = data_dir / "sessions"
    cache_dir: Path = data_dir / "cache"

    # ── ChromaDB ──
    chroma_persist_dir: Path = data_dir / "chroma"

    # ── Embedding ──
    embedding_model: str = "D:/model/bge-base-zh-v1.5"  # 本地 768 维

    # ── Interview ──
    max_rounds: int = 10
    max_consecutive_empty: int = 3

    # ── Gateway ──
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    gradio_ui_port: int = 7860
    gateway_api_key: str           # Bearer Token
    gateway_require_auth: bool     # dev 环境自动 false
    gateway_rate_limit: int = 60   # 令牌桶容量

    # ── Feature flags ──
    use_vector_memory: bool = True # 可通过 NO_VECTOR_MEMORY 关闭

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

config = Config()  # 全局单例
```

环境区分逻辑：

```python
# dev 环境自动：
#   gateway_require_auth = False
#   log_level = DEBUG
# prod 环境自动：
#   gateway_require_auth = True
#   log_level = INFO
```

所有配置值都有对应的环境变量覆盖能力（`field(default_factory=lambda: os.getenv(...))` 模式），`.env` 文件由 `python-dotenv` 加载。

---

## Part 4：文件解析工具 — 友好错误提示

`tools/__init__.py` 提供统一的 `parse_file()` 入口，支持 PDF / DOCX / TXT 三种格式：

```python
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

class ParseError(Exception):
    """包含用户友好的中文提示 + 内部调试 detail"""
    def __init__(self, message: str, filename: str = "", detail: str = ""):
        self.filename = filename
        self.detail = detail
        super().__init__(message)

def parse_file(path: str | Path) -> str:
    # 1. 格式检查 → 不支持则抛出中文 ParseError
    # 2. 存在性检查 → 文件不存在给明确提示
    # 3. 空文件检查 → st_size == 0 提前拦截
    # 4. 分派解析：
    #    .pdf  → pdfplumber 提取文本
    #    .docx → python-docx 提取段落
    #    .txt  → 直接读取（utf-8，非法字节用 errors='replace'）
    # 5. 空内容检查 → 解析后无文本则提示"文件可能包含扫描图片"
    # 6. 文本清洗 → clean_text()
```

错误处理分层：
- **文件层**：格式不支持 / 文件不存在 / 空文件 → `ParseError` + 中文提示
- **解析层**：PDF 损坏 / DOCX 格式异常 → 捕获并包装为 `ParseError`
- **清洗层**：清洗后无内容 → `ParseError`

每一层都有明确的中文错误信息，前端直接展示给用户。

---

## Part 5：Prompt 工程 — 模板管理与变量校验

### 5.1 模板加载

`prompts/__init__.py` 管理 7 个 Markdown 模板文件：

```python
_PROMPTS_DIR = Path(__file__).resolve().parent
_CACHE: dict[str, "PromptTemplate"] = {}

def load_prompt(name: str) -> PromptTemplate:
    """按文件名加载（不带 .md 后缀），自动缓存"""
    path = _PROMPTS_DIR / f"{name}.md"
    content = path.read_text(encoding="utf-8")
    variables = set(re.findall(r"\{(\w+)\}", content))  # 自动提取变量
    tmpl = PromptTemplate(name, content, variables)
    _CACHE[name] = tmpl
    return tmpl
```

### 5.2 变量校验

```python
class PromptTemplate:
    def format(self, **kwargs) -> str:
        given = set(kwargs.keys())
        missing = self._variables - given
        if missing:
            raise KeyError(
                f"Prompt '{self.name}' 缺少变量: {sorted(missing)}\n"
                f"  需要: {sorted(self._variables)}\n"
                f"  传入: {sorted(given)}"
            )
        extra = given - self._variables
        if extra:
            logger.warning(f"Prompt '{self.name}' 多余变量: {sorted(extra)}")
        return self._template.format(**kwargs)
```

比原生的 `str.format()` 多两件事：
- **缺失变量**：第一时间 `KeyError`，附带明确缺失列表
- **多余变量**：warning 而非静默忽略，防止拼写错误

### 5.3 模板示例

`prompts/interviewer.md`：

```markdown
你是一个专业的 AI 面试官。请根据候选人信息和岗位要求，生成一道面试题。

### 岗位信息
- 岗位名称：{job_title}
- 核心技能要求：{required_skills}

### 候选人信息
- 技能水平：{candidate_skills}
- 相关项目经验：{candidate_projects}

### 本轮考察
- 考察技能：{target_skill}
- 难度级别：{difficulty}
- 考察意图：{intent}

## 出题规则
1. 难度适配：basic → 概念理解, intermediate → 实际应用,
   advanced → 原理分析, deep → 底层原理追问
2. 结合候选人背景：有项目经验则结合项目场景，无则出基础题
3. 题目要有区分度

## 输出 JSON 格式
{"skill": "...", "difficulty": "...", "content": "...",
 "context": "...", "expected_answer_points": ["..."]}
```

7 个模板统一用这个模式：Markdown 格式 + `{variable}` 占位符 + JSON Schema 定义输出结构。

---

## Part 6：Agent 基类 — 类型安全 + 重试 + 流式

所有业务 Agent 继承自 `agents/base.py` 的 `BaseAgent`：

```python
T = TypeVar("T", bound=BaseModel)  # 泛型，绑定 Pydantic

class BaseAgent:
    def __init__(self, llm: Optional[LLM] = None):
        self.llm = llm or LLM()     # 可注入 mock LLM 用于测试
```

### 6.1 非流式调用（`run`）

```python
async def run(
    self,
    user_prompt: str,
    response_model: Type[T],      # 期望的 Pydantic 模型类
    system_prompt: Optional[str] = None,
    max_retries: int = 2,
    backoff_base: float = 1.0,
) -> T:                           # ← 返回泛型 Pydantic 模型，不是 str
```

内部流程：
```
调用 LLM → 获取文本 → _parse_response() 三层兜底 → 返回 Pydantic 对象
                      ↓ 解析失败
                  指数退避重试（最多 2 次）
                  1s → 2s → 抛 RuntimeError
```

**指数退避重试**区分两种失败类型：
- `json.JSONDecodeError / ValueError / KeyError`：解析问题，可能通过重试纠正（LLM 输出不稳定）
- 通用 `Exception`：网络超时 / API 错误 / 连接中断

**每种失败独立计数**，不混用退避策略。

### 6.2 三层 JSON 解析兜底

```python
def _parse_response(self, text: str, model_class: Type[T]) -> T:
    # 第一层：直接解析整个文本
    try: return model_class.model_validate_json(text)
    except: pass

    # 第二层：从 ```json ... ``` 代码块提取
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if json_match:
        try: return model_class.model_validate_json(json_match.group(1))
        except: pass

    # 第三层：从 { ... } 提取最外层 JSON（允许前后有自然语言）
    brace_match = re.search(r"(\{[\s\S]*\})", text)
    if brace_match:
        try: return model_class.model_validate_json(brace_match.group(1))
        except: pass

    raise ValueError(f"无法提取有效 JSON (共 {len(text)} 字符)")
```

三层兜底覆盖了 DeepSeek 输出 JSON 的常见情况——有时裹在 markdown 里，有时前面有解释性文字。

### 6.3 流式调用（`run_streaming`）

```python
async def run_streaming(
    self,
    user_prompt: str,
    response_model: Type[T],
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_retries: int = 1,
) -> AsyncIterator[tuple[str, bool, Optional[T]]]:
    # 生成中: ("文字块", False, None)
    # 完成:   ("", True, parsed_model)
```

每收到一个 token 就 yield 出去，流结束后一次性解析完整文本。Gradio 前端用这个实现打字机效果。

---

## Part 7：四个业务 Agent

### 7.1 JD 解析 Agent（`jd_parser.py`）

```python
class JDParserAgent(BaseAgent):
    def __init__(self, llm=None):
        super().__init__(llm)
        self._system_prompt = load_prompt("jd_parser")

    async def run(self, jd_text: str) -> JD:
        jd = await super().run(
            user_prompt=jd_text,
            response_model=JD,
            system_prompt=self._system_prompt,
        )
        jd.raw_text = jd_text[:2000]  # 保留原始文本调试用
        return jd
```

5 行核心代码。输入 JD 纯文本，输出结构化 `JD` 对象。

### 7.2 简历分析 Agent（`resume_analyzer.py`）

与 JD 解析完全对称——5 行代码，输入简历文本，输出 `Resume` 对象。

### 7.3 面试官 Agent（`interviewer.py`）— 最复杂的核心

承担三个职责：

**① 出题**

```python
async def generate_question(
    self, jd: JD, resume: Resume, target_skill: str,
    difficulty: str = "intermediate", intent: str = "",
    candidate_name: str = "",
) -> Question:
```

出题时会从 ChromaDB 拉取两类长期记忆作为 Prompt 补充：
1. `_get_similar_questions_hint(skill)` → 查 `ih_question_bank`，返回"以下历史题目请避免重复"
2. `_get_candidate_history_hint(candidate_name)` → 查 `ih_interview_sessions`，返回"该候选人之前面过 X 次，均分 Y"

出题完成后，`_store_question(question)` 将新题写入 `ih_question_bank`。

**② 追问策略**

```
generate_deepen_question()   → 答得好 → 难度升级（basic→intermediate→...→deep）
generate_clarify_question()  → 答得模糊 → 保持难度，要求具体化
generate_switch_question()   → 答不上 → 换下一个技能，基础难度
```

**③ 评判**

```python
async def judge_answer(self, question: Question, answer: str) -> JudgeResult:
```

将题目、期望得分点、候选人回答一起喂给 LLM，返回 `JudgeResult`（含 `next_action` 决策）。

**④ 流式出题**

```python
async def generate_question_stream(...) -> AsyncIterator[tuple[str, bool, Optional[Question]]]:
```

与 `generate_question` 逻辑相同，但通过 `run_streaming()` 流式输出。Gradio 首题使用此方法。

### 7.4 反馈 Agent（`feedback.py`）

```python
class FeedbackAgent(BaseAgent):
    async def generate_report(
        self, jd: JD, resume: Resume, rounds: list[dict],
    ) -> InterviewReport:
```

将所有轮次的问答+评判打包成一份 interview transcript，让 LLM 生成五维度评分报告。输出 `InterviewReport`。

---

## Part 8：技能匹配与面试排序

`orchestration/matcher.py` — 出题顺序不是按 JD 权重死板排序，而是按面试自然度编排：

```
优先级 1: 有项目经验支撑的技能  → 候选人能展开聊
优先级 2: 有技能但无项目经验      → 可能需要引导
优先级 3: JD 要求但简历未提及     → 完全缺口，快速过
优先级 4: 加分技能               → 放最后，锦上添花
```

### 核心逻辑

```python
def _assess_gap(skill, skill_map, project_techs) -> tuple:
    if skill.name in skill_map:
        if skill.name in project_techs:
            return ("有项目经验", "简历中有该技能，且有项目实践")
        return ("有技能无项目", "简历中有该技能，但无项目实践")
    return ("缺口", "JD 要求，简历中未提及")
```

排序键：`(有项目经验? 0:1, 是缺口? 1:0, -权重)`

### 输出

```python
def generate_gap_map(jd, resume) -> dict:
    return {
        "strengths": [...],      # 有项目经验
        "weaknesses": [...],     # 有技能无项目
        "gaps": [...],           # 完全缺口
        "bonus": [...],          # 加分项
        "ordered_skills": [...], # 排好序的完整列表
    }
```

当前匹配是**纯字符串小写匹配**。优化路线图中规划了 BGE embedding 语义匹配（React.js ↔ React、Kubernetes ↔ K8s）。

---

## Part 9：LangGraph 编排 — 状态机与条件路由

### 9.1 状态设计

```python
class InterviewState(TypedDict):
    # 输入
    jd_path: str
    resume_path: str

    # 解析结果
    jd: Optional[JD]
    resume: Optional[Resume]

    # 匹配结果
    gap_map: Optional[dict]
    ordered_skills: list[dict]
    current_skill_index: int

    # 多轮面试
    rounds: Annotated[list[RoundRecord], add]  # ← 自动追加
    current_round_number: int
    question: Optional[Question]
    answer: str
    judge_result: Optional[JudgeResult]

    # 终止条件追踪
    consecutive_empty: int
    terminated: bool
```

关键：`rounds` 用了 LangGraph 的 `Annotated[list, add]` reducer——每次节点返回 `{"rounds": [one_record]}` 时自动追加而非替换。

### 9.2 流程图

```
START
  ↓
parse_jd  →  parse_resume  →  match_skills
                                  ↓
                          generate_question  ←──────────────┐
                                  ↓                          │
                           judge_answer                     │
                                  ↓                          │
                           decide_next                      │
                              ↓         ↓                    │
                           end?      continue ───────────────┘
                              ↓
                            END
```

### 9.3 条件路由

```python
def _next_action_label(state) -> str:
    # 1. 连续空回答 ≥ 3 次 → end
    # 2. 轮次 ≥ max_rounds(10) → end
    # 3. skill_index ≥ len(ordered_skills) → end
    # 4. judge.next_action → deepen / clarify / switch / end
```

四个终止条件覆盖面试可能结束的所有场景。

### 9.4 交互式入口

Gradio 和 Gateway 不跑完整 LangGraph 流，而是分步调用：

```python
# 初始化
state = await init_interview(jd_path, resume_path)
# 第一题
state = await generate_next_question(state)
# 每轮循环
state = await judge_and_decide(state, answer)
if not terminated:
    state = await generate_next_question(state)
```

这样每轮之间可以保存状态到 SessionStore，前端刷新不丢进度。

---

## Part 10：MCP Gateway — 企业级工程化

### 10.1 架构

```
FastAPI Gateway (:8000)
    ├── 鉴权中间件 (Bearer Token, dev 自动关闭)
    ├── 限流中间件 (令牌桶, 60/min)
    ├── /api/v1/*     面试 REST API
    ├── /mcp/*         MCP 工具调用
    └── /health       健康检查
         ↓
    Server 注册中心
    ├── JD Server (parse_jd)
    ├── Resume Server (parse_resume)
    └── Question Bank Server (4 tools)
```

### 10.2 熔断器

三态模型，每个 MCP Server 独立一个：

```
        连续失败 3 次
CLOSED ──────────────→ OPEN
  ↑                      │
  │   冷却 30s 后         │
  └──── HALF-OPEN ←──────┘
           │
     试探成功 → CLOSED
     试探失败 → OPEN
```

健康检查 `/health` 返回所有熔断器状态。

### 10.3 令牌桶限流

```python
class RateLimiter:
    def allow(self, ip) -> bool:
        # 按时间比例补充 token
        # tokens = min(max, tokens + elapsed/window * max)
        # 够 → 消耗 1 个，通过
        # 不够 → 429 Too Many Requests
```

跳过 `/health` 和 Gradio 静态资源。容量可配（`GATEWAY_RATE_LIMIT`）。

### 10.4 REST API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/interview` | 创建面试（解析 JD+简历+出首题） |
| `POST` | `/api/v1/interview/{id}/talk` | 提交回答（评判+决策+写长期记忆） |
| `GET` | `/api/v1/interview/{id}` | 获取会话状态 |
| `GET` | `/api/v1/interview/{id}/report` | 获取面试报告 |
| `POST` | `/mcp/{tool_name}` | 通用 MCP 工具调用 |
| `GET` | `/mcp/sse` | MCP SSE transport |
| `GET` | `/health` | 健康检查 + 熔断器状态 |

---

## Part 11：Gradio 前端 — 三步流程

### 11.1 UI 流程

```
Step 1: 📄 上传区
  → 上传 JD + 简历 → 点击"开始面试"
  → 后端流式展示进度（解析 → LLM 分析 → 缺口匹配 → 流式出题）

Step 2: 💬 面试区
  → 题目逐字出现（打字机效果）
  → 候选人输入回答 → 提交 → 评分 + 下一题
  → 支持"跳过"和"结束面试"

Step 3: 📊 报告区
  → 五维度进度条 + 录用建议 + 逐轮回顾
  → "再来一场"一键重置
```

### 11.2 回调设计

```python
# 全部使用原生 async def — 无事件循环包装
async def on_start(jd_file, resume_file):
    # Generator: yield 进度 → 实时更新 UI
    state = await init_interview(jd_path, resume_path)
    # 流式出题
    async for delta, done, result in interviewer.generate_question_stream(...):
        if not done:
            yield (..., [("🤖 面试官", text + " ▌")], ...)  # 实时更新
        else:
            state["question"] = result
    ...

async def on_submit(answer, state):
    # 评判 + 出下一题 / 生成报告
    state = await judge_and_decide(state, answer)
    if terminated:
        state = await _generate_report(state)
        store_interview_memory(state)  # 写入长期记忆
    else:
        state = await generate_next_question(state)
    ...
```

### 11.3 Phase 5 改进

- **原生 async**：消除 `_async()` 每次创建事件循环的反模式
- **统一状态机**：Gradio 和 Gateway 共用 supervisor 函数
- **流式首题**：`generate_question_stream()` 实现打字机效果
- **返回类型修复**：`gr.Chatbot(type="tuples")` 而非过时的 `type="messages"`

---

## Part 12：记忆系统 — 短期 + 长期

### 12.1 短期记忆 — SessionStore

轻量级 JSON 文件存储，每场面试一个文件 `data/sessions/{id}.json`：

```python
class SessionStore:
    def save(state: InterviewState) -> str      # 自动生成 ID
    def load(interview_id) -> InterviewState    # 按 ID 加载
    def find_by_candidate(name) -> list         # 按姓名搜索
    def list_all() -> list                      # 全部列表
    def delete(interview_id) -> bool            # 删除
```

不依赖 ChromaDB，Gateway 重启不丢数据。

### 12.2 长期记忆 — ChromaDB VectorStore

2 个 Collection，各有完整的读写闭环：

| Collection | 写入时机 | 读取时机 | 用途 |
|---|---|---|---|
| `ih_question_bank` | 每次出题后 | 下次出题时 | 语义检索历史题目，避免重复 |
| `ih_interview_sessions` | 面试结束时 | 下次面试开始时 | 候选人历史面试参考 |

**读写链路**：

```
出题 ──→ 查 ih_question_bank ──→ "历史类似题目：..."
    ──→ 查 ih_interview_sessions ──→ "该候选人面过X次，均分Y"
    ──→ LLM 出题
    ──→ 存储到 ih_question_bank ✅

面试结束 ──→ 存储到 ih_interview_sessions ✅
```

**VectorStore 核心方法**：

```python
class VectorStore:
    def add(collection, documents, metadatas, ids) → bool   # 写入
    def query(collection, query_text, n_results=5) → list   # 语义检索
    def get(collection, doc_id) → Optional[dict]            # 按 ID 查
    def delete(collection, doc_id) → bool                   # 删除

    # 便捷方法
    def store_interview_session(interview_json, metadata) → bool
    def search_similar_questions(skill, n=3) → list
    def search_candidate_history(candidate_name) → list

    @property
    def available(self) → bool  # ChromaDB 是否可用
```

**优雅降级**：ChromaDB 或 Embedding 加载失败 → `self._available = False` → 所有方法返回空值 → 核心面试流程不受影响。

**Embedding**：`BAAI/bge-base-zh-v1.5`，本地 768 维，`SentenceTransformer` 直接加载本地路径。支持 HF 镜像。

---

## Part 13：MCP Server 详解

### 13.1 JD Server & Resume Server

结构对称，各一个工具函数：

```python
app = FastMCP("jd-server")

@app.tool()
async def parse_jd(text: str) -> dict:
    """解析 JD 文本为结构化 JSON"""
    jd = await JDParserAgent(LLM()).run(text)
    return jd.model_dump()
```

Agent 实例延迟初始化为全局单例。

### 13.2 题库 Server

4 个工具函数：

| 工具 | 说明 |
|------|------|
| `generate_questions(jd_json, skill, difficulty, count)` | LLM 动态出题 |
| `search_seed_bank(skill, difficulty, count)` | 种子题库检索 |
| `add_to_seed_bank(question_json)` | 优质题反哺种子库 |
| `get_seed_bank_stats()` | 统计信息 |

出题策略：LLM 生成为主（灵活个性化），种子题库为辅（去重 + 兜底）。

---

## Part 14：部署与启动

### CLI

```bash
python main.py web                    # Gradio :7860 + Gateway :8000
python main.py gateway                # 仅 Gateway
python main.py history                # 查看历史
python main.py history -c 张三        # 按候选人搜索
```

### 配置

```bash
# .env 核心项
ENV=dev                                    # dev | prod
DEEPSEEK_API_KEY=sk-xxx                    # DeepSeek API Key
LLM_MODEL=deepseek-v4-pro                  # 模型名
EMBEDDING_MODEL=D:/model/bge-base-zh-v1.5  # 本地 Embedding
GATEWAY_API_KEY=dev-key-change-me          # Gateway 鉴权
LOG_LEVEL=INFO                             # 日志级别
```

### Docker Compose

```yaml
services:
  app:
    build: .
    ports: ["8000:8000", "7860:7860"]
    environment:
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
    volumes:
      - chroma_data:/app/data/chroma
      - sessions:/app/data/sessions
  chromadb:
    image: chromadb/chroma
    ports: ["8001:8000"]
```

---

## Part 15：Phase 5 改动日志

### 15.1 代码层面

| 改动 | 文件 | 说明 |
|------|------|------|
| 消除 `_async()` | `web/app.py` | 删除事件循环包装，全部改为 `async def` |
| 统一状态机 | `web/app.py` | 删除重复的面试逻辑，改用 supervisor 函数 |
| 流式首题 | `web/app.py` | `on_start` 中调用 `generate_question_stream()` |
| Prompt 校验 | `prompts/__init__.py` | 新增 `PromptTemplate` 类，变量缺失明确报错 |
| 环境区分 | `config.py` | `ENV=dev` 自动关闭鉴权 + DEBUG 日志 |
| 清理死 Collection | `memory/vector_store.py` | 删除 `ih_jd_history`、`ih_candidate_profiles`、`update_candidate_profile()` |
| 题库写入回路 | `agents/interviewer.py` | 新增 `_store_question()`，出题后自动存入向量库 |
| 历史读取回路 | `agents/interviewer.py` + `orchestration/supervisor.py` | 新增 `_get_candidate_history_hint()`，面试时查候选人历史 |

### 15.2 基础设施

| 改动 | 说明 |
|------|------|
| Embedding 升级 | `all-MiniLM-L6-v2`(384维) → `bge-base-zh-v1.5`(768维, 中文SOTA) |
| 模型本地化 | 下载到 `D:\model\bge-base-zh-v1.5`，不联网加载 |
| 长期记忆闭环 | 2 个 Collection 各有完整读写链路 |

### 15.3 文档

| 动作 | 文件 |
|------|------|
| 新建 | `docs/optimization-roadmap.md` |
| 重写 | `README.md`、`docs/CLAUDE.md`、`docs/blog-ai-interviewer.md`（本文件） |
| 删除 | 6 个过时阶段文档（phase1-3、DESIGN、ROADMAP、ticklish-imagining-key） |

---

## Part 16：设计亮点与感悟

### 1. 类型安全的 Agent 抽象

每个 Agent 方法返回 Pydantic 模型而非裸 str。三层 JSON 兜底 + 指数退避重试 + 流式支持，全部在基类完成。子类写业务逻辑只需 5 行代码。

### 2. 智能追问策略

不是死板的"出 N 道题结束"，而是 deepen → clarify → switch 三向动态路由。配合四个终止条件，面试过程自然不突兀。

### 3. 技能排序策略

"有项目经验 → 有技能无项目 → 完全缺口"的编排顺序，让面试从候选人擅长的领域开始，体验合理。

### 4. MCP 协议解耦

三个业务 Server 通过 MCP 注册到 Gateway，增删工具无需改 Gateway 代码。配合独立熔断器 + 令牌桶限流，具备生产级容错能力。

### 5. 优雅降级

ChromaDB 不可用 → 降级无记忆模式。Embedding 加载失败 → 同上。**降级不影响核心面试流程**——这是最重要的设计原则。

### 6. 状态持久化 + 断点续跑

LangGraph Checkpoint（MemorySaver）+ SessionStore JSON 文件存储双保险。刷新页面、Gateway 重启都不丢进度。

### 7. 错误提示友好

文件解析每一层都有中文 `ParseError`。Prompt 格式错误第一时间指明缺了哪个变量。不抛裸 RuntimeError。

### 8. 长期记忆闭环

不是"存了但从不读"的摆设。`ih_question_bank` 每次出题自动存入，下次出题语义检索。`ih_interview_sessions` 面试结束存入，下次面试开始参考历史。两个 Collection，两条完整链路。

---

*最后更新：2026-06-20 | 当前版本 v0.5.0*
