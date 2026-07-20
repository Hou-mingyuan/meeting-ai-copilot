from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from app_contracts import AnswerRequest, AppError, AppErrorCode, CancellationToken
from llm_providers import create_llm_provider, stream_with_recovery
from question_detection import QuestionDetector

DEFAULT_CONFIG: dict[str, Any] = {
    "output_dir_name": "实时监听",
    "output_file_name": "实时监听.txt",
    "log_file_name": "运行日志.txt",
    "capture_system_audio": True,
    "capture_microphone": False,
    "audio_mode": "system",
    "system_audio_device": None,
    "microphone_audio_device": None,
    "system_audio_gain": 1.0,
    "microphone_audio_gain": 1.0,
    "audio_silence_threshold": 0.0005,
    "audio_queue_seconds": 3,
    "cloud_asr_enabled": True,
    "cloud_asr_provider": "volcengine_streaming",
    "cloud_asr_endpoint": "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
    "cloud_asr_api_key": "",
    "cloud_asr_resource_id": "volc.seedasr.sauc.duration",
    "cloud_asr_chunk_ms": 100,
    "cloud_asr_sample_rate": 16000,
    "cloud_asr_enable_partial": True,
    "cloud_asr_reconnect_max_attempts": 6,
    "cloud_asr_reconnect_base_seconds": 0.5,
    "cloud_asr_reconnect_max_seconds": 8.0,
    "cloud_asr_reconnect_stable_seconds": 30.0,
    "cloud_asr_replay_seconds": 8,
    "cloud_asr_ping_interval_seconds": 20,
    "cloud_asr_ping_timeout_seconds": 10,
    "cloud_asr_hotwords": [
        "Java",
        "Spring Boot",
        "MySQL",
        "Oracle",
        "React",
        "Node.js",
        "TypeScript",
        "Redis",
        "Docker",
        "Kubernetes",
    ],
    "audio_device": None,
    "ai_enabled": True,
    "ai_provider_name": "Volcengine Coding Plan",
    "ai_wire_api": "responses",
    "ai_base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
    "ai_model": "glm-5.2",
    "ai_api_key": "",
    "ai_api_key_env": "VOLCENGINE_CODING_PLAN_API_KEY",
    "ai_answer_file_name": "AI参考答案.txt",
    "ai_timeout_seconds": 90,
    "ai_stream_idle_timeout_seconds": 10,
    "ai_min_question_chars": 12,
    "ai_cooldown_seconds": 8,
    "ai_duplicate_window_seconds": 60,
    "ai_question_threshold": 0.55,
    "ai_auto_answer_enabled": True,
    "ai_context_max_chars": 4000,
    "ai_max_answer_chars": 8000,
    "ai_retry_max_attempts": 3,
    "ai_retry_base_seconds": 0.25,
    "ai_source_labels": ["云端实时ASR"],
    "ai_send_all_transcript": False,
    "ai_system_prompt": (
        "你是全栈开发工程师面试参考答案助手。你会收到会议实时转写出来的面试官问题，"
        "可能是中文、英文或中英混合。请先判断问题含义，再给出适合被面试者口述的参考回答。"
        "回答要专业、简洁、自然，优先覆盖 Java、Spring Boot、MySQL、Oracle、React、Node.js、"
        "系统设计、并发、缓存、事务、索引、SQL 优化等全栈开发主题。"
        "如果转写明显不完整或不像问题，请用一句话说明需要等待更完整的问题。"
        "不要编造具体个人经历，可以给可替换的话术。"
    ),
    "privacy_require_confirmation": True,
    "session_enabled": True,
    "session_retention_days": 30,
    "session_export_on_stop": True,
}


@dataclass
class Paths:
    desktop: Path
    output_dir: Path
    output_file: Path
    log_file: Path
    ai_answer_file: Path


class Logger:
    def __init__(self, log_file: Path) -> None:
        self.log_file = log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, message: str) -> None:
        safe_message = self._redact(message)
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {safe_message}"
        with self._lock:
            print(line, flush=True)
            with self.log_file.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")

    @staticmethod
    def _redact(message: str) -> str:
        text = str(message)
        patterns = (
            (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1<redacted>"),
            (r"(?i)((?:api[_-]?key|access[_-]?key|secret)\s*[:=]\s*)[^\s,;]+", r"\1<redacted>"),
            (r"\b[A-Za-z0-9_-]{32,}\b", "<redacted-token>"),
        )
        for pattern, replacement in patterns:
            text = re.sub(pattern, replacement, text)
        return text


def load_config(config_path: Path) -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    user_config: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            user_config = json.load(f)
        if not isinstance(user_config, dict):
            raise ValueError("配置文件根节点必须是 JSON object")
        config.update(user_config)
    if "audio_mode" not in user_config:
        system_enabled = bool(config.get("capture_system_audio", True))
        microphone_enabled = bool(config.get("capture_microphone", False))
        if system_enabled and microphone_enabled:
            config["audio_mode"] = "mixed"
        elif microphone_enabled:
            config["audio_mode"] = "microphone"
        else:
            config["audio_mode"] = "system"
    if str(config.get("audio_mode") or "") not in {"system", "microphone", "mixed", "fixture"}:
        raise ValueError("audio_mode 必须是 system、microphone、mixed 或 fixture")
    return config


def get_desktop() -> Path:
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        desktop = Path(userprofile) / "Desktop"
        if desktop.exists():
            return desktop
        onedrive = os.environ.get("OneDrive")
        if onedrive and (Path(onedrive) / "Desktop").exists():
            return Path(onedrive) / "Desktop"
    return Path.home() / "Desktop"


def dated_file_name(file_name: str, date_text: str) -> str:
    path = Path(file_name)
    stem = path.stem
    suffix = path.suffix or ".txt"
    if stem.startswith(date_text):
        return file_name
    return f"{date_text}_{stem}{suffix}"


def build_paths(config: dict[str, Any]) -> Paths:
    desktop = get_desktop()
    configured_output = str(config.get("output_directory") or "").strip()
    output_dir = Path(configured_output).expanduser() if configured_output else desktop / str(config["output_dir_name"])
    output_dir = output_dir.resolve()
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)
    return Paths(
        desktop=desktop,
        output_dir=output_dir,
        output_file=output_dir / dated_file_name(str(config["output_file_name"]), today),
        log_file=output_dir / dated_file_name(str(config["log_file_name"]), today),
        ai_answer_file=output_dir / dated_file_name(str(config["ai_answer_file_name"]), today),
    )


def refresh_paths_for_today(paths: Paths, config: dict[str, Any]) -> Paths:
    today = datetime.now().strftime("%Y-%m-%d")
    return Paths(
        desktop=paths.desktop,
        output_dir=paths.output_dir,
        output_file=paths.output_dir / dated_file_name(str(config["output_file_name"]), today),
        log_file=paths.output_dir / dated_file_name(str(config["log_file_name"]), today),
        ai_answer_file=paths.output_dir / dated_file_name(str(config["ai_answer_file_name"]), today),
    )


def append_transcript(output_file: Path, text: str, source_label: str | None = None) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{source_label}] " if source_label else ""
    with output_file.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"{timestamp}  {prefix}{text.strip()}\n")


def append_transcript_today(paths: Paths, config: dict[str, Any], text: str, source_label: str | None = None) -> Path:
    current_paths = refresh_paths_for_today(paths, config)
    append_transcript(current_paths.output_file, text, source_label)
    return current_paths.output_file


def write_partial_transcript_today(paths: Paths, config: dict[str, Any], text: str, source_label: str | None = None) -> Path:
    current_paths = refresh_paths_for_today(paths, config)
    partial_file = current_paths.output_dir / dated_file_name("临时识别.txt", datetime.now().strftime("%Y-%m-%d"))
    partial_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{source_label}] " if source_label else ""
    with partial_file.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"{timestamp}  {prefix}{text.strip()}\n")
        f.flush()
    return partial_file


def start_ai_answer_stream(answer_file: Path, question: str, source_label: str) -> None:
    answer_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with answer_file.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"[{timestamp}] 来源：{source_label}\n")
        f.write("问题：\n")
        f.write(question.strip() + "\n\n")
        f.write("参考答案（流式）：\n")
        f.flush()


def append_ai_answer_delta(answer_file: Path, delta: str) -> None:
    if not delta:
        return
    answer_file.parent.mkdir(parents=True, exist_ok=True)
    with answer_file.open("a", encoding="utf-8", newline="\n") as f:
        f.write(delta)
        f.flush()


def finish_ai_answer_stream(answer_file: Path) -> None:
    answer_file.parent.mkdir(parents=True, exist_ok=True)
    with answer_file.open("a", encoding="utf-8", newline="\n") as f:
        f.write("\n\n" + "-" * 60 + "\n\n")
        f.flush()


def start_ai_answer_stream_today(paths: Paths, config: dict[str, Any], question: str, source_label: str) -> Path:
    current_paths = refresh_paths_for_today(paths, config)
    start_ai_answer_stream(current_paths.ai_answer_file, question, source_label)
    return current_paths.ai_answer_file


def list_devices() -> None:
    import soundcard as sc

    print("=== 扬声器 / 系统输出设备 ===")
    for idx, speaker in enumerate(sc.all_speakers()):
        print(f"[speaker {idx}] {speaker.name}")
    print()
    print("=== 麦克风 / 输入设备（含 loopback）===")
    for idx, mic in enumerate(sc.all_microphones(include_loopback=True)):
        loopback = " loopback" if getattr(mic, "isloopback", False) else ""
        print(f"[mic {idx}] {mic.name}{loopback}")
    print()
    print("默认扬声器：")
    print(sc.default_speaker().name)


def diagnose_environment(config_path: Path, config: dict[str, Any], paths: Paths) -> None:
    print("=== 基础信息 ===")
    print(f"配置文件: {config_path}")
    print(f"输出目录: {paths.output_dir}")
    print(f"实时转写文件: {paths.output_file}")
    print(f"AI答案文件: {paths.ai_answer_file}")
    print()
    print("=== 云端实时 ASR ===")
    print(f"endpoint: {config.get('cloud_asr_endpoint')}")
    print(f"resource_id: {config.get('cloud_asr_resource_id')}")
    print(f"api_key: {'已配置' if get_cloud_asr_configured(config) else '未配置'}")
    print(f"chunk_ms: {config.get('cloud_asr_chunk_ms')}")
    print(f"sample_rate: {config.get('cloud_asr_sample_rate')}")
    print()
    print("=== AI ===")
    print(f"ai_enabled: {config.get('ai_enabled')}")
    print(f"ai_provider_name: {config.get('ai_provider_name')}")
    print(f"ai_base_url: {config.get('ai_base_url')}")
    print(f"ai_model: {config.get('ai_model')}")
    print(f"ai_api_key: {'已配置' if get_ai_api_key(config) else ('Mock' if is_mock_ai(config) else '未配置')}")
    print()
    print("=== Python 包 ===")
    for name in ["numpy", "soundcard", "websockets", "volcengine_audio"]:
        try:
            module = __import__(name)
            version = getattr(module, "__version__", "unknown")
            print(f"{name}: OK, version={version}")
        except Exception as exc:
            print(f"{name}: FAIL, {exc!r}")
    print()
    print("=== 音频设备 ===")
    try:
        list_devices()
    except Exception as exc:
        print(f"列出音频设备失败: {exc!r}")


def get_cloud_asr_configured(config: dict[str, Any]) -> bool:
    return bool(str(config.get("cloud_asr_api_key") or "").strip() or os.environ.get("VOLC_ASR_API_KEY"))


def get_ai_api_key(config: dict[str, Any]) -> str:
    env_name = str(config.get("ai_api_key_env") or "").strip()
    if env_name:
        environment_value = os.environ.get(env_name, "").strip()
        if environment_value:
            return environment_value
    return str(config.get("ai_api_key") or "").strip()


def is_mock_ai(config: dict[str, Any]) -> bool:
    provider = str(config.get("ai_provider") or "").strip().lower()
    if provider == "mock":
        return True
    return str(config.get("ai_model") or "").strip().lower() == "mock"


def is_ai_ready(config: dict[str, Any]) -> bool:
    return is_mock_ai(config) or bool(get_ai_api_key(config))


def stream_builtin_mock_ai_answer(question: str):
    """Offline mock tokens — no HTTP, for --test-ai with ai_provider=mock."""
    preview = question.strip()[:40] or "mock question"
    tokens = [
        "【Mock】",
        preview,
        " 的参考要点：",
        "Redis",
        " 缓存热点读；",
        "MySQL",
        " 索引优化查询路径；",
        "二者互补。",
    ]
    for token in tokens:
        time.sleep(0.02)
        yield token


def stream_mock_server_ai_answer(question: str, config: dict[str, Any]):
    base_url = normalize_base_url(str(config.get("mock_base_url") or config.get("ai_base_url") or ""))
    if not base_url:
        raise RuntimeError("mock_base_url 未配置")
    timeout = float(config.get("ai_timeout_seconds", 90))
    payload = {"input": question.strip()}
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    for event in iter_sse_json(f"{base_url}/mock/ai/responses", payload, headers, timeout):
        delta = extract_ai_delta(event)
        if delta:
            yield delta


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def iter_sse_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib_request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if not data_text or data_text == "[DONE]":
                    continue
                try:
                    yield json.loads(data_text)
                except json.JSONDecodeError:
                    continue
    except urllib_error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {error_body[:1000]}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"网络请求失败：{exc!r}") from exc


def extract_ai_delta(data: dict[str, Any]) -> str:
    event_type = str(data.get("type") or "")
    if event_type and event_type not in {
        "response.output_text.delta",
        "response.refusal.delta",
        "chat.completion.chunk",
    }:
        if event_type.startswith("response."):
            return ""

    delta = data.get("delta")
    if isinstance(delta, str) and event_type == "response.output_text.delta":
        return delta

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            choice_delta = first.get("delta")
            if isinstance(choice_delta, dict):
                content = choice_delta.get("content")
                if isinstance(content, str):
                    return content
    return ""


def stream_ai_answer_api(question: str, config: dict[str, Any]):
    request = AnswerRequest(
        request_id=uuid.uuid4().hex,
        session_id="one-shot",
        question=question,
        context="",
        source="CLI",
        manual=True,
    )
    provider = create_llm_provider(config, get_ai_api_key(config))
    yield from stream_with_recovery(
        provider,
        request,
        CancellationToken(),
        max_attempts=int(config.get("ai_retry_max_attempts", 3)),
        base_delay_seconds=float(config.get("ai_retry_base_seconds", 0.25)),
        timeout_seconds=float(config.get("ai_timeout_seconds", 90)),
        max_answer_chars=int(config.get("ai_max_answer_chars", 8000)),
    )


def is_question_like(text: str, config: dict[str, Any]) -> bool:
    detector = QuestionDetector(config)
    return detector.score(text) >= detector.threshold


def maybe_enqueue_ai_question(
    ai_queue: "queue.Queue[tuple[str, str]] | None",
    config: dict[str, Any],
    logger: Logger,
    state: dict[str, Any],
    source_label: str,
    text: str,
) -> None:
    if ai_queue is None:
        return

    allowed_sources = config.get("ai_source_labels") or ["云端实时ASR"]
    if isinstance(allowed_sources, list) and "*" not in allowed_sources and source_label not in allowed_sources:
        return

    if not is_question_like(text, config):
        return

    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    now = time.time()
    cooldown = float(config.get("ai_cooldown_seconds", 8))
    if normalized == state.get("last_ai_question") and now - float(state.get("last_ai_time", 0)) < 60:
        return
    if now - float(state.get("last_ai_time", 0)) < cooldown:
        return

    try:
        ai_queue.put_nowait((source_label, text.strip()))
        state["last_ai_question"] = normalized
        state["last_ai_time"] = now
        logger.write(f"已提交 AI 参考答案任务：chars={len(text.strip())}")
    except queue.Full:
        logger.write("AI 参考答案队列已满，本次问题已跳过。")


class AiTaskController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: CancellationToken | None = None
        self._last_request: AnswerRequest | None = None

    def begin(self, request: AnswerRequest) -> CancellationToken:
        token = CancellationToken()
        with self._lock:
            self._active = token
            self._last_request = request
        return token

    def finish(self, token: CancellationToken) -> None:
        with self._lock:
            if self._active is token:
                self._active = None

    def cancel_active(self, reason: str = "user") -> bool:
        with self._lock:
            token = self._active
        if token is None:
            return False
        token.cancel(reason)
        return True

    @property
    def last_request(self) -> AnswerRequest | None:
        with self._lock:
            return self._last_request


def ai_answer_worker(
    ai_queue: "queue.Queue[AnswerRequest | tuple[str, str]]",
    stop_event: threading.Event,
    config: dict[str, Any],
    paths: Paths,
    logger: Logger,
    status_tui: Any | None = None,
    session_store: Any | None = None,
    task_controller: AiTaskController | None = None,
) -> None:
    controller = task_controller or AiTaskController()
    provider = create_llm_provider(config, get_ai_api_key(config))
    logger.write(
        "AI参考答案已启用："
        f"provider={provider.name}, "
        f"model={config.get('ai_model')}, "
        f"output={paths.ai_answer_file}"
    )
    initial_paths = refresh_paths_for_today(paths, config)
    initial_paths.ai_answer_file.parent.mkdir(parents=True, exist_ok=True)
    if not initial_paths.ai_answer_file.exists():
        with initial_paths.ai_answer_file.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] AI参考答案已启动，等待识别到面试问题。\n")
            f.write("识别到问题后，答案会在这里流式写入。\n")
            f.write("\n" + "-" * 60 + "\n\n")
            f.flush()

    while not stop_event.is_set():
        try:
            queued = ai_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if isinstance(queued, AnswerRequest):
            request = queued
        else:
            source_label, question = queued
            request = AnswerRequest(
                request_id=uuid.uuid4().hex,
                session_id=str(getattr(session_store, "session_id", "legacy")),
                question=question,
                context="",
                source=source_label,
            )
        token = controller.begin(request)
        try:
            if status_tui is not None:
                status_tui.set_ai("生成中")
                status_tui.set_answer("")
            logger.write("正在调用 AI 流式生成参考答案...")
            answer_file = start_ai_answer_stream_today(paths, config, request.question, request.source)
            if session_store is not None:
                session_store.begin_answer(request)
            delta_count = 0
            for delta in stream_with_recovery(
                provider,
                request,
                token,
                max_attempts=int(config.get("ai_retry_max_attempts", 3)),
                base_delay_seconds=float(config.get("ai_retry_base_seconds", 0.25)),
                timeout_seconds=float(config.get("ai_timeout_seconds", 90)),
                max_answer_chars=int(config.get("ai_max_answer_chars", 8000)),
            ):
                append_ai_answer_delta(answer_file, delta)
                if session_store is not None:
                    session_store.append_answer_delta(request.request_id, delta)
                if status_tui is not None:
                    status_tui.append_answer_delta(delta)
                delta_count += 1
            finish_ai_answer_stream(answer_file)
            if session_store is not None:
                session_store.finish_answer(request.request_id)
            logger.write(f"AI参考答案已流式写入：{answer_file}，chunks={delta_count}")
            if status_tui is not None:
                status_tui.set_ai("待命")
        except AppError as exc:
            status = "cancelled" if exc.code == AppErrorCode.CANCELLED else "failed"
            if session_store is not None:
                session_store.finish_answer(request.request_id, status=status, error=exc.safe_message)
            finish_ai_answer_stream(refresh_paths_for_today(paths, config).ai_answer_file)
            logger.write(f"AI参考答案{status}：code={exc.code.value}，message={exc.safe_message}")
            if status_tui is not None:
                status_tui.set_ai("已取消" if status == "cancelled" else "失败")
                if status == "cancelled":
                    status_tui.set_notice("AI 已取消；转写与采集继续")
                else:
                    status_tui.set_notice(f"AI 失败：{exc.safe_message}；可按 R 重试")
        except Exception as exc:
            if session_store is not None:
                session_store.finish_answer(request.request_id, status="failed", error="内部错误")
            logger.write(f"AI参考答案生成失败：type={type(exc).__name__}")
            if status_tui is not None:
                status_tui.set_ai("失败")
                status_tui.set_notice("AI 内部错误；可按 R 重试")
        finally:
            controller.finish(token)


def select_loopback_microphone(audio_device: str | None, logger: Logger):
    import soundcard as sc

    microphones = sc.all_microphones(include_loopback=True)
    if audio_device:
        audio_device_lower = audio_device.lower()
        for mic in microphones:
            if audio_device_lower in mic.name.lower():
                logger.write(f"使用配置指定的音频设备：{mic.name}")
                return mic
        logger.write(f"未找到配置指定的音频设备：{audio_device}，改用默认扬声器 loopback")

    default_speaker = sc.default_speaker()
    default_name = default_speaker.name.lower()
    for mic in microphones:
        mic_name = mic.name.lower()
        if getattr(mic, "isloopback", False) and (default_name in mic_name or mic_name in default_name):
            logger.write(f"使用默认扬声器 loopback：{mic.name}")
            return mic

    for mic in microphones:
        if getattr(mic, "isloopback", False):
            logger.write(f"使用第一个可用 loopback：{mic.name}")
            return mic

    logger.write("未找到 loopback 设备，尝试使用默认扬声器录制接口")
    return sc.get_microphone(default_speaker.name, include_loopback=True)


def measure_loopback_level(device, sample_rate: int, seconds: float = 0.4) -> float:
    import numpy as np

    frames = max(1600, int(sample_rate * seconds))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with device.recorder(samplerate=sample_rate, channels=1, blocksize=frames) as recorder:
                data = recorder.record(numframes=frames)
        if data.ndim > 1:
            data = data[:, 0]
        data = data.astype(np.float32, copy=False)
        return float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
    except Exception:
        return 0.0


def select_active_loopback_microphone(audio_device: str | None, sample_rate: int, logger: Logger):
    import soundcard as sc

    if audio_device:
        return select_loopback_microphone(audio_device, logger)

    microphones = [m for m in sc.all_microphones(include_loopback=True) if getattr(m, "isloopback", False)]
    if not microphones:
        return select_loopback_microphone(None, logger)

    logger.write("正在自动检测当前有声音的系统输出设备...")
    scored: list[tuple[float, Any]] = []
    for mic in microphones:
        level = measure_loopback_level(mic, sample_rate)
        scored.append((level, mic))
        logger.write(f"音频设备检测：level={level:.6f}，device={mic.name}")

    scored.sort(key=lambda x: x[0], reverse=True)
    best_level, best_device = scored[0]
    logger.write(f"选择系统声音设备：{best_device.name}，level={best_level:.6f}")
    if best_level < 0.0005:
        logger.write("提醒：当前所有系统输出设备音量都很低。请确认腾讯会议声音正在从电脑播放出来。")
    return best_device
