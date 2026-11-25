# SVM C 板块｜独立审查线 R1-001

**结论：BLOCKED。当前不存在可作为 R1-002 正式验收候选的 SHA；已提交的 b310f57 只能作为 R1-001 兼容原型审查。C 板块不得据此合并或宣称 R1 完成。**

- 审查日期：2026-11-23（Asia/Shanghai）
- 审查线：C 板块独立审查线，只读审查；未修改实现、测试、契约、阈值或其他线文件，未合并 PR。
- 审查方式：独立 worktree `D:\codex\svm-c-review-r1`（分支 `agent/c-review-r1`，从 `origin/main` 创建）；另建 detached worktree 固定候选 SHA 用于执行。
- 适用范围：SVM Core 外的受控视频修复交付、媒体、Manifest、Metrics、隔离与回归；不评价自然视频泛化。

## 0. 审查对象与不可变标识

| 项目 | 值 |
| --- | --- |
| 仓库 / remote | `https://github.com/Hesperr233/Structured-Vector-Motion` |
| `main` | `cf4815357909f4e462f26619d33e6bfcb1e16236` |
| **Reviewed Candidate SHA** | **`b310f57e6a21562798f5ccad2a1897ba4d99c47e`**（`origin/agent/c-impl-r1`，审查期间未变化） |
| 候选内嵌契约文档 SHA | `6b9a304f66d8a657f77e729a73e36f8bb0f3133c`（R1-001 文档，非 R1-002） |
| 文档分支审查开始时 | `5561d8b59fb1d3ebd5544bacde88f387d3902100` |
| 文档分支审查期间推进 | `dc2cac17ddbff6cb95d21a567e7e5f87c1f62029`，`docs: align C video repair R1-002 contract`，2026-11-23 21:35 +0800 |
| Contract Version | `c-video-repair-contract@0.1`；最新草案修订 `R1-002`；**DRAFT，未记录 G1 冻结** |
| Fixture / 算法版本 | 生成器 `svm-c-r1-synthetic@1`，seed `20261109`；原型 profile `svm-c-r1-restoration@1`；正式 `algorithm_id` 与 `code_sha` 未实现 |
| CI Run URL | **无**。GitHub Actions API 返回 `total_count: 0`；仓库无 C 视频测试、无视频 workflow |
| 2 秒原型媒体 | `D:\codex\Structured-Vector-Motion\.c-impl-r1\build\r1-002\s1\result`（未跟踪 build 产物） |
| 8 秒未提交运行 | `D:\codex\Structured-Vector-Motion\.c-impl-r1\build\r1-002\motion`（未提交、无 SHA，仅供 G2 观察） |

审查期间候选实现 SHA 未变，但契约文档从 `5561d8b` 推进到 `dc2cac1`（R1-002）。已按要求重新核对受影响部分：候选 `b310f57` 不包含 `dc2cac1`（`git merge-base --is-ancestor dc2cac1 b310f57` 返回 1），其内嵌文档仍是 R1-001 旧草案。因此当前候选不能作为 R1-002 候选验收。

## 1. G0 只读预审结果

G0 不给出整个项目 PASS。九项重点核查与 Gitlink/旧日志结果如下。

| ID | 检查项 | 结果 | 证据摘要 |
| --- | --- | --- | --- |
| G0-1 | SVM Motion 与外部视频修复边界 | **PASS（仅已提交 A 原型）** | 候选 diff 只新增 `docs/c-video-repair/` 与 `prototype/video_restoration/`；后者不 import `svm`；文档把工具分类为 Artifact Processor / Exporter，不是 Adapter/Backend。B 正式路径尚未提交。 |
| G0-2 | Document / Revision 污染 | **PASS** | `git diff --name-status cf48153..b310f57` 无 `svm/`、`schema/`、`spec/`、`tests/`、`.github/`、`pyproject.toml`；无 Document 写入、无 Proposal/Change Authority/Revision 调用。 |
| G0-3 | FINAL 与 stale 状态 | **BLOCKED（B）/ N/A（A）** | 候选 A 原型不使用 MotionEvaluator/SVGRenderer，因此不存在 FINAL/stale 选择；但 B 所需的 FINAL、非 stale 证据只出现在未提交的 8 秒运行的 `source-manifest.json`（`quality: FINAL`, `stale_frames: 0`），无对应代码 SHA，不能验收。 |
| G0-4 | 原始视频保护 | **PARTIAL** | 已提交原型只接受 PNG 序列；两次运行前后输入与掩膜 SHA 完全不变，输出目录必须为新目录。MP4 ingest、原视频字节快照与源 PTS 映射未实现，真实视频无法验证。 |
| G0-5 | clean reference 泄漏到 repair | **PASS（A 原型）** | 删除 `fixture/evaluation/` 后连续运行两次，两组共 48 张 PNG、3 支 MP4 与 `manifest.json` 均与既有结果逐字节一致；repair 清单无 `evaluation`/`fixture.json`/clean 路径；扫描项目 import 无反向依赖。 |
| G0-6 | Mask / ROI 是否真正有效 | **PARTIAL** | ROI 外变化像素为 0；非二值掩膜、满幅 ROI、ROI 外 ghost、缺帧、额外帧、输出已存在均以退出 2 拒绝且不产生修复帧。但 ROI 是整块矩形，没有 `feather_px`、`max_changed_fraction`、独立 `evaluation_roi`；2 秒样例中 ROI 内 516,096 像素有 387,358 改变（75.06%），且出现可见矩形亮度接缝。 |
| G0-7 | 导出完整解码证据 | **PASS（媒体属性）/ FAIL（命名与契约）** | 2 秒与 8 秒媒体均可完整解码，PTS 连续，帧数/分辨率/帧率/时长正确，对照左右方向正确；但已提交 A 原型没有 `before.mp4`/`after.mp4`，只有 `original.mp4`/`repaired.mp4`。 |
| G0-8 | 虚假成功 Manifest | **FAIL** | 2 秒与 8 秒运行均为 `status: partial` 但 `exit_code: 0`；没有 `SUCCESS` manifest，也没有 `acceptance-report.json`；自动化仅看退出码会把部分结果当成功。 |
| G0-9 | CI 基线是否可信 | **FAIL（C 验收）/ PASS（Core 回归）** | 无同 SHA CI、无 `tests/test_video_repair_*`、无视频 workflow；GitHub Actions 运行总数为 0。独立在候选 SHA 上跑全量 unittest 为 429 通过、0 失败、0 跳过；Ruff/Pyright/compileall 通过。Core 回归可信，C 验收 CI 不存在。 |
| G0-10 | Gitlink 与旧日志 | **FAIL（仓库结构）** | `.c-impl-r1`、`.review-r1` 是 mode `160000` gitlink，主树无 `.gitmodules`，`git submodule status` 直接 fatal；`.c-impl-r1` 实际是本仓库 worktree，`.review-r1` 是嵌套仓库。`.review-r1-baseline.log` 是失败的旧基线。 |

### 1.1 Gitlink / 旧日志细节

- `.c-impl-r1` 指向 `b310f57`，`.review-r1` 指向 `e3cb5a2`，两者在主树以 gitlink 记录；`.gitmodules` 不存在，`git submodule status` 报 `no submodule mapping found in .gitmodules for path '.c-impl-r1'`。
- `.c-impl-r1/.git` 为 `gitdir: D:/codex/Structured-Vector-Motion/.git/worktrees/-c-impl-r1`，说明它是同一仓库的内部 worktree，而不是独立子模块；`.review-r1` 是另一个嵌套仓库。
- [.review-r1-baseline.log](https://github.com/Hesperr233/Structured-Vector-Motion/blob/cf4815357909f4e462f26619d33e6bfcb1e16236/.review-r1-baseline.log#L1704-L1706) 记录 `Ran 60 tests`、`FAILED (failures=5, errors=55)`，原因是环境缺 `svgpathtools`/`jsonschema`，不能作为可信基线。
- 文档分支 `dc2cac1` 已删除 `.c-impl-r1`、`.review-r1`、`.review-r1-baseline.log`；主分支 `cf48153` 仍保留。本次审查未删除、未修改这些文件。

### 1.2 契约漂移

- 审查开始时 `origin/agent/c-docs-r1` 为 `5561d8b`；审查期间推进到 `dc2cac1`，提交 `docs: align C video repair R1-002 contract`。
- `dc2cac1` 将契约改写为 R1-002，并明确：正式 `ingest`/`repair`/`export`/`evaluate`/`verify` 全部“待实现”；原型仅是 R1-001 兼容路径；原型 `demo` 退出 0 仍可能是 `partial`（[pipeline-contract-v0.1.md R1-002 L8-L32](https://github.com/Hesperr233/Structured-Vector-Motion/blob/dc2cac17ddbff6cb95d21a567e7e5f87c1f62029/docs/c-video-repair/pipeline-contract-v0.1.md#L8-L32)）。
- 候选 `b310f57` 内嵌的是 `6b9a304` 的旧草案（例如旧 `requirements-v0.1.md` 仍要求 `before.mp4`/`after.mp4`、`run-manifest.json`、`metrics.json` 由独立 evaluate 产出），与 R1-002 的正式接口、签名残差指标、`verify` 成功时点等均不一致。
- R1-002 仍是 DRAFT：`acceptance-criteria-v0.1.md` 明确“现有数值门槛原样保留为 G1 提案，尚未冻结”，且“A 留出通过与 B 演示通过分别记录，任一缺失则 C 板块整体交付未通过”（[R1-002 acceptance L3-L24](https://github.com/Hesperr233/Structured-Vector-Motion/blob/dc2cac17ddbff6cb95d21a567e7e5f87c1f62029/docs/c-video-repair/acceptance-criteria-v0.1.md#L3-L24)）。

## 2. G2 独立核验

### 2.1 代码与范围

- 候选 diff（`cf48153..b310f57`）仅 11 个新增文件：4 份 C 文档、7 个原型 Python/README 文件；没有任何 Core、Schema、Spec、测试、workflow 或 `pyproject.toml` 改动。
- `prototype/video_restoration/*.py` 只导入标准库、NumPy、OpenCV、Pillow；未导入 `svm`；`repair.py` 不调用 `evaluate.py`，`evaluate.py` 只被独立进程调用。
- 候选未使用 Document、Revision、Proposal、Change Authority；没有 `RevisionStore.commit()` 调用，也没有 accepted Document 修改。

### 2.2 回归与静态检查（独立 worktree，固定 SHA）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 全量 unittest | `python -m unittest discover -s tests -v` | **429 tests OK**，0 失败、0 错误、0 跳过；包含 Golden A/B、Motion M、Motion Revision N、SVGRenderer |
| Ruff format | `python -m ruff format --check .` | 209 files already formatted |
| Ruff lint | `python -m ruff check .` | All checks passed |
| Pyright | `python -m pyright --pythonpath <tooling-python>` | 0 errors, 0 warnings, 0 informations |
| compileall | `python -m compileall -q svm tests` | exit 0 |
| 工作树污染 | `git status --short` | 空（执行前后无跟踪文件变化） |

环境：Python 3.12.9（另行用 3.12.14 复核），NumPy 2.5.4/2.5.5，Pillow 12.3.0/13.0.0，OpenCV 4.14.0，Shapely 2.1.2，svgpathtools 1.8.0。Windows 单平台；未验证 Ubuntu 与 CI 矩阵的 3.11/3.12 全组合。

完整日志：`D:\codex\svm-c-review-temp\candidate-unittest.log`、`candidate-ruff-format.log`、`candidate-ruff-check.log`、`candidate-pyright2.log`、`candidate-compileall.log`。

### 2.3 2 秒 A 原型：媒体、指标与隔离

**独立复现**：复制 `build/r1-002/s1/fixture`，删除 `evaluation/`，用候选 SHA 的 `demo` 连续运行两次；两次输出与既有 `build/r1-002/s1/result` 的两组共 48 张 PNG、3 支 MP4、`manifest.json` **逐字节一致**；运行前后输入与四组掩膜 SHA 完全不变。该实验同时验证隔离（无 clean reference）与 D0 确定性。

**媒体核验**（ffprobe + 全解码 + 逐帧 RGB MAE；独立脚本 `verify_media.py`）：

| 文件 | 容器/编码 | 分辨率 | fps | 帧数 | PTS | 时长 | 最大单帧 MAE |
| --- | --- | --- | --- | --- | ---: | ---: | ---: |
| `original.mp4` | H.264 / yuv420p / BT.709 TV | 256 × 256 | 12/1 | 24 | 0..23 | 2.000 s | 1.902217 |
| `repaired.mp4` | 同上 | 256 × 256 | 12/1 | 24 | 0..23 | 2.000 s | 1.842412 |
| `comparison.mp4` | 同上 | 512 × 256 | 12/1 | 24 | 0..23 | 2.000 s | 1.922170 |

对照方向：左半幅对 `frames-original` 平均 MAE 1.793846，右半幅对 `frames-repaired` 1.745381；交换左右后 MAE 分别为 3.479401/3.492196，证明未交换。逐帧同步差最大 0.113856 DN。三支媒体均通过完整解码，无解码错误；MP4 文件 SHA 与 `manifest.json` 记录一致。

**指标复算**（独立从无损帧与 clean reference 重算，与 `metrics.json` 差值为 0）：

| 指标 | 修复前 | 修复后 | 相对改善 |
| --- | ---: | ---: | ---: |
| ROI RGB MAE | 6.611509 | 0.007679 | 99.88% |
| 局部缺陷 RGB MAE | 73.772006 | 0 | 100% |
| 拖影掩膜 >1 DN 残差 | 26.609687 | 8.169516 | 69.30% |
| 静态区亮度时间标准差 | 7.810250 | 0 | 100% |
| 目标内部 RGB 均值时间标准差 | 7.810250 | ≈0（3.3e-14） | ≈100% |
| 运动边缘梯度 | 40.990333 | 40.990333 | 1.000000 |
| 全帧 RGB MAE | 6.536589 | 4.369707 | 33.15% |
| ROI 外变化像素 | — | 0 | — |

指标数值本身可独立复算，未发现伪造；但见 §4 的契约与质量缺陷。

**目视检查**：已查看代表帧接触表 [sheet-2s.png](file:///d:/codex/svm-c-review-temp/sheet-2s.png)、ROI 边界放大图 [crop-2s-frame0.png](file:///d:/codex/svm-c-review-temp/crop-2s-frame0.png) 与拖影区放大图 [crop-ghost-frame2.png](file:///d:/codex/svm-c-review-temp/crop-ghost-frame2.png)。缺陷方块被清除，拖影未见新重影，物体轮廓保持；但修复后在 ROI 边界出现明显矩形亮度接缝（详见 P1-2）。未进行连续视频的人眼完整播放，主观观看仍应在 G3 人审补做。

### 2.4 未提交的 8 秒 B 观察样本

`build/r1-002/motion` 是审查期间出现的本地运行，使用 `before.mp4`/`after.mp4`/`comparison.mp4`、`run-manifest.json`、`source-manifest.json` 与真实 SVM Document；但它**没有已提交代码，没有 Candidate SHA，不能作为审查对象**。独立核验结果：

| 项目 | 结果 |
| --- | --- |
| 规格 | 96 帧，640 × 360，12/1 fps，8.000 s；对照 1280 × 360 |
| 媒体 | 三支均 H.264/yuv420p/BT.709 TV，PTS 0..95，完整解码；最大单帧 MAE 1.585800 / 1.541531 / 1.578014 |
| 对照方向 | 左半幅 = before，右半幅 = after；方向正确，逐帧同步差最大 0.496723 DN |
| Manifest | `status: partial`，`exit_code: 0`，`contract_version: c-video-repair-contract@0.1`，`draft_revision: R1-002` |
| 未解决 | **65 帧存在 `unresolved_pixels`，合计 30,057 像素** |
| ROI | 外变化 0；内变化 10,132,692 / 13,541,376（74.82%）；同样存在 ROI 边界 8 DN 接缝 |
| 来源证据 | `source-manifest.json` 声明 `quality: FINAL`、`stale_frames: 0`、`all_operations_clean: true`；`document.svm.json` 独立 `svm validate` 通过 |
| 运行日志 | `logs/motion-source.log` 记录 mask/roi 路径不存在；`logs/motion-evaluate.log` 记录 `(96,360,640,3)` 与 `(96,360,640)` 广播错误；产物与日志的最终对应关系无法仅凭仓库复现 |
| 代码状态 | `.c-impl-r1` 工作树在审查期间出现未提交修改：`media.py`、`sequence.py`、`evaluate.py`；无对应提交 |

结论：这是一个有意义的 B 方向样本，具备 FINAL/stale 声明与真实媒体，但仍为 `partial`、有 3 万未解决像素、日志有失败、代码未提交。若实践线要把它作为 R1-002 候选，必须先提交并提供唯一 SHA，再整体重跑 G2/G3。

## 3. G3 最终审查（针对唯一 Candidate SHA b310f57）

### A. 架构边界

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 是否修改 `svm/` | **否** | `git diff --name-only cf48153..b310f57 -- svm` 为空 |
| 是否新增 Core Operation | **否** | 无 `svm/operations.py` 或注册表改动 |
| 是否修改 Schema | **否** | `schema/` 无改动 |
| 是否直接修改 accepted Document | **否** | 原型只读写外部 RGB PNG；未导入 `svm` |
| 是否绕过 Proposal / Change Authority / Revision | **否** | 没有相关调用；没有 `RevisionStore.commit()` |
| 是否把修复帧声称为 SVM Immutable Value / Document 内容 | **否** | README 明确修复像素不进入 Document/Revision/Proposal |

架构边界结论：**PASS（仅已提交 A 原型）**。但 R1-002 正式 CLI 与 B 路径尚未提交，不能据此给 C 板块整体 PASS。

### B. 算法质量

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 局部修复边缘污染 | **FAIL（P1-2）** | ROI 外像素不变，但全局闪烁只在 ROI 内校正，ROI 边界出现 12–13 DN 亮度台阶；放大图可见矩形接缝 |
| 闪烁抑制误伤真实亮度变化 | **部分通过** | `repair.py:95-106` 对同向亮度变化连续超过 2 帧拒绝校正；负例 `sustained-lighting-change` 退出 0 且不强行压平。但真实短时亮度变化仍可能被当作闪烁 |
| 拖影改善引入新重影 | **未发现** | 拖影区放大图未见新重影；残差 8.169516 DN，468 个拖影像素中 140 个保留（README 第 64 行） |
| 运动物体冻结 | **未发现** | 修复只改变 ROI 像素；物体几何未被重写，运动边缘梯度与 clean 完全一致（40.990333） |
| 通过模糊降低误差 | **未发现** | 边缘梯度 before/after/clean 均为 40.990333；无模糊迹象 |
| 外观稳定 | **部分通过** | 目标内部时间标准差降至 ≈0，但仅一个组合样例，无独立 S4 外观漂移片段；改善主要来自闪烁校正 |
| ROI 外保持不变 | **PASS** | 2 秒与 8 秒运行 ROI 外变化均为 0 |

### C. 数据安全

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| clean reference 是否进入 repair | **否** | 删除 `evaluation/` 后输出逐字节一致；repair 清单无 eval 路径 |
| ground truth 是否进入 repair | **否** | 真值只在 `evaluation/fixture.json` 与 clean 帧；repair CLI 未接收 |
| 评分函数是否被修复算法调用 | **否** | `repair.py` 不导入 `evaluate.py`；`demo.py` 不调用评分 |
| 输入文件是否被覆盖 | **否** | 运行前后输入/掩膜 SHA 不变；输出目录必须为新目录 |
| Manifest 是否可追溯 | **PARTIAL（P1-4）** | 有输入/掩膜/输出 SHA、profile、参数、环境、`repair_module_sha256`；缺 `contract_version`、`draft_revision`、`run_id`、`semantic_digest`、`code_sha`、`algorithm_id`、`scenario_kind`、`real_video`、`generalization_claimed`、`clean_reference_in_input`、bundle/mask/config 摘要 |
| 原视频快照 | **未实现** | 仅支持 PNG 序列；MP4 ingest、字节快照、源 PTS 映射未实现 |

### D. 媒体质量

| 检查 | 2 秒 A 原型 | 8 秒未提交 B 样本 |
| --- | --- | --- |
| 文件真实存在 | 是（`original/repaired/comparison.mp4`） | 是（`before/after/comparison.mp4`） |
| 契约命名 | **否**（缺 `before.mp4`/`after.mp4`） | 是（但未提交、无 SHA） |
| 完整解码 | 是 | 是 |
| FPS | 12/1 | 12/1 |
| PTS 连续 | 0..23 | 0..95 |
| 帧数 | 24 | 96 |
| 分辨率 | 256×256 / 对照 512×256 | 640×360 / 对照 1280×360 |
| 同步对照 | 左原右修，方向正确；最大同步差 0.114 DN | 左 before 右 after，方向正确；最大同步差 0.497 DN |
| 无损 PNG 解码 | 24 帧 RGB8，ROI 外不变 | 96 帧 RGB8，ROI 外不变 |
| 媒体解码容差 | 实际 max 1.92 DN（满足提案 2.0）；代码守卫为 8 DN | 实际 max 1.59 DN；代码守卫 8 DN |
| 结论 | 媒体本身通过；命名/Manifest/退出状态不通过 | 媒体本身通过；运行 `partial`、未提交、日志有失败 |

### E. 回归

| 检查 | 结果 |
| --- | --- |
| Golden A / Golden B | 通过（429 tests OK） |
| Motion M / Motion Revision N | 通过 |
| SVGRenderer | 通过 |
| 原有完整测试 | 429 通过，0 失败、0 跳过 |
| Ruff / Pyright / compileall | 通过 |
| C 专属测试 | **不存在** `tests/test_video_repair_*` |
| C 专属 CI | **不存在** 视频 job；GitHub Actions 运行总数为 0 |

回归结论：**SVM Core 回归 PASS；C 验收回归不存在。**

## 4. 缺陷清单

P0 阻断合并；P1 必须修复后重新审查；P2 可排入后续迭代但必须记录；P3 非阻断建议。

### P0

| ID | 缺陷 | 证据 / 复现 | 关闭条件 |
| --- | --- | --- | --- |
| P0-1 | 无同 SHA CI、无 C 测试 | 候选 diff 无 `tests/`、`.github/` 改动；[ci.yml](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/.github/workflows/ci.yml) 无 video/repair 引用；GitHub Actions API `total_count: 0` | 在候选 SHA 上新增 C 测试与视频 job，实际运行并提供 URL；缺工具必须失败而非 skip |
| P0-2 | 契约未冻结且审查期间漂移 | docs 从 `5561d8b` 推进到 `dc2cac1`（R1-002）；候选内嵌 `6b9a304`，不包含 `dc2cac1`；R1-002 仍 DRAFT，G1 未冻结 | 策划线记录 G1 冻结：文档 SHA、阈值、区域、留出集、工具环境；实现线基于该 SHA 提交候选 |
| P0-3 | 无 R1-002 候选；B 交付未提交 | R1-002 明确正式 `ingest/repair/export/evaluate/verify` 全部“待实现”（[pipeline L18-L22](https://github.com/Hesperr233/Structured-Vector-Motion/blob/dc2cac17ddbff6cb95d21a567e7e5f87c1f62029/docs/c-video-repair/pipeline-contract-v0.1.md#L18-L22)）；唯一候选 `b310f57` 仅 2 秒 A 原型；8 秒 B 运行未提交、`partial`、65 帧未解决 | 提交正式实现与 B 驱动，给出唯一 Candidate SHA；重跑 G2/G3 |

### P1

| ID | 缺陷 | 证据 / 复现 |
| --- | --- | --- |
| P1-1 | `PARTIAL` 运行返回 0，形成虚假成功信号 | [demo.py L84-L102](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/demo.py#L84-L102) 在 `repair_status == contains_refusals` 时写 `partial`，但 `exit_code` 仍为 0；README 第 47 行也以退出 0 描述通过。2 秒样例 16 帧 ghost 拒绝；8 秒样例 65 帧、30,057 像素未解决，仍退出 0。R1-002 要求证据不足退出 5，且仅 `verify` 的 0 表示验收通过（[pipeline L132-L141](https://github.com/Hesperr233/Structured-Vector-Motion/blob/dc2cac17ddbff6cb95d21a567e7e5f87c1f62029/docs/c-video-repair/pipeline-contract-v0.1.md#L132-L141)） |
| P1-2 | 全局闪烁 + ROI 内校正产生可见矩形接缝（边缘污染） | [synthetic.py L123](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/synthetic.py#L123) 对全帧加闪烁；[repair.py L249-L251](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/repair.py#L249-L251) 只在 ROI 应用校正。frame 0 的 ROI 内误差 0、ROI 外误差 12 DN，边界像素从 `[92,116,140]` 跳到 `[105,128,153]`；放大图 [crop-2s-frame0.png](file:///d:/codex/svm-c-review-temp/crop-2s-frame0.png) 可见矩形亮带。R1-002 U3 要求不得引入新的可见伪影 |
| P1-3 | 契约命名/目录缺失 | 候选只有 `original.mp4`、`repaired.mp4`、`manifest.json`（[media.py L164-L172](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/media.py#L164-L172)）；无 `before.mp4`、`after.mp4`、`run-manifest.json`、`acceptance-report.json`、`run.log`；帧名为六位 `000000.png`，R1-002 要求 `frames/frame-0000.png` |
| P1-4 | Manifest/报告身份字段不足 | `manifest.json` 键只有 `profile/scope/width/height/frame_count/fps/...`（[demo.py L55-L82](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/demo.py#L55-L82)）；缺 `contract_version`、`draft_revision`、`run_id`、`semantic_digest`、`code_sha`、`algorithm_id`、`scenario_kind`、`real_video`、`generalization_claimed`、`clean_reference_in_input`、输入 bundle/mask/config 摘要。R1-002 要求这些字段（[pipeline L145-L155](https://github.com/Hesperr233/Structured-Vector-Motion/blob/dc2cac17ddbff6cb95d21a567e7e5f87c1f62029/docs/c-video-repair/pipeline-contract-v0.1.md#L145-L155)） |
| P1-5 | 无正式 config / mask manifest / input bundle；参数硬编码 | [repair.py L8-L23](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/repair.py#L8-L23) 为固定 `PARAMETERS`；没有 `config.json`、未知字段失败、`max_changed_fraction`、`feather_px`、`photometric_roi`；[sequence.py L63-L77](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/sequence.py#L63-L77) 只检查四组角色目录 |
| P1-6 | 无独立 `verify` / acceptance report；metrics 无门槛与总体判定 | `evaluate.py` 只写诊断数值（[evaluate.py L85-L119](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/evaluate.py#L85-L119)），无 M1–M4 公式 ID、threshold、pass/fail、guards、`overall.pass`、`acceptance-report.json`；无 CI 独立 oracle |
| P1-7 | 只有组合样例，缺 S1–S4/H1–H5/N0 | [synthetic.py L18-L31](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/synthetic.py#L18-L31) 只有一个组合场景（defect + ghost + flicker），无 S4 外观漂移、无单项 clip、无留出集、无 N0 无退化直通；R1-002 要求 S1–S5/H1–H5/N0（[acceptance L14-L24](https://github.com/Hesperr233/Structured-Vector-Motion/blob/dc2cac17ddbff6cb95d21a567e7e5f87c1f62029/docs/c-video-repair/acceptance-criteria-v0.1.md#L14-L24)） |
| P1-8 | MP4 校验容差过松 | [media.py L108-L110](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/media.py#L108-L110) 只拒绝最大帧 MAE > 8 DN，无 PSNR；R1-002 X10/X11 要求 MAE ≤ 2.0 DN、PSNR ≥ 40 dB。实际值虽满足 2.0，但守卫无法阻止 2–8 DN 的回归 |
| P1-9 | 闪烁指标仍是旧的绝对误差差分，且区域不是 B | [evaluate.py L67-L70](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/evaluate.py#L67-L70) 先取 `abs(original-clean)` 再作帧间差分；R1-002 M2 要求对带符号残差先作时间差分再取绝对值（[acceptance L28-L39](https://github.com/Hesperr233/Structured-Vector-Motion/blob/dc2cac17ddbff6cb95d21a567e7e5f87c1f62029/docs/c-video-repair/acceptance-criteria-v0.1.md#L28-L39)）。对称亮暗交替时旧指标可退化为 0；且区域使用 `appearance_xyxy`，不是固定静态背景 B |
| P1-10 | 8 秒 B 样本是未提交、`partial`、日志有失败 | `build/r1-002/motion/run-manifest.json`：`status: partial`、`exit_code: 0`、65 帧/30,057 未解决像素；`logs/motion-source.log` 与 `logs/motion-evaluate.log` 均记录错误；`.c-impl-r1` 的工作树有未提交 `media.py`/`sequence.py`/`evaluate.py` 修改。无 SHA、无复现脚本，不能作为候选 |

### P2（记录并排入后续迭代）

| ID | 问题 | 证据 |
| --- | --- | --- |
| P2-1 | ROI / 掩膜语义混用 | `repair` 的 `roi` 是整块矩形；没有独立 `repair_roi`、`evaluation_roi`、`photometric_roi`、`feather_px`、`max_changed_fraction`；2 秒 ROI 变化 75.06%，8 秒 74.82% |
| P2-2 | 缺保真/运动护栏 | 只有边缘梯度与 ROI 外零变化；缺 Q2 轮廓 IoU、Q3 质心、Q4/T3 时间匹配、T1/T2 闪烁不增、T4/T5 防冻结/重排、T7 首末帧证据 |
| P2-3 | 无 clean-reference-in-input 检测 | 只依赖 CLI 参数面与输入目录精确文件列表；没有 `CVR-E014` 检查或 `clean_reference_in_input` 字段。实验证明隔离成立，但代码未强制 |
| P2-4 | 无预运行输入/掩膜摘要与漂移检测 | [sequence.py L23-L60](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/sequence.py#L23-L60) 解码后记录 SHA；没有独立 input bundle/manifest 供运行前校验，也无 `CVR-E013` |
| P2-5 | PNG 编码器非规范 filter-0 | [sequence.py L80-L94](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/sequence.py#L80-L94) 使用 Pillow `compress_level=6`；像素身份可复现，但文件字节依赖 Pillow/zlib，不是 R1-002 的固定编码器 |
| P2-6 | 运行目录非原子发布 | [demo.py L34-L41](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/demo.py#L34-L41) 直接 `mkdir` 并写入；R1-002 要求临时目录校验后原子改名（[pipeline L124-L130](https://github.com/Hesperr233/Structured-Vector-Motion/blob/dc2cac17ddbff6cb95d21a567e7e5f87c1f62029/docs/c-video-repair/pipeline-contract-v0.1.md#L124-L130)） |
| P2-7 | 空输出目录也被拒绝 | `demo.py` 使用 `output.exists()` 而非“新或空”；契约允许新或空目录 |
| P2-8 | `metrics.json` 由 demo 先写占位 | [demo.py L88](https://github.com/Hesperr233/Structured-Vector-Motion/blob/b310f57e6a21562798f5ccad2a1897ba4d99c47e/prototype/video_restoration/demo.py#L88) 在 evaluate 前写 `metrics.json`，含 `evaluation.status: not_run`；可能被误认为已评分 |
| P2-9 | 全帧闪烁与 ROI 校正范围不一致 | 合成器全帧注入闪烁，修复只在 ROI 校正；指标区域选在 ROI 内 `appearance_xyxy`，未度量 ROI 外残留闪烁；这是 P1-2 接缝的根因之一 |
| P2-10 | 无 MP4 ingest / 音轨 / VFR / 旋转处理 | 只接受 PNG 序列；`ingest` 待实现；真实原视频保护无法验证 |
| P2-11 | `stable` 的 2 px 排除未在代码验证 | `load_masks` 只检查 stable 在 ROI 外；不验证与目标/拖影至少 2 px 距离 |
| P2-12 | 零分母处理不完整 | `evaluate._pair` 在 `before == 0` 时返回 `relative_reduction: None`，没有 `METRIC_UNDEFINED` 原因与失败状态 |
| P2-13 | Gitlink 结构与主树污染 | 主树 `.c-impl-r1`/`.review-r1` 为无 `.gitmodules` 的 gitlink；`git submodule status` fatal；克隆/CI 无法自然复现。文档分支 `dc2cac1` 已删除，主分支仍在 |
| P2-14 | 并发未提交改动使候选不稳定 | 审查期间 `.c-impl-r1` 出现 `media.py`/`sequence.py`/`evaluate.py` 未提交修改；8 秒 motion 样本由未提交代码产生；候选 SHA 与工作树内容不一致 |

### P3（非阻断建议）

| ID | 建议 |
| --- | --- |
| P3-1 | `.review-r1-baseline.log` 是缺依赖的失败基线（60 tests / 5 failures / 55 errors），建议从仓库移除或在 README 明确标注为“环境失败，不是基线” |
| P3-2 | 旧策划文档曾引用 `Gay-wushuang/Structured-Vector-Motion`，与 remote `Hesperr233/...` 不一致；`dc2cac1` 已修正，后续以实际 remote 为准 |
| P3-3 | 旧策划文档使用 `docs/video-repair/`，实际契约在 `docs/c-video-repair/`；`dc2cac1` 已统一，旧文档读者需注意 |
| P3-4 | README 第 47 行“demo 退出 0 表示输出视频通过验证”容易被理解为修复验收通过；应改为“媒体检查通过；`partial` 不是验收成功” |
| P3-5 | 8 秒 motion 样本的 `motion-source.log`/`motion-evaluate.log` 有失败记录但最终产物存在；应保留重试命令与最终成功日志，避免证据链歧义 |

## 5. 契约符合性对照（候选 b310f57 vs R1-002）

| 项目 | R1-002 要求 | 候选实现 | 结果 |
| --- | --- | --- | --- |
| 正式 CLI | `ingest`/`repair`/`export`/`evaluate`/`verify` | 只有 `synthetic`/`demo`/`evaluate` 原型入口 | **不符合** |
| 输入包 | `input/video-manifest.json`、`frames/frame-0000.png`、bundle digest、`clean_reference_in_input: false` | 六位 `000000.png`，无 manifest/bundle digest | **不符合** |
| 掩膜 | `mask-manifest.json`、`repair_roi`、`evaluation_roi`、`photometric_roi`、`feather_px`、`donor_valid` | 四组角色目录，无 manifest/ROI/feather/donor | **不符合** |
| 配置 | `config.json`、未知字段失败、`max_changed_fraction`、fallback 拒绝 | 硬编码 `PARAMETERS`，无 config | **不符合** |
| 输出 | `frames-repaired/frame-0000.png`、`before/after/comparison.mp4`、`run-manifest.json`、`metrics.json`、`acceptance-report.json`、`run.log` | `frames-original/repaired`、`original/repaired/comparison.mp4`、`manifest.json`、`metrics.json`；无 acceptance report/run.log | **不符合** |
| 状态/退出 | 阶段状态，`PARTIAL` 退出 5，仅 `verify` 的 0 为成功 | `partial` 退出 0；无 verify | **不符合** |
| 指标 | M1–M4 签名残差公式、阈值、guards、overall | 诊断数值，无公式 ID/阈值/overall | **不符合** |
| 媒体 | before/after 对照 + 逐 PTS 解码，MAE ≤ 2.0 / PSNR ≥ 40 | 有完整解码与逐帧 MAE，但命名不同、容差 8 DN | **部分符合** |
| 留出/B | S1–S5/H1–H5/N0 + 5–10 秒 FINAL B | 仅 2 秒组合 A；8 秒 B 未提交/partial | **不符合** |
| CI | 同 SHA 全量 CI + 视频验收 job | 无视频测试、无 workflow、无运行 | **不符合** |

## 6. 最终结论

**BLOCKED。**

- 风险等级：**C 验收风险高**；**SVM Core / 架构风险低**（候选未触碰 Core、Schema、Spec、tests、workflow）。
- 已确认的正面结果：2 秒 A 原型的媒体、隔离、D0 确定性和数值复算均可在本地独立复现；429 项全部回归通过；Ruff/Pyright/compileall 通过；没有 Document/Revision 污染。
- 阻断项：
  1. 无同 SHA CI、无 C 测试、无视频 workflow；GitHub Actions 运行总数为 0。
  2. R1-002 契约未冻结；审查期间文档从 `5561d8b` 推进到 `dc2cac1`；候选内嵌 `6b9a304`，不能作为 R1-002 候选。
  3. 唯一候选 `b310f57` 是 2 秒 A 原型；正式 CLI、B 5–10 秒 FINAL 交付未提交；8 秒样本 `partial`、未提交、日志有失败。
  4. `PARTIAL` 运行退出 0，形成虚假成功信号。
  5. 全局闪烁 + ROI 内校正产生可见矩形接缝（U3 视觉伪影）。
  6. 缺少契约命名的媒体/Manifest、config/mask manifest/input bundle、独立 `verify` 与 acceptance report。
  7. 指标/容差仍是旧原型口径，未采用 R1-002 的签名残差与 2.0 DN / 40 dB 媒体门槛。

### 重新审查条件

1. 策划线记录 G1 冻结：文档 SHA、阈值、S1–S5/H1–H5/N0、工具环境、媒体容差。
2. 实践线提交正式实现（含 B 5–10 秒驱动）并给出唯一 Candidate SHA；不得以未提交工作树或 `build/` 产物作为候选。
3. CI 线新增 `tests/test_video_repair_*` 与视频 job，在候选 SHA 上实际运行，提供 CI Run URL；缺工具必须失败。
4. 修复 P1-1 至 P1-10，尤其是退出码、可见接缝、契约命名/Manifest、config/mask manifest、独立 verify、媒体容差、M2 签名残差。
5. 重新提交 before/after/comparison、无损帧、run-manifest、metrics、acceptance-report 与运行日志；审查线将在新的唯一 SHA 上重跑 G2/G3 的受影响部分。

### 本次审查局限

- 未做连续视频的人眼完整播放；已完成全解码、逐 PTS 校验、逐帧 MAE、ROI 检查、代表帧接触表与局部放大。最终人审仍应观看完整 before/after/comparison。
- 跨平台（Ubuntu/Windows × Python 3.11/3.12）未验证；本次仅在 Windows 上用独立环境复核。
- 8 秒 B 样本是审查期间出现的未提交本地产物，未纳入 Reviewed SHA；若其代码后续提交，需重新核对全部受影响部分。
