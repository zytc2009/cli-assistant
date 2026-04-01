"""Tests for lib/cli_detector.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lib.cli_detector import (
    CLIDetected,
    CLIDetector,
    add_custom_cli_to_config,
    format_cli_status,
    save_detected_clis_to_config,
)


class TestCLIDetector:
    def test_detect_one_unknown_cli(self):
        detector = CLIDetector()
        result = detector.detect_one("nonexistent")

        assert result.is_installed is False
        assert "Unknown CLI" in result.error_message

    @patch("shutil.which")
    def test_detect_one_installed(self, mock_which: MagicMock):
        mock_which.return_value = "/usr/bin/claude"

        detector = CLIDetector()
        result = detector.detect_one("claude")

        assert result.is_installed is True
        assert result.cli_id == "claude"
        assert result.name == "Claude Code"
        assert result.command == 'claude -p "{prompt_file}" --output-format text'

    @patch("shutil.which")
    def test_detect_one_not_installed(self, mock_which: MagicMock):
        mock_which.return_value = None

        detector = CLIDetector()
        result = detector.detect_one("claude")

        assert result.is_installed is False
        assert "Not found in PATH" in result.error_message

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_detect_one_version_parsed(
        self, mock_run: MagicMock, mock_which: MagicMock
    ):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="claude 2.1.86",
            stderr="",
        )

        detector = CLIDetector()
        result = detector.detect_one("claude")

        assert result.version == "2.1.86"

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_detect_one_version_timeout(
        self, mock_run: MagicMock, mock_which: MagicMock
    ):
        import subprocess

        mock_which.return_value = "/usr/bin/claude"
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)

        detector = CLIDetector()
        result = detector.detect_one("claude")

        assert "timeout" in result.error_message.lower()

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_detect_one_version_stderr_error(
        self, mock_run: MagicMock, mock_which: MagicMock
    ):
        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Some error",
        )

        detector = CLIDetector()
        result = detector.detect_one("claude")

        assert "Some error" in result.error_message

    @patch("shutil.which")
    def test_detect_all(self, mock_which: MagicMock):
        mock_which.return_value = "/usr/bin/claude"  # Only claude installed

        detector = CLIDetector()
        results = detector.detect_all()

        assert len(results) == 4  # All known CLIs
        claude_result = next(r for r in results if r.cli_id == "claude")
        assert claude_result.is_installed is True

    @patch("shutil.which")
    def test_get_installed(self, mock_which: MagicMock):
        def which_side_effect(cmd):
            return f"/usr/bin/{cmd}" if cmd in ["claude", "codex"] else None

        mock_which.side_effect = which_side_effect

        detector = CLIDetector()
        installed = detector.get_installed()

        assert len(installed) == 2
        cli_ids = {c.cli_id for c in installed}
        assert cli_ids == {"claude", "codex"}

    @patch("shutil.which")
    def test_get_available_cli_ids(self, mock_which: MagicMock):
        def which_side_effect(cmd):
            return f"/usr/bin/{cmd}" if cmd == "claude" else None
        mock_which.side_effect = which_side_effect

        detector = CLIDetector()
        ids = detector.get_available_cli_ids()

        assert ids == ["claude"]


class TestFormatCliStatus:
    def test_installed_formatting(self):
        cli = CLIDetected(
            cli_id="claude",
            name="Claude Code",
            version="2.1.86",
            is_installed=True,
            command="claude -p",
            check_cmd="claude --version",
        )

        result = format_cli_status(cli)
        assert "✓" in result
        assert "claude" in result
        assert "2.1.86" in result

    def test_not_installed_formatting(self):
        cli = CLIDetected(
            cli_id="gemini",
            name="Google Gemini",
            version="",
            is_installed=False,
            command="",
            check_cmd="",
        )

        result = format_cli_status(cli)
        assert "✗" in result
        assert "gemini" in result


class TestSaveDetectedClisToConfig:
    def test_saves_new_cli_to_empty_config(self, tmp_path: Path):
        config_path = tmp_path / "agents.yaml"
        detected = [
            CLIDetected(
                cli_id="claude",
                name="Claude Code",
                version="2.1.86",
                is_installed=True,
                command='claude -p "{prompt_file}"',
                check_cmd="claude --version",
                strengths="深度推理",
            )
        ]

        save_detected_clis_to_config(detected, config_path)

        assert config_path.exists()
        content = config_path.read_text(encoding="utf-8")
        assert "claude" in content
        assert "Claude Code" in content

    def test_does_not_overwrite_existing(self, tmp_path: Path):
        config_path = tmp_path / "agents.yaml"
        config_path.write_text(
            """
agents:
  claude:
    name: "Existing Config"
    cli: claude
    command: 'old command'
    prompt_method: file
    max_tokens: 1000
    timeout: 60
    strengths: "old"
    cost_tier: high
""",
            encoding="utf-8",
        )

        detected = [
            CLIDetected(
                cli_id="claude",
                name="Claude Code",
                version="2.1.86",
                is_installed=True,
                command='claude -p "{prompt_file}"',
                check_cmd="claude --version",
                strengths="深度推理",
            )
        ]

        save_detected_clis_to_config(detected, config_path)

        content = config_path.read_text(encoding="utf-8")
        # Should still have the original config
        assert "Existing Config" in content
        # Should not have added a duplicate
        lines = content.count("claude:")
        assert lines == 1

    def test_adds_new_cli_to_existing(self, tmp_path: Path):
        config_path = tmp_path / "agents.yaml"
        config_path.write_text(
            """
agents:
  claude:
    name: "Claude"
    cli: claude
    command: 'claude -p'
    prompt_method: file
    max_tokens: 1000
    timeout: 60
    strengths: "推理"
    cost_tier: medium
""",
            encoding="utf-8",
        )

        detected = [
            CLIDetected(
                cli_id="codex",
                name="Codex",
                version="1.0",
                is_installed=True,
                command="codex -q",
                check_cmd="codex --version",
                strengths="代码",
            )
        ]

        save_detected_clis_to_config(detected, config_path)

        content = config_path.read_text(encoding="utf-8")
        assert "claude" in content
        assert "codex" in content


class TestAddCustomCliToConfig:
    def test_adds_custom_cli(self, tmp_path: Path):
        config_path = tmp_path / "agents.yaml"

        result = add_custom_cli_to_config(
            cli_id="my-cli",
            name="My Custom CLI",
            command="mycli -f {prompt_file}",
            strengths="Custom",
            config_path=config_path,
        )

        assert result is True
        assert config_path.exists()
        content = config_path.read_text(encoding="utf-8")
        assert "my-cli" in content
        assert "My Custom CLI" in content

    def test_returns_false_on_error(self, tmp_path: Path):
        # tmp_path is not writable in some test scenarios
        # But Path on a real filesystem should work
        config_path = Path("/nonexistent/path/agents.yaml")

        result = add_custom_cli_to_config(
            cli_id="my-cli",
            name="My CLI",
            command="mycli",
            strengths="",
            config_path=config_path,
        )

        # Should return False if write fails
        assert result is False
