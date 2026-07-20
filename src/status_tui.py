"""Interactive, dependency-free terminal UI for the Windows meeting runtime."""

from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import time
import unicodedata
from typing import Optional


def enable_windows_virtual_terminal() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.argtypes = [ctypes.c_ulong]
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.GetConsoleMode.restype = ctypes.c_int
        kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetConsoleMode.restype = ctypes.c_int
        handle = kernel32.GetStdHandle(0xFFFFFFF5)
        mode = ctypes.c_uint32()
        if handle in {0, -1} or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text)


def fit_display(text: str, width: int, *, pad: bool = True) -> str:
    if width <= 0:
        return ""
    result: list[str] = []
    used = 0
    for char in str(text):
        char_width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + char_width > width:
            break
        result.append(char)
        used += char_width
    value = "".join(result)
    if display_width(str(text)) > width and width >= 2:
        while result and display_width("".join(result)) > width - 1:
            result.pop()
        value = "".join(result) + "…"
        used = display_width(value)
    return value + (" " * max(0, width - used) if pad else "")


class StatusTui:
    PANEL_LINES = 10

    KEY_COMMANDS = {
        " ": "toggle_pause",
        "a": "ask_last",
        "e": "edit_question",
        "c": "cancel_ai",
        "r": "retry_ai",
        "x": "export",
        "1": "mode_system",
        "2": "mode_microphone",
        "3": "mode_mixed",
        "d": "select_devices",
        "t": "toggle_auto_answer",
        "q": "stop",
    }

    def __init__(self, enabled: bool = True, *, force: bool = False, width: int | None = None) -> None:
        self.enabled = enabled and (force or sys.stdout.isatty())
        self._force = force
        self._width = width
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._input_mode = threading.Event()
        self._render_thread: Optional[threading.Thread] = None
        self._keyboard_thread: Optional[threading.Thread] = None
        self._commands: "queue.Queue[str]" = queue.Queue(maxsize=20)
        self._initialized = False
        self._started_at = time.monotonic()
        self.capture = "等待开始"
        self.mode = "系统声音"
        self.devices = "自动选择"
        self.asr = "未连接"
        self.ai = "待命"
        self.last_line = "（暂无转写）"
        self.answer = "（暂无 AI 参考答案）"
        self.notice = "采集前会明确确认；AI 内容仅供参考"
        self.paused = False
        self.auto_answer = True
        self.privacy_confirmed = False

    def start(self) -> None:
        if not self.enabled or self._render_thread is not None:
            return
        enable_windows_virtual_terminal()
        self._stop.clear()
        self._render_thread = threading.Thread(target=self._loop, name="tui-render", daemon=False)
        self._render_thread.start()
        if os.name == "nt" and (self._force or sys.stdin.isatty()):
            self._keyboard_thread = threading.Thread(target=self._keyboard_loop, name="tui-keyboard", daemon=False)
            self._keyboard_thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in (self._keyboard_thread, self._render_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)
        with self._lock:
            if self.enabled and self._initialized:
                sys.stdout.write(f"\033[{self.PANEL_LINES}A\033[J")
                sys.stdout.flush()
                self._initialized = False
        self._keyboard_thread = None
        self._render_thread = None

    @property
    def active_thread_names(self) -> list[str]:
        return [
            thread.name
            for thread in (self._keyboard_thread, self._render_thread)
            if thread is not None and thread.is_alive()
        ]

    def get_command(self, timeout: float = 0.0) -> str | None:
        try:
            return self._commands.get(timeout=timeout)
        except queue.Empty:
            return None

    def submit_command(self, command: str) -> bool:
        try:
            self._commands.put_nowait(command)
            return True
        except queue.Full:
            self.set_notice("命令队列已满，请稍后重试")
            return False

    def prompt(self, label: str, default: str = "") -> str:
        self._input_mode.set()
        try:
            with self._lock:
                if self._initialized:
                    sys.stdout.write(f"\033[{self.PANEL_LINES}A\033[J")
                    sys.stdout.flush()
                    self._initialized = False
            suffix = f" [{default}]" if default else ""
            value = input(f"{label}{suffix}: ").strip()
            return value or default
        finally:
            self._input_mode.clear()

    def set_capture(self, status: str) -> None:
        with self._lock:
            self.capture = status
            self.paused = status in {"已暂停", "暂停"}

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode

    def set_devices(self, devices: str) -> None:
        with self._lock:
            self.devices = " ".join(str(devices or "").split()) or "自动选择"

    def set_asr(self, status: str) -> None:
        with self._lock:
            self.asr = status

    def set_ai(self, status: str) -> None:
        with self._lock:
            self.ai = status

    def set_auto_answer(self, enabled: bool) -> None:
        with self._lock:
            self.auto_answer = bool(enabled)

    def set_privacy_confirmed(self, confirmed: bool) -> None:
        with self._lock:
            self.privacy_confirmed = bool(confirmed)

    def set_notice(self, text: str) -> None:
        with self._lock:
            self.notice = " ".join(str(text or "").split())

    def set_last_line(self, text: str) -> None:
        with self._lock:
            cleaned = " ".join(str(text or "").split())
            self.last_line = cleaned or "（暂无转写）"

    def set_answer(self, text: str) -> None:
        with self._lock:
            cleaned = " ".join(str(text or "").split())
            self.answer = cleaned or "（等待首个流式片段）"

    def append_answer_delta(self, delta: str) -> None:
        if not delta:
            return
        with self._lock:
            current = "" if self.answer.startswith("（") else self.answer
            self.answer = (current + str(delta))[:2000]

    def snapshot(self, width: int | None = None) -> list[str]:
        terminal_width = width or self._width or shutil.get_terminal_size((100, 30)).columns
        terminal_width = max(40, min(140, terminal_width))
        inner = terminal_width - 2
        with self._lock:
            elapsed = int(time.monotonic() - self._started_at)
            privacy = "已确认" if self.privacy_confirmed else "待确认"
            auto = "开" if self.auto_answer else "关"
            lines = [
                "┌" + fit_display(" meeting-ai-copilot · 透明采集 ", inner) + "┐",
                "│" + fit_display(f" 录制 {self.capture}   输入 {self.mode}   隐私 {privacy}", inner) + "│",
                "│" + fit_display(f" 设备 {self.devices}", inner) + "│",
                "│" + fit_display(f" ASR {self.asr}   AI {self.ai}   自动回答 {auto}   运行 {elapsed}s", inner) + "│",
                "│" + fit_display(f" 实时转写 {self.last_line}", inner) + "│",
                "│" + fit_display(f" AI参考答案 {self.answer}", inner) + "│",
                "│" + fit_display(f" 提示 {self.notice}", inner) + "│",
                "│" + fit_display(" [Space]暂停/恢复 [A]回答最近一句 [E]编辑问题 [C]取消AI [R]重试", inner) + "│",
                "│" + fit_display(" [1]系统 [2]麦克风 [3]混合 [D]选设备 [T]自动回答 [X]导出 [Q]停止", inner) + "│",
                "└" + ("─" * inner) + "┘",
            ]
        return lines

    def render_text(self, width: int | None = None) -> str:
        return "\n".join(self.snapshot(width))

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._input_mode.is_set():
                self._render()
            self._stop.wait(0.25)

    def _keyboard_loop(self) -> None:
        import msvcrt

        while not self._stop.is_set():
            if self._input_mode.is_set() or not msvcrt.kbhit():
                self._stop.wait(0.05)
                continue
            key = msvcrt.getwch().casefold()
            command = self.KEY_COMMANDS.get(key)
            if command:
                self.submit_command(command)

    def _render(self) -> None:
        lines = self.snapshot()
        with self._lock:
            if self._initialized:
                sys.stdout.write(f"\033[{self.PANEL_LINES}A")
            for line in lines:
                sys.stdout.write("\033[K" + line + "\n")
            sys.stdout.flush()
            self._initialized = True
