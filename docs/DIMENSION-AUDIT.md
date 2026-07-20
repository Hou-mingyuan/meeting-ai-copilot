# 能力审计矩阵

> 2026-07-20 按代码、测试和本机运行重新核验。状态含义：`stable` 已接入主流程且有测试；`mock-only` 仅用于确定性替身；`external` 需要 BYOK/证书；`not-in-v1` 明确不承诺。

| 能力 | 状态 | 证据/边界 |
| --- | --- | --- |
| Windows 系统声音 | stable | Realtek loopback 固定 WAV 频谱验收 |
| Windows 麦克风 | stable | 实际设备打开与 PCM 块读取 |
| 混合输入 | stable | 双线程输入、混合来源与实机验收 |
| 设备选择/热切换/消失恢复 | stable | TUI 命令、实机热切换、Fake 设备消失回归 |
| 火山流式 ASR provider | stable-contract / external-account | 请求、解析、心跳、重连、错误分类有测试；真实账号未验 |
| 固定音频 Mock ASR | mock-only | WAV/PCM 哈希锁定，partial/final 与断线恢复 |
| OpenAI-compatible/Responses SSE | stable-contract / external-account | 完成事件、提前 EOF、取消、重试、429/鉴权边界；真实模型未验 |
| 确定性 Mock AI | mock-only | 固定答案、取消、首次失败后重试 |
| 自动与手动问题检测 | stable | 阈值、冷却、去重、编辑、关闭自动回答 |
| 结构化会话与导出 | stable | JSON schema 1、原子写、Markdown/TXT、保留策略 |
| Windows TUI | stable | 固定 10 行、转写/参考答案分区、宽字符尺寸、快捷键、截图/录屏 |
| GUI/系统托盘 | not-in-v1 | v1 明确采用 TUI，不保留假按钮 |
| Docker | smoke-only | Linux 依赖与逻辑，不能采集 WASAPI |
| Windows 便携包 | stable-local | PyInstaller + clean-profile smoke；未签名未发布 |
| 真实模型质量/供应商延迟 | external | 需要用户 BYOK，不从 Mock 推导 |
| 代码签名 | external | 缺少证书，明确未签名 |

详细命令、数字和已知限制见 [ACCEPTANCE.md](ACCEPTANCE.md)。
