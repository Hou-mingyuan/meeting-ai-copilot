#!/usr/bin/env python3
"""Generate the deterministic PCM fixture committed under tests/fixtures."""

from __future__ import annotations

import argparse
import hashlib
import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 16000
DURATION_SECONDS = 3.0


def build_samples() -> list[int]:
    samples: list[int] = []
    total = int(SAMPLE_RATE * DURATION_SECONDS)
    for index in range(total):
        second = index / SAMPLE_RATE
        if second < 0.25 or 1.35 < second < 1.6 or second > 2.75:
            value = 0.0
        else:
            frequency = 330.0 if second < 1.35 else 440.0
            carrier = math.sin(2.0 * math.pi * frequency * second)
            modulation = 0.65 + 0.35 * math.sin(2.0 * math.pi * 3.0 * second)
            value = 0.32 * carrier * modulation
        samples.append(int(max(-1.0, min(1.0, value)) * 32767))
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/meeting_question.wav"),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pcm = b"".join(struct.pack("<h", sample) for sample in build_samples())
    with wave.open(str(args.output), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(pcm)
    print(f"fixture={args.output}")
    print(f"wav_sha256={hashlib.sha256(args.output.read_bytes()).hexdigest()}")
    print(f"pcm_sha256={hashlib.sha256(pcm).hexdigest()}")
    print(f"frames={len(pcm) // 2} sample_rate={SAMPLE_RATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
