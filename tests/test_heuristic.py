from __future__ import annotations

import json
import queue
from pathlib import Path

import pytest

from cloud_asr_volcengine import build_start_request
from cloud_runtime import (
    DEFAULT_CONFIG,
    Logger,
    dated_file_name,
    is_question_like,
    load_config,
    maybe_enqueue_ai_question,
)
from transcript_question_fsm import (
    PartialQuestionState,
    on_partial_update,
    on_receive_timeout,
    partial_stable_seconds,
)


@pytest.fixture
def base_config() -> dict:
    return DEFAULT_CONFIG.copy()


@pytest.fixture
def example_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "cloud_asr_sample_rate": 8000,
                "cloud_asr_hotwords": ["Redis", "MySQL"],
                "ai_min_question_chars": 4,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path / "missing.json")
        assert cfg["cloud_asr_sample_rate"] == DEFAULT_CONFIG["cloud_asr_sample_rate"]
        assert cfg["ai_cooldown_seconds"] == DEFAULT_CONFIG["ai_cooldown_seconds"]

    def test_user_values_override_defaults(self, example_config_path: Path) -> None:
        cfg = load_config(example_config_path)
        assert cfg["cloud_asr_sample_rate"] == 8000
        assert cfg["cloud_asr_hotwords"] == ["Redis", "MySQL"]
        assert cfg["ai_cooldown_seconds"] == DEFAULT_CONFIG["ai_cooldown_seconds"]


class TestIsQuestionLike:
    def test_english_question_marker(self, base_config: dict) -> None:
        text = "Can you explain the difference between Redis cache and MySQL index?"
        assert is_question_like(text, base_config) is True

    def test_chinese_statement_rejected(self, base_config: dict) -> None:
        assert is_question_like("今天会议先同步项目进度", base_config) is False

    def test_question_mark_shortcut(self, base_config: dict) -> None:
        cfg = {**base_config, "ai_min_question_chars": 4}
        assert is_question_like("MySQL 索引应该怎么建立？", cfg) is True

    def test_too_short_rejected(self, base_config: dict) -> None:
        cfg = {**base_config, "ai_min_question_chars": 20}
        assert is_question_like("怎么优化？", cfg) is False

    def test_send_all_transcript_mode(self, base_config: dict) -> None:
        cfg = {**base_config, "ai_send_all_transcript": True, "ai_min_question_chars": 4}
        assert is_question_like("今天同步进度", cfg) is True


class TestBuildStartRequest:
    def test_sample_rate_from_config(self, base_config: dict) -> None:
        cfg = {**base_config, "cloud_asr_sample_rate": 8000}
        req = build_start_request(cfg)
        assert req["audio"]["rate"] == 8000

    def test_inline_hotwords_when_no_boost_table(self, base_config: dict) -> None:
        cfg = {
            **base_config,
            "cloud_asr_hotwords": ["事务", "Redis"],
            "cloud_asr_boosting_table_id": "",
            "cloud_asr_boosting_table_name": "",
        }
        req = build_start_request(cfg)
        corpus = req["request"]["corpus"]
        assert "context" in corpus
        assert "事务" in corpus["context"]

    def test_boost_table_suppresses_inline_hotwords(self, base_config: dict) -> None:
        cfg = {
            **base_config,
            "cloud_asr_hotwords": ["事务"],
            "cloud_asr_boosting_table_id": "table-123",
        }
        req = build_start_request(cfg)
        corpus = req["request"]["corpus"]
        assert "context" not in corpus
        assert corpus["boosting_table_id"] == "table-123"


class TestTranscriptQuestionFsm:
    def test_partial_stable_seconds_floor(self, base_config: dict) -> None:
        assert partial_stable_seconds({**base_config, "ai_partial_stable_seconds": 0.05}) == 0.2

    def test_non_question_clears_pending(self, base_config: dict) -> None:
        state = PartialQuestionState(pending_question="old", pending_since=1.0)
        new_state, ask = on_partial_update(state, "今天先过一下排期", base_config, now=2.0)
        assert ask is None
        assert new_state.pending_question == ""

    def test_question_mark_triggers_immediate_ask(self, base_config: dict) -> None:
        state = PartialQuestionState()
        text = "Redis 和 MySQL 索引有什么区别？"
        new_state, ask = on_partial_update(state, text, base_config, now=1.0)
        assert ask == text
        assert new_state.pending_question == ""

    def test_partial_question_waits_for_stable_timeout(self, base_config: dict) -> None:
        cfg = {**base_config, "ai_partial_stable_seconds": 0.8}
        state = PartialQuestionState()
        text = "Can you explain how Redis cache eviction works"
        state, ask = on_partial_update(state, text, cfg, now=10.0)
        assert ask is None
        assert state.pending_question == text

        state, ask = on_receive_timeout(state, cfg, now=10.5)
        assert ask is None

        state, ask = on_receive_timeout(state, cfg, now=10.9)
        assert ask == text
        assert state.pending_question == ""

    def test_same_partial_does_not_reset_timer(self, base_config: dict) -> None:
        cfg = {**base_config, "ai_partial_stable_seconds": 1.0}
        state = PartialQuestionState()
        text = "please explain the difference between thread pool sizes"
        state, _ = on_partial_update(state, text, cfg, now=5.0)
        state, ask = on_partial_update(state, text, cfg, now=5.5)
        assert ask is None
        assert state.pending_since == 5.0


class TestMaybeEnqueueAiQuestion:
    def test_cooldown_blocks_duplicate_enqueue(self, tmp_path: Path, base_config: dict) -> None:
        ai_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=4)
        logger = Logger(tmp_path / "run.log")
        state: dict = {}
        cfg = {**base_config, "ai_cooldown_seconds": 30}
        question = "Can you explain Redis persistence modes in detail?"

        maybe_enqueue_ai_question(ai_queue, cfg, logger, state, "云端实时ASR", question)
        maybe_enqueue_ai_question(ai_queue, cfg, logger, state, "云端实时ASR", question)

        assert ai_queue.qsize() == 1


class TestDatedFileName:
    def test_prefixes_date_when_missing(self) -> None:
        assert dated_file_name("实时监听.txt", "2026-07-06") == "2026-07-06_实时监听.txt"

    def test_keeps_existing_date_prefix(self) -> None:
        assert dated_file_name("2026-07-06_实时监听.txt", "2026-07-06") == "2026-07-06_实时监听.txt"
