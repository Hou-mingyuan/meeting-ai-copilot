# meeting-ai-copilot

> **会议实时 ASR + LLM 流式答案助手** — 监听 Windows 系统声音，云端实时转写会议语音，识别到问题后流式生成 AI 参考答案。

<p>
  <img alt="CI" src="https://github.com/Hou-mingyuan/meeting-ai-copilot/actions/workflows/ci.yml/badge.svg">
  <img alt="python" src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white">
  <img alt="platform" src="https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white">
  <img alt="asr" src="https://img.shields.io/badge/ASR-Volcengine%20Streaming-orange">
  <img alt="llm" src="https://img.shields.io/badge/LLM-SSE%20Streaming-blue">
  <img alt="license" src="https://img.shields.io/badge/License-MIT-green">
</p>

---

## 核心能力

| 模块 | 说明 |
| --- | --- |
| **实时 ASR** | 火山引擎大模型流式语音识别（WebSocket 双向流），100ms 音频块低延迟上送，partial + final 分句输出 |
| **LLM 流式答案** | 识别到面试/问答类语句后，通过 HTTP SSE 流式调用大模型，逐 token 写入桌面文件 |
| **系统声音采集** | WASAPI Loopback 自动检测当前有声音的输出设备，默认不采集麦克风 |
| **热词优化** | 支持内联热词或火山控制台词表（`boosting_table_id`），提升专业术语识别率 |
| ** resilient 运行** | 断线自动重连、跨天自动切换日期文件、问题去重与冷却防重复调用 |

## 数据流

```mermaid
flowchart LR
    subgraph Input["输入"]
        Meet[会议软件播放声音]
    end
    subgraph Capture["采集"]
        Loop[WASAPI Loopback]
    end
    subgraph ASR["实时 ASR"]
        WS[火山 WebSocket 流式识别]
    end
    subgraph Output["输出"]
        TXT[桌面 txt 转写文件]
        AI[LLM SSE 流式答案]
    end

    Meet --> Loop --> WS --> TXT
    WS -->|识别到问题| AI
```

## 快速开始

```powershell
git clone https://github.com/Hou-mingyuan/meeting-ai-copilot.git
cd meeting-ai-copilot
copy config.example.json config.json
# 编辑 config.json 填入 cloud_asr_api_key 与 ai_api_key
启动云端实时转写和AI答案.bat
```

详细步骤见 [USAGE.md](USAGE.md)。部署运维见 [DEPLOYMENT.md](DEPLOYMENT.md)，安全策略见 [SECURITY.md](SECURITY.md)。

## 配置

复制 `config.example.json` 为 `config.json`，填入密钥：

```json
{
  "cloud_asr_api_key": "your-volcengine-asr-key",
  "ai_api_key": "your-volcengine-coding-plan-key"
}
```

也支持环境变量：`VOLC_ASR_API_KEY`、`VOLCENGINE_CODING_PLAN_API_KEY`。

## 项目结构

```
meeting-ai-copilot/
├── src/
│   ├── cloud_asr_volcengine.py   # 入口：ASR WebSocket + 调度
│   ├── cloud_runtime.py          # 音频采集、AI SSE、文件输出
│   └── status_tui.py             # 终端状态面板（采集/ASR/AI）
├── config.example.json
├── requirements.txt
├── 启动云端实时转写和AI答案.bat
├── README.md
├── USAGE.md
├── DEPLOYMENT.md
├── SECURITY.md
├── CHANGELOG.md
└── VERSION
```

## 诊断与测试

```powershell
python -m py_compile src\cloud_runtime.py src\cloud_asr_volcengine.py
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --config config.example.json --smoke-test
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --diagnose
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --test-asr-handshake
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --test-ai
```

### Docker Desktop smoke（CI / 依赖验收）

Docker 容器用于验证依赖安装、配置加载、ASR 请求构造和问题识别逻辑；**不**采集 Windows 系统声音（见 [DEPLOYMENT.md §5](DEPLOYMENT.md#5-docker-诊断部署与演示边界) 能力对照表）。Portfolio Hub 将本项目标记为 Windows 原生，Docker smoke 即该维度的可验收基线。

```powershell
docker compose up --build --abort-on-container-exit --exit-code-from meeting-ai-copilot
```

零密钥完整演示链路请用 `.\scripts\demo-mock.ps1`（Mock ASR/AI，非 Docker 内）。

<details>
<summary><strong>Docker vs Windows 宿主机 — 如何选择？</strong></summary>

| 你的目标 | 推荐方式 |
| --- | --- |
| CI / Portfolio 验收依赖与逻辑 | **Docker smoke**（`docker compose up …`） |
| 真实会议听写 + WASAPI 系统声音 | **Windows 宿主机**（`.bat` 或 `python src/cloud_asr_volcengine.py`） |
| 零密钥演示全链路 | **宿主机** + `.\scripts\demo-mock.ps1` |
| BYOK 生产使用 | **Windows 宿主机** |

| 能力 | Docker smoke | Windows 宿主机 |
| --- | :---: | :---: |
| 配置加载 / ASR 请求构造 | ✓ | ✓ |
| 问题识别启发式 | ✓ | ✓ |
| WASAPI 系统声音采集 | ✗ | ✓ |
| 真实火山 ASR / LLM 流式 | ✗（需 BYOK + 宿主机） | ✓ |

> Docker 在本项目中的可验收目标是镜像可构建、依赖可安装、`--smoke-test` 可跑通；**不**采集 Windows 系统声音——这是设计限制，不是部署失败。详见 [DEPLOYMENT.md §5](DEPLOYMENT.md#5-docker-诊断部署与演示边界)。

</details>

### 终端状态面板（TUI）

交互式终端运行时默认显示 **4 行状态面板**（采集 / ASR 连接 / AI / 最近一句），可用 `--no-tui` 关闭：

```powershell
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --config config.json
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --config config.json --no-tui
```

详细日志仍写入桌面 `运行日志.txt`。

## 演示指南

面向作品集评审与首次体验。**本项目不提供内置演示账号**，采用 BYOK（Bring Your Own Key）模式：自备火山引擎 ASR Key 与 Coding Plan AI Key 后，按下列顺序完成配置、验收与核心流程演示。

### 零密钥 Mock 演示（`ai_provider=mock` · 无需火山 Key）

| 步骤 | 命令 / 操作 | 预期 |
| --- | --- | --- |
| ✓ | `python src/cloud_asr_volcengine.py --config config.example.json --smoke-test` | 三行 `SMOKE OK:` |
| ✓ | `python src/cloud_asr_volcengine.py --config config.mock-offline.json --test-ai` | 内置 Mock 流式答案，无需 HTTP |
| ✓ | `.\scripts\demo-mock.ps1` | `MOCK DEMO OK`（ASR+AI 全链路 Mock 服务） |
| ✓ | `docker compose up …` | 容器 smoke，同上三行 OK |

**一键 Mock 闭环（PowerShell）：**

```powershell
.\scripts\demo-mock.ps1
```

或手动两步：

```powershell
python loadtest\mock_server.py --port 8765          # 终端 1
python scripts\demo_mock_loop.py --base-url http://127.0.0.1:8765   # 终端 2
```

预期末尾输出 `MOCK DEMO OK（未调用火山 ASR/LLM API）`。Mock 不采集 Windows 系统声音；真实会议请走 BYOK 路径。

### 演示账号说明

| 项目 | 说明 |
| --- | --- |
| 内置演示账号 | **无** — 不向仓库写入任何共享 Key |
| 体验方式 | 复制 `config.example.json` → 填入自有密钥，或设置环境变量 |
| 无 Key 时可验收 | `--smoke-test`、`--test-ai`（`config.mock-offline.json`）、`scripts/demo-mock.ps1`、Docker smoke |

### BYOK 配置

1. 复制 `config.example.json` 为 `config.json`
2. 填入火山引擎凭证（Bring Your Own Key）：

| 字段 | 环境变量（可选） | 说明 |
| --- | --- | --- |
| `cloud_asr_api_key` | `VOLC_ASR_API_KEY` | 实时语音识别 API Key |
| `ai_api_key` | `VOLCENGINE_CODING_PLAN_API_KEY` | LLM 参考答案 API Key |
| `cloud_asr_boosting_table_id` | — | 可选，控制台热词表 ID |

> 切勿将含真实 Key 的 `config.json` 提交到 Git。详见 [SECURITY.md](SECURITY.md)。

### smoke-test 与 diagnose

**无密钥验收**（CI / 首次克隆后）：

```powershell
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --config config.example.json --smoke-test
```

预期输出包含三行 `SMOKE OK:` 并以 exit code 0 退出：

```text
SMOKE OK: config loaded
SMOKE OK: ASR start request built
SMOKE OK: AI question heuristic passed
```

**填入 BYOK 后**（Windows 宿主机，正式开会前）：

```powershell
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --config config.json --diagnose
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --test-asr-handshake
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --test-ai
```

| 命令 | 用途 | 通过标准 |
| --- | --- | --- |
| `--smoke-test` | 配置加载、ASR 请求构造、问题识别逻辑 | 三行 `SMOKE OK:`，exit 0 |
| `--diagnose` | 环境、依赖包、音频 loopback 设备、Key 是否已配置 | `api_key: 已配置`，依赖包均为 `OK` |
| `--test-asr-handshake` | ASR WebSocket 握手 | 无报错退出 |
| `--test-ai` | LLM SSE 通道 | 流式返回 token |

### Docker smoke

在无 Windows 音频环境或 CI 中，用容器验证依赖安装、配置加载与问题识别逻辑：

```powershell
docker compose up --build --abort-on-container-exit --exit-code-from meeting-ai-copilot
```

预期容器日志同样输出三行 `SMOKE OK:` 并以 code 0 退出。

> 容器不采集系统声音；完整演示仍需在 Windows 宿主机运行 `启动云端实时转写和AI答案.bat`。

### 核心流程：会议 → 转写 → AI

1. **启动**：运行 `启动云端实时转写和AI答案.bat`（自动创建 `.venv` 并安装依赖）
2. **会议**：打开腾讯会议等，确保声音从扬声器/耳机播放（默认不采集麦克风）
3. **转写**：WASAPI Loopback 采集 → 火山 WebSocket 流式 ASR → 写入桌面 `实时监听\YYYY-MM-DD_实时监听.txt`
4. **AI 答案**：识别到面试/问答语句后，SSE 流式调用大模型 → 写入 `YYYY-MM-DD_AI参考答案.txt`

```mermaid
sequenceDiagram
    participant Meet as 会议软件
    participant App as meeting-ai-copilot
    participant ASR as 火山 ASR
    participant LLM as 大模型 SSE

    Meet->>App: 系统声音 (WASAPI Loopback)
    App->>ASR: 100ms 音频块 WebSocket
    ASR-->>App: partial / final 转写
    App->>App: 写入桌面 txt
    App->>LLM: 识别到问题 → SSE 流式请求
    LLM-->>App: token 流
    App->>App: 写入 AI参考答案.txt
```

更多细节见 [USAGE.md](USAGE.md)。

### Mock 演示录屏（pending-local）

Windows 原生 GUI 演示需本机 OBS / Xbox Game Bar 录制约 **60s** Mock 流程（配置 smoke + 桌面输出文件滚动）。占位路径：`_optimization-screenshots/meeting-ai/demo-mock-60s.mp4`（待手动录制）。

Locust 50 VU 本地热路径（无需密钥）：

```powershell
pip install locust
locust -f loadtest\locustfile.py --headless -u 50 -r 10 -t 30s --only-summary
```

实测见 [PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md) §6。

## 版本

当前版本：**1.0.0**（见 [VERSION](VERSION) 与 [CHANGELOG.md](CHANGELOG.md)）

## 许可证

[MIT License](LICENSE)
