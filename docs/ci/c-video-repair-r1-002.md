# C 板块 R1-002 独立验收记录

验收结论为 **BLOCKED**。本轮已运行实践候选的开发样例与 Motion B 样例，并记录违反草案门槛的实测结果。G1 的冻结文件、留出集和正式阶段入口仍缺，当前证据不足以申请 G3。

## 证据身份

| 对象 | SHA |
| --- | --- |
| 合并的 `origin/main` | `e3d9e70f0fd5f35e963ca4e7d8083bf5b8f14105` |
| CI 线同步前 | `6f3bb8bbb70fbe29a90252757da664045a1b0c0e` |
| 合并提交 | `f1efbd4c896262d12e07b3dc919a6f3d405b5fb5` |
| 最新契约文档分支快照 | `52180bd1e71b3b23b2e7cbbe2e236b05f944f2a3`，`c-video-repair-contract@0.1 / R1-002 DRAFT` |
| 最新实践远端候选 | `a8f211bea533c2101200db620fe6d8ee47b4ecf8` |
| CI 代码 | 以各工作流工件的 `ci_sha` 为准，从实际 test checkout 读取 |

fetch 后，CI 分支比 main 多 4 个独立提交、少 16 个提交。普通 merge 保留原提交和 Golden A/B 等测试。候选和文档按完整远端 SHA 分离检出，视频专项工作流在同一次运行校验实现、契约和 CI 代码的三个 SHA。最新文档分支与 main 的四份契约文件内容相同，二者都标记 DRAFT。

## 覆盖与裁决

| 范围 | 已验证 | 正式状态 |
| --- | --- | --- |
| A / S1、S2、S3、S5 | 24 帧、256 × 256 RGB8、12 fps。分别运行缺陷、闪烁、拖影和旧组合场景，重新计算 PNG/Mask 证据和逐帧媒体误差。旧合成器的 S5 没有单独的慢漂移注入。 | FAIL。原型清单和媒体属性与草案不符。S3、S5 实际以退出 0 返回 `partial`，S2、S5 的 ROI 变化比例超过 0.25。 |
| A / N0 | 用关闭全部现有退化后的独立样例检查像素不变性和媒体。 | 像素检查 PASS。正式清单和媒体检查 FAIL。 |
| A / S4、H1–H5 | 当前无独立慢漂移样例、冻结 holdout 或固定整段拖尾评分区证据。 | BLOCKED。实际样例和正式 M3 判定 NOT RUN。 |
| B / 来源 | 从实践候选重新运行 8 秒、96 帧、12 fps、640 × 360 Document。再用 MotionEvaluator FINAL 和 SVGRenderer 独立求值，重放 resvg 0.44.0，校验无 stale、SVG、栅格及掩膜。 | 来源重放 PASS。正式 B 总体 BLOCKED。 |
| B / 修复 | 重算导出帧和掩膜摘要、检查 ROI 外和输入保护。原型修复与离线 PNG 目录物理分开。 | ROI 外像素不变 PASS。修复读取的 source manifest 仍包含 clean 和评测摘要。独立记录 46 帧、20,237 个未解析像素，`partial` 退出 0。 |
| Q/T | 独立 oracle 实现轮廓 IoU、质心、邻帧对应、梯度和时间/授权护栏，并用分析样例自测。 | 当前候选缺冻结的输出轮廓分割规则、可靠供体计数和正式阶段写域记录。相应护栏 BLOCKED。B 变化比例和未解析像素检查 FAIL。 |
| 媒体 | 独立 ffprobe 和全帧 RGB 解码检查每路 PTS、帧率、尺寸、时长、零音轨、范围、传递函数、对照双半幅、SHA、逐帧 MAE 和 PSNR。阈值分别为 ≤ 2.0 DN、≥ 40 dB。 | B 三路完整回读，旧 BT.709 transfer 与草案 sRGB transfer 不符。A 部分帧也未达到逐帧误差门槛。 |
| 状态机 | 旧格式不得作正式成功，带拒绝像素的 partial 必须返回 5。旧兼容测试继续验证输入覆盖保护、非法数据、缺工具及工具失败。 | 正式 staged ingest/repair/export/verify、必选产物缺失拒绝和 SUCCESS 发布语义 NOT RUN。缺工具时报告 BLOCKED，已实测的兼容失败保留 FAIL。 |

## 复现与工件

本地使用 Windows、Python 3.12.14、新装的完整项目 extras、FFmpeg/ffprobe 9.0.2、resvg 0.44.0。Linux 以带 SHA-256 校验的固定 FFmpeg 构建和 resvg 0.44.0 验证。以下命令在 CI 工作树运行，将候选和各个新输出目录替换为实际路径。

```text
python -m pip install --upgrade pip
python -m pip install -e ".[dev,geometry,svg,trace,analysis]"
python -m ruff format --check .
python -m ruff check .
python -m pyright
python -m compileall -q svm tests
python -m unittest discover -s tests -v
python tests/test_video_repair_candidate.py --candidate <candidate> --output <compatibility>
python tests/test_video_repair_r1_synthetic.py --candidate <candidate> --output <profile-A>
python tests/test_video_repair_r1_motion.py --candidate <candidate> --output <profile-B> --resvg resvg
```

视频专项作业保留三路媒体、代表性 PNG、采样和运行 manifest、指标、验收报告与失败日志。专项门禁保留已完成的测量和未运行项，必选证据缺失时返回非零。[PR #2 的运行记录](https://github.com/Hesperr233/Structured-Vector-Motion/pull/2/checks)
