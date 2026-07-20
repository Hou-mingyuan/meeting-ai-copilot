from __future__ import annotations

import queue
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app_contracts import AppError, AppErrorCode, AudioMode
from audio_pipeline import DropOldestAudioBuffer, SoundCardAudioSource
from cloud_asr_volcengine import audio_capture_worker
from cloud_runtime import Logger


class FakeRecorder:
    calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def record(self, numframes: int):
        FakeRecorder.calls += 1
        if FakeRecorder.calls == 1:
            raise RuntimeError("device disappeared")
        return np.full((numframes, 1), 0.1, dtype=np.float32)


class FakeDevice:
    name = "Fake Loopback"
    isloopback = True

    def recorder(self, **kwargs):
        return FakeRecorder()


class DeniedDevice(FakeDevice):
    def recorder(self, **kwargs):
        raise PermissionError("microphone privacy denied")


def test_device_disappearance_recovers_without_leaking_thread(monkeypatch) -> None:
    FakeRecorder.calls = 0
    device = FakeDevice()
    fake_soundcard = SimpleNamespace(
        all_microphones=lambda include_loopback=True: [device],
        default_speaker=lambda: SimpleNamespace(name="Fake Loopback"),
        get_microphone=lambda name, include_loopback=True: device,
    )
    monkeypatch.setitem(sys.modules, "soundcard", fake_soundcard)
    events = []
    source = SoundCardAudioSource(
        AudioMode.SYSTEM,
        sample_rate=16000,
        chunk_ms=20,
        status_callback=events.append,
    )
    source.start()
    try:
        deadline = time.monotonic() + 2
        chunk = None
        while chunk is None and time.monotonic() < deadline:
            chunk = source.read(timeout=0.2)
        assert chunk is not None
        assert chunk.rms > 0.05
        assert any(event.state == "device_recovering" for event in events)
        assert sum(event.state == "device_ready" for event in events) >= 2
    finally:
        source.stop()
    assert source.active_thread_names == []


def test_permission_denied_is_fatal_and_operator_safe(monkeypatch) -> None:
    device = DeniedDevice()
    fake_soundcard = SimpleNamespace(
        all_microphones=lambda include_loopback=True: [device],
        default_speaker=lambda: SimpleNamespace(name="Fake Loopback"),
        get_microphone=lambda name, include_loopback=True: device,
    )
    monkeypatch.setitem(sys.modules, "soundcard", fake_soundcard)
    events = []
    source = SoundCardAudioSource(AudioMode.SYSTEM, chunk_ms=20, status_callback=events.append)
    source.start()
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                source.read(timeout=0.05)
            except AppError as exc:
                assert exc.code == AppErrorCode.DEVICE_UNAVAILABLE
                assert "privacy denied" not in exc.safe_message
                break
        else:
            pytest.fail("permission denial was not propagated")
    finally:
        source.stop()
    assert any(event.state == "permission_denied" for event in events)
    assert source.active_thread_names == []


def test_capture_controller_propagates_fatal_error(tmp_path) -> None:
    class FailedSource:
        mode = AudioMode.MICROPHONE
        active_thread_names = []

        def start(self):
            return None

        def read(self, timeout=0.5):
            raise AppError(AppErrorCode.DEVICE_UNAVAILABLE, "麦克风权限不足")

        def stop(self):
            return None

    stop = threading.Event()
    errors = queue.Queue(maxsize=1)
    audio_capture_worker(
        DropOldestAudioBuffer(2),
        stop,
        FailedSource(),
        Logger(tmp_path / "capture.log"),
        error_queue=errors,
    )
    assert stop.is_set()
    assert errors.get_nowait().code == AppErrorCode.DEVICE_UNAVAILABLE
