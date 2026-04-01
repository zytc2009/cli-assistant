"""Context window management and compression."""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from .agent_runner import AgentRunner

# Rough token estimation: Chinese ~1.5 token/char, English ~0.75 token/word
_CHINESE_RATIO = 1.5
_ENGLISH_RATIO = 0.75


def estimate_tokens(text: str) -> int:
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * _CHINESE_RATIO + other_chars * _ENGLISH_RATIO)


def compress_history(
    rounds: List[Dict],
    runner: "AgentRunner",
    summarizer_agent: str,
    summarizer_prompt_template: str,
    max_chars: int = 3000,
    keep_recent: int = 2,
) -> List[Dict]:
    """Keep the most recent `keep_recent` rounds verbatim; summarize the rest."""
    if len(rounds) <= keep_recent:
        return rounds

    to_summarize = rounds[:-keep_recent]
    recent = rounds[-keep_recent:]

    # Build text to summarize
    lines = []
    for rd in to_summarize:
        lines.append(f"### 第 {rd['round']} 轮")
        for agent_name, response in rd["responses"].items():
            lines.append(f"**{agent_name}:**\n{response}")
        lines.append("---")
    raw_text = "\n".join(lines)

    # Only compress if actually long
    if len(raw_text) < max_chars:
        return rounds

    prompt = summarizer_prompt_template.replace("{raw_discussion}", raw_text)
    response = runner.invoke(summarizer_agent, prompt)

    summary_round = {
        "round": f"1-{to_summarize[-1]['round']} (摘要)",
        "responses": {"摘要": response.content},
    }
    return [summary_round] + recent
