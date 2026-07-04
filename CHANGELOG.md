# Changelog

All notable changes to **meeting-ai-copilot** are documented in this file.

## [1.0.0] - 2026-07-04

### Added

- 首次开源发布，品牌名 **meeting-ai-copilot**
- Windows 系统声音（WASAPI Loopback）→ 火山引擎实时 ASR WebSocket 流式转写
- 识别到面试问题后，通过 HTTP SSE 流式生成 AI 参考答案并写入桌面文件
- 支持 partial / final 分句识别、断线自动重连、跨天自动切换输出文件
- 热词内联与火山控制台词表（`boosting_table_id`）两种模式
- 一键启动脚本、诊断模式、ASR 握手测试、AI 接口测试
- `config.example.json` 模板与 `USAGE.md` 使用文档

### Security

- 移除内置 API Key；密钥仅通过本地 `config.json` 或环境变量注入
