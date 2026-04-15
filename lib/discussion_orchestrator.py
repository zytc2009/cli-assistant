"""Discussion orchestration for discuss mode (Phase 1-3)."""
from __future__ import annotations

import concurrent.futures
import re
from typing import Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .agent_runner import AgentResponse, AgentRunner
from .config import Config
from .consensus import ConsensusResult, detect_consensus
from .context import compress_history
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

    # Map default discussion-mode prompt names to requirement-mode variants.
    _REQUIREMENT_PROMPT_MAP = {
        "independent_opinion.md": "requirement_independent.md",
        "moderator_opening.md": "requirement_moderator_opening.md",
        "discussion_response.md": "requirement_discussion_response.md",
        "moderator_synthesis.md": "requirement_synthesis.md",
    }
    _REQUIREMENT_SAFETY_MAX_ROUNDS = 12

    def __init__(self, config: Config, base_dir, runner: AgentRunner, summarizer_agent: str = "claude-sonnet"):
        self.config = config
        self.base_dir = base_dir
        self.runner = runner
        self.summarizer_agent = summarizer_agent

    def _prompt_for(self, discussion: Discussion, default_name: str) -> str:
        """Load the prompt template appropriate for the discussion's flow."""
        if discussion.flow == "requirement":
            name = self._REQUIREMENT_PROMPT_MAP.get(default_name, default_name)
        else:
            name = default_name
        return self.config.prompt(name)

    def _discussion_participants(self, discussion: Discussion) -> List[str]:
        """Return agents who should respond in Phase 2 for the current flow."""
        other_agents = [aid for aid in discussion.agents if aid != discussion.moderator]
        if other_agents:
            return other_agents
        if discussion.flow == "requirement" and discussion.moderator:
            return [discussion.moderator]
        return []

    # ── Phase 1 ──────────────────────────────────────────────────────────────

    def run_independent_phase(
        self,
        discussion: Discussion,
        streaming_runner=None,
    ) -> DiscussionPhase:
        """Phase 1: All agents give independent opinions.

        When streaming_runner is provided, agents are invoked sequentially with
        real-time output.  Without it, agents run in parallel via AgentRunner.
        """
        if discussion.flow == "requirement":
            console.print("\n[bold cyan]Phase 1: 梳理需求焦点[/bold cyan]")
            console.print("[dim]各 AI 先列出已知信息、缺口和待澄清问题...[/dim]\n")
        else:
            console.print("\n[bold cyan]Phase 1: 收集各方观点[/bold cyan]")
            console.print("[dim]所有 AI 独立发表观点...[/dim]\n")

        template = self._prompt_for(discussion, "independent_opinion.md")

        phase = DiscussionPhase(
            phase_type="independent",
            phase_index=1,
        )
        discussion.phases.append(phase)

        responses: Dict[str, str] = {}

        if streaming_runner is not None:
            # Sequential execution with streaming output
            for agent_id in discussion.agents:
                agent_cfg = self.config.get_agent(agent_id)
                prompt = build_independent_prompt(
                    template_content=template,
                    agent=agent_cfg,
                    user_idea=discussion.user_idea,
                )
                response = streaming_runner.invoke_with_retry_streaming(
                    agent_name=agent_id,
                    prompt_content=prompt,
                    show_header=True,
                )
                if response.success:
                    responses[agent_id] = response.content
                else:
                    responses[agent_id] = f"[调用失败: {response.error}]"
        else:
            # Parallel execution via AgentRunner
            def invoke_one(agent_id: str):
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

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(discussion.agents)
                ) as executor:
                    futures = {
                        executor.submit(invoke_one, aid): aid
                        for aid in discussion.agents
                    }
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

    def run_independent_phase_streaming(
        self,
        discussion: Discussion,
        streaming_runner,
    ) -> DiscussionPhase:
        """Phase 1 with streaming output (calls unified method)."""
        return self.run_independent_phase(discussion, streaming_runner=streaming_runner)

    # ── Moderator selection ───────────────────────────────────────────────────

    def select_moderator(self, discussion: Discussion) -> str:
        """Let user select a moderator from the agents."""
        if not discussion.phases:
            raise ValueError("Phase 1 must be completed before selecting moderator")
        phase1 = discussion.phases[0]
        if not phase1.rounds:
            raise ValueError("No responses in Phase 1")

        round1 = phase1.rounds[0]

        console.print("[bold]观点摘要：[/bold]\n")

        agent_options = []
        for idx, (agent_id, content) in enumerate(round1.responses.items(), 1):
            agent_cfg = self.config.get_agent(agent_id)
            agent_options.append((agent_id, agent_cfg, content))

            summary = self._extract_summary(content)

            panel = Panel(
                f"[dim]{summary}[/dim]\n\n"
                f"[cyan]擅长：{agent_cfg.strengths}[/cyan]",
                title=f"[{idx}] {agent_cfg.name}",
                border_style="blue",
            )
            console.print(panel)

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
                selected_id = agent_options[0][0]
                discussion.moderator = selected_id
                console.print(
                    f"\n[yellow]无效选择，默认选择 {agent_options[0][1].name} 作为主持人[/yellow]\n"
                )
                return selected_id
        except ValueError:
            selected_id = agent_options[0][0]
            discussion.moderator = selected_id
            console.print(
                f"\n[yellow]无效输入，默认选择 {agent_options[0][1].name} 作为主持人[/yellow]\n"
            )
            return selected_id

    # ── Phase 2 ──────────────────────────────────────────────────────────────

    def run_discussion_phase(
        self,
        discussion: Discussion,
        max_rounds: int = 3,
        streaming_runner=None,
    ) -> DiscussionPhase:
        """Phase 2: Moderator-led multi-round discussion.

        When streaming_runner is provided, agent invocations use streaming output.
        """
        if not discussion.moderator:
            raise ValueError("Moderator must be selected before discussion phase")

        effective_max_rounds = (
            self._REQUIREMENT_SAFETY_MAX_ROUNDS
            if discussion.flow == "requirement"
            else max_rounds
        )
        moderator_cfg = self.config.get_agent(discussion.moderator)

        phase = DiscussionPhase(
            phase_type="discussion",
            phase_index=2,
        )
        discussion.phases.append(phase)

        # Build initial history from Phase 1 (bounds-checked)
        history: List[Dict] = []
        if discussion.phases and len(discussion.phases) >= 1:
            phase1 = discussion.phases[0]
            if phase1.rounds:
                phase1_round = phase1.rounds[0]
                history.append({
                    "round": 1,
                    "phase": "独立发言",
                    "responses": {
                        self.config.get_agent(aid).name: content
                        for aid, content in phase1_round.responses.items()
                    },
                })

        for round_num in range(1, effective_max_rounds + 1):
            if discussion.flow == "requirement":
                console.print(f"\n[bold cyan]Phase 2: 需求澄清（第 {round_num} 轮）[/bold cyan]\n")
            else:
                console.print(
                    f"\n[bold cyan]Phase 2: 讨论（第 {round_num} 轮 / 最多 {effective_max_rounds} 轮）[/bold cyan]\n"
                )

            participants = self._discussion_participants(discussion)
            if not participants:
                console.print("[yellow]警告：没有其他 Agent 参与讨论（主持人为唯一参与者）[/yellow]")
                break

            # Step 1: Moderator opening
            console.print("[dim]主持人准备开场...[/dim]")
            moderator_opening = self._run_moderator_opening(
                discussion=discussion,
                round_num=round_num,
                max_rounds=effective_max_rounds,
                history=history,
                streaming_runner=streaming_runner,
            )

            # Parse convergence signal
            should_conclude = self._parse_convergence_signal(moderator_opening)

            # Display moderator opening
            console.print(f"[bold yellow]🎙 {moderator_cfg.name} 引导：[/bold yellow]")
            console.print(Panel(moderator_opening, border_style="yellow"))

            # Step 2: Other agents respond sequentially
            console.print(f"[dim]开始讨论轮次，参与者: {participants}[/dim]")
            round_responses = self._run_discussion_round(
                discussion=discussion,
                moderator_opening=moderator_opening,
                history=history,
                streaming_runner=streaming_runner,
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

            # Compress history if it gets too long to save tokens
            try:
                history = compress_history(
                    rounds=history,
                    runner=self.runner,
                    summarizer_agent=self.summarizer_agent,
                    summarizer_prompt_template=self.config.prompt("summarizer.md"),
                    max_chars=4000,
                    keep_recent=1,
                )
            except Exception as e:
                console.print(f"[dim]历史压缩失败（继续）: {e}[/dim]")

            # Show round summary
            console.print(f"\n[dim]本轮 {len(round_responses)} 人发言完成[/dim]\n")

            # Consensus detection
            if discussion.flow != "requirement":
                consensus = self._check_consensus(round_responses)
                if consensus.consensus_reached:
                    console.print(
                        f"[green]✓ 检测到共识达成（{consensus.consensus_level}）："
                        f"{consensus.recommendation}[/green]\n"
                    )
                    break

            # User decision point
            if should_conclude:
                if discussion.flow == "requirement":
                    console.print("[yellow]💡 需求已基本澄清，进入最终整理[/yellow]\n")
                else:
                    console.print("[yellow]💡 主持人建议结束讨论：各方观点已充分表达[/yellow]\n")
                break

            if discussion.flow == "requirement":
                continue

            if round_num < effective_max_rounds:
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

        if discussion.flow == "requirement" and len(phase.rounds) >= effective_max_rounds:
            console.print(
                "[yellow]⚠ 需求讨论达到内部安全轮次上限，自动进入最终整理[/yellow]\n"
            )

        return phase

    def run_discussion_phase_streaming(
        self,
        discussion: Discussion,
        streaming_runner,
        max_rounds: int = 3,
    ) -> DiscussionPhase:
        """Phase 2 with streaming output (calls unified method)."""
        return self.run_discussion_phase(
            discussion, max_rounds=max_rounds, streaming_runner=streaming_runner
        )

    def _run_moderator_opening(
        self,
        discussion: Discussion,
        round_num: int,
        max_rounds: int,
        history: List[Dict],
        streaming_runner=None,
    ) -> str:
        """Run moderator opening for a round."""
        template = self._prompt_for(discussion, "moderator_opening.md")
        moderator_cfg = self.config.get_agent(discussion.moderator)

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

        console.print(f"[dim]主持人 prompt 长度: {len(prompt)} 字符[/dim]")

        if streaming_runner is not None:
            console.print(f"[dim]调用主持人 {moderator_cfg.name} (streaming)...[/dim]")
            response = streaming_runner.invoke_with_retry_streaming(
                agent_name=discussion.moderator,
                prompt_content=prompt,
                show_header=False,
            )
            console.print(f"[dim]主持人返回: success={response.success}[/dim]")
        else:
            console.print(f"[dim]调用主持人 {moderator_cfg.name}...[/dim]")
            with console.status(f"[yellow]{moderator_cfg.name} 正在准备开场引导...[/yellow]"):
                response = self.runner.invoke_with_retry(discussion.moderator, prompt)

        return response.content if response.success else "[主持人调用失败，请继续讨论]"

    def _run_discussion_round(
        self,
        discussion: Discussion,
        moderator_opening: str,
        history: List[Dict],
        streaming_runner=None,
    ) -> Dict[str, str]:
        """Run one round of discussion."""
        template = self._prompt_for(discussion, "discussion_response.md")
        moderator_cfg = self.config.get_agent(discussion.moderator)

        responses: Dict[str, str] = {}
        participants = self._discussion_participants(discussion)

        for agent_id in participants:
            agent_cfg = self.config.get_agent(agent_id)
            console.print(f"[dim]准备调用 {agent_cfg.name}...[/dim]")

            prompt = build_discussion_prompt(
                template_content=template,
                agent=agent_cfg,
                user_idea=discussion.user_idea,
                history=history,
                moderator_name=moderator_cfg.name,
                moderator_opening=moderator_opening,
            )
            console.print(f"[dim]Prompt 长度: {len(prompt)} 字符[/dim]")

            if streaming_runner is not None:
                console.print(f"[dim]调用 {agent_cfg.name} (streaming)...[/dim]")
                response = streaming_runner.invoke_with_retry_streaming(
                    agent_name=agent_id,
                    prompt_content=prompt,
                    show_header=True,
                )
                console.print(f"[dim]{agent_cfg.name} 返回: success={response.success}, content长度={len(response.content) if response.content else 0}[/dim]")
                if response.success:
                    responses[agent_id] = response.content
                else:
                    responses[agent_id] = f"[调用失败: {response.error}]"
            else:
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
                    console.print(
                        f"  [green]✓[/green] {agent_cfg.name} ({response.duration_seconds:.1f}s)"
                    )
                    responses[agent_id] = response.content
                else:
                    console.print(
                        f"  [red]✗[/red] {agent_cfg.name} (失败: {response.error})"
                    )
                    responses[agent_id] = f"[调用失败: {response.error}]"

        return responses

    def _check_consensus(self, round_responses: Dict[str, str]) -> ConsensusResult:
        """Check for consensus in the latest round responses using available agents."""
        # Use the first configured agent as detector if available
        if not self.config.agents:
            return ConsensusResult.unknown()

        detector_agent = next(iter(self.config.agents))

        # Simple consensus detection prompt
        detector_prompt_template = (
            "请分析以下最新一轮的讨论内容，判断各方是否达成共识。\n\n"
            "{latest_round}\n\n"
            "请以 JSON 格式回复（无需其他内容）：\n"
            '{{"consensus_reached": true/false, "consensus_level": "full/partial/none", '
            '"agreed_points": ["..."], "disputed_points": ["..."], '
            '"recommendation": "继续讨论/结束讨论"}}'
        )

        try:
            return detect_consensus(
                latest_round_responses=round_responses,
                runner=self.runner,
                detector_agent=detector_agent,
                detector_prompt_template=detector_prompt_template,
            )
        except Exception:
            return ConsensusResult.unknown()

    def _parse_convergence_signal(self, moderator_opening: str) -> bool:
        """Parse [SUGGEST_CONCLUDE] or [CONTINUE] signal from moderator."""
        return "[SUGGEST_CONCLUDE]" in moderator_opening

    # ── Phase 3 ──────────────────────────────────────────────────────────────

    def run_synthesis_phase(
        self,
        discussion: Discussion,
        streaming_runner=None,
    ) -> str:
        """Phase 3: Moderator synthesizes final output.

        When streaming_runner is provided, invocation uses streaming output.
        """
        if discussion.flow == "requirement":
            console.print("\n[bold cyan]Phase 3: 总结讨论并生成需求文档[/bold cyan]\n")
        else:
            console.print("\n[bold cyan]Phase 3: 生成结果文档[/bold cyan]\n")

        if not discussion.moderator:
            raise ValueError("Moderator must be selected")

        moderator_cfg = self.config.get_agent(discussion.moderator)
        template = self._prompt_for(discussion, "moderator_synthesis.md")

        # Build full history (bounds-checked)
        full_history: List[Dict] = []

        # Phase 1 (bounds-checked)
        if discussion.phases and len(discussion.phases) >= 1:
            phase1 = discussion.phases[0]
            if phase1.rounds:
                for r in phase1.rounds:
                    full_history.append({
                        "round": len(full_history) + 1,
                        "phase": "独立发言",
                        "responses": {
                            self.config.get_agent(aid).name: content
                            for aid, content in r.responses.items()
                        },
                    })

        # Phase 2 (bounds-checked)
        if len(discussion.phases) > 1:
            phase2 = discussion.phases[1]
            for r in phase2.rounds:
                round_responses = {
                    self.config.get_agent(aid).name: content
                    for aid, content in r.responses.items()
                }
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

        if streaming_runner is not None:
            response = streaming_runner.invoke_with_retry_streaming(
                agent_name=discussion.moderator,
                prompt_content=prompt,
                show_header=True,
            )
        else:
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

        console.print("[green]✓ 完成[/green]\n")
        return final_output

    def run_synthesis_phase_streaming(
        self,
        discussion: Discussion,
        streaming_runner,
    ) -> str:
        """Phase 3 with streaming output (calls unified method)."""
        return self.run_synthesis_phase(discussion, streaming_runner=streaming_runner)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_summary(self, content: str, max_len: int = 150) -> str:
        """Extract a brief summary from agent response."""
        match = re.search(r"###\s*整体评价\s*\n(.+?)(?:\n###|\Z)", content, re.DOTALL)
        if match:
            summary = match.group(1).strip()
        else:
            summary = content.strip().split("\n\n")[0][:max_len]

        if len(summary) > max_len:
            summary = summary[:max_len] + "..."
        return summary
