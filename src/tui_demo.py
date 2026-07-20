from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from audio_pipeline import fixture_sha256
from offline_demo import load_fixture_metadata
from status_tui import StatusTui


def run_mock_tui_demo(
    fixture: Path,
    duration_seconds: float = 60.0,
    *,
    tui: StatusTui | None = None,
) -> dict[str, Any]:
    fixture = Path(fixture).resolve()
    metadata = load_fixture_metadata(fixture)
    if fixture_sha256(fixture) != metadata["wav_sha256"]:
        raise RuntimeError("Mock TUI fixture hash mismatch")
    duration = max(1.0, float(duration_seconds))
    tui = tui or StatusTui(enabled=True, force=True)
    tui.set_privacy_confirmed(True)
    tui.set_mode("固定音频 Mock（不采集真实设备）")
    tui.set_devices(f"{fixture.name} · SHA256 {metadata['wav_sha256'][:12]}…")
    tui.set_capture("准备中")
    tui.set_asr("Mock 待命")
    tui.set_ai("Mock 待命")
    tui.set_answer("（等待问题触发）")
    tui.set_auto_answer(True)
    tui.set_notice("本演示不打开 Windows 音频设备，不调用真实 ASR/AI")
    tui.start()
    started = time.monotonic()
    final_text = str(metadata["expected_final"])
    stopped_by_user = False
    try:
        while True:
            elapsed = time.monotonic() - started
            progress = min(1.0, elapsed / duration)
            command = tui.get_command()
            if command == "stop":
                stopped_by_user = True
                break
            if progress < 0.10:
                tui.set_capture("读取 fixture")
                tui.set_asr("Mock 已连接")
            elif progress < 0.24:
                tui.set_capture("采集中")
                tui.set_last_line(final_text[: max(4, int(len(final_text) * progress / 0.24))])
                tui.set_notice("固定 WAV 正在按 100ms 音频块送入 Mock ASR")
            elif progress < 0.30:
                tui.set_capture("已暂停")
                tui.set_notice("暂停：音频仍保持低延迟，不向 ASR 发送新块")
            elif progress < 0.42:
                tui.set_capture("采集中")
                tui.set_asr("Mock 重连 1/6")
                tui.set_last_line(final_text)
                tui.set_notice("模拟网络断线：保留已确认文本并重放未确认音频")
            elif progress < 0.55:
                tui.set_asr("Mock 已恢复")
                tui.set_ai("参考答案流式生成中")
                tui.set_answer("【Mock 参考】Redis 缓存减少热点读取延迟；")
                tui.set_notice("检测到问题；上下文受 4000 字上限约束")
            elif progress < 0.64:
                tui.set_ai("已取消")
                tui.set_answer("【Mock 参考】Redis 缓存减少热点读取延迟；（已取消）")
                tui.set_notice("用户取消 AI；会议转写继续运行")
            elif progress < 0.82:
                tui.set_ai("重试流式生成中")
                tui.set_answer("【Mock 参考】Redis 缓存减少热点读取延迟；MySQL 索引缩小查询扫描范围。")
                tui.set_notice("答案标记为 AI 参考，不会混入会议原话")
            elif progress < 0.92:
                tui.set_ai("参考答案已完成")
                tui.set_answer("【Mock 参考】Redis 缓存减少热点读取延迟；MySQL 索引缩小查询扫描范围。两者可配合使用。")
                tui.set_notice("会话已保存为 JSON，并导出 Markdown / TXT")
            else:
                tui.set_capture("已停止")
                tui.set_asr("已关闭")
                tui.set_ai("已完成")
                tui.set_notice("Mock 演示完成 · 线程 0 残留 · 按 Q 可提前退出")
            if elapsed >= duration:
                break
            time.sleep(0.08)
    finally:
        tui.stop()
    print("MOCK TUI DEMO COMPLETED" if not stopped_by_user else "MOCK TUI DEMO STOPPED BY USER")
    return {
        "status": "stopped" if stopped_by_user else "completed",
        "fixture_sha256": metadata["wav_sha256"],
        "duration_seconds": round(time.monotonic() - started, 2),
        "residual_threads": tui.active_thread_names,
    }
