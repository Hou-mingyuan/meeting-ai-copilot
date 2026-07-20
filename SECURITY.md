# Security and Privacy Policy

## 支持版本

| 版本 | 状态 |
| --- | --- |
| `1.1.x` release candidate | 支持 |
| `1.0.x` | 仅关键安全修复 |

## 采集透明性

- 程序不后台静默启动，不注册自启动服务。
- 真实模式打开设备前显示采集内容、ASR/AI 地址和本地目录，交互模式必须输入 `Y`。
- 用户应遵守会议参与者知情与适用法律、组织政策；工具不提供绕过提示或隐藏录音能力。
- TUI 始终显示输入模式、设备、录制/暂停、ASR、AI 与隐私确认状态。

## 数据流

| 数据 | 去向 |
| --- | --- |
| PCM 音频 | 配置的 ASR WebSocket；Mock 模式不上传 |
| 当前问题与同会话受限上下文 | 配置的 AI HTTPS/SSE；AI 关闭时不发送 |
| 转写、答案、状态 | 用户本地输出目录 |
| API Key | 优先从环境变量读取；只用于请求头，不写会话与日志 |

不同会话使用独立 `session_id` 和上下文容器。AI 输出标记为参考答案，不进入会议原话。

## 密钥

优先环境变量：

- `VOLC_ASR_API_KEY`
- `VOLCENGINE_CODING_PLAN_API_KEY`

本机 `config.json` 为兼容方案，已被 Git 忽略。仓库的 JSON 示例只含空值。日志脱敏会移除 Bearer token、Key 字段和长 token 形态；自动 secret scan 检查受版本控制及未忽略文本文件。

当前没有 Windows Credential Manager 集成。需要企业级集中凭证治理时，应使用受控环境变量注入或外部启动器，不要把密钥放进便携 ZIP。

## 本地会议数据

默认保留 30 天，只自动清理应用创建的 `session-*` 文件。兼容版日期文件不会自动删除。卸载不会删除会议记录，避免无确认的数据破坏。

本地 JSON/TXT/Markdown 未加密；依赖 Windows 用户目录权限和磁盘加密。需要更高保护时，把 `output_directory` 指向 BitLocker/EFS 或组织批准的加密目录。

运行日志记录状态、错误代码、长度、计数和短摘要哈希，不记录完整转写正文、完整问题或答案。

## 网络与 Mock

- 真实端点使用配置的 `wss://` / `https://`，代码不关闭 TLS 验证。
- ASR/AI 鉴权失败返回可诊断错误，不静默回退为 Mock 成功。
- HTTP Mock 只绑定 loopback、限制请求大小，并只允许端口 `19060-19069`。
- Mock 响应来自固定 fixture 与确定性答案，界面和文档明确标识。

## 依赖与发布

CI 运行 Ruff、pytest、`pip-audit`、secret/端口扫描和 Docker smoke。PyInstaller 便携包在 Windows runner 构建并做 clean-profile smoke。

当前 EXE 没有代码签名证书，**未签名**。仓库脚本只本地构建，不自动发布。

## 报告漏洞

优先使用 GitHub Private Vulnerability Reporting / Security Advisory。不要在公开 Issue 中附带 Key、真实会议内容、未脱敏日志或可利用细节。

报告应包含版本、复现步骤、影响、最小脱敏日志和环境信息。
