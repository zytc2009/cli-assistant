# CLI Assistant — 多 AI 讨论编排器

将多个 AI CLI（Claude Code、Codex、Gemini、Kimi 等）组织成结构化的讨论会议。每次会议由 Orchestrator 担任"主持人"，驱动各 AI 完成多轮交叉讨论，最终输出会议纪要与可执行方案。

```
用户提出议题
    → brainstorm：各方独立提案 → 交叉评审 → 融合
    → review：深入评审 → 给出修改建议
    → decision：最终评估 → [AGREE] / [DISAGREE]
    → 输出 final_proposal.md
```

---

## 目录

- [快速开始](#快速开始)
- [安装](#安装)
- [CLI 对接详情](#cli-对接详情)
- [使用指南](#使用指南)
- [配置说明](#配置说明)
- [技术架构](#技术架构)
- [文件结构](#文件结构)
- [扩展指南](#扩展指南)
- [常见问题](#常见问题)

---

## 快速开始

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 逐一验证各 CLI 连通性
python cli_assistant.py test-round "测试" --agent claude-sonnet
python cli_assistant.py test-round "测试" --agent codex
python cli_assistant.py test-round "测试" --agent gemini
python cli_assistant.py test-round "测试" --agent kimi

# 3. 发起一次完整的技术选型会议（3 阶段自动串联）
python cli_assistant.py new "微服务通信方案选型" --preset tech_selection --strategy balanced

# 4. 查看结果
python cli_assistant.py list
python cli_assistant.py show <topic_id> --proposal
```

---

## 安装

### Python 依赖

```bash
pip install -r requirements.txt
```

| 包 | 用途 |
|---|---|
| `pyyaml` | 读取 YAML 配置文件 |
| `click` | CLI 命令框架 |
| `rich` | 终端彩色输出、进度条、Markdown 渲染 |

### 各 CLI 安装

| CLI | 安装命令 | 验证 |
|-----|---------|------|
| Claude Code | `npm install -g @anthropic-ai/claude-code` | `claude --version` |
| Codex | `npm install -g @openai/codex` | `codex --version` |
| Gemini CLI | `npm install -g @google/gemini-cli` | `gemini --version` |
| Kimi | 参考 [官方文档](https://kimi.moonshot.cn/cli) | `kimi --version` |

本项目经验证的版本（供参考）：

```
claude  2.1.86
codex   0.117.0
gemini  0.35.3
kimi    1.28.0
```

---

## CLI 对接详情

> 本节记录各 CLI 的**实际验证**过的非交互调用方式，以及已知的平台限制。

### Claude Code

**非交互模式**：`-p/--print` 标志

```bash
claude -p "{prompt_file}" --output-format text
```

**Windows 注意事项**：

1. **Git 必须添加到 PATH**：Claude CLI 依赖 git-bash，安装 [Git for Windows](https://git-scm.com/download/win) 时需选择 **"Git from the command line and also from 3rd-party software"**，或手动将 `C:\Program Files\Git\bin` 添加到系统 PATH。

2. **验证配置**：
   ```powershell
   # 在 PowerShell 或 cmd 中运行
   where bash
   # 应输出类似: C:\Program Files\Git\bin\bash.exe
   ```

3. **如仍无法找到 bash**，可手动设置环境变量：
   ```powershell
   # PowerShell
   $env:CLAUDE_CODE_GIT_BASH_PATH = "C:\Program Files\Git\bin\bash.exe"
   
   # 或永久设置
   [Environment]::SetEnvironmentVariable("CLAUDE_CODE_GIT_BASH_PATH", "C:\Program Files\Git\bin\bash.exe", "User")
   ```

4. `--output-format text` 确保输出纯文本，去掉 ANSI 格式符
5. 支持 `--model` 指定模型：`claude-opus-4-6` / `claude-sonnet-4-6`

**agents.yaml 配置**：

```yaml
claude-sonnet:
  name: "Claude Sonnet"
  cli: claude
  model: claude-sonnet-4-6
  command: 'claude -p "{prompt_file}" --model claude-sonnet-4-6 --output-format text'
  prompt_method: file
  max_tokens: 4000
  timeout: 120
  strengths: "代码实现、快速迭代、性价比高"
  cost_tier: medium
```

---

### Codex

**非交互模式**：`codex exec` 子命令 + `-o` 输出到文件

```bash
codex exec --skip-git-repo-check --full-auto --ephemeral \
  -o {output_file} "$(cat {prompt_file})"
```

**关键标志说明**：

| 标志 | 作用 |
|------|------|
| `exec` | 非交互子命令（区别于默认的交互模式） |
| `--skip-git-repo-check` | 允许在非 git 目录下运行 |
| `--full-auto` | 自动批准所有操作，无需确认 |
| `--ephemeral` | 不保存 session 文件到磁盘 |
| `-o {output_file}` | 将最后一条消息写入文件（避免 stdout 噪声） |

**stdout 噪声说明**（为什么必须用 `-o`）：

不加 `-o` 时，stdout 包含额外内容：
```
codex                          ← 模型标识头
实际回答内容...
tokens used                    ← token 统计尾
1,433
实际回答内容...                 ← 回答重复一次
```

加 `-o {output_file}` 后，文件中只有干净的最后一条消息。

**`{output_file}` 占位符**：`agent_runner.py` 会在运行时自动创建临时文件并替换此占位符，使用方式与 `{prompt_file}` 相同。

**agents.yaml 配置**：

```yaml
codex:
  name: "Codex"
  cli: codex
  model: ""
  command: 'codex exec --skip-git-repo-check --full-auto --ephemeral -o {output_file} "$(cat {prompt_file})"'
  prompt_method: file
  output_method: file          # 告知 runner 从 output_file 读取结果
  max_tokens: 4000
  timeout: 120
  strengths: "工程实现、代码生成、工具调用"
  cost_tier: medium
```

**账号限制**：若使用 ChatGPT 账号登录，只能使用默认模型，`-m o3` / `-m o4-mini` 会报错。OpenAI API key 用户无此限制。

---

### Gemini CLI

**非交互模式**：stdin 管道 + `-p/--prompt` 标志 + `--yolo`

```bash
cat {prompt_file} | gemini -p " " --yolo
```

**关键标志说明**：

| 标志 | 作用 |
|------|------|
| `-p " "` | 触发非交互（headless）模式；stdin 内容会追加到 `-p` 的值 |
| `--yolo` | 自动批准所有工具调用，无需手动确认 |
| `cat ... \|` | 通过 stdin 传入 prompt（避开命令行长度限制） |

**为什么不用 `gemini -p "$(cat {prompt_file})"`**：
- Windows cmd 命令行长度限制 8191 字符
- 长 prompt（多轮历史）会截断，导致上下文丢失
- stdin 管道无此限制

**agents.yaml 配置**：

```yaml
gemini:
  name: "Gemini"
  cli: gemini
  model: gemini-2.0-flash
  command: 'cat {prompt_file} | gemini -p " " --yolo'
  prompt_method: file
  max_tokens: 4000
  timeout: 120
  strengths: "多模态理解、知识广度、Google 生态"
  cost_tier: low
```

---

### Kimi

**非交互模式**：stdin 管道 + `--input-format text` + `--output-format stream-json`

```bash
export PYTHONIOENCODING=utf-8 && cat {prompt_file} | kimi --print --input-format text --output-format stream-json
```

**关键标志说明**：

| 标志 | 作用 |
|------|------|
| `--print` | 非交互模式（print mode） |
| `--input-format text` | 从 stdin 读取 prompt 内容 |
| `--output-format stream-json` | 输出 JSON 流格式 |
| `PYTHONIOENCODING=utf-8` | 解决 Windows UTF-8 编码问题 |

> **注意**：
> 1. 不使用 `-p` 参数，因为 `-p` 会直接使用参数值作为输入，忽略 stdin
> 2. 使用 `stream-json` 格式时，程序会自动解析提取 `type=text` 的内容

**agents.yaml 配置**：

```yaml
kimi:
  name: "Kimi"
  cli: kimi
  model: default
  command: 'export PYTHONIOENCODING=utf-8 && cat {prompt_file} | kimi --print --input-format text --output-format stream-json'
  prompt_method: file
  output_format: json    # 告知程序解析 JSON 输出
  max_tokens: 4000
  timeout: 120
  strengths: "产品视角、用户体验、中文场景、长上下文"
  cost_tier: medium
```

---

### 快速对比

| CLI | 非交互方式 | 输出来源 | 自动批准标志 | Windows 特殊处理 |
|-----|-----------|---------|------------|----------------|
| Claude | `-p {file}` | stdout | 无需 | 需要 git-bash |
| Codex | `exec -o {file}` | 输出文件 | `--full-auto` | 无 |
| Gemini | stdin pipe + `-p " "` | stdout | `--yolo` | 无 |
| Kimi | stdin pipe + `--input-format text` | JSON stream | `--print` | PYTHONIOENCODING |

---

## 使用指南

CLI Assistant 支持多种交互方式，适应不同使用场景：

| 模式             | 启动方式                           | 主持人                       | 输出方式     | 适用场景             |
| ---------------- | ---------------------------------- | ---------------------------- | ------------ | -------------------- |
| **交互式向导**   | `council`（无参数）                | 自由讨论用户选择，需求讨论自动 | 流式实时输出 | 快速启动、探索性讨论 |
| **Discuss 命令** | `council discuss "想法"`           | 用户选择 | 流式实时输出 | 想法讨论、方案设计   |
| **结构化会议**   | `council new` + `council continue` | 程序编排 | 批量输出     | 技术评审、多阶段决策 |
| **交互菜单**     | `council interactive`              | 程序编排 | 菜单驱动     | 复杂流程、灵活控制   |

**模式选择建议**：

- 日常快速讨论 → 使用 **交互式向导**（无参数启动）
- 明确的单次话题 → 使用 **Discuss 命令**
- 需要多轮评审 → 使用 **结构化会议**

### 流式实时输出

交互式向导和 Discuss 命令支持**流式实时输出**，执行过程中即可看到各 AI 的思考过程：

```
[Claude Sonnet] 正在思考...
────────────────────────────────────────────────────
> 从架构设计角度，我建议采用 CQRS + Event Sourcing 模式...
> 领域边界划分是成败关键...
────────────────────────────────────────────────────
✓ 完成 (12.3s)
```

**特殊输出格式处理**：
- **Kimi**: 自动解析 `--output-format stream-json` 的 JSON 流，提取 `type=text` 的内容
- **Codex**: 支持从 `-o {output_file}` 输出文件读取结果
- **Claude/Gemini**: 直接从 stdout 读取纯文本输出

### 交互式向导（推荐）

直接运行 `council`（或 `python cli_assistant.py`）进入交互式向导。输入问题后，向导会先让你选择讨论模式，再根据模式展示不同步骤：

- 自由讨论：5 步，包含主持人选择和最大轮次配置
- 需求讨论：4 步，不选择主持人，也不单独配置最大轮次

**自由讨论示例**

```bash
$ python cli_assistant.py

══════════════════════════════════════════════════════
  🤖 Multi-AI Discussion Council
══════════════════════════════════════════════════════

[第1步] 请输入您的问题/想法（直接回车结束输入）：
> 我想设计一个事件驱动的微服务架构
> 用于处理电商订单流程

[讨论模式] 请选择本次讨论的目标：
  [1] 自由讨论 - 多 AI 自由探讨观点，输出会议纪要
  [2] 需求讨论 - 多 AI 协作澄清需求，输出 requirement.md（喂给 auto-dev）
选择 [1/2，默认 1]: 1

[第2步] 检测本地可用的 AI CLI...
  [✓] claude      - Claude Code (2.1.86)
  [✓] codex       - OpenAI Codex (0.117.0)
  [✗] gemini      - Google Gemini (未安装)
  [✓] kimi        - Moonshot Kimi (1.28.0)

[第3步] 选择参与讨论的 AI（输入编号，多个用逗号分隔）：
  [1] claude      - 深度推理、架构设计、代码实现
  [2] codex       - 复杂推理、数学、算法、工程实现
  [3] kimi        - 产品视角、用户体验、中文场景、长上下文
选择: 1,2,3

[第4步] 选择主持人：
  [1] Claude Sonnet - 擅长：架构设计、系统性分析
  [2] Codex o4-mini - 擅长：工程实现、性能优化
  [3] Moonshot Kimi - 擅长：产品视角、用户体验
选择: 1

[第5步] 讨论配置：
  最大轮次 [3]: 2

══════════════════════════════════════════════════════
  讨论开始
══════════════════════════════════════════════════════

Phase 1: 收集各方观点
[claude] 正在思考...
────────────────────────────────────────────────────
> 从架构设计角度，我建议采用 CQRS + Event Sourcing...
────────────────────────────────────────────────────
✓ 完成 (12.3s)
...
```

**需求讨论示例**

```bash
$ python cli_assistant.py

══════════════════════════════════════════════════════
  🤖 Multi-AI Discussion Council
══════════════════════════════════════════════════════

[第1步] 请输入您的问题/想法（直接回车结束输入）：
> 我要梳理一个内部知识库问答系统的需求

[讨论模式] 请选择本次讨论的目标：
  [1] 自由讨论 - 多 AI 自由探讨观点，输出会议纪要
  [2] 需求讨论 - 多 AI 协作澄清需求，输出 requirement.md（喂给 auto-dev）
选择 [1/2，默认 1]: 2

[第2步] 检测本地可用的 AI CLI...
...

[第3步] 选择参与讨论的 AI（输入编号，多个用逗号分隔）：
选择: 1,2

══════════════════════════════════════════════════════
  讨论开始
══════════════════════════════════════════════════════

Phase 1: 收集各方观点
...
```

需求讨论模式下，不设轮数上限，不选择主持人，由用户输入 `d` 或所有字段收敛时才结束。

---

### 命令一览

```
council                  交互式向导（无参数时自动启动）
cli_assistant.py discuss       讨论模式（Phase 1-3，实时输出）
cli_assistant.py new           发起新议题（单阶段或预设流程）
cli_assistant.py continue      继续已有议题的下一阶段
cli_assistant.py interactive   交互式菜单模式
cli_assistant.py finalize      将最新方案标记为定稿
cli_assistant.py list          列出所有历史议题
cli_assistant.py show          查看议题详情 / 方案 / 纪要
cli_assistant.py test-round    测试单个 Agent 连通性
cli_assistant.py agent         Agent 配置管理（detect/list/add/remove）
```

---

### `discuss` — 讨论模式（Phase 1-3）

针对一个想法/问题，进行三阶段结构化讨论，**实时显示各 AI 的输出**：

```bash
python cli_assistant.py discuss "想法/问题" [选项]
```

| 选项 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--agents` | `-a` | 全部 | 逗号分隔的 Agent ID |
| `--rounds` | `-r` | 3 | Phase 2 最大讨论轮次 |
| `--moderator` | `-m` | — | 指定主持人（默认自动选择） |

**示例**

```bash
# 最简：使用所有配置好的 AI
python cli_assistant.py discuss "如何设计一个高并发的订单系统"

# 指定参与者和轮次
python cli_assistant.py discuss "API 限流方案" -a claude-sonnet,codex,kimi -r 2

# 指定主持人
python cli_assistant.py discuss "数据库分片策略" -a claude-sonnet,codex -m claude-sonnet
```

**讨论流程**

自由讨论（默认）：

```
Phase 1: 独立发言
├── 所有 AI 独立发表观点（互不可见，避免锚定）
└── 每个 AI 输出实时显示

Phase 2: 主持人引导讨论（最多 max_rounds 轮）
├── 主持人开场引导本轮焦点
├── 其他 AI 依次回应，可见历史发言
└── 每轮结束可补充用户意见；共识达成时提前退出

Phase 3: 综合输出
└── 主持人生成结构化最终文档
```

需求讨论（选择模式 2）：

```
Phase 1: 各 AI 独立审视需求
└── 每个 AI 输出：已明确字段 / 待澄清问题 / 假设前提

用户回答环节
└── 可一次性回答多个问题，或直接跳过

需求理解确认（Phase 2 开始前）
├── AI 综合 Phase 1 + 用户回答，生成确认清单：
│   ├── 已明确：各字段当前理解
│   ├── 准备做的假设：用户未回答时 AI 的默认处理
│   └── 仍有疑问：必须用户回答的问题
└── 用户确认，或输入纠正（直接回车跳过）

视觉方案选择（可选，仅启用 Visual Companion 时）
├── AI 判断议题是否涉及 UI/架构/流程图
├── 若涉及：生成 2-3 个 HTML 方案推送到浏览器
├── 用户在浏览器点选，选择结果注入后续讨论
└── 若不涉及：静默跳过，继续文本讨论

Phase 2: 无主持人迭代澄清（无轮数限制）
├── 所有 AI 平等发言，每轮输出：
│   ├── 字段当前认知（Goal/Scope/Inputs/Outputs/AC）
│   ├── 仍待澄清的问题（必须由用户回答的）
│   ├── 假设（用户未回答时 AI 自行推进，不再追问）
│   └── 已收敛的字段 [CONVERGED]
├── 每轮后：展示待澄清问题，可选用户补充（回车跳过）
├── 启用 Visual Companion 时：浏览器同步刷新需求收敛看板
└── 所有字段收敛 → 自动退出；输入 d → 手动结束

Phase 3: 综合生成 requirement.md
├── 由最便宜的可用 AI 综合全程信息输出需求文档
├── 自动质检：检查完整性/一致性/清晰度/范围/YAGNI
├── 若发现问题：提示用户，可选择自动修正一轮
└── 最终保存 requirement.md
```

**与 `new` 模式的区别**

| | `discuss` | `new` |
|---|-----------|-------|
| 流程 | 固定三阶段 | 可配置多 Session |
| 输出 | 实时流式显示 | 批量结束后显示 |
| 适用 | 快速讨论一个想法 | 复杂多阶段会议 |
| 交互 | 轻量级 | 完整会议纪要 |

---

### `new` — 发起新会议

```bash
python cli_assistant.py new "议题" [选项]
```

| 选项 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--agents` | `-a` | 全部 | 逗号分隔的 Agent ID，如 `claude-sonnet,gemini` |
| `--mode` | `-m` | `brainstorm` | 单阶段类型：`brainstorm` / `review` / `decision` |
| `--strategy` | `-s` | — | 模型策略：`high_stakes` / `balanced` / `budget` |
| `--rounds` | `-r` | 模板默认 | 覆盖最大轮次 |
| `--preset` | `-p` | — | 预设流程：`tech_selection` / `code_review` / `architecture` / `postmortem` |

**示例**

```bash
# 最简：单阶段 brainstorm
python cli_assistant.py new "gRPC vs REST 选型"

# 指定参会者 + 轮次
python cli_assistant.py new "数据库选型" -a claude-sonnet,gemini,kimi -r 2

# 完整技术选型三阶段（balanced 策略）
python cli_assistant.py new "缓存方案设计" --preset tech_selection --strategy balanced

# 省钱模式
python cli_assistant.py new "代码规范讨论" --strategy budget --mode brainstorm
```

---

### `continue` — 继续下一阶段

```bash
python cli_assistant.py continue <topic_id> [选项]
```

| 选项 | 简写 | 说明 |
|------|------|------|
| `--feedback` | `-f` | 向下一阶段注入用户意见 |
| `--mode` | `-m` | 手动指定阶段类型（默认自动推进） |
| `--agents` | `-a` | 替换本轮参会者 |
| `--strategy` | `-s` | 切换模型策略 |

**示例**

```bash
# 自动推进到 review 阶段
python cli_assistant.py continue topic_缓存方案设计_a1b2c3

# 附加用户意见
python cli_assistant.py continue topic_xxx -f "重点考虑性能，成本不是首要因素"

# 换用更强模型做最终决策
python cli_assistant.py continue topic_xxx --mode decision --strategy high_stakes

# 替换参会者
python cli_assistant.py continue topic_xxx -a claude-opus,kimi

# Discuss 模式：继续完成 Phase 3 综合阶段
python cli_assistant.py continue topic_xxx
```

**Discuss 模式特殊用法**：

Discuss 模式（`council discuss` 或交互式向导）如果在中途停止，可以使用 `continue` 命令继续：
- Phase 1-2 已完成 → 自动执行 Phase 3（综合输出）
- 已 finalized → 显示现有结果

---

### `interactive` — 交互式模式

```bash
python cli_assistant.py interactive "议题" [--agents a,b,c] [--strategy balanced]
```

启动后进入菜单驱动界面，每个阶段结束后可输入补充意见或直接 `q` 定稿退出。

```
会议已创建：微服务通信方案选型
ID: 微服务通信方案选型_a1b2c3

请选择操作：
  [1] → 开始 brainstorm — 发散思维，收集各方观点
  [2]   开始 review — 对已有方案进行评审和改进
  [3]   开始 decision — 收敛到最终方案
  [q] 退出并定稿
  [s] 查看当前状态
```

---

### `finalize` — 定稿

```bash
python cli_assistant.py finalize <topic_id>
```

将最新 Session 的 `proposal.md` 复制为 `final_proposal.md`，状态标记为 `finalized`。

---

### `list` — 查看所有议题

```bash
python cli_assistant.py list
```

---

### `show` — 查看议题详情

```bash
python cli_assistant.py show <topic_id> [--proposal] [--minutes]
```

| 选项 | 说明 |
|------|------|
| `--proposal` | 在终端渲染最新方案文档 |
| `--minutes` | 在终端渲染最新会议纪要 |

---

### `test-round` — 测试 Agent 连通性

正式会议前，验证各 CLI 链路是否通畅：

```bash
python cli_assistant.py test-round "测试议题" --agent claude-sonnet
python cli_assistant.py test-round "测试议题" --agent codex
python cli_assistant.py test-round "测试议题" --agent gemini
python cli_assistant.py test-round "测试议题" --agent kimi
```

成功时输出 Agent 的原始回答和耗时；失败时显示错误信息供排查。

---

### `agent` — Agent 配置管理

管理 `config/agents.yaml` 中的 AI CLI 配置。

#### `agent detect` — 检测本地 CLI

自动扫描已安装的 AI CLI：

```bash
$ python cli_assistant.py agent detect

  CLI          名称                  状态          版本         擅长领域
─────────────────────────────────────────────────────────────────────────────────
  claude       Claude Code           ✓ 已安装      2.1.86       深度推理、架构设计
  codex        OpenAI Codex          ✓ 已安装      0.117.0      复杂推理、数学
  gemini       Google Gemini         ✗ 未安装      -            多模态理解
  kimi         Moonshot Kimi         ✓ 已安装      1.28.0       产品视角、中文场景

检测到 3 个已安装 CLI
```

#### `agent list` — 列出配置

```bash
$ python cli_assistant.py agent list

  Agent ID        名称                  CLI          模型                  成本      超时
────────────────────────────────────────────────────────────────────────────────────────────
  claude-opus     Claude Opus           claude       claude-opus-4-6       high      180s
  claude-sonnet   Claude Sonnet         claude       claude-sonnet-4-6     medium    120s
  codex-o3        Codex o3              codex        o3                    high      180s
  ...
```

#### `agent add` — 添加 Agent

交互式添加新 Agent 到配置：

```bash
# 添加已知 CLI（自动填充命令和擅长领域）
$ python cli_assistant.py agent add claude
添加已知 CLI: Claude Code
命令: claude -p "{prompt_file}" --output-format text
擅长: 深度推理、架构设计、代码实现
显示名称 [Claude Code]:
✓ 已添加 Claude Code 到 agents.yaml

# 添加自定义 CLI
$ python cli_assistant.py agent add my-custom-cli
添加自定义 CLI: my-custom-cli
显示名称: My Custom AI
命令模板（使用 {prompt_file} 作为 prompt 文件占位符）:
命令: myai -f {prompt_file}
擅长领域 [通用能力]: 自然语言处理
✓ 已添加 My Custom AI 到 agents.yaml
```

#### `agent remove` — 删除 Agent

```bash
$ python cli_assistant.py agent remove kimi
确认删除 'Kimi' (kimi)? [y/N]: y
✓ 已删除 Kimi
```

---

## 配置说明

### Agent 注册（`config/agents.yaml`）

每个 Agent 条目的字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✓ | 显示名称，注入到 prompt 中 |
| `cli` | ✓ | CLI 类型标识（仅用于分类） |
| `model` | | 模型 ID（部分 CLI 通过此字段选模型） |
| `command` | ✓ | 调用命令，必须含 `{prompt_file}`；Codex 还需 `{output_file}` |
| `prompt_method` | ✓ | 固定为 `file` |
| `output_method` | | `file` 表示从 `{output_file}` 读取结果，省略则从 stdout 读 |
| `max_tokens` | | Token 上限（用于上下文长度预估） |
| `timeout` | ✓ | subprocess 超时秒数，超时后自动重试 |
| `strengths` | | 擅长领域描述，注入 prompt 作为角色设定 |
| `cost_tier` | | `high` / `medium` / `low`，影响策略自动选择 |

---

### 会议模板（`config/meeting_templates.yaml`）

三种内置模板：

| 模板 | 轮次 | 目标 |
|------|------|------|
| `brainstorm` | 3 | 发散思维，形成初步方案 |
| `review` | 2 | 深入评审，给出修改建议 |
| `decision` | 2 | 最终评估，输出 [AGREE]/[DISAGREE] |

---

### 模型策略（`config/model_strategies.yaml`）

| 策略 | 适用场景 | 成本 |
|------|----------|------|
| `high_stakes` | 重要架构决策 | 高 |
| `balanced` | 日常技术讨论（推荐） | 中 |
| `budget` | 快速头脑风暴 | 低 |

---

### 预设流程（`presets`）

| 预设 | 阶段 | 适用场景 |
|------|------|----------|
| `tech_selection` | brainstorm → review → decision | 技术方案选型 |
| `code_review` | review | 代码评审 |
| `architecture` | brainstorm × 2 → review → decision | 大型架构设计 |
| `postmortem` | brainstorm → decision | 事故复盘 |

---

### Prompt 模板（`config/prompts/`）

| 文件 | 用途 |
|------|------|
| `base_system.md` | 每个 Agent 每轮发言的主 prompt |
| `minutes_generator.md` | 会议纪要生成 prompt |
| `proposal_generator.md` | 方案文档生成 prompt |
| `summarizer.md` | 上下文压缩（长讨论摘要）prompt |
| `consensus_detector.md` | 共识检测 prompt，要求输出 JSON |

可直接编辑调整 AI 行为风格和输出格式，无需改 Python 代码。

`base_system.md` 中的占位符：

| 占位符 | 说明 |
|--------|------|
| `{topic}` | 议题标题 |
| `{session_type}` | 阶段类型 |
| `{session_description}` | 阶段描述 |
| `{round}` / `{max_rounds}` | 当前轮次 / 总轮次 |
| `{agent_name}` | 当前 Agent 显示名 |
| `{agent_strengths}` | Agent 擅长领域 |
| `{round_rule}` | 本轮规则说明 |
| `{agent_list}` | 参会者列表 |
| `{history_section}` | 历史发言（自动生成） |

---

## 技术架构

详见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

---

## 文件结构

```
cli-assistant/
├── cli_assistant.py                    # CLI 入口（click 命令组）
├── requirements.txt
├── README.md                     # 本文件
├── ARCHITECTURE.md               # 技术架构文档
│
├── config/
│   ├── agents.yaml               # Agent CLI 注册与参数
│   ├── meeting_templates.yaml    # 会议阶段模板
│   ├── model_strategies.yaml     # 模型策略 + 预设流程
│   └── prompts/
│       ├── base_system.md        # 主 prompt 模板
│       ├── minutes_generator.md
│       ├── proposal_generator.md
│       ├── summarizer.md
│       ├── consensus_detector.md
│       ├── requirement_reviewer.md      # 需求文档自动质检
│       ├── requirement_*.md             # 需求讨论专用 prompts
│       └── visual_option_generator.md   # Visual Companion 方案生成
│
├── lib/
│   ├── config.py                 # 配置加载、数据类、校验
│   ├── agent_runner.py           # subprocess CLI 调用封装
│   ├── streaming_runner.py       # 实时流式输出 runner
│   ├── prompt_builder.py         # prompt 组装（历史注入）
│   ├── meeting.py                # 会议/讨论状态 + JSON 持久化
│   ├── orchestrator.py           # 核心会议循环（new/continue）
│   ├── discussion_orchestrator.py # 讨论编排器（discuss 模式）
│   ├── summarizer.py             # 纪要 + 方案文档生成
│   ├── consensus.py              # 共识检测
│   ├── context.py                # 上下文压缩
│   ├── cli_detector.py           # CLI 自动检测
│   └── visual_companion.py       # 浏览器视觉伴侣管理
│
├── visual/                       # Visual Companion 服务端
│   ├── server.cjs                # Node HTTP + WebSocket 服务器
│   ├── frame-template.html       # 页面框架与主题 CSS
│   └── helper.js                 # 客户端交互脚本
│
├── tests/                        # 测试（TODO）
│   ├── unit/                     # 单元测试
│   └── integration/              # CLI 集成测试
│
└── meetings/                     # 会议记录（自动生成）
    └── {topic_id}/
        ├── meeting.json          # 元数据
        ├── topic.md              # 议题描述
        ├── final_proposal.md     # 定稿方案
        └── session_01/
            ├── minutes.md        # 会议纪要
            ├── proposal.md       # 本阶段方案
            └── raw/
                ├── round_01_claude-sonnet.md
                ├── round_01_codex.md
                └── round_02_gemini.md
```

---

## 扩展指南

### 添加新 Agent

**方式一：使用命令（推荐）**

```bash
# 添加已知 CLI（自动填充配置）
python cli_assistant.py agent add claude

# 添加自定义 CLI
python cli_assistant.py agent add my-ai
```

**方式二：手动编辑配置**

**第一步**：在 `config/agents.yaml` 中添加条目，确保命令包含 `{prompt_file}`

**第二步**：用 `test-round` 验证连通性

```bash
python cli_assistant.py test-round "测试" --agent your-new-agent
```

**第三步**：将新 Agent ID 加入 `config/model_strategies.yaml` 的相关策略

```yaml
model_strategies:
  balanced:
    brainstorm: [claude-sonnet, your-new-agent, kimi]
```

### 自定义会议模板

在 `config/meeting_templates.yaml` 中添加新模板，然后用 `--mode` 指定：

```yaml
templates:
  security_audit:
    description: "安全审计"
    max_rounds: 2
    speaking_order: round_robin
    round_rules:
      1: "从安全视角审查方案，列出潜在漏洞"
      2: "给出具体加固建议"
    output: audit_report
```

---

## Visual Companion

在需求讨论中启用 Visual Companion 后，系统会在 **Phase 1 结束后** 自动判断议题是否涉及 UI/架构/流程图。若判断为是，会生成 2-3 个 HTML 线框图/架构图方案并推送到本地浏览器，你可以直接点选偏好的方案，选择结果会被记录并纳入后续讨论上下文。

**启用方式**：在交互式向导选择「需求讨论」后，选择 `[1] yes` 启用。

**浏览器看板**：
- Phase 1 结束：显示各 AI 的初步理解和参会者
- Phase 2 每轮：实时刷新「需求澄清看板」，展示已收敛字段、待收敛字段和待澄清问题
- Phase 3：显示「正在生成最终需求文档」

**前置依赖**：需要本地安装 Node.js（用于启动 `visual/server.cjs`）。

---

## 需求文档自动质检

需求讨论进入 Phase 3 后，生成 `requirement.md` 前会自动执行一轮质检，检查维度：
- **完整性**：是否有 TODO / TBD / 留空占位符
- **一致性**：各段落之间是否互相矛盾
- **清晰度**：是否存在可被两种理解的歧义表述
- **范围**：是否过于庞大，涵盖多个可独立子系统
- **YAGNI**：是否包含过度设计或未请求的功能

若发现问题，终端会展示审阅结果，并询问是否根据审阅意见自动修正一轮。

---

## 常见问题

**Q: `test-round` 报"Unknown agent"？**

检查 `config/agents.yaml` 中的 Agent ID 拼写，用 `python cli_assistant.py agent list` 查看可用 ID。

**Q: Claude 在 Windows 上报"requires git-bash"？**

安装 [git-bash](https://git-scm.com/downloads/win)，然后设置环境变量：
```
CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe
```

**Q: Codex 报"model not supported"？**

使用 ChatGPT 账号登录时只能使用默认模型，去掉 `-m` 参数即可。

**Q: Gemini 每次都弹出工具确认？**

确保命令中包含 `--yolo` 标志。

**Q: Kimi 报 gbk 编码错误？**

设置环境变量后重试：
```bash
set PYTHONIOENCODING=utf-8   # Windows
```

**Q: Agent 调用超时？**

增大 `config/agents.yaml` 中对应 Agent 的 `timeout` 值（单位：秒）。系统会自动重试 2 次。

**Q: 如何调试 Agent 调用问题？**

每次调用时，完整的 prompt 会自动保存到 `logs/{agent_name}_{timestamp}.md`，可用于：
- 检查 prompt 内容是否符合预期
- 直接用 CLI 测试：`cat logs/codex_xxx.md | codex ...`
- 排查格式错误或内容过长问题

**Q: Discuss 模式 Phase 3 失败如何恢复？**

使用 `continue` 命令继续执行 Phase 3：
```bash
python cli_assistant.py continue topic_id
```
已完成的 Phase 1-2 数据不会丢失。

**Q: 历史记录太长导致 prompt 超限？**

`lib/context.py` 超过 3000 字符时自动压缩早期轮次为摘要，可调整 `max_chars` 参数。

**Q: 如何复现某次讨论？**

每轮原始输出保存在 `meetings/{topic_id}/session_XX/raw/`，可直接查阅或重新投喂给 CLI。
