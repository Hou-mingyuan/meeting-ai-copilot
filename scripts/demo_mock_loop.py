#!/usr/bin/env python3
"""HTTP Mock demo driven by the committed deterministic WAV fixture."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from audio_pipeline import FixtureAudioSource  # noqa: E402
from offline_demo import load_fixture_metadata  # noqa: E402
from question_detection import QuestionDetector  # noqa: E402


def post_json(url: str, payload: dict, timeout: float = 15.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def stream_ai_attempt(url: str, question: str, request_id: str, disconnect_once: bool) -> tuple[str, bool]:
    data = json.dumps(
        {"input": question, "request_id": request_id, "disconnect_once": disconnect_once},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    answer_parts: list[str] = []
    completed = False
    with urllib.request.urlopen(request, timeout=15.0) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if event.get("type") == "response.completed":
                completed = True
            delta = event.get("delta")
            if event.get("type") == "response.output_text.delta" and isinstance(delta, str):
                answer_parts.append(delta)
    return "".join(answer_parts), completed


def stream_ai_with_recovery(url: str, question: str) -> tuple[str, int]:
    request_id = uuid.uuid4().hex
    emitted = ""
    reconnects = 0
    print("  AI streaming: ", end="", flush=True)
    for attempt in range(1, 4):
        answer, completed = stream_ai_attempt(url, question, request_id, disconnect_once=True)
        if not answer.startswith(emitted):
            raise RuntimeError("Mock AI retry content changed")
        suffix = answer[len(emitted) :]
        print(suffix, end="", flush=True)
        emitted = answer
        if completed:
            print()
            return emitted, reconnects
        reconnects += 1
        time.sleep(0.05 * attempt)
    raise RuntimeError("Mock AI did not complete after retries")


def load_chunks(fixture: Path):
    source = FixtureAudioSource(fixture)
    source.start()
    chunks = []
    while not source.finished:
        chunk = source.read()
        if chunk is not None:
            chunks.append(chunk)
    source.stop()
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="meeting-ai-copilot zero-key HTTP Mock demo")
    parser.add_argument("--base-url", default="http://127.0.0.1:19060")
    parser.add_argument("--fixture", type=Path, default=PROJECT_ROOT / "tests" / "fixtures" / "meeting_question.wav")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    fixture = args.fixture.resolve()
    metadata = load_fixture_metadata(fixture)
    chunks = load_chunks(fixture)
    session_id = uuid.uuid4().hex

    print("=== meeting-ai-copilot · 固定音频 HTTP Mock 演示 ===\n")
    print(f"Fixture: {fixture.name} · SHA256 {metadata['wav_sha256'][:16]}…")
    print("[1/3] WAV fixture → Mock ASR partial/final（含一次断线恢复）\n")

    final_text = ""
    reconnects = 0
    try:
        for index, chunk in enumerate(chunks):
            payload = {
                "session_id": session_id,
                "seq": chunk.sequence,
                "audio_b64": base64.b64encode(chunk.pcm).decode("ascii"),
                "eof": index == len(chunks) - 1,
                "disconnect_once": True,
            }
            for attempt in range(1, 4):
                try:
                    body = post_json(f"{base}/mock/asr/stream", payload)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 503 or attempt == 3:
                        raise
                    reconnects += 1
                    time.sleep(0.05 * attempt)
            text = str(body.get("text", ""))
            kind = str(body.get("type", "partial"))
            if kind == "final":
                final_text = text
            if chunk.sequence in {1, 5, 12, 20, 30}:
                print(f"  ASR {kind:7} seq={chunk.sequence:02d}: {text}")
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"\nMock 服务不可达或协议失败 ({base})：{exc}", file=sys.stderr)
        return 1

    if reconnects != 1 or final_text != metadata["expected_final"]:
        print("Mock ASR 结果或断线恢复次数不符合 fixture 预期", file=sys.stderr)
        return 2
    detector = QuestionDetector({"ai_min_question_chars": 4, "ai_cooldown_seconds": 0})
    if not detector.evaluate(final_text).accepted:
        print("问题检测未触发", file=sys.stderr)
        return 3

    print("\n[2/3] 问题检测命中 → Mock AI SSE（含一次断线恢复）\n")
    try:
        answer, ai_reconnects = stream_ai_with_recovery(f"{base}/mock/ai/responses", final_text)
    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"\nMock AI 失败：{exc}", file=sys.stderr)
        return 4
    if ai_reconnects != 1 or not answer.startswith("【Mock 参考】"):
        print("Mock AI 重连结果不符合预期", file=sys.stderr)
        return 5

    print(
        f"\n[3/3] 完成 · 音频块 {len(chunks)} · ASR重连 {reconnects} · "
        f"AI重连 {ai_reconnects} · 转写 {len(final_text)} 字 · 答案 {len(answer)} 字"
    )
    print("\nMOCK DEMO OK（固定音频；未调用真实 ASR/LLM API）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
