# C 板块 CI 基线记录 R1-001

- 日期为 2026-11-09（Asia/Shanghai）。
- 基线 SHA 为 `cf4815357909f4e462f26619d33e6bfcb1e16236`。首次基线运行从原工作区的该 SHA 启动。发现共享工作区被其他工作线切换后，又从同一 SHA 分离检出复跑。交付分支位于独立克隆，另从测试代码提交 `3efe8f657805de1eb09923712063444fbed61949` 建立 ASCII 路径验证工作树。
- Codex 会话标识为 GPT-6。精确子型号、实际推理强度和当前会话可选档位未暴露。Medium 未核实，本轮未切换配置。
- Windows NT 10.0.26200.0 x64，PowerShell，Python 3.12.14，虚拟环境 pip 25.0.1，Node.js 24.22.1，Git 2.56.0.windows.4。Ruff 0.17.4、Pyright 1.1.414、Pillow 12.3.0、NumPy 2.3.5、OpenCV 4.14.0.94、Shapely 2.1.2。
- 初始 PATH 没有 Python 或 FFmpeg。用 Codex 内置 Python 执行 `python -m venv --system-site-packages .venv`，再用虚拟环境执行 `python -m pip install -e ".[dev,geometry,svg,trace,analysis]"`，均退出 0。初次直接用绝对路径调用 Pyright 时退出 1，报告找不到依赖。将虚拟环境 Scripts 与内置 Node 加入 PATH 后复跑，类型检查通过。
- 本地 FFmpeg/ffprobe 8.0.1 essentials build 通过临时工具目录加入 PATH。安装命令为 `python -m pip install --target <temp>/svm-c-ffmpeg-tools static-ffmpeg==3.0`，再通过 `static_ffmpeg.add_paths()` 获取二进制。工具只用于本地验证，项目依赖保持原配置。

| 实际命令 | 基线 | 交付分支 |
| --- | --- | --- |
| `python -m ruff format --check .` | 退出 0，198 个文件格式正确 | 退出 0，204 个文件格式正确 |
| `python -m ruff check .` | 退出 0 | 退出 0 |
| `python -m pyright` | 退出 0，0 errors、0 warnings | 退出 0，0 errors、0 warnings |
| `python -m compileall -q svm tests` | 退出 0 | 退出 0 |
| `python -m unittest discover -s tests -v` | ASCII 路径退出 0，429 通过、0 跳过（380.123 秒） | ASCII 路径退出 0，433 通过、0 跳过（391.879 秒） |
| `python -m unittest discover -s tests -p test_fixture.py -v` | 无此测试 | 退出 0，4 通过 |
| `python -m video_restoration.fixture <new-temp-root> --scenario combined`，`PYTHONPATH=tests` | 无此入口 | 退出 0，24 帧输入、24 帧参考和单张 Mask |
| `python -m unittest video_restoration.media_smoke -v`，`PYTHONPATH=tests` | 无此测试 | 初次缺 FFmpeg，退出 1，1 通过、1 错误。加工具后退出 0，2 通过，24 帧逐帧解码校验 |
| `python -m unittest discover -s tests -p test_editor_server.py -v` | 分离检出专项复跑退出 0，6 通过 | 未单独运行 |
| `python -m unittest discover -s tests -p test_raster_geometry_observations.py -v` | 未单独运行 | ASCII 路径退出 0，2 通过 |
| GitHub Actions 四格矩阵和新增实验作业 | 未运行 | 未运行 |
| 实践线产品验收、真值隔离复跑、完整媒体人工审查 | 未运行 | 未运行，等待冻结契约及候选 SHA |

含中文路径的基线分离检出全量运行退出 1，427 通过、2 错误。除 OpenCV 路径问题外，`test_untrusted_host_is_rejected` 在服务端返回 403 后，客户端收到 `ConnectionResetError [WinError 10054]`。Editor Server 专项复跑 6 项通过。原工作区首次基线运行退出 0，429 通过。上述全量运行均无跳过。

含中文路径的交付工作树全量运行退出 1，432 通过、1 错误。已有光栅测试 `test_real_pixel_quantization_blocks_exact_s4` 的 `cv2.imread` 返回空值，导致 TypeError。将同字节 PNG 放在 ASCII 路径后，`cv2.imread` 可读，从中文路径读取原始字节再用 `cv2.imdecode` 也成功。早期与编辑并行的试跑出现了同一错误，且格式和 Lint 在文件格式化前报错。最终记录保留这些失败，测试要求保持原配置。

本地验证覆盖 Windows x64 / Python 3.12。其他现有 CI 环境及 Ubuntu FFmpeg 补丁版本留待远端运行确认。编解码自检使用生成的合成序列。视频修复改善、产品输入保护和输出 manifest 校验需由接入产品的测试另行验证。
