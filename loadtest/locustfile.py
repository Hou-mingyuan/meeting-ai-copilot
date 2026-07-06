"""Locust mock load test for meeting-ai-copilot pipeline helpers.

Does not call Volcengine ASR or real LLM APIs. Exercises config load and
question-detection throughput that mirrors the desktop runtime hot path.

Usage:
  pip install locust
  locust -f loadtest/locustfile.py --headless -u 100 -r 20 -t 30s --only-summary
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from locust import User, between, task

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_runtime import is_question_like, load_config  # noqa: E402


CONFIG = load_config(ROOT / "config.example.json")
QUESTIONS = [
    "请介绍一下你在 Spring Boot 项目里如何做 MySQL 索引优化？",
    "How do you design a Redis cache invalidation strategy?",
    "这段只是普通转写，没有问句标记。",
    "Why would you choose Kafka over RabbitMQ in an event-driven system?",
]


class QuestionDetectionUser(User):
    wait_time = between(0.05, 0.15)

    @task(4)
    def detect_questions(self) -> None:
        start = time.perf_counter()
        hits = sum(1 for q in QUESTIONS if is_question_like(q, CONFIG))
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.environment.events.request.fire(
            request_type="LOCAL",
            name="is_question_like_batch",
            response_time=elapsed_ms,
            response_length=hits,
            exception=None,
            context={},
        )

    @task(1)
    def reload_config(self) -> None:
        start = time.perf_counter()
        cfg = load_config(ROOT / "config.example.json")
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.environment.events.request.fire(
            request_type="LOCAL",
            name="load_config",
            response_time=elapsed_ms,
            response_length=len(json.dumps(cfg)),
            exception=None,
            context={},
        )
