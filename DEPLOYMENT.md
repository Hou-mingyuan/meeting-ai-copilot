# 部署、打包与故障排查

## 支持环境

| 项目 | 要求 |
| --- | --- |
| 生产运行 | Windows 10/11 x64，真实 WASAPI 音频设备 |
| 源码运行 | Python 3.10-3.12，建议 3.12；约 500MB 可用磁盘 |
| 便携包 | Windows 10/11 x64；不要求系统 Python；约 150MB 解压空间 |
| Docker smoke | Docker Desktop / Linux Docker；不支持 Windows 真实采音 |
| 网络 | BYOK 模式需要访问配置的 ASR WebSocket 与 AI HTTPS/SSE 地址 |

本项目是单用户 Windows 工具，不是 Web 服务，不需要数据库、账号或浏览器入口。

## 推荐启动路径

源码仓库双击：

```text
启动云端实时转写和AI答案.bat
```

脚本显示可见菜单，不静默启动。首次源码运行会创建 `.venv`；缺少 Python 时先征求确认，再决定是否通过 winget 安装。便携包会优先使用包内 EXE。

零密钥默认路径：选择 Mock 演示。真实会议路径：创建 `config.json`、配置 BYOK、确认隐私后开始。

## 配置与密钥

环境变量优先于 `config.json`：

```powershell
$env:VOLC_ASR_API_KEY = "..."
$env:VOLCENGINE_CODING_PLAN_API_KEY = "..."
```

应用不会把 Key 写入日志或会话。`config.json` 只适合个人本机，并已被 Git 忽略。

## 本地 Mock 与端口

离线 Mock 不启动服务：

```powershell
python src\cloud_asr_volcengine.py --mock-demo
```

HTTP 协议与性能测试使用：

```powershell
python loadtest\mock_server.py --port 19060
python scripts\demo_mock_loop.py --base-url http://127.0.0.1:19060
python loadtest\dry_run.py --base-url http://127.0.0.1:19060
```

Mock 服务只绑定 loopback，并强制使用 `19060-19069`。端口范围外启动失败。服务请求校验固定 WAV/PCM 哈希，不接受调用者传入任意“期望文本”冒充 ASR。

## Windows 主机音频验收

```powershell
python src\cloud_asr_volcengine.py --windows-audio-acceptance
```

命令会：

1. 枚举 loopback 与麦克风。
2. 播放固定 WAV，用真实 WASAPI loopback 捕获并检查 330/440Hz 测试音。
3. 实际打开麦克风和混合输入。
4. 验证暂停/恢复、两个输出设备之间热切换、显式停止和残留线程。

该命令不连接 Docker，不调用云端 ASR/AI。远程桌面、无声卡或禁用麦克风权限的主机会如实失败。

## Docker smoke

```powershell
docker compose up --build --abort-on-container-exit --exit-code-from meeting-ai-copilot
```

Docker 只证明：镜像构建、依赖安装、配置加载、火山 ASR 请求构造、问题检测。Linux 容器没有 Windows WASAPI，因此不能替代上一节的主机证据。

## 便携包构建

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt -r requirements-build.txt
powershell -File scripts\build-portable.ps1
```

输出：`dist\meeting-ai-copilot-<version>-win-x64.zip`。构建脚本先运行测试，再使用 PyInstaller one-folder 模式，复制配置、文档和固定 fixture，最后用 Windows `tar.exe -a` 生成 ZIP 与 SHA-256。

clean-profile smoke：

```powershell
powershell -File scripts\test-portable.ps1 -ZipPath dist\meeting-ai-copilot-1.1.0-rc.1-win-x64.zip
```

测试会解压到带空格的临时路径，设置独立 `USERPROFILE`，直接运行 EXE 版本、smoke、Mock 保存/导出和一键启动脚本。它不使用系统 Python执行应用。

当前产物未签名。没有代码签名证书时，不声明 Windows SmartScreen 信誉或签名通过。

## 一键验收

```powershell
.\一键验收.bat --no-pause
```

默认执行编译、Ruff、pytest、离线 fixture、HTTP Mock、性能、Windows 真实音频、Docker、便携包、secret/端口扫描和 `git diff --check`。维护者可在明确知道验收边界时对单项使用 `scripts\verify-all.ps1` 的 `-SkipWindowsAudio`、`-SkipDocker`、`-SkipPackage`。

## 更新

保留本机 `config.json` 后替换源码或便携目录。先比较 `config.example.json` 新字段，再运行：

```powershell
python -m pytest tests -q
python src\cloud_asr_volcengine.py --smoke-test --config config.example.json
```

结构化会话 schema 当前为版本 1；更新不会迁移或删除已有会议数据。

## 卸载

便携版不注册服务、注册表启动项或计划任务。按 `Q` 退出后删除解压目录即可。默认会议数据在桌面 `实时监听`，是否删除由用户单独决定；应用不会因卸载自动删除敏感记录。

## 故障排查

| 现象 | 建议 |
| --- | --- |
| `api_key 未配置` | 设置环境变量或本机 `config.json`；Mock 演示无需 Key |
| 设备列表为空 | 检查 Windows 音频服务、驱动和麦克风隐私权限 |
| loopback 有设备但无信号 | 核对会议软件正在使用的输出设备；用 TUI `1` 切换 |
| 混合只收到一路 | 检查另一设备是否断开；状态会显示恢复中，已有一路继续工作 |
| ASR 鉴权失败 | 校验 Key、resource ID 与账号权限；该错误不会自动无限重连 |
| ASR 重连达到上限 | 检查网络后重新启动；已确认文本仍在 JSON/TXT 中 |
| AI SSE 断开 | 自动有限重试；仍失败时按 `R`，或 `C` 取消后继续转写 |
| 便携 EXE 找不到资源 | 重新运行 build + clean-profile smoke，不要只复制单个 EXE |

运行日志只保留状态和计数。排障材料发送前仍应检查并脱敏；不要发送真实 `config.json`。
