"""Core meeting orchestration loop."""
from __future__ import annotations

import concurrent.futures
from datetime import datetime
from typing import Callable, Dict, List, Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .agent_runner import AgentResponse, AgentRunner
from .config import Config, MeetingTemplate
from .consensus import ConsensusResult, detect_consensus
from .meeting import Meeting, Round, Session, save_meeting
from .prompt_builder import build_prompt
from .summarizer import generate_minutes, generate_proposal

console = Console()


class Orchestrator:
    def __init__(self, config: Config, base_dir, runner: AgentRunner):
        self.config = config
        self.base_dir = base_dir
        self.runner = runner

    def run_session(
        self,
        meeting: Meeting,
        session_type: str,
        agents: List[str],
        prior_proposal: str = "",
        user_feedback: str = "",
        summarizer_agent: str = "claude-sonnet",
        on_response: Optional[Callable[[str, AgentResponse], None]] = None,
    ) -> Session:
        template = self.config.get_template(session_type)
        session_index = len(meeting.sessions) + 1

        session = Session(
            session_index=session_index,
            session_type=session_type,
            agents=agents,
            started_at=datetime.now().isoformat(),
        )
        meeting.sessions.append(session)
        meeting.status = "in_progress"

        base_prompt_template = self.config.prompt("base_system.md")
        agent_list = [self.config.get_agent(a).name for a in agents]
        history: List[Dict] = []

        console.print(f"\n[bold cyan]=== Session {session_index}: {session_type} ===[/bold cyan]")
        console.print(f"[dim]{template.description}[/dim]")
        console.print(f"参会者：{', '.join(agent_list)}\n")

        for round_num in range(1, template.max_rounds + 1):
            console.print(f"[bold]Round {round_num}/{template.max_rounds}[/bold]")
            round_rule = template.round_rules.get(round_num, "按会议规则发言")

            current_round = Round(round_num=round_num)
            session.rounds.append(current_round)

            if round_num == 1:
                # First round: can run in parallel
                responses = self._run_round_parallel(
                    agents=agents,
                    base_prompt_template=base_prompt_template,
                    meeting=meeting,
                    session=session,
                    template=template,
                    round_num=round_num,
                    round_rule=round_rule,
                    agent_list=agent_list,
                    history=history,
                    prior_proposal=prior_proposal,
                    user_feedback=user_feedback,
                    on_response=on_response,
                )
            else:
                # Subsequent rounds: sequential (needs prior responses)
                responses = self._run_round_sequential(
                    agents=agents,
                    base_prompt_template=base_prompt_template,
                    meeting=meeting,
                    session=session,
                    template=template,
                    round_num=round_num,
                    round_rule=round_rule,
                    agent_list=agent_list,
                    history=history,
                    on_response=on_response,
                )

            current_round.responses = responses

            # Add to history for next round
            history.append({
                "round": round_num,
                "responses": {
                    self.config.get_agent(aid).name: resp
                    for aid, resp in responses.items()
                },
            })

            # Save after each round
            save_meeting(meeting, self.base_dir)

            # Consensus detection (use cheapest available agent)
            consensus = self._detect_consensus(responses, summarizer_agent)
            session.consensus_level = consensus.consensus_level

            console.print(f"  [dim]共识级别: {consensus.consensus_level} — {consensus.recommendation}[/dim]")

            if consensus.consensus_level == "full" and round_num < template.max_rounds:
                console.print("[green]  已达成完全共识，提前结束当前阶段。[/green]")
                break

        # Generate minutes
        console.print("\n[dim]生成会议纪要...[/dim]")
        session.minutes = generate_minutes(
            session=session,
            topic=meeting.topic,
            runner=self.runner,
            summarizer_agent=summarizer_agent,
            minutes_prompt_template=self.config.prompt("minutes_generator.md"),
        )

        # Generate proposal
        console.print("[dim]生成方案文档...[/dim]")
        session.proposal = generate_proposal(
            session=session,
            topic=meeting.topic,
            runner=self.runner,
            summarizer_agent=summarizer_agent,
            proposal_prompt_template=self.config.prompt("proposal_generator.md"),
            prior_proposal=prior_proposal,
        )

        session.finished_at = datetime.now().isoformat()
        save_meeting(meeting, self.base_dir)

        console.print(f"\n[green]Session {session_index} 完成！[/green]")
        return session

    def _run_round_parallel(
        self,
        agents,
        base_prompt_template,
        meeting,
        session,
        template,
        round_num,
        round_rule,
        agent_list,
        history,
        prior_proposal,
        user_feedback,
        on_response,
    ) -> Dict[str, str]:
        responses: Dict[str, str] = {}

        def invoke_one(agent_id: str) -> tuple[str, AgentResponse]:
            agent_cfg = self.config.get_agent(agent_id)
            prompt = build_prompt(
                template_content=base_prompt_template,
                agent=agent_cfg,
                topic=meeting.topic,
                session_type=session.session_type,
                session_description=template.description,
                round_num=round_num,
                max_rounds=template.max_rounds,
                round_rule=round_rule,
                agent_list=agent_list,
                history=history,
                prior_proposal=prior_proposal,
                user_feedback=user_feedback,
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
                    f"  {self.config.get_agent(agent_id).name} 发言中...", total=None
                )
                for agent_id in agents
            }

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as executor:
                futures = {executor.submit(invoke_one, aid): aid for aid in agents}
                for future in concurrent.futures.as_completed(futures):
                    agent_id, response = future.result()
                    agent_name = self.config.get_agent(agent_id).name
                    progress.update(tasks[agent_id], description=f"  {agent_name} ✓ ({response.duration_seconds:.1f}s)")
                    progress.stop_task(tasks[agent_id])
                    responses[agent_id] = response.content
                    if on_response:
                        on_response(agent_id, response)

        return responses

    def _run_round_sequential(
        self,
        agents,
        base_prompt_template,
        meeting,
        session,
        template,
        round_num,
        round_rule,
        agent_list,
        history,
        on_response,
    ) -> Dict[str, str]:
        responses: Dict[str, str] = {}
        # Build incremental history including current round's prior responses
        current_round_responses: Dict[str, str] = {}

        for agent_id in agents:
            agent_cfg = self.config.get_agent(agent_id)
            agent_name = agent_cfg.name

            # Include already-spoken agents in this round
            incremental_history = list(history)
            if current_round_responses:
                incremental_history.append({
                    "round": round_num,
                    "responses": {
                        self.config.get_agent(aid).name: resp
                        for aid, resp in current_round_responses.items()
                    },
                })

            prompt = build_prompt(
                template_content=base_prompt_template,
                agent=agent_cfg,
                topic=meeting.topic,
                session_type=session.session_type,
                session_description=template.description,
                round_num=round_num,
                max_rounds=template.max_rounds,
                round_rule=round_rule,
                agent_list=agent_list,
                history=incremental_history,
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task(f"  {agent_name} 发言中...", total=None)
                response = self.runner.invoke_with_retry(agent_id, prompt)
                progress.update(task, description=f"  {agent_name} ✓")

            console.print(f"  [green]✓[/green] {agent_name} ({response.duration_seconds:.1f}s)")
            responses[agent_id] = response.content
            current_round_responses[agent_id] = response.content

            if on_response:
                on_response(agent_id, response)

        return responses

    def _detect_consensus(self, responses: Dict[str, str], detector_agent: str) -> ConsensusResult:
        try:
            detector_prompt = self.config.prompt("consensus_detector.md")
            named_responses = {
                self.config.get_agent(aid).name: content
                for aid, content in responses.items()
            }
            return detect_consensus(
                latest_round_responses=named_responses,
                runner=self.runner,
                detector_agent=detector_agent,
                detector_prompt_template=detector_prompt,
            )
        except Exception:
            from .consensus import ConsensusResult as CR
            return CR.unknown()
