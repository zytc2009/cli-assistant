"""Tests for lib/summarizer.py."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lib.agent_runner import AgentResponse
from lib.meeting import Round, Session
from lib.summarizer import generate_minutes, generate_proposal


@pytest.fixture
def session_with_rounds() -> Session:
    return Session(
        session_index=1,
        session_type="brainstorm",
        agents=["claude-sonnet", "codex-o4-mini"],
        rounds=[
            Round(
                round_num=1,
                responses={"claude-sonnet": "Claude的想法", "codex-o4-mini": "Codex的想法"},
            ),
            Round(
                round_num=2,
                responses={"claude-sonnet": "Claude的补充", "codex-o4-mini": "Codex的补充"},
            ),
        ],
        proposal="",
        minutes="",
        consensus_level="partial",
        started_at="2026-04-01T10:00:00",
        finished_at="2026-04-01T10:30:00",
    )


class TestGenerateMinutes:
    def test_success_returns_content(self, session_with_rounds: Session):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="# 会议纪要\n\n内容",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        result = generate_minutes(
            session_with_rounds,
            "测试议题",
            runner,
            "claude-sonnet",
            "生成纪要: {full_discussion}",
        )

        assert "内容" in result
        runner.invoke.assert_called_once()

    def test_failure_returns_fallback_with_discussion(
        self, session_with_rounds: Session
    ):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="",
            success=False,
            error="timeout",
            duration_seconds=1.0,
        )

        result = generate_minutes(
            session_with_rounds,
            "测试议题",
            runner,
            "claude-sonnet",
            "生成纪要: {full_discussion}",
        )

        assert "生成失败" in result
        assert "timeout" in result
        assert "Claude的想法" in result  # discussion included

    def test_prompt_includes_all_rounds(
        self, session_with_rounds: Session
    ):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        generate_minutes(
            session_with_rounds,
            "测试议题",
            runner,
            "claude-sonnet",
            "纪要: {full_discussion}",
        )

        prompt = runner.invoke.call_args[0][1]
        assert "第 1 轮" in prompt
        assert "第 2 轮" in prompt
        assert "Claude的想法" in prompt
        assert "Codex的想法" in prompt

    def test_empty_rounds_no_index_error(self):
        runner = MagicMock()
        session = Session(
            session_index=1,
            session_type="brainstorm",
            agents=["claude-sonnet"],
            rounds=[],
        )
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="# 纪要",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        # Should not raise IndexError
        result = generate_minutes(
            session, "测试", runner, "claude-sonnet", "纪要: {full_discussion}"
        )
        assert "纪要" in result


class TestGenerateProposal:
    def test_success_returns_content(self, session_with_rounds: Session):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="# 方案\n\n内容",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        result = generate_proposal(
            session_with_rounds,
            "测试议题",
            runner,
            "claude-sonnet",
            "生成方案: {context}",
        )

        assert "内容" in result

    def test_failure_returns_fallback(self, session_with_rounds: Session):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="",
            success=False,
            error="network error",
            duration_seconds=1.0,
        )

        result = generate_proposal(
            session_with_rounds,
            "测试议题",
            runner,
            "claude-sonnet",
            "生成方案: {context}",
        )

        assert "生成失败" in result
        assert "network error" in result

    def test_prior_proposal_in_context(
        self, session_with_rounds: Session
    ):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        generate_proposal(
            session_with_rounds,
            "测试议题",
            runner,
            "claude-sonnet",
            "方案: {context}",
            prior_proposal="# 上一方案内容",
        )

        prompt = runner.invoke.call_args[0][1]
        assert "上一方案内容" in prompt

    def test_minutes_in_context(self, session_with_rounds: Session):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )
        session_with_rounds.minutes = "这是会议纪要内容"

        generate_proposal(
            session_with_rounds,
            "测试议题",
            runner,
            "claude-sonnet",
            "方案: {context}",
        )

        prompt = runner.invoke.call_args[0][1]
        assert "会议纪要内容" in prompt

    def test_status_mapped_correctly(self, session_with_rounds: Session):
        runner = MagicMock()
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        generate_proposal(
            session_with_rounds,
            "测试议题",
            runner,
            "claude-sonnet",
            "方案: {context} 状态: {status}",
        )

        prompt = runner.invoke.call_args[0][1]
        assert "草案" in prompt

    def test_empty_rounds_handled(self):
        runner = MagicMock()
        session = Session(
            session_index=1,
            session_type="brainstorm",
            agents=["claude-sonnet"],
            rounds=[],
        )
        runner.invoke.return_value = AgentResponse(
            agent="claude-sonnet",
            content="# 方案",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        # Should not raise IndexError accessing rounds[-1]
        result = generate_proposal(
            session, "测试", runner, "claude-sonnet", "方案: {context}"
        )
        assert "方案" in result
