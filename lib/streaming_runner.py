"""Streaming runner for real-time CLI output."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional


def _find_bash_win32() -> str:
    """Find bash executable on Windows, checking PATH and common Git install locations."""
    # 1. Check CLAUDE_CODE_GIT_BASH_PATH env var
    env_path = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")
    if env_path and Path(env_path).exists():
        return env_path
    # 2. Check PATH via shutil.which
    found = shutil.which("bash")
    if found:
        return found
    # 3. Not found - raise clear error
    raise RuntimeError(
        "\n" + "=" * 60 + "\n"
        "ERROR: Cannot find bash.exe on Windows\n"
        "=" * 60 + "\n\n"
        "Claude CLI requires Git bash. Please ensure:\n\n"
        "1. Git for Windows is installed:\n"
        "   https://git-scm.com/download/win\n\n"
        "2. Git is added to your system PATH:\n"
        "   - Default location: C:\\Program Files\\Git\\bin\\bash.exe\n"
        "   - Or: C:\\Program Files\\Git\\usr\\bin\\bash.exe\n\n"
        "3. Or set environment variable before running:\n"
        "   $env:CLAUDE_CODE_GIT_BASH_PATH = \"C:\\Program Files\\Git\\bin\\bash.exe\"\n\n"
        "Current PATH does not contain bash.exe\n"
        "=" * 60
    )


def _build_popen_args(cmd: str) -> dict:
    """Build Popen kwargs that use bash on Windows, sh on Unix."""
    if sys.platform == "win32":
        bash = _find_bash_win32()
        return {"args": [bash, "-c", cmd], "shell": False}
    return {"args": cmd, "shell": True}

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .agent_runner import AgentResponse
from .config import AgentConfig

console = Console()


class StreamingRunner:
    """Run agents with real-time streaming output."""

    def __init__(self, agents: dict[str, AgentConfig]):
        self.agents = agents

    def invoke_streaming(
        self,
        agent_name: str,
        prompt_content: str,
        on_output: Optional[Callable[[str], None]] = None,
        show_header: bool = True,
    ) -> AgentResponse:
        """Invoke an agent with real-time streaming output.

        Args:
            agent_name: The agent ID to invoke
            prompt_content: The prompt to send
            on_output: Optional callback for each line of output
            show_header: Whether to show the agent name header

        Returns:
            AgentResponse with complete output
        """
        agent = self.agents.get(agent_name)
        if agent is None:
            return AgentResponse(
                agent=agent_name,
                content=f"[错误：未知 agent '{agent_name}']",
                success=False,
                error=f"Unknown agent: {agent_name}",
                duration_seconds=0.0,
            )

        prompt_file = None
        start = time.time()
        output_lines = []

        try:
            cmd = agent.command

            # Handle prompt input: either via file placeholder or stdin
            stdin_pipe = None
            if "{prompt_file}" in cmd:
                # Create temp file for commands that need file path
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".md",
                    delete=False,
                    encoding="utf-8",
                ) as f:
                    f.write(prompt_content)
                    prompt_file = f.name
                if sys.platform == "win32":
                    prompt_file = Path(prompt_file).as_posix()
                cmd = cmd.replace("{prompt_file}", prompt_file)
            else:
                # Use stdin for commands with '-' placeholder
                stdin_pipe = subprocess.PIPE

            if show_header:
                console.print(f"\n[bold cyan][{agent.name}][/bold cyan] 正在思考...")
                console.print("─" * 52)

            # Start subprocess with streaming output
            popen_args = _build_popen_args(cmd)
            process = subprocess.Popen(
                **popen_args,
                stdin=stdin_pipe,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,  # Line buffered
            )

            # Write prompt content to stdin and close it (if using stdin mode)
            if stdin_pipe and process.stdin:
                process.stdin.write(prompt_content)
                process.stdin.close()

            # Stream stdout in real-time
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    if not line:
                        break
                    line = line.rstrip('\n\r')
                    output_lines.append(line)

                    # Print with prefix
                    console.print(f"> {line}")

                    # Call callback if provided
                    if on_output:
                        on_output(line)

            # Wait for process to complete
            try:
                return_code = process.wait(timeout=agent.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                duration = time.time() - start
                return AgentResponse(
                    agent=agent_name,
                    content="\n".join(output_lines) if output_lines else "[本轮缺席：调用超时]",
                    success=False,
                    error=f"Timeout after {agent.timeout}s",
                    duration_seconds=duration,
                )
            duration = time.time() - start

            # Get stderr if any
            stderr_output = ""
            if process.stderr:
                stderr_output = process.stderr.read()

            full_output = "\n".join(output_lines)

            if show_header:
                console.print("─" * 52)
                if return_code == 0 and full_output.strip():
                    console.print(f"[green]✓[/green] 完成 ({duration:.1f}s)\n")
                else:
                    console.print(f"[red]✗[/red] 失败 ({duration:.1f}s)\n")

            if return_code == 0 and full_output.strip():
                return AgentResponse(
                    agent=agent_name,
                    content=full_output,
                    success=True,
                    error=None,
                    duration_seconds=duration,
                )
            else:
                error_msg = stderr_output.strip() or f"Exit code {return_code}"
                return AgentResponse(
                    agent=agent_name,
                    content=full_output or "[无输出]",
                    success=False,
                    error=error_msg,
                    duration_seconds=duration,
                )

        except subprocess.TimeoutExpired:
            duration = time.time() - start
            return AgentResponse(
                agent=agent_name,
                content="\n".join(output_lines) if output_lines else "[本轮缺席：调用超时]",
                success=False,
                error=f"Timeout after {agent.timeout}s",
                duration_seconds=duration,
            )
        except Exception as e:
            duration = time.time() - start
            return AgentResponse(
                agent=agent_name,
                content="\n".join(output_lines) if output_lines else "[本轮缺席：调用异常]",
                success=False,
                error=str(e),
                duration_seconds=duration,
            )
        finally:
            if prompt_file:
                try:
                    os.unlink(prompt_file)
                except OSError:
                    pass

    def invoke_with_retry_streaming(
        self,
        agent_name: str,
        prompt_content: str,
        max_retries: int = 2,
        on_output: Optional[Callable[[str], None]] = None,
        show_header: bool = True,
    ) -> AgentResponse:
        """Invoke with retry and streaming output."""
        last_response = None
        for attempt in range(max_retries + 1):
            response = self.invoke_streaming(
                agent_name=agent_name,
                prompt_content=prompt_content,
                on_output=on_output,
                show_header=show_header and attempt == 0,  # Only show header on first attempt
            )
            if response.success:
                return response
            last_response = response
            if attempt < max_retries:
                console.print(f"[dim]重试 {attempt + 2}/{max_retries + 1}...[/dim]")

        return last_response or AgentResponse(
            agent=agent_name,
            content="[本轮缺席：调用失败]",
            success=False,
            error="All retries failed",
            duration_seconds=0.0,
        )
