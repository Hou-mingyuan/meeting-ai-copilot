# Changelog

## [1.1.0-rc.1] - 2026-07-19

### Added

- 稳定的 AudioSource、ASR、LLM、QuestionDetector、SessionStore 契约。
- 系统声音、麦克风、混合输入，稳定设备 ID、运行时热切换、静音检测、背压与设备恢复。
- 有界 ASR 重放、最大重连次数、心跳、鉴权/限流/网络错误分类、partial/final 乱序与重复处理。
- AI 上下文边界、SSE 完成检测、取消、自动重试、手动编辑问题与重试。
- 结构化 JSON 会话、30 天保留策略、Markdown/TXT 导出和跨天时间戳。
- 10 行交互 TUI：实时转写/参考答案分区、暂停、模式/设备切换、自动回答、取消、重试、导出、停止。
- 固定 WAV fixture、确定性 Mock ASR/AI、断线恢复测试和 HTTP Mock 端口门禁 `19060-19069`。
- Windows WASAPI 主机验收、PyInstaller 便携包、clean-profile smoke 与一键验收。
- Ruff、pip-audit、secret/端口扫描、Windows 便携包 CI job。
- 真实 TUI 截图与 60 秒固定音频 Mock 录屏工作流。

### Changed

- 真实采集前必须明确确认采集范围与数据去向。
- 环境变量优先于磁盘 Key。
- 日志不再写完整 partial/final/问题正文，只保留状态、长度、计数和摘要哈希。
- 版本提升为 `1.1.0-rc.1`；文档按 Windows 与 Docker 证据边界重写。

### Known external checks

- 真实火山 ASR/AI 需要用户 BYOK，当前未验证模型质量与供应商延迟。
- 便携 EXE 未签名，未发布。

## [1.0.0] - 2026-07-04

- 首次开源版本：Windows WASAPI loopback、火山流式 ASR、LLM SSE、日期 TXT 文件和基础 Mock/smoke。
