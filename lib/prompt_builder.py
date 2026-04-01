"""Prompt assembly for meeting rounds."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .config import AgentConfig, MeetingTemplate


def build_history_section(
    history: Optional[List[Dict[str, str]]],
    round_num: int,
) -> str:
    if round_num == 1 or not history:
        return "（第一轮，请独立思考，不参考他人观点）"

    lines = []
    for round_data in history:
        rnum = round_data["round"]
        lines.append(f"### 第 {rnum} 轮\n")
        for agent_name, response in round_data["responses"].items():
            lines.append(f"**{agent_name} 的发言：**\n{response}\n")
        lines.append("---\n")

    lines.append("\n请基于以上讨论，按本轮规则继续发言。")
    return "\n".join(lines)


def build_prompt(
    template_content: str,
    agent: AgentConfig,
    topic: str,
    session_type: str,
    session_description: str,
    round_num: int,
    max_rounds: int,
    round_rule: str,
    agent_list: List[str],
    history: Optional[List[Dict[str, str]]] = None,
    prior_proposal: Optional[str] = None,
    user_feedback: Optional[str] = None,
) -> str:
    history_section = build_history_section(history, round_num)

    # Prepend prior session proposal if available
    if prior_proposal and round_num == 1:
        history_section = (
            f"## 上一阶段方案\n\n{prior_proposal}\n\n"
            + (f"## 用户补充意见\n\n{user_feedback}\n\n" if user_feedback else "")
            + history_section
        )

    prompt = template_content.format(
        topic=topic,
        session_type=session_type,
        session_description=session_description,
        round=round_num,
        max_rounds=max_rounds,
        agent_name=agent.name,
        agent_strengths=agent.strengths,
        round_rule=round_rule,
        agent_list="、".join(agent_list),
        history_section=history_section,
    )
    return prompt
