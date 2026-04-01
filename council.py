#!/usr/bin/env python3
"""Multi-AI Discussion Orchestrator — council CLI."""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

# Ensure lib is importable
sys.path.insert(0, str(Path(__file__).parent))

from lib.agent_runner import AgentRunner
from lib.config import Config
from lib.meeting import (
    Meeting,
    create_topic_id,
    list_meetings,
    load_meeting,
    save_meeting,
)
from lib.orchestrator import Orchestrator

BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"

console = Console()


def _make_runner(config: Config) -> AgentRunner:
    return AgentRunner(config.agents)


def _make_orchestrator(config: Config, runner: AgentRunner) -> Orchestrator:
    return Orchestrator(config=config, base_dir=BASE_DIR, runner=runner)


def _parse_agents(agents_str: str, config: Config, strategy: str, session_type: str) -> list[str]:
    if agents_str:
        agent_list = [a.strip() for a in agents_str.split(",")]
        for a in agent_list:
            config.get_agent(a)  # validate
        return agent_list
    if strategy:
        strat = config.get_strategy(strategy)
        agents = strat.agents_for(session_type)
        if agents:
            return agents
    # Default fallback: all configured agents
    return list(config.agents.keys())


def _pick_summarizer(config: Config) -> str:
    """Pick cheapest available agent for summarization tasks."""
    preference = ["claude-sonnet", "codex-o4-mini", "kimi", "claude-opus", "codex-o3"]
    for a in preference:
        if a in config.agents:
            return a
    return next(iter(config.agents))


# ── CLI Group ─────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("0.1.0", prog_name="council")
def cli():
    """Multi-AI Discussion Orchestrator — conduct structured AI meetings."""
    pass


# ── new ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("topic")
@click.option("--agents", "-a", default="", help="Comma-separated agent IDs (e.g. claude-sonnet,codex-o4-mini)")
@click.option("--mode", "-m", default="brainstorm", type=click.Choice(["brainstorm", "review", "decision"]), help="Session type")
@click.option("--strategy", "-s", default="", help="Model strategy: high_stakes|balanced|budget")
@click.option("--rounds", "-r", default=0, type=int, help="Override max rounds (0 = use template default)")
@click.option("--preset", "-p", default="", help="Use a preset: tech_selection|code_review|architecture|postmortem")
def new(topic, agents, mode, strategy, rounds, preset):
    """Start a new meeting on TOPIC."""
    config = Config(CONFIG_DIR)
    runner = _make_runner(config)
    orchestrator = _make_orchestrator(config, runner)
    summarizer = _pick_summarizer(config)

    topic_id = create_topic_id(topic)
    from datetime import datetime
    meeting = Meeting(
        topic_id=topic_id,
        topic=topic,
        created_at=datetime.now().isoformat(),
    )

    console.print(f"\n[bold green]新议题：{topic}[/bold green]")
    console.print(f"ID: {topic_id}")

    if preset:
        preset_cfg = config.presets.get(preset)
        if not preset_cfg:
            console.print(f"[red]未知预设: {preset}[/red]")
            sys.exit(1)
        sessions_to_run = preset_cfg.sessions
        used_strategy = strategy or preset_cfg.default_strategy
        console.print(f"使用预设: {preset_cfg.description}，共 {len(sessions_to_run)} 个阶段\n")
    else:
        sessions_to_run = [mode]
        used_strategy = strategy

    prior_proposal = ""
    for session_type in sessions_to_run:
        agent_list = _parse_agents(agents, config, used_strategy, session_type)
        console.print(f"\n参会者：{', '.join(agent_list)}")

        # Override max_rounds if specified
        if rounds > 0:
            template = config.get_template(session_type)
            template.max_rounds = rounds

        session = orchestrator.run_session(
            meeting=meeting,
            session_type=session_type,
            agents=agent_list,
            prior_proposal=prior_proposal,
            summarizer_agent=summarizer,
        )
        prior_proposal = session.proposal

    console.print(f"\n[bold]会议记录保存至:[/bold] meetings/{topic_id}/")
    console.print(f"方案文档：meetings/{topic_id}/session_{len(meeting.sessions):02d}/proposal.md")


# ── continue ──────────────────────────────────────────────────────────────────

@cli.command("continue")
@click.argument("topic_id")
@click.option("--feedback", "-f", default="", help="User feedback to inject into next session")
@click.option("--mode", "-m", default="", type=click.Choice(["", "brainstorm", "review", "decision"]), help="Override session type")
@click.option("--agents", "-a", default="", help="Override agents for this session")
@click.option("--strategy", "-s", default="", help="Override model strategy")
def continue_meeting(topic_id, feedback, mode, agents, strategy):
    """Continue an existing meeting with the next session."""
    config = Config(CONFIG_DIR)
    runner = _make_runner(config)
    orchestrator = _make_orchestrator(config, runner)
    summarizer = _pick_summarizer(config)

    try:
        meeting = load_meeting(topic_id, BASE_DIR)
    except FileNotFoundError:
        console.print(f"[red]未找到议题: {topic_id}[/red]")
        sys.exit(1)

    # Auto-advance session type
    session_sequence = ["brainstorm", "review", "decision"]
    if meeting.sessions:
        last_type = meeting.sessions[-1].session_type
        last_idx = session_sequence.index(last_type) if last_type in session_sequence else -1
        next_type = session_sequence[last_idx + 1] if last_idx + 1 < len(session_sequence) else "decision"
    else:
        next_type = "brainstorm"

    session_type = mode or next_type
    agent_list = _parse_agents(agents, config, strategy, session_type)

    # Get prior proposal
    prior_proposal = ""
    if meeting.sessions:
        prior_proposal = meeting.sessions[-1].proposal

    console.print(f"\n[bold green]继续议题：{meeting.topic}[/bold green]")
    console.print(f"下一阶段：{session_type}")
    if feedback:
        console.print(f"用户意见：{feedback}")

    orchestrator.run_session(
        meeting=meeting,
        session_type=session_type,
        agents=agent_list,
        prior_proposal=prior_proposal,
        user_feedback=feedback,
        summarizer_agent=summarizer,
    )

    console.print(f"\n[bold]会议记录:[/bold] meetings/{topic_id}/")


# ── finalize ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("topic_id")
def finalize(topic_id):
    """Mark a meeting as finalized and copy latest proposal as final_proposal.md."""
    config = Config(CONFIG_DIR)
    try:
        meeting = load_meeting(topic_id, BASE_DIR)
    except FileNotFoundError:
        console.print(f"[red]未找到议题: {topic_id}[/red]")
        sys.exit(1)

    if not meeting.sessions:
        console.print("[red]没有可定稿的内容[/red]")
        sys.exit(1)

    latest = meeting.sessions[-1]
    if not latest.proposal:
        console.print("[red]最新 session 没有方案文档[/red]")
        sys.exit(1)

    meeting.final_proposal = latest.proposal
    meeting.status = "finalized"
    save_meeting(meeting, BASE_DIR)

    final_path = BASE_DIR / "meetings" / topic_id / "final_proposal.md"
    console.print(f"\n[bold green]✓ 已定稿[/bold green]")
    console.print(f"最终方案：{final_path}")


# ── list ──────────────────────────────────────────────────────────────────────

@cli.command("list")
def list_cmd():
    """List all meetings."""
    meetings = list_meetings(BASE_DIR)
    if not meetings:
        console.print("[dim]暂无议题记录[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=30)
    table.add_column("议题", width=30)
    table.add_column("状态", width=12)
    table.add_column("阶段数", justify="right", width=6)
    table.add_column("创建时间", width=20)

    status_colors = {
        "draft": "yellow",
        "in_progress": "blue",
        "finalized": "green",
    }

    for m in meetings:
        color = status_colors.get(m["status"], "white")
        table.add_row(
            m["topic_id"],
            m["topic"],
            f"[{color}]{m['status']}[/{color}]",
            str(m["session_count"]),
            m["created_at"][:16].replace("T", " "),
        )

    console.print(table)


# ── show ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("topic_id")
@click.option("--proposal", is_flag=True, help="Show latest proposal")
@click.option("--minutes", is_flag=True, help="Show latest minutes")
def show(topic_id, proposal, minutes):
    """Show details of a meeting."""
    config = Config(CONFIG_DIR)
    try:
        meeting = load_meeting(topic_id, BASE_DIR)
    except FileNotFoundError:
        console.print(f"[red]未找到议题: {topic_id}[/red]")
        sys.exit(1)

    console.print(f"\n[bold]议题：[/bold]{meeting.topic}")
    console.print(f"[bold]ID：[/bold]{meeting.topic_id}")
    console.print(f"[bold]状态：[/bold]{meeting.status}")
    console.print(f"[bold]创建：[/bold]{meeting.created_at[:16].replace('T', ' ')}")
    console.print(f"[bold]共 {len(meeting.sessions)} 个阶段[/bold]\n")

    for s in meeting.sessions:
        agent_names = [config.agents[a].name if a in config.agents else a for a in s.agents]
        console.print(
            f"  Session {s.session_index}: [cyan]{s.session_type}[/cyan]"
            f" | {len(s.rounds)} 轮"
            f" | 参会者: {', '.join(agent_names)}"
            f" | 共识: {s.consensus_level or 'N/A'}"
        )

    if proposal and meeting.sessions:
        latest = meeting.sessions[-1]
        if latest.proposal:
            console.print("\n[bold]最新方案：[/bold]")
            console.print(Markdown(latest.proposal))
        else:
            console.print("[dim]暂无方案文档[/dim]")

    if minutes and meeting.sessions:
        latest = meeting.sessions[-1]
        if latest.minutes:
            console.print("\n[bold]最新会议纪要：[/bold]")
            console.print(Markdown(latest.minutes))
        else:
            console.print("[dim]暂无会议纪要[/dim]")


# ── test-round ────────────────────────────────────────────────────────────────

@cli.command("test-round")
@click.argument("topic")
@click.option("--agent", "-a", default="claude-sonnet", help="Agent ID to test")
@click.option("--mode", "-m", default="brainstorm", type=click.Choice(["brainstorm", "review", "decision"]))
def test_round(topic, agent, mode):
    """Test a single agent invocation (verify CLI connectivity)."""
    config = Config(CONFIG_DIR)
    runner = _make_runner(config)

    agent_cfg = config.get_agent(agent)
    template = config.get_template(mode)
    base_prompt = config.prompt("base_system.md")

    from lib.prompt_builder import build_prompt
    prompt = build_prompt(
        template_content=base_prompt,
        agent=agent_cfg,
        topic=topic,
        session_type=mode,
        session_description=template.description,
        round_num=1,
        max_rounds=template.max_rounds,
        round_rule=template.round_rules.get(1, ""),
        agent_list=[agent_cfg.name],
        history=None,
    )

    console.print(f"\n[bold]测试调用：[/bold]{agent_cfg.name}")
    console.print(f"议题：{topic}\n")

    response = runner.invoke_with_retry(agent, prompt)

    if response.success:
        console.print(f"[green]✓ 成功[/green] ({response.duration_seconds:.1f}s)\n")
        console.print(Markdown(response.content))
    else:
        console.print(f"[red]✗ 失败[/red]: {response.error}")
        if response.content:
            console.print(response.content)


# ── interactive ────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("topic")
@click.option("--agents", "-a", default="", help="Comma-separated agent IDs")
@click.option("--strategy", "-s", default="balanced", help="Model strategy")
def interactive(topic, agents, strategy):
    """Run an interactive meeting session."""
    config = Config(CONFIG_DIR)
    runner = _make_runner(config)
    orchestrator = _make_orchestrator(config, runner)
    summarizer = _pick_summarizer(config)

    from datetime import datetime
    topic_id = create_topic_id(topic)
    meeting = Meeting(
        topic_id=topic_id,
        topic=topic,
        created_at=datetime.now().isoformat(),
    )

    session_sequence = ["brainstorm", "review", "decision"]
    session_descriptions = {
        "brainstorm": "发散思维，收集各方观点",
        "review": "对已有方案进行评审和改进",
        "decision": "收敛到最终方案",
    }

    console.print(f"\n[bold green]会议已创建：{topic}[/bold green]")
    console.print(f"ID: {topic_id}\n")

    prior_proposal = ""

    while True:
        current_session_idx = len(meeting.sessions)
        next_type = session_sequence[min(current_session_idx, len(session_sequence) - 1)]

        console.print("\n[bold]请选择操作：[/bold]")
        for i, st in enumerate(session_sequence, 1):
            marker = "→" if st == next_type else " "
            console.print(f"  [{i}] {marker} 开始 {st} — {session_descriptions[st]}")
        console.print("  [q] 退出并定稿")
        console.print("  [s] 查看当前状态")

        choice = click.prompt("\n选择", default="1" if current_session_idx == 0 else "q")

        if choice == "q":
            if meeting.sessions:
                meeting.status = "finalized"
                meeting.final_proposal = meeting.sessions[-1].proposal
                save_meeting(meeting, BASE_DIR)
                console.print(f"\n[green]已定稿，记录保存至 meetings/{topic_id}/[/green]")
            break
        elif choice == "s":
            console.print(f"\n当前阶段数: {len(meeting.sessions)}")
            for s in meeting.sessions:
                console.print(f"  Session {s.session_index}: {s.session_type} ({len(s.rounds)} 轮)")
            continue

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(session_sequence):
                console.print("[red]无效选择[/red]")
                continue
            session_type = session_sequence[idx]
        except ValueError:
            console.print("[red]无效选择[/red]")
            continue

        feedback = ""
        if prior_proposal:
            console.print("\n[dim]（可选）输入补充意见或约束条件（直接回车跳过）：[/dim]")
            feedback = input("> ").strip()

        agent_list = _parse_agents(agents, config, strategy, session_type)
        session = orchestrator.run_session(
            meeting=meeting,
            session_type=session_type,
            agents=agent_list,
            prior_proposal=prior_proposal,
            user_feedback=feedback,
            summarizer_agent=summarizer,
        )
        prior_proposal = session.proposal

        console.print(f"\n[bold]会议纪要：[/bold] meetings/{topic_id}/session_{session.session_index:02d}/minutes.md")
        console.print(f"[bold]方案文档：[/bold] meetings/{topic_id}/session_{session.session_index:02d}/proposal.md")


if __name__ == "__main__":
    cli()
