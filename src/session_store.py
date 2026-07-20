from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app_contracts import AnswerRequest, TranscriptEvent


def _iso_timestamp(value: float | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
    return datetime.fromtimestamp(value, timezone.utc).astimezone().isoformat(timespec="milliseconds")


class JsonSessionStore:
    SCHEMA_VERSION = 1

    def __init__(
        self,
        output_dir: Path,
        *,
        audio_mode: str,
        devices: list[str] | None = None,
        asr_provider: str = "",
        llm_provider: str = "",
        consent_confirmed: bool = False,
        session_id: str | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or uuid.uuid4().hex
        self.path = self.output_dir / f"session-{self.session_id}.json"
        self._lock = threading.RLock()
        self._closed = False
        self._data: dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "session_id": self.session_id,
            "started_at": _iso_timestamp(),
            "ended_at": None,
            "state": "recording",
            "privacy": {
                "consent_confirmed": bool(consent_confirmed),
                "audio_mode": audio_mode,
                "devices": list(devices or []),
            },
            "providers": {"asr": asr_provider, "llm": llm_provider},
            "transcripts": [],
            "answers": [],
            "events": [],
        }
        self._flush_locked()

    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def _flush_locked(self) -> None:
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(self._data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(6):
            try:
                os.replace(temp_path, self.path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (2**attempt))

    def record_event(self, component: str, state: str, message: str = "") -> None:
        with self._lock:
            self._data["events"].append(
                {"at": _iso_timestamp(), "component": component, "state": state, "message": message}
            )
            self._flush_locked()

    def add_transcript(self, event: TranscriptEvent) -> None:
        with self._lock:
            self._data["transcripts"].append(
                {
                    "event_id": event.event_id,
                    "sequence": event.sequence,
                    "at": _iso_timestamp(event.received_at),
                    "source": event.source,
                    "type": "final" if event.is_final else "partial",
                    "text": event.text,
                }
            )
            self._flush_locked()

    def begin_answer(self, request: AnswerRequest) -> None:
        with self._lock:
            self._data["answers"].append(
                {
                    "request_id": request.request_id,
                    "at": _iso_timestamp(request.created_at),
                    "source": request.source,
                    "manual": request.manual,
                    "question": request.question,
                    "context": request.context,
                    "label": "AI 参考答案（非会议原话）",
                    "status": "streaming",
                    "text": "",
                    "error": "",
                }
            )
            self._flush_locked()

    def _find_answer(self, request_id: str) -> dict[str, Any]:
        for answer in reversed(self._data["answers"]):
            if answer["request_id"] == request_id:
                return answer
        raise KeyError(f"unknown answer request: {request_id}")

    def append_answer_delta(self, request_id: str, delta: str) -> None:
        if not delta:
            return
        with self._lock:
            answer = self._find_answer(request_id)
            answer["text"] += delta
            self._flush_locked()

    def finish_answer(self, request_id: str, status: str = "completed", error: str = "") -> None:
        with self._lock:
            answer = self._find_answer(request_id)
            answer["status"] = status
            answer["error"] = error
            answer["finished_at"] = _iso_timestamp()
            self._flush_locked()

    def set_state(self, state: str) -> None:
        with self._lock:
            self._data["state"] = state
            self._flush_locked()

    def close(self, state: str = "stopped") -> None:
        with self._lock:
            if self._closed:
                return
            self._data["state"] = state
            self._data["ended_at"] = _iso_timestamp()
            self._closed = True
            self._flush_locked()

    def export(self, format_name: str) -> str:
        format_name = format_name.lower().lstrip(".")
        if format_name not in {"txt", "md", "markdown"}:
            raise ValueError("export format must be txt or md")
        suffix = ".md" if format_name in {"md", "markdown"} else ".txt"
        target = self.output_dir / f"session-{self.session_id}{suffix}"
        with self._lock:
            data = self.data

        lines: list[str] = []
        if suffix == ".md":
            lines.extend(
                [
                    "# 会议记录",
                    "",
                    f"- 会话 ID：`{data['session_id']}`",
                    f"- 开始：{data['started_at']}",
                    f"- 结束：{data['ended_at'] or '进行中'}",
                    f"- 输入：{data['privacy']['audio_mode']}",
                    "",
                    "## 转写",
                    "",
                ]
            )
            for item in data["transcripts"]:
                if item["type"] == "final":
                    lines.append(f"- `{item['at']}` **{item['source']}**：{item['text']}")
            lines.extend(["", "## AI 参考答案", "", "> 以下内容由 AI 生成，不是会议原话。", ""])
            for item in data["answers"]:
                lines.extend(
                    [
                        f"### {item['question']}",
                        "",
                        item["text"] or f"（{item['status']}）",
                        "",
                    ]
                )
        else:
            lines.extend(
                [
                    "会议记录",
                    f"会话 ID: {data['session_id']}",
                    f"开始: {data['started_at']}",
                    f"结束: {data['ended_at'] or '进行中'}",
                    f"输入: {data['privacy']['audio_mode']}",
                    "",
                    "[转写]",
                ]
            )
            for item in data["transcripts"]:
                if item["type"] == "final":
                    lines.append(f"{item['at']} [{item['source']}] {item['text']}")
            lines.extend(["", "[AI 参考答案 - 非会议原话]"])
            for item in data["answers"]:
                lines.extend([f"问题: {item['question']}", item["text"] or f"（{item['status']}）", ""])

        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
        return str(target)

    @staticmethod
    def purge_old(output_dir: Path, retention_days: int, now: datetime | None = None) -> list[Path]:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        current = now or datetime.now(timezone.utc)
        cutoff = current - timedelta(days=retention_days)
        removed: list[Path] = []
        for path in Path(output_dir).glob("session-*"):
            if path.suffix.lower() not in {".json", ".txt", ".md", ".tmp"} or not path.is_file():
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                path.unlink()
                removed.append(path)
        return removed
