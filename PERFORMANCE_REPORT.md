# 性能与稳定性报告

## 范围

本报告分开记录三类数据：

1. 固定 WAV + 本地 Mock 的可重复延迟。
2. Windows 主机真实音频采集数据。
3. 需要 BYOK 的外部 ASR/AI 延迟，当前不伪造结果。

测试日期：2026-07-19 至 2026-07-20；Windows 11 `10.0.26200`；Python `3.12.10`。

## 门槛

| 指标 | 门槛 |
| --- | ---: |
| Mock health p95 | `< 300ms` |
| 固定 WAV Mock ASR p95 | `< 800ms` |
| Mock AI SSE TTFB p95 | `< 800ms` |
| HTTP 业务错误率 | `0` |
| 固定音频 final | 与 metadata 完全一致 |
| 退出残留音频/TUI/AI 线程 | `0` |

外部云端 ASR/AI 延迟不计入本地门槛。

## 基线与收敛结果

改造前的 HTTP Mock 使用随机延迟与调用者传入的 `text_hint`，端口为 8765。2026-07-19 在 20 次、并发 4 下记录：

| 阶段 | health p95 | ASR p95 | AI TTFB p95 | AI 总时长 p95 | 错误 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 改造前基线 | 160.99ms | 327.37ms | 513.48ms | 1312.67ms | 0/20 |
| 固定 fixture 收敛后 | 23.94ms | 80.68ms | 146.03ms | 212.25ms | 0/20 |
| 最终统一复跑（2026-07-20） | 27.88ms | 82.33ms | 141.44ms | 209.31ms | 0/20 |

收敛后命令：

```powershell
python loadtest\mock_server.py --port 19060
python loadtest\dry_run.py --base-url http://127.0.0.1:19060 --iterations 20 --concurrency 4
```

变化不只来自优化：测试方法改为传输并校验固定 WAV，服务延迟由随机改为确定性，因此这组数据用于回归预算，不宣称等同云端性能。

## 固定音频端到端

```powershell
python src\cloud_asr_volcengine.py --mock-demo
```

2026-07-20 最终包内 EXE 复跑：

| 指标 | 结果 |
| --- | ---: |
| WAV 时长 / 块大小 | 3.0s / 100ms |
| 音频块 | 30 |
| partial / final | 3 / 1 |
| ASR 模拟断线恢复 | 1 |
| AI 取消 | 通过 |
| AI 首次失败后调用次数 | 2 |
| 保存/导出 | JSON、Markdown、TXT 均通过 |
| 流程耗时 | 244.11ms（非实时回放） |
| 残留线程 | 0 |

离线流程故意不按 3 秒实时等待，用于快速确定性回归。60 秒演示另由 TUI 录屏模式按产品状态播放。

## Windows 真实采音

```powershell
python src\cloud_asr_volcengine.py --windows-audio-acceptance
```

2026-07-20 最终统一复跑：

| 指标 | 结果 |
| --- | ---: |
| 实际输出设备 | Realtek(R) Audio loopback |
| 系统声音块 | 41 |
| 峰值 RMS | 0.485461 |
| 主频 | 330.00Hz（fixture 目标 330Hz） |
| 麦克风块 | 5（当时环境静音，RMS 0） |
| 混合块 | 41 |
| 混合峰值 RMS | 0.242550 |
| 热切换 | Realtek -> ToDesk -> Realtek，通过 |
| 背压丢弃 | 0 |
| 暂停/恢复、停止 | 通过 |
| 残留音频线程 | 0 |

麦克风 RMS 为 0 只说明测试时无人说话；设备打开和 PCM 块读取成功，不据此宣称真实语音识别质量。

## 缓冲与长期运行设计

- 默认音频块 100ms，ASR 队列 3 秒，重放缓冲 8 秒。
- 队列满时丢弃最旧块而不是阻塞采集；计数进入日志/验收报告。
- 混合输入各有 4 块源缓冲，防止一路设备阻塞另一路。
- partial 只覆盖兼容文件；结构化会话主要持久化 final，避免每 100ms 重写大 JSON。
- AI 上下文默认最多 4000 字、答案最多 8000 字，防止长会议内存与请求无限增长。
- final 历史和上下文使用有界 deque；TUI 固定频率刷新。

当前没有完成 2 小时真实会议 soak。固定 fixture、并发 Mock、设备恢复和退出线程测试通过，但长时驱动/供应商稳定性仍属于外部或延长验收项。

## k6

```powershell
$env:MOCK_BASE_URL = "http://127.0.0.1:19060"
k6 run loadtest\k6_smoke.js
```

CI 使用 1 VU smoke 与最高 5 VU burst，门槛与本报告一致。最终本机若未安装 k6，可由 Docker 版 k6 或 CI 执行；Python `dry_run.py` 是本地确定性门禁。

## BYOK 外部项

尚未提供真实 Key，因此没有本轮真实 ASR partial/final 延迟、真实 AI TTFB、模型答案质量、供应商 429 配额或长会议费用数据。这些结果不得从 Mock 数字推导。
