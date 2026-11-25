"""Generate and independently inspect the R1-002 candidate's 8-second B evidence."""

import argparse
import hashlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image
from test_video_repair_r1_media import verify_media
from test_video_repair_r1_oracle import guardrail_checks, measure, metric_checks

WIDTH, HEIGHT, COUNT, FPS = 640, 360, 96, 12
NAMES = [f"{tick:06d}.png" for tick in range(COUNT)]
MEDIA = ("before.mp4", "after.mp4", "comparison.mp4")
ROLES = ("roi", "stable", "ghost", "defect")
CONTRACT = "c-video-repair-contract@0.1/R1-002-DRAFT"
CANDIDATE_SHA = "a8f211bea533c2101200db620fe6d8ee47b4ecf8"
CONTRACT_SHA = "52180bd1e71b3b23b2e7cbbe2e236b05f944f2a3"

# Run inside the pinned checkout so SVM comes from the candidate, not an editable CI install.
REPLAY = """
from dataclasses import replace
from pathlib import Path
import json
import sys

from svm.document import validate_document
from svm.evaluator import EvaluationState, Quality
from svm.motion import MotionEvaluator
from svm.renderers import SVGRenderer, SVGRenderOptions
from svm.scene import EvaluatedStyle

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
validate_document(document)
assert document["animation"]["timebase"]["ticks_per_second"] == 12
assert document["animation"]["content"]
motion = MotionEvaluator(document)
renderer = SVGRenderer(SVGRenderOptions(width=640, height=360, view_box=(-2, -1.125, 4, 2.25)))
output = Path(sys.argv[2])
moving = set(json.loads(sys.argv[3]))
black = EvaluatedStyle(fill="#000000", stroke="none", stroke_width=0.0, opacity=1.0)
for tick in range(96):
    frame = motion.evaluate(tick, Quality.FINAL)
    assert frame.scene.quality == Quality.FINAL, tick
    assert frame.evaluator.runtime, tick
    for node in frame.evaluator.runtime.values():
        assert node.state == EvaluationState.CLEAN and node.evaluated_quality == Quality.FINAL, tick
        assert node.outputs is not None and node.stale_outputs is None, tick
    (output / f"{tick:06d}.svg").write_bytes(renderer.render(frame.scene).encode())
    selected = tuple(replace(entity, style=black) for entity in frame.scene.entities
                     if entity.entity_id in moving)
    assert len(selected) == len(moving) > 0, tick
    mask_svg = renderer.render(replace(frame.scene, entities=selected))
    (output / f"mask-{tick:06d}.svg").write_bytes(mask_svg.encode())
"""


class Blocked(ValueError):
    """An environment prerequisite is absent or incompatible."""


def require(condition, diagnostic):
    if not condition:
        raise ValueError(diagnostic)


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_clip(directory, records, mode="RGB", *, collect=True):
    require(sorted(path.name for path in directory.iterdir()) == NAMES, "PNG frame inventory")
    require(len(records) == COUNT, "PNG manifest frame count")
    shape = (COUNT, HEIGHT, WIDTH, 3) if mode == "RGB" else (COUNT, HEIGHT, WIDTH)
    frames = np.empty(shape, dtype=np.uint8 if mode == "RGB" else bool) if collect else None
    ihdr = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2 if mode == "RGB" else 0, 0, 0, 0)
    for tick, (name, record) in enumerate(zip(NAMES, records, strict=True)):
        data = (directory / name).read_bytes()
        require(data[16:29] == ihdr, f"PNG IHDR at tick {tick}")
        with Image.open(io.BytesIO(data)) as image:
            require(
                (image.format, image.mode, image.size) == ("PNG", mode, (WIDTH, HEIGHT)),
                f"PNG mode or dimensions at tick {tick}",
            )
            pixels = np.asarray(image)
        if mode == "L":
            require(not np.any((pixels != 0) & (pixels != 255)), f"non-binary mask at tick {tick}")
        require(
            record
            == {
                "file": name,
                "tick": tick,
                "sha256": digest(data),
                "pixel_sha256": digest(pixels.tobytes()),
            },
            f"PNG hash, filename or order at tick {tick}",
        )
        if frames is not None:
            frames[tick] = pixels
    return frames


def command(argv, cwd, *, timeout=180):
    return subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout, check=False)


def version(binary, flag, cwd):
    try:
        result = command([binary, flag], cwd, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Blocked(f"{Path(binary).name}: required tool unavailable") from error
    if result.returncode or not result.stdout:
        raise Blocked(f"{Path(binary).name}: tool version query failed")
    return result.stdout.decode(errors="replace").splitlines()[0]


def metadata(header):
    require(
        (header["width"], header["height"], header["frame_count"]) == (WIDTH, HEIGHT, COUNT),
        "Motion profile B dimensions or count",
    )
    require(header["fps"] == {"numerator": FPS, "denominator": 1}, "Motion profile B rate")
    require(Fraction(header["duration_seconds"]) == 8, "Motion profile B duration")
    require(header["timebase_ticks_per_second"] == FPS, "Motion profile B timebase")


def expand(mask, radius):
    height, width = mask.shape
    padded = np.pad(mask, radius)
    return np.logical_or.reduce(
        [
            padded[y : y + height, x : x + width]
            for x in range(2 * radius + 1)
            for y in range(2 * radius + 1)
        ]
    )


def verify_motion_source(candidate, source, manifest, clean, fixture, edge, interior, resvg):
    document = source / "document.svm.json"
    require(
        digest(document.read_bytes()) == manifest["document_sha256"], "document digest mismatch"
    )
    require(fixture["document_sha256"] == manifest["document_sha256"], "fixture document digest")
    require(manifest["endpoint_rule"] == "left-closed-right-open", "source endpoint rule")
    require(len(manifest["frames"]) == COUNT, "source manifest frame count")
    silhouette = np.empty((COUNT, HEIGHT, WIDTH), dtype=bool)
    with tempfile.TemporaryDirectory(prefix="svm-motion-replay-") as replay_dir:
        replay = Path(replay_dir)
        moving = json.dumps(fixture["scene"]["moving_entities"])
        result = command([sys.executable, "-c", REPLAY, document, replay, moving], candidate)
        require(result.returncode == 0, f"independent FINAL replay exited {result.returncode}")
        raster_hashes = []
        for tick, record in enumerate(manifest["frames"]):
            stem = f"{tick:06d}"
            svg = replay / f"{stem}.svg"
            mask_svg = replay / f"mask-{stem}.svg"
            require(
                (record["tick"], Fraction(record["seconds"]), record["quality"], record["stale"])
                == (tick, Fraction(tick, FPS), "FINAL", False),
                f"source tick or quality at {tick}",
            )
            for rendered in (svg, mask_svg):
                original = source / ("mask-svg" if rendered == mask_svg else "svg") / f"{stem}.svg"
                require(
                    original.read_bytes() == rendered.read_bytes(),
                    f"independent SVG replay at {tick}",
                )
            svg_hash = digest(svg.read_bytes())
            require(svg_hash == record["svg_sha256"], f"SVG digest at {tick}")
            raster_hashes.append(svg_hash)
            for rendered, options in ((svg, []), (mask_svg, ["--background", "white"])):
                png = rendered.with_suffix(".png")
                process = command([resvg, *options, rendered, png], replay, timeout=30)
                require(process.returncode == 0, f"resvg raster replay at {tick}")
                with Image.open(png) as image:
                    require(image.size == (WIDTH, HEIGHT), f"resvg dimensions at {tick}")
                    if rendered == svg:
                        opaque = image.convert("RGBA").getchannel("A").getextrema() == (255, 255)
                        require(opaque, f"resvg transparency at {tick}")
                        require(
                            np.array_equal(np.asarray(image.convert("RGB")), clean[tick]),
                            f"resvg RGB pixels at {tick}",
                        )
                        require(
                            digest(png.read_bytes()) == record["png_sha256"],
                            f"resvg raw PNG hash at {tick}",
                        )
                    else:
                        silhouette[tick] = np.asarray(image.convert("RGB")).mean(axis=2) < 128
                png.unlink()
            contour = silhouette[tick]
            require(
                np.array_equal(expand(contour, 1) & expand(~contour, 1), edge[tick])
                and np.array_equal(contour & ~expand(~contour, 2), interior[tick]),
                f"replayed edge or interior mask at {tick}",
            )
    require(raster_hashes == manifest["rasterizer"]["svg_sha256"], "rasterizer SVG digest list")
    require(np.any(silhouette[1:] != silhouette[:-1]), "static source silhouette")
    return silhouette


def source_records(manifest):
    for tick, record in enumerate(manifest["frames"]):
        initial = manifest["inputs"][tick]
        reference = manifest["evaluation"]["clean_frames"][tick]
        require(record["png_file"] == NAMES[tick], f"source PNG locator at {tick}")
        for field, expected in (
            ("input_sha256", initial["sha256"]),
            ("input_pixel_sha256", initial["pixel_sha256"]),
            ("clean_sha256", reference["sha256"]),
            ("clean_pixel_sha256", reference["pixel_sha256"]),
            ("edge_sha256", manifest["evaluation"]["motion_edge_masks"][tick]["sha256"]),
            ("interior_sha256", manifest["evaluation"]["object_interior_masks"][tick]["sha256"]),
        ):
            require(record[field] == expected, f"divergent source digest {field} at {tick}")


def rectangle(bounds):
    left, top, right, bottom = bounds
    require(0 <= left < right <= WIDTH and 0 <= top < bottom <= HEIGHT, "invalid ROI coordinates")
    mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    mask[top:bottom, left:right] = True
    return mask


def protection(manifest, initial, repaired, masks):
    status = manifest["frame_status"]
    reports = manifest["frame_reports"]
    require(len(status) == len(reports) == COUNT, "repair frame status extent")
    unresolved = []
    changed_outside = 0
    for tick, (before, after, frame, stages) in enumerate(
        zip(initial, repaired, status, reports, strict=True)
    ):
        changed = np.any(before != after, axis=2)
        changed_outside += int(np.count_nonzero(changed & ~masks["roi"][tick]))
        refused = stages["ghost"]["refused_pixels"] + stages["defect"]["refused_pixels"]
        count = int(np.count_nonzero(changed))
        require(
            (frame["index"], stages["frame"], frame["changed_pixels"], frame["unresolved_pixels"])
            == (tick, tick, count, refused),
            f"stale repair frame record at {tick}",
        )
        require(
            frame["status"] == ("REPAIRED" if count else "UNRESOLVED" if refused else "UNCHANGED"),
            f"legacy repair frame status at {tick}",
        )
        unresolved.append(refused)
    require(changed_outside == 0, "repair changed pixels outside ROI")
    return unresolved


def run_motion(candidate, report, output, *, resvg, ffmpeg, ffprobe):
    checks = report["checks"]
    report["tools"] = {
        "resvg": version(resvg, "--version", candidate),
        "ffmpeg": version(ffmpeg, "-version", candidate),
        "ffprobe": version(ffprobe, "-version", candidate),
    }
    if report["tools"]["resvg"] not in {"0.44.0", "resvg 0.44.0"}:
        raise Blocked("Profile B requires resvg 0.44.0")
    commit = command(["git", "rev-parse", "--verify", "HEAD"], candidate)
    require(
        commit.returncode == 0 and commit.stdout.decode().strip() == report["implementation_sha"],
        "unexpected implementation checkout SHA",
    )
    tracked = command(["git", "status", "--porcelain", "--untracked-files=no"], candidate)
    require(tracked.returncode == 0 and not tracked.stdout, "dirty implementation checkout")

    with tempfile.TemporaryDirectory(prefix="svm-ci-motion-") as temporary:
        root = Path(temporary)
        source, repair = root / "offline", root / "repair"

        def motion(stage, run, *arguments):
            result = command(
                [
                    sys.executable,
                    "-m",
                    "prototype.video_restoration.motion",
                    stage,
                    "--run",
                    run,
                    *arguments,
                ],
                candidate,
            )
            report["stage_exits"][stage] = result.returncode
            return result.returncode

        require(motion("source", source, "--resvg", resvg) == 0, "candidate Motion source failed")
        source_manifest_path = source / "source-manifest.json"
        source_manifest = load_json(source_manifest_path)
        metadata(source_manifest)
        require(
            source_manifest["rasterizer"]["version"] == report["tools"]["resvg"],
            "source resvg version",
        )
        evaluation = source / "evaluation"
        fixture_path = evaluation / "fixture.json"
        fixture = load_json(fixture_path)
        eval_records = source_manifest["evaluation"]
        require(
            digest(fixture_path.read_bytes()) == eval_records["fixture_sha256"],
            "offline fixture hash",
        )
        clean = load_clip(evaluation / "clean", eval_records["clean_frames"])
        edge = load_clip(evaluation / "edge", eval_records["motion_edge_masks"], "L")
        interior = load_clip(evaluation / "interior", eval_records["object_interior_masks"], "L")
        silhouette = verify_motion_source(
            candidate, source, source_manifest, clean, fixture, edge, interior, resvg
        )
        source_records(source_manifest)
        checks["final_nonstale_motion_replay"] = {"status": "PASS", "frames": COUNT}

        # The repair bundle has only degradation, authorization masks and metadata.
        repair.mkdir()
        shutil.copyfile(source_manifest_path, repair / source_manifest_path.name)
        for item in ("input", "mask"):
            shutil.copytree(source / item, repair / item)
        require(
            sorted(path.name for path in repair.iterdir())
            == ["input", "mask", "source-manifest.json"],
            "repair bundle contains evaluation artifacts",
        )
        repair_exit = motion("repair", repair, "--ffmpeg", ffmpeg, "--ffprobe", ffprobe)
        require(repair_exit in {0, 5}, f"candidate Motion repair or export exited {repair_exit}")
        manifest = load_json(repair / "run-manifest.json")
        metadata(manifest)
        source_digest = digest(source_manifest_path.read_bytes())
        require(
            digest((repair / source_manifest_path.name).read_bytes())
            == source_digest
            == manifest["source_manifest_sha256"],
            "modified source manifest or broken digest link",
        )
        require(
            manifest["document_sha256"] == source_manifest["document_sha256"],
            "changed document link",
        )
        require(
            manifest["inputs"] == source_manifest["inputs"]
            and manifest["masks"] == source_manifest["masks"],
            "repaired manifest input or mask identity",
        )
        initial = load_clip(repair / "input", source_manifest["inputs"])
        masks = {
            role: load_clip(repair / "mask" / role, source_manifest["masks"][role], "L")
            for role in ROLES
        }
        checks["repair_bundle_without_offline_files"] = {"status": "PASS"}
        checks["repair_manifest_reference_isolation"] = {
            "status": "FAIL" if "evaluation" in source_manifest else "PASS",
            "diagnostic": "Repair reads a source manifest containing offline evidence hashes.",
        }
        require(
            manifest["frames_original"] == source_manifest["inputs"], "before frame digest mismatch"
        )
        load_clip(repair / "frames-original", manifest["frames_original"], collect=False)
        repaired = load_clip(repair / "frames-repaired", manifest["frames_repaired"])
        unresolved = protection(manifest, initial, repaired, masks)
        checks["png_mask_hashes_and_roi"] = {"status": "PASS"}
        checks["partial_exit_5"] = {
            "status": "PASS"
            if manifest["status"].upper() == "PARTIAL" and repair_exit == 5
            else "FAIL",
            "observed": {"state": manifest["status"], "exit": repair_exit},
            "expected": {"state": "PARTIAL", "exit": 5},
        }
        checks["no_unresolved_pixels"] = {
            "status": "PASS" if not any(unresolved) else "FAIL",
            "count": sum(unresolved),
            "frames": sum(count > 0 for count in unresolved),
        }

        hashes = {name: record["sha256"] for name, record in manifest["media"]["files"].items()}
        checks["strict_complete_media"] = verify_media(
            repair, initial, repaired, ffmpeg=ffmpeg, ffprobe=ffprobe, expected_hashes=hashes
        )
        regions = fixture["regions"]
        roi = rectangle(regions["roi_xyxy"])
        scoring_roi = rectangle(regions["defect_xyxy"])
        background = rectangle(regions["appearance_xyxy"])
        reports = {
            phase: measure(
                clip,
                clean,
                scoring_roi=scoring_roi,
                background=background,
                ghost=None,
                interior=interior,
            )
            for phase, clip in (("input", initial), ("output", repaired))
        }
        checks["draft_quality_metrics"] = metric_checks(
            reports["input"],
            reports["output"],
            ("M1", "M2", "M3", "M4"),
            combined=True,
        )
        checks["draft_quality_metrics"]["M3"] = {
            "status": "BLOCKED",
            "diagnostic": "Candidate supplies a moving ghost mask, not a fixed E2 region.",
        }
        report["independent_metrics"] = reports
        checks["draft_q_t_guardrails"] = guardrail_checks(
            initial,
            repaired,
            clean,
            roi=roi,
            scoring_roi=scoring_roi,
            background=background,
            ghost=masks["ghost"],
            interior=interior,
            silhouette=silhouette,
            unresolved_pixels=unresolved,
        )

        media_files = checks["strict_complete_media"]["files"]
        if len(media_files) == len(MEDIA) and all(
            "pts" in item["checks"] for item in media_files.values()
        ):
            aligned = all(item["checks"]["pts"] for item in media_files.values())
            checks["draft_q_t_guardrails"]["T3"] = {
                "status": "PASS" if aligned else "FAIL",
                "source": "ffprobe presentation timestamps for all three outputs",
            }

        # Bundle the candidate evaluation alongside the independent results.
        shutil.copytree(evaluation, repair / "evaluation")
        evaluation_exit = motion("evaluate", repair)
        checked = load_json(repair / "run-manifest.json")
        checked_metrics = checked.get("evaluation", {}).get("metrics_sha256")
        require(
            evaluation_exit == 0
            and checked_metrics == digest((repair / "metrics.json").read_bytes()),
            "candidate evaluation stage failed or published an invalid digest",
        )
        require(
            checked.get("status", "").upper() != "SUCCESS", "legacy stage published formal SUCCESS"
        )
        for name in ("run-manifest.json", "metrics.json", *MEDIA):
            shutil.copyfile(repair / name, output / name)
        shutil.copyfile(source_manifest_path, output / "source-manifest.json")
        for name, stage in (("before", "original"), ("after", "repaired")):
            shutil.copyfile(
                repair / f"frames-{stage}" / NAMES[COUNT // 2], output / f"preview-{name}.png"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resvg", default="resvg")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--implementation-sha", default=CANDIDATE_SHA)
    parser.add_argument("--contract-sha", default=CONTRACT_SHA)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    worktree = Path(__file__).resolve().parents[1]
    revision = command(["git", "rev-parse", "--verify", "HEAD"], worktree, timeout=30)
    report = {
        "ci_sha": revision.stdout.decode().strip(),
        "implementation_sha": args.implementation_sha,
        "contract_sha": args.contract_sha,
        "contract": CONTRACT,
        "status": "BLOCKED",
        "diagnostic": "R1-002 is a draft without approved B evidence and a formal verify stage.",
        "checks": {},
        "stage_exits": {},
    }
    errors = []
    try:
        require(revision.returncode == 0, "CI worktree revision could not be read")
        run_motion(
            args.candidate.resolve(),
            report,
            args.output.resolve(),
            resvg=args.resvg,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
        )
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired, TypeError) as error:
        detail = str(error) if type(error) in (ValueError, Blocked) else type(error).__name__
        report["checks"]["evidence_pipeline"] = {
            "status": "BLOCKED" if isinstance(error, Blocked) else "FAIL",
            "diagnostic": detail,
        }
        errors.append(detail)

    def failures(checks, prefix=""):
        for name, check in checks.items():
            if not isinstance(check, dict):
                continue
            label = f"{prefix}{name}"
            if check.get("status") in {"FAIL", "BLOCKED"}:
                yield f"{check['status']} {label}"
            yield from failures(check, f"{label}.")

    errors.extend(failures(report["checks"]))
    (args.output / "acceptance-report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (args.output / "failures.log").write_text("\n".join(errors) + "\n", encoding="utf-8")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
