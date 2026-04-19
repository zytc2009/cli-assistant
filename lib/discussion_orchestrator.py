"""Discussion orchestration for discuss mode (Phase 1-3)."""
from __future__ import annotations

import concurrent.futures
import re
from typing import Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

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
    build_requirement_round_prompt,
    build_synthesis_prompt,
)
from .visual_companion import VisualCompanion

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
    _REQUIREMENT_FIELDS = [
        "Goal",
        "Scope",
        "Inputs",
        "Outputs",
        "Acceptance Criteria",
    ]
    _REQUIREMENT_SAFETY_MAX_ROUNDS = 12

    def __init__(
        self,
        config: Config,
        base_dir,
        runner: AgentRunner,
        summarizer_agent: str = "claude-sonnet",
        visual_companion: Optional[VisualCompanion] = None,
    ):
        self.config = config
        self.base_dir = base_dir
        self.runner = runner
        self.summarizer_agent = summarizer_agent
        self.visual_companion = visual_companion

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

    def _normalize_requirement_field(self, field_name: str) -> Optional[str]:
        normalized = re.sub(r"\s+", " ", field_name.strip())
        aliases = {
            "goal": "Goal",
            "scope": "Scope",
            "inputs": "Inputs",
            "outputs": "Outputs",
            "acceptance criteria": "Acceptance Criteria",
            "acceptance": "Acceptance Criteria",
        }
        return aliases.get(normalized.lower())

    def _extract_converged_fields(self, text: str) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        for raw_field, value in re.findall(r"\[CONVERGED\]\s*([^:\n\uff1a]+)\s*[:\uff1a]\s*(.+?)(?=\n\s*[-\u2022\u25cf]|\n\s*\[CONVERGED\]|$)", text, re.DOTALL):
            field_name = self._normalize_requirement_field(raw_field)
            if field_name:
                fields[field_name] = value.strip()
        return fields

    def _extract_requirement_questions(
        self,
        discussion: Discussion,
        round_responses: Dict[str, str],
    ) -> List[Dict[str, str]]:
        """Collect and deduplicate requirement questions from agent responses."""
        questions: Dict[tuple[str, str], Dict[str, str]] = {}

        for agent_id, content in round_responses.items():
            try:
                agent_name = self.config.get_agent(agent_id).name
            except ValueError:
                agent_name = agent_id

            match = re.search(
                r"##\s*3[\.。]?\s*待澄清问题\s*\n(.*?)(?:\n##\s*\d|\Z)",
                content,
                re.DOTALL,
            )
            if not match:
                continue

            section = match.group(1).strip()
            if not section or section == "无":
                continue

            for raw_line in section.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                line = re.sub(r"^[-*•\u2022\u25cf]\s*", "", line)
                if not line or line == "无":
                    continue

                field_name = "未分类"
                question_text = line
                field_match = re.match(r"\[(?P<field>[^\]]+)\]\s*(?P<question>.+)", line)
                if field_match:
                    normalized = self._normalize_requirement_field(field_match.group("field"))
                    if normalized:
                        field_name = normalized
                    else:
                        field_name = field_match.group("field").strip()
                    question_text = field_match.group("question").strip()

                key = (field_name, question_text)
                if key not in questions:
                    questions[key] = {
                        "field": field_name,
                        "question": question_text,
                        "agents": agent_name,
                    }
                elif agent_name not in questions[key]["agents"].split("、"):
                    questions[key]["agents"] += f"、{agent_name}"

        return list(questions.values())

    def _build_requirement_questions_table(
        self,
        discussion: Discussion,
        round_responses: Dict[str, str],
    ) -> Table:
        """Render phase 1 clarification questions as a compact table."""
        table = Table(
            title="待澄清问题汇总",
            show_lines=True,
            expand=True,
        )
        table.add_column("字段", style="cyan", no_wrap=True)
        table.add_column("待澄清问题", style="white")
        table.add_column("来源 AI", style="magenta", no_wrap=True)

        items = self._extract_requirement_questions(discussion, round_responses)
        if not items:
            table.add_row("—", "（暂无需要额外澄清的问题）", "—")
            return table

        for item in items:
            table.add_row(item["field"], item["question"], item["agents"])
        return table

    def _show_requirement_questions_table(
        self,
        discussion: Discussion,
        round_responses: Dict[str, str],
    ) -> None:
        """Show the aggregated clarification table for requirement flow."""
        table = self._build_requirement_questions_table(discussion, round_responses)
        console.print(table)

    def _requirement_field_status(
        self,
        discussion: Discussion,
        moderator_opening: str = "",
        round_responses: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        status: Dict[str, str] = {}
        texts: List[str] = []

        for phase in discussion.phases:
            for round_ in phase.rounds:
                if round_.moderator_opening:
                    texts.append(round_.moderator_opening)
                texts.extend(round_.responses.values())

        if moderator_opening:
            texts.append(moderator_opening)
        if round_responses:
            texts.extend(round_responses.values())

        for text in texts:
            status.update(self._extract_converged_fields(text))

        return status

    def _requirement_status_section(
        self,
        discussion: Discussion,
        moderator_opening: str = "",
        round_responses: Optional[Dict[str, str]] = None,
    ) -> str:
        status = self._requirement_field_status(
            discussion,
            moderator_opening=moderator_opening,
            round_responses=round_responses,
        )
        unresolved = [field for field in self._REQUIREMENT_FIELDS if field not in status]

        lines = ["## 当前需求状态", "", "### 已收敛字段"]
        if status:
            for field in self._REQUIREMENT_FIELDS:
                if field in status:
                    lines.append(f"- {field}: {status[field]}")
        else:
            lines.append("- （暂无）")

        lines.extend(["", "### 待澄清字段"])
        if unresolved:
            for field in unresolved:
                lines.append(f"- {field}")
        else:
            lines.append("- （全部已收敛）")

        return "\n".join(lines)

    def _show_requirement_status(
        self,
        discussion: Discussion,
        moderator_opening: str = "",
        round_responses: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        status = self._requirement_field_status(
            discussion,
            moderator_opening=moderator_opening,
            round_responses=round_responses,
        )
        unresolved = [field for field in self._REQUIREMENT_FIELDS if field not in status]

        lines = ["已收敛:"]
        if status:
            for field in self._REQUIREMENT_FIELDS:
                if field in status:
                    lines.append(f"- {field}: {status[field]}")
        else:
            lines.append("- （暂无）")

        lines.extend(["", "待澄清:"])
        if unresolved:
            for field in unresolved:
                lines.append(f"- {field}")
        else:
            lines.append("- （全部已收敛）")

        console.print(Panel("\n".join(lines), title="需求状态", border_style="cyan"))
        return status

    def _push_requirement_status_visual(
        self,
        discussion: Discussion,
        round_responses: Optional[Dict[str, str]] = None,
        title: str = "需求澄清看板",
        name: str = "requirement-status",
    ) -> None:
        """Push a visual status board to the browser companion."""
        if not self.visual_companion:
            return

        status = self._requirement_field_status(
            discussion, round_responses=round_responses
        )
        unclear: List[str] = []
        if (
            discussion.phases
            and len(discussion.phases) > 1
            and discussion.phases[1].rounds
        ):
            latest_round = discussion.phases[1].rounds[-1]
            unclear = self._extract_unclear_points(latest_round.responses)

        converged_html = "".join(
            f'<div class="section"><div class="label">{field}</div><p>{status.get(field, "—")}</p></div>'
            for field in self._REQUIREMENT_FIELDS
            if field in status
        )

        unclear_html = ""
        if unclear:
            items = "".join(f"<li>{pt}</li>" for pt in unclear)
            unclear_html = f'<div class="section"><div class="label">待澄清问题</div><ul>{items}</ul></div>'

        pending = [f for f in self._REQUIREMENT_FIELDS if f not in status]
        pending_html = ""
        if pending:
            items = "".join(f"<li>{f}</li>" for f in pending)
            pending_html = f'<div class="section"><div class="label">待收敛字段</div><ul>{items}</ul></div>'

        round_num = 0
        if discussion.flow == "requirement" and len(discussion.phases) > 1:
            round_num = len(discussion.phases[1].rounds)
        elif discussion.phases:
            round_num = sum(len(p.rounds) for p in discussion.phases)

        html = (
            f'<h2>{title}</h2>\n'
            f'<p class="subtitle">第 {round_num} 轮更新</p>\n'
            f'<div class="section"><div class="label">已收敛 ({len(status)}/{len(self._REQUIREMENT_FIELDS)})</div></div>\n'
            f'{converged_html}\n'
            f'{pending_html}\n'
            f'{unclear_html}'
        )

        filename = self.visual_companion.write_screen(html, name)
        console.print(
            f"[dim]Visual companion: {self.visual_companion.url} (screen {filename})[/dim]"
        )

    def _collect_requirement_feedback(self) -> str:
        console.print("[dim]（可选）补充需求信息，多行输入，空行结束；直接空行跳过[/dim]")
        lines: List[str] = []
        while True:
            try:
                line = console.input("> ")
            except (KeyboardInterrupt, EOFError):
                break
            if line.strip() == "":
                break
            lines.append(line)
        return "\n".join(lines).strip()

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

        if discussion.flow == "requirement" and self.visual_companion:
            agent_names = [self.config.get_agent(aid).name for aid in discussion.agents]
            html = (
                f'<h2>需求讨论开始</h2>\n'
                f'<p class="subtitle">{len(responses)} 位 AI 已提交初步理解</p>\n'
                f'<div class="section"><div class="label">参会者</div><p>{"、".join(agent_names)}</p></div>\n'
                f'<div class="section"><p>进入 Phase 2 迭代澄清后，此处将实时显示需求收敛进度。</p></div>'
            )
            self.visual_companion.write_screen(html, "phase1-welcome")

        if discussion.flow == "requirement":
            console.print("[bold cyan]Phase 1 待澄清问题汇总[/bold cyan]")
            console.print("[dim]下面把各 AI 提到的澄清点合并成表，方便你快速查看。[/dim]\n")
            self._show_requirement_questions_table(discussion, responses)

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

    # ── Visual Option Phase ──────────────────────────────────────────────────

    def run_visual_option_phase(
        self,
        discussion: Discussion,
        streaming_runner=None,
    ) -> str:
        """Generate visual options after Phase 1 if the topic involves UI/architecture/flow.

        Returns the user's selection text (or empty string if skipped / no visual needed).
        """
        if not self.visual_companion:
            return ""

        if not discussion.phases or not discussion.phases[0].rounds:
            return ""

        try:
            template = self.config.prompt("visual_option_generator.md")
        except Exception:
            return ""

        phase1 = discussion.phases[0].rounds[0]
        responses_text = "\n\n".join(
            f"### {self.config.get_agent(aid).name}\n{content}"
            for aid, content in phase1.responses.items()
        )

        prompt = template.format(
            user_idea=discussion.user_idea,
            phase1_responses=responses_text,
        )

        synthesizer_cfg = self.config.get_agent(self.summarizer_agent)
        console.print("\n[dim]正在分析是否需要生成可视化方案...[/dim]")

        if streaming_runner is not None:
            response = streaming_runner.invoke_with_retry_streaming(
                agent_name=self.summarizer_agent,
                prompt_content=prompt,
                show_header=False,
            )
        else:
            with console.status(f"[yellow]{synthesizer_cfg.name} 正在生成视觉方案...[/yellow]"):
                response = self.runner.invoke_with_retry(self.summarizer_agent, prompt)

        if not response.success:
            return ""

        content = response.content.strip()
        if "NO_VISUAL_NEEDED" in content:
            console.print("[dim]AI 判断当前话题无需可视化方案，继续文本讨论[/dim]\n")
            return ""

        # Strip markdown code fence if present
        html = content
        if html.startswith("```html"):
            html = html[7:]
        if html.startswith("```"):
            html = html[3:]
        if html.endswith("```"):
            html = html[:-3]
        html = html.strip()

        if not html:
            return ""

        filename = self.visual_companion.write_screen(html, "visual-options")
        console.print(f"\n[bold cyan]📊 可视化方案已生成[/bold cyan]")
        console.print(f"[dim]请在浏览器中查看并选择：{self.visual_companion.url} (screen {filename})[/dim]")
        console.print("[dim]选择完成后，请返回终端按回车继续...[/dim]")
        try:
            console.input("")
        except (KeyboardInterrupt, EOFError):
            pass

        events = self.visual_companion.read_events()
        if events:
            last = events[-1]
            choice = last.get("choice", "")
            choice_text = last.get("text", "")
            selection = f"选择了 [{choice}] {choice_text}" if choice else ""
            if selection:
                console.print(f"[green]✓ 已记录选择：{selection}[/green]\n")
                self.visual_companion.write_waiting_screen()
                return selection

        console.print("[dim]未检测到浏览器选择，继续文本讨论[/dim]\n")
        self.visual_companion.write_waiting_screen()
        return ""

    # ── Phase 2 ──────────────────────────────────────────────────────────────

    def run_discussion_phase(
        self,
        discussion: Discussion,
        max_rounds: int = 3,
        streaming_runner=None,
    ) -> DiscussionPhase:
        """Phase 2: discussion loop.

        Requirement flow: no moderator, iterative clarification with user, no round limit.
        Free discussion flow: moderator-led, up to max_rounds.
        """
        if discussion.flow == "requirement":
            return self._run_requirement_discussion_phase(discussion, streaming_runner)

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
                    self._show_requirement_status(
                        discussion,
                        moderator_opening=moderator_opening,
                        round_responses=round_responses,
                    )
                    choice = console.input(
                        "\n[Enter] 结束并生成需求文档  [c] 继续讨论  [f] 补充信息后继续\n选择: "
                    ).strip().lower()
                    if choice == "f":
                        feedback = self._collect_requirement_feedback()
                        if feedback:
                            discussion.user_feedbacks.append(f"第{round_num}轮后补充: {feedback}")
                            console.print("[dim]补充信息已记录，继续澄清[/dim]\n")
                        continue
                    if choice == "c":
                        console.print("[dim]继续下一轮需求澄清...[/dim]\n")
                        continue
                    console.print("[yellow]💡 需求已基本澄清，进入最终整理[/yellow]\n")
                else:
                    console.print("[yellow]💡 主持人建议结束讨论：各方观点已充分表达[/yellow]\n")
                break

            if discussion.flow == "requirement":
                status = self._show_requirement_status(
                    discussion,
                    moderator_opening=moderator_opening,
                    round_responses=round_responses,
                )
                all_converged = len(status) == len(self._REQUIREMENT_FIELDS)
                if all_converged:
                    choice = console.input(
                        "\n[Enter] 结束并生成需求文档  [c] 继续讨论  [f] 补充信息后继续\n选择: "
                    ).strip().lower()
                    if choice == "f":
                        feedback = self._collect_requirement_feedback()
                        if feedback:
                            discussion.user_feedbacks.append(f"第{round_num}轮后补充: {feedback}")
                            console.print("[dim]补充信息已记录，继续澄清[/dim]\n")
                        continue
                    if choice == "c":
                        console.print("[dim]继续下一轮需求澄清...[/dim]\n")
                        continue
                    console.print("[yellow]💡 关键字段已收敛，进入最终整理[/yellow]\n")
                    break

                choice = console.input(
                    "\n[Enter] 继续下一轮  [f] 补充信息后继续  [d] 结束并生成需求文档\n选择: "
                ).strip().lower()
                if choice == "f":
                    feedback = self._collect_requirement_feedback()
                    if feedback:
                        discussion.user_feedbacks.append(f"第{round_num}轮后补充: {feedback}")
                        console.print("[dim]补充信息已记录[/dim]\n")
                elif choice == "d":
                    console.print("[dim]进入需求文档整理...[/dim]\n")
                    break
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

    # ── Requirement Phase 2: no-moderator iterative clarification ────────────

    def _run_requirement_discussion_phase(
        self,
        discussion: Discussion,
        streaming_runner=None,
    ) -> DiscussionPhase:
        """Phase 2 for requirement flow.

        All agents participate equally (no moderator).  The loop continues until
        consensus is detected or the user ends it.  After every round the user
        is shown remaining unclear questions and given a chance to add context
        (they can skip — their blind spots are fine, agents will assume).
        """
        console.print("\n[bold cyan]Phase 2: 需求迭代澄清[/bold cyan]")
        console.print("[dim]各 AI 平等参与，持续推进直到需求收敛或你选择结束[/dim]\n")

        phase = DiscussionPhase(phase_type="discussion", phase_index=2)
        discussion.phases.append(phase)

        # Seed history from Phase 1
        history: List[Dict] = []
        if discussion.phases and discussion.phases[0].rounds:
            history.append({
                "round": 1,
                "phase": "独立发言",
                "responses": {
                    self.config.get_agent(aid).name: content
                    for aid, content in discussion.phases[0].rounds[0].responses.items()
                },
            })

        # Show confirmation checklist before entering the loop
        correction = self._run_requirement_confirmation(discussion)
        if correction:
            discussion.user_feedbacks.append(f"确认阶段纠正: {correction}")

        round_num = 0
        while True:
            round_num += 1
            console.print(f"\n[bold cyan]── 第 {round_num} 轮澄清 ──[/bold cyan]\n")

            round_responses = self._run_requirement_round(
                discussion=discussion,
                history=history,
                round_num=round_num,
                streaming_runner=streaming_runner,
            )

            discussion_round = DiscussionRound(round_num=round_num, responses=round_responses)
            phase.rounds.append(discussion_round)
            save_discussion(discussion, self.base_dir)

            history.append({
                "round": round_num + 1,
                "phase": "澄清讨论",
                "responses": {
                    self.config.get_agent(aid).name: content
                    for aid, content in round_responses.items()
                },
            })

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

            # Show converged field status
            status = self._show_requirement_status(
                discussion, round_responses=round_responses
            )

            # Update visual companion
            self._push_requirement_status_visual(
                discussion,
                round_responses=round_responses,
                title="需求澄清看板",
                name=f"round-{round_num}",
            )

            all_converged = len(status) == len(self._REQUIREMENT_FIELDS)

            # For requirement flow, only auto-exit when ALL 5 fields have
            # explicit [CONVERGED] declarations — generic consensus detection
            # would false-positive when AIs agree on what's still unclear.
            if all_converged:
                console.print("\n[green]✓ 所有字段已收敛，进入 Phase 3[/green]\n")
                break

            # Show remaining unclear questions from agents' responses
            unclear = self._extract_unclear_points(round_responses)
            if unclear:
                console.print("\n[yellow]AI 仍待向你澄清的问题：[/yellow]")
                self._show_requirement_questions_table(discussion, round_responses)

            # Optional user clarification
            console.print(
                "\n[dim]可补充回答上面的问题（多行，空行结束）；"
                "直接回车跳过；输入 d 结束并生成文档：[/dim]"
            )
            lines: List[str] = []
            while True:
                try:
                    line = console.input("> ").strip()
                except (KeyboardInterrupt, EOFError):
                    break
                if line.lower() == "d":
                    console.print("\n[dim]结束讨论，进入 Phase 3...[/dim]\n")
                    return phase
                if line == "" and lines:
                    break
                if line == "":
                    break
                lines.append(line)

            if lines:
                feedback = "\n".join(lines)
                discussion.user_feedbacks.append(f"第{round_num}轮补充: {feedback}")
                console.print("[dim]已记录，继续下一轮...[/dim]\n")
            else:
                console.print("[dim]未补充，AI 将基于合理假设继续推进...[/dim]\n")

        return phase

    def _run_requirement_confirmation(self, discussion: Discussion) -> str:
        """Generate a consolidated understanding checklist for user confirmation.

        Calls the summarizer agent to synthesize Phase 1 responses and any user
        feedback into a structured checklist.  Shows it to the user and collects
        optional corrections before Phase 2 begins.  Returns the correction text
        (empty string if the user has nothing to add).
        """
        console.print("\n[bold cyan]── 需求理解确认 ──[/bold cyan]")
        console.print("[dim]正在整理各 AI 的初步理解，请稍候...[/dim]\n")

        try:
            template = self.config.prompt("requirement_confirmation.md")
        except Exception:
            # Prompt file missing — skip confirmation silently
            return ""

        # Build phase 1 responses section
        phase1_lines: List[str] = []
        if discussion.phases and discussion.phases[0].rounds:
            for aid, content in discussion.phases[0].rounds[0].responses.items():
                agent_name = self.config.get_agent(aid).name
                truncated = content[:800] + "..." if len(content) > 800 else content
                phase1_lines.append(f"### {agent_name}\n{truncated}")
        phase1_text = "\n\n".join(phase1_lines) if phase1_lines else "（无）"

        user_feedback_text = (
            "\n".join(discussion.user_feedbacks) if discussion.user_feedbacks else "（用户暂无补充）"
        )

        prompt = template.format(
            user_idea=discussion.user_idea,
            phase1_responses=phase1_text,
            user_feedback=user_feedback_text,
        )

        try:
            response = self.runner.invoke_with_retry(self.summarizer_agent, prompt)
        except Exception as e:
            console.print(f"[dim]确认清单生成失败（跳过）: {e}[/dim]")
            return ""

        if not response.success:
            console.print("[dim]确认清单生成失败（跳过）[/dim]")
            return ""

        console.print(Panel(response.content, title="当前需求理解", border_style="cyan"))

        console.print(
            "\n[dim]以上理解有误吗？可以在这里纠正或补充（多行，空行结束；直接回车表示确认无误）：[/dim]"
        )
        lines: List[str] = []
        while True:
            try:
                line = console.input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if line == "" and lines:
                break
            if line == "":
                break
            lines.append(line)

        correction = "\n".join(lines).strip()
        if correction:
            console.print("[dim]纠正已记录，将在讨论中体现[/dim]\n")
        else:
            console.print("[dim]确认无误，开始迭代澄清...[/dim]\n")

        return correction

    def _run_requirement_round(
        self,
        discussion: Discussion,
        history: List[Dict],
        round_num: int,
        streaming_runner=None,
    ) -> Dict[str, str]:
        """All agents respond equally in one requirement discussion round."""
        template = self.config.prompt("requirement_discussion_response.md")
        responses: Dict[str, str] = {}

        for agent_id in discussion.agents:
            agent_cfg = self.config.get_agent(agent_id)
            prompt = build_requirement_round_prompt(
                template_content=template,
                agent=agent_cfg,
                user_idea=discussion.user_idea,
                history=history,
                user_feedbacks=discussion.user_feedbacks,
                round_num=round_num,
            )

            if streaming_runner is not None:
                response = streaming_runner.invoke_with_retry_streaming(
                    agent_name=agent_id,
                    prompt_content=prompt,
                    show_header=True,
                )
                responses[agent_id] = response.content if response.success else f"[调用失败: {response.error}]"
            else:
                with console.status(f"[cyan]{agent_cfg.name} 发言中...[/cyan]"):
                    response = self.runner.invoke_with_retry(agent_id, prompt)
                if response.success:
                    console.print(f"  [green]✓[/green] {agent_cfg.name} ({response.duration_seconds:.1f}s)")
                    responses[agent_id] = response.content
                else:
                    console.print(f"  [red]✗[/red] {agent_cfg.name} 失败")
                    responses[agent_id] = f"[调用失败: {response.error}]"

        return responses

    def _extract_unclear_points(self, round_responses: Dict[str, str]) -> List[str]:
        """Parse '仍待澄清的问题' sections from agent responses, dedup."""
        points: List[str] = []
        for content in round_responses.values():
            match = re.search(
                r"###?\s*2[\.。]?\s*仍待澄清的问题\s*\n(.*?)(?:\n###?|\Z)",
                content,
                re.DOTALL,
            )
            if not match:
                continue
            section = match.group(1).strip()
            if not section or section == "无":
                continue
            for line in section.splitlines():
                line = line.strip().lstrip("- ").strip()
                if line and line != "无" and line not in points:
                    points.append(line)
        return points

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
            requirement_status_section=(
                self._requirement_status_section(discussion)
                if discussion.flow == "requirement"
                else ""
            ),
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
            if self.visual_companion:
                self.visual_companion.write_screen(
                    '<div style="display:flex;align-items:center;justify-content:center;min-height:60vh">'
                    '<p class="subtitle">正在生成最终需求文档...</p></div>',
                    "synthesis-start",
                )
        else:
            console.print("\n[bold cyan]Phase 3: 生成结果文档[/bold cyan]\n")

        # Requirement flow: use cheapest summarizer; free discussion: use moderator
        if discussion.flow == "requirement":
            synthesizer_id = self.summarizer_agent
        else:
            if not discussion.moderator:
                raise ValueError("Moderator must be selected")
            synthesizer_id = discussion.moderator

        synthesizer_cfg = self.config.get_agent(synthesizer_id)
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
                    round_responses[f"{synthesizer_cfg.name}(主持人)"] = r.moderator_opening
                full_history.append({
                    "round": len(full_history) + 1,
                    "phase": "讨论",
                    "responses": round_responses,
                })

        prompt = build_synthesis_prompt(
            template_content=template,
            agent=synthesizer_cfg,
            user_idea=discussion.user_idea,
            full_history=full_history,
            all_user_feedbacks=discussion.user_feedbacks,
        )

        if streaming_runner is not None:
            response = streaming_runner.invoke_with_retry_streaming(
                agent_name=synthesizer_id,
                prompt_content=prompt,
                show_header=True,
            )
        else:
            with console.status(f"[yellow]{synthesizer_cfg.name} 正在综合各方观点...[/yellow]"):
                response = self.runner.invoke_with_retry(synthesizer_id, prompt)

        if not response.success:
            console.print(f"[red]✗ 生成失败: {response.error}[/red]")
            return ""

        final_output = response.content

        # ── Auto-review for requirement flow ──────────────────────────────────
        if discussion.flow == "requirement":
            approved, review_text = self._review_requirement(final_output)
            if not approved:
                console.print(Panel(review_text, title="需求审阅结果", border_style="yellow"))
                fix = console.input("\n是否根据审阅意见自动修正需求文档？ [y/N]: ").strip().lower()
                if fix == "y":
                    console.print("[dim]正在修正需求文档...[/dim]")
                    revised = self._revise_requirement(
                        discussion=discussion,
                        original_requirement=final_output,
                        review_feedback=review_text,
                        synthesizer_id=synthesizer_id,
                        streaming_runner=streaming_runner,
                    )
                    if revised:
                        final_output = revised
                        console.print("[green]✓ 已修正[/green]\n")
                    else:
                        console.print("[yellow]修正失败，保留原始版本[/yellow]\n")
                else:
                    console.print("[dim]跳过修正，保留原始版本[/dim]\n")
            else:
                console.print("[dim]需求审阅通过[/dim]\n")

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

    # ── Requirement Review & Revision ─────────────────────────────────────────

    def _review_requirement(self, requirement_doc: str) -> tuple[bool, str]:
        """Run automatic spec review on the generated requirement document.

        Returns:
            (approved, review_text)
        """
        try:
            template = self.config.prompt("requirement_reviewer.md")
        except Exception:
            # Prompt missing — skip review silently
            return True, ""

        prompt = template.format(requirement_doc=requirement_doc)

        try:
            response = self.runner.invoke_with_retry(self.summarizer_agent, prompt)
        except Exception as e:
            console.print(f"[dim]需求审阅失败（跳过）: {e}[/dim]")
            return True, ""

        if not response.success:
            console.print("[dim]需求审阅失败（跳过）[/dim]")
            return True, ""

        review_text = response.content.strip()
        m = re.search(r'\*\*状态[：:]\*\*\s*(Approved|Issues Found)', review_text)
        if m:
            approved = m.group(1) == "Approved"
        else:
            # Fallback: treat as approved only if no explicit issue markers found
            approved = "Issues Found" not in review_text and not re.search(
                r'\*\*问题[（(]?如有[）)]?[：:]\*\*|\*\*问题[：:]\*\*', review_text
            )

        return approved, review_text

    def _revise_requirement(
        self,
        discussion: Discussion,
        original_requirement: str,
        review_feedback: str,
        synthesizer_id: str,
        streaming_runner=None,
    ) -> str:
        """Revise requirement document based on review feedback."""
        synthesizer_cfg = self.config.get_agent(synthesizer_id)

        # Rebuild full history (same logic as run_synthesis_phase)
        full_history: List[Dict] = []
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
        if len(discussion.phases) > 1:
            phase2 = discussion.phases[1]
            for r in phase2.rounds:
                round_responses = {
                    self.config.get_agent(aid).name: content
                    for aid, content in r.responses.items()
                }
                if r.moderator_opening:
                    round_responses[f"{synthesizer_cfg.name}(主持人)"] = r.moderator_opening
                full_history.append({
                    "round": len(full_history) + 1,
                    "phase": "讨论",
                    "responses": round_responses,
                })

        history_text = "\n\n".join(
            f"### Round {h['round']} ({h['phase']})\n" + "\n".join(f"**{k}**: {v}" for k, v in h["responses"].items())
            for h in full_history
        )
        feedback_text = "\n".join(discussion.user_feedbacks) if discussion.user_feedbacks else "（无）"

        revise_prompt = (
            f"你是 {synthesizer_cfg.name}。\n\n"
            f"## 原始需求\n\n{discussion.user_idea}\n\n"
            f"## 完整讨论记录\n\n{history_text}\n\n"
            f"## 用户补充\n\n{feedback_text}\n\n"
            f"## 之前生成的 requirement.md\n\n{original_requirement}\n\n"
            f"## 审阅意见\n\n{review_feedback}\n\n"
            f"## 你的任务\n\n"
            f"请根据审阅意见修改 requirement.md，输出修正后的完整文档。\n"
            f"严格遵循原有格式要求：只包含 Goal / Scope / Inputs / Outputs / Acceptance Criteria / Open Questions 六个段落。\n"
            f"输出的第一行必须是 `# Requirement: ...`，不要添加任何前言或解释。"
        )

        if streaming_runner is not None:
            response = streaming_runner.invoke_with_retry_streaming(
                agent_name=synthesizer_id,
                prompt_content=revise_prompt,
                show_header=False,
            )
        else:
            with console.status(f"[yellow]{synthesizer_cfg.name} 正在修正需求文档...[/yellow]"):
                response = self.runner.invoke_with_retry(synthesizer_id, revise_prompt)

        return response.content if response.success else ""

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
