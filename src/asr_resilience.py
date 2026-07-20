from __future__ import annotations

import collections
import re
from dataclasses import dataclass

from app_contracts import AppError, AppErrorCode, AudioChunk, TranscriptEvent


@dataclass(frozen=True)
class ReconnectPolicy:
    max_attempts: int = 6
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    stable_reset_seconds: float = 30.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(self.max_delay_seconds, max(0.0, retry_after))
        exponent = max(0, attempt - 1)
        return min(self.max_delay_seconds, self.base_delay_seconds * (2**exponent))


class ReconnectBudget:
    def __init__(self, policy: ReconnectPolicy) -> None:
        self.policy = policy
        self.attempts = 0

    def record_disconnect(self, connected_seconds: float) -> int:
        if connected_seconds >= self.policy.stable_reset_seconds:
            self.attempts = 0
        self.attempts += 1
        return self.attempts

    @property
    def exhausted(self) -> bool:
        return self.attempts > self.policy.max_attempts


def classify_connection_error(exc: BaseException) -> AppError:
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, PermissionError):
        return AppError(AppErrorCode.INTERNAL, "本地文件或设备权限不足", retryable=False)
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return AppError(AppErrorCode.PROVIDER_PROTOCOL, "ASR 响应或本地状态无效", retryable=False)

    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    text = str(exc).lower()
    match = re.search(r"\b(401|403|429|5\d\d)\b", text)
    if status is None and match:
        status = int(match.group(1))

    if status in {401, 403}:
        return AppError(
            AppErrorCode.AUTHENTICATION,
            "ASR 鉴权失败，请检查凭证与资源权限",
            retryable=False,
            status_code=status,
        )
    if status == 429:
        return AppError(
            AppErrorCode.RATE_LIMIT,
            "ASR 请求受到限流",
            retryable=True,
            status_code=status,
        )
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return AppError(AppErrorCode.TIMEOUT, "ASR 连接超时", retryable=True)
    if status is not None and int(status) >= 500:
        return AppError(
            AppErrorCode.NETWORK,
            f"ASR 服务暂时不可用（HTTP {status}）",
            retryable=True,
            status_code=int(status),
        )
    return AppError(AppErrorCode.NETWORK, "ASR 网络连接中断", retryable=True)


def classify_provider_response_error(code: object, message: object = "") -> AppError:
    """Classify an ASR protocol-level error without exposing provider payloads."""

    code_text = str(code).strip()
    try:
        numeric_code = int(code_text)
    except (TypeError, ValueError):
        numeric_code = None
    detail = str(message or "").casefold()

    auth_markers = (
        "unauthorized",
        "forbidden",
        "authentication",
        "api key",
        "invalid token",
        "resource not granted",
        "permission denied",
        "鉴权",
        "未授权",
        "无权限",
    )
    if numeric_code in {401, 403, 45000030} or any(marker in detail for marker in auth_markers):
        return AppError(
            AppErrorCode.AUTHENTICATION,
            f"ASR 鉴权或资源权限失败（code={code_text or 'unknown'}）",
            retryable=False,
            status_code=numeric_code,
        )

    rate_markers = ("rate limit", "too many requests", "quota", "qps", "限流", "配额", "并发上限")
    if numeric_code == 429 or any(marker in detail for marker in rate_markers):
        return AppError(
            AppErrorCode.RATE_LIMIT,
            f"ASR 请求受到限流（code={code_text or 'unknown'}）",
            retryable=True,
            status_code=numeric_code,
        )

    if numeric_code == 45000081 or "timeout" in detail or "超时" in detail:
        return AppError(
            AppErrorCode.TIMEOUT,
            f"ASR 服务等待音频超时（code={code_text or 'unknown'}）",
            retryable=True,
            status_code=numeric_code,
        )

    if (numeric_code is not None and numeric_code >= 50_000_000) or any(
        marker in detail for marker in ("service unavailable", "server error", "internal error", "服务不可用")
    ):
        return AppError(
            AppErrorCode.NETWORK,
            f"ASR 服务暂时不可用（code={code_text or 'unknown'}）",
            retryable=True,
            status_code=numeric_code,
        )

    return AppError(
        AppErrorCode.PROVIDER_PROTOCOL,
        f"ASR 服务拒绝请求（code={code_text or 'unknown'}）",
        retryable=False,
        status_code=numeric_code,
    )


class AudioReplayBuffer:
    """Keeps unconfirmed chunks so a new ASR connection can replay them."""

    def __init__(self, max_chunks: int) -> None:
        self._chunks: collections.deque[AudioChunk] = collections.deque(maxlen=max(1, max_chunks))

    def append(self, chunk: AudioChunk) -> None:
        if self._chunks and self._chunks[-1].sequence == chunk.sequence:
            return
        self._chunks.append(chunk)

    def snapshot(self) -> list[AudioChunk]:
        return list(self._chunks)

    def confirm_through(self, sequence: int | None = None) -> None:
        if sequence is None:
            self._chunks.clear()
            return
        while self._chunks and self._chunks[0].sequence <= sequence:
            self._chunks.popleft()

    def __len__(self) -> int:
        return len(self._chunks)


class TranscriptReconciler:
    """Filters stale partials and repeated finals across reconnects."""

    def __init__(self, max_final_history: int = 200, duplicate_window_seconds: float = 15.0) -> None:
        self._last_partial_sequence = -1
        self._finals: collections.deque[tuple[str, str, float]] = collections.deque(maxlen=max_final_history)
        self.duplicate_window_seconds = max(0.0, duplicate_window_seconds)

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.casefold().split())

    def accept(self, event: TranscriptEvent) -> TranscriptEvent | None:
        normalized = self._normalize(event.text)
        if not normalized:
            return None

        if event.is_final:
            event_id = event.event_id.strip()
            for prior_id, prior_text, prior_at in self._finals:
                if event_id and prior_id and event_id == prior_id:
                    return None
                if prior_text == normalized and abs(event.received_at - prior_at) <= self.duplicate_window_seconds:
                    return None
            self._finals.append((event_id, normalized, event.received_at))
            return event

        if event.sequence <= self._last_partial_sequence:
            return None
        self._last_partial_sequence = event.sequence
        return event

    def begin_connection(self) -> None:
        self._last_partial_sequence = -1
