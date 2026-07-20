from __future__ import annotations

from urllib import error as urllib_error

import pytest

from app_contracts import AnswerRequest, AppError, AppErrorCode, CancellationToken
from llm_providers import DeterministicMockLlmProvider, HttpSseLlmProvider, stream_with_recovery


def request() -> AnswerRequest:
    return AnswerRequest("req-1", "session-1", "Redis 和索引有什么区别？", "上一句上下文", "manual", True)


def test_mock_llm_is_deterministic() -> None:
    first = "".join(DeterministicMockLlmProvider().stream(request(), CancellationToken()))
    second = "".join(DeterministicMockLlmProvider().stream(request(), CancellationToken()))
    assert first == second
    assert first.startswith("【Mock 参考】")


def test_stream_recovers_from_retryable_disconnect_without_duplicate_text() -> None:
    provider = DeterministicMockLlmProvider(fail_first=True)
    answer = "".join(
        stream_with_recovery(
            provider,
            request(),
            CancellationToken(),
            base_delay_seconds=0,
        )
    )
    assert provider.calls == 2
    assert answer.count("【Mock 参考】") == 1


def test_cancel_stops_stream() -> None:
    cancel = CancellationToken()
    cancel.cancel("test")
    with pytest.raises(AppError) as caught:
        list(stream_with_recovery(DeterministicMockLlmProvider(), request(), cancel))
    assert caught.value.code == AppErrorCode.CANCELLED


def test_answer_character_boundary_is_enforced() -> None:
    answer = "".join(
        stream_with_recovery(
            DeterministicMockLlmProvider(),
            request(),
            CancellationToken(),
            max_answer_chars=12,
        )
    )
    assert len(answer) == 12


class FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def __iter__(self):
        return iter(self.lines)


def live_config() -> dict:
    return {
        "ai_base_url": "https://example.invalid/v1",
        "ai_model": "test-model",
        "ai_wire_api": "responses",
        "ai_timeout_seconds": 2,
        "ai_stream_idle_timeout_seconds": 1,
    }


def test_sse_early_eof_is_retryable_disconnect(monkeypatch) -> None:
    response = FakeResponse(
        [
            b'data: {"type":"response.created"}\n',
            b'data: {"type":"response.output_text.delta","delta":"partial"}\n',
        ]
    )
    monkeypatch.setattr("llm_providers.urllib_request.urlopen", lambda *args, **kwargs: response)
    with pytest.raises(AppError) as caught:
        list(HttpSseLlmProvider(live_config(), "key").stream(request(), CancellationToken()))
    assert caught.value.code == AppErrorCode.NETWORK
    assert caught.value.retryable is True


def test_sse_completed_event_is_success(monkeypatch) -> None:
    response = FakeResponse(
        [
            b'data: {"type":"response.output_text.delta","delta":"ok"}\n',
            b'data: {"type":"response.completed"}\n',
        ]
    )
    monkeypatch.setattr("llm_providers.urllib_request.urlopen", lambda *args, **kwargs: response)
    assert "".join(HttpSseLlmProvider(live_config(), "key").stream(request(), CancellationToken())) == "ok"


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable"),
    [
        (401, AppErrorCode.AUTHENTICATION, False),
        (429, AppErrorCode.RATE_LIMIT, True),
        (503, AppErrorCode.NETWORK, True),
    ],
)
def test_http_errors_are_classified(monkeypatch, status, expected_code, retryable) -> None:
    def fail(*args, **kwargs):
        raise urllib_error.HTTPError("https://example.invalid", status, "failure", {"Retry-After": "1"}, None)

    monkeypatch.setattr("llm_providers.urllib_request.urlopen", fail)
    with pytest.raises(AppError) as caught:
        list(HttpSseLlmProvider(live_config(), "key").stream(request(), CancellationToken()))
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable


def test_sse_failure_event_is_not_misreported_as_disconnect(monkeypatch) -> None:
    response = FakeResponse(
        [b'data: {"type":"response.failed","response":{"error":{"code":"content_policy"}}}\n']
    )
    monkeypatch.setattr("llm_providers.urllib_request.urlopen", lambda *args, **kwargs: response)
    with pytest.raises(AppError) as caught:
        list(HttpSseLlmProvider(live_config(), "key").stream(request(), CancellationToken()))
    assert caught.value.code == AppErrorCode.PROVIDER_PROTOCOL
    assert caught.value.retryable is False
