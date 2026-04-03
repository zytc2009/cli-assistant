#!/usr/bin/env python3
"""Multi-AI Discussion Orchestrator — council CLI."""
from __future__ import annotations

import io
import sys
from pathlib import Path

# Force UTF-8 on Windows to avoid GBK encoding errors with unicode characters
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# On Windows, set CLAUDE_CODE_GIT_BASH_PATH so claude CLI can find bash
import os
import shutil

if sys.platform == "win32" and "CLAUDE_CODE_GIT_BASH_PATH" not in os.environ:
    bash_path = shutil.which("bash")
    if bash_path:
        os.environ["CLAUDE_CODE_GIT_BASH_PATH"] = bash_path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

# Ensure lib is importable
sys.path.insert(0, str(Path(__file__).parent))

from lib.agent_runner import AgentRunner
from lib.config import Config
from lib.discussion_orchestrator import DiscussionOrchestrator
from lib.meeting import (
    Discussion,
    Meeting,
    create_topic_id,
    list_meetings,
    load_discussion,
    load_meeting,
    save_discussion,
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


def _make_discussion_orchestrator(config: Config, runner: AgentRunner) -> DiscussionOrchestrator:
    summarizer = _pick_summarizer(config)
    return DiscussionOrchestrator(config=config, base_dir=BASE_DIR, runner=runner, summarizer_agent=summarizer)


def _split_agents(value: str) -> list[str]:
    """Split agent string by English or Chinese comma."""
    return [v.strip() for v in value.replace("，", ",").split(",") if v.strip()]


def _parse_agents(agents_str: str, config: Config, strategy: str, session_type: str) -> list[str]:
    if agents_str:
        agent_list = _split_agents(agents_str)
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


# ── Interactive Wizard Helper Functions ───────────────────────────────────────

def _input_idea() -> str:
    """Collect user idea with multi-line input.

    User types lines and ends with an empty line.
    """
    console.print("\n[bold cyan][第1步][/bold cyan] 请输入您的问题/想法（直接回车结束输入）：")
    console.print("[dim]提示：输入多行内容，空行表示结束\n[/dim]")

    lines = []
    line_num = 1
    while True:
        prompt = f"> "
        try:
            line = console.input(prompt)
            if line.strip() == "":
                # Empty line ends input
                if lines:
                    break
                else:
                    console.print("[yellow]请输入内容，或输入空行两次退出[/yellow]")
                    continue
            lines.append(line)
            line_num += 1
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]输入取消[/yellow]")
            return ""

    return "\n".join(lines)


def _select_clis(available_clis: list) -> list[str]:
    """Let user select which CLIs to participate in discussion.

    Args:
        available_clis: List of CLIDetected objects

    Returns:
        List of selected CLI IDs
    """
    from lib.cli_detector import format_cli_status

    installed = [cli for cli in available_clis if cli.is_installed]

    if not installed:
        console.print("[red]错误：没有检测到可用的 AI CLI[/red]")
        return []

    console.print(f"\n[bold cyan][第3步][/bold cyan] 选择参与讨论的 AI（输入编号，多个用逗号分隔）：")

    for i, cli in enumerate(installed, 1):
        status = format_cli_status(cli)
        console.print(f"  [{i}] {status}")

    while True:
        choice = console.input("\n选择: ").strip()

        if not choice:
            # Default to all
            return [cli.cli_id for cli in installed]

        try:
            indices = [int(x.strip()) for x in choice.replace("，", ",").split(",")]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(installed):
                    selected.append(installed[idx - 1].cli_id)
                else:
                    console.print(f"[red]无效编号: {idx}[/red]")
                    break
            else:
                if selected:
                    return selected
        except ValueError:
            console.print("[red]请输入数字编号，用逗号分隔[/red]")


def _select_moderator(selected_agents: list[str], config: Config) -> str:
    """Let user select a moderator from selected agents.

    Args:
        selected_agents: List of selected agent IDs
        config: Config object with agent info

    Returns:
        Selected moderator agent ID
    """
    console.print(f"\n[bold cyan][第4步][/bold cyan] 选择主持人：")

    agents_info = []
    for i, agent_id in enumerate(selected_agents, 1):
        if agent_id in config.agents:
            agent = config.agents[agent_id]
        else:
            # For CLI-only agents not in config, create minimal info
            agent = type('obj', (object,), {
                'name': agent_id,
                'strengths': '通用能力'
            })()
        agents_info.append((agent_id, agent))
        console.print(f"  [{i}] {agent.name} - 擅长：{agent.strengths}")

    while True:
        choice = console.input("\n选择: ").strip()

        try:
            idx = int(choice)
            if 1 <= idx <= len(agents_info):
                selected_id = agents_info[idx - 1][0]
                agent_name = agents_info[idx - 1][1].name
                console.print(f"[green]✓ {agent_name} 被选为主持人[/green]\n")
                return selected_id
            else:
                console.print(f"[red]请输入 1-{len(agents_info)} 之间的数字[/red]")
        except ValueError:
            console.print("[red]请输入数字编号[/red]")


def _confirm_config() -> dict:
    """Let user confirm discussion configuration.

    Returns:
        Dict with configuration options
    """
    console.print(f"\n[bold cyan][第5步][/bold cyan] 讨论配置：")

    max_rounds_str = console.input("  最大轮次 [3]: ").strip()
    max_rounds = int(max_rounds_str) if max_rounds_str.isdigit() else 3

    config = {
        "max_rounds": max(max_rounds, 1),
    }

    return config


def _input_manual_cli() -> Optional[tuple]:
    """Prompt user to manually input a CLI configuration.

    Returns:
        Tuple of (cli_id, name, command, strengths) or None if cancelled
    """
    console.print("\n[yellow]未检测到任何 AI CLI，请手动配置[/yellow]")
    console.print("[dim]提示：输入命令名称和路径，或按 Ctrl+C 取消\n[/dim]")

    try:
        cli_id = console.input("CLI ID (如 claude/codex/kimi): ").strip()
        if not cli_id:
            return None

        name = console.input(f"显示名称 [{cli_id}]: ").strip() or cli_id

        console.print("命令模板（使用 {prompt_file} 作为 prompt 文件占位符）:")
        console.print("  示例: claude -p \"{prompt_file}\" --output-format text")
        while True:
            command = console.input("命令: ").strip()
            if not command:
                return None
            if "{prompt_file}" not in command and "{prompt_content}" not in command:
                console.print("[red]错误：命令必须包含 {prompt_file} 或 {prompt_content} 占位符[/red]")
                continue
            break

        strengths = console.input("擅长领域 [通用能力]: ").strip() or "通用能力"

        # Verify the command exists
        import shutil
        cmd_first = command.split()[0]
        if shutil.which(cmd_first) is None:
            console.print(f"[red]警告：命令 '{cmd_first}' 不在 PATH 中[/red]")
            confirm = console.input("是否仍要添加? [y/N]: ").strip().lower()
            if confirm != 'y':
                return None

        return (cli_id, name, command, strengths)

    except (KeyboardInterrupt, EOFError):
        return None


def _run_interactive_wizard():
    """Run the interactive wizard when no command is provided."""
    from datetime import datetime
    from lib.cli_detector import (
        CLIDetector,
        add_custom_cli_to_config,
        format_cli_status,
        save_detected_clis_to_config,
    )
    from lib.streaming_runner import StreamingRunner

    # Welcome header
    console.print("\n[bold green]══════════════════════════════════════════════════════[/bold green]")
    console.print("[bold green]  🤖 Multi-AI Discussion Council[/bold green]")
    console.print("[bold green]══════════════════════════════════════════════════════[/bold green]")

    # Step 1: Input idea
    user_idea = _input_idea()
    if not user_idea:
        console.print("[yellow]未输入内容，退出[/yellow]")
        return

    # Step 2: Detect CLIs
    console.print("\n[bold cyan][第2步][/bold cyan] 检测本地可用的 AI CLI...\n")
    detector = CLIDetector()
    all_clis = detector.detect_all()

    for cli in all_clis:
        console.print(f"  {format_cli_status(cli)}")

    installed = [cli for cli in all_clis if cli.is_installed]

    # Fallback: manual input if no CLIs detected
    if not installed:
        console.print("\n[yellow]未检测到已知的 AI CLI[/yellow]")
        manual_cli = _input_manual_cli()
        if manual_cli:
            cli_id, name, command, strengths = manual_cli
            # Add to agents.yaml
            agents_yaml_path = CONFIG_DIR / "agents.yaml"
            if add_custom_cli_to_config(cli_id, name, command, strengths, agents_yaml_path):
                console.print(f"[green]✓ 已添加 {name} 到配置[/green]\n")
                # Create a CLIDetected object for the manual CLI
                from lib.cli_detector import CLIDetected
                installed = [CLIDetected(
                    cli_id=cli_id,
                    name=name,
                    version="",
                    is_installed=True,
                    command=command,
                    check_cmd="",
                    strengths=strengths,
                )]
            else:
                console.print("[red]添加配置失败[/red]")
                return
        else:
            console.print("[yellow]未配置任何 CLI，退出[/yellow]")
            return

    # Save detected CLIs to config
    agents_yaml_path = CONFIG_DIR / "agents.yaml"
    try:
        save_detected_clis_to_config(installed, agents_yaml_path)
        console.print(f"[dim]已更新配置: {agents_yaml_path}\n[/dim]")
    except Exception as e:
        console.print(f"[dim yellow]保存配置警告: {e}[/dim yellow]")

    # Step 3: Select CLIs
    selected_ids = _select_clis(all_clis)
    if not selected_ids:
        return

    # Load config and update with detected CLIs
    config = Config(CONFIG_DIR)

    # Add detected CLIs to config if not already present
    for cli in installed:
        if cli.cli_id not in config.agents:
            # Create a temporary agent config
            from lib.config import AgentConfig
            config.agents[cli.cli_id] = AgentConfig(
                name=cli.name,
                cli=cli.cli_id,
                command=cli.command,
                prompt_method="file",
                max_tokens=4000,
                timeout=120,
                strengths=cli.strengths,
                cost_tier="medium",
            )

    # Validate selected agents
    valid_agents = []
    for aid in selected_ids:
        if aid in config.agents:
            valid_agents.append(aid)
        else:
            console.print(f"[yellow]警告：{aid} 未配置，已跳过[/yellow]")

    if not valid_agents:
        console.print("[red]错误：没有有效的 AI 可以参与讨论[/red]")
        return

    # Step 4: Select moderator
    moderator_id = _select_moderator(valid_agents, config)

    # Step 5: Confirm config
    disc_config = _confirm_config()

    # Create discussion
    topic_id = create_topic_id(user_idea[:50])
    discussion = Discussion(
        topic_id=topic_id,
        user_idea=user_idea,
        created_at=datetime.now().isoformat(),
        agents=valid_agents,
        moderator=moderator_id,
    )

    # Show start header
    console.print("\n[bold green]══════════════════════════════════════════════════════[/bold green]")
    console.print("[bold green]  讨论开始[/bold green]")
    console.print("[bold green]══════════════════════════════════════════════════════[/bold green]\n")

    # Initialize streaming runner
    streaming_runner = StreamingRunner(config.agents)

    # Initialize orchestrator (for non-streaming methods if needed)
    orchestrator = _make_discussion_orchestrator(config, _make_runner(config))

    # Phase 1: Independent opinions with streaming
    try:
        orchestrator.run_independent_phase_streaming(discussion, streaming_runner)
    except Exception as e:
        console.print(f"[red]Phase 1 执行失败: {e}[/red]")
        import traceback
        traceback.print_exc()
        return

    # Optional user feedback before discussion
    console.print("[dim]（可选）补充意见或约束（直接回车跳过）:[/dim]")
    feedback = console.input("> ").strip()
    if feedback:
        discussion.user_feedbacks.append(f"讨论前: {feedback}")
        console.print()

    # Phase 2: Discussion with streaming
    try:
        orchestrator.run_discussion_phase_streaming(
            discussion,
            streaming_runner,
            max_rounds=disc_config["max_rounds"],
        )
    except Exception as e:
        console.print(f"[red]Phase 2 执行失败: {e}[/red]")
        import traceback
        traceback.print_exc()
        # Save current state before exit
        from lib.meeting import save_discussion
        save_discussion(discussion, Path("meetings"))
        console.print("[yellow]当前讨论状态已保存[/yellow]")
        return

    # Phase 3: Synthesis with streaming
    try:
        final_output = orchestrator.run_synthesis_phase_streaming(discussion, streaming_runner)
    except Exception as e:
        console.print(f"[red]Phase 3 执行失败: {e}[/red]")
        # Save current state before exit
        from lib.meeting import save_discussion
        save_discussion(discussion, Path("meetings"))
        console.print("[yellow]当前讨论状态已保存[/yellow]")
        raise

    # Show completion
    console.print("[bold green]══════════════════════════════════════════════════════[/bold green]")
    console.print("[bold green]  讨论完成[/bold green]")
    console.print("[bold green]══════════════════════════════════════════════════════[/bold green]\n")

    # Show result
    console.print("[bold]结果已保存至：[/bold]")
    console.print(f"  meetings/{topic_id}/final_output.md\n")

    # Preview
    if final_output:
        console.print(Markdown(final_output[:2000] + "..." if len(final_output) > 2000 else final_output))
    else:
        console.print("[yellow]警告: 最终输出为空[/yellow]")


# ── CLI Group ─────────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.pass_context
@click.version_option("0.1.0", prog_name="council")
def cli(ctx):
    """Multi-AI Discussion Orchestrator — conduct structured AI meetings."""
    if ctx.invoked_subcommand is None:
        # No command provided, run interactive wizard
        _run_interactive_wizard()


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

        # Override max_rounds if specified — use dataclasses.replace() to avoid mutating shared object
        if rounds > 0:
            import dataclasses
            template = config.get_template(session_type)
            config.templates[session_type] = dataclasses.replace(template, max_rounds=rounds)

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


def _continue_discussion_synthesis(discussion, config):
    """Continue a discuss-mode discussion by running Phase 3 synthesis."""
    from lib.discussion_orchestrator import DiscussionOrchestrator
    from lib.streaming_runner import StreamingRunner
    from lib.meeting import save_discussion
    from lib.agent_runner import AgentRunner

    console.print(f"\n[bold green]继续讨论: {discussion.user_idea[:50]}...[/bold green]")
    console.print(f"[dim]当前状态: {discussion.status}, 已完成 {len(discussion.phases)} 个阶段[/dim]\n")

    # Check if already finalized
    if discussion.status == "finalized" and discussion.final_output:
        console.print("[yellow]该讨论已完成，最终输出已存在[/yellow]")
        console.print(f"\n[bold]最终结果:[/bold]\n{discussion.final_output[:500]}...")
        return

    # Check if there's Phase 2 data to synthesize
    if len(discussion.phases) < 2:
        console.print("[red]错误: 没有 Phase 2 讨论数据，无法生成最终结果[/red]")
        return

    # Initialize streaming runner and orchestrator
    streaming_runner = StreamingRunner(config.agents)
    orchestrator = DiscussionOrchestrator(
        config=config,
        base_dir=BASE_DIR,
        runner=AgentRunner(config.agents),
    )

    try:
        final_output = orchestrator.run_synthesis_phase_streaming(discussion, streaming_runner)

        console.print("\n[bold green]══════════════════════════════════════════════════════[/bold green]")
        console.print("[bold green]  讨论完成[/bold green]")
        console.print("[bold green]══════════════════════════════════════════════════════[/bold green]\n")

        console.print("[bold]最终结果已保存[/bold]")
        if final_output:
            from rich.markdown import Markdown
            console.print(Markdown(final_output[:2000] + "..." if len(final_output) > 2000 else final_output))
    except Exception as e:
        console.print(f"[red]Phase 3 执行失败: {e}[/red]")
        import traceback
        traceback.print_exc()
        # Save current state
        save_discussion(discussion, BASE_DIR)
        console.print("[yellow]当前状态已保存[/yellow]")


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

    # Check if it's a Discussion (discuss mode) or Meeting (traditional mode)
    from lib.meeting import load_discussion
    try:
        discussion = load_discussion(topic_id, BASE_DIR)
        # It's a discuss mode discussion - run synthesis phase
        _continue_discussion_synthesis(discussion, config)
        return
    except (FileNotFoundError, ValueError):
        # Not a discussion, try loading as traditional meeting
        pass

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
    table.add_column("ID", style="dim", width=25)
    table.add_column("类型", width=8)
    table.add_column("议题/想法", width=25)
    table.add_column("状态", width=12)
    table.add_column("阶段数", justify="right", width=6)
    table.add_column("创建时间", width=16)

    status_colors = {
        "draft": "yellow",
        "in_progress": "blue",
        "finalized": "green",
    }
    mode_labels = {
        "discuss": "[讨论]",
        "meeting": "[会议]",
    }

    for m in meetings:
        color = status_colors.get(m["status"], "white")
        mode_label = mode_labels.get(m.get("mode", "meeting"), "")
        topic_display = m["topic"][:22] + "..." if len(m["topic"]) > 25 else m["topic"]
        table.add_row(
            m["topic_id"],
            mode_label,
            topic_display,
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
@click.option("--output", is_flag=True, help="Show final output (for discuss mode)")
def show(topic_id, proposal, minutes, output):
    """Show details of a meeting or discussion."""
    config = Config(CONFIG_DIR)

    # Try to load as discussion first
    try:
        discussion = load_discussion(topic_id, BASE_DIR)
        _show_discussion(discussion, config, output)
        return
    except (FileNotFoundError, ValueError):
        pass

    # Try to load as meeting
    try:
        meeting = load_meeting(topic_id, BASE_DIR)
        _show_meeting(meeting, config, proposal, minutes)
    except FileNotFoundError:
        console.print(f"[red]未找到议题: {topic_id}[/red]")
        sys.exit(1)


def _show_discussion(discussion, config, show_output):
    """Display discussion details."""
    console.print(f"\n[bold]想法：[/bold]{discussion.user_idea[:60]}{'...' if len(discussion.user_idea) > 60 else ''}")
    console.print(f"[bold]ID：[/bold]{discussion.topic_id}")
    console.print(f"[bold]类型：[/bold][讨论模式]")
    console.print(f"[bold]状态：[/bold]{discussion.status}")

    if discussion.moderator:
        mod_name = config.get_agent(discussion.moderator).name if discussion.moderator in config.agents else discussion.moderator
        console.print(f"[bold]主持人：[/bold]{mod_name}")

    console.print(f"[bold]创建：[/bold]{discussion.created_at[:16].replace('T', ' ')}")
    console.print(f"[bold]共 {len(discussion.phases)} 个阶段[/bold]\n")

    for p in discussion.phases:
        phase_names = {
            "independent": "独立发言",
            "discussion": "讨论",
            "synthesis": "综合输出",
        }
        phase_name = phase_names.get(p.phase_type, p.phase_type)
        console.print(f"  Phase {p.phase_index}: [cyan]{phase_name}[/cyan] | {len(p.rounds)} 轮")

    if discussion.user_feedbacks:
        console.print(f"\n[bold]用户反馈：[/bold] {len(discussion.user_feedbacks)} 条")

    if show_output and discussion.final_output:
        console.print("\n[bold]最终输出：[/bold]")
        console.print(Markdown(discussion.final_output))


def _show_meeting(meeting, config, proposal, minutes):
    """Display meeting details."""
    console.print(f"\n[bold]议题：[/bold]{meeting.topic}")
    console.print(f"[bold]ID：[/bold]{meeting.topic_id}")
    console.print(f"[bold]类型：[/bold][会议模式]")
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
            feedback = console.input("> ").strip()

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


# ── discuss ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("idea")
@click.option("--agents", "-a", default="", help="Comma-separated agent IDs")
@click.option("--rounds", "-r", default=3, type=int, help="Max discussion rounds")
@click.option("--moderator", "-m", default="", help="Skip selection, specify moderator directly")
def discuss(idea, agents, rounds, moderator):
    """Start a discussion meeting on an IDEA (Phase 1-3 flow)."""
    from datetime import datetime

    config = Config(CONFIG_DIR)
    runner = _make_runner(config)
    orchestrator = _make_discussion_orchestrator(config, runner)

    # Parse agents
    if agents:
        agent_list = _split_agents(agents)
        for a in agent_list:
            config.get_agent(a)  # validate
    else:
        agent_list = list(config.agents.keys())

    if not agent_list:
        console.print("[red]错误：没有可用的 agent，请先配置 agents.yaml 或使用 --agents 指定参与者[/red]")
        sys.exit(1)

    topic_id = create_topic_id(idea)

    console.print(f"\n[bold green]══════════════════════════════════════════════════════[/bold green]")
    console.print(f"[bold green]  新讨论：{idea[:40]}{'...' if len(idea) > 40 else ''}[/bold green]")
    console.print(f"[bold green]  ID: {topic_id}[/bold green]")
    console.print(f"[bold green]══════════════════════════════════════════════════════[/bold green]\n")

    # Create discussion
    discussion = Discussion(
        topic_id=topic_id,
        user_idea=idea,
        created_at=datetime.now().isoformat(),
        agents=agent_list,
    )

    # Phase 1: Independent opinions
    orchestrator.run_independent_phase(discussion)

    # Select moderator
    if moderator:
        # Validate and use specified moderator
        config.get_agent(moderator)
        discussion.moderator = moderator
        console.print(f"[green]✓ {config.get_agent(moderator).name} 被指定为主持人[/green]\n")
    else:
        orchestrator.select_moderator(discussion)

    # Optional user feedback before discussion
    console.print("[dim]（可选）补充意见或约束（直接回车跳过）:[/dim]")
    feedback = console.input("> ").strip()
    if feedback:
        discussion.user_feedbacks.append(f"讨论前: {feedback}")
        console.print()

    # Phase 2: Discussion
    orchestrator.run_discussion_phase(discussion, max_rounds=rounds)

    # Phase 3: Synthesis
    final_output = orchestrator.run_synthesis_phase(discussion)

    # Show result
    console.print("[bold]结果文档已保存至：[/bold]")
    console.print(f"  meetings/{topic_id}/final_output.md\n")

    # Preview
    console.print(Markdown(final_output[:1500] + "..." if len(final_output) > 1500 else final_output))


# ── agent command group ─────────────────────────────────────────────────────────

@cli.group("agent")
def agent_cmd():
    """Manage AI CLI agents configuration."""
    pass


@agent_cmd.command("detect")
@click.option("--save", is_flag=True, help="Save detected CLIs to agents.yaml")
def agent_detect(save):
    """Auto-detect locally installed AI CLIs."""
    from lib.cli_detector import CLIDetector, format_cli_status, save_detected_clis_to_config

    console.print("\n[bold]检测本地 AI CLI...[/bold]\n")

    detector = CLIDetector()
    all_clis = detector.detect_all()

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("CLI", width=12)
    table.add_column("名称", width=20)
    table.add_column("状态", width=12)
    table.add_column("版本", width=12)
    table.add_column("擅长领域", width=25)

    for cli in all_clis:
        status = "[green]✓ 已安装[/green]" if cli.is_installed else "[red]✗ 未安装[/red]"
        version = cli.version or "-"
        table.add_row(cli.cli_id, cli.name, status, version, cli.strengths)

    console.print(table)

    installed = [cli for cli in all_clis if cli.is_installed]
    if installed:
        console.print(f"\n[green]检测到 {len(installed)} 个已安装 CLI[/green]")
        if save:
            agents_yaml_path = CONFIG_DIR / "agents.yaml"
            save_detected_clis_to_config(installed, agents_yaml_path)
            console.print(f"[green]✓ 已保存到 {agents_yaml_path}[/green]")
    else:
        console.print("\n[yellow]未检测到已安装的 AI CLI[/yellow]")


@agent_cmd.command("list")
def agent_list():
    """List currently configured agents."""
    config = Config(CONFIG_DIR)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Agent ID", width=15)
    table.add_column("名称", width=20)
    table.add_column("CLI", width=12)
    table.add_column("模型", width=18)
    table.add_column("成本", width=8)
    table.add_column("超时", justify="right", width=6)

    cost_colors = {
        "high": "red",
        "medium": "yellow",
        "low": "green",
    }

    for agent_id, agent in config.agents.items():
        cost_color = cost_colors.get(agent.cost_tier, "white")
        table.add_row(
            agent_id,
            agent.name,
            agent.cli,
            agent.model or "-",
            f"[{cost_color}]{agent.cost_tier}[/{cost_color}]",
            f"{agent.timeout}s",
        )

    console.print(table)
    console.print(f"\n共 {len(config.agents)} 个配置")


@agent_cmd.command("add")
@click.argument("cli_id")
def agent_add(cli_id):
    """Add a CLI to agents.yaml interactively."""
    from lib.cli_detector import CLIDetector, add_custom_cli_to_config

    detector = CLIDetector()

    # Check if it's a known CLI
    if cli_id in detector.KNOWN_CLIS:
        info = detector.KNOWN_CLIS[cli_id]
        detected = detector.detect_one(cli_id)

        console.print(f"\n[bold]添加已知 CLI: {info['name']}[/bold]")
        console.print(f"命令: {info['command']}")
        console.print(f"擅长: {info['strengths']}")

        # Allow customization
        name = console.input(f"显示名称 [{info['name']}]: ").strip() or info['name']
        command = console.input(f"命令 [{info['command']}]: ").strip() or info['command']
        strengths = console.input(f"擅长领域 [{info['strengths']}]: ").strip() or info['strengths']

        # Check if command exists
        import shutil
        cmd_first = command.split()[0]
        if shutil.which(cmd_first) is None:
            console.print(f"[yellow]警告: '{cmd_first}' 不在 PATH 中[/yellow]")
            if not click.confirm("仍要添加?"):
                return
    else:
        console.print(f"\n[bold]添加自定义 CLI: {cli_id}[/bold]")
        name = console.input("显示名称: ").strip()
        if not name:
            console.print("[red]名称不能为空[/red]")
            return

        console.print("命令模板（使用 {prompt_file} 作为 prompt 文件占位符）:")
        while True:
            command = console.input("命令: ").strip()
            if not command:
                console.print("[red]命令不能为空[/red]")
                return
            if "{prompt_file}" not in command and "{prompt_content}" not in command:
                console.print("[red]错误：命令必须包含 {prompt_file} 或 {prompt_content} 占位符[/red]")
                continue
            break

        strengths = console.input("擅长领域 [通用能力]: ").strip() or "通用能力"

    # Add to config
    agents_yaml_path = CONFIG_DIR / "agents.yaml"
    if add_custom_cli_to_config(cli_id, name, command, strengths, agents_yaml_path):
        console.print(f"[green]✓ 已添加 {name} 到 agents.yaml[/green]")
    else:
        console.print("[red]添加失败[/red]")


@agent_cmd.command("remove")
@click.argument("agent_id")
def agent_remove(agent_id):
    """Remove an agent from agents.yaml."""
    import yaml

    agents_yaml_path = CONFIG_DIR / "agents.yaml"

    # Load existing config
    with open(agents_yaml_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if "agents" not in config or agent_id not in config["agents"]:
        console.print(f"[red]Agent '{agent_id}' 不存在[/red]")
        return

    agent_name = config["agents"][agent_id].get("name", agent_id)

    if not click.confirm(f"确认删除 '{agent_name}' ({agent_id})?"):
        console.print("[dim]已取消[/dim]")
        return

    # Remove agent
    del config["agents"][agent_id]

    # Save back
    with open(agents_yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

    console.print(f"[green]✓ 已删除 {agent_name}[/green]")


if __name__ == "__main__":
    cli()
