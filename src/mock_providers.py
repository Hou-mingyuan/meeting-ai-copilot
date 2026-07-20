from __future__ import annotations

import hashlib
import queue
import threading
import time
from collections.abc import Iterable, Iterator

from app_contracts import AppError, AppErrorCode, AudioChunk, TranscriptEvent

DEFAULT_FIXTURE_TRANSCRIPT = "请解释一下 Redis 缓存和 MySQL 索引分别解决什么问题？"


class DeterministicMockAsrProvider:
    name = "mock-deterministic"

    def __init__(
        self,
        *,
        expected_text: str = DEFAULT_FIXTURE_TRANSCRIPT,
        expected_pcm_sha256: str = "",
        disconnect_after_chunk: int | None = None,
    ) -> None:
        self.expected_text = expected_text
        self.expected_pcm_sha256 = expected_pcm_sha256
        self.disconnect_after_chunk = disconnect_after_chunk
        self._disconnect_emitted = False

    def transcribe_chunks(self, chunks: Iterable[AudioChunk]) -> Iterator[TranscriptEvent]:
        collected = bytearray()
        last_sequence = 0
        for chunk in chunks:
            if (
                self.disconnect_after_chunk is not None
                and chunk.sequence == self.disconnect_after_chunk
                and not self._disconnect_emitted
            ):
                self._disconnect_emitted = True
                raise AppError(AppErrorCode.NETWORK, "Mock ASR 模拟断线", retryable=True)
            collected.extend(chunk.pcm)
            last_sequence = chunk.sequence
            if chunk.sequence in {5, 12, 20}:
                length = {5: 8, 12: 20, 20: 34}[chunk.sequence]
                yield TranscriptEvent(
                    event_id=f"mock-partial-{chunk.sequence}",
                    sequence=chunk.sequence,
                    text=self.expected_text[:length],
                    is_final=False,
                    source="Mock ASR（固定音频）",
                )
        digest = hashlib.sha256(bytes(collected)).hexdigest()
        if self.expected_pcm_sha256 and digest != self.expected_pcm_sha256:
            raise AppError(
                AppErrorCode.PROVIDER_PROTOCOL,
                "固定音频 fixture 校验失败",
                retryable=False,
            )
        yield TranscriptEvent(
            event_id=f"mock-final-{digest[:16]}",
            sequence=max(1, last_sequence),
            text=self.expected_text,
            is_final=True,
            source="Mock ASR（固定音频）",
        )

    async def run(
        self,
        audio_queue: "queue.Queue[AudioChunk]",
        stop_event: threading.Event,
        on_transcript,
    ) -> None:
        chunks: list[AudioChunk] = []
        idle_since = time.monotonic()
        while not stop_event.is_set() or not audio_queue.empty():
            try:
                chunks.append(await __import__("asyncio").to_thread(audio_queue.get, True, 0.1))
                idle_since = time.monotonic()
            except queue.Empty:
                if stop_event.is_set() or time.monotonic() - idle_since > 0.5:
                    break
        for event in self.transcribe_chunks(chunks):
            on_transcript(event)
