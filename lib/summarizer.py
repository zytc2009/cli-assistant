"""Meeting minutes and proposal generation."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .agent_runner import AgentRunner
    from .meeting import Session


def generate_minutes(
    session: "Session",
    topic: str,
    runner: "AgentRunner",
    summarizer_agent: str,
    minutes_prompt_template: str,
) -> str:
    # Build full discussion text
    lines = []
    for r in session.rounds:
        lines.append(f"\n### 第 {r.round_num} 轮\n")
        for agent_name, content in r.responses.items():
            lines.append(f"**{agent_name} 的发言：**\n{content}\n")
        lines.append("---")
    full_discussion = "\n".join(lines)

    agent_list = "、".join(session.agents)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt = minutes_prompt_template.format(
        topic=topic,
        session_type=session.session_type,
        timestamp=timestamp,
        agent_list=agent_list,
        rounds_used=len(session.rounds),
        max_rounds=len(session.rounds),  # actual rounds used
        full_discussion=full_discussion,
    )

    response = runner.invoke(summarizer_agent, prompt)
    if response.success:
        return response.content
    return f"# 会议纪要\n\n*生成失败：{response.error}*\n\n{full_discussion}"


def generate_proposal(
    session: "Session",
    topic: str,
    runner: "AgentRunner",
    summarizer_agent: str,
    proposal_prompt_template: str,
    prior_proposal: str = "",
) -> str:
    # Build context from minutes + last round
    context_parts = []
    if session.minutes:
        context_parts.append(f"## 会议纪要\n\n{session.minutes}")
    if prior_proposal:
        context_parts.append(f"## 上一阶段方案\n\n{prior_proposal}")

    last_round = session.rounds[-1] if session.rounds else None
    if last_round:
        lines = ["\n## 最后一轮各方发言\n"]
        for agent_name, content in last_round.responses.items():
            lines.append(f"**{agent_name}:**\n{content}\n")
        context_parts.append("\n".join(lines))

    context = "\n\n".join(context_parts)
    date = datetime.now().strftime("%Y-%m-%d")

    status_map = {
        "brainstorm": "草案",
        "review": "修订",
        "decision": "定稿",
    }
    status = status_map.get(session.session_type, "草案")

    prompt = proposal_prompt_template.format(
        topic=topic,
        session_type=session.session_type,
        session_number=session.session_index,
        date=date,
        context=context,
        status=status,
    )

    response = runner.invoke(summarizer_agent, prompt)
    if response.success:
        return response.content
    return f"# 方案：{topic}\n\n*生成失败：{response.error}*\n"
