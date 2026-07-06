#!/usr/bin/env python3
"""
meeting-ai-copilot 压测 Mock 服务（不调用真实火山 ASR/AI）

模拟：
  GET  /health
  POST /mock/asr/chunk        — 100ms 级 ASR partial/final 延迟
  POST /mock/ai/responses     — SSE 流式答案（mock TTFB + token 间隔）
"""
from __future__ import annotations

import argparse
import json
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


def sse_event(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class MockHandler(BaseHTTPRequestHandler):
    server_version = "MeetingMock/1.0"

    def log_message(self, format: str, *args) -> None:
        pass

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"status": "ok", "service": "meeting-ai-copilot-mock"})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/mock/asr/chunk":
            self._handle_asr_chunk()
            return
        if path == "/mock/ai/responses":
            self._handle_ai_sse()
            return
        self._send_json(404, {"error": "not_found"})

    def _handle_asr_chunk(self) -> None:
        started = time.perf_counter()
        body = self._read_json()
        seq = int(body.get("seq", 0))
        # 模拟 80–180ms ASR 处理
        time.sleep(random.uniform(0.08, 0.18))
        is_final = seq > 0 and seq % 5 == 0
        text = body.get("text_hint") or "请解释一下 Redis 和 MySQL 索引的区别？"
        partial = text[: min(len(text), 6 + seq * 4)]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        self._send_json(
            200,
            {
                "type": "final" if is_final else "partial",
                "text": partial if not is_final else text,
                "seq": seq,
                "latency_ms": elapsed_ms,
            },
        )

    def _handle_ai_sse(self) -> None:
        body = self._read_json()
        question = str(body.get("input", "mock question"))
        tokens = [
            "Redis",
            " 适合",
            "缓存",
            "热点",
            "数据；",
            "MySQL",
            " 索引",
            " 优化",
            " 查询",
            "路径。",
        ]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        # mock TTFB
        time.sleep(random.uniform(0.15, 0.35))
        self.wfile.write(sse_event({"type": "response.created", "question_len": len(question)}))
        self.wfile.flush()

        for i, token in enumerate(tokens):
            time.sleep(random.uniform(0.02, 0.06))
            self.wfile.write(
                sse_event(
                    {
                        "type": "response.output_text.delta",
                        "delta": token,
                        "index": i,
                    }
                )
            )
            self.wfile.flush()

        self.wfile.write(sse_event({"type": "response.completed"}))
        self.wfile.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="meeting-ai-copilot mock load-test server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MockHandler)
    print(f"Mock server listening on http://{args.host}:{args.port}")
    print("Endpoints: GET /health, POST /mock/asr/chunk, POST /mock/ai/responses")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down mock server")
        server.shutdown()


if __name__ == "__main__":
    main()
