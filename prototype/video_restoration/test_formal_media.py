"""Direct practice-line media tests. Run with ffmpeg and ffprobe on PATH."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from .formal_media import (
    FormalMediaError,
    _frame_metrics,
    decode_input_mp4,
    export_formal_media,
    verify_media,
)


def calibration_frames() -> tuple[np.ndarray, np.ndarray]:
    """8 aligned saturated bars above a 0..63 DN dark ramp in profile A."""
    palette = np.array(
        [
            [255, 255, 255],
            [255, 255, 0],
            [0, 255, 255],
            [0, 255, 0],
            [255, 0, 255],
            [255, 0, 0],
            [0, 0, 255],
            [0, 0, 0],
        ],
        dtype=np.uint8,
    )
    before = np.zeros((24, 256, 256, 3), dtype=np.uint8)
    before[:, :128] = palette[np.arange(256) // 32]
    before[:, 128:] = (np.arange(256) // 4)[:, None]
    after = np.flip(before, axis=1).copy()
    after[1::2] = np.flip(after[1::2], axis=2)
    return before, after


class MediaNumerics(unittest.TestCase):
    def test_mse_can_fail_when_mae_passes_and_zero_is_explicit(self):
        source = np.zeros((256, 256, 3), dtype=np.uint8)
        perfect = _frame_metrics(source, source)
        self.assertTrue(perfect["passed"])
        self.assertIsNone(perfect["psnr_db"])
        self.assertEqual(perfect["psnr_status"], "POSITIVE_INFINITY")
        changed = source.copy()
        changed[:2] = 128
        panel = _frame_metrics(changed, source)
        self.assertEqual(panel["mae_dn"], 1)
        self.assertAlmostEqual(panel["mse_dn2"], 128)
        self.assertLess(panel["psnr_db"], 40)
        self.assertFalse(panel["passed"])

    def test_missing_tool_and_existing_stage(self):
        frame = np.zeros((1, 2, 2, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            with self.assertRaises(FormalMediaError) as missing:
                export_formal_media(target, frame, frame, "absent-cvr-test-ffmpeg", "ffprobe")
            self.assertEqual((missing.exception.code, missing.exception.exit_code), ("CVR-E025", 6))
            sentinel = target / "before.mp4"
            sentinel.write_bytes(b"existing")
            with self.assertRaises(FormalMediaError) as occupied:
                export_formal_media(target, frame, frame, "ffmpeg", "ffprobe")
            self.assertEqual(
                (occupied.exception.code, occupied.exception.exit_code), ("CVR-E012", 2)
            )
            self.assertEqual(sentinel.read_bytes(), b"existing")


class MediaToolReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoder = shutil.which(os.environ.get("CVR_FFMPEG", "ffmpeg"))
        cls.probe = shutil.which(os.environ.get("CVR_FFPROBE", "ffprobe"))
        if not cls.encoder or not cls.probe:
            raise unittest.SkipTest("install FFmpeg/ffprobe or set CVR_FFMPEG and CVR_FFPROBE")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.stage = cls.root / "calibration"
        cls.stage.mkdir()
        cls.before, cls.after = calibration_frames()
        cls.report = export_formal_media(cls.stage, cls.before, cls.after, cls.encoder, cls.probe)

    def ffmpeg(self, *args: str) -> None:
        subprocess.run(
            [self.encoder, "-v", "error", "-nostdin", "-xerror", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            timeout=60,
        )

    def test_color_bars_dark_ramp_and_full_decode_progress(self):
        self.assertEqual(self.report["status"], "VERIFIED")
        for name, media in self.report["files"].items():
            check = media["verification"]
            self.assertTrue(check["faststart"])
            self.assertEqual(check["stream_count"], 1)
            self.assertEqual(check["stream"]["color_transfer"], "iec61966-2-1")
            self.assertEqual(check["stream"]["color_range"], "tv")
            self.assertEqual(check["stream"]["color_space"], "bt709")
            self.assertEqual(check["stream"]["color_primaries"], "bt709")
            self.assertEqual(check["stream"]["pix_fmt"], "yuv420p")
            self.assertEqual(check["decode_log"]["progress"]["frame"], "24")
            self.assertEqual(check["decode_log"]["progress"]["progress"], "end")
            self.assertEqual([f["stream_pts"] for f in check["frames"]], list(range(24)))
            self.assertEqual(check["frames"][-1]["presentation_pts"], "23/12")
            expected = (
                {"left_before", "right_after"} if name == "comparison.mp4" else {Path(name).stem}
            )
            for frame in check["frames"]:
                self.assertEqual(set(frame["panels"]), expected)
                for panel in frame["panels"].values():
                    self.assertLessEqual(panel["mae_dn"], 2)
                    self.assertGreaterEqual(panel["psnr_db"], 40)
        self.assertNotIn(str(self.root), json.dumps(self.report))
        rgb, info = decode_input_mp4(self.stage / "before.mp4", self.encoder, self.probe)
        self.assertEqual(
            info["frame_mapping"][-1],
            {"index": 23, "source_pts": 23, "presentation_pts": "23/12", "tick": 23},
        )
        self.assertEqual(info["audio_streams_dropped"], [])
        np.testing.assert_allclose(rgb[:, 32, 16::32, :], self.before[:, 32, 16::32, :], atol=2)
        np.testing.assert_allclose(rgb[:, 200, 2::4, :], self.before[:, 200, 2::4, :], atol=2)

    def test_wrong_right_half_rejected_at_exact_positions_with_unchanged_pts(self):
        stage = self.root / "swapped"
        stage.mkdir()
        wrong_after = self.after.copy()
        wrong_after[[6, 7]] = self.after[[7, 6]]
        changed = export_formal_media(stage, self.before, wrong_after, self.encoder, self.probe)
        self.assertEqual(changed["status"], "VERIFIED")
        shutil.copyfile(self.stage / "after.mp4", stage / "after.mp4")
        replay = verify_media(stage, self.before, self.after, self.encoder, self.probe)
        self.assertEqual((replay["status"], replay["code"]), ("FAILED", "CVR-E028"))
        self.assertEqual(replay["files"]["before.mp4"]["status"], "VERIFIED")
        self.assertEqual(replay["files"]["after.mp4"]["status"], "VERIFIED")
        frames = replay["files"]["comparison.mp4"]["verification"]["frames"]
        self.assertTrue(all(f["panels"]["left_before"]["passed"] for f in frames))
        self.assertEqual(
            [f["index"] for f in frames if not f["panels"]["right_after"]["passed"]], [6, 7]
        )
        self.assertEqual([f["stream_pts"] for f in frames], list(range(24)))

    def test_subsampled_chroma_failures_keep_every_file_and_frame(self):
        stage = self.root / "chroma-loss"
        stage.mkdir()
        colors = np.array([[255, 0, 255], [0, 255, 0]], dtype=np.uint8)
        stripes = np.broadcast_to(colors[np.arange(256) % 2], self.before.shape)
        report = export_formal_media(stage, self.before, stripes, self.encoder, self.probe)
        self.assertEqual((report["status"], report["code"]), ("FAILED", "CVR-E028"))
        for name in ("before.mp4", "after.mp4", "comparison.mp4"):
            self.assertTrue((stage / name).is_file())
            self.assertEqual(len(report["files"][name]["verification"]["frames"]), 24)
        panels = report["files"]["comparison.mp4"]["verification"]["frames"][-1]["panels"]
        self.assertTrue(panels["left_before"]["passed"])
        self.assertFalse(panels["right_after"]["passed"])

    def test_restricted_audio_drop_and_unsupported_transfer(self):
        self.ffmpeg(
            "-i",
            "calibration/before.mp4",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-t",
            "2",
            "audio.mp4",
        )
        with self.assertRaises(FormalMediaError) as failure:
            decode_input_mp4(self.root / "audio.mp4", self.encoder, self.probe)
        self.assertEqual(failure.exception.code, "CVR-E016")
        rgb, permitted = decode_input_mp4(
            self.root / "audio.mp4", self.encoder, self.probe, drop_audio=True
        )
        self.assertEqual(permitted["audio_streams_dropped"], [1])
        self.assertEqual(rgb.shape, self.before.shape)
        self.ffmpeg(
            "-i",
            "calibration/before.mp4",
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_metadata=transfer_characteristics=1",
            "-color_trc",
            "bt709",
            "wrong-transfer.mp4",
        )
        with self.assertRaises(FormalMediaError) as failure:
            decode_input_mp4(self.root / "wrong-transfer.mp4", self.encoder, self.probe)
        self.assertEqual(failure.exception.code, "CVR-E015")
