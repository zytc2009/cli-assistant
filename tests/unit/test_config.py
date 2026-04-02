"""Tests for lib/config.py."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lib.config import (
    AgentConfig,
    Config,
    load_agents,
    load_presets,
    load_prompt_template,
    load_strategies,
    load_templates,
)


class TestAgentConfigValidate:
    def test_valid_config_no_error(self):
        cfg = AgentConfig(
            name="Test",
            cli="test",
            model="m1",
            command='claude -p "{prompt_file}"',
            prompt_method="file",
            max_tokens=4000,
            timeout=120,
            strengths="test",
            cost_tier="medium",
        )
        cfg.validate("test-agent")  # should not raise

    def test_missing_prompt_file_raises(self):
        cfg = AgentConfig(
            name="Test",
            cli="test",
            model="m1",
            command="claude --help",
            prompt_method="file",
            max_tokens=4000,
            timeout=120,
            strengths="test",
            cost_tier="medium",
        )
        with pytest.raises(ValueError, match="must contain|stdin mode"):
            cfg.validate("test-agent")

    def test_stdin_mode_with_dash_p_accepted(self):
        """Test that commands using stdin mode (-p -) are accepted."""
        cfg = AgentConfig(
            name="Claude",
            cli="claude",
            model="claude-sonnet-4-6",
            command="claude -p - --output-format text",
            prompt_method="file",
            max_tokens=4000,
            timeout=120,
            strengths="test",
            cost_tier="medium",
        )
        cfg.validate("claude")  # should not raise

    def test_stdin_mode_with_dash_q_accepted(self):
        """Test that commands using stdin mode (-q -) are accepted."""
        cfg = AgentConfig(
            name="Codex",
            cli="codex",
            model="o3",
            command="codex -q - --approval-mode full-auto",
            prompt_method="file",
            max_tokens=4000,
            timeout=120,
            strengths="test",
            cost_tier="medium",
        )
        cfg.validate("codex")  # should not raise

    def test_timeout_zero_raises(self):
        cfg = AgentConfig(
            name="Test",
            cli="test",
            model="m1",
            command='claude -p "{prompt_file}"',
            prompt_method="file",
            max_tokens=4000,
            timeout=0,
            strengths="test",
            cost_tier="medium",
        )
        with pytest.raises(ValueError, match="timeout must be"):
            cfg.validate("test-agent")

    def test_prompt_content_placeholder_accepted(self):
        cfg = AgentConfig(
            name="Test",
            cli="test",
            model="m1",
            command='claude -c "{prompt_content}"',
            prompt_method="content",
            max_tokens=4000,
            timeout=60,
            strengths="test",
            cost_tier="low",
        )
        cfg.validate("test-agent")  # should not raise


class TestLoadAgents:
    def test_loads_valid_agents(self, tmp_path: Path):
        yaml_path = tmp_path / "agents.yaml"
        yaml_path.write_text("""
agents:
  claude-sonnet:
    name: "Claude Sonnet"
    cli: claude
    model: claude-sonnet-4-6
    command: 'claude -p "{prompt_file}" --output-format text'
    prompt_method: file
    max_tokens: 4000
    timeout: 120
    strengths: "代码实现"
    cost_tier: medium
    output_method: stdout
""", encoding="utf-8")

        agents = load_agents(yaml_path)
        assert "claude-sonnet" in agents
        assert agents["claude-sonnet"].name == "Claude Sonnet"
        assert agents["claude-sonnet"].timeout == 120
        assert agents["claude-sonnet"].output_method == "stdout"

    def test_loads_default_output_method(self, tmp_path: Path):
        yaml_path = tmp_path / "agents.yaml"
        yaml_path.write_text("""
agents:
  test:
    name: "Test"
    cli: test
    model: ""
    command: 'test -f {prompt_file}'
    prompt_method: file
    max_tokens: 1000
    timeout: 60
    strengths: ""
    cost_tier: low
""", encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["test"].output_method == "stdout"

    def test_loads_output_method_file(self, tmp_path: Path):
        yaml_path = tmp_path / "agents.yaml"
        yaml_path.write_text("""
agents:
  codex:
    name: "Codex"
    cli: codex
    model: o4-mini
    command: 'codex -o {output_file} "$(cat {prompt_file})"'
    prompt_method: file
    max_tokens: 4000
    timeout: 90
    strengths: ""
    cost_tier: low
    output_method: file
""", encoding="utf-8")

        agents = load_agents(yaml_path)
        assert agents["codex"].output_method == "file"


class TestLoadTemplates:
    def test_loads_templates(self, tmp_path: Path):
        yaml_path = tmp_path / "templates.yaml"
        yaml_path.write_text("""
templates:
  brainstorm:
    description: "头脑风暴"
    max_rounds: 3
    speaking_order: round_robin
    round_rules:
      1: "发散"
      2: "延展"
      3: "收敛"
    output: proposal
""", encoding="utf-8")

        templates = load_templates(yaml_path)
        assert "brainstorm" in templates
        assert templates["brainstorm"].max_rounds == 3
        assert templates["brainstorm"].round_rules[1] == "发散"
        assert templates["brainstorm"].round_rules[2] == "延展"

    def test_round_rules_keys_are_ints(self, tmp_path: Path):
        yaml_path = tmp_path / "templates.yaml"
        yaml_path.write_text("""
templates:
  test:
    description: "Test"
    max_rounds: 2
    speaking_order: round_robin
    round_rules:
      1: "第一轮"
      2: "第二轮"
    output: proposal
""", encoding="utf-8")

        templates = load_templates(yaml_path)
        rules = templates["test"].round_rules
        assert all(isinstance(k, int) for k in rules.keys())


class TestLoadStrategies:
    def test_loads_strategies(self, tmp_path: Path):
        yaml_path = tmp_path / "strategies.yaml"
        yaml_path.write_text("""
model_strategies:
  balanced:
    brainstorm: [claude-sonnet, codex-o4-mini]
    review: [claude-sonnet]
    decision: [claude-sonnet]
""", encoding="utf-8")

        strategies = load_strategies(yaml_path)
        assert "balanced" in strategies
        assert strategies["balanced"].brainstorm == ["claude-sonnet", "codex-o4-mini"]
        assert strategies["balanced"].review == ["claude-sonnet"]


class TestLoadPresets:
    def test_loads_presets(self, tmp_path: Path):
        yaml_path = tmp_path / "strategies.yaml"
        yaml_path.write_text("""
presets:
  tech_selection:
    description: "技术选型"
    sessions: [brainstorm, review, decision]
    default_strategy: balanced
""", encoding="utf-8")

        presets = load_presets(yaml_path)
        assert "tech_selection" in presets
        assert presets["tech_selection"].sessions == ["brainstorm", "review", "decision"]
        assert presets["tech_selection"].default_strategy == "balanced"


class TestLoadPromptTemplate:
    def test_loads_existing_template(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.md").write_text("Hello {name}", encoding="utf-8")

        content = load_prompt_template(prompts_dir, "test.md")
        assert content == "Hello {name}"

    def test_raises_for_missing_template(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        with pytest.raises(FileNotFoundError):
            load_prompt_template(prompts_dir, "nonexistent.md")


class TestConfig:
    def test_get_agent_valid(self, config: Config):
        agent = config.get_agent("claude-sonnet")
        assert agent.name == "Claude Sonnet"

    def test_get_agent_invalid_raises(self, config: Config):
        with pytest.raises(ValueError, match="Unknown agent"):
            config.get_agent("nonexistent-agent")

    def test_get_template_valid(self, config: Config):
        tmpl = config.get_template("brainstorm")
        assert tmpl.max_rounds == 3

    def test_get_template_invalid_raises(self, config: Config):
        with pytest.raises(ValueError, match="Unknown template"):
            config.get_template("nonexistent")

    def test_get_strategy_valid(self, config: Config):
        strat = config.get_strategy("balanced")
        assert strat.brainstorm == ["claude-sonnet", "codex-o4-mini"]

    def test_get_strategy_invalid_raises(self, config: Config):
        with pytest.raises(ValueError, match="Unknown strategy"):
            config.get_strategy("nonexistent")

    def test_prompt_loads_template(self, config: Config, tmp_path: Path):
        # Override prompts_dir in config to use our temp dir
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "base_system.md").write_text("Topic: {topic}", encoding="utf-8")
        config.prompts_dir = prompts_dir

        content = config.prompt("base_system.md")
        assert content == "Topic: {topic}"
