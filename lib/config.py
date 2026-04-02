"""Configuration loading and validation for ai-council."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass(frozen=True)
class AgentConfig:
    name: str
    cli: str
    model: str
    command: str
    prompt_method: str
    max_tokens: int
    timeout: int
    strengths: str
    cost_tier: str
    output_method: str = "stdout"
    output_file: str = ""

    def validate(self, agent_id: str) -> None:
        # 支持两种 prompt 传递方式：
        # 1. 文件模式：命令包含 {prompt_file} 或 {prompt_content}
        # 2. stdin 模式：命令使用 "-" 作为输入（如 claude -p -, codex -q -）
        is_file_mode = "{prompt_file}" in self.command or "{prompt_content}" in self.command
        is_stdin_mode = " -p -" in self.command or " -q -" in self.command
        if not is_file_mode and not is_stdin_mode:
            raise ValueError(f"Agent '{agent_id}' command must contain {{prompt_file}} or use stdin mode (e.g., -p -, -q -)")
        if self.timeout <= 0:
            raise ValueError(f"Agent '{agent_id}' timeout must be > 0")


@dataclass(frozen=True)
class MeetingTemplate:
    description: str
    max_rounds: int
    speaking_order: str
    round_rules: Dict[int, str]
    output: str


@dataclass(frozen=True)
class ModelStrategy:
    brainstorm: List[str] = field(default_factory=list)
    review: List[str] = field(default_factory=list)
    decision: List[str] = field(default_factory=list)

    def agents_for(self, session_type: str) -> List[str]:
        return getattr(self, session_type, [])


@dataclass(frozen=True)
class PresetConfig:
    description: str
    sessions: List[str]
    default_strategy: str


def load_agents(path: Path) -> Dict[str, AgentConfig]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    agents = {}
    for agent_id, cfg in data.get("agents", {}).items():
        agent = AgentConfig(
            name=cfg["name"],
            cli=cfg["cli"],
            model=cfg.get("model", ""),
            command=cfg["command"],
            prompt_method=cfg.get("prompt_method", "file"),
            max_tokens=cfg.get("max_tokens", 4000),
            timeout=cfg.get("timeout", 120),
            strengths=cfg.get("strengths", ""),
            cost_tier=cfg.get("cost_tier", "medium"),
            output_method=cfg.get("output_method", "stdout"),
            output_file=cfg.get("output_file", ""),
        )
        agent.validate(agent_id)
        agents[agent_id] = agent
    return agents


def load_templates(path: Path) -> Dict[str, MeetingTemplate]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    templates = {}
    for tmpl_id, cfg in data.get("templates", {}).items():
        round_rules = {int(k): v for k, v in cfg.get("round_rules", {}).items()}
        templates[tmpl_id] = MeetingTemplate(
            description=cfg["description"],
            max_rounds=cfg["max_rounds"],
            speaking_order=cfg.get("speaking_order", "round_robin"),
            round_rules=round_rules,
            output=cfg.get("output", "proposal"),
        )
    return templates


def load_strategies(path: Path) -> Dict[str, ModelStrategy]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    strategies = {}
    for strat_id, cfg in data.get("model_strategies", {}).items():
        strategies[strat_id] = ModelStrategy(
            brainstorm=cfg.get("brainstorm", []),
            review=cfg.get("review", []),
            decision=cfg.get("decision", []),
        )
    return strategies


def load_presets(path: Path) -> Dict[str, PresetConfig]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    presets = {}
    for preset_id, cfg in data.get("presets", {}).items():
        presets[preset_id] = PresetConfig(
            description=cfg["description"],
            sessions=cfg["sessions"],
            default_strategy=cfg.get("default_strategy", "balanced"),
        )
    return presets


def load_prompt_template(prompts_dir: Path, name: str) -> str:
    path = prompts_dir / name
    with open(path, encoding="utf-8") as f:
        return f.read()


class Config:
    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "config"
        self.config_dir = config_dir
        self.prompts_dir = config_dir / "prompts"
        self.agents = load_agents(config_dir / "agents.yaml")
        self.templates = load_templates(config_dir / "meeting_templates.yaml")
        self.strategies = load_strategies(config_dir / "model_strategies.yaml")
        self.presets = load_presets(config_dir / "model_strategies.yaml")

    def get_agent(self, agent_id: str) -> AgentConfig:
        if agent_id not in self.agents:
            raise ValueError(f"Unknown agent: '{agent_id}'. Available: {list(self.agents.keys())}")
        return self.agents[agent_id]

    def get_template(self, template_id: str) -> MeetingTemplate:
        if template_id not in self.templates:
            raise ValueError(f"Unknown template: '{template_id}'")
        return self.templates[template_id]

    def get_strategy(self, strategy_id: str) -> ModelStrategy:
        if strategy_id not in self.strategies:
            raise ValueError(f"Unknown strategy: '{strategy_id}'")
        return self.strategies[strategy_id]

    def prompt(self, name: str) -> str:
        return load_prompt_template(self.prompts_dir, name)
