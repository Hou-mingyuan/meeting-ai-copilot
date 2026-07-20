from __future__ import annotations

import hashlib
import queue
import threading
import time
import warnings
import wave
from pathlib import Path
from typing import Any

import numpy as np

from app_contracts import AppError, AppErrorCode, AudioChunk, AudioDeviceInfo, AudioMode, StatusCallback, StatusEvent


def normalize_float_audio(
    samples: np.ndarray,
    source_rate: int,
    target_rate: int,
    *,
    gain: float = 1.0,
) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.reshape(-1)
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate != target_rate and data.size:
        target_length = max(1, int(round(data.size * target_rate / source_rate)))
        source_positions = np.linspace(0.0, 1.0, num=data.size, endpoint=False)
        target_positions = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
        data = np.interp(target_positions, source_positions, data).astype(np.float32)
    if gain != 1.0:
        data = data * float(gain)
    return np.clip(data, -1.0, 1.0).astype(np.float32, copy=False)


def float_audio_to_pcm16(samples: np.ndarray) -> bytes:
    data = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (data * 32767.0).astype("<i2").tobytes()


def pcm16_to_float_audio(pcm: bytes) -> np.ndarray:
    if len(pcm) % 2:
        raise ValueError("PCM16 byte length must be even")
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def pcm16_rms(pcm: bytes) -> float:
    data = pcm16_to_float_audio(pcm)
    return float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0


def stable_device_id(kind: str, device: Any) -> str:
    """Return a compact ID derived from the OS endpoint ID, not enumeration order."""

    native_id = str(getattr(device, "id", "") or getattr(device, "name", "") or "unknown")
    digest = hashlib.sha256(native_id.casefold().encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


def mix_pcm16(chunks: list[bytes]) -> bytes:
    arrays = [pcm16_to_float_audio(chunk) for chunk in chunks if chunk]
    if not arrays:
        return b""
    length = min(array.size for array in arrays)
    if length <= 0:
        return b""
    mixed = np.mean(np.stack([array[:length] for array in arrays]), axis=0)
    return float_audio_to_pcm16(mixed)


class DropOldestAudioBuffer:
    def __init__(self, max_chunks: int) -> None:
        self._queue: "queue.Queue[AudioChunk]" = queue.Queue(maxsize=max(1, max_chunks))
        self.dropped_chunks = 0

    @property
    def queue(self) -> "queue.Queue[AudioChunk]":
        return self._queue

    def put(self, chunk: AudioChunk) -> bool:
        dropped = False
        try:
            self._queue.put_nowait(chunk)
            return dropped
        except queue.Full:
            try:
                self._queue.get_nowait()
                self.dropped_chunks += 1
                dropped = True
            except queue.Empty:
                pass
            self._queue.put_nowait(chunk)
            return dropped

    def get(self, timeout: float | None = None) -> AudioChunk:
        return self._queue.get(timeout=timeout)

    def drain(self) -> list[AudioChunk]:
        chunks: list[AudioChunk] = []
        while True:
            try:
                chunks.append(self._queue.get_nowait())
            except queue.Empty:
                return chunks

    def __len__(self) -> int:
        return self._queue.qsize()


def enumerate_audio_devices() -> list[AudioDeviceInfo]:
    import soundcard as sc

    devices: list[AudioDeviceInfo] = []
    try:
        default_speaker_name = sc.default_speaker().name
    except Exception:
        default_speaker_name = ""
    try:
        default_microphone_name = sc.default_microphone().name
    except Exception:
        default_microphone_name = ""

    loopbacks = [
        item
        for item in sc.all_microphones(include_loopback=True)
        if bool(getattr(item, "isloopback", False))
    ]
    microphones = [
        item
        for item in sc.all_microphones(include_loopback=False)
        if not bool(getattr(item, "isloopback", False))
    ]
    for device in loopbacks:
        devices.append(
            AudioDeviceInfo(
                id=stable_device_id("system", device),
                name=device.name,
                kind="system",
                is_default=bool(default_speaker_name and default_speaker_name.casefold() in device.name.casefold()),
                is_loopback=True,
            )
        )
    for device in microphones:
        devices.append(
            AudioDeviceInfo(
                id=stable_device_id("microphone", device),
                name=device.name,
                kind="microphone",
                is_default=device.name.casefold() == default_microphone_name.casefold(),
                is_loopback=False,
            )
        )
    return devices


def format_device_table(devices: list[AudioDeviceInfo]) -> str:
    if not devices:
        return "未检测到音频设备"
    id_width = max(18, *(len(device.id) for device in devices))
    lines = [f"{'ID':<{id_width}} 类型       默认  设备名称", "-" * (id_width + 54)]
    for device in devices:
        default = "是" if device.is_default else ""
        kind = "系统声音" if device.kind == "system" else "麦克风"
        lines.append(f"{device.id:<{id_width}} {kind:<10} {default:<4} {device.name}")
    return "\n".join(lines)


class SoundCardAudioSource:
    def __init__(
        self,
        mode: AudioMode,
        *,
        sample_rate: int = 16000,
        chunk_ms: int = 100,
        system_device: str | None = None,
        microphone_device: str | None = None,
        system_gain: float = 1.0,
        microphone_gain: float = 1.0,
        silence_threshold: float = 0.0005,
        status_callback: StatusCallback | None = None,
    ) -> None:
        if mode == AudioMode.FIXTURE:
            raise ValueError("SoundCardAudioSource does not support fixture mode")
        self.mode = mode
        self.sample_rate = max(8000, int(sample_rate))
        self.chunk_ms = min(1000, max(20, int(chunk_ms)))
        self.system_device = system_device
        self.microphone_device = microphone_device
        self.system_gain = float(system_gain)
        self.microphone_gain = float(microphone_gain)
        self.silence_threshold = max(0.0, float(silence_threshold))
        self.status_callback = status_callback
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._queues: dict[str, "queue.Queue[bytes]"] = {}
        self._threads: list[threading.Thread] = []
        self._sequence = 0
        self._started = False
        self._fatal_error: AppError | None = None
        self.device_names: dict[str, str] = {}
        self.dropped_chunks = 0

    def _emit(self, state: str, message: str = "") -> None:
        if self.status_callback:
            self.status_callback(StatusEvent("audio", state, message))

    def _labels(self) -> list[str]:
        if self.mode == AudioMode.SYSTEM:
            return ["system"]
        if self.mode == AudioMode.MICROPHONE:
            return ["microphone"]
        return ["system", "microphone"]

    @staticmethod
    def _match_device(devices: list[Any], selector: str | None, prefix: str) -> Any | None:
        if selector:
            if selector.startswith(prefix + ":"):
                for device in devices:
                    if selector.casefold() == stable_device_id(prefix, device).casefold():
                        return device
                try:
                    index = int(selector.split(":", 1)[1])
                    if 0 <= index < len(devices):
                        return devices[index]
                except ValueError:
                    pass
            selected = selector.casefold()
            for device in devices:
                if selected == device.name.casefold() or selected in device.name.casefold():
                    return device
        return None

    def _resolve(self, label: str):
        import soundcard as sc

        if label == "system":
            loopbacks = [
                item
                for item in sc.all_microphones(include_loopback=True)
                if bool(getattr(item, "isloopback", False))
            ]
            selected = self._match_device(loopbacks, self.system_device, "system")
            if selected is not None:
                return selected
            default_name = sc.default_speaker().name.casefold()
            for device in loopbacks:
                name = device.name.casefold()
                if default_name in name or name in default_name:
                    return device
            if loopbacks:
                return loopbacks[0]
            return sc.get_microphone(sc.default_speaker().name, include_loopback=True)

        microphones = [
            item
            for item in sc.all_microphones(include_loopback=False)
            if not bool(getattr(item, "isloopback", False))
        ]
        selected = self._match_device(microphones, self.microphone_device, "microphone")
        if selected is not None:
            return selected
        default_microphone = sc.default_microphone()
        if default_microphone is not None:
            return default_microphone
        if microphones:
            return microphones[0]
        raise RuntimeError("未检测到麦克风设备")

    def _put_latest(self, target: "queue.Queue[bytes]", pcm: bytes) -> None:
        try:
            target.put_nowait(pcm)
        except queue.Full:
            try:
                target.get_nowait()
                self.dropped_chunks += 1
            except queue.Empty:
                pass
            target.put_nowait(pcm)

    def _capture_worker(self, label: str, target: "queue.Queue[bytes]") -> None:
        frames = max(160, int(self.sample_rate * self.chunk_ms / 1000))
        gain = self.system_gain if label == "system" else self.microphone_gain
        retry_delay = 0.25
        silent_chunks = 0
        silence_notice_chunks = max(1, int(5_000 / self.chunk_ms))
        while not self._stop.is_set():
            try:
                device = self._resolve(label)
                self.device_names[label] = device.name
                self._emit("device_ready", f"{label}:{device.name}")
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    with device.recorder(
                        samplerate=self.sample_rate,
                        channels=1,
                        blocksize=frames,
                    ) as recorder:
                        retry_delay = 0.25
                        while not self._stop.is_set():
                            with warnings.catch_warnings():
                                warnings.simplefilter("ignore")
                                samples = recorder.record(numframes=frames)
                            normalized = normalize_float_audio(
                                samples,
                                self.sample_rate,
                                self.sample_rate,
                                gain=gain,
                            )
                            level = float(np.sqrt(np.mean(np.square(normalized)))) if normalized.size else 0.0
                            if level < self.silence_threshold:
                                silent_chunks += 1
                                if silent_chunks == silence_notice_chunks:
                                    self._emit("silence", label)
                            else:
                                silent_chunks = 0
                            if not self._paused.is_set():
                                self._put_latest(target, float_audio_to_pcm16(normalized))
            except PermissionError:
                if self._stop.is_set():
                    break
                self._fatal_error = AppError(
                    AppErrorCode.DEVICE_UNAVAILABLE,
                    f"{label} 音频设备权限不足",
                    retryable=False,
                )
                self._emit("permission_denied", label)
                self._stop.set()
                break
            except Exception as exc:
                if self._stop.is_set():
                    break
                self._emit("device_recovering", f"{label}:{type(exc).__name__}")
                if self._stop.wait(retry_delay):
                    break
                retry_delay = min(4.0, retry_delay * 2)

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                return
            self._stop.clear()
            self._paused.clear()
            self._fatal_error = None
            self._queues = {label: queue.Queue(maxsize=4) for label in self._labels()}
            self._threads = []
            for label, target in self._queues.items():
                thread = threading.Thread(
                    target=self._capture_worker,
                    args=(label, target),
                    name=f"audio-{label}",
                    daemon=False,
                )
                thread.start()
                self._threads.append(thread)
            self._started = True
            self._emit("started", self.mode.value)

    def read(self, timeout: float = 1.0) -> AudioChunk | None:
        with self._lifecycle_lock:
            if not self._started:
                raise RuntimeError("audio source is not started")
            if self._fatal_error is not None:
                raise self._fatal_error
            if self._paused.is_set():
                self._stop.wait(min(timeout, self.chunk_ms / 1000))
                return None
            deadline = time.monotonic() + max(0.0, timeout)
            chunks: list[bytes] = []
            sources: list[str] = []
            for label, source_queue in self._queues.items():
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    pcm = source_queue.get(timeout=remaining)
                except queue.Empty:
                    continue
                chunks.append(pcm)
                sources.append(label)
            if not chunks:
                if self._fatal_error is not None:
                    raise self._fatal_error
                return None
            pcm = chunks[0] if len(chunks) == 1 else mix_pcm16(chunks)
            self._sequence += 1
            return AudioChunk(
                sequence=self._sequence,
                pcm=pcm,
                rms=pcm16_rms(pcm),
                sources=tuple(sources),
            )

    def pause(self) -> None:
        self._paused.set()
        self._emit("paused", self.mode.value)

    def resume(self) -> None:
        self._paused.clear()
        for target in self._queues.values():
            while True:
                try:
                    target.get_nowait()
                except queue.Empty:
                    break
        self._emit("recording", self.mode.value)

    def switch(
        self,
        mode: AudioMode,
        system_device: str | None = None,
        microphone_device: str | None = None,
    ) -> None:
        with self._lifecycle_lock:
            self._emit("switching", mode.value)
            was_started = self._started
            if was_started:
                self.stop()
            self.mode = mode
            if system_device is not None:
                self.system_device = system_device
            if microphone_device is not None:
                self.microphone_device = microphone_device
            if was_started:
                self.start()
            self._emit("switched", mode.value)

    def stop(self) -> None:
        with self._lifecycle_lock:
            if not self._started:
                return
            self._stop.set()
            for thread in self._threads:
                thread.join(timeout=max(2.0, self.chunk_ms / 1000 * 4))
            alive = [thread.name for thread in self._threads if thread.is_alive()]
            self._threads = []
            self._queues = {}
            self._started = False
            self._emit("stop_timeout" if alive else "stopped", ",".join(alive))

    @property
    def active_thread_names(self) -> list[str]:
        return [thread.name for thread in self._threads if thread.is_alive()]


class FixtureAudioSource:
    mode = AudioMode.FIXTURE

    def __init__(
        self,
        path: Path,
        *,
        sample_rate: int = 16000,
        chunk_ms: int = 100,
        realtime: bool = False,
    ) -> None:
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms
        self.realtime = realtime
        self._wave: wave.Wave_read | None = None
        self._paused = False
        self._stopped = True
        self._sequence = 0

    def start(self) -> None:
        self.stop()
        self._wave = wave.open(str(self.path), "rb")
        if self._wave.getsampwidth() != 2:
            self._wave.close()
            self._wave = None
            raise ValueError("fixture must use 16-bit PCM")
        self._paused = False
        self._stopped = False
        self._sequence = 0

    def read(self, timeout: float = 1.0) -> AudioChunk | None:
        if self._wave is None or self._stopped:
            return None
        if self._paused:
            time.sleep(min(timeout, self.chunk_ms / 1000))
            return None
        source_rate = self._wave.getframerate()
        frames = max(1, int(source_rate * self.chunk_ms / 1000))
        raw = self._wave.readframes(frames)
        if not raw:
            return None
        channels = self._wave.getnchannels()
        if channels == 1 and source_rate == self.sample_rate:
            pcm = raw
        else:
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if channels > 1:
                samples = samples.reshape(-1, channels)
            normalized = normalize_float_audio(samples, source_rate, self.sample_rate)
            pcm = float_audio_to_pcm16(normalized)
        self._sequence += 1
        if self.realtime:
            time.sleep(self.chunk_ms / 1000)
        return AudioChunk(
            sequence=self._sequence,
            pcm=pcm,
            rms=pcm16_rms(pcm),
            sources=("fixture",),
        )

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def switch(
        self,
        mode: AudioMode,
        system_device: str | None = None,
        microphone_device: str | None = None,
    ) -> None:
        if mode != AudioMode.FIXTURE:
            raise ValueError("fixture source cannot switch to a hardware mode")
        self.start()

    def stop(self) -> None:
        self._stopped = True
        if self._wave is not None:
            self._wave.close()
            self._wave = None

    @property
    def finished(self) -> bool:
        if self._wave is None:
            return self._stopped
        return self._wave.tell() >= self._wave.getnframes()


def fixture_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
