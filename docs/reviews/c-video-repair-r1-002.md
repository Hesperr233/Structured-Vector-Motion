# SVM C 板块独立审查 R1-002 · 重新审查与签收

**结论：BLOCKED。候选实现 6d874f0 的架构边界、数据安全、媒体与非回归均已独立核实为通过，但缺少 G1 契约冻结、针对该 SHA 的独立 CI、独立 oracle 与留出集，且候选自身的 `verify` 阶段对 A/B 均以退出码 7（CVR-E031）失败。当前禁止合并。**

本报告是独立审查线 R1-002 的重新审查结果，不沿袭旧报告 `docs/reviews/c-video-repair-r1.md`（BLOCKED @ b310f57）的结论。审查 Agent 只发现、验证证据并给出结论，未修改实践线、文档线、CI 线任何文件，未合并 PR。

---

## 1. 审查对象与绑定 SHA

| 对象 | SHA | 说明 |
| --- | --- | --- |
| 基线 main | `e3d9e70f0fd5f35e963ca4e7d8083bf5b8f14105` | 用户指定基线，`git rev-parse HEAD` 一致 |
| Contract SHA | `52180bd1e71b3b23b2e7cbbe2e236b05f944f2a3` | `agent/c-docs-r1`，四份契约标 DRAFT（R1-002） |
| Implementation SHA（= Candidate SHA） | `6d874f08415bd6d2475eaeeec8daa8577363aa25` | 本地 `agent/c-impl-r1`，**未推送到 origin** |
| CI SHA | `0c6b2a20a4c74c5d1089200af30a6af00a5448` | `agent/c-ci-r1` |
| 远端实践候选（已推送） | `a8f211bea533c2101200db620fe6d8ee47b4ecf8` | origin/agent/c-impl-r1，CI 线实际核验对象 |
| 旧 R1-001 候选 | `b310f57…` | 上一轮 BLOCKED，本轮明确排除 |

### 1.1 CI Run URL

候选 `6d874f0` **没有**独立 CI 运行。候选自身证据 `candidate-summary.json` 记录 `independent_ci_sha_for_this_candidate: null`；CI 工作流固定 `IMPLEMENTATION_SHA = a8f211b`，不是 `6d874f0`。CI 线对 `a8f211b` 的运行记录仅存于 [PR #2 的 checks](https://github.com/Hesperr233/Structured-Vector-Motion/pull/2/checks)。

### 1.2 媒体产物与工件 SHA（B 8 秒样例）

| 工件 | SHA-256 |
| --- | --- |
| before.mp4 | `sha256:d7b9273dfc495317a0c8c32d2e2d5e1ea7cc91191776a33249951e49dab48f43` |
| after.mp4 | `sha256:0b6e03eb32bda8a91e8e7a64f8d2ca8c60af3df0958df7da6bbd8206cee415ba` |
| comparison.mp4 | `sha256:526a2e477206a1da72ad6e0037a6b326805f96ab82adb1454c96000c9926efc5` |
| run-manifest.json | `125C5790A2E5055AE47DD99959144948A4BAF3EDFC0F55CFCB83CFDF6B25D088` |
| metrics.json | `DC8E424924AF8D04272C1127FA56001E48F342224A1989B7FBDFB23D2E9D880F` |
| acceptance-report.json | `F9BBD9710429AFA0A980C7AEF1168AB1FF963FC1A7D433E07C56D403BCFC33C1` |

B 的 `run_id = run:b77204f7aaea84904f04e17e99f1327ce91c4beeacae2833b9fa1adb86e332b3`，`algorithm_id = cvr-causal-stationary-background@1`，`contract_version = c-video-repair-contract@0.1`，`draft_revision = R1-002`。

---

## 2. G0 只读预审：差异表

| 检查项 | 现状 | 与旧审查的差异 |
| --- | --- | --- |
| 当前 main | `e3d9e70`，已合并文档 PR（#1）与实现 PR（#2） | 相对 R1-001 新增 Motion 集成（e3fbb03）、R1-002 证据包（a483ddf）、规范 LF JSON（9a109cd） |
| 文档 PR | `agent/c-docs-r1` = `52180bd`，四份契约更新为 R1-002 | 契约内容由 R1-001 英文草案改为 R1-002 中文草案，仍 DRAFT |
| CI PR | `agent/c-ci-r1` = `0c6b2a2`，新增 8 个视频专项测试 + 2 个工作流 | 旧审查时 CI 无任何视频 job、无运行 |
| 实践 SHA | 本地 `agent/c-impl-r1` = `6d874f0`（formal 管线）；远端 = `a8f211b` | **本地领先远端 1 个提交，未推送** |
| CI SHA | `0c6b2a2` | 新 |
| G1 状态 | **未冻结**。契约自标 DRAFT，`candidate-summary.json` 记 `contract_state: DRAFT` | 无变化（仍未冻结） |
| 8 秒 B 证据 | `build/r1-002-g2-6d874f0/B/run`（96 帧、640×360、12fps，三路 mp4 + manifest + metrics） | 旧审查时 B 为未提交的本地产物，现已封存 |
| 当前测试 | 候选新增 `test_formal_media.py`、`test_formal_pipeline.py`；CI 新增 8 个专项测试 | 新 |
| 旧测试 | 429 项 Core 回归 | 保留 |

**关键差异结论**：候选由 `b310f57`（R1-001）推进到 `6d874f0`（R1-002，含 causal 修复 + 正式 staged 管线）。但 `6d874f0` 未推送、无独立 CI、G1 未冻结，三项前提均不满足，故不直接宣布 PASS。

---

## 3. G3 正式审查

审查 SHA 统一为候选 `6d874f08415bd6d2475eaeeec8daa8577363aa25`。

### A. 架构边界 — PASS

- `git diff --name-only e3d9e70 6d874f0` 仅返回 `prototype/video_restoration/` 下 12 个文件，**无 `svm/`、`schema/`、`spec/`、`tests/`、`.github/` 改动**。
- Document、Revision、Proposal/Change Authority 均未被触碰；候选不调用 `RevisionStore.commit()`。
- [motion.py](../../.c-impl-r1/prototype/video_restoration/motion.py#L41) 仅 `from svm import MotionEvaluator` 作只读消费，[L196-L214](../../.c-impl-r1/prototype/video_restoration/motion.py#L196-L214) 以 `MotionEvaluator(document).evaluate(tick, Quality.FINAL)` 采样，[L142-L152](../../.c-impl-r1/prototype/video_restoration/motion.py#L142-L152) `_assert_final` 断言每帧 `Quality.FINAL` 且无 `stale_outputs`。修复帧不是 Immutable Value。
- SVGRenderer 未改动。结论：C 板块保持在 Core 外，架构边界保持。

### B. 实践 — CHANGES_REQUIRED

- 8 秒 B：`fixture/ingest/repair/export/evaluate` 五阶段退出码均为 0（`B/stage-exits.txt`），96 帧、12fps、640×360 齐全；`verify` 退出 7（CVR-E031）。
- 局部缺陷/闪烁/拖影/外观：`causal_repair.py` 实现 ROI 内光度校正、同坐标时域供体、拖影检测；`ROI 外零变化` 经候选隔离重放（`reference_read_probe_denied: true`、`read_only_public_input_hashes_unchanged: true`）与 CI 线均确认 PASS。
- unresolved：A/B 验收报告 `T7_no_unresolved` 均 MET（`endpoint_unresolved` 为空，0 未解析像素）。
- **M3 拖影指标对 A 不达标**：`candidate-summary.json` 记 `A.failed_quality_checks: ["M3"]`，`A/run/metrics.json` 含 `cvr-metric-ghost-residual@0.1-r1.002` 未达 30% 门槛。A 总体 `CHANGES_REQUIRED`。
- 未将 `PARTIAL` 冒充 `SUCCESS`：`formal_pipeline.py` L532 `diagnostic = "CVR-E021" if status == "PARTIAL" else "CVR-E031"`，部分输出退出 5，未发布 `SUCCESS`。

### C. 数据安全 — PASS

- 正式 `repair` 仅读 `input/` 与 `masks/`，`read_video` 强制 `clean_reference_in_input: false`；`restore()` 只接收帧/掩膜/配置，不读任何文件。
- 候选隔离重放：`reference_read_probe_denied: true`。
- 测试 `test_input_tamper_and_reference_leak_are_rejected_before_publication` 通过，篡改输入与参考泄漏分别以 `CVR-E014`、`CVR-E013` 拒绝。
- Mask 完整性：`read_masks` 校验角色与摘要，`sealed()` 复核身份。原始输入哈希不变。结论：不读 clean reference、不读 ground truth、不读 evaluation。

### D. 媒体 — PASS

独立 ffprobe 与 ffmpeg 完整解码（不使用候选的 decode helper）：

| 文件 | 编码 | 尺寸 | pix_fmt | 范围/矩阵/transfer | 帧数/时长 | 逐帧 MAE（独立解码） | 最小 PSNR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B before.mp4 | h264 | 640×360 | yuv420p | tv / bt709 / iec61966-2-1 | 96 / 8.0s | 1.399 DN | 40.42 dB |
| B after.mp4 | h264 | 640×360 | yuv420p | tv / bt709 / iec61966-2-1 | 96 / 8.0s | 1.262 DN | 40.57 dB |
| B comparison.mp4 | h264 | 1280×360 | yuv420p | tv / bt709 / iec61966-2-1 | 96 / 8.0s | 左 1.393 / 右 1.267 | 40.52 / 40.69 dB |
| A before/after/comparison | h264 | 256×256 | yuv420p | tv / bt709 / iec61966-2-1 | 24 / 2.0s | ≤ 1.664 DN | ≥ 40.00 dB |

三路均满足契约门槛（MAE ≤ 2.0 DN，PSNR ≥ 40 dB）。对比半幅左右分别对照各自 PNG。transfer 已由旧 `bt709` 改为 `iec61966-2-1`（sRGB），修复了 CI 对 a8f211b 指出的 transfer 不符问题。

### E. CI — BLOCKED

- 候选 `6d874f0` **无独立 CI**（CI 固定 `a8f211b`，`candidate-summary.json` 记 `independent_ci_sha_for_this_candidate: null`）。
- 本审查独立运行全量 `python -m unittest discover -s tests -v`：**Ran 429 tests，OK，0 失败 0 错误**（295.6s）。
- 本审查独立运行候选 formal 测试 `test_formal_media` + `test_formal_pipeline`：**9 tests，OK（1 skipped，因未设 CVR_FFMPEG 环境变量）**。
- 结论：候选 SHA 的 CI 缺失，不满足「运行对象是最新候选 SHA」。

### F. 非回归 — PASS

- 独立全量 429 项回归通过，覆盖 Golden A/B、Motion、Motion Revision、SVGRenderer、Editor、Temporal Correspondence/Identity、POP 等。候选仅新增 C 板块代码，未破坏 Motion 编辑与 Core。

---

## 4. 缺陷分级

所有缺陷基于候选 `6d874f08415bd6d2475eaeeec8daa8577363aa25`。

### P0（阻断合并）

- **P0-1 候选未推送远端**。`git ls-remote origin` 中 `agent/c-impl-r1 = a8f211b`，本地领先 `6d874f0` 一个提交。复现：`git ls-remote origin agent/c-impl-r1`。后果：无法从远端复现、无法触发 CI。
- **P0-2 候选 SHA 无独立 CI**。`agent/c-ci-r1` 的工作流 `IMPLEMENTATION_SHA = a8f211b`，非 `6d874f0`；`candidate-summary.json` 记 `independent_ci_sha_for_this_candidate: null`。复现：读 `.github/workflows/video-repair.yml` 与候选 `candidate-summary.json`。后果：不满足「运行对象是最新候选 SHA」。
- **P0-3 G1 契约未冻结**。四份契约自标 DRAFT；`formal_pipeline.py` [L494](../../.c-impl-r1/prototype/video_restoration/formal_pipeline.py#L494) `overall = "CHANGES_REQUIRED" if failed else "BLOCKED"`，`verify` 阶段结构上**永远不可能返回 PASS**。复现：运行 `verify`，观察 exit 7。后果：当前无法签发通过。
- **P0-4 候选自身 verify 失败**。A/B 的 `stage-exits.txt` 均为 `verify 7`（CVR-E031）；A 总体 `CHANGES_REQUIRED`，B 总体 `BLOCKED`。复现：读 `build/r1-002-g2-6d874f0/{A,B}/stage-exits.txt` 与 `acceptance-report.json`。后果：验收未通过。

### P1（修复后复审）

- **P1-1 M3 拖影指标对 A 不达标**。文件 `build/r1-002-g2-6d874f0/A/run/metrics.json`，公式 `cvr-metric-ghost-residual@0.1-r1.002`，未达 30% 改善门槛；`candidate-summary.json` `failed_quality_checks: ["M3"]`。复现：`evaluate` 后读 M3 行。
- **P1-2 缺独立 oracle 与留出集 H1–H5**。评分仍由候选自身 `formal_scoring.py` 计算，无 CI 线独立 oracle 实现；无冻结 holdout。复现：`candidate-summary.json` 缺 `INDEPENDENT_CI_ORACLE`、`H1–H5` 证据。
- **P1-3 轮廓/质心/邻帧护栏 NOT RUN**。`candidate-summary.json` 记缺失 `Q2/Q3/Q4/T3/T4`，因缺冻结输出轮廓分割规则与可靠供体计数。复现：读 A/B `acceptance-report.json` 的护栏状态。

### P2（记录并排期）

- **P2-1 sRGB transfer 仅标记未转换**。`formal_media.py` [L25](../../.c-impl-r1/prototype/video_restoration/formal_media.py#L25)、[L28](../../.c-impl-r1/prototype/video_restoration/formal_media.py#L28) 注释「No transfer-function conversion is part of this profile」，标记 `iec61966-2-1` 但不做传递函数转换。往返一致且 MAE 达标，但标记为名义 sRGB。
- **P2-2 legacy 与 formal 双路径并存**。`demo/evaluate/synthetic/motion`（R1-001/R1-002 兼容路径）与 `formal_*`（正式管线）共存，兼容窗口未由策划线审定。
- **P2-3 评分与验收代码同源**。`formal_scoring.py` 既被候选 `evaluate` 调用，也作为 `verify` 的评分回放依据，缺少独立评分实现。

### P3（建议）

- **P3-1** 增加色条/暗阶往返测试，验证 sRGB transfer 标记与实际像素转换的一致性。
- **P3-2** 统一解码 filter：候选 decode 用 `neighbor+full_chroma_int`，本审查独立默认解码 MAE 略高（1.4 vs 0.6 DN）但仍达标；建议在契约中固定解码口径。

---

## 5. 风险等级与阻断项

- **风险等级：高**。契约未冻结、候选未推送、无独立 CI、无独立 oracle 与留出集，四项叠加使当前交付不可签收。
- **阻断项**：P0-1（未推送）、P0-2（无独立 CI）、P0-3（G1 未冻结）、P0-4（verify 失败）。

---

## 6. 最终结论

**BLOCKED。**

本审查独立核实了候选 `6d874f0` 的正面性质：Core 边界保持、数据安全（不读 clean/ground truth/evaluation）、媒体完整解码且 MAE/PSNR 达标、429 项 Core 回归与 9 项 formal 测试通过。但候选存在四项阻断：未推送到远端、无针对该 SHA 的独立 CI、G1 契约未冻结（`verify` 结构上无法 PASS）、候选自身 `verify` 以退出码 7 失败且 A 的 M3 拖影指标不达标。

**重新审查条件（供实践线/CI 线/策划线，非本审查线待办）**：
1. 实践线将 `6d874f0`（或其后续修复）推送到远端，并给出唯一 Candidate SHA。
2. 策划线完成 G1 契约冻结，登记留出种子与阈值。
3. CI 线将工作流 `IMPLEMENTATION_SHA` 指向唯一候选，补齐独立 oracle 实现并跑通全量 unittest + Ruff + Pyright + compileall + 视频专项 + 媒体验收。
4. 实践线修复 P1-1（M3）、P1-2（留出集/oracle）、P1-3（轮廓/质心护栏）后重新提审。

在上述条件满足前，不因实践线/CI 线/文档线的任何自评通过而给出 PASS。
