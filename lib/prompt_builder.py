"""Prompt assembly for meeting rounds."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .config import AgentConfig, MeetingTemplate


def build_independent_prompt(
    template_content: str,
    agent: AgentConfig,
    user_idea: str,
) -> str:
    """Build prompt for independent opinion phase (Phase 1)."""
    return template_content.format(
        agent_name=agent.name,
        agent_strengths=agent.strengths,
        user_idea=user_idea,
    )


def build_moderator_opening_prompt(
    template_content: str,
    agent: AgentConfig,
    user_idea: str,
    round_num: int,
    max_rounds: int,
    history: List[Dict],
    user_feedback: str = "",
) -> str:
    """Build prompt for moderator opening in discussion phase (Phase 2)."""
    # Build history section
    if history:
        lines = ["## 讨论历史\n"]
        for round_data in history:
            rnum = round_data["round"]
            lines.append(f"### 第 {rnum} 轮\n")
            for agent_name, response in round_data["responses"].items():
                lines.append(f"**{agent_name} 的发言：**\n{response[:500]}...\n")
            lines.append("---\n")
        history_section = "\n".join(lines)
    else:
        history_section = "## 讨论历史\n\n（这是第一轮讨论，以下是各 AI 的初始观点）"

    # Build user feedback section
    if user_feedback:
        user_feedback_section = f"## 用户补充意见\n\n{user_feedback}"
    else:
        user_feedback_section = "## 用户补充意见\n\n（无额外意见）"

    return template_content.format(
        agent_name=agent.name,
        user_idea=user_idea,
        history_section=history_section,
        user_feedback_section=user_feedback_section,
        round_num=round_num,
        max_rounds=max_rounds,
    )


def build_discussion_prompt(
    template_content: str,
    agent: AgentConfig,
    user_idea: str,
    history: List[Dict],
    moderator_name: str,
    moderator_opening: str,
) -> str:
    """Build prompt for participant response in discussion phase (Phase 2)."""
    # Build history section
    lines = ["## 讨论历史\n"]
    for round_data in history:
        rnum = round_data["round"]
        lines.append(f"### 第 {rnum} 轮\n")
        for agent_name, response in round_data["responses"].items():
            lines.append(f"**{agent_name} 的发言：**\n{response[:800]}...\n")
        lines.append("---\n")

    history_section = "\n".join(lines)

    return template_content.format(
        agent_name=agent.name,
        agent_strengths=agent.strengths,
        user_idea=user_idea,
        history_section=history_section,
        moderator_name=moderator_name,
        moderator_opening=moderator_opening,
    )


def build_synthesis_prompt(
    template_content: str,
    agent: AgentConfig,
    user_idea: str,
    full_history: List[Dict],
    all_user_feedbacks: List[str],
) -> str:
    """Build prompt for moderator synthesis phase (Phase 3)."""
    # Build full discussion history (truncate each response to 800 chars to control prompt size)
    _TRUNCATE_LEN = 800
    lines = []
    for round_data in full_history:
        rnum = round_data["round"]
        phase = round_data.get("phase", "未知阶段")
        lines.append(f"\n## [{phase}] 第 {rnum} 轮\n")
        for agent_name, response in round_data["responses"].items():
            truncated = response[:_TRUNCATE_LEN] + "..." if len(response) > _TRUNCATE_LEN else response
            lines.append(f"\n**{agent_name}：**\n{truncated}\n")
        lines.append("\n---\n")

    full_discussion_history = "\n".join(lines)

    # Build user feedbacks
    if all_user_feedbacks:
        feedbacks_text = "\n\n".join(
            f"- {fb}" for fb in all_user_feedbacks
        )
    else:
        feedbacks_text = "（无补充意见）"

    return template_content.format(
        agent_name=agent.name,
        user_idea=user_idea,
        full_discussion_history=full_discussion_history,
        all_user_feedback=feedbacks_text,
    )


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
