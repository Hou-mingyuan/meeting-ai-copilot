from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app_contracts import AnswerRequest, TranscriptEvent
from session_store import JsonSessionStore


def make_store(tmp_path: Path) -> JsonSessionStore:
    return JsonSessionStore(
        tmp_path,
        audio_mode="mixed",
        devices=["speaker", "microphone"],
        asr_provider="mock",
        llm_provider="mock",
        consent_confirmed=True,
        session_id="session-test",
    )


def test_structured_session_and_exports(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add_transcript(TranscriptEvent("p1", 1, "临时", False, "system", 100.0))
    store.add_transcript(TranscriptEvent("f1", 2, "最终问题？", True, "system", 101.0))
    request = AnswerRequest("a1", store.session_id, "最终问题？", "上下文", "manual", True, 102.0)
    store.begin_answer(request)
    store.append_answer_delta("a1", "参考内容")
    store.finish_answer("a1")
    store.close()

    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["privacy"]["consent_confirmed"] is True
    assert data["state"] == "stopped"
    assert data["transcripts"][1]["text"] == "最终问题？"
    assert data["answers"][0]["label"] == "AI 参考答案（非会议原话）"
    assert not store.path.with_suffix(".json.tmp").exists()

    markdown = Path(store.export("md")).read_text(encoding="utf-8")
    text = Path(store.export("txt")).read_text(encoding="utf-8")
    assert "AI 参考答案" in markdown and "不是会议原话" in markdown
    assert "最终问题？" in text and "参考内容" in text


def test_cross_day_timestamps_are_preserved(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    day_one = datetime(2026, 7, 19, 23, 59, tzinfo=timezone.utc).timestamp()
    day_two = datetime(2026, 7, 20, 0, 1, tzinfo=timezone.utc).timestamp()
    store.add_transcript(TranscriptEvent("d1", 1, "第一天", True, "system", day_one))
    store.add_transcript(TranscriptEvent("d2", 2, "第二天", True, "system", day_two))
    data = store.data
    assert data["transcripts"][0]["at"] != data["transcripts"][1]["at"]


def test_retention_only_removes_owned_old_session_files(tmp_path: Path) -> None:
    old_session = tmp_path / "session-old.json"
    unrelated = tmp_path / "notes.json"
    old_session.write_text("{}", encoding="utf-8")
    unrelated.write_text("{}", encoding="utf-8")
    old_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old_session, (old_timestamp, old_timestamp))
    os.utime(unrelated, (old_timestamp, old_timestamp))
    removed = JsonSessionStore.purge_old(
        tmp_path,
        30,
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    assert removed == [old_session]
    assert unrelated.exists()


def test_atomic_replace_retries_transient_windows_lock(tmp_path: Path, monkeypatch) -> None:
    import session_store

    real_replace = session_store.os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporary file lock")
        return real_replace(source, target)

    monkeypatch.setattr(session_store.os, "replace", flaky_replace)
    store = make_store(tmp_path)
    assert attempts == 3
    assert store.path.is_file()
