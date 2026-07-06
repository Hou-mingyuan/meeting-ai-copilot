#!/usr/bin/env python3
"""
无 k6 时的 dry-run 压测脚本（Python 标准库，mock ASR/AI）

用法：
  python loadtest/mock_server.py --port 8765   # 终端 1
  python loadtest/dry_run.py --base-url http://127.0.0.1:8765   # 终端 2
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def request_json(url: str, payload: dict | None = None, timeout: float = 10.0) -> tuple[float, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if payload is None else "POST")
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, json.loads(body) if body else {}


def request_sse(url: str, payload: dict, timeout: float = 10.0) -> tuple[float, float, int]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.perf_counter()
    ttfb_ms = 0.0
    deltas = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            chunk = resp.readline()
            if not chunk:
                break
            if ttfb_ms == 0:
                ttfb_ms = (time.perf_counter() - started) * 1000
            if b"output_text.delta" in chunk:
                deltas += 1
    total_ms = (time.perf_counter() - started) * 1000
    return ttfb_ms, total_ms, deltas


def one_iteration(base: str) -> dict[str, float]:
    health_ms, _ = request_json(f"{base}/health")
    asr_ms, asr_body = request_json(
        f"{base}/mock/asr/chunk",
        {"seq": 3, "text_hint": "请说明 MySQL 事务隔离级别？"},
    )
    ai_ttfb, ai_total, ai_deltas = request_sse(
        f"{base}/mock/ai/responses",
        {"input": "Explain CAP theorem briefly."},
    )
    return {
        "health_ms": health_ms,
        "asr_ms": asr_ms,
        "asr_reported_ms": float(asr_body.get("latency_ms", 0)),
        "ai_ttfb_ms": ai_ttfb,
        "ai_total_ms": ai_total,
        "ai_deltas": float(ai_deltas),
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((pct / 100) * (len(ordered) - 1)))
    return ordered[idx]


def main() -> int:
    parser = argparse.ArgumentParser(description="meeting-ai-copilot mock dry-run")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print(f"Dry-run against {base}  iterations={args.iterations}  concurrency={args.concurrency}")

    # 预热，避免首次连接抬高 p95
    for _ in range(3):
        try:
            one_iteration(base)
        except Exception:
            pass

    results: list[dict[str, float]] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(one_iteration, base) for _ in range(args.iterations)]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                errors += 1
                print(f"  ERROR: {exc}")

    if not results:
        print("DRY-RUN FAILED: no successful iterations (is mock_server running?)")
        return 1

    def col(key: str) -> list[float]:
        return [row[key] for row in results]

    summary = {
        "health_p95_ms": percentile(col("health_ms"), 95),
        "asr_p95_ms": percentile(col("asr_ms"), 95),
        "ai_ttfb_p95_ms": percentile(col("ai_ttfb_ms"), 95),
        "ai_total_p95_ms": percentile(col("ai_total_ms"), 95),
        "ai_deltas_avg": statistics.mean(col("ai_deltas")),
        "errors": errors,
        "ok": len(results),
    }

    print("\n=== Dry-run summary (mock, not Volcengine) ===")
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.2f}")
        else:
            print(f"  {k}: {v}")

    passed = (
        summary["errors"] == 0
        and summary["health_p95_ms"] < 500
        and summary["asr_p95_ms"] < 400
        and summary["ai_ttfb_p95_ms"] < 800
    )
    print("\nDRY-RUN", "PASSED" if passed else "FAILED")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
