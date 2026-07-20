from __future__ import annotations

import json
import time
from typing import Any, Iterator
from urllib import error as urllib_error
from urllib import request as urllib_request

from app_contracts import AnswerRequest, AppError, AppErrorCode, CancellationToken, LlmProvider


def _extract_delta(data: dict[str, Any]) -> str:
    event_type = str(data.get("type") or "")
    if event_type == "response.output_text.delta":
        delta = data.get("delta")
        return delta if isinstance(delta, str) else ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            return str(delta["content"])
    return ""


def _sse_failure(data: dict[str, Any]) -> AppError | None:
    event_type = str(data.get("type") or "").casefold()
    if event_type not in {"error", "response.failed", "response.incomplete"}:
        return None
    response = data.get("response") if isinstance(data.get("response"), dict) else {}
    error = data.get("error") or response.get("error") or response.get("incomplete_details") or {}
    if isinstance(error, dict):
        code = str(error.get("code") or error.get("type") or event_type)
        detail = str(error.get("message") or code).casefold()
        status = error.get("status") or error.get("status_code")
    else:
        code = event_type
        detail = str(error).casefold()
        status = None
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    combined = f"{code.casefold()} {detail}"
    if status_code in {401, 403} or any(marker in combined for marker in ("auth", "api_key", "permission")):
        return AppError(AppErrorCode.AUTHENTICATION, "AI 鉴权或模型权限失败", status_code=status_code)
    if status_code == 429 or any(marker in combined for marker in ("rate_limit", "too many", "quota")):
        return AppError(AppErrorCode.RATE_LIMIT, "AI 请求受到限流", retryable=True, status_code=status_code)
    if status_code is not None and status_code >= 500 or any(
        marker in combined for marker in ("server_error", "service_unavailable", "overloaded")
    ):
        return AppError(AppErrorCode.NETWORK, "AI 服务暂时不可用", retryable=True, status_code=status_code)
    return AppError(AppErrorCode.PROVIDER_PROTOCOL, "AI 服务未完成本次回答，请手动重试", retryable=False)


class DeterministicMockLlmProvider:
    name = "mock-deterministic"

    def __init__(self, token_delay_seconds: float = 0.0, fail_first: bool = False) -> None:
        self.token_delay_seconds = max(0.0, token_delay_seconds)
        self.fail_first = fail_first
        self.calls = 0

    def stream(self, request: AnswerRequest, cancel: CancellationToken) -> Iterator[str]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise AppError(AppErrorCode.NETWORK, "Mock AI 模拟断线", retryable=True)
        tokens = (
            "【Mock 参考】",
            "Redis 缓存减少热点读取延迟；",
            "MySQL 索引缩小查询扫描范围。",
            "两者解决的问题不同，可配合使用。",
        )
        for token in tokens:
            cancel.raise_if_cancelled()
            if self.token_delay_seconds:
                cancel.wait(self.token_delay_seconds)
                cancel.raise_if_cancelled()
            yield token


class HttpSseLlmProvider:
    def __init__(self, config: dict[str, Any], api_key: str = "") -> None:
        self.config = config
        self.api_key = api_key
        self.name = str(config.get("ai_provider_name") or "OpenAI-compatible SSE")

    def _url_and_payload(self, request: AnswerRequest) -> tuple[str, dict[str, Any]]:
        base_url = str(self.config.get("ai_base_url") or "").rstrip("/")
        model = str(self.config.get("ai_model") or "").strip()
        if not base_url or not model:
            raise AppError(AppErrorCode.INVALID_CONFIG, "AI 地址或模型未配置")
        system_prompt = str(self.config.get("ai_system_prompt") or "")
        context = request.context.strip()
        user_text = f"会议上下文：\n{context}\n\n当前问题：{request.question}" if context else request.question
        wire_api = str(self.config.get("ai_wire_api") or "responses").lower()
        if wire_api in {"responses", "response"}:
            return (
                f"{base_url}/responses",
                {
                    "model": model,
                    "instructions": system_prompt,
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": user_text}]}],
                    "tools": [],
                    "stream": True,
                },
            )
        if wire_api in {"chat", "chat_completions", "chat.completions"}:
            return (
                f"{base_url}/chat/completions",
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "stream": True,
                },
            )
        raise AppError(AppErrorCode.INVALID_CONFIG, f"不支持的 AI wire API：{wire_api}")

    def stream(self, request: AnswerRequest, cancel: CancellationToken) -> Iterator[str]:
        if not self.api_key:
            raise AppError(AppErrorCode.AUTHENTICATION, "AI API Key 未配置")
        url, payload = self._url_and_payload(request)
        timeout = max(1.0, float(self.config.get("ai_timeout_seconds", 90)))
        idle_timeout = min(timeout, max(1.0, float(self.config.get("ai_stream_idle_timeout_seconds", 10))))
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = urllib_request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib_request.urlopen(http_request, timeout=idle_timeout) as response:
                completed = False
                received_event = False
                for raw_line in response:
                    cancel.raise_if_cancelled()
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    if data_text == "[DONE]":
                        completed = True
                        break
                    if not data_text:
                        continue
                    try:
                        event = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    received_event = True
                    failure = _sse_failure(event)
                    if failure is not None:
                        raise failure
                    delta = _extract_delta(event)
                    if delta:
                        yield delta
                    if event.get("type") in {"response.completed", "response.done"}:
                        completed = True
                if not completed:
                    detail = "在完成事件前断开" if received_event else "未返回有效事件"
                    raise AppError(AppErrorCode.NETWORK, f"AI SSE {detail}", retryable=True)
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            if status in {401, 403}:
                raise AppError(
                    AppErrorCode.AUTHENTICATION,
                    "AI 鉴权失败，请检查凭证与模型权限",
                    status_code=status,
                ) from exc
            if status == 429:
                retry_after_text = exc.headers.get("Retry-After") if exc.headers else None
                retry_after = float(retry_after_text) if retry_after_text and retry_after_text.isdigit() else None
                raise AppError(
                    AppErrorCode.RATE_LIMIT,
                    "AI 请求受到限流",
                    retryable=True,
                    status_code=status,
                    retry_after=retry_after,
                ) from exc
            if status == 408:
                raise AppError(
                    AppErrorCode.TIMEOUT,
                    "AI 请求超时",
                    retryable=True,
                    status_code=status,
                ) from exc
            raise AppError(
                AppErrorCode.NETWORK,
                f"AI 服务暂时不可用（HTTP {status}）",
                retryable=status >= 500,
                status_code=status,
            ) from exc
        except urllib_error.URLError as exc:
            raise AppError(AppErrorCode.NETWORK, "AI 网络连接失败", retryable=True) from exc
        except TimeoutError as exc:
            raise AppError(AppErrorCode.TIMEOUT, "AI 响应超时", retryable=True) from exc
        except OSError as exc:
            raise AppError(AppErrorCode.NETWORK, "AI 网络连接中断", retryable=True) from exc


def stream_with_recovery(
    provider: LlmProvider,
    request: AnswerRequest,
    cancel: CancellationToken,
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.25,
    timeout_seconds: float = 90.0,
    max_answer_chars: int = 8000,
) -> Iterator[str]:
    emitted = ""
    started = time.monotonic()
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        cancel.raise_if_cancelled()
        attempt_text = ""
        try:
            for token in provider.stream(request, cancel):
                cancel.raise_if_cancelled()
                if time.monotonic() - started > timeout_seconds:
                    raise AppError(AppErrorCode.TIMEOUT, "AI 总响应时间超出限制", retryable=False)
                attempt_text += token
                if attempt > 1:
                    if emitted.startswith(attempt_text):
                        continue
                    if not attempt_text.startswith(emitted):
                        raise AppError(
                            AppErrorCode.PROVIDER_PROTOCOL,
                            "AI 重连后的内容与已输出内容不一致，请手动重试",
                            retryable=False,
                        )
                    token = attempt_text[len(emitted) :]
                remaining = max_answer_chars - len(emitted)
                if remaining <= 0:
                    return
                safe_token = token[:remaining]
                if safe_token:
                    emitted += safe_token
                    yield safe_token
                if len(emitted) >= max_answer_chars:
                    return
            return
        except AppError as exc:
            if exc.code == AppErrorCode.CANCELLED or not exc.retryable or attempt >= attempts:
                raise
            delay = exc.retry_after if exc.retry_after is not None else base_delay_seconds * (2 ** (attempt - 1))
            if cancel.wait(min(8.0, max(0.0, delay))):
                cancel.raise_if_cancelled()
    raise AppError(AppErrorCode.INTERNAL, "AI 重试状态异常")


def create_llm_provider(config: dict[str, Any], api_key: str = "") -> LlmProvider:
    provider = str(config.get("ai_provider") or "").strip().lower()
    model = str(config.get("ai_model") or "").strip().lower()
    if provider == "mock" or model == "mock":
        return DeterministicMockLlmProvider(float(config.get("mock_ai_token_delay_seconds", 0.02)))
    return HttpSseLlmProvider(config, api_key)
