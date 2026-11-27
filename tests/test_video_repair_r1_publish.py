import json
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import video_repair_r1_publish as publisher


class IndependentPublicationTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name)
        self.manifest_path = self.run_dir / "run-manifest.json"
        self.receipt = self.run_dir / "acceptance-report.json"
        files = {name: {"sha256": name} for name in ("before.mp4", "after.mp4", "comparison.mp4")}
        identity = {
            "run_id": "run:test",
            "semantic_digest": "semantic",
            "metrics_sha256": "metrics",
            "clip_id": "H1",
            "profile_id": "cvr-holdout-a@1",
        }
        self.manifest = {
            **identity,
            "last_stage": "evaluate",
            "status": "SCORED",
            "code_sha": "implementation",
            "unresolved_pixels": 0,
            "media": {"files": files},
            "contract_version": "c-video-repair-contract@0.1",
            "draft_revision": "R1-002",
            "stages": {},
        }
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.case = {
            **identity,
            "overall": "PASS",
            "reference_manifest_sha256": "reference",
            "media": {"status": "PASS", "files": files},
            "checks": {"scoring": {"status": "PASS"}},
            "metrics": {"M1": {"status": "PASS"}},
            "guardrails": {"Q1": {"status": "PASS"}},
        }
        self.report = {
            "qualification": "FROZEN",
            "implementation_sha": "implementation",
            "ci_sha": "oracle",
            "contract_sha": "contract",
            "freeze_sha256": "policy",
            "holdout_seal_sha256": "seal",
        }

    def test_draft_and_mismatched_case_cannot_publish(self):
        with self.assertRaisesRegex(ValueError, "frozen case"):
            publisher.publish_verified_run(
                self.run_dir, self.case, {**self.report, "qualification": "DRAFT"}
            )
        with self.assertRaisesRegex(ValueError, "no longer matches"):
            publisher.publish_verified_run(
                self.run_dir, {**self.case, "semantic_digest": "different"}, self.report
            )
        self.assertFalse(self.receipt.exists())
        self.assertEqual(json.loads(self.manifest_path.read_bytes()), self.manifest)

    def test_interrupted_manifest_commit_only_resumes_matching_receipt(self):
        replace = publisher._replace_json

        def interrupt(path: Path, data: bytes):
            if path == self.manifest_path:
                raise OSError("simulated publication interruption")
            replace(path, data)

        with (
            patch.object(publisher, "_replace_json", interrupt),
            self.assertRaisesRegex(OSError, "interruption"),
        ):
            publisher.publish_verified_run(self.run_dir, self.case, self.report)
        self.assertEqual(json.loads(self.manifest_path.read_bytes()), self.manifest)
        receipt = self.receipt.read_bytes()
        with self.assertRaisesRegex(ValueError, "different acceptance report"):
            publisher.publish_verified_run(
                self.run_dir, self.case, {**self.report, "ci_sha": "other"}
            )
        publisher.publish_verified_run(self.run_dir, self.case, self.report)
        result = json.loads(self.manifest_path.read_bytes())
        self.assertEqual(
            (result["last_stage"], result["status"], result["exit_code"]), ("verify", "SUCCESS", 0)
        )
        self.assertEqual(
            result["acceptance_report_sha256"], "sha256:" + sha256(receipt).hexdigest()
        )
