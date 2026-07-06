# meeting-ai-copilot 性能压测报告（P0 Mock 基线）

> **范围**：本报告描述 **Mock ASR/AI** 压测基线，用于 CI / 本地 dry-run。**不调用真实火山 WebSocket ASR 或 Coding Plan SSE**，避免密钥消耗与外部依赖。

## 1. 测试目标

| 目标 | 说明 |
| --- | --- |
| Smoke 基线 | 单用户 30s 内 health / ASR chunk / AI SSE 全链路可达 |
| 并发 burst | 5 VU × 20s 观察 mock 服务线程池下 p95 是否越界 |
| 指标对齐 | 用 mock 延迟形状近似生产：ASR ~100ms 块、AI TTFB ~200ms + token 流 |

## 2. 架构（Mock）

```
k6 / dry_run.py
    │  HTTP
    ▼
mock_server.py (:8765)
    ├─ GET  /health
    ├─ POST /mock/asr/chunk      ← 模拟 partial/final 转写
    └─ POST /mock/ai/responses     ← 模拟 SSE 流式答案
```

与真实 `meeting-ai-copilot` 的对应关系：

| Mock 端点 | 真实组件 | 真实协议 |
| --- | --- | --- |
| `/mock/asr/chunk` | 火山流式 ASR | WebSocket 双向流 + 100ms 音频块 |
| `/mock/ai/responses` | Volcengine Coding Plan | `POST {ai_base_url}/responses` SSE |
| `--smoke-test` CLI | 配置/启发式自检 | 无网络 |

## 3. 预期延迟指标（Mock 阈值）

| 指标 | Smoke 目标 (p95) | Burst 观察 | 说明 |
| --- | --- | --- | --- |
| `health` RTT | **< 500 ms** (dry_run 预热后) / **< 200 ms** (k6 理想) | < 250 ms | 本地 mock 探活 |
| ASR chunk RTT | **< 400 ms** | < 500 ms | 含 mock 80–180ms sleep |
| ASR 报告 `latency_ms` | **< 350 ms** | — | 响应 JSON 内字段 |
| AI SSE TTFB | **< 800 ms** | < 1200 ms | 首条 `data:` 到达 |
| AI SSE 总时长 | < 3000 ms | < 4000 ms | 10 token mock 流 |
| HTTP 失败率 | **< 1%** | < 2% | k6 `http_req_failed` |

## 3.1 CI 门禁（`.github/workflows/ci.yml` → job `loadtest-k6`）

CI 在 Ubuntu runner 上启动 `loadtest/mock_server.py`，依次执行 `dry_run.py` 与 `k6_smoke.js`。k6 阈值与 §3 表格一致，固化如下：

| k6 阈值 | 值 | 对应 §3 指标 |
| --- | --- | --- |
| `http_req_failed` | `rate<0.01` | HTTP 失败率 < 1% |
| `http_req_duration{endpoint:health}` | `p(95)<200` | health RTT |
| `http_req_duration{endpoint:asr}` | `p(95)<400` | ASR chunk RTT |
| `asr_chunk_latency_ms` | `p(95)<350` | ASR 报告 `latency_ms` |
| `ai_sse_ttfb_ms` | `p(95)<800` | AI SSE TTFB |

本地复现 CI 命令：

```powershell
python loadtest\mock_server.py --port 8765
python loadtest\dry_run.py --base-url http://127.0.0.1:8765
$env:MOCK_BASE_URL = "http://127.0.0.1:8765"
k6 run loadtest\k6_smoke.js
```

### 生产环境参考（非本次压测执行项）

以下为 **人工估算** 的线上目标，供后续接真 API 压测时对照；**本仓库 P0 不验证**。

| 阶段 | 参考目标 | 备注 |
| --- | --- | --- |
| ASR partial 可见 | 300–800 ms | 取决于网络与 100ms 音频块 |
| ASR final 分句 | 1–3 s | 视语句长度 |
| 问题识别 → AI 触发 | +50–200 ms | 本地启发式 |
| AI 首 token (TTFB) | 1–4 s | 模型与 Coding Plan 负载 |
| AI 完整答案 | 5–30 s | 流式输出长度 |

## 4. 本地运行方式

### 4.1 启动 Mock 服务

```powershell
cd MeetingLiveTranscriber-云端实时版\MeetingLiveTranscriber
python loadtest\mock_server.py --port 8765
```

### 4.2 Python dry-run（无需 k6）

```powershell
python loadtest\dry_run.py --base-url http://127.0.0.1:8765 --iterations 20 --concurrency 4
```

成功输出末尾为 `DRY-RUN PASSED`。

### 4.3 k6 smoke + burst

安装 [k6](https://k6.io/docs/get-started/installation/) 后：

```powershell
$env:MOCK_BASE_URL = "http://127.0.0.1:8765"
k6 run loadtest\k6_smoke.js
```

### 4.4 Locust 本地热路径（不依赖 HTTP mock）

压测 `load_config` 与 `is_question_like` 批处理，不调用火山 API：

```powershell
pip install locust
locust -f loadtest\locustfile.py --headless -u 100 -r 20 -t 30s --only-summary
```

### 4.5 与现有 smoke-test 的关系

```powershell
python src\cloud_asr_volcengine.py --config config.example.json --smoke-test
```

上述命令验证 **配置加载 / ASR 请求构造 / 问题启发式**，无密钥、无音频设备；与 HTTP 压测互补。

## 5. 文件清单

| 文件 | 用途 |
| --- | --- |
| `loadtest/mock_server.py` | Mock ASR/AI HTTP 服务 |
| `loadtest/k6_smoke.js` | k6 smoke + 5 VU burst |
| `loadtest/dry_run.py` | 无 k6 时 Python 并发 dry-run |
| `loadtest/locustfile.py` | Locust 本地问句识别 / 配置加载压测 |
| `PERFORMANCE_REPORT.md` | 本报告 |

## 6. 实测记录

| 日期 | 工具 | VU/并发 | health p95 | ASR p95 | AI TTFB p95 | 失败率 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-06 | dry_run | 4 | 33 ms | 190 ms | 331 ms | 0 | mock @8765，20 iter，PASSED |
| 2026-07-06 | locust | 100 users | — | — | — | 0 | `is_question_like_batch` p95=0ms，`load_config` p95=4ms，22950 reqs |
| 2026-07-06 | k6 (Docker) | 5 | 50 ms | — | 108 ms | 0.48% | `host.docker.internal` 桥接偶发超时 |

dry_run 摘要（2026-07-06）：

```text
health_p95_ms: 33.07
asr_p95_ms: 190.29
ai_ttfb_p95_ms: 331.16
ai_total_p95_ms: 748.69
errors: 0 / ok: 20
DRY-RUN PASSED
```

## 7. 限制与后续

- Mock **未** 覆盖 WebSocket 帧编解码、WASAPI 采集、问题冷却去重。
- 接真 API 前需单独准备密钥隔离环境、限流与费用告警。
- 建议后续增加：Locust WebSocket 场景、录制真实 ASR 延迟分布回放。
- `loadtest/locustfile.py` 已覆盖本地问句识别热路径；HTTP mock 仍用 `mock_server.py` + `k6_smoke.js` / `dry_run.py`。
