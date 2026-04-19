"""Tests for cli_assistant.py helper behavior."""
from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from cli_assistant import (
    _confirm_config,
    _build_harness_task_document,
    _input_multiline,
    _resolve_moderator,
    cli,
    _validate_requirement_output,
)


class TestResolveModerator:
    @patch("cli_assistant._select_moderator")
    def test_free_discussion_keeps_interactive_selection(
        self,
        mock_select_moderator,
        config,
    ):
        mock_select_moderator.return_value = "codex-o4-mini"
        moderator = _resolve_moderator(
            selected_agents=["claude-sonnet", "codex-o4-mini"],
            config=config,
            flow="discussion",
        )

        assert moderator == "codex-o4-mini"
        mock_select_moderator.assert_called_once_with(
            ["claude-sonnet", "codex-o4-mini"],
            config,
        )

    @patch("cli_assistant._select_moderator")
    def test_requirement_flow_skips_interactive_selection(
        self,
        mock_select_moderator,
        config,
    ):
        moderator = _resolve_moderator(
            selected_agents=["claude-sonnet", "codex-o4-mini"],
            config=config,
            flow="requirement",
        )

        assert moderator == "claude-sonnet"
        mock_select_moderator.assert_not_called()


class TestConfirmConfig:
    @patch("cli_assistant.console.input", return_value="")
    @patch("cli_assistant.console.print")
    def test_free_discussion_uses_step_five(
        self,
        mock_print,
        mock_input,
    ):
        disc_config = _confirm_config("discussion")

        assert disc_config == {"max_rounds": 3}
        mock_input.assert_called_once()
        assert mock_print.call_count == 1
        assert "讨论" in mock_print.call_args[0][0]

    @patch("cli_assistant.console.print")
    @patch("cli_assistant.console.input")
    def test_requirement_flow_skips_config_prompt(
        self,
        mock_input,
        mock_print,
    ):
        disc_config = _confirm_config("requirement")

        assert disc_config == {"max_rounds": 3}
        mock_input.assert_not_called()
        mock_print.assert_not_called()


class TestInputMultiline:
    @patch("cli_assistant.console.input", return_value="")
    @patch("cli_assistant.console.print")
    def test_blank_first_line_skips_input(
        self,
        mock_print,
        mock_input,
    ):
        value = _input_multiline("测试标题", "测试提示")

        assert value == ""
        mock_input.assert_called_once_with("> ")
        assert mock_print.call_count == 2

    @patch("cli_assistant.console.input", side_effect=["第一行", "第二行", ""])
    @patch("cli_assistant.console.print")
    def test_empty_line_ends_after_collecting_lines(
        self,
        mock_print,
        mock_input,
    ):
        value = _input_multiline("测试标题", "测试提示")

        assert value == "第一行\n第二行"
        assert mock_input.call_count == 3
        assert mock_print.call_count == 2


class TestValidateRequirementOutput:
    def test_accepts_requirement_only_document(self):
        _validate_requirement_output(
            "\n".join(
                [
                    "# Requirement: Build a calculator",
                    "## Goal",
                    "Build a command-line calculator.",
                    "## Scope",
                    "- In scope: add arithmetic operations",
                    "## Inputs",
                    "- stdin expressions",
                    "## Outputs",
                    "- stdout results",
                    "## Acceptance Criteria",
                    "- correct arithmetic",
                    "## Open Questions",
                    "- none",
                ]
            )
        )

    def test_rejects_execution_fields(self):
        requirement = "\n".join(
            [
                "# Requirement: Build a calculator",
                "## Goal",
                "Build a command-line calculator.",
                "## Scope",
                "- In scope: add arithmetic operations",
                "## Inputs",
                "- stdin expressions",
                "## Outputs",
                "- stdout results",
                "## Acceptance Criteria",
                "- correct arithmetic",
                "## Open Questions",
                "- none",
                "## Constraints",
                "- execution_mode: cli",
            ]
        )

        try:
            _validate_requirement_output(requirement)
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "execution metadata" in str(exc)


class TestBuildHarnessTaskDocument:
    def test_appends_constraints_and_ready_status(self):
        document = _build_harness_task_document(
            "# Requirement: Build a calculator\n## Goal\nBuild a command-line calculator.\n",
            ["- language: python", "- platform: windows"],
        )

        assert document.endswith("## Status\nready\n")
        assert "## Constraints" in document
        assert "- language: python" in document
        assert "- platform: windows" in document


class TestExportTaskCommand:
    def test_exports_to_explicit_output_path(self, tmp_path):
        requirement_doc = "\n".join(
            [
                "# Requirement: Build a calculator",
                "## Goal",
                "Build a command-line calculator.",
                "## Scope",
                "- In scope: add arithmetic operations",
                "## Inputs",
                "- stdin expressions",
                "## Outputs",
                "- stdout results",
                "## Acceptance Criteria",
                "- correct arithmetic",
                "## Open Questions",
                "- none",
            ]
        )
        output_path = tmp_path / "exports" / "task.md"
        runner = CliRunner()

        with (
            patch("cli_assistant._resolve_final_output_for_topic", return_value=requirement_doc),
            patch(
                "cli_assistant.console.input",
                side_effect=["python", "windows", "", "", "", ""],
            ),
        ):
            result = runner.invoke(
                cli,
                ["export-task", "topic-1", "--output", str(output_path)],
            )

        assert result.exit_code == 0
        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "## Status" in content
        assert "ready" in content
        assert "- language: python" in content
        assert "- platform: windows" in content
