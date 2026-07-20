from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from app_contracts import AppError, AppErrorCode, AudioChunk
from asr_resilience import AudioReplayBuffer
from cloud_asr_volcengine import confirm_capture_consent, get_cloud_asr_key, receive_responses
from cloud_runtime import Logger, build_paths, get_ai_api_key, load_config


class FakeWebSocket:
    def __init__(self, messages, stop_event: threading.Event) -> None:
        self.messages = list(messages)
        self.stop_event = stop_event

    async def recv(self):
        value = self.messages.pop(0)
        if not self.messages:
            self.stop_event.set()
        return value


def test_receive_partial_final_and_confirm_replay(monkeypatch) -> None:
    stop = threading.Event()
    websocket = FakeWebSocket([b"partial", b"final"], stop)
    parsed = {
        b"partial": {
            "sequence": 2,
            "message": {"result": {"utterances": [{"text": "请解释 Redis", "definite": False}]}},
        },
        b"final": {
            "sequence": 3,
            "message": {"result": {"utterances": [{"text": "请解释 Redis？", "definite": True}]}},
        },
    }
    monkeypatch.setattr(
        "cloud_asr_volcengine.VolcengineAsrFunctionsV3.parse_response",
        lambda raw: parsed[raw],
    )
    replay = AudioReplayBuffer(3)
    replay.append(AudioChunk(1, b"\x00\x00"))
    events = []
    asyncio.run(receive_responses(websocket, stop, events.append, lambda: None, replay))
    assert [event.is_final for event in events] == [False, True]
    assert events[-1].text == "请解释 Redis？"
    assert len(replay) == 0


def test_receive_raises_top_level_provider_error(monkeypatch) -> None:
    stop = threading.Event()
    websocket = FakeWebSocket([b"error"], stop)
    monkeypatch.setattr(
        "cloud_asr_volcengine.VolcengineAsrFunctionsV3.parse_response",
        lambda raw: {"code": 45000030, "message": "requested resource not granted"},
    )
    with pytest.raises(AppError) as raised:
        asyncio.run(receive_responses(websocket, stop, lambda event: None, lambda: None, AudioReplayBuffer(3)))
    assert raised.value.code == AppErrorCode.AUTHENTICATION
    assert raised.value.retryable is False


def test_logger_redacts_credentials(tmp_path: Path, capsys) -> None:
    log_path = tmp_path / "run.log"
    logger = Logger(log_path)
    logger.write("Authorization: Bearer secret-value api_key=abcdefghijklmnopqrstuvwxyz123456")
    content = log_path.read_text(encoding="utf-8")
    assert "secret-value" not in content
    assert "abcdefghijklmnopqrstuvwxyz123456" not in content
    assert "<redacted>" in content or "<redacted-token>" in content


def test_noninteractive_capture_requires_explicit_confirmation(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path / "missing.json")
    config["output_directory"] = str(tmp_path / "output")
    paths = build_paths(config)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert confirm_capture_consent(config, paths, preconfirmed=False) is False
    assert confirm_capture_consent(config, paths, preconfirmed=True) is True


def test_legacy_capture_flags_map_to_audio_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"capture_system_audio": true, "capture_microphone": true}',
        encoding="utf-8",
    )
    assert load_config(config_path)["audio_mode"] == "mixed"


def test_environment_keys_override_disk_values(monkeypatch) -> None:
    monkeypatch.setenv("VOLC_ASR_API_KEY", "environment-asr")
    monkeypatch.setenv("CUSTOM_AI_KEY", "environment-ai")
    assert get_cloud_asr_key({"cloud_asr_api_key": "disk-asr"}, "cloud_asr_api_key", "VOLC_ASR_API_KEY") == "environment-asr"
    assert get_ai_api_key({"ai_api_key": "disk-ai", "ai_api_key_env": "CUSTOM_AI_KEY"}) == "environment-ai"
