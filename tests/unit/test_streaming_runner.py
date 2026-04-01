"""Tests for lib/streaming_runner.py."""
from __future__ import annotations

import subprocess
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from lib.agent_runner import AgentResponse
from lib.config import AgentConfig
from lib.streaming_runner import StreamingRunner


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
    }


@pytest.fixture
def runner(agents) -> StreamingRunner:
    return StreamingRunner(agents)


class TestInvokeStreaming:
    def test_unknown_agent_returns_error(self, runner: StreamingRunner):
        result = runner.invoke_streaming("nonexistent", "prompt")

        assert result.success is False
        assert "Unknown agent" in result.error

    @patch("subprocess.Popen")
    def test_success_with_streaming(self, mock_popen: MagicMock, runner: StreamingRunner):
        # Mock the process
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline.side_effect = [
            "第一行输出",
            "第二行输出",
            ""  # EOF
        ]
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        callback_lines = []

        result = runner.invoke_streaming(
            "claude-sonnet",
            "prompt",
            on_output=lambda line: callback_lines.append(line),
        )

        assert result.success is True
        assert "第一行输出" in result.content
        assert "第二行输出" in result.content
        assert callback_lines == ["第一行输出", "第二行输出"]

    @patch("subprocess.Popen")
    def test_timeout(self, mock_popen: MagicMock, runner: StreamingRunner):
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline.side_effect = ["输出行", ""]
        mock_process.wait.side_effect = subprocess.TimeoutExpired("cmd", 120)
        mock_process.kill = MagicMock()
        mock_popen.return_value = mock_process

        result = runner.invoke_streaming("claude-sonnet", "prompt")

        assert result.success is False
        assert "超时" in result.error or "Timeout" in result.error

    @patch("subprocess.Popen")
    def test_process_error(self, mock_popen: MagicMock, runner: StreamingRunner):
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline.side_effect = ["输出", ""]
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = "Process error"
        mock_process.wait.return_value = 1
        mock_popen.return_value = mock_process

        result = runner.invoke_streaming("claude-sonnet", "prompt")

        assert result.success is False
        assert "Process error" in result.error


class TestInvokeWithRetryStreaming:
    @patch("subprocess.Popen")
    def test_success_first_attempt(self, mock_popen: MagicMock, runner: StreamingRunner):
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline.side_effect = ["OK", ""]
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        result = runner.invoke_with_retry_streaming("claude-sonnet", "prompt")

        assert result.success is True

    @patch("subprocess.Popen")
    def test_retry_on_failure(self, mock_popen: MagicMock, runner: StreamingRunner):
        # Need distinct process mocks for each Popen call
        mock_process_fail = MagicMock()
        mock_process_fail.stdout = MagicMock()
        mock_process_fail.stdout.readline.side_effect = ["第一行\n", ""]
        mock_process_fail.stderr = MagicMock()
        mock_process_fail.stderr.read.return_value = ""
        mock_process_fail.wait.side_effect = subprocess.TimeoutExpired("cmd", 120)
        mock_process_fail.kill = MagicMock()

        mock_process_success = MagicMock()
        mock_process_success.stdout = MagicMock()
        mock_process_success.stdout.readline.side_effect = ["成功\n", ""]
        mock_process_success.stderr = MagicMock()
        mock_process_success.stderr.read.return_value = ""
        mock_process_success.wait.return_value = 0

        mock_popen.side_effect = [mock_process_fail, mock_process_success]

        result = runner.invoke_with_retry_streaming("claude-sonnet", "prompt", max_retries=1)

        # First failed with timeout, second succeeded
        assert result.success is True

    @patch("subprocess.Popen")
    def test_show_header_only_first_attempt(
        self, mock_popen: MagicMock, runner: StreamingRunner
    ):
        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline.side_effect = ["line", ""]
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        with patch("lib.streaming_runner.console.print") as mock_print:
            runner.invoke_with_retry_streaming(
                "claude-sonnet", "prompt", max_retries=1, show_header=True
            )

            # Header should be printed only once (first attempt)
            # Called for: header, separator, separator (close), completion
            header_calls = [
                c for c in mock_print.call_args_list
                if "正在思考" in str(c)
            ]
            # Should be exactly one "正在思考" header
            assert len(header_calls) >= 1
