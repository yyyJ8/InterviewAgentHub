# 面试体验修复方案

> 2026-06-21 | 三个问题：出题顺序、跳过逻辑、记录查看

---

## 问题 1：出题顺序不合理 — 问了 Docker 却不问 Agent 核心

### 现象

Agent 开发实习生岗位，第一个问题问 Docker，而不是 LangGraph、Agent 开发等核心技能。

### 根因

**A. JD 解析器没有区分"核心开发技能"和"工具链技能"**

当前 JD 解析 prompt 只看技能出现位置和描述篇幅来定权重，但 Docker、Git、IDEA 这类工具也会被当成技能提取，且权重可能给得偏高。

Agent 开发 JD 包含：
- 核心：Python、LangGraph、Agent 开发、LLM 调用
- 工具：Docker、Git、Linux

JD 解析器一视同仁，全塞进 `required_skills`。

**B. Matcher 排序盲区**

`rank_skills()` 只按 (有项目经验, 是缺口, -权重) 排序。如果候选人简历里有 Docker 项目经验，Docker 就会排在第一位——而 Agent 开发技能因为没有项目经验反倒排后面。

### 修复方案

**Step 1 — JD 解析 prompt 加分类规则**（`prompts/jd_parser.md`）

新增一条规则：区分核心技能和工具链技能，工具链自动降权：

```
技能分类：
- 核心开发技能（编程语言、框架、架构）→ 正常权重 70-100
- 工具/基础设施（Docker、Git、Linux、CI/CD）→ 自动降权，不超过 50
- IDE/编辑器（IDEA、VS Code）→ 不提取为技能
```

**Step 2 — Matcher 加核心技能优先**（`orchestration/matcher.py`）

在排序键中加入一个因子：核心开发技能优先于工具链，确保即使候选人有 Docker 项目经验，Agent 核心技能也会先被问到。

```
排序键改为：(有项目经验? 0:1, 是工具? 1:0, 是缺口? 1:0, -权重)
```

**Step 3 — 面试官 Prompt 微调**（`prompts/interviewer.md`）

增加一条规则：优先从岗位名称中的核心方向出题。

---

## 问题 2：跳过后重复出同一道题

### 现象

输入"跳过"或空回答后，下一题仍然是同一道题（或同一技能的变体）。

### 根因

`web/app.py` 第 398-399 行：

```python
if not answer.strip():
    answer = "（跳过）"   # ← 覆盖了空字符串！
```

然后 `judge_and_decide(state, "（跳过）")` 传入 LLM 评判：

```
judge_answer_node:
  is_empty = not answer.strip()   # "（跳过）" → False！
  → consecutive_empty 不递增
  → LLM 看到候选人说"（跳过）"，可能判为"需要澄清" → next_action = "clarify"

_next_action_label:
  空回答检测：answer = "（跳过）" → 不触发
  → 返回 "clarify"

generate_next_question:
  → generate_clarify_question() → 同一道题的变体！
```

**一句话**：跳过检查被 `"（跳过）"` 覆盖了，整个跳过链路全部失灵。

### 修复方案

**在 `on_submit` 里提前拦截跳过**，不经过 LLM 评判：

```python
async def on_submit(answer, state):
    ...
    is_skip = not answer.strip()

    if is_skip:
        # 跳过 → 不调 LLM，直接 switch 到下一个技能
        state["answer"] = ""
        empty_count = state.get("consecutive_empty", 0) + 1
        state["consecutive_empty"] = empty_count

        if empty_count >= config.max_consecutive_empty:
            state["terminated"] = True
            state = await _generate_report(state)
            _save_session(state)
            return (..., report_text, ...)

        # 跳到下一个技能
        state["current_skill_index"] += 1
        if state["current_skill_index"] >= len(state.get("ordered_skills", [])):
            state["terminated"] = True
            state = await _generate_report(state)
            _save_session(state)
            return (..., report_text, ...)

        # 生成新技能的题目
        state = await generate_next_question(state)
        ...

    # 正常回答 → 走评判流程
    state = await judge_and_decide(state, answer)
    ...
```

**改动量**：`web/app.py` 加 ~25 行，不改其他文件。

---

## 问题 3：面试结束后记录在哪查看

### 现状

面试结束时，数据被写入两个地方：

| 存储 | 位置 | 内容 | 查看方式 |
|------|------|------|----------|
| SessionStore | `data/sessions/{id}.json` | 完整面试状态（每轮问答+评判） | `python main.py history` |
| ChromaDB | `data/chroma/` | 面试摘要 + 题库 embedding | 仅语义检索，无直接查看命令 |

### 问题

1. `python main.py history` 之前不能用——因为 Gradio 绕过了 Gateway，SessionStore 没写入（**Phase 5 已修复**）
2. 没有"查看某一场面试详情"的命令
3. CLI 历史列表信息太少，看不到具体内容

### 修复方案

**Step 1 — `history` 命令增强**（`main.py`）

支持查看详情、列表更丰富：

```bash
python main.py history                        # 列表
python main.py history -c 江豪                 # 按候选人
python main.py history -i abc12345            # 查看某场详情
python main.py history --last                 # 最新一场
```

详情输出示例：
```
面试 ID: abc12345
候选人: 江豪
岗位: Java 开发实习生
状态: completed
时间: 2026-06-21 17:30
总轮次: 4
总分: 87.5

第 1 轮 — MySQL (intermediate) — 88/100
  题目: 在 MOOC 平台项目中，如何设计课程观看时长的实时统计表？
  回答: 我采用了预聚合中间表 + Flink 消费 Binlog 的方案...

第 2 轮 — Java (advanced) — 92/100
  ...
```

**Step 2 — Gradio UI 里加"历史面试"入口**

在首页加一个简单的下拉框，展示历史面试列表，选中后跳转到报告页。

**Step 3 — 清理旧 ChromaDB 数据**

之前的调试产生了 4 条 MySQL 题目和 1 条 1 轮面试记录。提供清理命令：

```bash
python main.py clean-memory    # 清空 ChromaDB + sessions
```

---

## 实施顺序

| 优先级 | 问题 | 改动量 | 影响文件 |
|--------|------|--------|----------|
| 🔴 P0 | 跳过逻辑 | 小（~25 行） | `web/app.py` |
| 🟡 P1 | 出题顺序 | 中（prompt + matcher） | `prompts/jd_parser.md`、`matcher.py` |
| 🟢 P2 | 记录查看 | 中（CLI 增强） | `main.py` |

---

## 验证方法

修复后，用 demo 数据跑一次完整面试：

```bash
python main.py web
# 上传 data/demo/Agent开发实习生_JD.txt + 张明远_简历.txt
# 1. 确认第一题问的是 Agent/Python/LangGraph，不是 Docker
# 2. 输入"跳过"→ 确认下一题换技能了
# 3. 答完全部轮次 → 结束 → python main.py history 确认有记录
```
