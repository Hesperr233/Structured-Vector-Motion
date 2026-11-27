"""Run independent A-profile checks on the available development prototype."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_video_repair_candidate import (
    CONTRACT,
    CONTRACT_DOCS_SHA,
    MEDIA,
    SHA,
    digest,
    invoke,
    load_frames,
    require,
    snapshot,
    tool_output,
)
from test_video_repair_r1_media import verify_media
from test_video_repair_r1_oracle import guardrail_checks, measure, metric_checks

SCENARIOS = {
    "S1": ("flicker", "ghost"),
    "S2": ("defect", "ghost"),
    "S3": ("defect", "flicker"),
    "S5": (),
    "N0": ("defect", "flicker", "ghost"),
}
HOLDOUTS = ("H1", "H2", "H3", "H4", "H5")
CANONICAL_MEDIA = ("before.mp4", "after.mp4", "comparison.mp4")


def state_checks(returncode, manifest, unresolved):
    """Check the formal release obligations even when a prototype can only report legacy state."""
    state = manifest.get("state", manifest.get("status"))
    checks = {}
    if str(state).upper() == "PARTIAL" or unresolved:
        checks["partial_exits_5"] = "PASS" if returncode == 5 else "FAIL"
    if str(state).upper() == "FAILED":
        checks["failure_nonzero"] = "PASS" if returncode else "FAIL"
    checks["no_success_from_repair"] = "FAIL" if str(state).upper() == "SUCCESS" else "PASS"
    checks["formal_manifest"] = (
        "PASS" if manifest.get("contract_version") == "c-video-repair-contract@0.1" else "FAIL"
    )
    return checks


class StateChecksTest(unittest.TestCase):
    def test_legacy_and_premature_success_cannot_pass(self):
        self.assertEqual(
            state_checks(0, {"status": "partial"}, unresolved=True),
            {
                "partial_exits_5": "FAIL",
                "no_success_from_repair": "PASS",
                "formal_manifest": "FAIL",
            },
        )
        self.assertEqual(
            state_checks(0, {"status": "SUCCESS"}, False)["no_success_from_repair"], "FAIL"
        )

    def test_formal_partial_and_failed_require_correct_exit(self):
        manifest = {"state": "PARTIAL", "contract_version": "c-video-repair-contract@0.1"}
        self.assertEqual(state_checks(5, manifest, True)["partial_exits_5"], "PASS")
        self.assertEqual(state_checks(0, {"state": "FAILED"}, False)["failure_nonzero"], "FAIL")
        self.assertEqual(state_checks(6, {"state": "FAILED"}, False)["failure_nonzero"], "PASS")


def check_scenario(candidate, output, scenario, omitted, ffmpeg, ffprobe):
    with tempfile.TemporaryDirectory(prefix=f"cvr-{scenario}-") as temp:
        root = Path(temp)
        fixture, work = root / "generated", root / "repair-io"
        work.mkdir()

        def call(module, *args):
            return invoke(
                [sys.executable, "-m", f"prototype.video_restoration.{module}", *map(str, args)],
                candidate,
            )

        options = [part for stage in omitted for part in ("--without", stage)]
        generation = call("synthetic", "--seed", "20261109", "--output", fixture, *options)
        require(generation.returncode == 0, "A development fixture generation failed")
        for name in ("input", "mask"):
            shutil.copytree(fixture / name, work / name)
        original = snapshot(work)
        run = work / "run"
        repair = call(
            "demo",
            "--input",
            work / "input",
            "--mask",
            work / "mask",
            "--output",
            run,
            "--ffmpeg",
            ffmpeg,
            "--ffprobe",
            ffprobe,
        )
        manifest_path = run / "manifest.json"
        require(manifest_path.is_file(), "repair did not publish prototype manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        unresolved = manifest.get("repair_status") == "contains_refusals"
        checks = state_checks(repair.returncode, manifest, unresolved)
        checks["source_unchanged"] = (
            "PASS"
            if all(digest((work / file).read_bytes()) == value for file, value in original.items())
            else "FAIL"
        )
        before, original_records = load_frames(work / "input", "RGB")
        reproduced, preview_records = load_frames(run / "frames-original", "RGB")
        after, repaired_records = load_frames(run / "frames-repaired", "RGB")
        roi, _ = load_frames(work / "mask/roi", "L")
        changed = (before != after).any(axis=-1)
        require(np.array_equal(before, reproduced), "before PNG differs from input")
        for label, records in (
            ("inputs", original_records),
            ("frames_original", preview_records),
            ("frames_repaired", repaired_records),
        ):
            require(manifest[label] == records, f"manifest {label} disagrees with PNGs")
        checks["outside_roi_unchanged"] = "FAIL" if np.any(changed & (roi == 0)) else "PASS"
        checks["change_fraction_at_most_0_25"] = (
            "PASS"
            if np.all(changed.sum(axis=(1, 2)) <= 0.25 * (roi != 0).sum(axis=(1, 2)))
            else "FAIL"
        )
        if scenario == "N0":
            checks["all_pixels_unchanged"] = "FAIL" if np.any(changed) else "PASS"
        clean, _ = load_frames(fixture / "evaluation/clean", "RGB")
        interior = load_frames(fixture / "evaluation/interior", "L")[0] != 0
        score_region = np.zeros((256, 256), dtype=bool)
        score_region[89:98, 180:191] = True
        background = np.zeros_like(score_region)
        background[165:190, 65:185] = True
        require(np.all(clean[:, background] == clean[0, background]), "non-static scoring patch")
        diagnostics = {
            "before": measure(
                before,
                clean,
                scoring_roi=score_region,
                background=background,
                ghost=None,
                interior=interior,
            ),
            "after": measure(
                after,
                clean,
                scoring_roi=score_region,
                background=background,
                ghost=None,
                interior=interior,
            ),
        }
        required = {
            "S1": ("M1",),
            "S2": ("M2",),
            "S3": ("M3",),
            "S5": ("M1", "M2", "M3", "M4"),
            "N0": (),
        }
        draft = metric_checks(
            diagnostics["before"],
            diagnostics["after"],
            required[scenario],
            combined=scenario == "S5",
        )
        if "M3" in required[scenario]:
            draft["M3"] = {"status": "BLOCKED", "reason": "fixed whole-clip G mask is unavailable"}
        guards = guardrail_checks(
            before,
            after,
            clean,
            roi=roi != 0,
            scoring_roi=score_region,
            background=background,
            interior=interior,
            unresolved_pixels=[
                item["ghost"]["refused_pixels"] + item["defect"]["refused_pixels"]
                for item in manifest["frame_reports"]
            ],
        )
        diagnostics.update(draft_metric_checks=draft, draft_guardrails=guards)
        target = output / scenario
        target.mkdir()
        (target / "manifest.json").write_bytes(manifest_path.read_bytes())
        shutil.copyfile(run / "frames-original/000012.png", target / "preview-before.png")
        shutil.copyfile(run / "frames-repaired/000012.png", target / "preview-after.png")
        for legacy, canonical in zip(MEDIA, CANONICAL_MEDIA, strict=True):
            shutil.copyfile(run / legacy, target / canonical)
        media = verify_media(
            target,
            before,
            after,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            expected_hashes={
                canonical: manifest["media"]["files"][legacy]["sha256"]
                for legacy, canonical in zip(MEDIA, CANONICAL_MEDIA, strict=True)
            },
        )
        if len(media["files"]) == len(CANONICAL_MEDIA) and all(
            "pts" in result["checks"] for result in media["files"].values()
        ):
            guards["T3"] = {
                "status": "PASS"
                if all(result["checks"]["pts"] for result in media["files"].values())
                else "FAIL",
            }
        (target / "metrics.json").write_text(
            json.dumps(diagnostics, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        return {
            "status": "FAIL"
            if "FAIL" in checks.values() or media["status"] == "FAIL"
            else "BLOCKED",
            "repair_exit": repair.returncode,
            "prototype_status": manifest["status"],
            "legacy_media_aliases": dict(zip(CANONICAL_MEDIA, MEDIA, strict=True)),
            "checks": checks,
            "draft_metric_checks": {metric: row["status"] for metric, row in draft.items()},
            "draft_guardrails": {guard: row["status"] for guard, row in guards.items()},
            "media": media,
            "not_run": ["frozen M3 scoring region", "formal ingest/export/verify"],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    candidate, output = args.candidate.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "BLOCKED",
        "contract": CONTRACT,
        "contract_docs_sha": CONTRACT_DOCS_SHA,
        "implementation_sha": SHA,
        "ci_sha": tool_output("git", Path(__file__).resolve().parent, "rev-parse", "HEAD")
        .decode()
        .strip(),
        "scenarios": {
            case: {"status": "BLOCKED", "reason": "frozen holdout unavailable"} for case in HOLDOUTS
        },
    }
    report["scenarios"]["S4"] = {
        "status": "BLOCKED",
        "reason": "candidate fixture has no independent slow appearance drift",
    }
    errors = []
    missing = [
        Path(tool).name for tool in (args.ffmpeg, args.ffprobe) if shutil.which(tool) is None
    ]
    try:
        if missing:
            report["scenarios"].update(
                {
                    case: {"status": "BLOCKED", "reason": "required media tool missing"}
                    for case in SCENARIOS
                }
            )
            raise ValueError("Required media tools missing: " + ", ".join(missing))
        require(
            tool_output("git", candidate, "rev-parse", "HEAD").decode().strip() == SHA,
            "unexpected implementation SHA",
        )
        require(
            not tool_output("git", candidate, "status", "--porcelain"), "dirty implementation tree"
        )
        for case, stages in SCENARIOS.items():
            try:
                report["scenarios"][case] = check_scenario(
                    candidate, output, case, stages, args.ffmpeg, args.ffprobe
                )
            except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
                reason = str(error) if type(error) is ValueError else type(error).__name__
                report["scenarios"][case] = {"status": "FAIL", "reason": reason}
                errors.append(f"{case}: {reason}")
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        errors.append(str(error) if type(error) is ValueError else type(error).__name__)
    (output / "acceptance-report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    failed_checks = [
        f"{case}: {label}"
        for case, result in report["scenarios"].items()
        for label, state in result.get("checks", {}).items()
        if state == "FAIL"
    ]
    failed_checks.extend(
        f"{case}: strict media {result['media']['status']}"
        for case, result in report["scenarios"].items()
        if result.get("media", {}).get("status") not in (None, "PASS")
    )
    (output / "failures.log").write_text("\n".join(errors + failed_checks) + "\n", encoding="utf-8")
    return int(bool(errors or failed_checks))


if __name__ == "__main__":
    sys.exit(main())
