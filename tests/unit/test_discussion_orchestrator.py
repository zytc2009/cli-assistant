"""Tests for lib/discussion_orchestrator.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.agent_runner import AgentResponse, AgentRunner
from lib.config import Config
from lib.discussion_orchestrator import DiscussionOrchestrator
from lib.meeting import Discussion, DiscussionPhase, DiscussionRound


@pytest.fixture
def orchestrator(config: Config, mock_runner: MagicMock, tmp_path) -> DiscussionOrchestrator:
    return DiscussionOrchestrator(config=config, base_dir=tmp_path, runner=mock_runner)


class TestRunIndependentPhase:
    @patch("lib.discussion_orchestrator.save_discussion")
    @patch("lib.discussion_orchestrator.Progress")
    def test_parallel_execution(
        self, mock_progress: MagicMock, mock_save: MagicMock,
        orchestrator: DiscussionOrchestrator, mock_runner: MagicMock,
        sample_discussion: Discussion
    ):
        initial_phases = len(sample_discussion.phases)
        phase = orchestrator.run_independent_phase(sample_discussion)

        assert phase.phase_type == "independent"
        assert phase.phase_index == 1
        assert len(phase.rounds) == 1
        assert len(sample_discussion.phases) == initial_phases + 1

    @patch("lib.discussion_orchestrator.save_discussion")
    def test_streaming_mode(
        self, mock_save: MagicMock, orchestrator: DiscussionOrchestrator,
        mock_runner: MagicMock, sample_discussion: Discussion
    ):
        streaming_runner = MagicMock()
        streaming_runner.invoke_with_retry_streaming.return_value = AgentResponse(
            agent="claude-sonnet",
            content="Streaming response",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        phase = orchestrator.run_independent_phase(
            sample_discussion, streaming_runner=streaming_runner
        )

        assert phase.phase_type == "independent"
        assert len(phase.rounds[0].responses) == 2


class TestSelectModerator:
    @patch("lib.discussion_orchestrator.console.input")
    def test_selects_valid_choice(
        self, mock_input: MagicMock, orchestrator: DiscussionOrchestrator,
        sample_discussion: Discussion
    ):
        # Add Phase 1 with responses
        phase1 = DiscussionPhase(
            phase_type="independent",
            phase_index=1,
            rounds=[
                DiscussionRound(
                    round_num=1,
                    responses={"claude-sonnet": "Claude的观点", "codex-o4-mini": "Codex的观点"},
                )
            ],
        )
        sample_discussion.phases.append(phase1)

        mock_input.return_value = "1"

        moderator_id = orchestrator.select_moderator(sample_discussion)

        assert moderator_id == "claude-sonnet"
        assert sample_discussion.moderator == "claude-sonnet"

    @patch("lib.discussion_orchestrator.console.input")
    def test_invalid_choice_defaults_to_first(
        self, mock_input: MagicMock, orchestrator: DiscussionOrchestrator,
        sample_discussion: Discussion
    ):
        phase1 = DiscussionPhase(
            phase_type="independent",
            phase_index=1,
            rounds=[
                DiscussionRound(round_num=1, responses={"claude-sonnet": "观点"}),
            ],
        )
        sample_discussion.phases.append(phase1)

        mock_input.return_value = "999"  # Invalid

        moderator_id = orchestrator.select_moderator(sample_discussion)

        # Should default to first
        assert moderator_id == "claude-sonnet"

    def test_raises_without_phase1(self, orchestrator: DiscussionOrchestrator, sample_discussion: Discussion):
        # Clear the phases from sample_discussion fixture
        sample_discussion.phases = []
        with pytest.raises(ValueError, match="Phase 1 must be completed"):
            orchestrator.select_moderator(sample_discussion)


class TestParseConvergenceSignal:
    def test_detects_suggest_conclude(self, orchestrator: DiscussionOrchestrator):
        result = orchestrator._parse_convergence_signal("讨论已充分，[SUGGEST_CONCLUDE]建议结束")
        assert result is True

    def test_no_signal(self, orchestrator: DiscussionOrchestrator):
        result = orchestrator._parse_convergence_signal("继续讨论其他问题")
        assert result is False


class TestRunDiscussionPhase:
    @patch("lib.discussion_orchestrator.save_discussion")
    @patch("lib.discussion_orchestrator.console.input")
    def test_single_round(
        self, mock_input: MagicMock, mock_save: MagicMock,
        orchestrator: DiscussionOrchestrator, sample_discussion: Discussion
    ):
        # Setup: Phase 1 completed, moderator selected
        phase1 = DiscussionPhase(
            phase_type="independent",
            phase_index=1,
            rounds=[
                DiscussionRound(round_num=1, responses={"claude-sonnet": "Claude的观点", "codex-o4-mini": "Codex的观点"}),
            ],
        )
        sample_discussion.phases.append(phase1)
        sample_discussion.moderator = "claude-sonnet"

        mock_input.return_value = "d"  # End discussion

        # Mock _run_moderator_opening
        with patch.object(orchestrator, "_run_moderator_opening", return_value="主持人开场"):
            with patch.object(orchestrator, "_run_discussion_round", return_value={"codex-o4-mini": "回应"}):
                with patch.object(orchestrator, "_check_consensus") as mock_consensus:
                    mock_consensus.return_value = MagicMock(consensus_reached=False)

                    phase = orchestrator.run_discussion_phase(
                        sample_discussion, max_rounds=3
                    )

        assert phase.phase_type == "discussion"
        assert sample_discussion.moderator == "claude-sonnet"

    def test_raises_without_moderator(self, orchestrator: DiscussionOrchestrator, sample_discussion: Discussion):
        sample_discussion.moderator = None
        with pytest.raises(ValueError, match="Moderator must be selected"):
            orchestrator.run_discussion_phase(sample_discussion)

    @patch("lib.discussion_orchestrator.save_discussion")
    @patch("lib.discussion_orchestrator.console.input")
    def test_requirement_flow_runs_until_moderator_concludes(
        self,
        mock_input: MagicMock,
        mock_save: MagicMock,
        orchestrator: DiscussionOrchestrator,
        sample_discussion: Discussion,
    ):
        phase1 = DiscussionPhase(
            phase_type="independent",
            phase_index=1,
            rounds=[
                DiscussionRound(
                    round_num=1,
                    responses={
                        "claude-sonnet": "已知 Goal，仍缺 Acceptance Criteria",
                        "codex-o4-mini": "缺少 Inputs 约束",
                    },
                )
            ],
        )
        sample_discussion.phases.append(phase1)
        sample_discussion.moderator = "claude-sonnet"
        sample_discussion.flow = "requirement"

        with patch.object(
            orchestrator,
            "_run_moderator_opening",
            side_effect=["[CONTINUE] 继续澄清验收标准", "[SUGGEST_CONCLUDE] 字段已清晰"],
        ):
            with patch.object(
                orchestrator,
                "_run_discussion_round",
                return_value={"codex-o4-mini": "补充澄清内容"},
            ):
                with patch.object(orchestrator, "_check_consensus") as mock_consensus:
                    phase = orchestrator.run_discussion_phase(
                        sample_discussion,
                        max_rounds=3,
                    )

        assert len(phase.rounds) == 2
        mock_consensus.assert_not_called()
        mock_input.assert_not_called()

    def test_requirement_flow_uses_moderator_as_fallback_participant(
        self,
        orchestrator: DiscussionOrchestrator,
        sample_discussion: Discussion,
    ):
        sample_discussion.agents = ["claude-sonnet"]
        sample_discussion.moderator = "claude-sonnet"
        sample_discussion.flow = "requirement"

        participants = orchestrator._discussion_participants(sample_discussion)

        assert participants == ["claude-sonnet"]


class TestCheckConsensus:
    def test_returns_unknown_when_no_agents(self, orchestrator: DiscussionOrchestrator):
        orchestrator.config.agents = {}  # No agents configured

        result = orchestrator._check_consensus({"A": "response"})

        assert result.consensus_level == "none"

    @patch("lib.discussion_orchestrator.detect_consensus")
    def test_uses_first_available_agent(
        self, mock_detect: MagicMock, orchestrator: DiscussionOrchestrator
    ):
        mock_detect.return_value = MagicMock(
            consensus_reached=False,
            consensus_level="none",
            agreed_points=[],
            disputed_points=[],
            recommendation="继续",
        )

        orchestrator._check_consensus({"A": "response"})

        # Should call detect_consensus with first agent in config
        call_kwargs = mock_detect.call_args[1]
        assert "claude-sonnet" in call_kwargs["detector_agent"]


class TestExtractSummary:
    def test_extracts_section_with_marker(self, orchestrator: DiscussionOrchestrator):
        content = """
# 标题

## 内容

### 整体评价
这是摘要内容，
可以包含多行。

### 其他部分
"""
        result = orchestrator._extract_summary(content)
        assert "这是摘要内容" in result

    def test_fallback_to_first_paragraph(self, orchestrator: DiscussionOrchestrator):
        content = "第一段内容。第二段内容。"
        result = orchestrator._extract_summary(content, max_len=10)
        assert "第一段内容" in result

    def test_truncation(self, orchestrator: DiscussionOrchestrator):
        content = "x" * 200
        result = orchestrator._extract_summary(content, max_len=50)
        assert len(result) <= 53  # 50 + "..."


class TestRunSynthesisPhase:
    @patch("lib.discussion_orchestrator.save_discussion")
    def test_synthesis_with_history(
        self, mock_save: MagicMock, orchestrator: DiscussionOrchestrator,
        mock_runner: MagicMock, sample_discussion: Discussion
    ):
        # Setup: all phases completed
        phase1 = DiscussionPhase(
            phase_type="independent",
            phase_index=1,
            rounds=[DiscussionRound(round_num=1, responses={"claude-sonnet": "Phase1观点"})],
        )
        phase2 = DiscussionPhase(
            phase_type="discussion",
            phase_index=2,
            rounds=[
                DiscussionRound(
                    round_num=1,
                    moderator_opening="主持人开场",
                    responses={"claude-sonnet": "Phase2主持", "codex-o4-mini": "Phase2回应"},
                )
            ],
        )
        sample_discussion.phases.extend([phase1, phase2])
        sample_discussion.moderator = "claude-sonnet"

        mock_runner.invoke_with_retry.return_value = AgentResponse(
            agent="claude-sonnet",
            content="# 最终输出\n\n综合结果",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        with patch("lib.discussion_orchestrator.console.status"):
            result = orchestrator.run_synthesis_phase(sample_discussion)

        assert result == "# 最终输出\n\n综合结果"
        assert sample_discussion.final_output == result
        assert sample_discussion.status == "finalized"

    def test_raises_without_moderator(
        self, orchestrator: DiscussionOrchestrator, sample_discussion: Discussion
    ):
        sample_discussion.moderator = ""
        with pytest.raises(ValueError, match="Moderator must be selected"):
            orchestrator.run_synthesis_phase(sample_discussion)


class TestRunModeratorOpening:
    @patch("lib.discussion_orchestrator.console.status")
    def test_uses_moderator_agent(
        self, mock_status: MagicMock, orchestrator: DiscussionOrchestrator,
        mock_runner: MagicMock, sample_discussion: Discussion
    ):
        sample_discussion.moderator = "claude-sonnet"
        sample_discussion.user_feedbacks = ["反馈1"]

        mock_runner.invoke_with_retry.return_value = AgentResponse(
            agent="claude-sonnet",
            content="主持人开场内容",
            success=True,
            error=None,
            duration_seconds=1.0,
        )

        result = orchestrator._run_moderator_opening(
            discussion=sample_discussion,
            round_num=1,
            max_rounds=3,
            history=[],
        )

        assert "主持人开场内容" in result
        # Should include user feedback
        mock_runner.invoke_with_retry.assert_called_once()
