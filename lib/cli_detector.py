"""CLI detector for detecting locally installed AI CLI tools."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class CLIDetected:
    """Information about a detected CLI."""
    cli_id: str
    name: str
    version: str
    is_installed: bool
    command: str
    check_cmd: str
    strengths: str = ""
    error_message: str = ""


class CLIDetector:
    """Detect locally installed AI CLI tools."""

    KNOWN_CLIS: Dict[str, Dict] = {
        "claude": {
            "name": "Claude Code",
            "command": 'claude -p "{prompt_file}" --output-format text',
            "check_cmd": "claude --version",
            "version_pattern": r"(\d+\.\d+\.\d+)",
            "strengths": "深度推理、架构设计、代码实现",
        },
        "codex": {
            "name": "OpenAI Codex",
            "command": 'codex -q "$(cat {prompt_file})" --approval-mode full-auto',
            "check_cmd": "codex --version",
            "version_pattern": r"(\d+\.\d+\.\d+)",
            "strengths": "复杂推理、数学、算法、工程实现",
        },
        "kimi": {
            "name": "Moonshot Kimi",
            "command": 'kimi chat --file {prompt_file}',
            "check_cmd": "kimi --version",
            "version_pattern": r"(\d+\.\d+\.\d+)",
            "strengths": "产品视角、用户体验、中文场景、长上下文",
        },
        "gemini": {
            "name": "Google Gemini",
            "command": 'cat {prompt_file} | gemini -p " " --yolo',
            "check_cmd": "gemini --version",
            "version_pattern": r"(\d+\.\d+\.\d+)",
            "strengths": "多模态理解、知识广度",
        },
    }

    def detect_all(self) -> List[CLIDetected]:
        """Detect all known CLIs and return their status."""
        results = []
        for cli_id, info in self.KNOWN_CLIS.items():
            detected = self.detect_one(cli_id)
            results.append(detected)
        return results

    def detect_one(self, cli_id: str) -> CLIDetected:
        """Detect a single CLI by ID."""
        if cli_id not in self.KNOWN_CLIS:
            return CLIDetected(
                cli_id=cli_id,
                name=cli_id,
                version="",
                is_installed=False,
                command="",
                check_cmd="",
                error_message=f"Unknown CLI: {cli_id}",
            )

        info = self.KNOWN_CLIS[cli_id]

        # Check if command exists in PATH
        executable = cli_id
        is_installed = shutil.which(executable) is not None

        version = ""
        error_message = ""

        if is_installed:
            try:
                # Try to get version
                result = subprocess.run(
                    info["check_cmd"],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    output = result.stdout.strip()
                    # Extract version using regex
                    pattern = info.get("version_pattern", r"(\d+\.\d+\.\d+)")
                    match = re.search(pattern, output)
                    if match:
                        version = match.group(1)
                    else:
                        version = output[:20]  # Use first 20 chars if no pattern match
                else:
                    error_message = result.stderr.strip()[:100]
            except subprocess.TimeoutExpired:
                error_message = "Version check timeout"
            except Exception as e:
                error_message = str(e)[:100]
        else:
            error_message = "Not found in PATH"

        return CLIDetected(
            cli_id=cli_id,
            name=info["name"],
            version=version,
            is_installed=is_installed,
            command=info["command"],
            check_cmd=info["check_cmd"],
            strengths=info.get("strengths", ""),
            error_message=error_message,
        )

    def get_installed(self) -> List[CLIDetected]:
        """Get only installed CLIs."""
        return [cli for cli in self.detect_all() if cli.is_installed]

    def get_available_cli_ids(self) -> List[str]:
        """Get list of installed CLI IDs."""
        return [cli.cli_id for cli in self.get_installed()]


def format_cli_status(cli: CLIDetected) -> str:
    """Format CLI status for display."""
    status_icon = "✓" if cli.is_installed else "✗"
    version_str = f" ({cli.version})" if cli.version else ""
    return f"[{status_icon}] {cli.cli_id:<10} - {cli.name}{version_str}"


def save_detected_clis_to_config(detected_clis: List[CLIDetected], config_path: Path) -> None:
    """Save detected CLIs to agents.yaml config file.

    Only adds CLIs that are not already present in the config.
    """
    import yaml

    # Load existing config if exists
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {"agents": {}}

    if "agents" not in config:
        config["agents"] = {}

    # Add detected CLIs that are not already in config
    for cli in detected_clis:
        if cli.is_installed and cli.cli_id not in config["agents"]:
            config["agents"][cli.cli_id] = {
                "name": cli.name,
                "cli": cli.cli_id,
                "command": cli.command,
                "prompt_method": "file",
                "max_tokens": 4000,
                "timeout": 120,
                "strengths": cli.strengths,
                "cost_tier": "medium",
            }

    # Save back to file
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)


def add_custom_cli_to_config(
    cli_id: str,
    name: str,
    command: str,
    strengths: str,
    config_path: Path,
) -> bool:
    """Add a custom CLI configuration to agents.yaml.

    Returns True if successful, False otherwise.
    """
    import yaml

    try:
        # Load existing config if exists
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {"agents": {}}

        if "agents" not in config:
            config["agents"] = {}

        # Add or update the CLI config
        config["agents"][cli_id] = {
            "name": name,
            "cli": cli_id,
            "command": command,
            "prompt_method": "file",
            "max_tokens": 4000,
            "timeout": 120,
            "strengths": strengths,
            "cost_tier": "medium",
        }

        # Save back to file
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False)

        return True
    except Exception:
        return False
