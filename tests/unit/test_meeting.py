"""Tests for lib/meeting.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.meeting import (
    Discussion,
    DiscussionPhase,
    DiscussionRound,
    Meeting,
    Round,
    Session,
    create_topic_id,
    list_meetings,
    load_discussion,
    load_meeting,
    save_discussion,
    save_meeting,
)


class TestCreateTopicId:
    def test_slug_generation(self):
        topic_id = create_topic_id("我的测试主题")
        assert "我的测试主题" in topic_id or "_" in topic_id

    def test_length_limit(self):
        long_topic = "a" * 100
        topic_id = create_topic_id(long_topic)
        slug_part = topic_id.rsplit("_", 1)[0]
        assert len(slug_part) <= 20

    def test_uuid_suffix(self):
        topic_id = create_topic_id("测试")
        parts = topic_id.rsplit("_", 1)
        assert len(parts) == 2
        assert len(parts[1]) == 6

    def test_special_chars_replaced(self):
        topic_id = create_topic_id("主题!@#$%^&*()")
        # No special chars should remain in slug part
        slug_part = topic_id.rsplit("_", 1)[0]
        assert all(c.isalnum() or c == "_" or '\u4e00' <= c <= '\u9fff' for c in slug_part)


class TestMeetingPersistence:
    def test_save_and_load_roundtrip(self, tmp_path: Path, sample_meeting: Meeting):
        save_meeting(sample_meeting, tmp_path)
        loaded = load_meeting(sample_meeting.topic_id, tmp_path)

        assert loaded.topic_id == sample_meeting.topic_id
        assert loaded.topic == sample_meeting.topic
        assert loaded.status == sample_meeting.status
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].session_type == "brainstorm"
        assert len(loaded.sessions[0].rounds) == 1

    def test_load_missing_meeting_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_meeting("nonexistent", tmp_path)

    def test_meeting_json_contains_all_fields(self, tmp_path: Path, sample_meeting: Meeting):
        save_meeting(sample_meeting, tmp_path)
        meta_path = tmp_path / "meetings" / sample_meeting.topic_id / "meeting.json"
        data = json.loads(meta_path.read_text(encoding="utf-8"))

        assert data["topic_id"] == sample_meeting.topic_id
        assert data["topic"] == sample_meeting.topic
        assert data["status"] == sample_meeting.status
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_type"] == "brainstorm"

    def test_session_proposal_saved(self, tmp_path: Path, sample_meeting: Meeting):
        save_meeting(sample_meeting, tmp_path)
        proposal_path = (
            tmp_path / "meetings" / sample_meeting.topic_id
            / "session_01" / "proposal.md"
        )
        assert proposal_path.exists()
        assert "方案" in proposal_path.read_text(encoding="utf-8")

    def test_session_minutes_saved(self, tmp_path: Path, sample_meeting: Meeting):
        save_meeting(sample_meeting, tmp_path)
        minutes_path = (
            tmp_path / "meetings" / sample_meeting.topic_id
            / "session_01" / "minutes.md"
        )
        assert minutes_path.exists()
        assert "纪要" in minutes_path.read_text(encoding="utf-8")

    def test_raw_responses_saved(self, tmp_path: Path, sample_meeting: Meeting):
        save_meeting(sample_meeting, tmp_path)
        raw_dir = (
            tmp_path / "meetings" / sample_meeting.topic_id
            / "session_01" / "raw"
        )
        assert (raw_dir / "round_01_claude-sonnet.md").exists()
        assert (raw_dir / "round_01_codex-o4-mini.md").exists()

    def test_final_proposal_saved(self, tmp_path: Path, sample_meeting: Meeting):
        sample_meeting.final_proposal = "# 最终方案"
        sample_meeting.status = "finalized"
        save_meeting(sample_meeting, tmp_path)

        final_path = tmp_path / "meetings" / sample_meeting.topic_id / "final_proposal.md"
        assert final_path.exists()
        assert "# 最终方案" in final_path.read_text(encoding="utf-8")


class TestDiscussionPersistence:
    def test_save_and_load_discussion_roundtrip(
        self, tmp_path: Path, sample_discussion: Discussion
    ):
        save_discussion(sample_discussion, tmp_path)
        loaded = load_discussion(sample_discussion.topic_id, tmp_path)

        assert loaded.topic_id == sample_discussion.topic_id
        assert loaded.user_idea == sample_discussion.user_idea
        assert loaded.moderator == sample_discussion.moderator
        assert loaded.status == sample_discussion.status
        assert len(loaded.phases) == 2

    def test_load_missing_discussion_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_discussion("nonexistent", tmp_path)

    def test_load_non_discussion_raises(self, tmp_path: Path):
        topic_dir = tmp_path / "meetings" / "test_topic"
        topic_dir.mkdir(parents=True)
        (topic_dir / "meeting.json").write_text(
            json.dumps({"mode": "meeting", "topic_id": "test_topic", "topic": "test"}),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="not a discussion"):
            load_discussion("test_topic", tmp_path)

    def test_discussion_independent_phase_saved(
        self, tmp_path: Path, sample_discussion: Discussion
    ):
        save_discussion(sample_discussion, tmp_path)
        phase_dir = tmp_path / "meetings" / sample_discussion.topic_id / "phase_01_independent" / "raw"
        assert (phase_dir / "claude-sonnet.md").exists()
        assert (phase_dir / "codex-o4-mini.md").exists()

    def test_discussion_discussion_phase_saved(
        self, tmp_path: Path, sample_discussion: Discussion
    ):
        save_discussion(sample_discussion, tmp_path)
        phase_dir = (
            tmp_path / "meetings" / sample_discussion.topic_id
            / "phase_02_discussion" / "round_01"
        )
        assert (phase_dir / "moderator_opening.md").exists()
        assert (phase_dir / "claude-sonnet.md").exists()

    def test_discussion_final_output_saved(
        self, tmp_path: Path, sample_discussion: Discussion
    ):
        sample_discussion.final_output = "# 最终输出"
        sample_discussion.status = "finalized"
        save_discussion(sample_discussion, tmp_path)

        final_path = tmp_path / "meetings" / sample_discussion.topic_id / "final_output.md"
        assert final_path.exists()


class TestListMeetings:
    def test_empty_meetings_dir(self, tmp_path: Path):
        (tmp_path / "meetings").mkdir(parents=True)
        assert list_meetings(tmp_path) == []

    def test_list_meetings_only(self, tmp_path: Path, sample_meeting: Meeting):
        save_meeting(sample_meeting, tmp_path)
        (tmp_path / "meetings" / "other_dir").mkdir()  # no meeting.json

        meetings = list_meetings(tmp_path)
        assert len(meetings) == 1
        assert meetings[0]["topic_id"] == sample_meeting.topic_id
        assert meetings[0]["mode"] == "meeting"

    def test_list_discussions_only(self, tmp_path: Path, sample_discussion: Discussion):
        save_discussion(sample_discussion, tmp_path)

        meetings = list_meetings(tmp_path)
        assert len(meetings) == 1
        assert meetings[0]["topic_id"] == sample_discussion.topic_id
        assert meetings[0]["mode"] == "discuss"
        assert meetings[0]["moderator"] == sample_discussion.moderator

    def test_list_mixed_meetings_and_discussions(
        self, tmp_path: Path, sample_meeting: Meeting, sample_discussion: Discussion
    ):
        save_meeting(sample_meeting, tmp_path)
        save_discussion(sample_discussion, tmp_path)

        meetings = list_meetings(tmp_path)
        assert len(meetings) == 2
        modes = {m["mode"] for m in meetings}
        assert modes == {"meeting", "discuss"}

    def test_corrupted_json_skipped(self, tmp_path: Path, sample_meeting: Meeting):
        save_meeting(sample_meeting, tmp_path)
        # Corrupt the JSON
        meta_path = tmp_path / "meetings" / sample_meeting.topic_id / "meeting.json"
        meta_path.write_text("{ invalid json", encoding="utf-8")

        meetings = list_meetings(tmp_path)
        assert len(meetings) == 0

    def test_topic_truncated_in_list(self, tmp_path: Path, sample_discussion: Discussion):
        sample_discussion.user_idea = "a" * 100
        save_discussion(sample_discussion, tmp_path)

        meetings = list_meetings(tmp_path)
        assert len(meetings[0]["topic"]) <= 53  # 50 + "..."
