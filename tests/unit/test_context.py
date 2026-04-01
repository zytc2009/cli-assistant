"""Tests for lib/context.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lib.agent_runner import AgentResponse
from lib.context import compress_history, estimate_tokens


class TestEstimateTokens:
    def test_chinese_tokens(self):
        # 10 Chinese chars * 1.5 = 15 tokens
        result = estimate_tokens("一二三四五六七八九十")
        assert result == 15

    def test_english_tokens(self):
        # 10 English chars * 0.75 = 7.5 → int(7.5) = 7
        result = estimate_tokens("helloworld")
        assert result == 7

    def test_mixed_tokens(self):
        chinese = "一二三"  # 3 chars
        english = "abc"     # 3 chars
        result = estimate_tokens(chinese + english)
        # 3 * 1.5 + 3 * 0.75 = 4.5 + 2.25 = 6.75 → int(6.75) = 6
        assert result == 6

    def test_empty_string(self):
        assert estimate_tokens("") == 0


class TestCompressHistory:
    def test_fewer_rounds_than_keep_returns_unchanged(self):
        rounds = [{"round": 1, "responses": {"A": "x"}}]
        runner = MagicMock()
        result = compress_history(rounds, runner, "agent", "prompt: {raw_discussion}", keep_recent=2)
        assert result == rounds

    def test_summarizes_old_rounds(self):
        # 3 rounds with keep_recent=2 and max_chars=500 should trigger summarization
        rounds = [
            {"round": 1, "responses": {"A": "a" * 500}},
            {"round": 2, "responses": {"B": "b" * 500}},
            {"round": 3, "responses": {"C": "c"}},
        ]
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="s",
            content="摘要内容",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        result = compress_history(
            rounds, runner, "agent", "prompt: {raw_discussion}", keep_recent=2, max_chars=500
        )

        # 3 rounds: to_summarize = [round1], recent = [round2, round3]
        # After compression: [summary] + recent = 3 items
        assert len(result) == 3
        assert "摘要" in result[0]["round"]
        assert result[1] == rounds[1]
        assert result[2] == rounds[2]

    def test_short_history_not_compressed(self):
        # History shorter than max_chars should not be compressed
        rounds = [
            {"round": 1, "responses": {"A": "short"}},
            {"round": 2, "responses": {"B": "short"}},
        ]
        runner = MagicMock()
        result = compress_history(
            rounds, runner, "agent", "prompt: {raw_discussion}",
            max_chars=5000, keep_recent=2
        )

        # Should return unchanged since total is under max_chars
        runner.invoke.assert_not_called()

    def test_uses_summarizer_agent(self):
        # Use max_chars small enough to trigger compression
        rounds = [
            {"round": 1, "responses": {"A": "x" * 2000}},
            {"round": 2, "responses": {"B": "y" * 2000}},
            {"round": 3, "responses": {"C": "z"}},
        ]
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="summarizer",
            content="摘要",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        compress_history(rounds, runner, "summarizer", "prompt: {raw_discussion}", max_chars=1000, keep_recent=2)

        runner.invoke.assert_called_once()
        call_args = runner.invoke.call_args
        assert call_args[0][0] == "summarizer"
