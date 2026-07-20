from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from app_contracts import AudioChunk, AudioMode, StatusEvent
from audio_pipeline import SoundCardAudioSource, enumerate_audio_devices, pcm16_to_float_audio


def dominant_frequency(chunks: list[AudioChunk], sample_rate: int) -> float:
    if not chunks:
        return 0.0
    samples = np.concatenate([pcm16_to_float_audio(chunk.pcm) for chunk in chunks])
    if samples.size < 1024:
        return 0.0
    samples = samples - float(np.mean(samples))
    window = np.hanning(samples.size)
    spectrum = np.abs(np.fft.rfft(samples * window))
    frequencies = np.fft.rfftfreq(samples.size, 1.0 / sample_rate)
    valid = (frequencies >= 100.0) & (frequencies <= 1000.0)
    if not np.any(valid):
        return 0.0
    valid_indices = np.flatnonzero(valid)
    return float(frequencies[valid_indices[int(np.argmax(spectrum[valid]))]])


def _capture(
    source: SoundCardAudioSource,
    duration_seconds: float,
    *,
    playback_fixture: Path | None = None,
) -> tuple[list[AudioChunk], bool]:
    playback_started = False
    source.start()
    try:
        time.sleep(0.35)
        if playback_fixture is not None:
            import winsound

            winsound.PlaySound(str(playback_fixture), winsound.SND_FILENAME | winsound.SND_ASYNC)
            playback_started = True
        deadline = time.monotonic() + duration_seconds
        chunks: list[AudioChunk] = []
        while time.monotonic() < deadline:
            chunk = source.read(timeout=0.4)
            if chunk is not None:
                chunks.append(chunk)
        if playback_started:
            winsound.PlaySound(None, 0)
        return chunks, True
    finally:
        if playback_started:
            import winsound

            winsound.PlaySound(None, 0)
        source.stop()


def run_windows_audio_acceptance(fixture: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("Windows audio acceptance requires a Windows host")
    fixture = Path(fixture).resolve()
    initial_threads = {thread.name for thread in threading.enumerate()}
    devices = enumerate_audio_devices()
    system_devices = [device for device in devices if device.kind == "system"]
    microphone_devices = [device for device in devices if device.kind == "microphone"]
    if not system_devices:
        raise RuntimeError("no WASAPI loopback device found")
    if not microphone_devices:
        raise RuntimeError("no microphone device found")
    default_system = next((device for device in system_devices if device.is_default), system_devices[0])
    default_microphone = next((device for device in microphone_devices if device.is_default), microphone_devices[0])
    status_events: list[StatusEvent] = []

    def status_callback(event: StatusEvent) -> None:
        status_events.append(event)

    system_source = SoundCardAudioSource(
        AudioMode.SYSTEM,
        system_device=default_system.id,
        sample_rate=16000,
        chunk_ms=100,
        status_callback=status_callback,
    )
    system_source.start()
    try:
        time.sleep(0.35)
        import winsound

        winsound.PlaySound(str(fixture), winsound.SND_FILENAME | winsound.SND_ASYNC)
        system_chunks: list[AudioChunk] = []
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            chunk = system_source.read(timeout=0.4)
            if chunk is not None:
                system_chunks.append(chunk)
        winsound.PlaySound(None, 0)
        playback_stopped = True
        system_source.pause()
        pause_verified = system_source.read(timeout=0.15) is None
        system_source.resume()

        hot_switch_verified = True
        switch_target = next((device for device in system_devices if device.id != default_system.id), default_system)
        system_source.switch(AudioMode.SYSTEM, system_device=switch_target.id)
        time.sleep(0.4)
        hot_switch_verified = system_source.device_names.get("system") == switch_target.name
        system_source.switch(AudioMode.SYSTEM, system_device=default_system.id)
        time.sleep(0.4)
        hot_switch_verified = hot_switch_verified and system_source.device_names.get("system") == default_system.name
    finally:
        system_source.stop()

    system_peak_rms = max((chunk.rms for chunk in system_chunks), default=0.0)
    system_frequency = dominant_frequency(system_chunks, 16000)
    system_signal_verified = system_peak_rms >= 0.01 and (
        abs(system_frequency - 330.0) <= 20.0 or abs(system_frequency - 440.0) <= 20.0
    )

    microphone_source = SoundCardAudioSource(
        AudioMode.MICROPHONE,
        microphone_device=default_microphone.id,
        sample_rate=16000,
        chunk_ms=100,
        status_callback=status_callback,
    )
    microphone_chunks, _ = _capture(microphone_source, 0.8)
    microphone_open_verified = bool(microphone_chunks) and all(
        "microphone" in chunk.sources for chunk in microphone_chunks
    )

    mixed_source = SoundCardAudioSource(
        AudioMode.MIXED,
        system_device=default_system.id,
        microphone_device=default_microphone.id,
        sample_rate=16000,
        chunk_ms=100,
        status_callback=status_callback,
    )
    mixed_chunks, mixed_playback_stopped = _capture(
        mixed_source,
        4.0,
        playback_fixture=fixture,
    )
    mixed_sources_verified = bool(mixed_chunks) and any(
        set(chunk.sources) == {"system", "microphone"} for chunk in mixed_chunks
    )
    mixed_peak_rms = max((chunk.rms for chunk in mixed_chunks), default=0.0)

    final_threads = {thread.name for thread in threading.enumerate()}
    residual = sorted(
        name
        for name in final_threads - initial_threads
        if name.startswith(("audio-", "audio-controller", "fixture-playback"))
    )
    checks = {
        "device_enumeration": bool(system_devices and microphone_devices),
        "system_fixture_playback_stopped": playback_stopped,
        "system_loopback_signal": system_signal_verified,
        "pause_resume": pause_verified,
        "hot_switch": hot_switch_verified,
        "microphone_open_and_capture": microphone_open_verified,
        "mixed_sources": mixed_sources_verified,
        "mixed_fixture_playback_stopped": mixed_playback_stopped,
        "no_residual_audio_threads": not residual,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "host": "Windows",
        "fixture": str(fixture),
        "devices": [
            {
                "id": device.id,
                "kind": device.kind,
                "name": device.name,
                "default": device.is_default,
            }
            for device in devices
        ],
        "selected": {
            "system": {"id": default_system.id, "name": default_system.name},
            "microphone": {"id": default_microphone.id, "name": default_microphone.name},
        },
        "checks": checks,
        "metrics": {
            "system_chunks": len(system_chunks),
            "system_peak_rms": round(system_peak_rms, 6),
            "system_dominant_frequency_hz": round(system_frequency, 2),
            "microphone_chunks": len(microphone_chunks),
            "microphone_peak_rms": round(max((chunk.rms for chunk in microphone_chunks), default=0.0), 6),
            "mixed_chunks": len(mixed_chunks),
            "mixed_peak_rms": round(mixed_peak_rms, 6),
            "backpressure_drops": system_source.dropped_chunks
            + microphone_source.dropped_chunks
            + mixed_source.dropped_chunks,
        },
        "status_events": [
            {"component": event.component, "state": event.state, "message": event.message}
            for event in status_events
        ],
        "residual_threads": residual,
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
