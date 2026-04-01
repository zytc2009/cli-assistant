"""Tests for lib/consensus.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lib.agent_runner import AgentResponse
from lib.consensus import ConsensusResult, detect_consensus


class TestConsensusResultUnknown:
    def test_unknown_returns_defaults(self):
        result = ConsensusResult.unknown()

        assert result.consensus_reached is False
        assert result.consensus_level == "none"
        assert result.agreed_points == []
        assert result.disputed_points == []
        assert result.recommendation == "继续讨论"


class TestDetectConsensus:
    def test_success_parses_json(self):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content='{"consensus_reached": true, "consensus_level": "full", "agreed_points": ["A", "B"], "disputed_points": ["C"], "recommendation": "结束"}',
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        result = detect_consensus(
            {"A": "resp1", "B": "resp2"},
            runner,
            "claude-sonnet",
            "分析: {latest_round}",
        )

        assert result.consensus_reached is True
        assert result.consensus_level == "full"
        assert result.agreed_points == ["A", "B"]
        assert result.disputed_points == ["C"]

    def test_invoke_failure_returns_unknown(self):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="",
            success=False,
            error="timeout",
            duration_seconds=1.0,
        )

        result = detect_consensus(
            {"A": "resp1"}, runner, "claude-sonnet", "prompt"
        )

        assert result == ConsensusResult.unknown()

    def test_invalid_json_returns_unknown(self):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="这不是 JSON",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        result = detect_consensus(
            {"A": "resp1"}, runner, "claude-sonnet", "prompt"
        )

        assert result == ConsensusResult.unknown()

    def test_json_without_matching_braces_returns_unknown(self):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="{没有结束括号",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        result = detect_consensus(
            {"A": "resp1"}, runner, "claude-sonnet", "prompt"
        )

        assert result == ConsensusResult.unknown()

    def test_partial_json_uses_defaults(self):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content='{"consensus_reached": true}',
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        result = detect_consensus(
            {"A": "resp1"}, runner, "claude-sonnet", "prompt"
        )

        # Should use defaults for missing fields
        assert result.consensus_level == "none"
        assert result.agreed_points == []

    def test_prompt_contains_discussion_text(self):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content='{"consensus_reached": false, "consensus_level": "none", "agreed_points": [], "disputed_points": [], "recommendation": "继续"}',
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        detect_consensus(
            {"Claude": "回复内容", "Codex": "另一个回复"},
            runner,
            "claude-sonnet",
            "分析: {latest_round}",
        )

        call_args = runner.invoke.call_args[0]
        prompt = call_args[1]
        assert "Claude" in prompt
        assert "回复内容" in prompt
