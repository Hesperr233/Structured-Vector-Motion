# C 板块 R1-001 CI 验证报告

2026-11-23（Asia/Shanghai）。Codex Windows 桌面版，会话模型为 GPT‑6.2 Sol（`gpt-6.2-sol`），实际推理强度未暴露，High 未核实。

- `git fetch origin --prune` 及 `git ls-remote --heads origin` 核对到 main 为 `cf4815357909f4e462f26619d33e6bfcb1e16236`。用独立 ASCII 路径工作树从该 SHA 执行基线。远端 CI 分支原先不存在，恢复了旧本地提交 `ad967a92c9d661ff2287c3a0579998ccc7f61f98` 及其父提交。
- 兼容性测试代码提交为 `661bd177e680798d8595120094e5cc3bcfa3aa17`。被测实现为 `b310f57e6a21562798f5ccad2a1897ba4d99c47e`，其运行 profile 为 `svm-c-r1-restoration@1`。
- 文档线 SHA `dc2cac17ddbff6cb95d21a567e7e5f87c1f62029` 的 `c-video-repair-contract@0.1 / R1-002` 为 DRAFT。草案评分仅作诊断。正式门禁处于 **BLOCKED**。

## 本地基线与分支

Windows x64 10.0.26200.0，PowerShell，Python 3.12.14，pip 26.3，Node.js 24.22.1，Git 2.56.0.windows.4。新虚拟环境使用 Ruff 0.17.6、Pyright 1.1.414、Pillow 12.3.0、NumPy 2.5.5、OpenCV 4.14.0.94、Shapely 2.1.2、svgpathtools 1.8.0、potracer 0.0.4 和 jsonschema 4.26.0。本地编码使用 FFmpeg/ffprobe 9.0.2 Gyan essentials。远端兼容作业将 NumPy 锁为 2.5.4，并校验固定 Linux FFmpeg 归档 SHA-256。两环境分别核验自身媒体。

| 命令 | 干净 main 基线 | CI 代码提交 |
| --- | --- | --- |
| `python -m venv .venv` | PASS，0 | 使用同一新虚拟环境 |
| `python -m pip install --upgrade pip` | PASS，0 | 使用同一新虚拟环境 |
| `python -m pip install -e ".[dev,geometry,svg,trace,analysis]"` | PASS，0 | 使用同一新虚拟环境 |
| `python -m ruff format --check .` | PASS，0 | PASS，0 |
| `python -m ruff check .` | PASS，0 | PASS，0 |
| `python -m pyright` | PASS，0 | PASS，0 |
| `python -m compileall -q svm tests` | PASS，0 | PASS，0 |
| `python -m unittest discover -s tests -v` | PASS，0，429 通过、0 跳过 | PASS，0，434 通过、0 跳过 |
| `python -m unittest discover -s tests -p test_video_repair_metrics.py -v` | NOT RUN，基线没有此文件 | PASS，0，1 项 |
| `python tests/test_video_repair_candidate.py --candidate <checkout> --output <new-dir> --ffmpeg <exe> --ffprobe <exe>` | NOT RUN，基线没有此文件 | PASS，0，17 项原型兼容检查 |

本轮重新安装完整依赖并执行真实验证。既有 CI 继续使用 Ubuntu 和 Windows × Python 3.11、3.12。所有旧 Golden 和测试均保留。PR 中可见的旧 CI 文档和实验工作流来自恢复的历史提交。

## 覆盖矩阵

| 类别 | 已执行证据 | 正式验收与未运行项 |
| --- | --- | --- |
| 输入 | 固定种子 20261109，24 帧 × 256 × 256 RGB8、12 fps。缺帧、额外索引、损坏 PNG、非法分辨率均退出 2。 | BLOCKED。非法输入 fps、输入摘要篡改、重复或乱序清单索引由正式 ingest 校验。合法重复像素帧用不同索引和 PTS 的回归案例 NOT RUN。 |
| Mask | 全空、全屏、缺陷授权越界均退出 2。无损帧保护域外零改动，重算四组 Mask 的文件和像素摘要。 | BLOCKED。正式越界 ROI、羽化、Mask 元数据和授权的分离检查待接入。 |
| 修复 | 独立计算开发样例的局部 MAE、静态区域残差差分、拖尾残差、运动目标内部色漂和旧样例边缘 MAE。读回原型返回的 `partial`。 | BLOCKED。S1–S5、冻结 H1–H5、N0、运动态轮廓/质心、场景拒绝和首尾的正式验收。 |
| 数据安全 | 移走评测树后首次运行，删除评测树后复跑，比较全部无损 PNG 和关键 manifest 字段。输出指向源目录退出 2，源 PNG 与 Mask 的文件摘要保持一致。缺编码器或编码器失败退出 6，无媒体发布，manifest 为 `failed`。 | BLOCKED。正式目录隔离需要只读输入和不可访问的参考挂载验证。链接别名、并发写入、中断恢复和正式 `SUCCESS` 状态机 NOT RUN。 |
| 导出 | 三路完整解码，检查 24 个展示 PTS、12 fps、时长 2 秒、H.264/yuv420p、尺寸、零音轨和文件 SHA。对照左右两半分别与对应 PNG 按帧比较。 | BLOCKED。正式色彩传递函数、逐 PTS 门槛和范围转换需要固定环境色条与暗阶验证。人工完整观看 NOT RUN。 |
| SVM Motion B | NOT RUN。 | BLOCKED。策划批准 profile 后，在固定 Document/Revision 上用 FINAL 采样，拒绝 stale、失败和缺帧。拟用 6 秒、12 fps、72 帧，实际 Motion tick 从 Timebase 做整数映射。固定 SVG 光栅化版本和画布，记录 Document/Revision、Git、SVG/PNG SHA 和时间映射。离线注入组合退化，然后逐场景评分、回读三路完整媒体并人工审查。 |

正式留出场景在 G1 登记种子、区域、轮廓规则、输入/参考 SHA 和阈值。算法开发只使用开发集。预留的门禁须按冻结合约接入独立评分，逐个场景和逐帧计算判定；无必选证据时继续报 BLOCKED。原型得分只列开发诊断。

## 已发现的问题

1. **实践线**。原型候选的组合开发样例为 `partial`，存在拒绝或未解析像素。原型导出色彩 transfer 为 BT.709，R1-002 草案指定 sRGB transfer。本地独立读回最大逐帧 RGB MAE 为 1.979 DN，最小逐帧 PSNR 为 39.03 dB，低于草案提出的 40 dB。实践线可据此验证正式导出配置。
2. **文档线和策划线**。原型四项开发数据依次为局部缺陷 73.772 → 0、光度写域内背景闪烁 13.043 → 0、旧拖尾区残差 26.610 → 8.170、目标内部外观残差标准差 7.810 → 0 DN，旧边带 MAE 6.5 → 0。原型未提供 H4 独立漂移挑战，也未提供全片物体轮廓外的冻结拖尾评分区。需定义和封存这些证据后才可启用正式阈值。
3. **CI 线**。本地验证完成于 Windows。历史中文路径 OpenCV 风险和跨平台编解码差异仍应跟进。关系到正式交付的必选项中，NOT RUN、SKIPPED、BLOCKED 或 FAIL 任一状态均阻断合并。

## 远端记录

[草稿 PR #2](https://github.com/Hesperr233/Structured-Vector-Motion/pull/2)。代码提交 `661bd177` 的原始记录保留在 [视频兼容和验收门禁](https://github.com/Hesperr233/Structured-Vector-Motion/actions/runs/45189302319) 与 [现有 CI 矩阵](https://github.com/Hesperr233/Structured-Vector-Motion/actions/runs/45189302105)。该次视频作业中，评分自检和三路解码通过，正式门禁报告 BLOCKED 并退出 1。已上传 [兼容报告、工具日志和三路 MP4](https://github.com/Hesperr233/Structured-Vector-Motion/actions/runs/45189302319/artifacts/14826580316) 及 [门禁状态](https://github.com/Hesperr233/Structured-Vector-Motion/actions/runs/45189302319/artifacts/14824238560)。报告提交会触发新记录，合并以 PR 当前提交的 Checks 为准。
