"""Explicit integration driver for the pinned R1-001 prototype, with no product imports."""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image
from test_video_repair_metrics import scores

SHA = "b310f57e6a21562798f5ccad2a1897ba4d99c47e"
CONTRACT = "c-video-repair-contract@0.1/R1-002-DRAFT"
PROFILE = "svm-c-r1-restoration@1"
SIZE = 256
COUNT = 24
FPS = 12
NAMES = [f"{index:06d}.png" for index in range(COUNT)]
MEDIA = ("original.mp4", "repaired.mp4", "comparison.mp4")


def require(condition, label):
    if not condition:
        raise ValueError(label)


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def load_frames(root, mode):
    require(sorted(path.name for path in root.iterdir()) == NAMES, "frame inventory")
    frames = []
    records = []
    for tick, name in enumerate(NAMES):
        path = root / name
        data = path.read_bytes()
        require(data[24:29] == bytes((8, 2 if mode == "RGB" else 0, 0, 0, 0)), "PNG IHDR")
        with Image.open(path) as image:
            require(
                (image.format, image.mode, image.size) == ("PNG", mode, (SIZE, SIZE)),
                "decoded PNG format",
            )
            samples = np.asarray(image)
            records.append(
                {
                    "file": name,
                    "tick": tick,
                    "sha256": digest(data),
                    "pixel_sha256": digest(samples.tobytes()),
                }
            )
            frames.append(samples)
    return np.stack(frames), records


def invoke(argv, cwd):
    return subprocess.run(argv, cwd=cwd, capture_output=True, timeout=75, check=False)


def tool_output(tool, cwd, *args):
    result = invoke([tool, *args], cwd)
    require(result.returncode == 0, f"{Path(tool).stem} failed (exit {result.returncode})")
    return result.stdout


def verify_media(run, report, before, after, ffmpeg, ffprobe):
    results = {}
    for name, sources in zip(MEDIA, ((before,), (after,), (before, after)), strict=True):
        width = SIZE * len(sources)
        metadata = json.loads(
            tool_output(
                ffprobe, run, "-v", "error", "-show_streams", "-show_frames", "-of", "json", name
            )
        )
        (stream,) = metadata["streams"]
        require(stream["codec_type"] == "video", f"{name}: expected zero audio tracks")
        require(
            (stream["width"], stream["height"], stream["codec_name"], stream["pix_fmt"])
            == (width, SIZE, "h264", "yuv420p"),
            f"{name}: video properties",
        )
        require(
            (stream["color_space"], stream["color_range"], stream["color_transfer"])
            == ("bt709", "tv", "bt709"),
            f"{name}: R1-001 color metadata",
        )
        require(Fraction(stream["avg_frame_rate"]) == FPS, f"{name}: average FPS")
        require(Fraction(stream["duration"]) == Fraction(COUNT, FPS), f"{name}: duration")
        timestamps = [
            int(frame["best_effort_timestamp"]) * Fraction(stream["time_base"])
            for frame in metadata["frames"]
        ]
        require(timestamps == [Fraction(i, FPS) for i in range(COUNT)], f"{name}: PTS")
        data = tool_output(
            ffmpeg,
            run,
            "-v",
            "error",
            "-xerror",
            "-i",
            name,
            "-vf",
            "scale=in_color_matrix=bt709:in_range=tv:out_range=full,format=rgb24",
            "-fps_mode",
            "passthrough",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "pipe:1",
        )
        require(len(data) == COUNT * SIZE * width * 3, f"{name}: full RGB decode")
        frames = np.frombuffer(data, dtype=np.uint8).reshape(COUNT, SIZE, width, 3)
        panels = []
        for index, source in enumerate(sources):
            error = frames[:, :, index * SIZE : (index + 1) * SIZE].astype(np.int16) - source
            mae = np.abs(error).mean(axis=(1, 2, 3))
            mse = np.square(error, dtype=np.int32).mean(axis=(1, 2, 3))
            require(np.max(mae) <= 8.0, f"{name}: R1-001 RGB MAE ceiling, panel {index}")
            panels.append(
                {
                    "max_frame_mae_dn": float(np.max(mae)),
                    "min_frame_psnr_db": 10 * math.log10(255**2 / np.max(mse))
                    if np.any(mse)
                    else None,
                }
            )
        require(
            report["files"][name]["sha256"] == digest((run / name).read_bytes()),
            f"{name}: file digest",
        )
        results[name] = panels
    return results


def compatibility(candidate, output, ffmpeg, ffprobe):
    revision = tool_output("git", candidate, "rev-parse", "HEAD").decode().strip()
    require(revision == SHA, "unexpected implementation SHA")
    require(not tool_output("git", candidate, "status", "--porcelain"), "dirty implementation tree")
    versions = {
        name: tool_output(binary, candidate, "-version").decode().splitlines()[0]
        for name, binary in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe))
    }
    report = {
        "implementation_sha": SHA,
        "contract": CONTRACT,
        "contract_docs_sha": "dc2cac17ddbff6cb95d21a567e7e5f87c1f62029",
        "profile": PROFILE,
        "gate": "BLOCKED",
        "gate_reason": "Contract is DRAFT. Compatibility tests use development inputs only.",
        "checks": {},
        "tools": versions,
    }

    def record(name, fn):
        try:
            value = fn()
        except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
            reason = str(exc) if type(exc) is ValueError else type(exc).__name__
            report["checks"][name] = {"status": "FAIL", "diagnostic": reason}
            return False
        report["checks"][name] = {"status": "PASS", "detail": value}
        return True

    with tempfile.TemporaryDirectory(prefix="cvr-") as tmp:
        root = Path(tmp)
        fixture = root / "fixture"
        run, repeat = root / "run", root / "repeat"

        def cli(module, *args):
            return invoke(
                [sys.executable, "-m", f"prototype.video_restoration.{module}", *map(str, args)],
                candidate,
            )

        def repair(target, inputs=None, masks=None, encoder=None):
            return cli(
                "demo",
                "--input",
                inputs or fixture / "input",
                "--mask",
                masks or fixture / "mask",
                "--output",
                target,
                "--ffmpeg",
                encoder or ffmpeg,
                "--ffprobe",
                ffprobe,
            )

        def generate():
            require(
                cli("synthetic", "--output", fixture, "--seed", "20261109").returncode == 0,
                "developer fixture generation",
            )
            (fixture / "evaluation").rename(root / "offline")

        def valid():
            require(repair(run).returncode == 0, "candidate demo returned nonzero")
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            require(manifest["profile"] == PROFILE, "prototype profile")
            require(manifest["media"]["status"] == "verified", "prototype export state")
            return manifest["status"]

        def verify_png():
            before, input_records = load_frames(fixture / "input", "RGB")
            original, original_records = load_frames(run / "frames-original", "RGB")
            after, repaired_records = load_frames(run / "frames-repaired", "RGB")
            require(np.array_equal(before, original), "original PNG pixels differ from input")
            roi, _ = load_frames(fixture / "mask/roi", "L")
            require(np.array_equal(before[roi == 0], after[roi == 0]), "pixels changed outside ROI")
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            for key, records in (
                ("inputs", input_records),
                ("frames_original", original_records),
                ("frames_repaired", repaired_records),
            ):
                require(manifest[key] == records, f"manifest {key} digest or frame order")
            for role in ("roi", "stable", "ghost", "defect"):
                _, records = load_frames(fixture / "mask" / role, "L")
                require(manifest["masks"][role] == records, f"Mask digest or frame order: {role}")
            return before, after, manifest

        def media():
            before, after, manifest = verify_png()
            return verify_media(run, manifest["media"], before, after, ffmpeg, ffprobe)

        def evaluate():
            before, after, _ = verify_png()
            clean, _ = load_frames(root / "offline/clean", "RGB")
            masks = {
                label: load_frames(path, "L")[0] != 0
                for label, path in {
                    "damage": fixture / "mask/defect",
                    "ghost": fixture / "mask/ghost",
                    "interior": root / "offline/interior",
                }.items()
            }
            background = np.zeros((SIZE, SIZE), dtype=bool)
            background[165:190, 65:185] = True
            masks["background"] = background
            edge = load_frames(root / "offline/edge", "L")[0] != 0
            return {
                key: {
                    **scores(clip, clean, **masks),
                    "legacy_edge_mae_dn": float(np.abs(clip.astype(np.int16) - clean)[edge].mean()),
                }
                for key, clip in (("input", before), ("output", after))
            }

        def repeat_run():
            require(repair(repeat).returncode == 0, "repeat demo returned nonzero")
            for directory in ("frames-original", "frames-repaired"):
                require(
                    snapshot(run / directory) == snapshot(repeat / directory),
                    f"{directory}: different PNGs",
                )
            first = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            second = json.loads((repeat / "manifest.json").read_text(encoding="utf-8"))
            for key in ("inputs", "masks", "parameters", "frame_reports", "frames_repaired"):
                require(first[key] == second[key], f"repeat manifest field {key}")

        if record("development_fixture", generate):
            original = snapshot(fixture)
            if record("legacy_demo", valid):
                record("lossless_png_hashes_and_roi", lambda: verify_png()[2]["frame_count"])
                record("three_media_complete_decode", media)
                record("independent_draft_diagnostics", evaluate)
                offline = root / "offline"
                require(offline.resolve().parent == root.resolve(), "offline directory moved")
                shutil.rmtree(offline)
                record("repeat_after_reference_removal", repeat_run)
                record(
                    "refuse_source_as_output",
                    lambda: require(
                        repair(fixture / "input").returncode == 2, "source alias accepted"
                    ),
                )
            else:
                for name in (
                    "lossless_png_hashes_and_roi",
                    "three_media_complete_decode",
                    "independent_draft_diagnostics",
                    "repeat_after_reference_removal",
                    "refuse_source_as_output",
                ):
                    report["checks"][name] = {"status": "BLOCKED", "requires": "legacy_demo"}

            def invalid(name, edit, *, mask_case=False):
                copy = root / name
                source = fixture / ("mask" if mask_case else "input")
                shutil.copytree(source, copy)
                edit(copy)
                target = root / f"out-{name}"
                kwargs = {"masks" if mask_case else "inputs": copy}
                require(repair(target, **kwargs).returncode == 2, f"{name}: expected exit 2")
                require(not target.exists(), f"{name}: invalid input published output")

            record("missing_frame", lambda: invalid("missing", lambda p: (p / NAMES[0]).unlink()))
            record(
                "extra_index",
                lambda: invalid("extra", lambda p: shutil.copyfile(p / NAMES[0], p / "000024.png")),
            )
            record(
                "corrupt_png",
                lambda: invalid("corrupt", lambda p: (p / NAMES[0]).write_bytes(b"corrupt PNG")),
            )
            record(
                "bad_resolution",
                lambda: invalid("size", lambda p: Image.new("RGB", (32, 32)).save(p / NAMES[0])),
            )
            for color in (0, 255):

                def overwrite_roi(path, value=color):
                    for name in NAMES:
                        Image.new("L", (SIZE, SIZE), value).save(path / "roi" / name)

                record(
                    f"roi_all_{color}",
                    lambda label=f"roi-{color}": invalid(label, overwrite_roi, mask_case=True),
                )

            def out_of_roi(path):
                Image.new("L", (SIZE, SIZE), 255).save(path / "defect" / NAMES[0])

            record("defect_outside_roi", lambda: invalid("outside", out_of_roi, mask_case=True))

            def tool_failure(name, encoder):
                target = root / name
                require(repair(target, encoder=encoder).returncode == 6, "expected tool exit 6")
                state = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
                require(state["status"] == "failed", "tool failure published success")
                require(
                    all(not (target / file).exists() for file in MEDIA), "media leaked on failure"
                )

            record("missing_encoder", lambda: tool_failure("missing-tool", str(root / "absent")))
            record("failing_encoder", lambda: tool_failure("failed-tool", sys.executable))
            record(
                "source_bytes_unchanged",
                lambda: require(snapshot(fixture) == original, "source/Mask file changed"),
            )
        if (run / "comparison.mp4").is_file():
            for name in MEDIA:
                shutil.copyfile(run / name, output / name)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        result = compatibility(
            args.candidate.resolve(), args.output.resolve(), args.ffmpeg, args.ffprobe
        )
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        result = {
            "implementation_sha": SHA,
            "contract": CONTRACT,
            "contract_docs_sha": "dc2cac17ddbff6cb95d21a567e7e5f87c1f62029",
            "gate": "BLOCKED",
            "checks": {"setup": {"status": "FAIL", "diagnostic": type(error).__name__}},
        }
    (args.output / "compatibility.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    sys.exit(any(check["status"] != "PASS" for check in result["checks"].values()))
