# meeting-ai-copilot 使用指南

## 快速开始

### 1. 准备配置

```powershell
cd MeetingLiveTranscriber
copy config.example.json config.json
```

编辑 `config.json`，填入你的火山引擎凭证：

| 字段 | 说明 |
| --- | --- |
| `cloud_asr_api_key` | 实时语音识别 API Key（或设置环境变量 `VOLC_ASR_API_KEY`） |
| `cloud_asr_boosting_table_id` | 可选，火山控制台热词表 ID |
| `ai_api_key` | AI 参考答案 API Key（或设置 `VOLCENGINE_CODING_PLAN_API_KEY`） |

> **切勿**将含真实 Key 的 `config.json` 提交到 Git。

### 2. 启动

双击或在终端运行：

```text
启动云端实时转写和AI答案.bat
```

脚本会自动：检查 Python → 创建 `.venv` → 安装依赖 → 启动实时转写 + AI 流式答案。

### 3. 使用场景

打开腾讯会议（或其它会议软件），确保声音从电脑扬声器/耳机播放。程序默认**只监听系统播放声音**，不采集麦克风。

## 输出文件

默认写入 `桌面\实时监听\`，文件名带当天日期：

| 文件 | 内容 |
| --- | --- |
| `YYYY-MM-DD_实时监听.txt` | 最终 ASR 识别结果 |
| `YYYY-MM-DD_临时识别.txt` | 正在变化的 partial 结果（覆盖更新） |
| `YYYY-MM-DD_AI参考答案.txt` | 识别到问题后流式写入的 AI 答案 |
| `YYYY-MM-DD_运行日志.txt` | 运行日志 |

## 命令行

```powershell
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --config config.json
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --diagnose
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --list-devices
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --test-asr-handshake
.venv\Scripts\python.exe src\cloud_asr_volcengine.py --test-ai
```

## 常见问题

- **没有识别到声音**：确认会议声音从电脑播放；运行 `--list-devices` 查看 loopback 设备
- **热词不生效**：词表须与 ASR Key 在同一应用下；配置了 `boosting_table_id` 后内联热词会被跳过
- **AI 不触发**：只有识别到「像问题」的语句才会调用 LLM（含问号或「怎么/如何/解释」等关键词）

更多架构说明见 [README.md](README.md)。
