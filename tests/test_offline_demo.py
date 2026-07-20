from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from offline_demo import run_offline_acceptance

FIXTURE = Path(__file__).parent / "fixtures" / "meeting_question.wav"
ROOT = Path(__file__).resolve().parent.parent


def test_fixed_fixture_mock_e2e_is_complete(tmp_path: Path) -> None:
    report = run_offline_acceptance(FIXTURE, tmp_path)
    assert report["status"] == "passed"
    assert report["audio_chunks"] == 30
    assert report["pause_resume_verified"] is True
    assert report["asr_disconnects_recovered"] == 1
    assert report["partial_events"] == 3
    assert report["question_triggered"] is True
    assert report["ai_cancel_verified"] is True
    assert report["ai_retry_calls"] == 2
    assert report["answer"].startswith("【Mock 参考】")
    assert report["residual_threads"] == []
    assert Path(report["session_json"]).is_file()
    assert Path(report["export_markdown"]).is_file()
    assert Path(report["export_text"]).is_file()


def test_fixed_fixture_repeated_runs_are_comparable(tmp_path: Path) -> None:
    first = run_offline_acceptance(FIXTURE, tmp_path / "first")
    second = run_offline_acceptance(FIXTURE, tmp_path / "second")
    comparable_keys = ["wav_sha256", "pcm_sha256", "audio_chunks", "final_text", "answer"]
    assert {key: first[key] for key in comparable_keys} == {key: second[key] for key in comparable_keys}


def test_session_json_does_not_mislabel_ai_as_transcript(tmp_path: Path) -> None:
    report = run_offline_acceptance(FIXTURE, tmp_path)
    data = json.loads(Path(report["session_json"]).read_text(encoding="utf-8"))
    assert all(item["source"].startswith("Mock ASR") for item in data["transcripts"])
    assert all(item["label"] == "AI 参考答案（非会议原话）" for item in data["answers"])


def test_offline_cli_handles_legacy_windows_console_encoding(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [
            sys.executable,
            "src/cloud_asr_volcengine.py",
            "--mock-demo",
            "--fixture",
            str(FIXTURE),
            "--output-directory",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert result.returncode == 0, stderr
    stdout = result.stdout.decode("utf-8")
    assert "请解释一下 Redis 缓存和 MySQL 索引分别解决什么问题？" in stdout
    assert "MOCK ACCEPTANCE PASSED" in stdout
