"""Minimal terminal status panel for meeting-ai-copilot (stdlib only)."""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional


class StatusTui:
    """Fixed 4-line status panel; refreshes in place when stdout is a TTY."""

    PANEL_LINES = 4

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled and sys.stdout.isatty()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._initialized = False
        self._started_at = time.monotonic()
        self.capture = "等待"
        self.asr = "未连接"
        self.ai = "待命"
        self.last_line = "（暂无）"

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.enabled and self._initialized:
            sys.stdout.write(f"\033[{self.PANEL_LINES}A\033[J")
            sys.stdout.flush()
            self._initialized = False

    def set_capture(self, status: str) -> None:
        with self._lock:
            self.capture = status

    def set_asr(self, status: str) -> None:
        with self._lock:
            self.asr = status

    def set_ai(self, status: str) -> None:
        with self._lock:
            self.ai = status

    def set_last_line(self, text: str) -> None:
        with self._lock:
            cleaned = " ".join(str(text or "").split())
            self.last_line = cleaned[:72] if cleaned else "（暂无）"

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._render()
            self._stop.wait(0.45)

    def _render(self) -> None:
        with self._lock:
            elapsed = int(time.monotonic() - self._started_at)
            lines = [
                "┌─ meeting-ai-copilot 状态 ─────────────────────────────",
                f"│ 采集: {self.capture:<8}  ASR: {self.asr:<10}  AI: {self.ai:<8}",
                f"│ 最近一句: {self.last_line}",
                f"└─ 运行 {elapsed}s · Ctrl+C 退出 · 详情见桌面运行日志 ─────",
            ]
        if self._initialized:
            sys.stdout.write(f"\033[{self.PANEL_LINES}A")
        for line in lines:
            sys.stdout.write("\033[K" + line + "\n")
        sys.stdout.flush()
        self._initialized = True
