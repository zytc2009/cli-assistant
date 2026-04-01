"""CLI agent invocation with subprocess."""
from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AgentConfig


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
            # Create temp file for prompt
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(prompt_content)
                prompt_file = f.name

            cmd = agent.command.replace("{prompt_file}", prompt_file)

            # Handle output to file (e.g., codex -o flag)
            if "{output_file}" in cmd:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".md",
                    delete=False,
                    encoding="utf-8",
                ) as f:
                    output_file = f.name
                cmd = cmd.replace("{output_file}", output_file)

            result = subprocess.run(
                cmd,
                shell=True,
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
