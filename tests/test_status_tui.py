from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from app_contracts import AudioChunk, AudioMode
from asr_resilience import AudioReplayBuffer
from audio_pipeline import DropOldestAudioBuffer
from cloud_asr_volcengine import handle_tui_commands
from cloud_runtime import Logger
from status_tui import StatusTui, display_width, enable_windows_virtual_terminal, fit_display


def test_fit_display_handles_wide_characters() -> None:
    value = fit_display("系统声音 microphone device", 12)
    assert display_width(value) == 12
    assert value.endswith("…") or "系统" in value


def test_tui_snapshot_has_stable_dimensions() -> None:
    tui = StatusTui(enabled=False)
    tui.set_privacy_confirmed(True)
    tui.set_mode("系统声音 + 麦克风")
    tui.set_devices("这是一个非常长的设备名称 Realtek Audio Device With Extra Words")
    tui.set_last_line("请解释一下非常长的 Redis 缓存和 MySQL 索引问题，确保终端不会横向溢出")
    tui.set_answer("【参考】这是一个很长的流式答案，用于验证所有宽度都不会溢出或破坏边框")
    for width in [40, 60, 100, 140]:
        lines = tui.snapshot(width)
        assert len(lines) == tui.PANEL_LINES
        assert all(display_width(line) == width for line in lines)


def test_tui_commands_are_bounded_and_retrievable() -> None:
    tui = StatusTui(enabled=False)
    assert tui.submit_command("toggle_pause") is True
    assert tui.get_command() == "toggle_pause"
    assert tui.get_command() is None
    assert tui.KEY_COMMANDS["d"] == "select_devices"


def test_tui_labels_ai_as_reference() -> None:
    tui = StatusTui(enabled=False)
    assert "AI 内容仅供参考" in tui.render_text(80)
    tui.set_answer("")
    tui.append_answer_delta("第一段")
    tui.append_answer_delta("第二段")
    rendered = tui.render_text(80)
    assert "AI参考答案" in rendered
    assert "第一段第二段" in rendered


def test_windows_virtual_terminal_setup_is_nonfatal() -> None:
    assert isinstance(enable_windows_virtual_terminal(), bool)


def test_runtime_device_selection_applies_both_stable_ids(tmp_path, monkeypatch) -> None:
    class FakeSource:
        mode = AudioMode.MIXED
        system_device = None
        microphone_device = None
        switched = None

        def switch(self, mode, system_device=None, microphone_device=None):
            self.switched = (mode, system_device, microphone_device)

    class FakeStore:
        def __init__(self):
            self.events = []

        def record_event(self, *event):
            self.events.append(event)

    tui = StatusTui(enabled=False)
    answers = iter(["system:abc123", "microphone:def456"])
    monkeypatch.setattr(tui, "prompt", lambda label, default="": next(answers))
    tui.submit_command("select_devices")
    tui.submit_command("stop")
    source = FakeSource()
    store = FakeStore()
    stop = threading.Event()
    audio_buffer = DropOldestAudioBuffer(3)
    replay_buffer = AudioReplayBuffer(3)
    asyncio.run(
        handle_tui_commands(
            tui,
            source,
            audio_buffer,
            replay_buffer,
            stop,
            SimpleNamespace(),
            None,
            SimpleNamespace(),
            store,
            Logger(tmp_path / "tui.log"),
        )
    )
    assert source.switched == (AudioMode.MIXED, "system:abc123", "microphone:def456")
    assert store.events[-1][1] == "device_selected"


def test_pause_discards_unsent_and_replay_audio(tmp_path) -> None:
    class FakeSource:
        mode = AudioMode.SYSTEM
        system_device = None
        microphone_device = None
        paused = False

        def pause(self):
            self.paused = True

    tui = StatusTui(enabled=False)
    tui.submit_command("toggle_pause")
    tui.submit_command("stop")
    source = FakeSource()
    audio_buffer = DropOldestAudioBuffer(3)
    audio_buffer.put(AudioChunk(1, b"\x00\x00"))
    replay_buffer = AudioReplayBuffer(3)
    replay_buffer.append(AudioChunk(1, b"\x00\x00"))
    asyncio.run(
        handle_tui_commands(
            tui,
            source,
            audio_buffer,
            replay_buffer,
            threading.Event(),
            SimpleNamespace(),
            None,
            SimpleNamespace(),
            None,
            Logger(tmp_path / "pause.log"),
        )
    )
    assert source.paused is True
    assert len(audio_buffer) == 0
    assert len(replay_buffer) == 0
