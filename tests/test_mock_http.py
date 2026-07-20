from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 19062
BASE_URL = f"http://127.0.0.1:{PORT}"


def wait_for_health(timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=0.5) as response:
                return json.loads(response.read().decode("utf-8"))
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("mock server did not become healthy")


def test_http_mock_fixture_disconnect_sse_and_perf() -> None:
    server = subprocess.Popen(
        [sys.executable, "loadtest/mock_server.py", "--port", str(PORT)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        health = wait_for_health()
        assert health["mode"] == "deterministic-fixture"
        demo = subprocess.run(
            [
                sys.executable,
                "scripts/demo_mock_loop.py",
                "--base-url",
                BASE_URL,
                "--fixture",
                "tests/fixtures/meeting_question.wav",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert demo.returncode == 0, demo.stdout + demo.stderr
        assert "ASR重连 1" in demo.stdout
        assert "AI重连 1" in demo.stdout
        performance = subprocess.run(
            [
                sys.executable,
                "loadtest/dry_run.py",
                "--base-url",
                BASE_URL,
                "--iterations",
                "4",
                "--concurrency",
                "2",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert performance.returncode == 0, performance.stdout + performance.stderr
        assert "DRY-RUN PASSED" in performance.stdout
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_mock_server_rejects_ports_outside_reserved_range() -> None:
    result = subprocess.run(
        [sys.executable, "loadtest/mock_server.py", "--port", "8765"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "19060-19069" in result.stderr
