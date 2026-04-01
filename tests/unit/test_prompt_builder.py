"""Tests for lib/prompt_builder.py."""
from __future__ import annotations

from typing import Dict, List

import pytest

from lib.agent_runner import AgentResponse
from lib.config import AgentConfig
from lib.prompt_builder import (
    build_discussion_prompt,
    build_history_section,
    build_independent_prompt,
    build_moderator_opening_prompt,
    build_prompt,
    build_synthesis_prompt,
)


@pytest.fixture
def agent() -> AgentConfig:
    return AgentConfig(
        name="Claude Sonnet",
        cli="claude",
        model="claude-sonnet-4-6",
        command='claude -p "{prompt_file}"',
        prompt_method="file",
        max_tokens=4000,
        timeout=120,
        strengths="代码实现、架构设计",
        cost_tier="medium",
    )


class TestBuildIndependentPrompt:
    def test_placeholders_replaced(self, agent: AgentConfig):
        template = "你是 {agent_name}，擅长 {agent_strengths}。用户想法：{user_idea}"
        result = build_independent_prompt(template, agent, "如何设计缓存")

        assert "Claude Sonnet" in result
        assert "代码实现、架构设计" in result
        assert "如何设计缓存" in result

    def test_no_extra_placeholders(self, agent: AgentConfig):
        template = "你是 {agent_name}。"
        result = build_independent_prompt(template, agent, "test")
        assert "{" not in result


class TestBuildModeratorOpeningPrompt:
    def test_rounds_info_included(self, agent: AgentConfig):
        template = "第 {round_num}/{max_rounds} 轮"
        result = build_moderator_opening_prompt(
            template, agent, "主题", 2, 3, [], ""
        )
        assert "2/3" in result

    def test_history_section_generated(self, agent: AgentConfig):
        template = "历史: {history_section}"
        history = [
            {"round": 1, "responses": {"Claude": "回复1", "Codex": "回复2"}}
        ]
        result = build_moderator_opening_prompt(template, agent, "主题", 2, 3, history, "")

        assert "第 1 轮" in result
        assert "Claude" in result
        assert "回复1" in result

    def test_first_round_no_history(self, agent: AgentConfig):
        template = "历史: {history_section}"
        result = build_moderator_opening_prompt(template, agent, "主题", 1, 3, [], "")

        assert "第一轮讨论" in result
        assert "初始观点" in result

    def test_user_feedback_included(self, agent: AgentConfig):
        template = "反馈: {user_feedback_section}"
        result = build_moderator_opening_prompt(
            template, agent, "主题", 1, 3, [], "这是用户反馈"
        )

        assert "这是用户反馈" in result

    def test_no_user_feedback(self, agent: AgentConfig):
        template = "反馈: {user_feedback_section}"
        result = build_moderator_opening_prompt(template, agent, "主题", 1, 3, [], "")

        assert "无额外意见" in result


class TestBuildDiscussionPrompt:
    def test_truncation_at_800_chars(self, agent: AgentConfig):
        template = "历史: {history_section}"
        long_response = "x" * 1000
        history = [
            {"round": 1, "responses": {"Claude": long_response}}
        ]
        result = build_discussion_prompt(
            template, agent, "主题", history, "主持人", "主持人开场"
        )

        assert "..." in result
        # The truncation is per-agent-response, 800 chars + "..."
        # So the total should be less than 1000 + overhead
        assert len(result) < 1200

    def test_moderator_opening_included(self, agent: AgentConfig):
        template = "开场: {moderator_opening}"
        result = build_discussion_prompt(
            template, agent, "主题", [], "主持人", "聚焦在性能问题"
        )

        assert "聚焦在性能问题" in result

    def test_agent_name_and_strengths_included(self, agent: AgentConfig):
        template = "你是 {agent_name}，擅长 {agent_strengths}"
        result = build_discussion_prompt(
            template, agent, "主题", [], "主持人", "开场"
        )

        assert "Claude Sonnet" in result
        assert "代码实现、架构设计" in result


class TestBuildSynthesisPrompt:
    def test_truncation_at_800_per_response(self, agent: AgentConfig):
        template = "历史: {full_discussion_history}"
        long_response = "y" * 1000
        full_history = [
            {"round": 1, "phase": "独立发言", "responses": {"Claude": long_response}}
        ]
        result = build_synthesis_prompt(
            template, agent, "主题", full_history, []
        )

        assert "..." in result

    def test_user_feedbacks_joined(self, agent: AgentConfig):
        template = "反馈: {all_user_feedback}"
        result = build_synthesis_prompt(
            template, agent, "主题", [], ["反馈1", "反馈2"]
        )

        assert "反馈1" in result
        assert "反馈2" in result

    def test_no_user_feedbacks(self, agent: AgentConfig):
        template = "反馈: {all_user_feedback}"
        result = build_synthesis_prompt(template, agent, "主题", [], [])

        assert "无补充意见" in result

    def test_phase_info_included(self, agent: AgentConfig):
        template = "历史: {full_discussion_history}"
        full_history = [
            {"round": 1, "phase": "独立发言", "responses": {}}
        ]
        result = build_synthesis_prompt(template, agent, "主题", full_history, [])

        assert "独立发言" in result


class TestBuildHistorySection:
    def test_round_one_returns_early(self):
        result = build_history_section(None, round_num=1)
        assert "独立思考" in result

    def test_empty_history_returns_early(self):
        result = build_history_section([], round_num=2)
        assert "独立思考" in result

    def test_history_formatted(self):
        history = [
            {"round": 1, "responses": {"Claude": "回复1", "Codex": "回复2"}},
            {"round": 2, "responses": {"Claude": "回复3"}},
        ]
        result = build_history_section(history, round_num=2)

        assert "第 1 轮" in result
        assert "第 2 轮" in result
        assert "Claude" in result
        assert "回复1" in result
        assert "回复3" in result


class TestBuildPrompt:
    def test_basic_placeholders(self, agent: AgentConfig):
        template = "Topic: {topic}\nType: {session_type}\nRound: {round}/{max_rounds}"
        result = build_prompt(
            template, agent, "Test Topic", "brainstorm", "Brainstorm",
            round_num=1, max_rounds=3, round_rule="Brainstorm rule",
            agent_list=["Claude", "Codex"],
        )

        assert "Test Topic" in result
        assert "brainstorm" in result
        assert "1/3" in result

    def test_prior_proposal_prepended_round_one(self, agent: AgentConfig):
        template = "内容: {history_section}"
        result = build_prompt(
            template, agent, "主题", "brainstorm", "",
            round_num=1, max_rounds=3, round_rule="",
            agent_list=[],
            prior_proposal="# 上一方案",
            history=None,
        )

        assert "上一方案" in result

    def test_user_feedback_appended_when_prior_proposal(
        self, agent: AgentConfig
    ):
        template = "内容: {history_section}"
        result = build_prompt(
            template, agent, "主题", "brainstorm", "",
            round_num=1, max_rounds=3, round_rule="",
            agent_list=[],
            prior_proposal="# 上一方案",
            user_feedback="用户反馈内容",
            history=None,
        )

        assert "上一方案" in result
        assert "用户反馈内容" in result

    def test_history_section_for_round_two(self, agent: AgentConfig):
        template = "历史: {history_section}"
        history = [
            {"round": 1, "responses": {"Claude": "回复"}}
        ]
        result = build_prompt(
            template, agent, "主题", "brainstorm", "",
            round_num=2, max_rounds=3, round_rule="",
            agent_list=[],
            history=history,
        )

        assert "第 1 轮" in result
        assert "回复" in result
        assert "独立思考" not in result  # should have real history
