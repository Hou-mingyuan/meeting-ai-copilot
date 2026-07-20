from __future__ import annotations

from pathlib import Path

from status_tui import StatusTui
from tui_demo import run_mock_tui_demo


def test_mock_tui_demo_finishes_without_threads(monkeypatch) -> None:
    fixture = Path(__file__).parent / "fixtures" / "meeting_question.wav"
    monkeypatch.setattr("tui_demo.time.sleep", lambda _: None)
    ticks = iter([0.0, 0.0, 1.1, 1.1])
    monkeypatch.setattr("tui_demo.time.monotonic", lambda: next(ticks, 1.1))
    report = run_mock_tui_demo(fixture, 1.0, tui=StatusTui(enabled=False))
    assert report["status"] == "completed"
    assert report["residual_threads"] == []
