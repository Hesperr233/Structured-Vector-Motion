"""Practice-side regressions for partial outcomes, prefix causality and publication."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from . import formal_bundle as b
from . import formal_pipeline as pipeline
from .__main__ import main
from .causal_repair import restore
from .formal_scoring import development_guards
from .sequence import write_json


class CausalTests(unittest.TestCase):
    def setUp(self):
        self.frames = np.full((5, 16, 16, 3), 80, dtype=np.uint8)
        self.local = np.zeros((5, 16, 16), dtype=bool)
        self.local[3, 4, 4] = True
        self.frames[3, 4, 4] = 120
        stable = np.zeros_like(self.local)
        stable[:, :2, :2] = True
        self.masks = {
            "repair": self.local,
            "defect": self.local,
            "donor_valid": ~self.local,
            "static_sample": stable,
        }
        self.manifest = {"repair_roi": [0, 0, 16, 16]}
        self.config = b.default_config("test", [0, 0, 8, 8])
        self.config["photometric"]["enabled"] = False

    def test_later_frames_never_change_a_prefix(self):
        fixed, reports = restore(self.frames, self.masks, self.manifest, self.config)
        self.assertTrue((fixed[3, 4, 4] == 80).all())
        self.assertEqual(reports[3]["evidence"]["past_window"], [0, 1, 2])
        self.frames[4] = 13
        self.masks["donor_valid"][4] = False
        again, rerun = restore(self.frames, self.masks, self.manifest, self.config)
        np.testing.assert_array_equal(again[:4], fixed[:4])
        self.assertEqual(rerun[:4], reports[:4])

    def test_occluded_or_inconsistent_past_donors_leave_original_rgb(self):
        self.frames[0, 4, 4] = 30
        for exclude in (False, True):
            if exclude:
                self.masks["donor_valid"][:2, 4, 4] = False
            fixed, rows = restore(self.frames, self.masks, self.manifest, self.config)
            self.assertEqual(rows[3]["status"], "UNRESOLVED")
            self.assertEqual(rows[3]["unresolved_pixels"], 1)
            np.testing.assert_array_equal(fixed[3, 4, 4], self.frames[3, 4, 4])

    def test_global_exposure_is_not_corrected_inside_a_small_box(self):
        self.frames[2] = 86
        self.config["photometric"]["enabled"] = True
        after, rows = restore(self.frames, self.masks, self.manifest, self.config)
        np.testing.assert_array_equal(after[2], self.frames[2])
        self.assertIn("correction_would_create_boundary_seam", rows[2]["evidence"]["reasons"])

    def test_foreground_freeze_and_photometric_seam_are_detected_offline(self):
        clean = np.zeros((3, 16, 16, 3), dtype=np.uint8)
        contour = np.zeros(clean.shape[:3], bool)
        for i in range(3):
            contour[i, 4:9, 4 + i : 8 + i] = True
            clean[i, contour[i]] = 190
        box = b.rectangle([2, 2, 13, 13], 16, 16)
        tampered = clean.copy()
        tampered[1] = tampered[0]
        tampered[2, box] += 5
        guards = development_guards(clean, tampered, clean, contour, box)
        self.assertEqual(guards["DEV_AUTHORED_MOTION"]["status"], "FAILED")
        self.assertEqual(guards["DEV_EDGE_AND_SEAM"]["status"], "FAILED")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        pixels = np.full((24, 256, 256, 3), 90, np.uint8)
        pixels[0, 36:38, 36:38] = 40
        active = np.zeros(pixels.shape[:3], bool)
        active[0, 36:38, 36:38] = True
        stable = np.zeros((256, 256), bool)
        stable[:4, :4] = True
        b.make_video(self.root / "source", pixels, "synthetic-controlled", "test", "test-profile")
        b.make_masks(
            self.root / "masks",
            {"repair": active, "defect": active, "donor_valid": ~active, "static_sample": stable},
            [0, 0, 60, 60],
        )
        config = b.default_config("test", [0, 0, 60, 60])
        config["photometric"]["enabled"] = False
        write_json(self.root / "config.json", config)
        self.run_dir = self.root / "run"
        self.ingest = ["ingest", "--source", self.root / "source", "--output", self.root / "input"]
        self.repair = [
            "repair",
            "--input",
            self.root / "input",
            "--masks",
            self.root / "masks",
            "--config",
            self.root / "config.json",
            "--output",
            self.run_dir,
        ]
        self.sha = patch.object(pipeline, "code_sha", return_value="a" * 40)
        self.sha.start()
        self.addCleanup(self.sha.stop)

    def call(self, args):
        return main([str(value) for value in args])

    def manifest(self):
        return json.loads((self.run_dir / "run-manifest.json").read_bytes())

    def test_real_stage_exit_codes_and_fresh_terminal_directory(self):
        self.assertEqual(self.call(self.ingest), 0)
        self.assertEqual(self.call(self.repair), 5)
        m = self.manifest()
        self.assertEqual((m["status"], m["exit_code"], m["unresolved_pixels"]), ("PARTIAL", 5, 4))
        self.assertEqual(m["frames"][0]["status"], "UNRESOLVED")
        self.assertEqual(self.call(["export", "--run", self.run_dir]), 5)
        self.assertEqual(self.manifest(), m)
        self.assertEqual(self.call(self.repair), 2)
        self.assertEqual(self.manifest(), m)
        self.assertEqual(
            self.call(
                [
                    "export",
                    "--run",
                    self.run_dir,
                    "--diagnostic",
                    "--encoder",
                    "definitely-absent-cvr-encoder",
                ]
            ),
            6,
        )
        self.assertEqual(self.manifest()["status"], "FAILED")
        self.assertFalse((self.run_dir / "acceptance-report.json").exists())

    def test_input_tamper_and_reference_leak_are_rejected_before_publication(self):
        self.assertEqual(self.call(self.ingest), 0)
        (self.root / "input/clean").mkdir()
        self.assertEqual(self.call(self.repair), 3)
        self.assertFalse(self.run_dir.exists())
        (self.root / "input/clean").rmdir()
        p = self.root / "masks/mask-manifest.json"
        m = json.loads(p.read_bytes())
        m["roles"]["repair"]["files"][0]["pixel_sha256"] = "sha256:" + "0" * 64
        write_json(p, m)
        self.assertEqual(self.call(self.repair), 3)
        self.assertFalse(self.run_dir.exists())

    def test_interrupted_publish_recovers_only_owned_files(self):
        run = self.root / "publish"
        run.mkdir()
        staging = self.root / "staging"
        staging.mkdir()
        m = {"last_stage": "repair", "run_id": "run:test", "stages": {}, "status": "REPAIRED"}
        write_json(run / "run-manifest.json", m)
        for n in ("before.mp4", "after.mp4"):
            (staging / n).write_bytes(n.encode())
        original = Path.replace

        def interrupt(path, target):
            if path.parent == staging and path.name == "after.mp4":
                raise KeyboardInterrupt()
            return original(path, target)

        with patch.object(Path, "replace", interrupt), self.assertRaises(KeyboardInterrupt):
            pipeline.publish(run, staging, m, "export")
        self.assertEqual(b.json_file(run / "run-manifest.json")["status"], "REPAIRED")
        for n in ("before.mp4", "after.mp4"):
            (staging / n).write_bytes(n.encode())
        pipeline.publish(run, staging, m, "export")
        (run / "before.mp4").write_bytes(b"unowned replacement")
        (staging / "before.mp4").write_bytes(b"candidate")
        with self.assertRaisesRegex(b.StageError, "unowned"):
            pipeline.publish(run, staging, m, "export")
        self.assertEqual((run / "before.mp4").read_bytes(), b"unowned replacement")


if __name__ == "__main__":
    unittest.main()
