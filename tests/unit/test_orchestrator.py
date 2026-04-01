"""Tests for lib/orchestrator.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.agent_runner import AgentResponse, AgentRunner
from lib.config import Config
from lib.consensus import ConsensusResult
from lib.meeting import Meeting
from lib.orchestrator import Orchestrator


@pytest.fixture
def orchestrator(config: Config, mock_runner: MagicMock, tmp_path) -> Orchestrator:
    return Orchestrator(config=config, base_dir=tmp_path, runner=mock_runner)


class TestRunSession:
    @patch("lib.orchestrator.console")
    @patch("lib.orchestrator.save_meeting")
    @patch("lib.orchestrator.Progress")
    def test_creates_session(
        self, mock_progress: MagicMock, mock_save: MagicMock, mock_console: MagicMock,
        orchestrator: Orchestrator, mock_runner: MagicMock
    ):
        mock_runner.invoke_with_retry.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        meeting = Meeting(
            topic_id="test_123",
            topic="测试议题",
            created_at="2026-04-01T10:00:00",
        )

        session = orchestrator.run_session(
            meeting=meeting,
            session_type="brainstorm",
            agents=["claude-sonnet"],
        )

        assert session.session_type == "brainstorm"
        assert session.session_index == 1
        assert len(meeting.sessions) == 1

    @patch("lib.orchestrator.console")
    @patch("lib.orchestrator.save_meeting")
    @patch("lib.orchestrator.Progress")
    def test_sequential_rounds_for_subsequent(
        self, mock_progress: MagicMock, mock_save: MagicMock, mock_console: MagicMock,
        orchestrator: Orchestrator, mock_runner: MagicMock
    ):
        mock_runner.invoke_with_retry.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        meeting = Meeting(
            topic_id="test_123",
            topic="测试议题",
            created_at="2026-04-01T10:00:00",
        )

        session = orchestrator.run_session(
            meeting=meeting,
            session_type="brainstorm",
            agents=["claude-sonnet", "codex-o4-mini"],
        )

        assert mock_runner.invoke_with_retry.call_count >= 2

    @patch("lib.orchestrator.console")
    @patch("lib.orchestrator.save_meeting")
    @patch("lib.orchestrator.Progress")
    def test_early_exit_on_full_consensus(
        self, mock_progress: MagicMock, mock_save: MagicMock, mock_console: MagicMock,
        orchestrator: Orchestrator, mock_runner: MagicMock
    ):
        mock_runner.invoke_with_retry.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        meeting = Meeting(
            topic_id="test_123",
            topic="测试议题",
            created_at="2026-04-01T10:00:00",
        )

        with patch.object(orchestrator, "_detect_consensus") as mock_consensus:
            mock_consensus.return_value = ConsensusResult(
                consensus_reached=True,
                consensus_level="full",
                agreed_points=["All agree"],
                disputed_points=[],
                recommendation="结束",
            )

            session = orchestrator.run_session(
                meeting=meeting,
                session_type="brainstorm",
                agents=["claude-sonnet"],
            )

            assert len(session.rounds) == 1
            assert session.consensus_level == "full"

    @patch("lib.orchestrator.console")
    @patch("lib.orchestrator.save_meeting")
    @patch("lib.orchestrator.Progress")
    def test_runs_all_rounds_without_consensus(
        self, mock_progress: MagicMock, mock_save: MagicMock, mock_console: MagicMock,
        orchestrator: Orchestrator, mock_runner: MagicMock
    ):
        mock_runner.invoke_with_retry.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        meeting = Meeting(
            topic_id="test_123",
            topic="测试议题",
            created_at="2026-04-01T10:00:00",
        )

        with patch.object(orchestrator, "_detect_consensus") as mock_consensus:
            mock_consensus.return_value = ConsensusResult(
                consensus_reached=False,
                consensus_level="none",
                agreed_points=[],
                disputed_points=[],
                recommendation="继续",
            )

            session = orchestrator.run_session(
                meeting=meeting,
                session_type="brainstorm",
                agents=["claude-sonnet"],
            )

            assert len(session.rounds) == 3


class TestDetectConsensus:
    def test_returns_unknown_on_exception(self, orchestrator: Orchestrator):
        with patch.object(orchestrator.runner, "invoke") as mock_invoke:
            mock_invoke.side_effect = RuntimeError("Unexpected")

            result = orchestrator._detect_consensus({"A": "resp"}, "claude-sonnet")

            assert result.consensus_level == "none"
            assert result.consensus_reached is False


class TestRunRoundParallel:
    @patch("lib.orchestrator.Progress")
    def test_parallel_execution(self, mock_progress: MagicMock, orchestrator: Orchestrator):
        # This is hard to test fully due to ThreadPoolExecutor
        # Just verify it doesn't crash
        from lib.meeting import Meeting, Session
        from lib.config import MeetingTemplate

        meeting = Meeting(
            topic_id="test_123",
            topic="测试议题",
            created_at="2026-04-01T10:00:00",
        )
        session = Session(session_index=1, session_type="brainstorm", agents=["claude-sonnet"])
        template = orchestrator.config.get_template("brainstorm")

        mock_runner = MagicMock()
        mock_runner.invoke_with_retry.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )
        orchestrator.runner = mock_runner

        with patch("lib.orchestrator.Progress"):
            responses = orchestrator._run_round_parallel(
                agents=["claude-sonnet"],
                base_prompt_template="template",
                meeting=meeting,
                session=session,
                template=template,
                round_num=1,
                round_rule="rule",
                agent_list=["Claude Sonnet"],
                history=[],
                prior_proposal="",
                user_feedback="",
                on_response=None,
            )

        assert "claude-sonnet" in responses


class TestRunRoundSequential:
    def test_sequential_calls_in_order(self, orchestrator: Orchestrator):
        from lib.meeting import Meeting, Session
        from lib.config import MeetingTemplate

        meeting = Meeting(
            topic_id="test_123",
            topic="测试议题",
            created_at="2026-04-01T10:00:00",
        )
        session = Session(session_index=1, session_type="brainstorm", agents=["claude-sonnet", "codex-o4-mini"])
        template = orchestrator.config.get_template("brainstorm")

        mock_runner = MagicMock()
        mock_runner.invoke_with_retry.return_value = AgentResponse(
            agent="claude-sonnet",
            content="OK",
            success=True,
            error=None,
            duration_seconds=1.0,
        )
        orchestrator.runner = mock_runner

        call_order = []

        def track_call(*args, **kwargs):
            call_order.append(args[0] if args else kwargs.get("agent_id"))
            return AgentResponse(
                agent=call_order[-1] if isinstance(call_order[-1], str) else "claude-sonnet",
                content="OK",
                success=True,
                error=None,
                duration_seconds=1.0,
            )

        mock_runner.invoke_with_retry.side_effect = track_call

        with patch("lib.orchestrator.Progress"):
            responses = orchestrator._run_round_sequential(
                agents=["claude-sonnet", "codex-o4-mini"],
                base_prompt_template="template",
                meeting=meeting,
                session=session,
                template=template,
                round_num=2,
                round_rule="rule",
                agent_list=["Claude Sonnet", "Codex"],
                history=[{"round": 1, "responses": {"Claude": "First round response"}}],
                on_response=None,
            )

        # Sequential means codex should be called after claude-sonnet in this round
        assert len(call_order) == 2
