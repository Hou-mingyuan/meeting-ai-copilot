# meeting-ai-copilot 部署与运维指南

本文档面向**最终用户部署**与**维护者运维**。本工具是 Windows 桌面端实时转写助手，不是多租户 Web 服务；生产使用即在用户 PC 上长期运行。

## 1. 部署形态

| 形态 | 用途 | 说明 |
| --- | --- | --- |
| **Windows 宿主机（推荐）** | 真实会议转写 | 双击 `启动云端实时转写和AI答案.bat`，采集 WASAPI Loopback |
| **Docker smoke** | CI / 依赖自检 | 仅验证配置加载与问题识别逻辑，**不能**采集 Windows 系统声音 |

## 2. 前置条件

- Windows 10/11，Python 3.10+（启动脚本会自动创建 `.venv`）
- 火山引擎账号：实时 ASR Key + Coding Plan AI Key
- 会议软件（如腾讯会议）声音从电脑扬声器/耳机播放

## 3. 首次部署（Windows）

```powershell
git clone https://github.com/Hou-mingyuan/meeting-ai-copilot.git
cd meeting-ai-copilot
copy config.example.json config.json
# 编辑 config.json 填入 cloud_asr_api_key 与 ai_api_key
启动云端实时转写和AI答案.bat
```

或使用环境变量（适合不想在磁盘留 Key 的场景）：

```powershell
$env:VOLC_ASR_API_KEY = "your-asr-key"
$env:VOLCENGINE_CODING_PLAN_API_KEY = "your-ai-key"
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --config config.json
```

详细配置项见 [USAGE.md](USAGE.md)。

## 4. 输出与日志

默认输出目录：`桌面\实时监听\`

| 文件 | 说明 |
| --- | --- |
| `YYYY-MM-DD_实时监听.txt` | 最终 ASR 结果 |
| `YYYY-MM-DD_临时识别.txt` | partial 临时结果 |
| `YYYY-MM-DD_AI参考答案.txt` | AI 流式答案 |
| `YYYY-MM-DD_运行日志.txt` | 运行日志（排障首选） |

跨天自动切换日期文件；断网 ASR 会自动重连。

## 5. Docker 诊断部署

用于验证镜像构建与 smoke 逻辑（Linux/macOS/Windows Docker Desktop 均可）：

```powershell
docker compose up --build --abort-on-container-exit --exit-code-from meeting-ai-copilot
```

预期输出包含 `SMOKE OK:` 三行。容器内**不**运行真实音频采集。

## 6. 升级流程

```powershell
git pull
# 若 requirements.txt 有变更，重新运行启动脚本或：
.venv\Scripts\python.exe -m pip install -r requirements.txt
python -m py_compile src\cloud_runtime.py src\cloud_asr_volcengine.py
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --config config.example.json --smoke-test
```

保留现有 `config.json`；对照 [CHANGELOG.md](CHANGELOG.md) 检查新增配置项。

## 7. 运维 Runbook

### 7.1 启动前检查

```powershell
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --diagnose
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --list-devices
```

### 7.2 常见问题

| 现象 | 处理 |
| --- | --- |
| 无识别结果 | 确认会议声音从电脑播放；`--list-devices` 检查 loopback 设备 |
| ASR 握手失败 | 检查 `cloud_asr_api_key` / 火山控制台用量余额 |
| AI 不触发 | 仅「像问题」的语句会调用 LLM；见 USAGE.md 常见问题 |
| 热词无效 | 词表须与 ASR Key 同应用；配置了 `boosting_table_id` 后内联热词被跳过 |

### 7.3 排障材料

向维护者提供（**脱敏后**）：

- `YYYY-MM-DD_运行日志.txt`
- `config.json`（移除真实 Key）

### 7.4 密钥轮换

1. 在火山控制台轮换 ASR / AI Key。
2. 更新本地 `config.json` 或环境变量。
3. 重启程序；无需重装依赖。

## 8. CI 与发布

GitHub Actions（`.github/workflows/ci.yml`）在每次 push/PR 执行：

- `py_compile` 语法检查
- `--smoke-test`（无密钥）
- `pip audit` 依赖审计（informational）
- Docker Compose smoke

发布前在 Windows 宿主机做一次 `--diagnose` 与真实会议 smoke 验证。

## 9. 相关文档

- [README.md](README.md) — 架构与快速开始
- [USAGE.md](USAGE.md) — 详细使用说明
- [SECURITY.md](SECURITY.md) — 安全策略与漏洞报告
