# 需求文档生成

你是本次讨论的主持人 {agent_name}。

## 原始需求

{user_idea}

## 完整讨论记录

{full_discussion_history}

## 用户在讨论中的所有回答与补充

{all_user_feedback}

## 你的任务

综合以上信息，输出一份只包含需求本体的 Markdown 文档。
严格遵守下面的格式，不要添加任何前言、解释、实现方案、技术栈、执行配置或运行时字段。
不要包含 `Constraints`、`Status`、`workspace_dir`、`output_dir`、`language`、`platform`、`harness`、`execution_mode` 之类的执行信息。
如果某些信息仍不明确，把它们写入 `Open Questions`，不要凭空补充。

---

# Requirement: {topic_summary}

## Goal

一句话说明要做什么，必须可以直接被开发执行。

## Scope

- In scope:
  - ...
- Out of scope:
  - ...

## Inputs

- ...

## Outputs

- ...

## Acceptance Criteria

- ...
- ...

## Open Questions

讨论结束时仍未解决的问题；如果没有，写 `无`。
- ...

---

## 重要原则

- 只输出上面的 6 个部分，不要添加其他章节
- 所有内容必须来自讨论记录或用户回答，不要凭空编造
- 字段必须具体可执行；如果某项无法收敛，放到 `Open Questions`
- 第一行必须是 `# Requirement: ...`
