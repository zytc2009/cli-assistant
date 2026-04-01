# Multi-AI Discussion — 实现任务清单

基于 [multi-ai-discussion-orchestrator.md](./multi-ai-discussion-orchestrator.md) 设计文档拆分。

---

## Phase 0: 数据模型扩展

> 为 `discuss` 模式奠定数据基础，不破坏现有 `new`/`continue`/`interactive` 功能。

### Task 0.1 — 扩展 Meeting 数据模型

**文件**：`lib/meeting.py`

**变更**：
- [ ] 新增 `Discussion` 数据类（与现有 `Meeting` 并存，不修改 `Meeting`）
  ```python
  @dataclass
  class Phase:
      phase_type: str           # independent / discussion / synthesis
      phase_index: int
      moderator_opening: str = ""
      rounds: List[Round] = field(default_factory=list)

  @dataclass
  class Discussion:
      topic_id: str
      user_idea: str            # 用户原始想法
      moderator: str = ""       # 主持人 Agent ID
      created_at: str = ""
      status: str = "draft"     # draft / discussing / finalized
      final_output: str = ""
      user_feedbacks: List[str] = field(default_factory=list)
      phases: List[Phase] = field(default_factory=list)
  ```
- [ ] 复用现有 `Round` 数据类（结构不变）

**验证**：单元测试创建 `Discussion` 对象，确认序列化/反序列化正确。

### Task 0.2 — Discussion 持久化

**文件**：`lib/meeting.py`

**变更**：
- [ ] `save_discussion(discussion, base_dir)` — 保存到 `meetings/{topic_id}/`
  - `meeting.json`：元数据（含 `mode: "discuss"` 字段区分）
  - `idea.md`：用户原始想法
  - `phase_01_independent/raw/{agent_id}.md`：Phase 1 各 AI 原始回答
  - `phase_02_discussion/round_{N}/moderator_opening.md` + `{agent_id}.md`
  - `phase_03_synthesis/final_output.md`
  - `final_output.md`（顶层副本）
- [ ] `load_discussion(topic_id, base_dir)` — 从 JSON 恢复 `Discussion` 对象
- [ ] 更新 `list_meetings()` 兼容 `Discussion` 类型（通过 `mode` 字段区分显示）

**验证**：创建 Discussion → save → load → 验证所有字段一致。

---

## Phase 1: Prompt 模板

> 新增 4 个 prompt 模板文件，不修改现有 `base_system.md`。

### Task 1.1 — 独立发言 Prompt

**文件**：`config/prompts/independent_opinion.md`

**内容**：
- 占位符：`{agent_name}`, `{agent_strengths}`, `{user_idea}`
- 要求 AI 从专业视角给出：整体评价、关键要点（2-3 个）、补充建议
- 明确指示"独立思考，不假设他人观点"
- 输出格式：`### 整体评价` / `### 关键要点` / `### 补充建议`

### Task 1.2 — 主持人开场引导 Prompt

**文件**：`config/prompts/moderator_opening.md`

**内容**：
- 占位符：`{agent_name}`, `{user_idea}`, `{history_section}`, `{user_feedback_section}`, `{round_num}`, `{max_rounds}`
- 要求主持人：总结共识（2-3 句）、指出分歧（1-2 个）、提出焦点问题
- 包含收敛信号：输出 `讨论状态：[CONTINUE]` 或 `[SUGGEST_CONCLUDE]`
- 强调"引导者而非裁判"

### Task 1.3 — 讨论回应 Prompt

**文件**：`config/prompts/discussion_response.md`

**内容**：
- 占位符：`{agent_name}`, `{agent_strengths}`, `{user_idea}`, `{history_section}`, `{moderator_name}`, `{moderator_opening}`
- 要求 AI：回应焦点问题、补充/完善观点、回应他人
- 强调"简洁聚焦，避免重复共识"

### Task 1.4 — 主持人综合输出 Prompt

**文件**：`config/prompts/moderator_synthesis.md`

**内容**：
- 占位符：`{agent_name}`, `{user_idea}`, `{full_discussion_history}`, `{all_user_feedback}`
- 输出结构：核心结论、关键论据（标注来源 AI）、分歧记录、行动建议、风险提示
- 强调"忠实反映讨论内容，不添加未出现的观点"

---

## Phase 2: Prompt 构建逻辑

> 在 `lib/prompt_builder.py` 中新增构建函数，不修改现有 `build_prompt()`。

### Task 2.1 — 独立发言 Prompt 构建

**文件**：`lib/prompt_builder.py`

**新增函数**：
```python
def build_independent_prompt(
    template_content: str,   # independent_opinion.md 内容
    agent: AgentConfig,
    user_idea: str,
) -> str
```

**逻辑**：简单占位符替换，无历史注入。

### Task 2.2 — 主持人开场 Prompt 构建

**文件**：`lib/prompt_builder.py`

**新增函数**：
```python
def build_moderator_opening_prompt(
    template_content: str,   # moderator_opening.md 内容
    agent: AgentConfig,
    user_idea: str,
    round_num: int,
    max_rounds: int,
    history: List[Dict],     # 所有历史发言
    user_feedback: str = "",
) -> str
```

**逻辑**：
- 组装 `history_section`（复用现有 `build_history_section` 逻辑）
- 组装 `user_feedback_section`（有则显示，无则"无额外意见"）

### Task 2.3 — 讨论回应 Prompt 构建

**文件**：`lib/prompt_builder.py`

**新增函数**：
```python
def build_discussion_prompt(
    template_content: str,    # discussion_response.md 内容
    agent: AgentConfig,
    user_idea: str,
    history: List[Dict],
    moderator_name: str,
    moderator_opening: str,
) -> str
```

**逻辑**：
- 组装 `history_section`（所有历史 + 当前轮已发言者）
- 注入 `moderator_opening`

### Task 2.4 — 主持人综合 Prompt 构建

**文件**：`lib/prompt_builder.py`

**新增函数**：
```python
def build_synthesis_prompt(
    template_content: str,    # moderator_synthesis.md 内容
    agent: AgentConfig,
    user_idea: str,
    full_history: List[Dict],
    all_user_feedbacks: List[str],
) -> str
```

**逻辑**：
- 组装完整讨论历史（所有 Phase 的所有 Round）
- 合并所有用户补充意见

---

## Phase 3: 核心编排逻辑

> 新增 `lib/discussion_orchestrator.py`，独立于现有 `lib/orchestrator.py`。

### Task 3.1 — Phase 1 编排：独立发言（并行）

**文件**：`lib/discussion_orchestrator.py`

**新增类**：`DiscussionOrchestrator`

**方法**：`run_independent_phase(discussion, agents) -> Phase`
- 为每个 Agent 构建独立发言 prompt
- 并行调用所有 Agent（复用 `AgentRunner` + `ThreadPoolExecutor`）
- 收集响应，创建 `Phase(phase_type="independent")` 并追加到 `discussion.phases`
- 每个 Agent 完成后实时显示进度（spinner）
- 调用 `save_discussion()` 持久化
- 返回 Phase 对象

**依赖**：Task 0.1, 0.2, 1.1, 2.1

### Task 3.2 — 主持人选择交互

**文件**：`lib/discussion_orchestrator.py`

**方法**：`select_moderator(discussion, agents) -> str`
- 展示所有 Agent 的观点摘要（取每个回答的前 100 字 + Agent 擅长领域）
- 用 Rich Panel 格式化显示
- 用户输入编号选择主持人
- 返回选中的 Agent ID
- 将选择结果存入 `discussion.moderator`

**依赖**：Task 3.1 完成后才能选

### Task 3.3 — Phase 2 编排：主持人引导的讨论轮次

**文件**：`lib/discussion_orchestrator.py`

**方法**：`run_discussion_phase(discussion, agents, max_rounds) -> Phase`

**每轮流程**：
1. 调用主持人 Agent 生成开场引导（`build_moderator_opening_prompt`）
2. 保存 `moderator_opening` 到 Phase
3. 解析收敛信号 `[CONTINUE]` / `[SUGGEST_CONCLUDE]`
4. 展示主持人引导内容
5. 依次调用其他 Agent（顺序执行，每人能看到前人发言 + 主持人引导）
6. 展示本轮摘要
7. 用户选择：`[c]` 继续 / `[f]` 补充意见 / `[d]` 结束
8. 如果主持人建议结束，优先提示用户
9. 到达 `max_rounds` 自动结束
10. 每轮结束后 `save_discussion()`

**依赖**：Task 0.1, 0.2, 1.2, 1.3, 2.2, 2.3, 3.2

### Task 3.4 — Phase 3 编排：主持人综合输出

**文件**：`lib/discussion_orchestrator.py`

**方法**：`run_synthesis_phase(discussion) -> str`
- 组装完整讨论历史（Phase 1 + Phase 2 所有轮次）
- 合并所有 `user_feedbacks`
- 调用主持人 Agent 生成最终文档（`build_synthesis_prompt`）
- 保存为 `Phase(phase_type="synthesis")`
- 保存 `final_output.md`
- 更新 `discussion.status = "finalized"`
- 返回最终文档内容

**依赖**：Task 0.1, 0.2, 1.4, 2.4, 3.3

---

## Phase 4: CLI 入口

### Task 4.1 — `discuss` 命令

**文件**：`council.py`

**新增命令**：
```python
@cli.command()
@click.argument("idea")
@click.option("--agents", "-a", default="")
@click.option("--rounds", "-r", default=3, type=int)
@click.option("--moderator", "-m", default="")
def discuss(idea, agents, rounds, moderator):
```

**流程**：
1. 加载配置，解析 Agent 列表
2. 创建 `Discussion` 对象
3. 调用 `DiscussionOrchestrator.run_independent_phase()` → Phase 1
4. 如果未通过 `--moderator` 指定，调用 `select_moderator()` → 用户选择
5. （可选）收集用户补充意见
6. 调用 `run_discussion_phase()` → Phase 2（多轮）
7. 调用 `run_synthesis_phase()` → Phase 3
8. 输出结果文件路径

**依赖**：Phase 0-3 全部完成

### Task 4.2 — `list` / `show` 命令兼容

**文件**：`council.py`

**变更**：
- [ ] `list` 命令：识别 `mode: "discuss"` 的记录，在状态列加 `[讨论]` 标签
- [ ] `show` 命令：对 Discussion 类型展示 Phase 结构而非 Session 结构
  - 显示主持人信息
  - `--output` flag 展示最终输出文档（对应现有的 `--proposal`）

**依赖**：Task 0.2

---

## Phase 5: 体验优化（P1）

### Task 5.1 — 观点摘要自动提取

**文件**：`lib/discussion_orchestrator.py`

**逻辑**：
- Phase 1 完成后，从每个 AI 的完整回答中提取前 2-3 句作为摘要
- 简单策略：取 `### 整体评价` 下的首段（不额外调用 LLM）
- 展示在主持人选择界面

### Task 5.2 — 收敛信号解析

**文件**：`lib/discussion_orchestrator.py`

**逻辑**：
- 从主持人开场引导中用正则提取 `[CONTINUE]` / `[SUGGEST_CONCLUDE]`
- `[SUGGEST_CONCLUDE]` 时改变用户提示的默认选项为 `[d]`
- 展示主持人的建议理由

### Task 5.3 — 上下文压缩集成

**文件**：`lib/discussion_orchestrator.py`

**逻辑**：
- Phase 2 多轮讨论时，历史记录可能超长
- 当历史字符数超过阈值时，调用 `lib/context.py` 压缩早期轮次
- 保留最近 2 轮原文 + 早期摘要

---

## Phase 6: 扩展功能（P2）

### Task 6.1 — `--no-interact` 非交互模式

- 自动选第一个 Agent 当主持人
- 跑满 `--rounds` 轮后自动进入 Phase 3
- 不暂停等待用户输入
- 适合脚本化调用和 CI 场景

### Task 6.2 — 讨论记录导出

- 将完整讨论过程导出为单个 Markdown 文档
- 包含：用户想法 → 各方初始观点 → 每轮讨论 → 最终结论
- 格式适合分享和存档

### Task 6.3 — 中途更换主持人

- Phase 2 每轮结束后增加选项 `[m] 更换主持人`
- 更换后下一轮由新主持人引导
- 记录主持人变更历史

### Task 6.4 — 中途增减参会者

- Phase 2 每轮结束后增加选项 `[a] 管理参会者`
- 支持添加/移除 Agent
- 新加入的 Agent 收到完整历史作为上下文

---

## 依赖关系与实施顺序

```
Phase 0 (数据模型)
  Task 0.1 ──→ Task 0.2
                  │
Phase 1 (Prompt 模板)          ← 可与 Phase 0 并行
  Task 1.1, 1.2, 1.3, 1.4     ← 四个模板互相独立，可并行
                  │
Phase 2 (Prompt 构建)
  Task 2.1 ←── Task 1.1
  Task 2.2 ←── Task 1.2
  Task 2.3 ←── Task 1.3
  Task 2.4 ←── Task 1.4
                  │
Phase 3 (核心编排)
  Task 3.1 ←── Task 0.2 + 2.1
  Task 3.2 ←── Task 3.1
  Task 3.3 ←── Task 3.2 + 2.2 + 2.3
  Task 3.4 ←── Task 3.3 + 2.4
                  │
Phase 4 (CLI 入口)
  Task 4.1 ←── Task 3.4
  Task 4.2 ←── Task 0.2
                  │
Phase 5 (体验优化)  ← Phase 4 完成后
  Task 5.1, 5.2, 5.3  ← 互相独立，可并行
                  │
Phase 6 (扩展功能)  ← Phase 5 完成后
  Task 6.1, 6.2, 6.3, 6.4  ← 互相独立，可并行
```

**最短路径**（MVP 可运行）：
```
0.1 → 0.2 → 1.1 → 2.1 → 3.1 → 3.2 → 1.2+1.3 → 2.2+2.3 → 3.3 → 1.4 → 2.4 → 3.4 → 4.1
```

**并行优化**：Phase 0 和 Phase 1 可同时进行，Phase 1 的 4 个模板可并行编写。

---

## 文件变更总览

| 操作 | 文件 | Task |
|------|------|------|
| 修改 | `lib/meeting.py` | 0.1, 0.2 |
| 新增 | `config/prompts/independent_opinion.md` | 1.1 |
| 新增 | `config/prompts/moderator_opening.md` | 1.2 |
| 新增 | `config/prompts/discussion_response.md` | 1.3 |
| 新增 | `config/prompts/moderator_synthesis.md` | 1.4 |
| 修改 | `lib/prompt_builder.py` | 2.1, 2.2, 2.3, 2.4 |
| 新增 | `lib/discussion_orchestrator.py` | 3.1, 3.2, 3.3, 3.4 |
| 修改 | `council.py` | 4.1, 4.2 |
| 不变 | `lib/agent_runner.py` | — |
| 不变 | `lib/config.py` | — |
| 不变 | `lib/context.py` | — |
| 不变 | `lib/consensus.py` | — |
| 不变 | `lib/summarizer.py` | — |
