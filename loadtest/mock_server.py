#!/usr/bin/env python3
"""Deterministic local Mock ASR/AI server for fixture and performance tests."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PORT_MIN = 19060
PORT_MAX = 19069
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "meeting_question.wav"
METADATA_PATH = FIXTURE_PATH.with_suffix(".json")
METADATA = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
EXPECTED_WAV_SHA256 = str(METADATA["wav_sha256"])
EXPECTED_PCM_SHA256 = str(METADATA["pcm_sha256"])
EXPECTED_FINAL = str(METADATA["expected_final"])
MAX_REQUEST_BYTES = 512 * 1024


def sse_event(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class MockState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.ai_failures: set[str] = set()

    def add_audio_chunk(self, session_id: str, sequence: int, audio: bytes) -> tuple[bool, str]:
        with self.lock:
            session = self.sessions.setdefault(
                session_id,
                {"chunks": {}, "disconnect_emitted": False, "updated_at": time.monotonic()},
            )
            existing = session["chunks"].get(sequence)
            if existing is not None and existing != audio:
                return False, "sequence_conflict"
            session["chunks"][sequence] = audio
            session["updated_at"] = time.monotonic()
            return True, "duplicate" if existing is not None else "stored"

    def should_disconnect(self, session_id: str, sequence: int, requested: bool) -> bool:
        if not requested or sequence != 12:
            return False
        with self.lock:
            session = self.sessions.setdefault(
                session_id,
                {"chunks": {}, "disconnect_emitted": False, "updated_at": time.monotonic()},
            )
            if session["disconnect_emitted"]:
                return False
            session["disconnect_emitted"] = True
            return True

    def finish_audio(self, session_id: str) -> tuple[str, int]:
        with self.lock:
            session = self.sessions.pop(session_id, None)
        if not session:
            return "", 0
        chunks = session["chunks"]
        pcm = b"".join(chunks[index] for index in sorted(chunks))
        return hashlib.sha256(pcm).hexdigest(), len(chunks)

    def should_fail_ai(self, request_id: str, requested: bool) -> bool:
        if not requested:
            return False
        with self.lock:
            if request_id in self.ai_failures:
                return False
            self.ai_failures.add(request_id)
            return True


STATE = MockState()


class MockHandler(BaseHTTPRequestHandler):
    server_version = "MeetingMock/2.0"

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": "meeting-ai-copilot-mock",
                    "mode": "deterministic-fixture",
                    "fixture_sha256": EXPECTED_WAV_SHA256,
                },
            )
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/mock/asr/fixture":
            self._handle_asr_fixture()
            return
        if path == "/mock/asr/stream":
            self._handle_asr_stream()
            return
        if path == "/mock/ai/responses":
            self._handle_ai_sse()
            return
        self._send_json(404, {"error": "not_found"})

    @staticmethod
    def _decode_base64(value: Any) -> bytes | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error):
            return None

    def _handle_asr_fixture(self) -> None:
        started = time.perf_counter()
        body = self._read_json()
        wav = self._decode_base64(body.get("wav_b64"))
        if wav is None:
            self._send_json(400, {"error": "wav_b64_required"})
            return
        digest = hashlib.sha256(wav).hexdigest()
        if digest != EXPECTED_WAV_SHA256:
            self._send_json(422, {"error": "fixture_hash_mismatch", "actual_sha256": digest})
            return
        time.sleep(0.06)
        self._send_json(
            200,
            {
                "type": "final",
                "text": EXPECTED_FINAL,
                "fixture_sha256": digest,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

    def _handle_asr_stream(self) -> None:
        started = time.perf_counter()
        body = self._read_json()
        session_id = str(body.get("session_id") or "").strip()
        try:
            sequence = int(body.get("seq", 0))
        except (TypeError, ValueError):
            sequence = 0
        audio = self._decode_base64(body.get("audio_b64"))
        if not session_id or sequence <= 0 or audio is None:
            self._send_json(400, {"error": "session_id_seq_audio_required"})
            return
        if STATE.should_disconnect(session_id, sequence, bool(body.get("disconnect_once"))):
            self._send_json(503, {"error": "mock_disconnect", "retryable": True, "ack_seq": sequence - 1})
            return
        accepted, state = STATE.add_audio_chunk(session_id, sequence, audio)
        if not accepted:
            self._send_json(409, {"error": state, "ack_seq": sequence - 1})
            return
        time.sleep(0.01)
        if bool(body.get("eof")):
            digest, chunk_count = STATE.finish_audio(session_id)
            if digest != EXPECTED_PCM_SHA256:
                self._send_json(
                    422,
                    {
                        "error": "pcm_hash_mismatch",
                        "actual_sha256": digest,
                        "chunks": chunk_count,
                    },
                )
                return
            response_type = "final"
            text = EXPECTED_FINAL
        else:
            response_type = "partial"
            text = EXPECTED_FINAL[: min(len(EXPECTED_FINAL), 4 + sequence * 2)]
        self._send_json(
            200,
            {
                "type": response_type,
                "text": text,
                "seq": sequence,
                "ack_seq": sequence,
                "idempotency": state,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

    def _handle_ai_sse(self) -> None:
        body = self._read_json()
        request_id = str(body.get("request_id") or "default")
        fail_this_attempt = STATE.should_fail_ai(request_id, bool(body.get("disconnect_once")))
        tokens = [
            "【Mock 参考】",
            "Redis 缓存减少热点读取延迟；",
            "MySQL 索引缩小查询扫描范围。",
            "两者可配合使用。",
        ]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        time.sleep(0.12)
        try:
            self.wfile.write(sse_event({"type": "response.created", "request_id": request_id}))
            self.wfile.flush()
            for index, token in enumerate(tokens):
                if fail_this_attempt and index == 2:
                    self.close_connection = True
                    return
                time.sleep(0.015)
                self.wfile.write(
                    sse_event({"type": "response.output_text.delta", "delta": token, "index": index})
                )
                self.wfile.flush()
            self.wfile.write(sse_event({"type": "response.completed"}))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


class CleanThreadingHttpServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True


def main() -> int:
    parser = argparse.ArgumentParser(description="meeting-ai-copilot deterministic mock server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT_MIN)
    args = parser.parse_args()
    if not PORT_MIN <= args.port <= PORT_MAX:
        parser.error(f"port must be in {PORT_MIN}-{PORT_MAX}")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("mock server only binds to loopback")

    server = CleanThreadingHttpServer((args.host, args.port), MockHandler)
    print(f"Mock server listening on http://{args.host}:{args.port}", flush=True)
    print(f"Fixture SHA256: {EXPECTED_WAV_SHA256}", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        print("Shutting down mock server", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
