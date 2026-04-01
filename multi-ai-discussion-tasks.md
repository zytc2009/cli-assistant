# Multi-AI Discussion — 实现任务清单（交互式向导版）

> **状态更新（2026-04-01）**：Phase 0-4 已全部完成，P1 功能（检测保存/手动回退）部分完成。

基于更新后的设计文档，实现无参数启动的交互式向导模式。

---

## Phase 0: CLI 检测模块 ✅

### Task 0.1 — 创建 `lib/cli_detector.py` ✅

**文件**：`lib/cli_detector.py`

**功能**：
- 定义 `CLIDetected` 数据类（name, cli_id, version, is_installed, command）
- 定义 `CLIDetector` 类
- 实现 `detect_all()` — 检测所有已知 CLI，返回可用列表
- 实现 `detect_one(cli_name)` — 检测单个 CLI
- 支持检测：claude, codex, kimi, gemini

**检测方法**：使用 `shutil.which()` 检查命令是否存在

**验证**：单元测试检测本地 CLI（ mock 测试 + 真实环境测试）

---

### Task 0.2 — 创建 `lib/streaming_runner.py` ✅

**文件**：`lib/streaming_runner.py`

**功能**：
- 定义 `StreamingRunner` 类
- 实现 `invoke_streaming(agent_name, prompt_content, on_output)` 方法
- 使用 `subprocess.Popen` + 逐行读取 stdout
- 每读到一行立即调用 `on_output(line)` 回调
- 返回 `AgentResponse` 对象

**输出格式**：
```
[agent_name] 正在思考...
────────────────────────────────────────────────────
> 第一行输出
> 第二行输出
...
────────────────────────────────────────────────────
✓ 完成 (X.Xs)
```

**验证**：测试流式输出是否正确逐行显示

---

## Phase 1: 交互式向导框架 ✅

### Task 1.1 — 多行输入收集 ✅

**文件**：`council.py` 新增函数

**功能**：
```python
def _input_idea() -> str:
    """交互式收集用户问题描述，支持多行输入。"""
    # 提示用户输入，空行结束
    # 返回收集到的完整文本
```

**交互示例**：
```
[第1步] 请输入您的问题/想法（直接回车结束输入）：
> 我想设计一个事件驱动的微服务架构
> 用于处理电商订单流程
> （回车结束）
```

---

### Task 1.2 — CLI 选择与确认 ✅

**文件**：`council.py` 新增函数

**功能**：
```python
def _select_clis(available_clis: List[CLIDetected]) -> List[str]:
    """让用户选择参与讨论的 CLI。"""
    # 显示检测到的 CLI 列表
    # 用户输入编号（支持多选，逗号分隔）
    # 返回选中的 CLI ID 列表
```

**交互示例**：
```
[第2步] 检测本地可用的 AI CLI...

  [✓] claude      - Claude Code (已安装)
  [✓] codex       - OpenAI Codex (已安装)
  [✗] gemini      - Google Gemini (未安装)
  [✓] kimi        - Moonshot Kimi (已安装)

[第3步] 选择参与讨论的 AI（输入编号，多个用逗号分隔）：
  [1] claude      - Claude Sonnet 4.6
  [2] codex       - Codex o4-mini
  [3] kimi        - Moonshot Kimi

选择: 1,2,3
```

---

### Task 1.3 — 主持人选择 ✅

**文件**：`council.py` 新增函数

**功能**：
```python
def _select_moderator(selected_agents: List[str], config: Config) -> str:
    """让用户从已选 AI 中选择主持人。"""
    # 显示各 AI 的擅长领域
    # 用户输入编号选择
    # 返回选中的 Agent ID
```

**交互示例**：
```
[第4步] 选择主持人：
  [1] Claude Sonnet - 擅长：架构设计、系统性分析
  [2] Codex o4-mini - 擅长：工程实现、性能优化
  [3] Moonshot Kimi - 擅长：产品视角、用户体验

选择: 1
```

---

### Task 1.4 — 配置确认 ✅

**文件**：`council.py` 新增函数

**功能**：
```python
def _confirm_config() -> dict:
    """让用户确认讨论配置。"""
    # 最大轮次（默认3）
    # 返回配置字典
```

**交互示例**：
```
[第5步] 讨论配置：
  最大轮次 [3]: 2
```

---

## Phase 2: 实时输出集成 ✅

### Task 2.1 — Phase 1 实时输出改造 ✅

**文件**：修改 `lib/discussion_orchestrator.py`

**功能**：
- 新增 `run_independent_phase_streaming()` 方法
- 使用 `StreamingRunner` 替代原有 `AgentRunner`
- 每个 Agent 输出实时显示，带分隔线

**输出格式**：
```
Phase 1: 独立发言
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[claude] 正在思考...
────────────────────────────────────────────────────
> 从架构设计角度，我建议...
> 1. 领域边界划分...
> 2. 事件总线选型...
────────────────────────────────────────────────────
✓ 完成 (12.3s)

[codex] 正在思考...
────────────────────────────────────────────────────
> 从技术实现角度...
────────────────────────────────────────────────────
✓ 完成 (8.5s)
```

---

### Task 2.2 — Phase 2 实时输出改造 ✅

**文件**：修改 `lib/discussion_orchestrator.py`

**功能**：
- 主持人开场引导实时显示
- 各 AI 回应实时显示
- 支持查看已输出内容时的交互控制

---

### Task 2.3 — Phase 3 实时输出改造 ✅

**文件**：修改 `lib/discussion_orchestrator.py`

**功能**：
- 主持人综合输出实时显示
- 显示进度（如"正在生成第2节..."）

---

## Phase 3: 无参数启动入口 ✅

### Task 3.1 — 主入口改造 ✅

**文件**：`council.py`

**功能**：
- 修改 `cli()` 主函数，支持无参数时进入交互向导
- 检测是否有命令行参数
- 无参数时调用 `_run_interactive_wizard()`

```python
@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        # 无参数，进入交互向导
        _run_interactive_wizard()
```

---

### Task 3.2 — 交互向导主流程 ✅

**文件**：`council.py` 新增函数

**功能**：
```python
def _run_interactive_wizard():
    """无参数启动时的交互式向导。"""
    # 1. 显示欢迎信息
    # 2. 调用 _input_idea() 收集问题
    # 3. 调用 cli_detector 检测 CLI
    # 4. 调用 _select_clis() 选择 CLI
    # 5. 调用 _select_moderator() 选择主持人
    # 6. 调用 _confirm_config() 确认配置
    # 7. 创建 Discussion 对象
    # 8. 使用流式版本运行 Phase 1-3
    # 9. 显示结果并保存
```

**完整流程示例**：
```
$ council

══════════════════════════════════════════════════════
  🤖 Multi-AI Discussion Council
══════════════════════════════════════════════════════

[第1步] 请输入您的问题/想法...
...

[第2步] 检测本地可用的 AI CLI...
...

[第3步] 选择参与讨论的 AI...
...

[第4步] 选择主持人...
...

[第5步] 讨论配置...
...

══════════════════════════════════════════════════════
  讨论开始
══════════════════════════════════════════════════════

Phase 1: 独立发言
...

（Phase 2, 3 ...）

══════════════════════════════════════════════════════
  讨论完成
══════════════════════════════════════════════════════

结果已保存至：meetings/xxx/final_output.md
```

---

## Phase 4: 配置持久化（P1）🔄 进行中

### Task 4.1 — 检测到的 CLI 自动保存 🔄 待实现

**文件**：`lib/cli_detector.py` 或 `council.py`

**功能**：
- 将检测到的 CLI 信息自动写入 `agents.yaml`
- 只添加新检测到的 CLI，不覆盖已有配置
- 更新 `command` 为实际检测到的可用命令

---

## Phase 5: 回退机制（P1）🔄 进行中

### Task 5.1 — 手动输入 CLI 路径 🔄 待实现

**文件**：`council.py`

**功能**：
- 如果检测不到任何 CLI，提示用户手动输入
- 支持交互式输入命令路径
- 验证输入的命令是否可用

**交互示例**：
```
[第2步] 检测本地可用的 AI CLI...

  [✗] claude      - 未安装
  [✗] codex       - 未安装
  [✗] kimi        - 未安装

未检测到任何 AI CLI，请手动输入命令路径：
> /usr/local/bin/claude

验证中... ✓ 可用
```

---

## 依赖关系与实施顺序

```
Phase 0 (基础模块)
  Task 0.1 ──→ Task 0.2
                  │
Phase 1 (交互输入)              ← 可与 Phase 0 并行
  Task 1.1, 1.2, 1.3, 1.4     ← 四个任务互相独立
                  │
Phase 2 (实时输出)
  Task 2.1 ←── Task 0.2
  Task 2.2 ←── Task 2.1
  Task 2.3 ←── Task 2.2
                  │
Phase 3 (入口集成)
  Task 3.1 ←── Task 0.1 + 1.x
  Task 3.2 ←── Task 3.1 + 2.x
                  │
Phase 4-5 (P1 优化)  ← Phase 3 完成后
  Task 4.1, 5.1
```

**最短路径**（MVP 可运行）：
```
0.1 → 0.2 → 1.1 → 1.2 → 1.3 → 1.4 → 2.1 → 2.2 → 2.3 → 3.1 → 3.2
```

**✅ 已完成（2026-04-01）**

---

## 文件变更总览

| 操作 | 文件 | Task |
|------|------|------|
| 新增 | `lib/cli_detector.py` | 0.1 |
| 新增 | `lib/streaming_runner.py` | 0.2 |
| 修改 | `lib/discussion_orchestrator.py` | 2.1, 2.2, 2.3 |
| 修改 | `council.py` | 1.1, 1.2, 1.3, 1.4, 3.1, 3.2, 5.1 |
| 修改 | `config/agents.yaml` | 4.1 |
| 不变 | `lib/agent_runner.py` | 保留原有实现 |
| 不变 | `lib/config.py` | 无需修改 |
| 不变 | `lib/meeting.py` | 无需修改 |
