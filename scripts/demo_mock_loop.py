#!/usr/bin/env python3
"""Zero-key mock demo: meeting audio → ASR partial/final → AI streaming answer.

Requires loadtest/mock_server.py running (or use scripts/demo-mock.ps1).

  python scripts/demo_mock_loop.py --base-url http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def post_json(url: str, payload: dict, timeout: float = 15.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def stream_ai(url: str, question: str, timeout: float = 15.0) -> str:
    data = json.dumps({"input": question}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    answer_parts: list[str] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            if b"output_text.delta" not in line:
                continue
            try:
                payload = json.loads(line.decode("utf-8").strip().removeprefix("data: "))
                delta = payload.get("delta", "")
                if delta:
                    answer_parts.append(delta)
                    print(delta, end="", flush=True)
            except json.JSONDecodeError:
                continue
    print()
    return "".join(answer_parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="meeting-ai-copilot zero-key mock demo loop")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    question = "请解释一下 Redis 缓存和 MySQL 索引分别解决什么问题？"
    print("=== meeting-ai-copilot · Mock 演示（零密钥）===\n")
    print("[1/3] 模拟会议音频 → 云端 ASR 流式转写\n")

    final_text = ""
    try:
        for seq in range(1, 6):
            body = post_json(
                f"{base}/mock/asr/chunk",
                {"seq": seq, "text_hint": question},
            )
            text = str(body.get("text", ""))
            kind = body.get("type", "partial")
            print(f"  ASR {kind:7} seq={seq}: {text}")
            if kind == "final":
                final_text = text
            time.sleep(0.05)
    except urllib.error.URLError as exc:
        print(f"\nMock 服务不可达 ({base})：{exc}", file=sys.stderr)
        print("请先运行: python loadtest/mock_server.py --port 8765", file=sys.stderr)
        return 1

    if not final_text:
        final_text = question

    print(f"\n[2/3] 识别为面试问题，触发 AI 流式参考答案\n")
    print("  AI streaming: ", end="", flush=True)

    try:
        answer = stream_ai(f"{base}/mock/ai/responses", final_text)
    except urllib.error.URLError as exc:
        print(f"\nAI mock 失败：{exc}", file=sys.stderr)
        return 1

    print(f"\n[3/3] 演示完成 · 转写 {len(final_text)} 字 · 答案 {len(answer)} 字")
    print("\nMOCK DEMO OK（未调用火山 ASR/LLM API）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
