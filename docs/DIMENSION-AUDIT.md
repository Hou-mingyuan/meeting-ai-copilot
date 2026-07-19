# Meeting AI Copilot · 十维审计报告

> **审计日期**：2026-07-06（**Round-7** · project-hub-1 · pytest 启发式测试）  
> **范围**：`meeting-ai-copilot`（Python 3.10+ · Windows WASAPI · 火山 ASR WebSocket · LLM SSE · Mock 零密钥）  
> **评分**：1–10 分  
> **关联**：[PRODUCTION-READINESS.md](../../ai-portfolio/PRODUCTION-READINESS.md) · [PERFORMANCE_REPORT.md](../PERFORMANCE_REPORT.md) · [CSDN 正文](../../docs/csdn/07-meeting-ai-copilot.md)

---

## 总览

| 维度 | 得分 | 等级 |
| --- | ---: | --- |
| 1. 文档与 README | **9** | 优秀 |
| 2. Docker 与部署 | **8** | 良好（诊断 smoke 实测 ✓ · 决策表已上提） |
| 3. CI / CD | **9** | 优秀 |
| 4. 性能与压测 | **8** | 良好（Locust 50VU 实测回填 ✓） |
| 5. 安全基线 | **8** | 良好 |
| 6. 测试与质量 | **9** | 优秀 |
| 7. 架构与核心链路 | **9** | 优秀 |
| 8. 桌面 UX / 可观测 | **8** | 良好（TUI 状态面板 + 运行时长） |
| 9. 演示与作品集 | **9** | 优秀 |
| 10. 可维护性与工程化 | **8** | 良好 |
| **加权平均** | **8.5** | **作品集就绪+** |

**结论**：**Windows 系统声音 → 流式 ASR → 问题启发式 → SSE 答案** 链路清晰。**Round-5**：`StatusTui` + Locust 50 VU。**Round-6**：Docker smoke 三行 OK · `DEPLOYMENT.md` §9 决策表 · D2 **△7→8** · 均分 **8.4**。**Round-7（project-hub-1）**：`pytest` 启发式套件（`load_config` / `is_question_like` / `build_start_request` / `transcript_question_fsm` partial→stable→ask）· CI 接入 · D6 **8→9** · 均分 **8.5**。

---

## 1. 文档与 README（9/10）

### 现状

- README：mermaid 数据流 + sequenceDiagram、演示指南 ✓ 表、BYOK 说明、Mock 路径、`--smoke-test` / `--diagnose` 矩阵。
- `USAGE.md`、`DEPLOYMENT.md`（含 Docker 能力边界表）、`SECURITY.md`、`PERFORMANCE_REPORT.md`。
- CSDN 长文 `docs/csdn/07-meeting-ai-copilot.md` 就绪。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| P2 | README 增加 **BYOK 配置截图**（火山控制台 Key 位置打码） |
| P3 | 英文 README 精简版（国际化 Pin） |
| P3 | 故障排查决策树（ASR 403 / AI 429 / 无 loopback 设备） |

---

## 2. Docker 与部署（8/10）

### 现状

- `docker-compose.yml`：**smoke-only** — 验证依赖、`--smoke-test` 三行 OK，**不采集 WASAPI**（文档已明确）。
- **Round-6 实测（2026-07-06）**：`docker compose up --no-build --abort-on-container-exit` → `SMOKE OK: config loaded` / `ASR start request built` / `AI question heuristic passed` · exit **0**。
- `DEPLOYMENT.md` §5 能力对照 + **§9 Docker 化可行性评估**（决策表 + mermaid 选型流）；README `<details>` 折叠块「Docker vs Windows 宿主机」。
- CI 跑 Docker smoke；Hub Profile 标记 **Windows 原生 / N/A Docker 运行时**。
- Windows 一键 bat + `.venv` 自举。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| ~~**P1**~~ | ~~DEPLOYMENT「Docker vs 宿主机」决策表上提 README~~ ✅ §9 + README 折叠块 |
| P2 | 可选 WSL2 音频实验说明（非承诺支持，§9.2 已记录） |
| P3 | 安装包 / PyInstaller 单文件 exe（Roadmap） |

---

## 3. CI / CD（9/10）

### 现状

- `.github/workflows/ci.yml`：`py_compile`、`--smoke-test`、Mock `--test-ai`、`demo-mock.ps1`、Docker smoke。
- 远程 GHA **绿** + README CI badge。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| P2 | CI 矩阵标注 Python 3.10 / 3.11 / 3.12 |
| P3 | 发布 GitHub Release 时附带 `config.example.json` checksum |

---

## 4. 性能与压测（8/10）

### 现状

- `loadtest/mock_server.py` + `scripts/demo_mock_loop.py`；`loadtest/locustfile.py` 针对本地热路径。
- **Round-5**：Locust **50 VU × 30s** 复跑 — `is_question_like_batch` p95 **1ms**、`load_config` p95 **16ms**，6895 reqs，**0 失败**（见 PERFORMANCE_REPORT §6）。
- `PERFORMANCE_REPORT.md` 定义 ASR 100ms 块、重连退避；生产 ASR 压测仍依赖 BYOK（合理）。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| ~~P2~~ | ~~Locust 50 用户 Mock AI 跑一轮，填入 PERFORMANCE_REPORT~~ ✅ Round-5 |
| P2 | 记录 **partial→final 延迟** 与 **AI 首 token 延迟** 本机采样（BYOK 一次） |
| P3 | 内存/CPU 长会议 2h 采样脚本 |

---

## 5. 安全基线（8/10）

### 现状

- `SECURITY.md`：密钥仅 `.env`/本地 config、禁止 commit Key、桌面 txt 敏感提示。
- 配置加载支持环境变量覆盖；无密钥时 Mock 回退。
- 输出文件在用户桌面 `实时监听\` 目录，路径可配置。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| P2 | 可选 **输出目录加密** 或 Windows DPAPI 包装（Roadmap） |
| P3 | 日志脱敏（Key 前缀 never print）静态检查 |
| P3 | AI 请求内容本地 retention 策略文档 |

---

## 6. 测试与质量（9/10）

### 现状

- `--smoke-test`：配置 + ASR 请求构造 + 问题启发式。
- `--diagnose` / `--test-asr-handshake` / `--test-ai` 分层诊断。
- `demo-mock.ps1` 集成 Mock 全链路；CI 自动跑。
- **Round-7**：`tests/test_heuristic.py` **18 项 pytest** — `load_config` 合并默认、`is_question_like` 中英启发式、`build_start_request` 热词/词表、`transcript_question_fsm` partial 停顿→ask、AI 冷却去重；`src/transcript_question_fsm.py` 从 `receive_responses` 抽出可测状态机；CI `python -m pytest tests/ -q`。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| ~~P2~~ | ~~`pytest` 单元测试：`is_question_like`、partial 状态机~~ ✅ Round-7 |
| P2 | partial/final 文件写入逻辑单元测试 |
| P3 | 音频块 mock 回归（100ms PCM 边界） |
| P3 | `--mock-demo` 与 offline mock 行为一致性快照测试 |

---

## 7. 架构与核心链路（9/10）

### 现状

- **cloud_asr_volcengine.py** 入口调度；**cloud_runtime.py** 采集 / SSE / 文件 IO 分离清晰。
- partial 覆盖写 + final 追加，避免重复句子；问题冷却 + 60s 去重。
- Mock 路径：`ai_provider=mock`、内置 offline mock、`mock_server` HTTP 三档。
- 热词：内联 + `boosting_table_id` 双模式。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| P2 | 可插拔 LLM 网关（OpenAI 兼容）除火山 Coding Plan |
| P3 | 多 ASR vendor 抽象（当前火山深度绑定） |
| P3 | 插件式「问题检测」规则 YAML 外置 |

---

## 8. 桌面 UX / 可观测（8/10）

### 现状

- **`StatusTui`**（`src/status_tui.py`）：4 行终端面板 — 采集 / ASR / AI / 最近一句 + **运行时长**；TTY 默认开启，`--no-tui` 可关。
- 已接入 `cloud_asr_volcengine.py` 主循环与 `cloud_runtime` AI worker 状态回调。
- 控制台日志 + `--diagnose` 设备枚举；桌面 txt 输出保留。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| ~~**P1**~~ | ~~最小 **系统托盘** 或 TUI~~ ✅ TUI 已落地（Round-5 运行时长 polish） |
| P2 | 结构化日志 JSON + 可选 `--log-file` |
| P3 | 桌面通知（新 AI 答案就绪）· Windows 系统托盘（Roadmap） |

---

## 9. 演示与作品集（9/10）

### 现状

- Mock 零密钥：`demo-mock.ps1`、`config.mock-offline.json`、`--mock-demo`。
- README 演示指南 ✓ 四步；Docker smoke；CSDN + 简历段落素材。
- Hub 矩阵 **N/A Docker 运行时**，CI smoke 作为可验收基线。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| P2 | **60s 录屏脚本**（Mock 问题 → txt 出现 → AI 答案文件） |
| P3 | Portfolio 卡片统一 Mock 命令一行复制 |
| P3 | 示例 `config.mock.json` 预填演示热词 |

---

## 10. 可维护性与工程化（8/10）

### 现状

- `VERSION` + `CHANGELOG.md`；`requirements.txt` 锁定主依赖。
- `scripts/`、`loadtest/` 目录清晰；config 示例与 mock 配置分离。
- PChat 批次已补 PERFORMANCE_REPORT、loadtest、CI Mock 路径。

### 优化点

| 优先级 | 动作 |
| ---: | --- |
| P2 | `ruff` / `mypy` 可选 CI job |
| P3 | 单命令 `make test` 或 `scripts/test-all.ps1` |
| P3 | 类型注解覆盖 `cloud_runtime` 公共 API |

---

## 优先行动清单（Top 8）

| # | 优先级 | 动作 | 维度 |
| ---: | ---: | --- | --- |
| 1 | ~~**P1**~~ | ~~最小托盘/TUI 显示 ASR+AI 状态~~ ✅ TUI + 运行时长 | UX 8 |
| 2 | ~~**P1**~~ | ~~Docker vs 宿主机决策表上提 README~~ ✅ + smoke 实测 | 部署 8 |
| 3 | **P2** | pytest 覆盖问题启发式与文件写入 | 测试 8→9 |
| 4 | ~~**P2**~~ | ~~Locust Mock 50 VU 写入 PERFORMANCE_REPORT~~ ✅ Round-5 复跑 | 性能 8 |
| 5 | **P2** | BYOK 一次采样：首 token / partial 延迟 | 性能 |
| 6 | **P2** | Mock 演示 60s 录屏 | 演示 9→10 |
| 7 | **P3** | 可插拔 OpenAI 兼容 LLM | 架构 |
| 8 | **P3** | PyInstaller exe 打包 spike | 部署 |

---

## 与 ai-portfolio 矩阵对照

| 矩阵维度 | 标记 | 说明 |
| --- | --- | --- |
| README 四要素 | ✓ | 演示指南 + BYOK + SECURITY/DEPLOYMENT |
| Docker | ✓ | smoke 验收（非完整运行时） |
| CI | ✓ | GHA 绿 + badge |
| 压测 | ✓ | PERFORMANCE_REPORT + loadtest |
| 安全 / 部署 / 演示 | ✓ | |
| 多租户 | N/A | 桌面单用户 |
| Hub / 矩阵 | ✓ | CI badge + 矩阵条目 |

---

## 相关文档

- [README.md](../README.md)
- [USAGE.md](../USAGE.md)
- [DEPLOYMENT.md](../DEPLOYMENT.md)
- [SECURITY.md](../SECURITY.md)
- [PERFORMANCE_REPORT.md](../PERFORMANCE_REPORT.md)

*Round-6 复评 **8.4**；下一目标：pytest 覆盖启发式 → **8.5+**。*
