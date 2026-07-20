from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterator, Protocol, runtime_checkable


class AudioMode(str, Enum):
    SYSTEM = "system"
    MICROPHONE = "microphone"
    MIXED = "mixed"
    FIXTURE = "fixture"


class AppErrorCode(str, Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    DEVICE_UNAVAILABLE = "device_unavailable"
    INVALID_CONFIG = "invalid_config"
    PROVIDER_PROTOCOL = "provider_protocol"
    INTERNAL = "internal"


class AppError(RuntimeError):
    """An operator-safe error shared by audio, ASR, and LLM providers."""

    def __init__(
        self,
        code: AppErrorCode,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True)
class AudioDeviceInfo:
    id: str
    name: str
    kind: str
    is_default: bool = False
    is_loopback: bool = False


@dataclass(frozen=True)
class AudioChunk:
    sequence: int
    pcm: bytes
    captured_at: float = field(default_factory=time.time)
    rms: float = 0.0
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class TranscriptEvent:
    event_id: str
    sequence: int
    text: str
    is_final: bool
    source: str
    received_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class AnswerRequest:
    request_id: str
    session_id: str
    question: str
    context: str
    source: str
    manual: bool = False
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class StatusEvent:
    component: str
    state: str
    message: str = ""
    occurred_at: float = field(default_factory=time.time)


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "user") -> None:
        self.reason = reason
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise AppError(AppErrorCode.CANCELLED, "操作已取消", retryable=False)


StatusCallback = Callable[[StatusEvent], None]
TranscriptCallback = Callable[[TranscriptEvent], None]


@runtime_checkable
class AudioSource(Protocol):
    mode: AudioMode

    def start(self) -> None: ...

    def read(self, timeout: float = 1.0) -> AudioChunk | None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def switch(
        self,
        mode: AudioMode,
        system_device: str | None = None,
        microphone_device: str | None = None,
    ) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class AsrProvider(Protocol):
    name: str

    async def run(
        self,
        audio_queue: "queue.Queue[AudioChunk]",
        stop_event: threading.Event,
        on_transcript: TranscriptCallback,
    ) -> None: ...


@runtime_checkable
class LlmProvider(Protocol):
    name: str

    def stream(self, request: AnswerRequest, cancel: CancellationToken) -> Iterator[str]: ...


@runtime_checkable
class QuestionDetectorProtocol(Protocol):
    def evaluate(self, text: str, *, manual: bool = False, now: float | None = None): ...


@runtime_checkable
class SessionStoreProtocol(Protocol):
    session_id: str

    def add_transcript(self, event: TranscriptEvent) -> None: ...

    def begin_answer(self, request: AnswerRequest) -> None: ...

    def append_answer_delta(self, request_id: str, delta: str) -> None: ...

    def finish_answer(self, request_id: str, status: str = "completed", error: str = "") -> None: ...

    def export(self, format_name: str) -> str: ...
