from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import queue
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import websockets
from volcengine_audio.stt import VolcengineAsrFunctionsV3

from app_contracts import AnswerRequest, AppError, AudioChunk, AudioMode, StatusEvent, TranscriptEvent
from asr_resilience import (
    AudioReplayBuffer,
    ReconnectBudget,
    ReconnectPolicy,
    TranscriptReconciler,
    classify_connection_error,
    classify_provider_response_error,
)
from audio_pipeline import DropOldestAudioBuffer, SoundCardAudioSource, enumerate_audio_devices, format_device_table
from cloud_runtime import (
    AiTaskController,
    Logger,
    ai_answer_worker,
    append_ai_answer_delta,
    append_transcript_today,
    build_paths,
    diagnose_environment,
    finish_ai_answer_stream,
    is_ai_ready,
    is_mock_ai,
    is_question_like,
    load_config,
    start_ai_answer_stream_today,
    stream_ai_answer_api,
    write_partial_transcript_today,
)
from question_detection import QuestionDetector
from session_store import JsonSessionStore
from status_tui import StatusTui
from transcript_question_fsm import PartialQuestionState, on_partial_update, on_receive_timeout


def configure_console_streams() -> None:
    # Redirected Windows consoles may default to cp1252 even though the CLI emits Chinese text.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def get_cloud_asr_key(config: dict[str, Any], field: str, env_name: str) -> str:
    import os

    environment_value = os.environ.get(env_name, "").strip()
    if environment_value:
        return environment_value
    value = str(config.get(field) or "").strip()
    if value:
        return value
    return ""


def validate_cloud_asr_config(config: dict[str, Any], logger: Logger) -> bool:
    missing: list[str] = []
    api_key = get_cloud_asr_key(config, "cloud_asr_api_key", "VOLC_ASR_API_KEY")
    app_key = get_cloud_asr_key(config, "cloud_asr_app_key", "VOLC_ASR_APP_KEY")
    access_key = get_cloud_asr_key(config, "cloud_asr_access_key", "VOLC_ASR_ACCESS_KEY")
    if not api_key and not (app_key and access_key):
        missing.append("cloud_asr_api_key / VOLC_ASR_API_KEY")
    if not str(config.get("cloud_asr_resource_id") or "").strip():
        missing.append("cloud_asr_resource_id")

    if missing:
        logger.write("火山实时 ASR 未配置，缺少：" + "，".join(missing))
        logger.write("请检查 config.json，或设置 VOLC_ASR_API_KEY 后重试。")
        return False
    return True


def build_volcengine_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {
        "X-Api-Resource-Id": str(config.get("cloud_asr_resource_id") or "volc.seedasr.sauc.duration"),
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }
    api_key = get_cloud_asr_key(config, "cloud_asr_api_key", "VOLC_ASR_API_KEY")
    if api_key:
        headers["X-Api-Key"] = api_key
        return headers

    headers["X-Api-App-Key"] = get_cloud_asr_key(config, "cloud_asr_app_key", "VOLC_ASR_APP_KEY")
    headers["X-Api-Access-Key"] = get_cloud_asr_key(config, "cloud_asr_access_key", "VOLC_ASR_ACCESS_KEY")
    return headers


def build_start_request(config: dict[str, Any]) -> dict[str, Any]:
    hotwords = [str(x) for x in config.get("cloud_asr_hotwords") or [] if str(x).strip()]

    corpus: dict[str, Any] = {}
    # 自学习平台词表：在火山控制台建好热词表/替换词表后，把 id/name 填进 config 即可生效，无需改代码。
    boosting_id = str(config.get("cloud_asr_boosting_table_id") or "").strip()
    boosting_name = str(config.get("cloud_asr_boosting_table_name") or "").strip()
    correct_name = str(config.get("cloud_asr_correct_table_name") or "").strip()
    use_table = bool(boosting_id or boosting_name)

    # 内联热词：火山要求 context 里【只放】{"hotwords":[{"word":...}]}，不能混 context_type/context_data，
    # 否则服务端解析不到、热词不生效（这正是之前“加了事务还是听成税务”的根因）。双向流式上限约 100 tokens。
    # 注意：内联直传优先级高于词表、会盖过词表，所以一旦配了词表就不再发内联，让词表真正生效。
    inline_limit = max(1, int(config.get("cloud_asr_hotword_limit", 30)))
    if hotwords and not use_table:
        corpus["context"] = json.dumps(
            {"hotwords": [{"word": word} for word in hotwords[:inline_limit]]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    if boosting_id:
        corpus["boosting_table_id"] = boosting_id
    if boosting_name:
        corpus["boosting_table_name"] = boosting_name
    if correct_name:
        corpus["correct_table_name"] = correct_name

    return {
        "user": {"uid": "meeting-live-transcriber"},
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": int(config.get("cloud_asr_sample_rate", 16000)),
            "bits": 16,
            "channel": 1,
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "enable_nonstream": False,
            "enable_accelerate_text": True,
            "accelerate_score": 8,
            "vad_segment_duration": 800,
            "end_window_size": 300,
            "force_to_speech_time": 500,
            "corpus": corpus,
        },
    }


def extract_text_from_response(message: Any) -> tuple[str, bool]:
    if not isinstance(message, dict):
        return "", False

    text = ""
    definite = False
    result = message.get("result")
    if isinstance(result, dict):
        text = str(result.get("text") or "").strip()
        utterances = result.get("utterances")
        if isinstance(utterances, list):
            definite = any(bool(u.get("definite")) for u in utterances if isinstance(u, dict))

    if not text:
        payload = message.get("payload")
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict):
                text = str(result.get("text") or "").strip()

    return text, definite


def extract_utterances(message: Any) -> list[dict[str, Any]]:
    # 火山 result.text 是“从头到现在的累计全文”，直接用会越滚越长（整段拼在后面）。
    # 这里改取分句列表 result.utterances，每个元素是一句，自带 definite（是否已说完）。
    if not isinstance(message, dict):
        return []
    containers = [message.get("result")]
    payload = message.get("payload")
    if isinstance(payload, dict):
        containers.append(payload.get("result"))
    for container in containers:
        if isinstance(container, dict):
            utterances = container.get("utterances")
            if isinstance(utterances, list):
                cleaned: list[dict[str, Any]] = []
                for item in utterances:
                    if isinstance(item, dict):
                        sentence = str(item.get("text") or "").strip()
                        if sentence:
                            stable_id = str(
                                item.get("utterance_id")
                                or item.get("id")
                                or item.get("start_time")
                                or item.get("start")
                                or ""
                            )
                            cleaned.append(
                                {
                                    "text": sentence,
                                    "definite": bool(item.get("definite")),
                                    "event_id": stable_id,
                                }
                            )
                if cleaned:
                    return cleaned
    return []


def audio_capture_worker(
    audio_buffer: DropOldestAudioBuffer,
    stop_event: threading.Event,
    source: SoundCardAudioSource,
    logger: Logger,
    status_tui: StatusTui | None = None,
    error_queue: "queue.Queue[BaseException] | None" = None,
) -> None:
    try:
        source.start()
        logger.write(f"音频采集已启动：mode={source.mode.value}")
        if status_tui is not None:
            status_tui.set_capture("采集中")
        last_drop_notice = 0
        while not stop_event.is_set():
            chunk = source.read(timeout=0.5)
            if chunk is None:
                continue
            dropped = audio_buffer.put(chunk)
            if dropped and audio_buffer.dropped_chunks >= last_drop_notice + 10:
                last_drop_notice = audio_buffer.dropped_chunks
                logger.write(f"音频背压：已丢弃最旧块 count={last_drop_notice}")
    except Exception as exc:
        logger.write(f"音频采集控制器失败：type={type(exc).__name__}")
        if error_queue is not None:
            try:
                error_queue.put_nowait(exc)
            except queue.Full:
                pass
        stop_event.set()
    finally:
        source.stop()
        if status_tui is not None:
            status_tui.set_capture("已停止")


class TranscriptRuntimeState:
    def __init__(self, config: dict[str, Any]) -> None:
        self.detector = QuestionDetector(config)
        self.max_context_chars = max(0, int(config.get("ai_context_max_chars", 4000)))
        self._finals: collections.deque[str] = collections.deque(maxlen=200)
        self._lock = threading.Lock()
        self.last_text = ""

    def add_final(self, text: str) -> None:
        with self._lock:
            self.last_text = text
            self._finals.append(text)

    def set_partial(self, text: str) -> None:
        with self._lock:
            self.last_text = text

    def context(self, exclude: str = "") -> str:
        with self._lock:
            items = list(self._finals)
        if exclude and items and items[-1] == exclude:
            items = items[:-1]
        text = "\n".join(items)
        return text[-self.max_context_chars :] if self.max_context_chars else ""


def create_audio_source(
    config: dict[str, Any],
    logger: Logger,
    status_tui: StatusTui | None,
) -> SoundCardAudioSource:
    mode = AudioMode(str(config.get("audio_mode") or "system"))
    ready_devices: dict[str, str] = {}

    def on_status(event: StatusEvent) -> None:
        if event.state == "device_ready":
            logger.write(f"音频设备就绪：{event.message}")
        elif event.state == "device_recovering":
            logger.write(f"音频设备恢复中：{event.message}")
        elif event.state == "silence":
            logger.write(f"音频静音检测：source={event.message}")
        elif event.state == "stop_timeout":
            logger.write(f"音频线程停止超时：{event.message}")
        if status_tui is not None:
            if event.state == "device_ready":
                label, _, name = event.message.partition(":")
                ready_devices[label] = name or event.message
                status_tui.set_devices(" | ".join(ready_devices[key] for key in sorted(ready_devices)))
            elif event.state == "switching":
                ready_devices.clear()
                status_tui.set_devices("正在重新选择设备")
            elif event.state == "device_recovering":
                status_tui.set_capture("设备恢复中")
            elif event.state == "permission_denied":
                status_tui.set_capture("权限不足")
                status_tui.set_notice("无法读取音频设备；请检查 Windows 麦克风隐私权限")
            elif event.state == "silence":
                status_tui.set_notice("当前输入持续静音，请检查设备或会议音量")

    return SoundCardAudioSource(
        mode,
        sample_rate=int(config.get("cloud_asr_sample_rate", 16000)),
        chunk_ms=int(config.get("cloud_asr_chunk_ms", 100)),
        system_device=config.get("system_audio_device") or config.get("audio_device"),
        microphone_device=config.get("microphone_audio_device"),
        system_gain=float(config.get("system_audio_gain", 1.0)),
        microphone_gain=float(config.get("microphone_audio_gain", 1.0)),
        silence_threshold=float(config.get("audio_silence_threshold", 0.0005)),
        status_callback=on_status,
    )


class MeetingTranscriptCoordinator:
    def __init__(
        self,
        config: dict[str, Any],
        paths,
        logger: Logger,
        ai_queue: "queue.Queue[AnswerRequest] | None",
        runtime_state: TranscriptRuntimeState,
        session_store: JsonSessionStore | None,
        status_tui: StatusTui | None,
    ) -> None:
        self.config = config
        self.paths = paths
        self.logger = logger
        self.ai_queue = ai_queue
        self.runtime_state = runtime_state
        self.session_store = session_store
        self.status_tui = status_tui
        self.reconciler = TranscriptReconciler()
        self.partial_state = PartialQuestionState()

    def begin_connection(self) -> None:
        self.reconciler.begin_connection()

    def _enqueue_question(self, text: str, *, manual: bool, source: str) -> bool:
        if self.ai_queue is None:
            return False
        allowed_sources = self.config.get("ai_source_labels") or ["云端实时ASR"]
        if not manual and isinstance(allowed_sources, list) and "*" not in allowed_sources and source not in allowed_sources:
            return False
        detection = self.runtime_state.detector.evaluate(text, manual=manual)
        if not detection.accepted:
            return False
        request = AnswerRequest(
            request_id=uuid.uuid4().hex,
            session_id=str(getattr(self.session_store, "session_id", "runtime")),
            question=text.strip(),
            context=self.runtime_state.context(exclude=text),
            source=source,
            manual=manual,
        )
        try:
            self.ai_queue.put_nowait(request)
        except queue.Full:
            self.logger.write("AI 队列已满，本次问题未提交")
            if self.status_tui is not None:
                self.status_tui.set_notice("AI 队列已满，可取消当前答案后重试")
            return False
        self.logger.write(
            f"AI 问题已排队：manual={manual}，chars={len(request.question)}，context_chars={len(request.context)}"
        )
        if self.status_tui is not None:
            self.status_tui.set_ai("排队")
        return True

    def enqueue_manual(self, text: str, source: str = "用户手动触发") -> bool:
        return self._enqueue_question(text, manual=True, source=source)

    def on_idle(self) -> None:
        self.partial_state, ask_text = on_receive_timeout(
            self.partial_state,
            self.config,
            time.monotonic(),
        )
        if ask_text:
            self._enqueue_question(ask_text, manual=False, source="云端实时ASR")

    def on_transcript(self, event: TranscriptEvent) -> None:
        accepted = self.reconciler.accept(event)
        if accepted is None:
            return
        if accepted.is_final:
            self.partial_state = PartialQuestionState()
            self.runtime_state.add_final(accepted.text)
            append_transcript_today(self.paths, self.config, accepted.text, accepted.source)
            if self.session_store is not None:
                self.session_store.add_transcript(accepted)
            self.logger.write(
                "ASR final 已确认："
                f"chars={len(accepted.text)}，digest={hashlib.sha256(accepted.text.encode('utf-8')).hexdigest()[:12]}"
            )
            self._enqueue_question(accepted.text, manual=False, source=accepted.source)
        else:
            self.runtime_state.set_partial(accepted.text)
            write_partial_transcript_today(self.paths, self.config, accepted.text, f"{accepted.source}-临时")
            self.logger.write(f"ASR partial 已更新：chars={len(accepted.text)}")
            self.partial_state, ask_text = on_partial_update(
                self.partial_state,
                accepted.text,
                self.config,
                time.monotonic(),
            )
            if ask_text:
                self._enqueue_question(ask_text, manual=False, source=accepted.source)
        if self.status_tui is not None:
            self.status_tui.set_last_line(accepted.text)


def provider_error_from_response(parsed: dict[str, Any]) -> AppError | None:
    response_code = parsed.get("code")
    message = parsed.get("message")
    if response_code is not None:
        return classify_provider_response_error(response_code, message)
    if not isinstance(message, dict) or not message.get("error"):
        return None
    error_value = message["error"]
    if isinstance(error_value, dict):
        code = error_value.get("code") or error_value.get("status_code") or message.get("code") or "unknown"
        detail = error_value.get("message") or error_value
    else:
        code = message.get("code") or message.get("status_code") or "unknown"
        detail = error_value
    return classify_provider_response_error(code, detail)


async def receive_responses(
    websocket,
    stop_event: threading.Event,
    on_transcript,
    on_idle,
    replay_buffer: AudioReplayBuffer,
) -> None:
    last_partial = ""
    fallback_sequence = 0
    while not stop_event.is_set():
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=0.4)
        except asyncio.TimeoutError:
            on_idle()
            continue

        parsed = VolcengineAsrFunctionsV3.parse_response(raw)
        provider_error = provider_error_from_response(parsed)
        if provider_error is not None:
            raise provider_error
        message = parsed.get("message")
        is_last = bool(parsed.get("is_last_package"))
        utterances = extract_utterances(message)
        fallback_sequence += 1
        response_sequence = abs(int(parsed.get("sequence") or fallback_sequence))
        now = time.time()

        if not utterances:
            if is_last:
                text, _ = extract_text_from_response(message)
                if text:
                    replay_buffer.confirm_through()
                    on_transcript(TranscriptEvent("", response_sequence, text, True, "云端实时ASR", now))
            continue

        current_partial = ""
        for utterance in utterances:
            sentence = utterance["text"]
            if utterance["definite"]:
                replay_buffer.confirm_through()
                on_transcript(
                    TranscriptEvent(
                        str(utterance.get("event_id") or ""),
                        response_sequence,
                        sentence,
                        True,
                        "云端实时ASR",
                        now,
                    )
                )
            else:
                current_partial = sentence
        if current_partial and current_partial != last_partial:
            last_partial = current_partial
            on_transcript(TranscriptEvent("", response_sequence, current_partial, False, "云端实时ASR", now))


async def send_audio(
    websocket,
    audio_queue: "queue.Queue[AudioChunk]",
    stop_event: threading.Event,
    logger: Logger,
    replay_buffer: AudioReplayBuffer,
) -> None:
    provider_sequence = 2
    for chunk in replay_buffer.snapshot():
        packet = VolcengineAsrFunctionsV3.generate_asr_audio_only_request(
            provider_sequence,
            chunk.pcm,
            compress=True,
        )
        await websocket.send(bytes(packet))
        provider_sequence += 1

    while not stop_event.is_set():
        try:
            chunk = await asyncio.to_thread(audio_queue.get, True, 0.2)
        except queue.Empty:
            continue
        replay_buffer.append(chunk)
        packet = VolcengineAsrFunctionsV3.generate_asr_audio_only_request(
            provider_sequence,
            chunk.pcm,
            compress=True,
        )
        await websocket.send(bytes(packet))
        provider_sequence += 1

    packet = VolcengineAsrFunctionsV3.generate_asr_audio_only_request(provider_sequence, b"", compress=False)
    try:
        await websocket.send(bytes(packet))
    except Exception as exc:
        logger.write(f"ASR 结束包发送失败：type={type(exc).__name__}")


class VolcengineStreamingAsrProvider:
    name = "volcengine-streaming"

    def __init__(
        self,
        config: dict[str, Any],
        logger: Logger,
        status_tui: StatusTui | None,
        replay_buffer: AudioReplayBuffer,
        on_idle,
        on_connection,
    ) -> None:
        self.config = config
        self.logger = logger
        self.status_tui = status_tui
        self.replay_buffer = replay_buffer
        self.on_idle = on_idle
        self.on_connection = on_connection

    async def run(
        self,
        audio_queue: "queue.Queue[AudioChunk]",
        stop_event: threading.Event,
        on_transcript,
    ) -> None:
        endpoint = str(
            self.config.get("cloud_asr_endpoint")
            or "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
        )
        chunk_ms = int(self.config.get("cloud_asr_chunk_ms", 100))
        policy = ReconnectPolicy(
            max_attempts=int(self.config.get("cloud_asr_reconnect_max_attempts", 6)),
            base_delay_seconds=float(self.config.get("cloud_asr_reconnect_base_seconds", 0.5)),
            max_delay_seconds=float(self.config.get("cloud_asr_reconnect_max_seconds", 8.0)),
            stable_reset_seconds=float(self.config.get("cloud_asr_reconnect_stable_seconds", 30.0)),
        )
        budget = ReconnectBudget(policy)
        self.logger.write(f"连接实时 ASR：endpoint={endpoint}")

        while not stop_event.is_set():
            connected_at = time.monotonic()
            try:
                if self.status_tui is not None:
                    self.status_tui.set_asr("连接中")
                headers = build_volcengine_headers(self.config)
                async with websockets.connect(
                    endpoint,
                    additional_headers=headers,
                    max_size=16 * 1024 * 1024,
                    open_timeout=15,
                    close_timeout=5,
                    ping_interval=float(self.config.get("cloud_asr_ping_interval_seconds", 20)),
                    ping_timeout=float(self.config.get("cloud_asr_ping_timeout_seconds", 10)),
                ) as websocket:
                    self.on_connection()
                    first_packet = VolcengineAsrFunctionsV3.generate_asr_full_client_request(
                        1,
                        build_start_request(self.config),
                        compression=True,
                    )
                    await websocket.send(bytes(first_packet))
                    self.logger.write(f"实时 ASR 已连接：chunk_ms={chunk_ms}")
                    if self.status_tui is not None:
                        self.status_tui.set_asr("已连接")
                    receiver = asyncio.create_task(
                        receive_responses(
                            websocket,
                            stop_event,
                            on_transcript,
                            self.on_idle,
                            self.replay_buffer,
                        )
                    )
                    sender = asyncio.create_task(
                        send_audio(websocket, audio_queue, stop_event, self.logger, self.replay_buffer)
                    )
                    done, pending = await asyncio.wait(
                        {receiver, sender},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        exception = task.exception()
                        if exception:
                            raise exception
                    if not stop_event.is_set():
                        raise ConnectionError("ASR connection closed")
            except Exception as exc:
                if stop_event.is_set():
                    break
                provider_error = classify_connection_error(exc)
                attempt = budget.record_disconnect(time.monotonic() - connected_at)
                self.logger.write(
                    f"ASR 断线：code={provider_error.code.value}，retryable={provider_error.retryable}，attempt={attempt}"
                )
                if not provider_error.retryable:
                    if self.status_tui is not None:
                        self.status_tui.set_asr("鉴权失败")
                    raise provider_error
                if budget.exhausted:
                    if self.status_tui is not None:
                        self.status_tui.set_asr("重连已停止")
                    raise RuntimeError(f"ASR 重连超过上限 {policy.max_attempts}") from exc
                delay = policy.delay(attempt, provider_error.retry_after)
                if self.status_tui is not None:
                    self.status_tui.set_asr(f"重连 {attempt}/{policy.max_attempts}")
                if await asyncio.to_thread(stop_event.wait, delay):
                    break


async def handle_tui_commands(
    status_tui: StatusTui,
    source: SoundCardAudioSource,
    audio_buffer: DropOldestAudioBuffer,
    replay_buffer: AudioReplayBuffer,
    stop_event: threading.Event,
    coordinator: MeetingTranscriptCoordinator,
    ai_queue: "queue.Queue[AnswerRequest] | None",
    ai_controller: AiTaskController,
    session_store: JsonSessionStore | None,
    logger: Logger,
) -> None:
    mode_labels = {
        AudioMode.SYSTEM: "系统声音",
        AudioMode.MICROPHONE: "麦克风",
        AudioMode.MIXED: "系统声音 + 麦克风",
    }
    paused = False
    while not stop_event.is_set():
        command = await asyncio.to_thread(status_tui.get_command, 0.2)
        if command is None:
            continue
        if command == "stop":
            status_tui.set_notice("正在停止并保存会话…")
            stop_event.set()
        elif command == "toggle_pause":
            paused = not paused
            if paused:
                source.pause()
                discarded = len(audio_buffer.drain())
                replay_buffer.confirm_through()
                status_tui.set_notice(f"已暂停；清理 {discarded} 个尚未发送的音频块")
            else:
                source.resume()
                status_tui.set_notice("已恢复采集；从当前声音继续")
            status_tui.set_capture("已暂停" if paused else "采集中")
            if session_store is not None:
                session_store.set_state("paused" if paused else "recording")
                session_store.record_event("capture", "paused" if paused else "resumed")
        elif command == "ask_last":
            text = coordinator.runtime_state.last_text
            if not text or not coordinator.enqueue_manual(text):
                status_tui.set_notice("暂无可回答的转写，或问题过短")
        elif command == "edit_question":
            edited = await asyncio.to_thread(
                status_tui.prompt,
                "编辑后提交给 AI 的问题",
                coordinator.runtime_state.last_text,
            )
            if not coordinator.enqueue_manual(edited, "用户编辑后触发"):
                status_tui.set_notice("问题过短，未提交")
        elif command == "cancel_ai":
            status_tui.set_notice("已请求取消 AI" if ai_controller.cancel_active() else "当前没有 AI 任务")
        elif command == "retry_ai":
            previous = ai_controller.last_request
            if previous is None or ai_queue is None:
                status_tui.set_notice("没有可重试的 AI 问题")
            else:
                retry_request = AnswerRequest(
                    uuid.uuid4().hex,
                    previous.session_id,
                    previous.question,
                    previous.context,
                    "用户重试",
                    True,
                )
                try:
                    ai_queue.put_nowait(retry_request)
                    status_tui.set_ai("重试排队")
                except queue.Full:
                    status_tui.set_notice("AI 队列已满")
        elif command == "export":
            if session_store is None:
                status_tui.set_notice("会话保存已关闭，无法导出")
                continue
            try:
                markdown_path = session_store.export("md")
                session_store.export("txt")
                status_tui.set_notice(f"已导出 {Path(markdown_path).name}")
            except Exception as exc:
                logger.write(f"会话导出失败：type={type(exc).__name__}")
                status_tui.set_notice(f"导出失败（{type(exc).__name__}），数据仍保留在会话 JSON")
        elif command == "toggle_auto_answer":
            enabled = not coordinator.runtime_state.detector.auto_enabled
            coordinator.runtime_state.detector.set_auto_enabled(enabled)
            status_tui.set_auto_answer(enabled)
            status_tui.set_notice(f"自动回答已{'开启' if enabled else '关闭'}")
        elif command == "select_devices":
            try:
                if source.mode in {AudioMode.SYSTEM, AudioMode.MIXED}:
                    selected = await asyncio.to_thread(
                        status_tui.prompt,
                        "系统声音设备 ID/名称（先用设备列表查看；auto=默认）",
                        source.system_device or "auto",
                    )
                    source.system_device = None if selected.casefold() in {"auto", "default", "自动"} else selected
                if source.mode in {AudioMode.MICROPHONE, AudioMode.MIXED}:
                    selected = await asyncio.to_thread(
                        status_tui.prompt,
                        "麦克风设备 ID/名称（先用设备列表查看；auto=默认）",
                        source.microphone_device or "auto",
                    )
                    source.microphone_device = (
                        None if selected.casefold() in {"auto", "default", "自动"} else selected
                    )
                status_tui.set_capture("切换设备中")
                await asyncio.to_thread(
                    source.switch,
                    source.mode,
                    source.system_device,
                    source.microphone_device,
                )
                status_tui.set_capture("采集中")
                status_tui.set_notice("设备选择已应用")
                if session_store is not None:
                    session_store.record_event(
                        "audio",
                        "device_selected",
                        f"system={source.system_device or 'auto'},microphone={source.microphone_device or 'auto'}",
                    )
            except Exception as exc:
                logger.write(f"音频设备切换失败：type={type(exc).__name__}")
                status_tui.set_capture("切换失败")
                status_tui.set_notice(f"设备切换失败（{type(exc).__name__}），请检查设备 ID 后重试")
        elif command.startswith("mode_"):
            target_mode = {
                "mode_system": AudioMode.SYSTEM,
                "mode_microphone": AudioMode.MICROPHONE,
                "mode_mixed": AudioMode.MIXED,
            }[command]
            try:
                status_tui.set_capture("切换设备中")
                await asyncio.to_thread(source.switch, target_mode)
                status_tui.set_mode(mode_labels[target_mode])
                status_tui.set_capture("采集中")
                if session_store is not None:
                    session_store.record_event("audio", "switched", target_mode.value)
            except Exception as exc:
                logger.write(f"音频模式切换失败：type={type(exc).__name__}")
                status_tui.set_capture("切换失败")
                status_tui.set_notice(f"模式切换失败（{type(exc).__name__}），请检查设备后重试")


async def run_cloud_asr(
    config: dict[str, Any],
    logger: Logger,
    paths,
    status_tui: StatusTui | None = None,
    session_store: JsonSessionStore | None = None,
) -> int:
    if not validate_cloud_asr_config(config, logger):
        return 2

    stop_event = threading.Event()
    chunk_ms = int(config.get("cloud_asr_chunk_ms", 100))
    queue_seconds = float(config.get("audio_queue_seconds", 3))
    max_chunks = max(10, int(queue_seconds * 1000 / max(chunk_ms, 1)))
    audio_buffer = DropOldestAudioBuffer(max_chunks)
    replay_buffer = AudioReplayBuffer(
        max(1, int(float(config.get("cloud_asr_replay_seconds", 8)) * 1000 / max(chunk_ms, 1)))
    )
    source = create_audio_source(config, logger, status_tui)
    runtime_state = TranscriptRuntimeState(config)
    ai_controller = AiTaskController()
    audio_errors: "queue.Queue[BaseException]" = queue.Queue(maxsize=1)

    ai_queue: "queue.Queue[AnswerRequest] | None" = None
    ai_thread: threading.Thread | None = None
    if bool(config.get("ai_enabled")) and is_ai_ready(config):
        ai_queue = queue.Queue(maxsize=10)
        ai_thread = threading.Thread(
            target=ai_answer_worker,
            args=(
                ai_queue,
                stop_event,
                config,
                paths,
                logger,
                status_tui,
                session_store,
                ai_controller,
            ),
            name="ai-answer",
            daemon=False,
        )
        ai_thread.start()

    coordinator = MeetingTranscriptCoordinator(
        config,
        paths,
        logger,
        ai_queue,
        runtime_state,
        session_store,
        status_tui,
    )
    capture_thread = threading.Thread(
        target=audio_capture_worker,
        args=(audio_buffer, stop_event, source, logger, status_tui, audio_errors),
        name="audio-controller",
        daemon=False,
    )
    capture_thread.start()

    def handle_signal(signum, frame):
        logger.write(f"收到停止信号：signal={signum}")
        stop_event.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

    command_task = None
    if status_tui is not None and status_tui.enabled:
        command_task = asyncio.create_task(
            handle_tui_commands(
                status_tui,
                source,
                audio_buffer,
                replay_buffer,
                stop_event,
                coordinator,
                ai_queue,
                ai_controller,
                session_store,
                logger,
            )
        )
    provider = VolcengineStreamingAsrProvider(
        config,
        logger,
        status_tui,
        replay_buffer,
        coordinator.on_idle,
        coordinator.begin_connection,
    )
    exit_code = 0
    try:
        await provider.run(audio_buffer.queue, stop_event, coordinator.on_transcript)
    except Exception as exc:
        error = classify_connection_error(exc)
        logger.write(f"实时 ASR 停止：code={error.code.value}，message={error.safe_message}")
        exit_code = 3 if error.code.value == "authentication" else 4
    finally:
        stop_event.set()
        ai_controller.cancel_active("shutdown")
        if command_task is not None:
            command_task.cancel()
            await asyncio.gather(command_task, return_exceptions=True)
        capture_thread.join(timeout=6)
        if ai_thread is not None:
            ai_thread.join(timeout=max(5.0, float(config.get("ai_stream_idle_timeout_seconds", 10)) + 2))
        alive = []
        if capture_thread.is_alive():
            alive.append(capture_thread.name)
        if ai_thread is not None and ai_thread.is_alive():
            alive.append(ai_thread.name)
        alive.extend(source.active_thread_names)
        if alive:
            logger.write(f"退出检查失败，线程仍存活：{','.join(alive)}")
            exit_code = 5
        try:
            capture_error = audio_errors.get_nowait()
        except queue.Empty:
            capture_error = None
        if capture_error is not None:
            if isinstance(capture_error, AppError):
                logger.write(
                    f"音频采集失败：code={capture_error.code.value}，message={capture_error.safe_message}"
                )
            else:
                logger.write(f"音频采集失败：type={type(capture_error).__name__}")
            if exit_code == 0:
                exit_code = 4
    return exit_code


async def test_cloud_asr_handshake(config: dict[str, Any], logger: Logger) -> int:
    if not validate_cloud_asr_config(config, logger):
        return 2

    endpoint = str(config.get("cloud_asr_endpoint") or "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel")
    headers = build_volcengine_headers(config)
    logger.write(f"测试火山实时 ASR 握手：{endpoint}")
    async with websockets.connect(endpoint, additional_headers=headers, max_size=16 * 1024 * 1024) as websocket:
        start_request = build_start_request(config)
        first_packet = VolcengineAsrFunctionsV3.generate_asr_full_client_request(
            1,
            start_request,
            compression=True,
        )
        await websocket.send(bytes(first_packet))
        raw = await asyncio.wait_for(websocket.recv(), timeout=15)
        parsed = VolcengineAsrFunctionsV3.parse_response(raw)
        provider_error = provider_error_from_response(parsed)
        if provider_error is not None:
            raise provider_error
        logger.write(f"火山实时 ASR 握手成功：sequence={parsed.get('sequence')}, size={parsed.get('size')}")
    return 0


def run_smoke_test(config_path: Path) -> int:
    config = load_config(config_path)
    start_request = build_start_request(config)

    sample_rate = int(config.get("cloud_asr_sample_rate", 16000))
    if start_request["audio"]["rate"] != sample_rate:
        raise RuntimeError("ASR 请求采样率与配置不一致")

    resource_id = str(config.get("cloud_asr_resource_id") or "").strip()
    if not resource_id:
        raise RuntimeError("cloud_asr_resource_id 未配置")

    positive_question = "Can you explain the difference between Redis cache and MySQL index?"
    negative_text = "今天会议先同步项目进度"
    if not is_question_like(positive_question, config):
        raise RuntimeError("问题识别启发式未命中英文问题")
    if is_question_like(negative_text, config):
        raise RuntimeError("问题识别启发式误判普通陈述")

    print("SMOKE OK: config loaded")
    print("SMOKE OK: ASR start request built")
    print("SMOKE OK: AI question heuristic passed")
    print(f"cloud_asr_provider={config.get('cloud_asr_provider')}")
    print(f"ai_wire_api={config.get('ai_wire_api')}")
    print(f"ai_model={config.get('ai_model')}")
    return 0


MODE_LABELS = {
    "system": "系统声音",
    "microphone": "麦克风",
    "mixed": "系统声音 + 麦克风",
    "fixture": "固定音频 fixture",
}


def application_resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root).resolve()
    return Path(__file__).resolve().parent.parent


def confirm_capture_consent(config: dict[str, Any], paths, *, preconfirmed: bool = False) -> bool:
    mode = str(config.get("audio_mode") or "system")
    print("\n=== 开始采集前确认 ===")
    print(f"采集内容：{MODE_LABELS.get(mode, mode)}")
    print(f"ASR 去向：{config.get('cloud_asr_endpoint')}（音频会发送到该服务）")
    if bool(config.get("ai_enabled")):
        print(f"AI 去向：{config.get('ai_base_url')}（问题与受限会议上下文会发送到该服务）")
    else:
        print("AI 去向：已关闭")
    print(f"本地保存：{paths.output_dir}")
    print("AI 输出始终标记为参考答案，不会写成会议原话。")
    if preconfirmed:
        print("确认方式：命令行 --accept-privacy")
        return True
    if not sys.stdin.isatty():
        print("非交互终端未确认采集；请在明确知情后添加 --accept-privacy。", file=sys.stderr)
        return False
    answer = input("确认已告知会议参与者并开始采集？输入 Y 继续: ").strip().casefold()
    return answer in {"y", "yes"}


def main() -> int:
    configure_console_streams()
    parser = argparse.ArgumentParser(description="Windows 会议实时转写 + AI 参考答案")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--version", action="store_true", help="显示版本")
    parser.add_argument("--test-ai", action="store_true", help="只测试 AI 接口")
    parser.add_argument("--test-asr-handshake", action="store_true", help="只测试火山实时 ASR WebSocket 握手")
    parser.add_argument("--diagnose", action="store_true", help="诊断云端实时 ASR、AI、依赖和音频设备")
    parser.add_argument("--smoke-test", action="store_true", help="无密钥、无音频设备的容器/CI 快速自检")
    parser.add_argument("--mock-demo", "--offline-demo", action="store_true", help="固定音频 + Mock ASR/AI 离线闭环")
    parser.add_argument(
        "--windows-audio-acceptance",
        action="store_true",
        help="播放固定 WAV 并验收真实 Windows loopback、麦克风和混合采集",
    )
    parser.add_argument("--fixture", help="离线演示使用的 WAV fixture")
    parser.add_argument("--report", help="把验收摘要写入 JSON")
    parser.add_argument("--output-directory", help="覆盖本地输出目录")
    parser.add_argument("--list-devices", action="store_true", help="列出可用音频设备")
    parser.add_argument("--audio-mode", choices=["system", "microphone", "mixed"], help="采集模式")
    parser.add_argument("--system-device", help="系统声音设备 ID 或名称")
    parser.add_argument("--microphone-device", help="麦克风设备 ID 或名称")
    parser.add_argument("--accept-privacy", action="store_true", help="明确确认采集范围与云端数据去向")
    parser.add_argument("--tui-preview", action="store_true", help="打印 TUI 静态预览后退出")
    parser.add_argument("--tui-width", type=int, default=100, help="TUI 预览宽度")
    parser.add_argument("--mock-tui-demo", action="store_true", help="运行可录屏的固定 fixture TUI Mock 演示")
    parser.add_argument("--demo-duration", type=float, default=60.0, help="TUI Mock 演示秒数")
    parser.add_argument(
        "--tui",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="终端状态面板（采集/ASR/AI/最近一句）；默认在交互式终端开启",
    )
    args = parser.parse_args()

    project_root = application_resource_root()
    if args.version:
        print((project_root / "VERSION").read_text(encoding="utf-8").strip())
        return 0

    if args.windows_audio_acceptance:
        from windows_audio_acceptance import run_windows_audio_acceptance, write_report

        fixture = Path(args.fixture).resolve() if args.fixture else project_root / "tests" / "fixtures" / "meeting_question.wav"
        report = run_windows_audio_acceptance(fixture)
        report_text = json.dumps(report, ensure_ascii=False, indent=2)
        print(report_text)
        if args.report:
            write_report(report, Path(args.report).resolve())
        return 0 if report["status"] == "passed" else 7

    if args.mock_tui_demo:
        from tui_demo import run_mock_tui_demo

        fixture = Path(args.fixture).resolve() if args.fixture else project_root / "tests" / "fixtures" / "meeting_question.wav"
        report = run_mock_tui_demo(fixture, args.demo_duration)
        return 0 if report["status"] == "completed" and not report["residual_threads"] else 8

    config_path = Path(args.config).resolve()
    if args.smoke_test:
        return run_smoke_test(config_path)

    if args.mock_demo:
        from offline_demo import run_offline_acceptance

        fixture = Path(args.fixture).resolve() if args.fixture else project_root / "tests" / "fixtures" / "meeting_question.wav"
        if args.output_directory:
            output_dir = Path(args.output_directory).expanduser().resolve()
        else:
            mock_config = load_config(project_root / "config.mock-offline.json")
            output_dir = build_paths(mock_config).output_dir / "Mock演示"
        report = run_offline_acceptance(fixture, output_dir)
        report_text = json.dumps(report, ensure_ascii=False, indent=2)
        print(report_text)
        print("MOCK ACCEPTANCE PASSED（固定音频；未调用真实 ASR/LLM）")
        if args.report:
            report_path = Path(args.report).resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_text + "\n", encoding="utf-8", newline="\n")
        return 0

    config = load_config(config_path)
    if args.output_directory:
        config["output_directory"] = str(Path(args.output_directory).expanduser().resolve())
    if args.audio_mode:
        config["audio_mode"] = args.audio_mode
    if args.system_device:
        config["system_audio_device"] = args.system_device
    if args.microphone_device:
        config["microphone_audio_device"] = args.microphone_device
    paths = build_paths(config)
    logger = Logger(paths.log_file)

    if args.list_devices:
        print(format_device_table(enumerate_audio_devices()))
        return 0

    if args.tui_preview:
        preview = StatusTui(enabled=False, width=args.tui_width)
        preview.set_mode(MODE_LABELS.get(str(config.get("audio_mode")), str(config.get("audio_mode"))))
        preview.set_privacy_confirmed(True)
        preview.set_capture("采集中")
        preview.set_asr("已连接")
        preview.set_ai("参考答案流式生成中")
        preview.set_last_line("请解释一下 Redis 缓存和 MySQL 索引分别解决什么问题？")
        print(preview.render_text(args.tui_width))
        return 0

    if args.diagnose:
        diagnose_environment(config_path, config, paths)
        print("\n=== 稳定设备 ID ===")
        try:
            print(format_device_table(enumerate_audio_devices()))
        except Exception as exc:
            print(f"设备枚举失败：{type(exc).__name__}")
        return 0

    if args.test_ai:
        if not is_ai_ready(config):
            logger.write("AI 未配置。设置 ai_api_key 或 ai_provider=mock（见 config.mock.json）。")
            return 1
        question = "Can you explain the difference between MySQL index and Oracle index?"
        answer_file = start_ai_answer_stream_today(paths, config, question, "云端模式AI流式接口测试")
        delta_count = 0
        char_count = 0
        try:
            for delta in stream_ai_answer_api(question, config):
                append_ai_answer_delta(answer_file, delta)
                delta_count += 1
                char_count += len(delta)
            finish_ai_answer_stream(answer_file)
            mode = "Mock" if is_mock_ai(config) else "Live"
            logger.write(
                f"AI流式接口测试成功（{mode}）：chunks={delta_count}，chars={char_count}，结果已写入：{answer_file}"
            )
            return 0
        except AppError as exc:
            logger.write(f"AI流式接口测试失败：code={exc.code.value}，message={exc.safe_message}")
            return 1

    if args.test_asr_handshake:
        try:
            return asyncio.run(test_cloud_asr_handshake(config, logger))
        except Exception as exc:
            error = classify_connection_error(exc)
            logger.write(f"ASR 握手测试失败：code={error.code.value}，message={error.safe_message}")
            return 1

    if not validate_cloud_asr_config(config, logger):
        return 2
    if bool(config.get("privacy_require_confirmation", True)) and not confirm_capture_consent(
        config,
        paths,
        preconfirmed=args.accept_privacy,
    ):
        logger.write("用户未确认采集，未启动音频设备")
        return 6

    session_store: JsonSessionStore | None = None
    if bool(config.get("session_enabled", True)):
        retention_days = int(config.get("session_retention_days", 30))
        removed = JsonSessionStore.purge_old(paths.output_dir, retention_days)
        if removed:
            logger.write(f"会话保留策略清理完成：count={len(removed)}")
        session_store = JsonSessionStore(
            paths.output_dir,
            audio_mode=str(config.get("audio_mode") or "system"),
            devices=[
                str(config.get("system_audio_device") or "auto"),
                str(config.get("microphone_audio_device") or "auto"),
            ],
            asr_provider=str(config.get("cloud_asr_provider") or "volcengine_streaming"),
            llm_provider=str(config.get("ai_provider_name") or "disabled"),
            consent_confirmed=True,
        )

    exit_code = 1
    try:
        logger.write("============================================================")
        logger.write("云端实时转写 + AI 答案启动")
        logger.write(f"音频模式：{config.get('audio_mode')}")
        logger.write(f"输出文件：{paths.output_file}")
        logger.write(f"AI答案文件：{paths.ai_answer_file}")
        if bool(config.get("ai_enabled")) and is_ai_ready(config):
            paths.ai_answer_file.parent.mkdir(parents=True, exist_ok=True)
            paths.ai_answer_file.touch(exist_ok=True)
        logger.write("使用 TUI 快捷键或 Ctrl+C 可停止。")
        logger.write("============================================================")
        use_tui = args.tui if args.tui is not None else sys.stdout.isatty()
        status_tui = StatusTui(enabled=use_tui)
        status_tui.set_privacy_confirmed(True)
        status_tui.set_mode(MODE_LABELS.get(str(config.get("audio_mode")), str(config.get("audio_mode"))))
        status_tui.set_auto_answer(bool(config.get("ai_auto_answer_enabled", True)))
        status_tui.start()
        try:
            exit_code = asyncio.run(run_cloud_asr(config, logger, paths, status_tui, session_store))
        finally:
            status_tui.stop()
    except KeyboardInterrupt:
        logger.write("用户停止。")
        exit_code = 0
    except Exception as exc:
        logger.write(f"云端实时 ASR 异常退出：type={type(exc).__name__}")
        exit_code = 1
    finally:
        if session_store is not None:
            session_store.close("stopped" if exit_code == 0 else "failed")
            if bool(config.get("session_export_on_stop", True)):
                markdown_path = session_store.export("md")
                session_store.export("txt")
                logger.write(f"会话已保存并导出：{markdown_path}")
        logger.write("云端实时转写工具已退出。")
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        message = exc.safe_message if isinstance(exc, AppError) else f"{type(exc).__name__}"
        print(f"ERROR: {message}", file=sys.stderr)
        raise SystemExit(1) from None
