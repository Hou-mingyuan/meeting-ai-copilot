from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cloud_runtime import is_question_like


@dataclass
class PartialQuestionState:
    pending_question: str = ""
    pending_since: float = 0.0


def partial_stable_seconds(config: dict[str, Any]) -> float:
    return max(0.2, float(config.get("ai_partial_stable_seconds", 0.8)))


def on_receive_timeout(
    state: PartialQuestionState,
    config: dict[str, Any],
    now: float,
) -> tuple[PartialQuestionState, str | None]:
    if state.pending_question and (now - state.pending_since) >= partial_stable_seconds(config):
        return PartialQuestionState(), state.pending_question
    return state, None


def on_partial_update(
    state: PartialQuestionState,
    text: str,
    config: dict[str, Any],
    now: float,
) -> tuple[PartialQuestionState, str | None]:
    if not is_question_like(text, config):
        return PartialQuestionState(), None
    if text.rstrip().endswith(("?", "？")):
        return PartialQuestionState(), text
    if text != state.pending_question:
        return PartialQuestionState(pending_question=text, pending_since=now), None
    return state, None
