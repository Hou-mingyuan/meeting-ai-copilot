from __future__ import annotations

from question_detection import QuestionDetector


def config(**overrides):
    return {
        "ai_min_question_chars": 4,
        "ai_question_threshold": 0.55,
        "ai_cooldown_seconds": 8,
        "ai_duplicate_window_seconds": 60,
        "ai_auto_answer_enabled": True,
        **overrides,
    }


def test_question_score_and_statement_rejection() -> None:
    detector = QuestionDetector(config())
    assert detector.evaluate("请解释一下 Redis 和 MySQL 的区别？", now=10).accepted is True
    rejected = QuestionDetector(config()).evaluate("今天同步项目进度", now=10)
    assert rejected.accepted is False
    assert rejected.reason == "below_threshold"


def test_auto_off_still_allows_manual_edited_question() -> None:
    detector = QuestionDetector(config(ai_auto_answer_enabled=False))
    assert detector.evaluate("怎么优化索引？", now=1).reason == "auto_disabled"
    result = detector.evaluate("请解释索引失效场景", manual=True, now=2)
    assert result.accepted is True
    assert result.reason == "manual"


def test_cooldown_and_duplicate_are_explicit() -> None:
    detector = QuestionDetector(config(ai_cooldown_seconds=5))
    assert detector.evaluate("如何设计缓存？", now=10).accepted
    assert detector.evaluate("为什么索引失效？", now=11).reason == "cooldown"
    assert detector.evaluate("如何设计缓存？", now=20).reason == "duplicate"


def test_threshold_is_configurable() -> None:
    detector = QuestionDetector(config(ai_question_threshold=0.9))
    assert detector.evaluate("请解释缓存策略", now=1).accepted is False
    assert detector.evaluate("请解释缓存策略？", now=2).accepted is True
