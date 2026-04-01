# Testing Strategy — AI Council

---

## 目录

- [测试文件结构](#测试文件结构)
- [模块依赖关系](#模块依赖关系)
- [测试执行顺序](#测试执行顺序)
- [单元测试详情](#单元测试详情)
- [集成测试详情](#集成测试详情)
- [覆盖率目标](#覆盖率目标)
- [关键边界条件](#关键边界条件)

---

## 测试文件结构

```
tests/
├── conftest.py                     # 共享 fixtures
│
├── unit/
│   ├── test_config.py              # 配置加载与校验
│   ├── test_meeting.py             # 状态持久化与数据模型
│   ├── test_prompt_builder.py       # Prompt 组装函数
│   ├── test_context.py              # 上下文压缩
│   ├── test_consensus.py            # 共识检测（mock runner）
│   ├── test_summarizer.py           # 纪要/方案生成（mock runner）
│   ├── test_agent_runner.py          # CLI 调用封装（mock subprocess）
│   ├── test_streaming_runner.py      # 流式输出（mock subprocess）
│   ├── test_cli_detector.py          # CLI 检测（mock shutil/subprocess）
│   ├── test_orchestrator.py          # 会议编排器（mock runner）
│   └── test_discussion_orchestrator.py  # 讨论编排器（mock runner）
│
└── integration/
    ├── test_cli_new.py             # new 命令
    ├── test_cli_discuss.py         # discuss 命令
    ├── test_cli_continue.py        # continue 命令
    ├── test_cli_finalize.py        # finalize 命令
    ├── test_cli_list_show.py       # list / show 命令
    ├── test_cli_test_round.py      # test-round 命令
    └── test_cli_agent.py           # agent detect/list/add/remove 命令
```

---

## 模块依赖关系

```
config.py          ← 无依赖（纯 YAML → dataclass）
context.py         ← 无依赖（纯 token 估算）
meeting.py         ← 无依赖（纯数据 + JSON I/O）
prompt_builder.py  ← config.AgentConfig
agent_runner.py    ← config.AgentConfig
streaming_runner.py ← agent_runner.AgentResponse, config.AgentConfig
consensus.py       ← agent_runner
summarizer.py      ← agent_runner
orchestrator.py     ← orchestrator + 以上所有
discussion_orchestrator.py ← orchestrator + 以上所有
cli_detector.py    ← 无外部依赖（纯 subprocess 检测）
council.py         ← 所有模块（CLI 命令）
```

**测试隔离原则**：
- 底层模块（`config`、`meeting`、`prompt_builder`）用真实数据测试，**不 mock**
- 上层模块（`orchestrator`、`discussion_orchestrator`）mock `AgentRunner`
- CLI 集成测试用 `click.testing.CliRunner` + mock runner

---

## 测试执行顺序

| 顺序 | 模块 | 原因 |
|------|------|------|
| 1 | `test_config.py` | 验证测试 fixtures 可用 |
| 2 | `test_meeting.py` | 持久化 round-trip，无 mock |
| 3 | `test_prompt_builder.py` | 纯函数，无 mock |
| 4 | `test_context.py` | 简单 |
| 5 | `test_consensus.py` | mock runner |
| 6 | `test_summarizer.py` | mock runner |
| 7 | `test_agent_runner.py` | mock subprocess |
| 8 | `test_streaming_runner.py` | mock subprocess |
| 9 | `test_cli_detector.py` | mock subprocess/shutil |
| 10 | `test_orchestrator.py` | mock runner |
| 11 | `test_discussion_orchestrator.py` | mock runner |
| 12 | CLI 集成测试 | CliRunner + mocked runners |

---

## 单元测试详情

### `test_config.py` — 优先级 ★★★

| 函数 | 测试用例 |
|------|---------|
| `AgentConfig.validate()` | 合法配置不抛异常；缺少必填字段抛 ValueError |
| `load_agents()` | 临时 YAML，验证解析出的 dataclasses |
| `load_templates()` | 验证 round_rules int 转换 |
| `load_strategies()` | 验证各策略的 agent 列表 |
| `load_presets()` | 验证 4 种预设流程 |
| `load_prompt_template()` | 临时 .md 文件，验证内容返回 |
| `Config.get_agent()` | 合法 ID 返回配置；无效 ID 抛 ValueError |
| `Config.get_template()` | 合法/无效 ID |
| `Config.get_strategy()` | 合法/无效 ID |

### `test_meeting.py` — 优先级 ★★★

| 函数 | 测试用例 |
|------|---------|
| `create_topic_id()` | slug 生成、长度限制、唯一性 |
| `save_meeting()` / `load_meeting()` | 临时目录，round-trip 验证所有字段 |
| `save_discussion()` / `load_discussion()` | 所有 DiscussionPhase 类型 round-trip |
| `list_meetings()` | 临时目录含 meeting + discussion，验证过滤 |
| 损坏 JSON | 缺失字段、格式错误，验证优雅降级 |
| 缺失 proposal.md / minutes.md | 验证不抛异常 |

### `test_prompt_builder.py` — 优先级 ★★★

| 函数 | 测试用例 |
|------|---------|
| `build_independent_prompt()` | 占位符替换 |
| `build_moderator_opening_prompt()` | 有/无 history，有/无 user_feedback |
| `build_discussion_prompt()` | 800 字符截断，历史格式化 |
| `build_synthesis_prompt()` | 截断，user_feedbacks 连接 |
| `build_history_section()` | round_num==1 早期返回，截断 |
| `build_prompt()` | prior_proposal 前置，历史整合 |

### `test_agent_runner.py` — 优先级 ★★★

| 场景 | 测试方法 |
|------|---------|
| 未知 agent | mock `self.agents.get()`，验证错误响应 |
| timeout | mock `subprocess.run()` 抛出 `TimeoutExpired` |
| subprocess error | mock returncode != 0 |
| success (stdout) | mock stdout 有内容 |
| success (output_file) | mock `Path.read_text()` |
| 异常处理 | mock `subprocess.run()` 抛通用异常 |
| `invoke_with_retry()` 成功 | 首次失败，二次成功 |
| `invoke_with_retry()` 全失败 | 三次均失败，验证最终错误响应 |
| 临时文件清理 | mock `Path.unlink()`，验证 finally 中调用 |

### `test_streaming_runner.py` — 优先级 ★★

| 场景 | 测试方法 |
|------|---------|
| 未知 agent | 同 agent_runner |
| 流式迭代 | mock `subprocess.Popen()` + 逐行输出 |
| `invoke_with_retry_streaming()` | 首次失败重试，header 仅首次显示 |
| `on_output` 回调 | 验证每行调用一次 |

### `test_consensus.py` — 优先级 ★★

| 场景 | 测试方法 |
|------|---------|
| 有效 JSON | mock runner 返回完整 JSON，验证解析 |
| JSON 解析失败 | mock runner 返回格式错误的 JSON |
| invoke 失败 | mock runner 返回 failure response |
| `ConsensusResult.unknown()` | 验证默认值 |

### `test_summarizer.py` — 优先级 ★★

| 场景 | 测试方法 |
|------|---------|
| `generate_minutes()` 成功 | mock runner，验证 prompt 格式化 |
| `generate_minutes()` 失败 | mock runner 抛异常，验证降级文档 |
| `generate_proposal()` 成功/失败 | 同上 |
| 空 session.rounds | 验证无索引错误 |

### `test_cli_detector.py` — 优先级 ★★

| 场景 | 测试方法 |
|------|---------|
| CLI 已安装 | mock `shutil.which` + subprocess 输出 |
| CLI 未安装 | mock `shutil.which` 返回 None |
| 版本号解析 | mock subprocess 输出含版本字符串 |
| `detect_all()` | mock `detect_one` 结果 |
| `save_detected_clis_to_config()` | 真实临时 YAML 文件 |

### `test_orchestrator.py` — 优先级 ★★

| 场景 | 测试方法 |
|------|---------|
| `run_session()` 正常流程 | mock `invoke_with_retry` 所有阶段 |
| 提前共识退出 | mock consensus 返回 "full" |
| `_run_round_parallel()` | mock ThreadPoolExecutor |
| `_run_round_sequential()` | 顺序执行，验证历史递增 |
| 共识检测异常 | mock runner 抛异常，验证返回 CR.unknown() |

### `test_discussion_orchestrator.py` — 优先级 ★

| 场景 | 测试方法 |
|------|---------|
| `run_independent_phase()` 并行 | mock `invoke_with_retry` |
| `run_independent_phase()` 流式 | mock `invoke_with_retry_streaming` |
| `select_moderator()` | mock `console.input` |
| `run_discussion_phase()` | mock 所有 runner 调用，mock 用户输入 |
| `_run_moderator_opening()` | mock runner 或 streaming_runner |
| `_run_discussion_round()` | mock 非主持人 agents |
| `_check_consensus()` | mock runner.invoke |
| `_parse_convergence_signal()` | "has signal" vs "no signal" |
| `run_synthesis_phase()` | mock runner.invoke，验证历史构建 |
| `_extract_summary()` | 有/无 "### 整体评价"，max_len 截断 |

### `test_context.py` — 优先级 ★

| 函数 | 测试用例 |
|------|---------|
| `estimate_tokens()` | 已知中英文字符串，验证估算 |
| `compress_history()` | mock runner.invoke，验证摘要路径 |

---

## 集成测试详情

使用 `click.testing.CliRunner` 测试所有 CLI 命令。

| 命令 | 验证内容 |
|------|---------|
| `new` | 创建 meeting，调用 orchestrator，保存文件 |
| `continue` | 加载 meeting，追加 session，保存 |
| `finalize` | 加载，设置 final_proposal，设置 status |
| `list` | 显示表格（mock 文件系统含已知 meetings） |
| `show` | 显示 meeting/discussion 详情 |
| `test-round` | 单 agent 调用 |
| `discuss` | 完整 3-phase 流程（mock runner，无真实 AI 调用） |
| `agent detect` | 检测 CLI，显示表格 |
| `agent list` | 读取配置，显示表格 |
| `agent add/remove` | 修改 YAML，验证变更 |

**重要**：`discuss`、`new`、`continue` 的集成测试中，`AgentRunner` 应 mock 掉，这样可以在没有真实 AI CLI 的环境下运行完整流程。

---

## 覆盖率目标

| 模块 | 目标覆盖率 |
|------|----------|
| `config.py` | 95% |
| `meeting.py` | 95% |
| `prompt_builder.py` | 95% |
| `context.py` | 90% |
| `consensus.py` | 90% |
| `agent_runner.py` | 90% |
| `streaming_runner.py` | 85% |
| `summarizer.py` | 85% |
| `cli_detector.py` | 85% |
| `orchestrator.py` | 80% |
| `discussion_orchestrator.py` | 75% |
| `council.py` | 70% |

**整体目标：80%**

---

## 关键边界条件

### `agent_runner`

- [ ] 未知 agent ID → 应返回错误响应，不抛异常
- [ ] timeout → 进程被 kill，返回超时占位响应
- [ ] subprocess 返回非 0 → 返回错误响应
- [ ] stdout 为空 → 视为失败，触发重试
- [ ] `{output_file}` 文件不存在 → 读不到内容时错误处理
- [ ] `{output_file}` 读取权限错误 → 异常被捕获
- [ ] 通用异常 → 异常被捕获，返回错误响应
- [ ] 重试 2 次后仍失败 → 返回最终错误响应，不中断会议

### `prompt_builder`

- [ ] round_num==1 时无 history → 早期返回"独立思考"提示
- [ ] history 超过 800 字符 → 截断
- [ ] moderator_opening 超过 500 字符 → 截断
- [ ] 无 prior_proposal → 不拼接
- [ ] 无 user_feedback → 不拼接

### `meeting`

- [ ] 完整字段 round-trip → save → load → 所有字段一致
- [ ] 损坏 JSON → load 抛 JSONDecodeError
- [ ] 缺失 proposal.md → load 后 proposal 字段为空字符串
- [ ] 缺失 minutes.md → load 后 minutes 字段为空字符串
- [ ] Discussion 所有 Phase 类型 → independent / discussion / synthesis

### `consensus`

- [ ] 有效 JSON（所有字段）→ 正确解析
- [ ] 有效 JSON（缺少字段）→ 用默认值填充
- [ ] 无效 JSON → 返回 `unknown()`
- [ ] runner.invoke 失败 → 返回 `unknown()`

### `orchestrator`

- [ ] round 1 并行执行 → 所有 agent 同时调用
- [ ] round >= 2 顺序执行 → 按 agent 列表顺序
- [ ] 共识达到 "full" → 在 max_rounds 之前提前结束
- [ ] 单个 agent 调用失败 → 不影响其他 agent，继续执行

---

## 运行测试

```bash
# 安装测试依赖
pip install pytest pytest-cov

# 运行所有测试
pytest tests/ -v

# 带覆盖率
pytest tests/ --cov=lib --cov-report=term-missing

# 只跑单元测试
pytest tests/unit/ -v

# 只跑集成测试
pytest tests/integration/ -v

# 只测特定模块
pytest tests/unit/test_meeting.py -v
```
