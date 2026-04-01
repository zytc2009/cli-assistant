"""Consensus detection via LLM analysis."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_runner import AgentRunner


@dataclass
class ConsensusResult:
    consensus_reached: bool
    consensus_level: str          # full | partial | none
    agreed_points: List[str]
    disputed_points: List[str]
    recommendation: str

    @classmethod
    def unknown(cls) -> "ConsensusResult":
        return cls(
            consensus_reached=False,
            consensus_level="none",
            agreed_points=[],
            disputed_points=[],
            recommendation="继续讨论",
        )


def detect_consensus(
    latest_round_responses: dict[str, str],
    runner: "AgentRunner",
    detector_agent: str,
    detector_prompt_template: str,
) -> ConsensusResult:
    lines = []
    for agent_name, content in latest_round_responses.items():
        lines.append(f"**{agent_name}:**\n{content}")
    discussion_text = "\n\n".join(lines)

    prompt = detector_prompt_template.replace("{latest_round}", discussion_text)
    response = runner.invoke(detector_agent, prompt)

    if not response.success:
        return ConsensusResult.unknown()

    # Extract JSON from response
    content = response.content.strip()
    # Find JSON block
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        return ConsensusResult.unknown()

    try:
        data = json.loads(match.group())
        return ConsensusResult(
            consensus_reached=bool(data.get("consensus_reached", False)),
            consensus_level=data.get("consensus_level", "none"),
            agreed_points=data.get("agreed_points", []),
            disputed_points=data.get("disputed_points", []),
            recommendation=data.get("recommendation", "继续讨论"),
        )
    except (json.JSONDecodeError, KeyError):
        return ConsensusResult.unknown()
