"""Tests for cli_assistant.py helper behavior."""
from __future__ import annotations

from unittest.mock import patch

from cli_assistant import _confirm_config, _input_multiline, _resolve_moderator


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
        mock_print.assert_called_once_with("\n[bold cyan][第5步][/bold cyan] 讨论配置：")

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
