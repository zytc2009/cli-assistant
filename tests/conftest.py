"""Shared fixtures for ai-council tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from lib.agent_runner import AgentResponse, AgentRunner
from lib.config import AgentConfig, Config
from lib.meeting import (
    Discussion,
    DiscussionPhase,
    DiscussionRound,
    Meeting,
    Round,
    Session,
)


# ── Config Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Create a temp config directory with minimal YAML files."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    # agents.yaml
    (cfg_dir / "agents.yaml").write_text("""
agents:
  claude-sonnet:
    name: "Claude Sonnet"
    cli: claude
    model: claude-sonnet-4-6
    command: 'claude -p "{prompt_file}" --output-format text'
    prompt_method: file
    max_tokens: 4000
    timeout: 120
    strengths: "Code implementation"
    cost_tier: medium
    output_method: stdout

  codex-o4-mini:
    name: "Codex o4-mini"
    cli: codex
    model: o4-mini
    command: 'codex exec --skip-git-repo-check --full-auto --ephemeral -o {output_file} "$(cat {prompt_file})"'
    prompt_method: file
    max_tokens: 4000
    timeout: 90
    strengths: "Engineering"
    cost_tier: low
    output_method: file
""", encoding="utf-8")

    # meeting_templates.yaml
    (cfg_dir / "meeting_templates.yaml").write_text("""
templates:
  brainstorm:
    description: "Brainstorm"
    max_rounds: 3
    speaking_order: round_robin
    round_rules:
      1: "Divergent thinking"
      2: "Build on others"
      3: "Converge"
    output: proposal
  review:
    description: "Review"
    max_rounds: 2
    speaking_order: round_robin
    round_rules:
      1: "Deep review"
      2: "Confirm direction"
    output: proposal
""", encoding="utf-8")

    # model_strategies.yaml (includes presets)
    (cfg_dir / "model_strategies.yaml").write_text("""
model_strategies:
  balanced:
    brainstorm: [claude-sonnet, codex-o4-mini]
    review: [claude-sonnet]
    decision: [claude-sonnet, codex-o4-mini]

  budget:
    brainstorm: [codex-o4-mini]
    review: [codex-o4-mini]
    decision: [claude-sonnet]

presets:
  tech_selection:
    description: "Tech selection"
    sessions: [brainstorm, review, decision]
    default_strategy: balanced
""", encoding="utf-8")

    # Create prompts directory with templates
    prompts_dir = cfg_dir / "prompts"
    prompts_dir.mkdir()

    (prompts_dir / "base_system.md").write_text(
        "Topic: {topic}\nType: {session_type}\nRound: {round}/{max_rounds}",
        encoding="utf-8",
    )
    (prompts_dir / "independent_opinion.md").write_text(
        "You are {agent_name}, skilled in {agent_strengths}.\nUser idea: {user_idea}",
        encoding="utf-8",
    )
    (prompts_dir / "moderator_opening.md").write_text(
        "You are moderator {agent_name}.\nHistory: {history_section}\nFeedback: {user_feedback_section}",
        encoding="utf-8",
    )
    (prompts_dir / "discussion_response.md").write_text(
        "You are {agent_name}.\nModerator opening: {moderator_opening}\nHistory: {history_section}",
        encoding="utf-8",
    )
    (prompts_dir / "moderator_synthesis.md").write_text(
        "You are moderator {agent_name}.\nDiscussion: {full_discussion_history}\nFeedback: {all_user_feedback}",
        encoding="utf-8",
    )
    (prompts_dir / "minutes_generator.md").write_text(
        "Generate minutes for: {topic}\n"
        "Type: {session_type}\n"
        "Agents: {agent_list}\n"
        "Time: {timestamp}\n"
        "Rounds: {rounds_used}/{max_rounds}\n"
        "Discussion:\n{full_discussion}",
        encoding="utf-8",
    )
    (prompts_dir / "proposal_generator.md").write_text(
        "Generate proposal for: {topic}\n"
        "Type: {session_type}\n"
        "Session: #{session_number}\n"
        "Date: {date}\n"
        "Status: {status}\n\n"
        "{context}",
        encoding="utf-8",
    )
    (prompts_dir / "consensus_detector.md").write_text(
        "Detect consensus based on: {latest_round}",
        encoding="utf-8",
    )
    (prompts_dir / "summarizer.md").write_text(
        "Summarize history: {history}",
        encoding="utf-8",
    )

    return cfg_dir


@pytest.fixture
def config(config_dir: Path) -> Config:
    """Real Config instance backed by temp YAML files."""
    return Config(config_dir=config_dir)


# ── Meeting Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def sample_round() -> Round:
    return Round(
        round_num=1,
        responses={"claude-sonnet": "Claude的想法", "codex-o4-mini": "Codex的想法"},
    )


@pytest.fixture
def sample_session(sample_round: Round) -> Session:
    return Session(
        session_index=1,
        session_type="brainstorm",
        agents=["claude-sonnet", "codex-o4-mini"],
        rounds=[sample_round],
        proposal="# 方案\n\n这是方案内容",
        minutes="# 纪要\n\n这是纪要内容",
        consensus_level="partial",
        started_at="2026-04-01T10:00:00",
        finished_at="2026-04-01T10:30:00",
    )


@pytest.fixture
def sample_meeting(sample_session: Session) -> Meeting:
    return Meeting(
        topic_id="test_topic_abc123",
        topic="测试议题",
        created_at="2026-04-01T10:00:00",
        sessions=[sample_session],
        status="in_progress",
        final_proposal="",
    )


# ── Discussion Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def sample_discussion_round() -> DiscussionRound:
    return DiscussionRound(
        round_num=1,
        moderator_opening="这是主持人开场",
        responses={"claude-sonnet": "Claude回应", "codex-o4-mini": "Codex回应"},
    )


@pytest.fixture
def sample_discussion_phase(sample_discussion_round: DiscussionRound) -> DiscussionPhase:
    return DiscussionPhase(
        phase_type="discussion",
        phase_index=2,
        rounds=[sample_discussion_round],
    )


@pytest.fixture
def sample_discussion(sample_discussion_phase: DiscussionPhase) -> Discussion:
    return Discussion(
        topic_id="discuss_test_xyz789",
        user_idea="这是一个测试想法",
        created_at="2026-04-01T10:00:00",
        agents=["claude-sonnet", "codex-o4-mini"],
        moderator="claude-sonnet",
        status="discussing",
        final_output="",
        user_feedbacks=["用户反馈1", "用户反馈2"],
        phases=[
            DiscussionPhase(
                phase_type="independent",
                phase_index=1,
                rounds=[
                    DiscussionRound(
                        round_num=1,
                        responses={"claude-sonnet": "独立观点1", "codex-o4-mini": "独立观点2"},
                    )
                ],
            ),
            sample_discussion_phase,
        ],
    )


# ── Mock AgentRunner ──────────────────────────────────────────────────────────

@pytest.fixture
def mock_runner() -> MagicMock:
    """A mock AgentRunner that returns success responses."""
    runner = MagicMock(spec=AgentRunner)
    runner.invoke.return_value = AgentResponse(
        agent="claude-sonnet",
        content="Mocked response content",
        success=True,
        error=None,
        duration_seconds=1.0,
    )
    runner.invoke_with_retry.return_value = AgentResponse(
        agent="claude-sonnet",
        content="Mocked response content",
        success=True,
        error=None,
        duration_seconds=1.0,
    )
    return runner


# ── Prompt Template Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    """Create a temp prompts directory with minimal templates."""
    p_dir = tmp_path / "prompts"
    p_dir.mkdir()

    (p_dir / "base_system.md").write_text(
        "Topic: {topic}\nType: {session_type}\nRound: {round}/{max_rounds}",
        encoding="utf-8",
    )
    (p_dir / "independent_opinion.md").write_text(
        "You are {agent_name}, skilled in {agent_strengths}.\nUser idea: {user_idea}",
        encoding="utf-8",
    )
    (p_dir / "moderator_opening.md").write_text(
        "You are moderator {agent_name}.\nHistory: {history_section}\nFeedback: {user_feedback}",
        encoding="utf-8",
    )
    (p_dir / "discussion_response.md").write_text(
        "You are {agent_name}.\nModerator opening: {moderator_opening}\nHistory: {history_section}",
        encoding="utf-8",
    )
    (p_dir / "moderator_synthesis.md").write_text(
        "You are moderator {agent_name}.\nDiscussion: {full_discussion}\nFeedback: {all_user_feedback}",
        encoding="utf-8",
    )
    (p_dir / "minutes_generator.md").write_text(
        "Generate minutes for: {topic}\nResponses: {responses}",
        encoding="utf-8",
    )
    (p_dir / "proposal_generator.md").write_text(
        "Generate proposal for: {topic}\nMinutes: {minutes}\nLast round: {last_round}",
        encoding="utf-8",
    )
    (p_dir / "consensus_detector.md").write_text(
        "Detect consensus: {responses}",
        encoding="utf-8",
    )
    (p_dir / "summarizer.md").write_text(
        "Summarize history: {history}",
        encoding="utf-8",
    )

    return p_dir
