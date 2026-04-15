"""Tests for cli_assistant.py helper behavior."""
from __future__ import annotations

from unittest.mock import patch

from cli_assistant import _confirm_config, _resolve_moderator


class TestResolveModerator:
    @patch("cli_assistant._select_moderator")
    def test_free_discussion_skips_interactive_selection(
        self,
        mock_select_moderator,
        config,
    ):
        moderator = _resolve_moderator(
            selected_agents=["claude-sonnet", "codex-o4-mini"],
            config=config,
            flow="discussion",
        )

        assert moderator == "claude-sonnet"
        mock_select_moderator.assert_not_called()

    @patch("cli_assistant._select_moderator", return_value="codex-o4-mini")
    def test_requirement_flow_keeps_interactive_selection(
        self,
        mock_select_moderator,
        config,
    ):
        moderator = _resolve_moderator(
            selected_agents=["claude-sonnet", "codex-o4-mini"],
            config=config,
            flow="requirement",
        )

        assert moderator == "codex-o4-mini"
        mock_select_moderator.assert_called_once_with(
            ["claude-sonnet", "codex-o4-mini"],
            config,
        )


class TestConfirmConfig:
    @patch("cli_assistant.console.input", return_value="")
    @patch("cli_assistant.console.print")
    def test_free_discussion_uses_step_four(
        self,
        mock_print,
        mock_input,
    ):
        disc_config = _confirm_config("discussion")

        assert disc_config == {"max_rounds": 3}
        mock_input.assert_called_once()
        mock_print.assert_called_once_with("\n[bold cyan][第4步][/bold cyan] 讨论配置：")

    @patch("cli_assistant.console.input", return_value="")
    @patch("cli_assistant.console.print")
    def test_requirement_flow_uses_step_five(
        self,
        mock_print,
        mock_input,
    ):
        disc_config = _confirm_config("requirement")

        assert disc_config == {"max_rounds": 3}
        mock_input.assert_called_once()
        mock_print.assert_called_once_with("\n[bold cyan][第5步][/bold cyan] 讨论配置：")
