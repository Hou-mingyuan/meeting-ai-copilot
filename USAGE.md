# 使用指南

## 1. 零密钥演示

双击 `一键Mock演示.bat`，或运行：

```powershell
python src\cloud_asr_volcengine.py --mock-demo
```

界面与输出会明确显示“固定音频 / Mock”。该模式不打开 Windows 音频设备，不调用火山 ASR/AI，不代表真实模型效果。

## 2. BYOK 配置

```powershell
copy config.example.json config.json
```

推荐用环境变量，环境变量优先于磁盘配置：

```powershell
$env:VOLC_ASR_API_KEY = "你的 ASR Key"
$env:VOLCENGINE_CODING_PLAN_API_KEY = "你的 AI Key"
```

也可在仅本机可访问的 `config.json` 中填写 `cloud_asr_api_key`、`ai_api_key`。该文件已被 `.gitignore` 排除。

## 3. 选择输入

列出稳定设备 ID：

```powershell
python src\cloud_asr_volcengine.py --list-devices
```

配置示例：

```json
{
  "audio_mode": "mixed",
  "system_audio_device": "system:a4f61c404991",
  "microphone_audio_device": "microphone:255f4690cdcd",
  "system_audio_gain": 1.0,
  "microphone_audio_gain": 1.0
}
```

上面的 ID 只是本机格式示例；请复制你自己的 `--list-devices` 输出。ID 由 Windows 音频端点标识派生，不依赖列表顺序；旧版 `system:0` 这类数字索引仍兼容，但不建议写入长期配置。

`audio_mode` 可取：

| 值 | 采集内容 |
| --- | --- |
| `system` | 会议软件播放到扬声器/耳机的声音 |
| `microphone` | 本机麦克风 |
| `mixed` | 系统声音与麦克风混合 |

运行中按 `1`、`2`、`3` 热切换输入模式，按 `D` 输入新的设备 ID 或名称。设备断开后会自动重新枚举并重开；状态显示“设备恢复中”。

## 4. 启动与隐私确认

双击 `启动云端实时转写和AI答案.bat`，选择真实会议；或运行：

```powershell
python src\cloud_asr_volcengine.py --config config.json
```

打开设备前，程序显示：采集内容、ASR 地址、AI 地址、本地保存目录。交互模式必须输入 `Y`；自动化脚本只有在已经明确知情时才能传 `--accept-privacy`。

## 5. TUI 操作

| 按键 | 行为 |
| --- | --- |
| `Space` | 暂停 / 恢复音频上送 |
| `A` | 把最近一句作为手动问题 |
| `E` | 编辑最近一句后提交给 AI |
| `C` | 取消当前 AI 流 |
| `R` | 重试上一个问题 |
| `T` | 开关自动回答 |
| `X` | 立即导出 Markdown 与 TXT |
| `1` / `2` / `3` | 系统声音 / 麦克风 / 混合 |
| `D` | 运行中选择系统声音和/或麦克风设备 |
| `Q` | 停止、保存、导出并退出 |

TUI 将 AI 流式正文显示在“AI参考答案”独立行，状态和正文都明确标为参考；AI 文本不会加入“会议原话”转写列表。

## 6. 问题检测与上下文

- 自动检测由 `ai_question_threshold`、`ai_min_question_chars` 控制。
- `ai_cooldown_seconds` 防止连续触发，`ai_duplicate_window_seconds` 防止 partial/final 重复触发。
- `ai_auto_answer_enabled=false` 可完全关闭自动回答，手动 `A`/`E` 仍可用。
- `ai_context_max_chars` 限制发给 AI 的同会话上下文；不同 `session_id` 不共享上下文。
- `ai_max_answer_chars`、`ai_timeout_seconds`、`ai_stream_idle_timeout_seconds` 限制答案边界。

## 7. 保存、导出与删除

默认目录：`桌面\实时监听`。每次运行独立保存 `session-<id>.json`，停止时生成 `.md` 与 `.txt`。

默认保留 30 天。程序只删除超过保留期的 `session-*` 文件；兼容版日期文件和用户其它文件不会被自动删除。立即删除时，退出程序后在资源管理器中删除对应会话文件。

## 8. 诊断与验收

```powershell
python src\cloud_asr_volcengine.py --diagnose --config config.example.json
python src\cloud_asr_volcengine.py --windows-audio-acceptance
python src\cloud_asr_volcengine.py --test-asr-handshake --config config.json
python src\cloud_asr_volcengine.py --test-ai --config config.json
```

`--windows-audio-acceptance` 会短暂播放固定测试音，用真实 WASAPI loopback 捕获并校验频率，同时打开麦克风和混合模式；不会发送到云端。

真实 `--test-asr-handshake` 和 `--test-ai` 需要 BYOK，可能产生供应商用量。

## 9. 常见故障

| 状态 | 判断与处理 |
| --- | --- |
| 没有设备 | 运行 `--list-devices`；确认 Windows 隐私设置允许桌面应用访问麦克风 |
| 持续静音 | 确认会议软件输出设备与选定 loopback 对应，或在 TUI 切换输入 |
| ASR 鉴权失败 | 检查 Key、资源 ID、账号权限；鉴权失败不会无限重试 |
| ASR 重连停止 | 连续失败达到上限；检查网络后重新启动，不会删除已确认文本 |
| AI 429 | 等待供应商限流窗口，或按 `R` 重试 |
| AI 已取消 | 转写继续；按 `R` 重试上一问题 |
| TUI 过窄 | 建议终端至少 40 列；长文本会截断，完整内容在导出文件中 |

更多运维细节见 [DEPLOYMENT.md](DEPLOYMENT.md)。
