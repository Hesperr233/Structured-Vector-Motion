"""Independent R1-002 media checks against authoritatively ordered lossless RGB frames."""

import hashlib
import json
import math
import shutil
import subprocess
import unittest
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import numpy as np

FPS = 12
PROFILES = {(24, 256, 256, 3): "A", (96, 360, 640, 3): "B"}
MAE_LIMIT = 2.0
PSNR_LIMIT = 40.0
DURATION_TOLERANCE = Fraction(1, 100)
RGB_DECODE_FILTER = "scale=in_color_matrix=bt709:in_range=tv:out_range=full,format=rgb24"
COLOR = {
    "color_space": "bt709",
    "color_range": "tv",
    "color_primaries": "bt709",
    "color_transfer": "iec61966-2-1",
}


def _tool_output(binary: str, role: str, *arguments: str) -> bytes:
    try:
        result = subprocess.run([binary, *arguments], capture_output=True, check=False, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{role}_timeout") from exc
    except OSError as exc:
        raise ValueError(f"{role}_launch_failed") from exc
    if result.returncode != 0 or result.stderr:
        raise ValueError(f"{role}_failed_exit_{result.returncode}")
    return result.stdout


def _probe(path: Path, ffprobe: str, count: int, height: int, width: int) -> dict:
    data = json.loads(
        _tool_output(
            ffprobe,
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_frames",
            "-show_format",
            "-of",
            "json",
            str(path),
        )
    )
    streams = data.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_count = sum(stream.get("codec_type") == "audio" for stream in streams)
    video = videos[0] if len(videos) == 1 else {}
    frames = [frame for frame in data.get("frames", []) if frame.get("media_type") == "video"]
    container = data.get("format", {})
    stream_duration_ok = _duration_in_tolerance(video.get("duration"), count)
    container_duration_ok = _duration_in_tolerance(container.get("duration"), count)
    pts_ok = False
    rate_ok = False
    try:
        rate_ok = Fraction(video["avg_frame_rate"]) == FPS
        time_base = Fraction(video["time_base"])
        pts_ok = len(frames) == count and all(
            Fraction(frame["pts"]) * time_base == Fraction(index, FPS)
            for index, frame in enumerate(frames)
        )
    except (KeyError, ValueError, ZeroDivisionError, TypeError):
        pass
    return {
        "container_mp4": "mp4" in container.get("format_name", "").split(","),
        "stream_inventory": len(streams) == len(videos) == 1 and audio_count == 0,
        "codec_and_format": video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p",
        "dimensions": (video.get("height"), video.get("width")) == (height, width)
        and all((frame.get("height"), frame.get("width")) == (height, width) for frame in frames),
        "rgb8_colorspace": {field: video.get(field) for field in COLOR} == COLOR
        and all({field: frame.get(field) for field in COLOR} == COLOR for frame in frames),
        "frame_count": len(frames) == count,
        "fps": rate_ok,
        "pts": pts_ok,
        "duration": stream_duration_ok and container_duration_ok,
        "rotation": all(entry.get("rotation", 0) == 0 for entry in video.get("side_data_list", [])),
        "observed": {
            "audio_tracks": audio_count,
            "video_tracks": len(videos),
            "video_frames": len(frames),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": video.get("avg_frame_rate"),
            "duration_seconds": container.get("duration"),
            "color": {field: video.get(field) for field in COLOR},
        },
    }


def _duration_in_tolerance(value: str | None, count: int) -> bool:
    try:
        return abs(Fraction(value) - Fraction(count, FPS)) <= DURATION_TOLERANCE
    except (ValueError, TypeError, ZeroDivisionError):
        return False


def _faststart(path: Path) -> bool:
    length = path.stat().st_size
    with path.open("rb") as stream:
        while stream.tell() < length:
            start = stream.tell()
            header = stream.read(8)
            if len(header) != 8:
                return False
            size = int.from_bytes(header[:4], "big")
            kind = header[4:]
            if size == 1:
                size = int.from_bytes(stream.read(8), "big")
            elif size == 0:
                size = length - start
            if size < stream.tell() - start or start + size > length:
                return False
            if kind in (b"moov", b"mdat"):
                return kind == b"moov"
            stream.seek(start + size)
    return False


def _compare_frame(decoded: np.ndarray, source: np.ndarray) -> dict:
    delta = np.subtract(decoded, source, dtype=np.int16)
    absolute = int(np.abs(delta).sum(dtype=np.int64))
    squared = int(np.square(delta, dtype=np.int32).sum(dtype=np.int64))
    count = source.size
    return {
        "rgb_mae_dn": absolute / count,
        "rgb_psnr_db": 10 * math.log10(255**2 * count / squared) if squared else None,
        "psnr_state": "FINITE" if squared else "POSITIVE_INFINITY",
        "mae_ok": absolute <= 2 * count,
        "psnr_ok": squared * 10_000 <= 255**2 * count,
    }


def _panel_report(frames: np.ndarray, sources: dict[str, np.ndarray]) -> dict:
    width = next(iter(sources.values())).shape[2]
    results = {}
    for panel_index, (role, source) in enumerate(sources.items()):
        decoded = frames[:, :, panel_index * width : (panel_index + 1) * width]
        reports = [
            _compare_frame(readback, original)
            for readback, original in zip(decoded, source, strict=True)
        ]
        finite = [report["rgb_psnr_db"] for report in reports if report["psnr_state"] == "FINITE"]
        results[role] = {
            "max_frame_mae_dn": max(report["rgb_mae_dn"] for report in reports),
            "min_frame_psnr_db": min(finite, default=None),
            "failed_frames": [
                index for index, row in enumerate(reports) if not (row["mae_ok"] and row["psnr_ok"])
            ],
            "frames": reports,
        }
    return results


def verify_media(
    run: Path,
    before: np.ndarray,
    after: np.ndarray,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    expected_hashes: Mapping[str, str] | None = None,
) -> dict:
    """Verify canonical MP4s and retain diagnostics even when color tags fail."""
    if before.shape not in PROFILES or after.shape != before.shape:
        raise ValueError("media sources must have matching A or B profile dimensions")
    if before.dtype != np.uint8 or after.dtype != np.uint8:
        raise ValueError("media sources must be RGB8")
    count, height, width, channels = before.shape
    decoder = shutil.which(ffmpeg)
    probe = shutil.which(ffprobe)
    report = {
        "status": "PASS",
        "profile": PROFILES[before.shape],
        "frame_count": count,
        "fps": "12/1",
        "thresholds": {"max_frame_mae_dn": MAE_LIMIT, "min_frame_psnr_db": PSNR_LIMIT},
        "files": {},
    }
    if decoder is None or probe is None:
        report["status"] = "BLOCKED"
        report["reason"] = "ffmpeg_or_ffprobe_unavailable"
        return report

    run = run.resolve()
    for name, sources in (
        ("before.mp4", {"before": before}),
        ("after.mp4", {"after": after}),
        ("comparison.mp4", {"before": before, "after": after}),
    ):
        path = run / name
        result = {"status": "PASS", "failures": [], "checks": {}, "panels": {}}
        report["files"][name] = result
        failures = result["failures"]
        checks = result["checks"]
        media_width = width * len(sources)
        if not path.is_file() or path.resolve().parent != run:
            failures.append("missing_or_outside_run")
        else:
            try:
                with path.open("rb") as stream:
                    result["sha256"] = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
                checks["faststart"] = _faststart(path)
                if expected_hashes is not None:
                    checks["digest"] = result["sha256"] == expected_hashes.get(name)
                else:
                    result["digest_verification"] = "NOT RUN"
            except OSError:
                failures.append("media_file_unreadable")

            try:
                metadata = _probe(path, probe, count, height, media_width)
                result["observed"] = metadata.pop("observed")
                checks.update(metadata)
            except (KeyError, TypeError, ValueError) as exc:
                # Emit process categories and codes only. The raw tool error may contain paths.
                reason = str(exc)
                failures.append(
                    reason if reason.startswith("ffprobe_") else "ffprobe_metadata_invalid"
                )

            try:
                data = _tool_output(
                    decoder,
                    "ffmpeg",
                    "-v",
                    "error",
                    "-xerror",
                    "-nostdin",
                    "-noautorotate",
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-vf",
                    RGB_DECODE_FILTER,
                    "-fps_mode",
                    "passthrough",
                    "-frames:v",
                    str(count + 1),
                    "-threads",
                    "1",
                    "-f",
                    "rawvideo",
                    "pipe:1",
                )
                checks["complete_decode"] = len(data) == count * height * media_width * channels
                if checks["complete_decode"]:
                    frames = np.frombuffer(data, dtype=np.uint8).reshape(
                        count, height, media_width, channels
                    )
                    result["panels"] = _panel_report(frames, sources)
                    checks["frame_error_limits"] = not any(
                        panel["failed_frames"] for panel in result["panels"].values()
                    )
                    del frames
                del data
            except ValueError as exc:
                failures.append(str(exc))
        failures.extend(check for check, passed in checks.items() if not passed)
        if failures:
            result["status"] = report["status"] = "FAIL"
    return report


class VideoRepairMediaTest(unittest.TestCase):
    def test_identical_rgb_frame_has_explicit_infinite_psnr(self) -> None:
        frame = np.full((2, 2, 3), 128, dtype=np.uint8)
        result = _compare_frame(frame, frame)
        self.assertEqual(result["rgb_mae_dn"], 0)
        self.assertIsNone(result["rgb_psnr_db"])
        self.assertEqual(result["psnr_state"], "POSITIVE_INFINITY")
        self.assertTrue(result["psnr_ok"])

    def test_comparison_panels_fail_independently_on_one_bad_frame(self) -> None:
        before = np.zeros((2, 2, 2, 3), dtype=np.uint8)
        after = np.full_like(before, 255)
        decoded = np.concatenate([before, after], axis=2)
        decoded[0, :, :2] = 2
        decoded[1, 0, 2, 0] = 245
        results = _panel_report(decoded, {"before": before, "after": after})
        self.assertEqual(results["before"]["failed_frames"], [])
        self.assertEqual(results["before"]["max_frame_mae_dn"], 2)
        self.assertEqual(results["after"]["failed_frames"], [1])
        self.assertTrue(results["after"]["frames"][1]["mae_ok"])
        self.assertFalse(results["after"]["frames"][1]["psnr_ok"])

    def test_large_rgb_delta_uses_signed_differences_and_wide_squares(self) -> None:
        original = np.full((1, 1, 3), 255, dtype=np.uint8)
        result = _compare_frame(np.zeros_like(original), original)
        self.assertEqual(result["rgb_mae_dn"], 255)
        self.assertEqual(result["rgb_psnr_db"], 0)
        self.assertFalse(result["mae_ok"])
        self.assertFalse(result["psnr_ok"])

    def test_duration_boundary_is_exact(self) -> None:
        self.assertTrue(_duration_in_tolerance("1.99", 24))
        self.assertTrue(_duration_in_tolerance("8.01", 96))
        self.assertFalse(_duration_in_tolerance("8.010001", 96))
        self.assertFalse(_duration_in_tolerance(None, 96))

    def test_probe_checks_color_and_audio_without_masking_bad_pts(self) -> None:
        video = {
            "codec_type": "video",
            "codec_name": "h264",
            "pix_fmt": "yuv420p",
            "height": 360,
            "width": 640,
            "time_base": "1/120",
            "avg_frame_rate": "12/1",
            "duration": "0.166667",
            **COLOR,
        }
        frames = [
            {
                "media_type": "video",
                "pts": index * 10,
                "height": 360,
                "width": 640,
                **COLOR,
            }
            for index in range(2)
        ]
        payload = {
            "streams": [video],
            "frames": frames,
            "format": {"duration": "0.166667", "format_name": "mov,mp4"},
        }
        with patch(f"{__name__}._tool_output", return_value=json.dumps(payload).encode()):
            result = _probe(Path("before.mp4"), "ffprobe", 2, 360, 640)
        self.assertTrue(result["pts"])
        self.assertTrue(result["rgb8_colorspace"])
        self.assertTrue(result["stream_inventory"])

        payload["streams"].append({"codec_type": "audio"})
        frames[1]["pts"] = 9
        frames[1]["color_transfer"] = "bt709"
        with patch(f"{__name__}._tool_output", return_value=json.dumps(payload).encode()):
            result = _probe(Path("before.mp4"), "ffprobe", 2, 360, 640)
        self.assertFalse(result["pts"])
        self.assertFalse(result["rgb8_colorspace"])
        self.assertFalse(result["stream_inventory"])
        self.assertEqual(result["observed"]["audio_tracks"], 1)
