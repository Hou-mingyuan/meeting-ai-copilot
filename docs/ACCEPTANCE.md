# v1.1.0-rc.1 验收记录

> 日期：2026-07-20。以下只记录本机实际执行或可复现命令；Docker、Windows 设备、Mock、便携包与 BYOK 边界分别陈述。

## 完成状态

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| 系统声音设备枚举与真实 loopback | 通过 | 最终包内 EXE；稳定端点 ID；Realtek fixture 主频 330.00Hz、峰值 RMS 0.485461 |
| 麦克风打开与 PCM 读取 | 通过 | 最终包内 EXE；Realtek 麦克风取得 5 块；测试时静音，不宣称语音质量 |
| 混合输入 | 通过 | 最终包内 EXE；41 块，峰值 RMS 0.242550，块来源同时含 system/microphone |
| 暂停/恢复、设备热切换、停止 | 通过 | Realtek -> ToDesk -> Realtek；残留音频线程 0 |
| ASR partial/final、乱序/重复、有限重连 | 通过 | 单元测试 + 固定 fixture 1 次断线恢复 |
| 问题检测自动/手动、阈值/冷却/去重 | 通过 | 自动化测试与离线流程 |
| AI SSE、取消、重试、边界与会话隔离 | 通过（Mock/协议） | 固定 Mock 取消通过，首次失败后 2 次调用完成；真实 BYOK 未测 |
| 会话 JSON、跨天时间、TXT/Markdown | 通过 | 原子保存、来源/时间戳、AI 参考标签、保留策略测试 |
| TUI 样式与交互 | 通过 | 40/60/100/140 列固定 10 行尺寸测试；转写/答案分区；实际截图与 60 秒视频 |
| 性能 | 通过本地预算 | 最终统一复跑：health 27.88ms、ASR 82.33ms、AI TTFB 141.44ms，均为 p95，0/20 错误 |
| Docker smoke | 通过当前代码依赖/逻辑 smoke | 当前工作树只读挂载到既有同依赖镜像；编译及固定 fixture ASR/AI/保存/导出闭环通过；不代表采音 |
| 便携包与一键启动 | 通过 | clean `USERPROFILE`、带空格路径、无 Python、EXE/TUI/真实音频/一键 Mock smoke |
| Secret / 端口 / Git whitespace | 通过 | `repo_checks.py`、`git diff --check` 与统一验收脚本通过 |

## 固定 fixture

```text
WAV SHA-256: 1ab7096bc74878e3392f91a724947263fa52b15e9edfcf113216b698c365e0d7
PCM SHA-256: 11654807346dfdd5eb0eeb9d2fefee00f6fa34c952b13a8528f69976c3c28c3c
期望 final : 请解释一下 Redis 缓存和 MySQL 索引分别解决什么问题？
```

fixture 是合成测试音，不是真实语音。Mock ASR 按哈希映射确定性文本；它验证音频分块、协议、状态和业务闭环，不验证语音模型准确率。

## Windows 主机命令

```powershell
python src\cloud_asr_volcengine.py --windows-audio-acceptance
```

实际设备：

- 系统：`system:a4f61c404991`，扬声器 (Realtek(R) Audio)，默认。
- 麦克风：`microphone:255f4690cdcd`，麦克风 (Realtek(R) Audio)，默认。
- 热切换目标：扬声器 (ToDesk Virtual Audio)。

所有检查为真：枚举、播放停止、loopback 信号、暂停/恢复、热切换、麦克风读取、混合来源、残留线程 0。

## Mock 与性能命令

```powershell
python src\cloud_asr_volcengine.py --mock-demo
python loadtest\mock_server.py --port 19060
python scripts\demo_mock_loop.py --base-url http://127.0.0.1:19060
python loadtest\dry_run.py --base-url http://127.0.0.1:19060 --iterations 20 --concurrency 4
```

HTTP 全链路验证 ASR 与 AI 各 1 次断线恢复。服务端拒绝范围外端口；所有服务只使用 `19060-19069`。

## 便携包

构建与测试：

```powershell
powershell -File scripts\build-portable.ps1
powershell -File scripts\test-portable.ps1 -ZipPath dist\meeting-ai-copilot-1.1.0-rc.1-win-x64.zip -IncludeWindowsAudio
```

clean-profile smoke 明确输出 `PORTABLE SMOKE PASSED`：版本、EXE smoke、固定 fixture、TUI、包内真实 Windows 音频、会话文件和一键 Mock 脚本均通过。`python_required=false`，路径包含空格。最终 ZIP 为 `meeting-ai-copilot-1.1.0-rc.1-win-x64.zip`，SHA-256 为 `269989b44af012c3d1fafb215a5cf73b720abc5f55b4083f1ad16fb0079f113d`；`dist` 不提交。

该 smoke 使用临时独立 `USERPROFILE`，不是新建 Windows OS 账号；它证明程序不依赖现有用户 Python/应用缓存。当前没有代码签名证书，EXE 未签名。

## 截图与录屏

- `docs/images/tui-mock.png`：从实际 Windows 控制台录屏中提取。
- `docs/media/mock-demo-60s.mp4`：实际 TUI 运行 60 秒，固定 fixture Mock，窗口内明确显示不采集真实设备/不调用真实服务。

## 外部 BYOK 项

当前环境没有用户提供的火山 ASR/AI Key，因此以下未验证，不标记通过：

- 真实 ASR 鉴权、partial/final 延迟、供应商准确率、时长包与 429 配额。
- 真实 AI 模型 TTFB、答案质量、内容策略与供应商限流。
- 两小时真实会议 soak 与外部网络长期稳定性。
- Windows 代码签名、SmartScreen 信誉和公开 Release 安装体验。

代码中的请求构造、错误分类、重试边界和 Mock 契约有测试；它们不能替代上述账号/模型验收。

## Docker 外部拉取记录

2026-07-20 最终复跑时，当前工作树以只读方式挂载到既有同版运行时依赖镜像，`compileall` 与固定 fixture Mock ASR/AI、取消/重试、保存/导出闭环均退出 0。镜像内 `soundcard 0.4.3`、`numpy 2.1.3`、`websockets 15.0.1`、`volcengine-audio 0.2.4` 与 `requirements.txt` 完全一致，`docker compose config --quiet` 通过。`docker compose build --pull` 在读取 `python:3.12-slim` 元数据时由 Docker Hub 返回 EOF，因此未把 fresh registry build 标为通过；该失败发生在读取 Dockerfile 基础镜像元数据之前，不是仓库构建步骤或应用代码失败。Docker 证据始终只用于 Linux 依赖与逻辑，不用于 Windows 采音结论。

## 已知低风险限制

- TUI 不是图形化托盘 GUI；v1 选择可打包、键盘可控的终端界面。
- 本地会话未内置加密，依赖 Windows 用户权限及磁盘加密。
- 供应商协议没有逐音频块确认；超过 8 秒默认重放窗口的断线可能丢失未确认音频，已确认文本不会删除。
- clean-profile 不是独立 OS 用户或 Windows Sandbox。
