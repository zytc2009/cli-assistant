"""Tests for lib/agent_runner.py."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lib.agent_runner import AgentResponse, AgentRunner
from lib.config import AgentConfig


@pytest.fixture
def agents() -> dict:
    return {
        "claude-sonnet": AgentConfig(
            name="Claude Sonnet",
            cli="claude",
            model="claude-sonnet-4-6",
            command='claude -p "{prompt_file}"',
            prompt_method="file",
            max_tokens=4000,
            timeout=120,
            strengths="代码",
            cost_tier="medium",
        ),
        "codex": AgentConfig(
            name="Codex",
            cli="codex",
            model="o4-mini",
            command='codex exec -o {output_file} "$(cat {prompt_file})"',
            prompt_method="file",
            max_tokens=4000,
            timeout=90,
            strengths="代码",
            cost_tier="low",
            output_method="file",
        ),
    }


@pytest.fixture
def runner(agents) -> AgentRunner:
    return AgentRunner(agents)


class TestInvoke:
    def test_unknown_agent_returns_error(self, runner: AgentRunner):
        result = runner.invoke("nonexistent", "prompt")

        assert result.success is False
        assert "Unknown agent" in result.error
        assert result.content == "[错误：未知 agent 'nonexistent']"

    @patch("subprocess.run")
    def test_timeout(self, mock_run: MagicMock, runner: AgentRunner):
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 120)

        result = runner.invoke("claude-sonnet", "prompt")

        assert result.success is False
        assert "超时" in result.content or "Timeout" in result.error

    @patch("subprocess.run")
    def test_subprocess_error(self, mock_run: MagicMock, runner: AgentRunner):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Process failed",
        )

        result = runner.invoke("claude-sonnet", "prompt")

        assert result.success is False
        assert "Process failed" in result.error

    @patch("subprocess.run")
    def test_success_with_stdout(self, mock_run: MagicMock, runner: AgentRunner):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Claude response content",
            stderr="",
        )

        result = runner.invoke("claude-sonnet", "prompt")

        assert result.success is True
        assert result.content == "Claude response content"
        assert result.error is None

    @patch("subprocess.run")
    def test_empty_stdout_treated_as_failure(
        self, mock_run: MagicMock, runner: AgentRunner
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        result = runner.invoke("claude-sonnet", "prompt")

        assert result.success is False
        assert "empty output" in result.error.lower()

    @patch("subprocess.run")
    def test_output_file_mode(self, mock_run: MagicMock, runner: AgentRunner, tmp_path: Path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        with patch("lib.agent_runner.tempfile.NamedTemporaryFile") as mock_temp:
            # Simulate output file being created and read
            output_file_content = "Codex file output"
            mock_temp.return_value.__enter__.return_value.name = str(tmp_path / "output.md")

            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.read_text", return_value=output_file_content):
                    result = runner.invoke("codex", "prompt")

        assert result.success is True
        assert output_file_content in result.content

    @patch("subprocess.run")
    def test_generic_exception_caught(
        self, mock_run: MagicMock, runner: AgentRunner
    ):
        mock_run.side_effect = RuntimeError("Unexpected error")

        result = runner.invoke("claude-sonnet", "prompt")

        assert result.success is False
        assert "Unexpected error" in result.error
        assert result.content == "[本轮缺席：调用异常]"

    @patch("subprocess.run")
    def test_temp_files_cleaned_up(
        self, mock_run: MagicMock, runner: AgentRunner, tmp_path: Path
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="OK",
            stderr="",
        )

        prompt_file_path = str(tmp_path / "prompt.md")
        output_file_path = str(tmp_path / "output.md")

        with patch("lib.agent_runner.tempfile.NamedTemporaryFile") as mock_temp:
            mock_temp.return_value.__enter__.return_value.name = prompt_file_path

            with patch("lib.agent_runner.Path") as mock_path:
                # Track which files were unlinked
                unlinked = []

                def track_unlink(self):
                    unlinked.append(str(self))

                mock_path.return_value.unlink = track_unlink
                mock_path.return_value.exists.return_value = True

                runner.invoke("claude-sonnet", "prompt")

    @patch("subprocess.run")
    def test_prompt_file_replaced_in_command(
        self, mock_run: MagicMock, runner: AgentRunner
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="OK",
            stderr="",
        )

        with patch("lib.agent_runner.tempfile.NamedTemporaryFile") as mock_temp:
            mock_temp.return_value.__enter__.return_value.name = "/tmp/prompt.md"

            runner.invoke("claude-sonnet", "test prompt")

        # Verify the command contained the prompt file path
        call_cmd = mock_run.call_args[0][0]
        assert "/tmp/prompt.md" in call_cmd


class TestInvokeWithRetry:
    @patch("subprocess.run")
    def test_success_first_attempt(
        self, mock_run: MagicMock, runner: AgentRunner
    ):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="OK",
            stderr="",
        )

        result = runner.invoke_with_retry("claude-sonnet", "prompt")

        assert result.success is True
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_retry_on_failure_then_success(
        self, mock_run: MagicMock, runner: AgentRunner
    ):
        mock_run.side_effect = [
            subprocess.TimeoutExpired("cmd", 120),
            MagicMock(returncode=0, stdout="OK", stderr=""),
        ]

        result = runner.invoke_with_retry("claude-sonnet", "prompt")

        assert result.success is True
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_all_retries_fail(
        self, mock_run: MagicMock, runner: AgentRunner
    ):
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 120)

        result = runner.invoke_with_retry("claude-sonnet", "prompt")

        assert result.success is False
        assert mock_run.call_count == 3  # initial + 2 retries

    @patch("subprocess.run")
    def test_custom_max_retries(self, mock_run: MagicMock, runner: AgentRunner):
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 120)

        result = runner.invoke_with_retry("claude-sonnet", "prompt", max_retries=1)

        assert result.success is False
        assert mock_run.call_count == 2  # initial + 1 retry
