from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

QUESTION_MARKERS = (
    "what ",
    "how ",
    "why ",
    "when ",
    "where ",
    "which ",
    "can you",
    "could you",
    "would you",
    "please explain",
    "explain ",
    "tell me",
    "describe ",
    "difference between",
    "compare ",
    "介绍",
    "说一下",
    "讲一下",
    "解释",
    "区别",
    "怎么",
    "如何",
    "为什么",
    "能不能",
    "请你",
    "你了解",
    "有没有",
    "排查",
)


@dataclass(frozen=True)
class DetectionResult:
    accepted: bool
    score: float
    reason: str
    normalized: str


class QuestionDetector:
    def __init__(self, config: dict[str, Any]) -> None:
        self.min_chars = max(1, int(config.get("ai_min_question_chars", 12)))
        self.threshold = min(1.0, max(0.0, float(config.get("ai_question_threshold", 0.55))))
        self.cooldown_seconds = max(0.0, float(config.get("ai_cooldown_seconds", 8)))
        self.duplicate_seconds = max(0.0, float(config.get("ai_duplicate_window_seconds", 60)))
        self.auto_enabled = bool(config.get("ai_auto_answer_enabled", True))
        self.send_all = bool(config.get("ai_send_all_transcript", False))
        self._last_normalized = ""
        self._last_at = 0.0

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.casefold()).strip()

    def score(self, text: str) -> float:
        clean = self.normalize(text)
        if len(clean) < self.min_chars:
            return 0.0
        if self.send_all:
            return 1.0
        score = 0.0
        if "?" in clean or "？" in clean:
            score += 0.65
        if any(marker in clean for marker in QUESTION_MARKERS):
            score += 0.55
        if clean.endswith(("吗", "呢", "么")):
            score += 0.2
        return min(1.0, score)

    def evaluate(self, text: str, *, manual: bool = False, now: float | None = None) -> DetectionResult:
        current = time.monotonic() if now is None else now
        normalized = self.normalize(text)
        score = 1.0 if manual else self.score(text)

        if not normalized or len(normalized) < self.min_chars:
            return DetectionResult(False, score, "too_short", normalized)
        if not manual and not self.auto_enabled:
            return DetectionResult(False, score, "auto_disabled", normalized)
        if not manual and score < self.threshold:
            return DetectionResult(False, score, "below_threshold", normalized)
        if self._last_normalized and not manual and current - self._last_at < self.cooldown_seconds:
            return DetectionResult(False, score, "cooldown", normalized)
        if (
            not manual
            and self._last_normalized
            and current - self._last_at < self.duplicate_seconds
            and (
                normalized == self._last_normalized
                or normalized in self._last_normalized
                or self._last_normalized in normalized
            )
        ):
            return DetectionResult(False, score, "duplicate", normalized)

        self._last_normalized = normalized
        self._last_at = current
        return DetectionResult(True, score, "manual" if manual else "question", normalized)

    def set_auto_enabled(self, enabled: bool) -> None:
        self.auto_enabled = bool(enabled)
