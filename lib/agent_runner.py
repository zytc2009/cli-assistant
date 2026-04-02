"""CLI agent invocation with subprocess."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AgentConfig


def _find_bash_win32() -> str:
    """Find bash executable on Windows, checking PATH and common Git install locations."""
    import os
    from pathlib import Path
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


def _build_subprocess_args(cmd: str) -> dict:
    """Build subprocess kwargs that use bash on Windows, sh on Unix."""
    if sys.platform == "win32":
        bash = _find_bash_win32()
        return {"args": [bash, "-c", cmd], "shell": False}
    return {"args": cmd, "shell": True}


@dataclass
class AgentResponse:
    agent: str
    content: str
    success: bool
    error: Optional[str]
    duration_seconds: float


class AgentRunner:
    def __init__(self, agents: dict[str, AgentConfig]):
        self.agents = agents

    def invoke(self, agent_name: str, prompt_content: str) -> AgentResponse:
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
        output_file = None
        start = time.time()
        try:
            cmd = agent.command

            # Handle prompt input: either via file placeholder or stdin
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

            # Handle output to file (e.g., codex -o flag)
            if "{output_file}" in cmd:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".md",
                    delete=False,
                    encoding="utf-8",
                ) as f:
                    output_file = f.name
                if sys.platform == "win32":
                    output_file = Path(output_file).as_posix()
                cmd = cmd.replace("{output_file}", output_file)

            sub_args = _build_subprocess_args(cmd)
            # Only pass input via stdin if not using file-based prompt
            stdin_input = prompt_content if "{prompt_file}" not in agent.command else None
            result = subprocess.run(
                **sub_args,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=agent.timeout,
                encoding="utf-8",
                errors="replace",
            )

            duration = time.time() - start

            # Determine output source
            if output_file and Path(output_file).exists():
                try:
                    output_content = Path(output_file).read_text(encoding="utf-8")
                except Exception:
                    output_content = result.stdout.strip()
            else:
                output_content = result.stdout.strip()

            if result.returncode == 0 and output_content:
                return AgentResponse(
                    agent=agent_name,
                    content=output_content,
                    success=True,
                    error=None,
                    duration_seconds=duration,
                )
            else:
                error_msg = result.stderr.strip() or f"Exit code {result.returncode}, empty output"
                return AgentResponse(
                    agent=agent_name,
                    content=output_content or "[本轮缺席：调用失败]",
                    success=False,
                    error=error_msg,
                    duration_seconds=duration,
                )

        except subprocess.TimeoutExpired:
            duration = time.time() - start
            return AgentResponse(
                agent=agent_name,
                content="[本轮缺席：调用超时]",
                success=False,
                error=f"Timeout after {agent.timeout}s",
                duration_seconds=duration,
            )
        except Exception as e:
            duration = time.time() - start
            return AgentResponse(
                agent=agent_name,
                content="[本轮缺席：调用异常]",
                success=False,
                error=str(e),
                duration_seconds=duration,
            )
        finally:
            if prompt_file:
                try:
                    Path(prompt_file).unlink(missing_ok=True)
                except Exception:
                    pass
            if output_file:
                try:
                    Path(output_file).unlink(missing_ok=True)
                except Exception:
                    pass

    def invoke_with_retry(
        self, agent_name: str, prompt_content: str, max_retries: int = 2
    ) -> AgentResponse:
        last_response = None
        for attempt in range(max_retries + 1):
            response = self.invoke(agent_name, prompt_content)
            if response.success:
                return response
            last_response = response
            if attempt < max_retries:
                pass  # retry immediately
        return last_response or AgentResponse(
            agent=agent_name,
            content="[本轮缺席：调用失败]",
            success=False,
            error="All retries failed",
            duration_seconds=0.0,
        )
