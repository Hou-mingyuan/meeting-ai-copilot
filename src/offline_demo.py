from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app_contracts import AnswerRequest, AppError, AppErrorCode, CancellationToken
from asr_resilience import TranscriptReconciler
from audio_pipeline import FixtureAudioSource
from llm_providers import DeterministicMockLlmProvider, stream_with_recovery
from mock_providers import DeterministicMockAsrProvider
from question_detection import QuestionDetector
from session_store import JsonSessionStore


def load_fixture_metadata(fixture_path: Path) -> dict[str, Any]:
    metadata_path = fixture_path.with_suffix(".json")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"fixture metadata missing: {metadata_path}")
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def run_offline_acceptance(
    fixture_path: Path,
    output_dir: Path,
    *,
    exercise_cancel: bool = True,
) -> dict[str, Any]:
    fixture_path = Path(fixture_path)
    output_dir = Path(output_dir)
    metadata = load_fixture_metadata(fixture_path)
    started = time.perf_counter()
    initial_threads = {thread.name for thread in threading.enumerate()}

    store = JsonSessionStore(
        output_dir,
        audio_mode="fixture",
        devices=[fixture_path.name],
        asr_provider="mock-deterministic",
        llm_provider="mock-deterministic",
        consent_confirmed=True,
    )
    source = FixtureAudioSource(fixture_path)
    source.start()
    chunks = []
    pause_verified = False
    while not source.finished:
        chunk = source.read()
        if chunk is None:
            continue
        chunks.append(chunk)
        if len(chunks) == 6:
            source.pause()
            pause_verified = source.read(timeout=0.001) is None
            source.resume()
    source.stop()

    asr = DeterministicMockAsrProvider(
        expected_text=str(metadata["expected_final"]),
        expected_pcm_sha256=str(metadata["pcm_sha256"]),
        disconnect_after_chunk=12,
    )
    reconciler = TranscriptReconciler()
    events = []
    disconnects = 0
    try:
        events.extend(asr.transcribe_chunks(chunks))
    except AppError as exc:
        if exc.code != AppErrorCode.NETWORK or not exc.retryable:
            raise
        disconnects += 1
    events.extend(asr.transcribe_chunks(chunks))

    accepted_events = []
    final_text = ""
    for event in events:
        accepted = reconciler.accept(event)
        if accepted is None:
            continue
        accepted_events.append(accepted)
        store.add_transcript(accepted)
        if accepted.is_final:
            final_text = accepted.text

    detector = QuestionDetector(
        {
            "ai_min_question_chars": 4,
            "ai_question_threshold": 0.55,
            "ai_cooldown_seconds": 0,
            "ai_auto_answer_enabled": True,
        }
    )
    detection = detector.evaluate(final_text)
    if not detection.accepted:
        raise RuntimeError("fixture final did not trigger the question detector")

    cancelled_answer = False
    if exercise_cancel:
        cancel_request = AnswerRequest(
            uuid.uuid4().hex,
            store.session_id,
            final_text,
            "",
            "Mock ASR（固定音频）",
        )
        cancel_token = CancellationToken()
        store.begin_answer(cancel_request)
        stream = stream_with_recovery(
            DeterministicMockLlmProvider(),
            cancel_request,
            cancel_token,
            base_delay_seconds=0,
        )
        first_delta = next(stream)
        store.append_answer_delta(cancel_request.request_id, first_delta)
        cancel_token.cancel("offline acceptance")
        try:
            next(stream)
        except AppError as exc:
            if exc.code != AppErrorCode.CANCELLED:
                raise
            cancelled_answer = True
            store.finish_answer(cancel_request.request_id, status="cancelled", error=exc.safe_message)

    answer_request = AnswerRequest(
        uuid.uuid4().hex,
        store.session_id,
        final_text,
        "上一句：本轮使用固定音频 fixture。",
        "Mock ASR（固定音频）",
        True,
    )
    store.begin_answer(answer_request)
    answer = ""
    recovering_llm = DeterministicMockLlmProvider(fail_first=True)
    for delta in stream_with_recovery(
        recovering_llm,
        answer_request,
        CancellationToken(),
        max_attempts=3,
        base_delay_seconds=0,
    ):
        answer += delta
        store.append_answer_delta(answer_request.request_id, delta)
    store.finish_answer(answer_request.request_id)
    store.record_event("acceptance", "completed", "fixture + Mock ASR + Mock AI")
    store.close()
    markdown_path = Path(store.export("md"))
    text_path = Path(store.export("txt"))

    final_threads = {thread.name for thread in threading.enumerate()}
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "status": "passed",
        "mode": "offline-fixture",
        "fixture": str(fixture_path),
        "wav_sha256": metadata["wav_sha256"],
        "pcm_sha256": metadata["pcm_sha256"],
        "audio_chunks": len(chunks),
        "pause_resume_verified": pause_verified,
        "asr_disconnects_recovered": disconnects,
        "partial_events": sum(not event.is_final for event in accepted_events),
        "final_text": final_text,
        "question_triggered": detection.accepted,
        "ai_cancel_verified": cancelled_answer if exercise_cancel else None,
        "ai_retry_calls": recovering_llm.calls,
        "answer": answer,
        "session_json": str(store.path),
        "export_markdown": str(markdown_path),
        "export_text": str(text_path),
        "elapsed_ms": round(elapsed_ms, 2),
        "residual_threads": sorted(final_threads - initial_threads),
    }
