from __future__ import annotations

from dataclasses import replace

from app_contracts import AppErrorCode, AudioChunk, TranscriptEvent
from asr_resilience import (
    AudioReplayBuffer,
    ReconnectBudget,
    ReconnectPolicy,
    TranscriptReconciler,
    classify_connection_error,
    classify_provider_response_error,
)


class HttpFailure(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def transcript(sequence: int, text: str, final: bool, event_id: str = "") -> TranscriptEvent:
    return TranscriptEvent(event_id or f"event-{sequence}", sequence, text, final, "test")


def test_reconnect_policy_is_bounded_exponential() -> None:
    policy = ReconnectPolicy(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=2.0)
    assert [policy.delay(attempt) for attempt in range(1, 6)] == [0.5, 1.0, 2.0, 2.0, 2.0]
    assert policy.delay(4, retry_after=30) == 2.0


def test_reconnect_budget_resets_after_stable_connection() -> None:
    budget = ReconnectBudget(ReconnectPolicy(max_attempts=2, stable_reset_seconds=10))
    assert budget.record_disconnect(1) == 1
    assert budget.record_disconnect(2) == 2
    assert budget.exhausted is False
    assert budget.record_disconnect(11) == 1
    assert budget.record_disconnect(1) == 2
    assert budget.record_disconnect(1) == 3
    assert budget.exhausted is True


def test_connection_error_classification() -> None:
    assert classify_connection_error(HttpFailure(403)).code == AppErrorCode.AUTHENTICATION
    assert classify_connection_error(HttpFailure(403)).retryable is False
    assert classify_connection_error(HttpFailure(429)).code == AppErrorCode.RATE_LIMIT
    assert classify_connection_error(HttpFailure(429)).retryable is True
    assert classify_connection_error(TimeoutError()).code == AppErrorCode.TIMEOUT


def test_provider_response_error_classification_is_bounded_and_safe() -> None:
    denied = classify_provider_response_error(45000030, "requested resource not granted")
    assert denied.code == AppErrorCode.AUTHENTICATION
    assert denied.retryable is False
    limited = classify_provider_response_error("provider-x", "QPS rate limit")
    assert limited.code == AppErrorCode.RATE_LIMIT
    assert limited.retryable is True
    unavailable = classify_provider_response_error(55000030, "service unavailable")
    assert unavailable.code == AppErrorCode.NETWORK
    assert unavailable.retryable is True
    invalid = classify_provider_response_error(45000001, "invalid model")
    assert invalid.code == AppErrorCode.PROVIDER_PROTOCOL
    assert invalid.retryable is False
    assert classify_connection_error(HttpFailure(503)).retryable is True


def test_replay_buffer_deduplicates_and_confirms() -> None:
    replay = AudioReplayBuffer(3)
    for sequence in [1, 2, 2, 3, 4]:
        replay.append(AudioChunk(sequence, b"\x00\x00"))
    assert [item.sequence for item in replay.snapshot()] == [2, 3, 4]
    replay.confirm_through(3)
    assert [item.sequence for item in replay.snapshot()] == [4]
    replay.confirm_through()
    assert len(replay) == 0


def test_reconciler_rejects_stale_partial_and_duplicate_final() -> None:
    reconciler = TranscriptReconciler()
    assert reconciler.accept(transcript(3, "正在识别", False)) is not None
    assert reconciler.accept(transcript(2, "旧 partial", False)) is None
    final = transcript(4, "最终问题？", True, "utterance-1")
    assert reconciler.accept(final) == final
    assert reconciler.accept(replace(final, sequence=8)) is None
    assert reconciler.accept(transcript(9, "最终问题？", True, "new-id")) is None
