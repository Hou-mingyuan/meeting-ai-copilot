from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app_contracts import AudioChunk, AudioMode
from audio_pipeline import (
    DropOldestAudioBuffer,
    FixtureAudioSource,
    fixture_sha256,
    float_audio_to_pcm16,
    mix_pcm16,
    normalize_float_audio,
    pcm16_rms,
    stable_device_id,
)
from mock_providers import DeterministicMockAsrProvider

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_metadata() -> dict:
    return json.loads((FIXTURES / "meeting_question.json").read_text(encoding="utf-8"))


def read_fixture_chunks() -> list[AudioChunk]:
    source = FixtureAudioSource(FIXTURES / "meeting_question.wav")
    source.start()
    chunks: list[AudioChunk] = []
    while not source.finished:
        chunk = source.read()
        if chunk is not None:
            chunks.append(chunk)
    source.stop()
    return chunks


def test_normalize_converts_stereo_and_sample_rate() -> None:
    source = np.column_stack(
        [np.linspace(-0.5, 0.5, 800, dtype=np.float32), np.linspace(0.5, -0.5, 800, dtype=np.float32)]
    )
    converted = normalize_float_audio(source, 8000, 16000)
    assert converted.shape == (1600,)
    assert float(np.max(np.abs(converted))) < 0.001


def test_normalize_applies_gain_and_clips() -> None:
    converted = normalize_float_audio(np.array([0.75, -0.75], dtype=np.float32), 16000, 16000, gain=2.0)
    assert converted.tolist() == [1.0, -1.0]


def test_mix_pcm_averages_inputs() -> None:
    positive = float_audio_to_pcm16(np.array([0.5, 0.5], dtype=np.float32))
    negative = float_audio_to_pcm16(np.array([-0.5, -0.5], dtype=np.float32))
    assert pcm16_rms(mix_pcm16([positive, negative])) < 0.0001


def test_drop_oldest_buffer_preserves_low_latency() -> None:
    buffer = DropOldestAudioBuffer(2)
    assert buffer.put(AudioChunk(1, b"\x00\x00")) is False
    assert buffer.put(AudioChunk(2, b"\x00\x00")) is False
    assert buffer.put(AudioChunk(3, b"\x00\x00")) is True
    assert buffer.dropped_chunks == 1
    assert [item.sequence for item in buffer.drain()] == [2, 3]


def test_fixed_fixture_hash_and_chunk_shape() -> None:
    metadata = fixture_metadata()
    path = FIXTURES / metadata["fixture"]
    assert fixture_sha256(path) == metadata["wav_sha256"]
    chunks = read_fixture_chunks()
    assert len(chunks) == 30
    assert [chunk.sequence for chunk in chunks] == list(range(1, 31))
    assert all(chunk.sources == ("fixture",) for chunk in chunks)
    pcm = b"".join(chunk.pcm for chunk in chunks)
    assert hashlib.sha256(pcm).hexdigest() == metadata["pcm_sha256"]
    assert max(chunk.rms for chunk in chunks) > 0.1


def test_fixture_pause_resume_and_stop() -> None:
    source = FixtureAudioSource(FIXTURES / "meeting_question.wav")
    source.start()
    first = source.read()
    source.pause()
    assert source.read(timeout=0.001) is None
    source.resume()
    second = source.read()
    source.stop()
    assert first is not None and second is not None
    assert second.sequence == first.sequence + 1
    assert source.finished is True


def test_deterministic_mock_asr_uses_fixture_bytes() -> None:
    metadata = fixture_metadata()
    provider = DeterministicMockAsrProvider(
        expected_text=metadata["expected_final"],
        expected_pcm_sha256=metadata["pcm_sha256"],
    )
    events = list(provider.transcribe_chunks(read_fixture_chunks()))
    assert [event.sequence for event in events[:-1]] == [5, 12, 20]
    assert events[-1].is_final is True
    assert events[-1].text == metadata["expected_final"]
    assert events[-1].source.startswith("Mock ASR")


def test_audio_mode_values_are_config_stable() -> None:
    assert {mode.value for mode in AudioMode} == {"system", "microphone", "mixed", "fixture"}


def test_device_id_uses_native_endpoint_not_enumeration_order() -> None:
    first = SimpleNamespace(id="endpoint-guid-1", name="Speaker")
    renamed = SimpleNamespace(id="endpoint-guid-1", name="Renamed Speaker")
    other = SimpleNamespace(id="endpoint-guid-2", name="Speaker")
    assert stable_device_id("system", first) == stable_device_id("system", renamed)
    assert stable_device_id("system", first) != stable_device_id("system", other)
    assert stable_device_id("system", first).startswith("system:")
