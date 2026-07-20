from __future__ import annotations

import numpy as np

from app_contracts import AudioChunk
from audio_pipeline import float_audio_to_pcm16
from windows_audio_acceptance import dominant_frequency


def test_dominant_frequency_detects_fixture_tone() -> None:
    sample_rate = 16000
    positions = np.arange(sample_rate, dtype=np.float32) / sample_rate
    samples = 0.25 * np.sin(2 * np.pi * 330.0 * positions)
    chunk = AudioChunk(1, float_audio_to_pcm16(samples), rms=0.17, sources=("system",))
    assert abs(dominant_frequency([chunk], sample_rate) - 330.0) < 1.0
