# AI Council — 技术架构文档

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
│                     council.py (CLI)                      │
│  new / continue / interactive / finalize / list / show   │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│                   Orchestrator                            │
│  · 控制会议轮次流程                                        │
│  · 第 1 轮并行调用 / 后续轮次顺序调用                       │
│  · 每轮后触发共识检测                                      │
│  · Session 结束后生成纪要和方案                            │
└───┬───────────────┬───────────────┬──────────────────────┘
    │               │               │
    ▼               ▼               ▼
AgentRunner   PromptBuilder    Summarizer / Consensus
(subprocess)  (模板 + 历史)    (调用 LLM 生成结构化输出)
    │
    ▼
各 AI CLI 进程
(claude / codex / kimi / ...)
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
@dataclass
class AgentConfig:
    name: str          # 显示名称
    cli: str           # CLI 类型标识
    model: str         # 模型 ID
    command: str       # 含 {prompt_file} 的命令模板
    prompt_method: str # 固定为 file
    max_tokens: int    # token 上限（供预估用）
    timeout: int       # subprocess 超时秒数
    strengths: str     # 注入 prompt 的角色说明
    cost_tier: str     # high / medium / low

@dataclass
class MeetingTemplate:
    description: str
    max_rounds: int
    speaking_order: str        # round_robin（当前唯一实现）
    round_rules: Dict[int, str] # 每轮的发言规则
    output: str

@dataclass
class ModelStrategy:
    brainstorm: List[str]  # 各阶段对应的 Agent ID 列表
    review: List[str]
    decision: List[str]
```

**`Config` 类**：统一入口，加载所有配置文件并提供 `get_agent()`、`get_template()`、`prompt()` 等方法。配置校验在加载时执行（`command` 必须含 `{prompt_file}`，`timeout > 0`）。

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
  ├─ 写 prompt 到临时文件（UTF-8，Windows 路径安全）
  ├─ 替换命令中的 {prompt_file}
  ├─ subprocess.run(shell=True, capture_output=True, timeout=N)
  ├─ 清理临时文件（finally 块保证执行）
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

**为什么用文件传 prompt 而不是命令行参数？**

| | 命令行参数 | 文件 / stdin |
|---|---|---|
| 长度限制 | Windows cmd 8191 字符 | 无限制 |
| 特殊字符 | 引号、换行需转义 | 无需处理 |
| 调试 | 难以复现 | 保留文件即可复现 |

Gemini 通过 stdin 管道（`cat {prompt_file} | gemini -p " " --yolo`）规避了命令行长度限制，同样属于"文件间接传入"的变体。

**重试机制**：`invoke_with_retry(max_retries=2)` — 失败立即重试，所有重试失败后返回"本轮缺席"占位响应，不中断整场会议。

---

### `lib/prompt_builder.py` — Prompt 组装

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

**历史注入逻辑**

```
round_num == 1:
    history_section = "（第一轮，请独立思考，不参考他人观点）"
    + prior_proposal（如有）
    + user_feedback（如有）

round_num >= 2:
    history_section = 所有历史轮次的格式化发言文本
    （顺序调用时还包含当前轮中已发言者的内容）
```

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

### `lib/orchestrator.py` — 核心会议循环

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
council.py::new()
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
| Agent CLI 调用超时 | 返回"本轮缺席"占位，重试 2 次后跳过 |
| Agent CLI 进程崩溃 | 捕获异常，返回错误信息，继续下一个 Agent |
| 空输出 | 视为失败，触发重试 |
| 共识检测 JSON 解析失败 | 返回 `unknown`（level=none），不中断流程 |
| 纪要/方案生成失败 | 返回含错误信息的降级文档 |
| 未知 Agent ID | 启动时校验，立即报错退出 |
| 配置文件缺少 `{prompt_file}` | 加载时校验，立即报错退出 |

---

## 各 CLI 实测对照（Windows，2026-04）

验证版本：`claude 2.1.86` / `codex 0.117.0` / `gemini 0.35.3` / `kimi 1.28.0`

| 维度 | Claude | Codex | Gemini | Kimi |
|------|--------|-------|--------|------|
| 非交互标志 | `-p {file}` | `exec -o {file}` | stdin + `-p " "` | `-p "$(cat {file})"` |
| 自动批准 | 不需要 | `--full-auto` | `--yolo` | `-y` |
| 输出来源 | stdout | 临时输出文件 | stdout | stdout |
| stdout 噪声 | 无 | 有（header + token stats + 重复） | 无 | 无 |
| Windows 特殊处理 | 需要 git-bash | 无 | 无 | 注意 gbk 编码 |
| 模型选择 | `--model claude-xxx` | 账号决定（API key 可用 `-m`）| `-m gemini-xxx` | `-m moonshot-xxx` |
| 在 git 外运行 | 无限制 | 需要 `--skip-git-repo-check` | 无限制 | 无限制 |

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

## 关键设计决策

### 为什么用文件传 prompt 而不是管道（stdin）

Windows 上 `subprocess` 的 `stdin=PIPE` 在某些 CLI 下行为不一致（特别是 Node.js 进程），而文件方式跨平台稳定，且便于调试（可直接查看临时文件内容）。Gemini 是例外，它通过 `cat | gemini` 的 shell 管道实现，底层仍是文件读取，只是绕过了命令行长度限制。

### 为什么不并行运行多个 Session

Session 之间存在依赖（下一 Session 的输入是上一 Session 的 proposal），且每个 Session 之间设计了用户干预点，顺序执行符合设计意图。

### 为什么用 JSON 而不是 SQLite 存储元数据

- 无需额外依赖
- 人类可读，便于手动修改和调试
- 文件级别的并发安全（单进程写入）
- 对于会议记录规模，JSON 性能完全足够

### 为什么摘要和方案生成也用 LLM 而不是规则提取

结构化摘要和融合各方观点的方案文档需要理解能力，规则提取无法处理自然语言的歧义和多样性。使用最便宜的 Agent（`claude-sonnet`）做此类"后处理"任务，在质量和成本上取得平衡。
