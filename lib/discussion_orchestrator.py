"""Discussion orchestration for discuss mode (Phase 1-3)."""
from __future__ import annotations

import concurrent.futures
import re
from datetime import datetime
from typing import Callable, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

from .agent_runner import AgentResponse, AgentRunner
from .config import Config
from .meeting import (
    Discussion,
    DiscussionPhase,
    DiscussionRound,
    save_discussion,
)
from .prompt_builder import (
    build_discussion_prompt,
    build_independent_prompt,
    build_moderator_opening_prompt,
    build_synthesis_prompt,
)

console = Console()


class DiscussionOrchestrator:
    """Orchestrates the three-phase discussion flow:
    - Phase 1: Independent opinions (parallel)
    - Phase 2: Moderator-led discussion (sequential rounds)
    - Phase 3: Moderator synthesis (final output)
    """

    def __init__(self, config: Config, base_dir, runner: AgentRunner):
        self.config = config
        self.base_dir = base_dir
        self.runner = runner

    def run_independent_phase(
        self,
        discussion: Discussion,
    ) -> DiscussionPhase:
        """Phase 1: All agents give independent opinions in parallel."""
        console.print("\n[bold cyan]Phase 1: 收集各方观点[/bold cyan]")
        console.print("[dim]所有 AI 独立发表观点...[/dim]\n")

        template = self.config.prompt("independent_opinion.md")

        phase = DiscussionPhase(
            phase_type="independent",
            phase_index=1,
        )
        discussion.phases.append(phase)

        responses: Dict[str, str] = {}

        def invoke_one(agent_id: str) -> tuple[str, AgentResponse]:
            agent_cfg = self.config.get_agent(agent_id)
            prompt = build_independent_prompt(
                template_content=template,
                agent=agent_cfg,
                user_idea=discussion.user_idea,
            )
            response = self.runner.invoke_with_retry(agent_id, prompt)
            return agent_id, response

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            tasks = {
                agent_id: progress.add_task(
                    f"  {self.config.get_agent(agent_id).name} 思考中...", total=None
                )
                for agent_id in discussion.agents
            }

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(discussion.agents)) as executor:
                futures = {executor.submit(invoke_one, aid): aid for aid in discussion.agents}
                for future in concurrent.futures.as_completed(futures):
                    agent_id, response = future.result()
                    agent_name = self.config.get_agent(agent_id).name
                    if response.success:
                        progress.update(
                            tasks[agent_id],
                            description=f"  {agent_name} ✓ ({response.duration_seconds:.1f}s)",
                        )
                        responses[agent_id] = response.content
                    else:
                        progress.update(
                            tasks[agent_id],
                            description=f"  {agent_name} ✗ (失败)",
                        )
                        responses[agent_id] = f"[调用失败: {response.error}]"

        # Create single round for phase 1
        round_data = DiscussionRound(round_num=1, responses=responses)
        phase.rounds.append(round_data)

        save_discussion(discussion, self.base_dir)
        console.print(f"\n[green]✓ Phase 1 完成[/green] ({len(responses)} 个观点)\n")

        return phase

    def select_moderator(self, discussion: Discussion) -> str:
        """Let user select a moderator from the agents."""
        phase1 = discussion.phases[0] if discussion.phases else None
        if not phase1:
            raise ValueError("Phase 1 must be completed before selecting moderator")

        round1 = phase1.rounds[0] if phase1.rounds else None
        if not round1:
            raise ValueError("No responses in Phase 1")

        console.print("[bold]观点摘要：[/bold]\n")

        agent_options = []
        for idx, (agent_id, content) in enumerate(round1.responses.items(), 1):
            agent_cfg = self.config.get_agent(agent_id)
            agent_options.append((agent_id, agent_cfg, content))

            # Extract a brief summary (first paragraph or first 100 chars)
            summary = self._extract_summary(content)

            panel = Panel(
                f"[dim]{summary}[/dim]\n\n"
                f"[cyan]擅长：{agent_cfg.strengths}[/cyan]",
                title=f"[{idx}] {agent_cfg.name}",
                border_style="blue",
            )
            console.print(panel)

        # User selection
        choice = console.input(
            f"\n请选择主持人 [1-{len(agent_options)}]: "
        ).strip()

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(agent_options):
                selected_id = agent_options[idx][0]
                selected_name = agent_options[idx][1].name
                discussion.moderator = selected_id
                console.print(f"\n[green]✓ {selected_name} 被选为主持人[/green]\n")
                return selected_id
            else:
                # Default to first
                selected_id = agent_options[0][0]
                discussion.moderator = selected_id
                console.print(f"\n[yellow]无效选择，默认选择 {agent_options[0][1].name} 作为主持人[/yellow]\n")
                return selected_id
        except ValueError:
            # Default to first
            selected_id = agent_options[0][0]
            discussion.moderator = selected_id
            console.print(f"\n[yellow]无效输入，默认选择 {agent_options[0][1].name} 作为主持人[/yellow]\n")
            return selected_id

    def run_discussion_phase(
        self,
        discussion: Discussion,
        max_rounds: int = 3,
    ) -> DiscussionPhase:
        """Phase 2: Moderator-led multi-round discussion."""
        if not discussion.moderator:
            raise ValueError("Moderator must be selected before discussion phase")

        moderator_cfg = self.config.get_agent(discussion.moderator)

        phase = DiscussionPhase(
            phase_type="discussion",
            phase_index=2,
        )
        discussion.phases.append(phase)

        # Build initial history from Phase 1
        history: List[Dict] = []
        if discussion.phases and discussion.phases[0].rounds:
            phase1_round = discussion.phases[0].rounds[0]
            history.append({
                "round": 1,
                "phase": "独立发言",
                "responses": {
                    self.config.get_agent(aid).name: content
                    for aid, content in phase1_round.responses.items()
                },
            })

        for round_num in range(1, max_rounds + 1):
            console.print(f"\n[bold cyan]Phase 2: 讨论（第 {round_num} 轮 / 最多 {max_rounds} 轮）[/bold cyan]\n")

            # Step 1: Moderator opening
            moderator_opening = self._run_moderator_opening(
                discussion=discussion,
                round_num=round_num,
                max_rounds=max_rounds,
                history=history,
            )

            # Parse convergence signal
            should_conclude = self._parse_convergence_signal(moderator_opening)

            # Display moderator opening
            console.print(f"[bold yellow]🎙 {moderator_cfg.name} 引导：[/bold yellow]")
            console.print(Panel(moderator_opening, border_style="yellow"))

            # Step 2: Other agents respond sequentially
            round_responses = self._run_discussion_round(
                discussion=discussion,
                moderator_opening=moderator_opening,
                history=history,
            )

            # Save round
            discussion_round = DiscussionRound(
                round_num=round_num,
                moderator_opening=moderator_opening,
                responses=round_responses,
            )
            phase.rounds.append(discussion_round)
            save_discussion(discussion, self.base_dir)

            # Add to history
            history.append({
                "round": round_num + 1,  # +1 because Phase 1 was round 1
                "phase": "讨论",
                "responses": {
                    self.config.get_agent(aid).name: content
                    for aid, content in round_responses.items()
                },
            })

            # Show round summary
            console.print(f"\n[dim]本轮 {len(round_responses)} 人发言完成[/dim]\n")

            # User decision point
            if should_conclude:
                console.print("[yellow]💡 主持人建议结束讨论：各方观点已充分表达[/yellow]\n")

            if round_num < max_rounds:
                choice = console.input(
                    "[c] 继续下一轮  [f] 补充意见后继续  [d] 结束讨论\n选择: "
                ).strip().lower()

                if choice == "d":
                    console.print("\n[dim]进入 Phase 3...[/dim]\n")
                    break
                elif choice == "f":
                    feedback = console.input("\n补充意见: ").strip()
                    if feedback:
                        discussion.user_feedbacks.append(f"第{round_num}轮后: {feedback}")
                        console.print("[dim]意见已记录[/dim]\n")

        return phase

    def _run_moderator_opening(
        self,
        discussion: Discussion,
        round_num: int,
        max_rounds: int,
        history: List[Dict],
    ) -> str:
        """Run moderator opening for a round."""
        template = self.config.prompt("moderator_opening.md")
        moderator_cfg = self.config.get_agent(discussion.moderator)

        # Get last user feedback if any
        user_feedback = discussion.user_feedbacks[-1] if discussion.user_feedbacks else ""

        prompt = build_moderator_opening_prompt(
            template_content=template,
            agent=moderator_cfg,
            user_idea=discussion.user_idea,
            round_num=round_num,
            max_rounds=max_rounds,
            history=history,
            user_feedback=user_feedback,
        )

        with console.status(f"[yellow]{moderator_cfg.name} 正在准备开场引导...[/yellow]"):
            response = self.runner.invoke_with_retry(discussion.moderator, prompt)

        return response.content if response.success else "[主持人调用失败，请继续讨论]"

    def _run_discussion_round(
        self,
        discussion: Discussion,
        moderator_opening: str,
        history: List[Dict],
    ) -> Dict[str, str]:
        """Run one round of discussion (non-moderator agents respond)."""
        template = self.config.prompt("discussion_response.md")
        moderator_cfg = self.config.get_agent(discussion.moderator)

        responses: Dict[str, str] = {}
        other_agents = [aid for aid in discussion.agents if aid != discussion.moderator]

        for agent_id in other_agents:
            agent_cfg = self.config.get_agent(agent_id)

            prompt = build_discussion_prompt(
                template_content=template,
                agent=agent_cfg,
                user_idea=discussion.user_idea,
                history=history,
                moderator_name=moderator_cfg.name,
                moderator_opening=moderator_opening,
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task(f"  {agent_cfg.name} 发言中...", total=None)
                response = self.runner.invoke_with_retry(agent_id, prompt)
                progress.update(task, description=f"  {agent_cfg.name} ✓")

            if response.success:
                console.print(f"  [green]✓[/green] {agent_cfg.name} ({response.duration_seconds:.1f}s)")
                responses[agent_id] = response.content
            else:
                console.print(f"  [red]✗[/red] {agent_cfg.name} (失败: {response.error})")
                responses[agent_id] = f"[调用失败: {response.error}]"

        return responses

    def _parse_convergence_signal(self, moderator_opening: str) -> bool:
        """Parse [SUGGEST_CONCLUDE] or [CONTINUE] signal from moderator."""
        if "[SUGGEST_CONCLUDE]" in moderator_opening:
            return True
        return False

    def run_synthesis_phase(self, discussion: Discussion) -> str:
        """Phase 3: Moderator synthesizes final output."""
        console.print("\n[bold cyan]Phase 3: 生成结果文档[/bold cyan]\n")

        if not discussion.moderator:
            raise ValueError("Moderator must be selected")

        moderator_cfg = self.config.get_agent(discussion.moderator)
        template = self.config.prompt("moderator_synthesis.md")

        # Build full history
        full_history: List[Dict] = []

        # Phase 1
        if discussion.phases and discussion.phases[0].rounds:
            phase1 = discussion.phases[0]
            for r in phase1.rounds:
                full_history.append({
                    "round": len(full_history) + 1,
                    "phase": "独立发言",
                    "responses": {
                        self.config.get_agent(aid).name: content
                        for aid, content in r.responses.items()
                    },
                })

        # Phase 2
        if len(discussion.phases) > 1:
            phase2 = discussion.phases[1]
            for r in phase2.rounds:
                round_responses = {
                    self.config.get_agent(aid).name: content
                    for aid, content in r.responses.items()
                }
                # Include moderator opening as part of responses
                if r.moderator_opening:
                    round_responses[f"{moderator_cfg.name}(主持人)"] = r.moderator_opening
                full_history.append({
                    "round": len(full_history) + 1,
                    "phase": "讨论",
                    "responses": round_responses,
                })

        prompt = build_synthesis_prompt(
            template_content=template,
            agent=moderator_cfg,
            user_idea=discussion.user_idea,
            full_history=full_history,
            all_user_feedbacks=discussion.user_feedbacks,
        )

        with console.status(f"[yellow]{moderator_cfg.name} 正在综合各方观点...[/yellow]"):
            response = self.runner.invoke_with_retry(discussion.moderator, prompt)

        if not response.success:
            console.print(f"[red]✗ 生成失败: {response.error}[/red]")
            return ""

        final_output = response.content
        discussion.final_output = final_output
        discussion.status = "finalized"

        # Create synthesis phase
        phase = DiscussionPhase(
            phase_type="synthesis",
            phase_index=3,
        )
        phase.rounds.append(DiscussionRound(round_num=1, responses={"output": final_output}))
        discussion.phases.append(phase)

        save_discussion(discussion, self.base_dir)

        console.print(f"[green]✓ 完成[/green]\n")
        return final_output

    def _extract_summary(self, content: str, max_len: int = 150) -> str:
        """Extract a brief summary from agent response."""
        # Try to get content after ### 整体评价
        match = re.search(r"###\s*整体评价\s*\n(.+?)(?:\n###|\Z)", content, re.DOTALL)
        if match:
            summary = match.group(1).strip()
        else:
            # Just take first paragraph
            summary = content.strip().split("\n\n")[0][:max_len]

        if len(summary) > max_len:
            summary = summary[:max_len] + "..."
        return summary
