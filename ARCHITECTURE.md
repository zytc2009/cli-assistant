# CLI Assistant — 技术架构文档

---

## 目录

- [系统概览](#系统概览)
- [核心设计原则](#核心设计原则)
- [模块详解](#模块详解)
- [数据流](#数据流)
- [会议生命周期](#会议生命周期)
- [Prompt 工程](#prompt-工程)
- [并发模型](#并发模型)
- [持久化方案](#持久化方案)
- [共识检测](#共识检测)
- [上下文管理](#上下文管理)
- [错误处理](#错误处理)
- [关键设计决策](#关键设计决策)

---

## 系统概览

```
┌──────────────────────────────────────────────────────────┐
│                     cli_assistant.py (CLI)               │
│  discuss / new / continue / interactive / finalize /     │
│  list / show / test-round / agent (detect/list/add/remove)│
└────────────────────────┬─────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
  ┌──────────────┐ ┌─────────────┐ ┌──────────────┐
  │  Interactive │ │ Discussion  │ │  Meeting     │
  │   Wizard     │ │Orchestrator │ │Orchestrator  │
  │  (no args)   │ │  (discuss)  │ │ (new/cont)   │
  └──────┬───────┘ └──────┬──────┘ └──────┬───────┘
         │                │               │
         └────────────────┼───────────────┘
                          ▼
        ┌─────────────────────────────────┐
        │      StreamingRunner /          │
        │       AgentRunner               │
        │  (real-time / batch output)     │
        └──────────────┬──────────────────┘
                       │
                       ▼
              各 AI CLI 进程
         (claude / codex / kimi / ...)
                          ▲
                          │ (optional)
              ┌───────────┴───────────┐
              │  VisualCompanion       │
              │  (browser mockups)     │
              └────────────────────────┘
```

---

## 核心设计原则

### 1. 不改 CLI 代码
所有 AI CLI 通过 `stdin/stdout` 的非交互模式调用。Orchestrator 通过 prompt 注入上下文和角色规则，完全不依赖各 CLI 的内部 API。

### 2. Prompt 是灵魂
- 第 1 轮：各 Agent **不**看到他人发言，避免锚定效应，保证观点多样性
- 第 2 轮起：历史发言全量注入，促进交叉碰撞和回应
- Session 间：只传上一阶段 `proposal.md`，不传原始讨论记录（控制 context 长度）

### 3. 全过程可追溯
所有原始发言、会议纪要、方案文档均落文件，支持离线审阅和复现。

### 4. 用户始终可干预
每个 Session 结束后暂停，用户可审阅方案、追加意见、替换 Agent 或直接定稿。

---

## 模块详解

### `lib/config.py` — 配置加载

**数据类**

```python
@dataclass(frozen=True)   # 不可变，防止意外 mutation
class AgentConfig:
    name: str          # 显示名称
    cli: str           # CLI 类型标识
    model: str         # 模型 ID
    command: str       # 命令模板，支持两种模式：
                       # - 含 {prompt_file}：通过临时文件传递 prompt
                       # - 使用 "-"（如 claude -p -）：通过 stdin 传递 prompt
    prompt_method: str # 固定为 file
    max_tokens: int    # token 上限（供预估用）
    timeout: int       # subprocess 超时秒数
    strengths: str     # 注入 prompt 的角色说明
    cost_tier: str     # high / medium / low

@dataclass(frozen=True)   # 需修改字段时使用 dataclasses.replace()
class MeetingTemplate:
    description: str
    max_rounds: int
    speaking_order: str        # round_robin（当前唯一实现）
    round_rules: Dict[int, str] # 每轮的发言规则
    output: str

@dataclass(frozen=True)
class ModelStrategy:
    brainstorm: List[str]  # 各阶段对应的 Agent ID 列表
    review: List[str]
    decision: List[str]
```

**`Config` 类**：统一入口，加载所有配置文件并提供 `get_agent()`、`get_template()`、`prompt()` 等方法。配置校验在加载时执行（`command` 必须含 `{prompt_file}` 或使用 `-` 表示 stdin 模式，`timeout > 0`）。

---

### `lib/agent_runner.py` — CLI 调用封装

**两种输出模式**

不同 CLI 的输出方式不同，runner 通过 `AgentConfig.output_method` 字段区分：

| `output_method` | 适用 CLI | 结果来源 |
|----------------|---------|---------|
| `stdout`（默认）| Claude、Gemini、Kimi | 从 subprocess stdout 读取 |
| `file` | Codex | 从 `{output_file}` 临时文件读取 |

**核心流程（stdout 模式）**

```
invoke(agent_name, prompt_content)
  │
  ├─ 检查命令是否含 {prompt_file}
  │   ├─ 是：写 prompt 到临时文件，替换命令中的占位符
  │   └─ 否：通过 stdin 直接传递 prompt（如 claude -p -）
  ├─ subprocess.run(input=prompt_content 或 input=None, capture_output=True, timeout=N)
  ├─ 清理临时文件（如有，finally 块保证执行）
  └─ 返回 AgentResponse(content=stdout, success, error, duration_seconds)
```

**核心流程（file 模式，用于 Codex）**

```
invoke(agent_name, prompt_content)
  │
  ├─ 写 prompt 到临时文件 {prompt_file}
  ├─ 创建临时输出文件 {output_file}
  ├─ 替换命令中的 {prompt_file} 和 {output_file}
  ├─ subprocess.run(...)
  ├─ 读取 {output_file} 内容作为结果
  ├─ 清理两个临时文件（finally 块）
  └─ 返回 AgentResponse(content=output_file_content, ...)
```

**为什么 Codex 需要 `{output_file}`？**

Codex `exec` 子命令的 stdout 包含额外噪声，无法直接使用：

```
codex                    ← 第 1 行：固定标识头
实际回答内容...           ← 中间：真正的回答
tokens used              ← 倒数第 2 行：token 统计
1,433                    ← 倒数第 1 行：token 数量
实际回答内容...           ← 末尾：回答重复一次
```

`-o {output_file}` 标志让 Codex 将最后一条消息单独写入文件，内容干净无噪声。

**为什么用文件或 stdin 传 prompt 而不是命令行参数？**

| | 命令行参数 | 文件 / stdin |
|---|---|---|
| 长度限制 | Windows cmd 8191 字符 | 无限制 |
| 特殊字符 | 引号、换行需转义 | 无需处理 |
| 调试 | 难以复现 | 保留文件即可复现 |

**两种传递方式：**

1. **stdin 模式**（推荐）：如 `claude -p -`，Python 通过 `subprocess.run(input=prompt)` 直接传递，避免 shell 转义问题
2. **文件模式**：如 `kimi --file {prompt_file}`，创建临时文件传递，适用于不支持 stdin 的 CLI

Gemini 通过 stdin 管道（`cat {prompt_file} | gemini -p " " --yolo`）规避了命令行长度限制，属于"文件间接传入"的变体。

**重试机制**：`invoke_with_retry(max_retries=2)` — 失败立即重试，所有重试失败后返回"本轮缺席"占位响应，不中断整场会议。

---

### `lib/streaming_runner.py` — 实时流式输出

为交互式向导和 discuss 模式提供**实时逐行输出**的能力，让用户看到 AI 正在思考的过程。

```python
class StreamingRunner:
    def invoke_streaming(agent_name, prompt_content, on_output=None) -> AgentResponse
```

**与 AgentRunner 的区别**

| | `AgentRunner` | `StreamingRunner` |
|---|---------------|-------------------|
| 输出时机 | 进程结束后一次性返回 | 每行输出实时打印 |
| 适用场景 | batch 模式（new/continue） | 交互模式（discuss/向导） |
| 用户体验 | 等待...然后看结果 | 实时看到 AI 思考过程 |
| 内部实现 | `subprocess.run()` | `subprocess.Popen()` + iter |

**流式输出原理**

```python
process = subprocess.Popen(
    cmd, shell=True, stdout=subprocess.PIPE, bufsize=1  # 行缓冲
)

for line in iter(process.stdout.readline, ''):
    line = line.rstrip('\n\r')
    output_lines.append(line)
    console.print(f"> {line}")  # 实时打印
    if on_output:
        on_output(line)  # 回调通知

try:
    return_code = process.wait(timeout=agent.timeout)  # 带超时
except subprocess.TimeoutExpired:
    process.kill()
    return AgentResponse(..., error="[本轮缺席：调用超时]")
```

---

### `lib/cli_detector.py` — CLI 自动检测

自动检测本地安装的 AI CLI 工具，支持配置持久化。

```python
class CLIDetector:
    KNOWN_CLIS = {
        "claude": {"name": "Claude Code", "command": "...", ...},
        "codex": {"name": "OpenAI Codex", ...},
        "kimi": {"name": "Moonshot Kimi", ...},
        "gemini": {"name": "Google Gemini", ...},
    }

    def detect_all() -> List[CLIDetected]  # 检测所有已知 CLI
    def detect_one(cli_id) -> CLIDetected   # 检测单个 CLI
```

**检测逻辑**

1. 使用 `shutil.which()` 检查命令是否在 PATH
2. 执行 `--version` 获取版本号（正则提取）
3. 返回结构化数据供后续使用

**配置持久化**

```python
def save_detected_clis_to_config(detected_clis, config_path):
    # 将检测到的 CLI 写入 agents.yaml
    # 仅添加尚未配置的条目
```

---

### `lib/prompt_builder.py` — Prompt 组装

**传统会议模式**

```python
build_prompt(
    template_content,  # base_system.md 原始内容
    agent,             # AgentConfig
    topic,             # 议题
    session_type,      # brainstorm / review / decision
    round_num,         # 当前轮次
    history,           # List[{round, responses}] 或 None
    prior_proposal,    # 上一 Session 的 proposal.md
    user_feedback,     # 用户补充意见
) -> str
```

历史注入逻辑：第 1 轮注入"独立思考"指令，第 2 轮起注入完整历史。

**讨论模式（discuss / 向导）**

| 函数 | 用于 | 关键变量 |
|------|------|---------|
| `build_independent_prompt` | Phase 1 独立发言 | agent, user_idea |
| `build_moderator_opening_prompt` | 自由讨论 Phase 2 主持人开场 | agent, history, user_feedback, round_num |
| `build_discussion_prompt` | 自由讨论 Phase 2 参与者回应 | agent, history, moderator_opening |
| `build_requirement_round_prompt` | **需求讨论 Phase 2 平等发言** | agent, history, user_feedbacks, round_num |
| `build_synthesis_prompt` | Phase 3 综合输出 | agent, full_history, all_user_feedbacks |

`build_requirement_round_prompt` 是需求讨论专用，不传入主持人字段，改传 `user_feedbacks`（全量补充列表）和 `round_num`，对应重写后的 `requirement_discussion_response.md` 模板。

---

### `lib/meeting.py` — 状态管理与持久化

**数据模型**

```
Meeting
├── topic_id: str          # 唯一 ID（议题摘要 + 6 位 UUID）
├── topic: str
├── created_at: str        # ISO 格式时间戳
├── status: str            # draft / in_progress / finalized
├── final_proposal: str
└── sessions: List[Session]
    └── Session
        ├── session_index: int
        ├── session_type: str    # brainstorm / review / decision
        ├── agents: List[str]    # Agent ID 列表
        ├── consensus_level: str # full / partial / none
        ├── proposal: str
        ├── minutes: str
        └── rounds: List[Round]
            └── Round
                ├── round_num: int
                └── responses: Dict[agent_id, content]
```

**持久化格式**

```
meetings/{topic_id}/
├── meeting.json          # 结构化元数据（机器读写）
├── topic.md              # 议题描述（人类可读）
├── final_proposal.md     # 定稿方案
└── session_01/
    ├── minutes.md        # 会议纪要（Markdown）
    ├── proposal.md       # 本阶段方案（Markdown）
    └── raw/
        ├── round_01_claude-sonnet.md
        ├── round_01_codex-o4-mini.md
        └── round_02_claude-sonnet.md
```

`save_meeting()` 在每轮结束后调用，保证进程中断时已完成的内容不丢失。

---

### `lib/orchestrator.py` — 核心会议循环（传统模式）

用于 `new`、`continue`、`interactive` 命令，支持多 Session 串联的完整会议流程。

**`run_session()` 流程**

```
run_session(meeting, session_type, agents, prior_proposal, user_feedback)
  │
  ├─ 创建 Session 对象，追加到 meeting.sessions
  │
  ├─ for round_num in 1..max_rounds:
  │   ├─ round_num == 1 → _run_round_parallel()  # 并行，各自独立
  │   └─ round_num >= 2 → _run_round_sequential() # 顺序，可见前人发言
  │   ├─ 保存 Round 到 session.rounds
  │   ├─ save_meeting()  ← 每轮持久化一次
  │   ├─ _detect_consensus() → ConsensusResult
  │   └─ consensus == "full" → break（提前结束）
  │
  ├─ generate_minutes()    # 调用 LLM 生成纪要
  ├─ generate_proposal()   # 调用 LLM 生成方案
  └─ save_meeting()
```

---

### `lib/visual_companion.py` — 浏览器视觉伴侣

管理本地 Node.js HTTP/WebSocket 服务器，为需求讨论提供可视化辅助。

```python
class VisualCompanion:
    def start() -> str          # 启动 server，返回 URL
    def write_screen(html, name) -> str   # 写入 HTML，浏览器自动刷新
    def read_events() -> List[Dict]       # 读取用户浏览器点击/选择事件
    def stop()                            # 终止 server 进程
```

**工作原理**：`visual/server.cjs` 监视 `content/` 目录的 HTML 文件变更，通过 WebSocket 向浏览器广播 `reload` 消息。内容片段会自动注入 `frame-template.html` 提供的主题 CSS 和交互脚本。

**触发时机**：仅在需求讨论中、用户主动启用时生效。

---

### `lib/discussion_orchestrator.py` — 讨论模式编排器

用于 `discuss` 命令和交互式向导，实现**三阶段结构化讨论**。支持两种 Phase 2 行为，由 `discussion.flow` 决定：

**数据模型扩展**

```
Discussion
├── topic_id: str
├── user_idea: str          # 用户的原始想法
├── flow: str               # "discussion"（自由讨论）/ "requirement"（需求讨论）
├── moderator: str          # 自由讨论的主持人 Agent（需求讨论中不参与引导）
├── phases: List[Phase]
│   └── Phase
│       ├── phase_type: str   # independent / discussion / synthesis
│       ├── phase_index: int
│       └── rounds: List[Round]
├── user_feedbacks: List[str]
└── final_output: str       # Phase 3 输出
```

**三阶段流程**

三个核心方法均接受可选的 `streaming_runner` 参数，流式变体（`*_streaming`）是同一实现的薄包装：

```
Phase 1 — run_independent_phase（两种 flow 相同）
  ├── 所有 AI 独立发言，互不可见（避免锚定效应）
  ├── 并行调用（streaming 时顺序实时显示，否则并发收集）
  └── 需求模式输出：已明确字段 / 待澄清问题 / 假设前提

用户回答环节
└── 用户可一次性回答多个问题，或直接跳过

确认清单 — _run_requirement_confirmation（需求讨论 Phase 2 前）
  ├── summarizer_agent 综合 Phase 1 + 用户回答
  ├── 展示：已明确字段 / 准备做的假设 / 仍有疑问
  └── 用户确认或纠正

视觉方案 — run_visual_option_phase（需求讨论，可选）
  ├── 仅在 Visual Companion 启用时执行
  ├── synthesizer agent 分析 Phase 1，判断是否需要可视化方案
  ├── 若涉及 UI/架构/流程：生成 2-3 个 HTML mockup 推送到浏览器
  ├── 用户在浏览器点选，事件写入 state/events
  └── 选择结果追加到 user_feedbacks，注入 Phase 2 上下文

Phase 2 — run_discussion_phase（按 flow 分支）

  [自由讨论] 主持人引导，最多 max_rounds 轮
    ├── 主持人开场（moderator_opening prompt）
    ├── for round in 1..max_rounds:
    │   ├── 其他 AI 依次回应主持人引导
    │   ├── 共识检测：full/partial 时提前结束
    │   └── 可选：用户补充意见
    └── 收集讨论记录到 Phase 2

  [需求讨论] 无主持人，无轮数限制，_run_requirement_discussion_phase
    ├── while True:
    │   ├── 所有 AI 平等发言（_run_requirement_round）
    │   │   └── 每个 AI 输出：字段认知 / 仍待澄清问题 / 假设 / CONVERGED 声明
    │   ├── 展示已收敛字段状态（_show_requirement_status）
    │   ├── 若启用 visual_companion → 推送需求澄清看板到浏览器
    │   ├── 若全部 5 个字段 [CONVERGED] → 自动结束
    │   ├── 提取并展示"仍待澄清的问题"（_extract_unclear_points）
    │   └── 可选用户补充（直接回车跳过；输入 d 强制结束）
    └── 用户盲区由 AI 给出假设后继续推进，不再反复追问

Phase 3 — run_synthesis_phase（按 flow 选择综合者）
  ├── 自由讨论：由主持人（moderator）综合
  ├── 需求讨论：由 summarizer_agent（最便宜的可用 Agent）综合
  ├── 生成结构化最终文档（requirement.md 或 final_output.md）
  ├── 自动质检 — _review_requirement()
  │   ├── 检查完整性/一致性/清晰度/范围/YAGNI
  │   ├── 若通过 → 静默保存
  │   └── 若不通过 → 展示审阅结果，用户可选择自动修正一轮
  └── 保存最终文档
```

### 需求层 / 执行层边界

需求讨论阶段只输出需求层内容，避免把执行字段提前混进 `requirement.md`：

- `requirement.md` 只保留 `Goal`、`Scope`、`Inputs`、`Outputs`、`Acceptance Criteria`、`Open Questions`
- `_validate_requirement_output()` 会拦截带有 `Constraints`、`Status`、`workspace_dir`、`output_dir`、`execution_mode`、`harness` 的输出
- `_build_harness_task_document()` 负责把收敛后的需求转换成 Harness 可消费的 `task.md`
- `export-task` 命令提供显式导出入口，支持把任务文档写到指定路径

**需求讨论 Prompt 结构**（`requirement_discussion_response.md`）

每轮 AI 发言按 4 节输出，便于自动解析：

```
### 1. 字段当前认知     ← 5 个字段的当前理解（待定时写 [待定: 原因]）
### 2. 仍待澄清的问题   ← 必须由用户回答的问题（用于 _extract_unclear_points 解析）
### 3. 我的假设         ← 用户未回答时的合理假设，让讨论继续推进
### 4. 已收敛的字段     ← [CONVERGED] 字段名: 最终内容（用于收敛追踪）
```

**与传统 Orchestrator 的区别**

| | `Orchestrator` | `DiscussionOrchestrator` |
|---|----------------|--------------------------|
| 模式 | 多 Session 串联 | 单议题三阶段 |
| 输出 | 批量（结束后显示） | 流式（实时显示） |
| Phase 2 角色 | 无主持人概念 | 自由讨论有主持人；需求讨论无主持人 |
| 轮次控制 | 固定 max_rounds | 自由讨论固定轮次；需求讨论无限循环直到收敛或用户结束 |
| 适用 | 复杂多阶段会议 | 快速讨论 / 需求澄清 |

---

### `lib/summarizer.py` — 纪要与方案生成

两个函数均通过调用 LLM（默认 `claude-sonnet`）生成结构化 Markdown 文档：

- `generate_minutes(session, topic, runner, ...)` → 从所有轮次发言提炼会议纪要
- `generate_proposal(session, topic, runner, prior_proposal, ...)` → 融合会议纪要 + 最后一轮发言生成方案文档

生成失败时返回包含错误信息的降级文档，不抛出异常。

---

### `lib/consensus.py` — 共识检测

每轮结束后，用最便宜的可用 Agent 分析最新一轮发言，输出 JSON：

```json
{
  "consensus_reached": true,
  "consensus_level": "partial",
  "agreed_points": ["采用微服务架构", "使用 gRPC 作为内部通信"],
  "disputed_points": ["是否引入 Service Mesh"],
  "recommendation": "进入下一阶段"
}
```

| consensus_level | 含义 | Orchestrator 动作 |
|----------------|------|-------------------|
| `full` | 所有人无实质异议 | 提前结束当前 Session |
| `partial` | 核心方向一致，细节有分歧 | 继续或进入下一阶段 |
| `none` | 核心方案存在根本分歧 | 继续讨论，提示用户介入 |

JSON 解析失败时返回 `ConsensusResult.unknown()`（`level=none`），不影响流程。

---

### `lib/context.py` — 上下文压缩

当历史记录超过 `max_chars`（默认 3000）时，保留最近 2 轮原文，将更早的轮次压缩为摘要：

```
[Round 1 原文] + [Round 2 原文] + [Round 3 原文]
       ↓ 超过阈值
[Round 1-2 摘要] + [Round 3 原文]
```

Token 预估（粗略）：
- 中文：1.5 token/字
- 英文：0.75 token/词

---

## 数据流

```
用户输入议题
    │
    ▼
cli_assistant.py::new()
    │
    ├─ Config.load()           读取 YAML 配置
    ├─ Meeting.create()        创建会议对象
    │
    └─ Orchestrator.run_session()
           │
           ├── Round 1（并行）
           │    ├── PromptBuilder.build_prompt(round=1, history=None)
           │    │    └── base_system.md + 独立思考指令
           │    ├── AgentRunner.invoke(claude-sonnet) → subprocess → stdout
           │    ├── AgentRunner.invoke(codex-o4-mini) → subprocess → stdout
           │    ├── AgentRunner.invoke(kimi)          → subprocess → stdout
           │    ├── save_meeting()
           │    └── detect_consensus()
           │
           ├── Round 2（顺序）
           │    ├── PromptBuilder.build_prompt(round=2, history=[Round1])
           │    │    └── base_system.md + 历史发言
           │    ├── AgentRunner.invoke(claude-sonnet) ← 能看到 Round 1 全部发言
           │    ├── AgentRunner.invoke(codex-o4-mini) ← 能看到 Round 1 + claude-sonnet 的 Round 2
           │    └── ...
           │
           ├── generate_minutes()  → LLM → minutes.md
           └── generate_proposal() → LLM → proposal.md
```

---

## 会议生命周期

```
状态机：

draft ──→ in_progress ──→ finalized
            │    ↑
            └────┘  （多个 Session 循环）
```

每次 `run_session()` 开始时状态切换为 `in_progress`；`finalize` 命令将状态置为 `finalized` 并生成 `final_proposal.md`。

**Session 自动推进顺序**

```
brainstorm → review → decision
```

`continue` 命令根据最后一个 Session 的类型自动选择下一个阶段，也可用 `--mode` 手动覆盖。

---

## 并发模型

```
第 1 轮：ThreadPoolExecutor（max_workers=len(agents)）

┌─────────────────────────────────────────────┐
│ Thread 1: invoke(claude-sonnet, prompt_R1)  │
│ Thread 2: invoke(codex-o4-mini, prompt_R1)  │  ← 同时执行
│ Thread 3: invoke(kimi, prompt_R1)           │
└─────────────────────────────────────────────┘
         ↓ 所有结果收集完成后
       组装 Round 1 历史

第 2 轮及以后：顺序执行

invoke(claude-sonnet, prompt_R2_with_R1_history)
    ↓
invoke(codex-o4-mini, prompt_R2_with_R1+claude_R2_history)
    ↓
invoke(kimi, ...)
```

**为什么第 2 轮顺序执行？**

顺序执行让每个 Agent 在发言时能看到当前轮中已发言 Agent 的内容，形成真正的"对话"效果，而非各说各话。

---

## 持久化方案

- **`meeting.json`**：结构化元数据，Python dict → JSON，支持随时加载/恢复
- **`*.md` 文件**：人类可读的 Markdown，方便直接查阅
- **`raw/` 目录**：每个 Agent 每轮的原始输出，用于调试和复现
- **保存时机**：每轮结束后立即 `save_meeting()`，崩溃时最多丢失当前轮

---

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| Agent CLI 调用超时（批量模式） | 返回"本轮缺席：调用超时"，重试 2 次后跳过 |
| Agent CLI 调用超时（流式模式） | `process.wait(timeout=N)` → `TimeoutExpired` → kill 进程，返回占位 |
| Agent CLI 进程崩溃 | 捕获异常，返回"本轮缺席：调用异常"，继续下一个 Agent |
| 空输出 | 视为失败，触发重试 |
| 共识检测 JSON 解析失败 | 返回 `unknown`（level=none），不中断流程 |
| 纪要/方案生成失败 | 返回含错误信息的降级文档 |
| 未知 Agent ID | 启动时校验，立即报错退出 |
| 配置文件缺少 `{prompt_file}` 且不含 stdin 模式 `-` | 加载时校验，立即报错退出 |

---

## 各 CLI 实测对照（Windows，2026-04）

验证版本：`claude 2.1.86` / `codex 0.117.0` / `gemini 0.35.3` / `kimi 1.28.0`

| 维度 | Claude | Codex | Gemini | Kimi |
|------|--------|-------|--------|------|
| 非交互标志 | `-p -` (stdin) | `-q -` (stdin) | stdin + `-p " "` | `--print -p -` |
| 自动批准 | 不需要 | `--full-auto` | `--yolo` | `-y` |
| 输出来源 | stdout | 临时输出文件 | stdout | stdout |
| stdout 噪声 | 无 | 有（header + token stats + 重复） | 无 | **有（详细执行日志）** |
| Windows 特殊处理 | 需要 git-bash | 无 | 无 | **⚠️ stdin 输入问题** |
| 模型选择 | `--model claude-xxx` | 账号决定（API key 可用 `-m`）| `-m gemini-xxx` | `-m moonshot-xxx` |
| 在 git 外运行 | 无限制 | 需要 `--skip-git-repo-check` | 无限制 | 无限制 |

**Kimi Windows 已知问题**：Kimi CLI 在 Windows 上存在 stdin 输入问题，`--print -p -` 模式无法正确接收 stdin 输入，始终显示输入为 `-`。建议暂时使用 Claude 和 Codex 进行多 AI 讨论，Kimi 支持将在后续版本修复。

**Codex stdout 噪声示例（不用 `-o` 时）**：

```
codex                                  ← 第 1 行固定头
我是一个 AI 助手，专注于编程任务。     ← 实际回答
tokens used                            ← 固定尾
1,433                                  ← token 数
我是一个 AI 助手，专注于编程任务。     ← 回答重复
```

**Gemini stdin 管道原因**：`gemini --prompt` 接受文本参数，但 Windows cmd 命令行长度上限 8191 字符。长 prompt（含多轮历史）超限后会静默截断。`cat {prompt_file} | gemini -p " " --yolo` 通过 stdin 绕过此限制，`-p` 只需传一个空格触发非交互模式即可。

---

## 测试策略

详见 [TESTING.md](./TESTING.md)。

---

## 关键设计决策

### 为什么 stdin 模式优于 $(cat) 展开

早期版本使用 `$(cat '{prompt_file}')` shell 命令展开来嵌入 prompt 内容，但发现严重问题：
- prompt 中的 `"`, `$`, `` ` `` 等特殊字符会被 bash 解释，导致命令失败
- 长 prompt 可能超出命令行长度限制（Windows cmd 8191 字符）

**修复方案**：改用 Python `subprocess.run(input=prompt_content)` 直接通过 stdin 传递，CLI 命令使用 `-` 表示从 stdin 读取（如 `claude -p -`）。对于不支持 stdin 的 CLI（如 Kimi），仍保留文件模式作为备选。

### 为什么保留文件模式作为备选

部分 CLI（如 Kimi）不支持 stdin 输入，必须通过 `--file` 参数传递文件路径。文件方式跨平台稳定，且便于调试（可直接查看临时文件内容）。Gemini 通过 `cat | gemini` 的 shell 管道实现，底层仍是文件读取，只是绕过了命令行长度限制。

### 为什么不并行运行多个 Session

Session 之间存在依赖（下一 Session 的输入是上一 Session 的 proposal），且每个 Session 之间设计了用户干预点，顺序执行符合设计意图。

### 为什么用 JSON 而不是 SQLite 存储元数据

- 无需额外依赖
- 人类可读，便于手动修改和调试
- 文件级别的并发安全（单进程写入）
- 对于会议记录规模，JSON 性能完全足够

### 为什么摘要和方案生成也用 LLM 而不是规则提取

结构化摘要和融合各方观点的方案文档需要理解能力，规则提取无法处理自然语言的歧义和多样性。使用最便宜的 Agent（`claude-sonnet`）做此类"后处理"任务，在质量和成本上取得平衡。
