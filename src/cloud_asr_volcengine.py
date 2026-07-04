from __future__ import annotations

import argparse
import asyncio
import json
import queue
import signal
import threading
import time
import uuid
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import websockets
from volcengine_audio.stt import VolcengineAsrFunctionsV3

from cloud_runtime import (
    Logger,
    ai_answer_worker,
    append_ai_answer_delta,
    finish_ai_answer_stream,
    append_transcript_today,
    build_paths,
    diagnose_environment,
    get_ai_api_key,
    is_question_like,
    list_devices,
    load_config,
    maybe_enqueue_ai_question,
    select_active_loopback_microphone,
    start_ai_answer_stream_today,
    stream_ai_answer_api,
    write_partial_transcript_today,
)


def get_cloud_asr_key(config: dict[str, Any], field: str, env_name: str) -> str:
    value = str(config.get(field) or "").strip()
    if value:
        return value
    import os

    return os.environ.get(env_name, "").strip()


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
                            cleaned.append({"text": sentence, "definite": bool(item.get("definite"))})
                if cleaned:
                    return cleaned
    return []


def audio_capture_worker(
    audio_queue: "queue.Queue[bytes]",
    stop_event: threading.Event,
    config: dict[str, Any],
    logger: Logger,
) -> None:
    sample_rate = int(config.get("cloud_asr_sample_rate", 16000))
    chunk_ms = int(config.get("cloud_asr_chunk_ms", 100))
    frames = max(160, int(sample_rate * chunk_ms / 1000))
    gain = float(config.get("system_audio_gain", 1.0))

    silence_limit = max(30, int(5000 / max(chunk_ms, 1)))
    silence_threshold = float(config.get("audio_silence_threshold", 0.0005))

    while not stop_event.is_set():
        device = select_active_loopback_microphone(config.get("audio_device"), sample_rate, logger)
        logger.write(f"云端 ASR 开始监听系统声音：{device.name}，chunk={chunk_ms}ms")
        silent_chunks = 0
        captured_audio = False

        try:
            recorder_context = device.recorder(samplerate=sample_rate, channels=1, blocksize=frames)
            with recorder_context as recorder:
                while not stop_event.is_set():
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        data = recorder.record(numframes=frames)
                    if data.ndim > 1:
                        data = data[:, 0]
                    data = data.astype(np.float32, copy=False)
                    if gain != 1.0:
                        data = np.clip(data * gain, -1.0, 1.0)

                    level = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
                    if level >= silence_threshold:
                        captured_audio = True
                        silent_chunks = 0
                    elif not config.get("audio_device"):
                        silent_chunks += 1

                    # 只有“还没在这个设备上听到过真实声音”时，长时间静音才重新找设备；
                    # 一旦听到过声音就固定下来——开会正常停顿不再切换，避免把声音切到
                    # 静音的虚拟声卡上、白白把后面要说的话丢掉。
                    if not captured_audio and silent_chunks >= silence_limit:
                        logger.write("尚未捕获到有效声音，重新检测系统输出设备...")
                        break

                    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                    try:
                        audio_queue.put_nowait(pcm)
                    except queue.Full:
                        try:
                            _ = audio_queue.get_nowait()
                            audio_queue.put_nowait(pcm)
                            logger.write("音频队列已满，丢弃最旧音频以保持低延迟。")
                        except queue.Empty:
                            pass
        except Exception as exc:
            logger.write(f"云端 ASR 音频采集失败：{exc!r}")
            time_sleep = min(2.0, max(0.2, chunk_ms / 1000))
            awaitable_stop = stop_event.wait(time_sleep)
            if awaitable_stop:
                break


async def receive_responses(
    websocket,
    stop_event: threading.Event,
    paths,
    config: dict[str, Any],
    logger: Logger,
    ai_queue: "queue.Queue[tuple[str, str]] | None",
) -> None:
    last_partial = ""
    recent_finals: list[str] = []
    asked_questions: list[str] = []
    ai_state: dict[str, Any] = {}
    pending_question = ""
    pending_since = 0.0
    stable_seconds = max(0.2, float(config.get("ai_partial_stable_seconds", 0.8)))

    def normalize(value: str) -> str:
        return " ".join(value.lower().split())

    def already_asked(norm: str) -> bool:
        # 已经问过完全相同、或互为前缀的（partial 与它对应的 final 视为同一个问题），跳过。
        for asked in asked_questions:
            if norm == asked or norm in asked or asked in norm:
                return True
        return False

    def ask_ai_now(text: str) -> None:
        # 只有“像问题”的内容才送 AI；正常闲聊不会触发。问完即问、不等 final。
        if ai_queue is None:
            return
        norm = normalize(text)
        if not norm or already_asked(norm):
            return
        if not is_question_like(text, config):
            return
        asked_questions.append(norm)
        if len(asked_questions) > 40:
            asked_questions.pop(0)
        maybe_enqueue_ai_question(ai_queue, config, logger, ai_state, "云端实时ASR", text)

    while not stop_event.is_set():
        try:
            # 加超时：说话人停顿、暂时没有新片段时也能醒过来，判断问题是否已经说完。
            raw = await asyncio.wait_for(websocket.recv(), timeout=0.4)
        except asyncio.TimeoutError:
            # 手头有一个“像问题”的片段且已停顿超过阈值（说明这句问完了），
            # 立刻拿去问 AI，不再干等服务端把整句标记为 final（那通常要等到下一句才来）。
            if pending_question and (time.monotonic() - pending_since) >= stable_seconds:
                ask_ai_now(pending_question)
                pending_question = ""
            continue

        parsed = VolcengineAsrFunctionsV3.parse_response(raw)
        message = parsed.get("message")
        is_last = bool(parsed.get("is_last_package"))
        utterances = extract_utterances(message)

        if not utterances:
            # 没有分句信息时退而求其次：只在结束包落一次整段，避免反复写累计全文。
            if is_last:
                text, _ = extract_text_from_response(message)
                normalized = normalize(text)
                if text and normalized not in recent_finals:
                    recent_finals.append(normalized)
                    if len(recent_finals) > 50:
                        recent_finals.pop(0)
                    logger.write(f"云端识别结果[final]：{text}")
                    append_transcript_today(paths, config, text, "云端实时ASR")
                    ask_ai_now(text)
            continue

        # 已说完(definite)的句子：逐句写转写、逐句判断是否问题，每句只处理一次，
        # 不再把“从头到现在的累计全文”当成一句反复写——这才是“拼在后面”的根因。
        current_partial = ""
        for utterance in utterances:
            sentence = utterance["text"]
            if utterance["definite"]:
                normalized = normalize(sentence)
                if normalized and normalized not in recent_finals:
                    recent_finals.append(normalized)
                    if len(recent_finals) > 50:
                        recent_finals.pop(0)
                    logger.write(f"云端识别结果[final]：{sentence}")
                    append_transcript_today(paths, config, sentence, "云端实时ASR")
                    ask_ai_now(sentence)
            else:
                current_partial = sentence  # 最后一个未说完的，就是“正在说的这句”

        if current_partial:
            if current_partial != last_partial:
                last_partial = current_partial
                logger.write(f"云端识别结果[partial]：{current_partial}")
                write_partial_transcript_today(paths, config, current_partial, "云端实时ASR-临时")
            # 只有“像问题”的在说片段才需要尽快回答；闲聊直接忽略。
            if is_question_like(current_partial, config):
                if current_partial.rstrip().endswith(("?", "？")):
                    # 已经带问号，基本说完了，立刻问，不必再等停顿。
                    ask_ai_now(current_partial)
                    pending_question = ""
                elif current_partial != pending_question:
                    pending_question = current_partial
                    pending_since = time.monotonic()
            else:
                pending_question = ""
        else:
            pending_question = ""


async def send_audio(websocket, audio_queue: "queue.Queue[bytes]", stop_event: threading.Event, logger: Logger) -> None:
    sequence = 2
    while not stop_event.is_set():
        try:
            audio = await asyncio.to_thread(audio_queue.get, True, 0.2)
        except queue.Empty:
            continue
        packet = VolcengineAsrFunctionsV3.generate_asr_audio_only_request(sequence, audio, compress=True)
        await websocket.send(bytes(packet))
        sequence += 1

    packet = VolcengineAsrFunctionsV3.generate_asr_audio_only_request(sequence, b"", compress=False)
    try:
        await websocket.send(bytes(packet))
    except Exception as exc:
        logger.write(f"发送结束包失败：{exc!r}")


async def run_cloud_asr(config: dict[str, Any], logger: Logger, paths) -> int:
    if not validate_cloud_asr_config(config, logger):
        return 2

    stop_event = threading.Event()
    chunk_ms = int(config.get("cloud_asr_chunk_ms", 100))
    queue_seconds = float(config.get("audio_queue_seconds", 3))
    audio_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=max(10, int(queue_seconds * 1000 / max(chunk_ms, 1))))

    ai_queue: "queue.Queue[tuple[str, str]] | None" = None
    ai_thread: threading.Thread | None = None
    if bool(config.get("ai_enabled")) and get_ai_api_key(config):
        ai_queue = queue.Queue(maxsize=10)
        ai_thread = threading.Thread(
            target=ai_answer_worker,
            args=(ai_queue, stop_event, config, paths, logger),
            daemon=True,
        )
        ai_thread.start()

    capture_thread = threading.Thread(
        target=audio_capture_worker,
        args=(audio_queue, stop_event, config, logger),
        daemon=True,
    )
    capture_thread.start()

    def handle_signal(signum, frame):
        logger.write("收到停止信号，准备退出云端实时 ASR...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    endpoint = str(config.get("cloud_asr_endpoint") or "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel")
    headers = build_volcengine_headers(config)
    logger.write(f"连接火山实时 ASR：{endpoint}")
    logger.write("云端实时模式：纯云端 ASR，目标是边说边出字。")

    reconnect_delay = 1.0
    while not stop_event.is_set():
        try:
            async with websockets.connect(endpoint, additional_headers=headers, max_size=16 * 1024 * 1024) as websocket:
                start_request = build_start_request(config)
                first_packet = VolcengineAsrFunctionsV3.generate_asr_full_client_request(
                    1,
                    start_request,
                    compression=True,
                )
                await websocket.send(bytes(first_packet))
                logger.write(f"火山实时 ASR 已连接，开始发送 {chunk_ms}ms 音频块。")
                reconnect_delay = 1.0

                receiver = asyncio.create_task(receive_responses(websocket, stop_event, paths, config, logger, ai_queue))
                sender = asyncio.create_task(send_audio(websocket, audio_queue, stop_event, logger))
                done, pending = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_EXCEPTION)
                for task in pending:
                    task.cancel()
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
        except Exception as exc:
            if stop_event.is_set():
                break
            logger.write(f"火山实时 ASR 连接断开，将自动重连：{exc!r}")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(10.0, reconnect_delay * 2)

    stop_event.set()
    capture_thread.join(timeout=3)
    if ai_thread is not None:
        ai_thread.join(timeout=5)
    return 0


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
        message = parsed.get("message")
        if isinstance(message, dict) and message.get("error"):
            raise RuntimeError(f"ASR 服务返回错误：{message}")
        logger.write(f"火山实时 ASR 握手成功：sequence={parsed.get('sequence')}, size={parsed.get('size')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="火山云端实时 ASR + AI 答案")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--test-ai", action="store_true", help="只测试 AI 接口")
    parser.add_argument("--test-asr-handshake", action="store_true", help="只测试火山实时 ASR WebSocket 握手")
    parser.add_argument("--diagnose", action="store_true", help="诊断云端实时 ASR、AI、依赖和音频设备")
    parser.add_argument("--list-devices", action="store_true", help="列出可用音频设备")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    paths = build_paths(config)
    logger = Logger(paths.log_file)

    if args.list_devices:
        list_devices()
        return 0

    if args.diagnose:
        diagnose_environment(config_path, config, paths)
        return 0

    if args.test_ai:
        if not get_ai_api_key(config):
            logger.write("AI API Key 未配置。")
            return 1
        question = "Can you explain the difference between MySQL index and Oracle index?"
        answer_file = start_ai_answer_stream_today(paths, config, question, "云端模式AI流式接口测试")
        delta_count = 0
        char_count = 0
        for delta in stream_ai_answer_api(question, config):
            append_ai_answer_delta(answer_file, delta)
            delta_count += 1
            char_count += len(delta)
        finish_ai_answer_stream(answer_file)
        logger.write(f"AI流式接口测试成功：chunks={delta_count}，chars={char_count}，结果已写入：{answer_file}")
        return 0

    if args.test_asr_handshake:
        return asyncio.run(test_cloud_asr_handshake(config, logger))

    try:
        logger.write("============================================================")
        logger.write("云端实时转写 + AI 答案启动")
        logger.write(f"输出文件：{paths.output_file}")
        logger.write(f"AI答案文件：{paths.ai_answer_file}")
        if bool(config.get("ai_enabled")) and get_ai_api_key(config):
            paths.ai_answer_file.parent.mkdir(parents=True, exist_ok=True)
            paths.ai_answer_file.touch(exist_ok=True)
        logger.write("按 Ctrl+C 可停止。")
        logger.write("============================================================")
        return asyncio.run(run_cloud_asr(config, logger, paths))
    except KeyboardInterrupt:
        logger.write("用户停止。")
        return 0
    except Exception as exc:
        logger.write(f"云端实时 ASR 异常退出：{exc!r}")
        return 1
    finally:
        logger.write("云端实时转写工具已退出。")


if __name__ == "__main__":
    raise SystemExit(main())
