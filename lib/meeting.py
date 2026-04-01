"""Meeting state management and persistence."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Round:
    round_num: int
    responses: Dict[str, str] = field(default_factory=dict)


@dataclass
class Session:
    session_index: int
    session_type: str          # brainstorm | review | decision
    agents: List[str]
    rounds: List[Round] = field(default_factory=list)
    proposal: str = ""
    minutes: str = ""
    consensus_level: str = ""  # full | partial | none | ""
    started_at: str = ""
    finished_at: str = ""


@dataclass
class Meeting:
    topic_id: str
    topic: str
    created_at: str
    sessions: List[Session] = field(default_factory=list)
    status: str = "draft"      # draft | in_progress | finalized
    final_proposal: str = ""


# ── Persistence ──────────────────────────────────────────────────────────────

def _meetings_base(base_dir: Path) -> Path:
    return base_dir / "meetings"


def _topic_dir(base_dir: Path, topic_id: str) -> Path:
    return _meetings_base(base_dir) / topic_id


def _session_dir(base_dir: Path, topic_id: str, session_index: int) -> Path:
    return _topic_dir(base_dir, topic_id) / f"session_{session_index:02d}"


def _raw_dir(base_dir: Path, topic_id: str, session_index: int) -> Path:
    return _session_dir(base_dir, topic_id, session_index) / "raw"


def create_topic_id(topic: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff]", "_", topic)[:20]
    short_uuid = str(uuid.uuid4())[:6]
    return f"{slug}_{short_uuid}"


def save_meeting(meeting: Meeting, base_dir: Path) -> None:
    topic_dir = _topic_dir(base_dir, meeting.topic_id)
    topic_dir.mkdir(parents=True, exist_ok=True)

    # Save metadata as JSON
    meta_path = topic_dir / "meeting.json"
    meta = {
        "topic_id": meeting.topic_id,
        "topic": meeting.topic,
        "created_at": meeting.created_at,
        "status": meeting.status,
        "sessions": [
            {
                "session_index": s.session_index,
                "session_type": s.session_type,
                "agents": s.agents,
                "consensus_level": s.consensus_level,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "rounds": [
                    {"round_num": r.round_num, "responses": r.responses}
                    for r in s.rounds
                ],
            }
            for s in meeting.sessions
        ],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # Save topic.md
    topic_md = topic_dir / "topic.md"
    if not topic_md.exists():
        topic_md.write_text(f"# {meeting.topic}\n\n", encoding="utf-8")

    # Save final proposal if finalized
    if meeting.final_proposal:
        (topic_dir / "final_proposal.md").write_text(meeting.final_proposal, encoding="utf-8")

    # Save per-session files
    for s in meeting.sessions:
        sess_dir = _session_dir(base_dir, meeting.topic_id, s.session_index)
        sess_dir.mkdir(parents=True, exist_ok=True)
        raw_dir = sess_dir / "raw"
        raw_dir.mkdir(exist_ok=True)

        if s.minutes:
            (sess_dir / "minutes.md").write_text(s.minutes, encoding="utf-8")
        if s.proposal:
            (sess_dir / "proposal.md").write_text(s.proposal, encoding="utf-8")

        # Save raw responses
        for r in s.rounds:
            for agent_id, content in r.responses.items():
                fname = f"round_{r.round_num:02d}_{agent_id}.md"
                (raw_dir / fname).write_text(content, encoding="utf-8")


def load_meeting(topic_id: str, base_dir: Path) -> Meeting:
    topic_dir = _topic_dir(base_dir, topic_id)
    meta_path = topic_dir / "meeting.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Meeting not found: {topic_id}")

    data = json.loads(meta_path.read_text(encoding="utf-8"))

    sessions = []
    for sd in data.get("sessions", []):
        rounds = [
            Round(round_num=rd["round_num"], responses=rd["responses"])
            for rd in sd.get("rounds", [])
        ]
        sess_dir = _session_dir(base_dir, topic_id, sd["session_index"])
        proposal = ""
        minutes = ""
        if (sess_dir / "proposal.md").exists():
            proposal = (sess_dir / "proposal.md").read_text(encoding="utf-8")
        if (sess_dir / "minutes.md").exists():
            minutes = (sess_dir / "minutes.md").read_text(encoding="utf-8")

        sessions.append(Session(
            session_index=sd["session_index"],
            session_type=sd["session_type"],
            agents=sd["agents"],
            rounds=rounds,
            proposal=proposal,
            minutes=minutes,
            consensus_level=sd.get("consensus_level", ""),
            started_at=sd.get("started_at", ""),
            finished_at=sd.get("finished_at", ""),
        ))

    final_proposal = ""
    final_path = topic_dir / "final_proposal.md"
    if final_path.exists():
        final_proposal = final_path.read_text(encoding="utf-8")

    return Meeting(
        topic_id=data["topic_id"],
        topic=data["topic"],
        created_at=data["created_at"],
        sessions=sessions,
        status=data.get("status", "draft"),
        final_proposal=final_proposal,
    )


# ── Discussion data model (discuss mode) ─────────────────────────────────────

@dataclass
class DiscussionRound:
    round_num: int
    moderator_opening: str = ""         # 主持人本轮开场引导
    responses: Dict[str, str] = field(default_factory=dict)  # agent_id → content


@dataclass
class DiscussionPhase:
    phase_type: str                      # independent / discussion / synthesis
    phase_index: int
    rounds: List[DiscussionRound] = field(default_factory=list)


@dataclass
class Discussion:
    topic_id: str
    user_idea: str
    created_at: str
    agents: List[str] = field(default_factory=list)
    moderator: str = ""                  # 主持人 Agent ID
    status: str = "draft"               # draft / discussing / finalized
    final_output: str = ""
    user_feedbacks: List[str] = field(default_factory=list)
    phases: List[DiscussionPhase] = field(default_factory=list)


# ── Discussion persistence ────────────────────────────────────────────────────

def _discussion_dir(base_dir: Path, topic_id: str) -> Path:
    return base_dir / "meetings" / topic_id


def save_discussion(discussion: Discussion, base_dir: Path) -> None:
    topic_dir = _discussion_dir(base_dir, discussion.topic_id)
    topic_dir.mkdir(parents=True, exist_ok=True)

    # Save metadata
    meta = {
        "mode": "discuss",
        "topic_id": discussion.topic_id,
        "user_idea": discussion.user_idea,
        "created_at": discussion.created_at,
        "agents": discussion.agents,
        "moderator": discussion.moderator,
        "status": discussion.status,
        "user_feedbacks": discussion.user_feedbacks,
        "phases": [
            {
                "phase_type": p.phase_type,
                "phase_index": p.phase_index,
                "rounds": [
                    {
                        "round_num": r.round_num,
                        "moderator_opening": r.moderator_opening,
                        "responses": r.responses,
                    }
                    for r in p.rounds
                ],
            }
            for p in discussion.phases
        ],
    }
    (topic_dir / "meeting.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # idea.md
    idea_path = topic_dir / "idea.md"
    if not idea_path.exists():
        idea_path.write_text(f"# 想法\n\n{discussion.user_idea}\n", encoding="utf-8")

    # Per-phase files
    for phase in discussion.phases:
        if phase.phase_type == "independent":
            phase_dir = topic_dir / "phase_01_independent" / "raw"
            phase_dir.mkdir(parents=True, exist_ok=True)
            for r in phase.rounds:
                for agent_id, content in r.responses.items():
                    (phase_dir / f"{agent_id}.md").write_text(content, encoding="utf-8")

        elif phase.phase_type == "discussion":
            phase_dir = topic_dir / "phase_02_discussion"
            phase_dir.mkdir(parents=True, exist_ok=True)
            for r in phase.rounds:
                round_dir = phase_dir / f"round_{r.round_num:02d}"
                round_dir.mkdir(exist_ok=True)
                if r.moderator_opening:
                    (round_dir / "moderator_opening.md").write_text(
                        r.moderator_opening, encoding="utf-8"
                    )
                for agent_id, content in r.responses.items():
                    (round_dir / f"{agent_id}.md").write_text(content, encoding="utf-8")

        elif phase.phase_type == "synthesis":
            phase_dir = topic_dir / "phase_03_synthesis"
            phase_dir.mkdir(parents=True, exist_ok=True)
            if discussion.final_output:
                (phase_dir / "final_output.md").write_text(
                    discussion.final_output, encoding="utf-8"
                )

    # Top-level final_output.md
    if discussion.final_output:
        (topic_dir / "final_output.md").write_text(discussion.final_output, encoding="utf-8")


def load_discussion(topic_id: str, base_dir: Path) -> Discussion:
    topic_dir = _discussion_dir(base_dir, topic_id)
    meta_path = topic_dir / "meeting.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Discussion not found: {topic_id}")

    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if data.get("mode") != "discuss":
        raise ValueError(f"{topic_id} is not a discussion (mode={data.get('mode')})")

    phases = []
    for pd in data.get("phases", []):
        rounds = [
            DiscussionRound(
                round_num=rd["round_num"],
                moderator_opening=rd.get("moderator_opening", ""),
                responses=rd.get("responses", {}),
            )
            for rd in pd.get("rounds", [])
        ]
        phases.append(DiscussionPhase(
            phase_type=pd["phase_type"],
            phase_index=pd["phase_index"],
            rounds=rounds,
        ))

    final_output = ""
    final_path = topic_dir / "final_output.md"
    if final_path.exists():
        final_output = final_path.read_text(encoding="utf-8")

    return Discussion(
        topic_id=data["topic_id"],
        user_idea=data["user_idea"],
        created_at=data["created_at"],
        agents=data.get("agents", []),
        moderator=data.get("moderator", ""),
        status=data.get("status", "draft"),
        final_output=final_output,
        user_feedbacks=data.get("user_feedbacks", []),
        phases=phases,
    )


def list_meetings(base_dir: Path) -> List[dict]:
    meetings_dir = _meetings_base(base_dir)
    if not meetings_dir.exists():
        return []
    result = []
    for topic_dir in sorted(meetings_dir.iterdir()):
        meta_path = topic_dir / "meeting.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                result.append({
                    "topic_id": data["topic_id"],
                    "topic": data["topic"],
                    "created_at": data["created_at"],
                    "status": data.get("status", "draft"),
                    "session_count": len(data.get("sessions", [])),
                })
            except Exception:
                pass
    return result
