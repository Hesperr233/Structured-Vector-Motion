"""Run the staged R1-002 CLI and independently inspect its sealed evidence."""

import argparse
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import unittest
from datetime import datetime
from fractions import Fraction
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import numpy as np
from PIL import Image
from test_video_repair_r1_media import RGB_DECODE_FILTER, verify_media
from test_video_repair_r1_motion import (
    Blocked,
    command,
    digest,
    expand,
    load_json,
    require,
    verify_motion_source,
    version,
)
from test_video_repair_r1_oracle import guardrail_checks, measure, metric_checks
from video_repair_r1_publish import publish_verified_run
from video_repair_r1_segmentation import MOTION_SEGMENTATION, development_segmenter, segment_motion

CONTRACT = "c-video-repair-contract@0.1"
REVISION = "R1-002"
FREEZE = "docs/c-video-repair/g1-freeze.json"
CONTRACT_DOCUMENTS = {
    f"docs/c-video-repair/{name}.md"
    for name in (
        "acceptance-criteria-v0.1",
        "integration-boundary",
        "pipeline-contract-v0.1",
        "requirements-v0.1",
    )
}
HOLDOUT_TOOL = "tools/video_repair_r1_holdout.py"
CASES = {
    "A-development": ("S1", "S2", "S3", "S4", "S5", "N0"),
    "A-holdout": ("H1", "H2", "H3", "H4", "H5"),
    "B": ("B5",),
}
ALL_METRICS = ("M1", "M2", "M3", "M4")
BUNDLE_FIELDS = (
    "contract_version",
    "draft_revision",
    "profile_id",
    "width",
    "height",
    "pixel_format",
    "color_space",
    "frame_count",
    "fps",
    "timebase_ticks_per_second",
    "frame_ticks",
    "duration_seconds",
    "endpoint_rule",
)


def canonical_digest(value):
    return digest(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def write_report(path, report):
    staged = path.with_suffix(".pending")
    staged.write_text(
        json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    staged.replace(path)


def git(root, *args):
    result = command(["git", *args], root, timeout=30)
    require(result.returncode == 0, f"Git checkout check failed ({args[0]})")
    return result.stdout


def verify_checkout(root, expected):
    top = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel").rstrip())).resolve()
    sha = git(root, "rev-parse", "HEAD").decode().strip()
    require(top == root and re.fullmatch(r"[0-9a-f]{40}", sha), "Invalid checkout root or SHA")
    require(sha == expected, f"Checkout SHA {sha} differs from expected {expected}")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"), "Dirty checkout")
    return sha


def asset(root, name):
    relative = PurePosixPath(name)
    require(
        relative.parts
        and not relative.is_absolute()
        and ".." not in relative.parts
        and "\\" not in name
        and ":" not in name
        and name == relative.as_posix(),
        "Invalid evidence path",
    )
    path = root.joinpath(*relative.parts)
    require(path.is_file() and path.resolve() == path, f"Missing or linked evidence file {name}")
    return path


def asset_record(root, record, expected):
    require(record["file"] == expected, f"Unexpected evidence path for {expected}")
    path = asset(root, expected)
    data = path.read_bytes()
    require(digest(data) == record["file_sha256"], f"File digest mismatch for {expected}")
    return path, data


def tracked(root, name):
    path = asset(root, name)
    data = path.read_bytes()
    # Git checkout normalization can modify text endings on Windows. These files are
    # hashed as raw bytes and must match the immutable Git blob exactly.
    require(git(root, "show", f"HEAD:{name}") == data, f"Unbound contract file {name}")
    return path, data


def inventory(root):
    files = {}
    require(root.is_dir() and root.resolve() == root, "Evidence directory is missing or linked")
    for path in root.rglob("*"):
        require(path.resolve() == path, "Linked evidence path")
        if path.is_file():
            files[path] = digest(path.read_bytes())
    require(files, "Empty evidence fixture")
    return files


def read_stack(root, records, role, count, size, *, mask=False):
    require(len(records) == count, f"Wrong record length for {role}")
    names = [f"frame-{i:04}.png" for i in range(count)]
    require(sorted(p.name for p in (root / role).iterdir()) == names, f"Frame inventory for {role}")
    frames = []
    for i, (record, name) in enumerate(zip(records, names, strict=True)):
        _, data = asset_record(root, record, f"{role}/{name}")
        require(type(record["index"]) is int and record["index"] == i, "Frame index mismatch")
        with Image.open(io.BytesIO(data)) as image:
            require(
                (image.format, image.mode, image.size) == ("PNG", "L" if mask else "RGB", size),
                f"Invalid PNG shape or format for {role}/{name}",
            )
            pixels = np.asarray(image)
        require(digest(pixels.tobytes()) == record["pixel_sha256"], "Pixel digest mismatch")
        if mask:
            require(not np.any((pixels != 0) & (pixels != 255)), "Mask is not binary")
            require(np.count_nonzero(pixels) == record["support_pixels"], "Mask support mismatch")
        frames.append(pixels != 0 if mask else pixels)
    return np.stack(frames)


def read_mask(root, role, description, count, size, *, constant=False):
    require(description["kind"] == ("constant" if constant else "per-frame"), "Mask temporal kind")
    frames = read_stack(root, description["files"], role, 1 if constant else count, size, mask=True)
    return frames[0] if constant else frames


def rectangle(bounds, size, feather=0):
    left, top, right, bottom = bounds
    width, height = size
    require(0 <= left < right <= width and 0 <= top < bottom <= height, "Invalid region bounds")
    require(type(feather) is int and 0 <= feather <= 8, "Invalid feather")
    area = np.zeros((height, width), dtype=bool)
    area[max(0, top - feather) : bottom + feather, max(0, left - feather) : right + feather] = True
    return area


def check_status(value):
    return {"status": "PASS" if value else "FAIL"}


def overall(states):
    states = set(states)
    if states & {"FAIL", "CHANGES_REQUIRED"}:
        return "CHANGES_REQUIRED"
    if not states or states - {"PASS", "NOT_APPLICABLE"}:
        return "BLOCKED"
    return "PASS"


def run_stage(argv, root, case_output, results, stage, *, allowed=(0,), timeout=240):
    try:
        result = command(argv, root, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        results[stage] = {"status": "BLOCKED", "reason": type(error).__name__}
        raise Blocked(f"Could not complete stage {stage}") from error
    log = case_output / f"{stage}.log"
    output = result.stdout + b"\n" + result.stderr
    paths = {root, case_output, *(Path(value) for value in argv if Path(value).is_absolute())}
    for path in sorted(paths, key=lambda item: (-len(str(item)), str(item))):
        label = b"{workspace}/" + path.name.encode("utf-8")
        for spelling in {str(path), path.as_posix()}:
            output = output.replace(spelling.encode("utf-8"), label)
    log.write_bytes(output)
    results[stage] = {
        "exit_code": result.returncode,
        "status": "PASS" if result.returncode in allowed else "FAIL",
        "log": log.name,
        "sha256": digest(log.read_bytes()),
    }
    require(result.returncode in allowed, f"Stage {stage} exited {result.returncode}")
    return result.returncode


def frozen_policy(args):
    try:
        _, freeze_bytes = tracked(args.contract, FREEZE)
        freeze = json.loads(freeze_bytes)
    except (OSError, ValueError) as exc:
        raise Blocked("Missing valid G1 freeze record at the exact contract SHA") from exc
    if freeze.get("state") != "FROZEN" or not freeze.get("approval"):
        raise Blocked("G1 requires an approved frozen contract")
    require(
        freeze.get("schema_version") == 1
        and freeze.get("contract_version") == CONTRACT
        and freeze.get("draft_revision") == REVISION,
        "Unsupported G1 freeze record",
    )
    seal = freeze["holdout_seal"]
    seal_path, data = tracked(args.contract, seal["path"])
    require(digest(data) == seal["sha256"], "The approved holdout seal digest differs")
    documents = freeze["contract_documents"]
    require(set(documents) == CONTRACT_DOCUMENTS, "Incomplete approved contract documents")
    for name, expected in sorted(documents.items()):
        _, document = tracked(args.contract, name)
        require(digest(document) == expected, f"Approved contract text differs: {name}")
    approval = freeze["approval"]
    proposed = {key: value for key, value in freeze.items() if key not in {"state", "approval"}}
    require(
        isinstance(approval, dict)
        and approval.get("decision") == "APPROVE_G1"
        and isinstance(approval.get("approved_by"), str)
        and bool(approval["approved_by"].strip())
        and approval.get("holdout_seal_sha256") == seal["sha256"]
        and approval.get("policy_sha256") == canonical_digest(proposed)
        and datetime.fromisoformat(approval["approved_at"]).utcoffset() is not None,
        "The G1 approval is incomplete or not bound to the frozen policy",
    )
    require(
        freeze["media"]["independent_decode_filter"] == RGB_DECODE_FILTER,
        "Independent RGB decode differs from the approved media profile",
    )
    require(args.holdout is None or args.holdout.resolve() == seal_path, "Unapproved holdout seal")
    return freeze, seal_path, digest(freeze_bytes)


def verify_identity(clip, fixture, run, manifest, implementation_sha):
    public = fixture / "source"
    initial = load_json(asset(public, "video-manifest.json"))
    size = (640, 360) if clip == "B5" else (256, 256)
    count = 96 if clip == "B5" else 24
    require(
        initial["clip_id"] == clip
        and (initial["width"], initial["height"], initial["frame_count"]) == (*size, count)
        and (initial["contract_version"], initial["draft_revision"]) == (CONTRACT, REVISION)
        and initial["frame_ticks"] == list(range(count))
        and initial["fps"] == {"numerator": 12, "denominator": 1}
        and initial["timebase_ticks_per_second"] == 12
        and initial["duration_seconds"] == f"{count // 12}/1"
        and initial["endpoint_rule"] == "left-closed-right-open"
        and initial["pixel_format"] == "RGB8"
        and initial["color_space"] == "sRGB"
        and initial["clean_reference_in_input"] is False,
        "Unexpected source profile, color, or timeline",
    )
    projection = {k: initial[k] for k in BUNDLE_FIELDS}
    projection["frames"] = [
        {k: frame[k] for k in ("index", "file_sha256", "pixel_sha256")}
        for frame in initial["frames"]
    ]
    require(
        canonical_digest(projection) == initial["bundle_digest"], "Input bundle digest mismatch"
    )
    snapshot = asset(run, "input/video-manifest.json").read_bytes()
    input_sha = digest(snapshot)
    require(snapshot == asset(public, "video-manifest.json").read_bytes(), "Input manifest changed")
    require(input_sha == manifest["input_manifest_sha256"], "Input manifest SHA mismatch")
    before = read_stack(public, initial["frames"], "frames", count, size)
    archived = read_stack(run / "input", initial["frames"], "frames", count, size)
    require(np.array_equal(archived, before), "Input snapshot pixels differ")
    masks = asset(fixture, "masks/mask-manifest.json").read_bytes()
    config = asset(fixture, "config.json").read_bytes()
    require(masks == asset(run, "masks/mask-manifest.json").read_bytes(), "Mask snapshot differs")
    require(config == asset(run, "config.json").read_bytes(), "Config snapshot differs")
    identity = {
        "contract_version": CONTRACT,
        "draft_revision": REVISION,
        "bundle_digest": initial["bundle_digest"],
        "mask_manifest_sha256": digest(masks),
        "config_sha256": digest(config),
        "algorithm_id": manifest["algorithm_id"],
        "code_sha": implementation_sha,
    }
    run_id = "run:" + canonical_digest(identity).split(":", 1)[1]
    require(
        all(manifest.get(key) == value for key, value in identity.items())
        and manifest["run_id"] == run_id
        and manifest["clip_id"] == clip
        and manifest["frame_count"] == count,
        "Candidate run is not bound to the pinned inputs and implementation",
    )
    after = read_stack(run, manifest["frames"], "frames-repaired", count, size)
    status_keys = ("index", "pixel_sha256", "status", "changed_pixels", "unresolved_pixels")
    semantic = {
        "run_id": run_id,
        "frames": [{k: f[k] for k in status_keys} for f in manifest["frames"]],
    }
    require(
        canonical_digest(semantic) == manifest["semantic_digest"], "Repair semantics digest differs"
    )
    changed = np.any(before != after, axis=-1)
    unresolved = []
    for i, frame in enumerate(manifest["frames"]):
        require(
            frame["tick"] == i and Fraction(frame["pts"]) == Fraction(i, 12), "Frame PTS mismatch"
        )
        require(
            frame["changed_pixels"] == int(changed[i].sum()), "Frame changed-pixel count differs"
        )
        refused = frame["unresolved_pixels"]
        require(
            type(refused) is int and 0 <= refused <= size[0] * size[1], "Invalid unresolved count"
        )
        unresolved.append(refused)
    require(sum(unresolved) == manifest["unresolved_pixels"], "Unresolved count mismatch")
    return before, after, initial, json.loads(masks), json.loads(config), unresolved


def segmentation(rule):
    if rule is None:
        return None
    require(rule["kind"] == "rgb-thresholds@1", "Unsupported output segmentation rule")
    red = rule["red_minimum"]
    contrast = rule["red_minus_green_minimum"]
    require(type(red) is int and 0 <= red <= 255, "Invalid red segmentation threshold")
    require(type(contrast) is int and -255 <= contrast <= 255, "Invalid contrast threshold")

    def segment(frame):
        return (frame[..., 0] >= red) & (frame[..., 0].astype(np.int16) - frame[..., 1] >= contrast)

    return segment


def local_changes(before, after, photo, stable):
    # Classify an observable change as photometric only when the original static
    # samples independently support the same integer RGB displacement.
    result = np.empty(before.shape[:3], dtype=bool)
    for i, (original, restored) in enumerate(zip(before, after, strict=True)):
        sample = stable[0] & stable[i]
        observed = original.astype(np.int16) - restored
        supported = np.zeros(photo.shape, dtype=bool)
        if np.count_nonzero(sample) >= 2:
            displacement = original[sample].astype(np.int16) - before[0, sample]
            offset = np.rint(np.median(displacement, axis=0)).astype(np.int16)
            if np.mean(np.max(np.abs(displacement - offset), axis=1) <= 2) >= 0.95:
                supported = photo & np.all(observed == offset, axis=-1)
        result[i] = np.any(observed != 0, axis=-1) & ~supported
    return result


def motion_replay(args, reference, source, clean, masks, frozen):
    require(
        source["kind"] == "svm-motion-controlled"
        and source["svm_code_sha"] == args.expected_candidate_sha
        and source["canvas"] == [640, 360]
        and source["view_box"] == [-2, -1.125, 4, 2.25]
        and source["timebase_ticks_per_second"] == 12,
        "Motion provenance differs from the pinned candidate or B profile",
    )
    document, content = asset_record(reference, source["document"], "motion/document.svm.json")
    _, basis = asset_record(reference, source["basis_stimulus"], "basis-stimulus.json")
    if frozen is not None:
        policy = frozen["motion"]
        observed = {
            "document_sha256": digest(content),
            "document_id": json.loads(content)["document_id"],
            "revision_id": source["revision_id"],
            "snapshot_kind": source["snapshot_kind"],
            "first_tick": 0,
            "last_tick": 95,
            "ticks_per_second": 12,
            "width": 640,
            "height": 360,
            "quality": source["motion"]["quality"],
            "stale_frames": source["motion"]["stale_frames"],
        }
        require(
            all(policy[key] == value for key, value in observed.items()), "B source is not approved"
        )
    records = []
    for tick, record in enumerate(source["frames"]):
        asset_record(reference, record["svg"], f"motion/svg/{tick:06}.svg")
        asset_record(reference, record["mask_svg"], f"motion/mask-svg/{tick:06}.svg")
        require(
            digest(clean[tick].tobytes()) == record["clean_pixel_sha256"], "Motion clean digest"
        )
        records.append(
            {
                **record,
                "svg_sha256": record["svg"]["file_sha256"],
                "png_sha256": record["raster_sha256"],
            }
        )
    silhouette = masks["contour"]
    edge = np.stack([expand(frame, 1) & expand(~frame, 1) for frame in silhouette])
    document_sha = digest(content)
    manifest = {
        "document_sha256": document_sha,
        "endpoint_rule": "left-closed-right-open",
        "frames": records,
        "rasterizer": {"svg_sha256": [record["svg_sha256"] for record in records]},
    }
    fixture = {**json.loads(basis), "document_sha256": document_sha}
    replayed = verify_motion_source(
        args.candidate, document.parent, manifest, clean, fixture, edge, masks["I"], args.resvg
    )
    require(np.array_equal(replayed, silhouette), "Frozen contour differs from Motion replay")
    return {"status": "PASS", "document_sha256": document_sha, "ticks": len(records)}


def inspect_case(args, clip, fixture, output, case, frozen):
    run = output / "run"
    reference = fixture / "reference"
    manifest = load_json(asset(run, "run-manifest.json"))
    before, after, initial, masks, config, unresolved = verify_identity(
        clip, fixture, run, manifest, args.expected_candidate_sha
    )
    shape = (len(before), before.shape[2], before.shape[1])
    count, *size = shape
    size = tuple(size)
    offline_bytes = asset(reference, "reference-manifest.json").read_bytes()
    offline = json.loads(offline_bytes)
    expected_format = (
        "cvr-holdout-reference@1" if clip.startswith("H") else "cvr-development-reference@1"
    )
    require(offline["reference_format"] == expected_format, "Unexpected reference format")
    require(
        offline["clip_id"] == clip
        and offline["profile_id"] == initial["profile_id"]
        and offline["scenario_kind"] == initial["scenario_kind"]
        and (offline["frame_count"], offline["width"], offline["height"]) == shape
        and offline["frame_ticks"] == initial["frame_ticks"]
        and all(
            offline[k] == manifest[k]
            for k in ("bundle_digest", "mask_manifest_sha256", "config_sha256")
        ),
        "Reference identity differs from exact staged inputs",
    )
    expected = () if clip == "N0" else ALL_METRICS if clip.endswith("5") else (f"M{clip[1]}",)
    require(tuple(offline["required_metrics"]) == expected, "Unexpected required metrics")
    metrics = asset(run, "metrics.json").read_bytes()
    require(digest(metrics) == manifest["metrics_sha256"], "Candidate metrics hash mismatch")
    metrics = json.loads(metrics)
    require(
        metrics["run_id"] == manifest["run_id"]
        and metrics["scoring_code_sha"] == args.expected_candidate_sha,
        "Candidate score belongs to a different run or implementation",
    )
    case.update(
        {
            key: manifest[key]
            for key in (
                "clip_id",
                "profile_id",
                "code_sha",
                "run_id",
                "semantic_digest",
                "metrics_sha256",
                "bundle_digest",
                "input_manifest_sha256",
                "mask_manifest_sha256",
                "config_sha256",
            )
        }
    )
    case["reference_manifest_sha256"] = digest(offline_bytes)
    case["run_manifest_sha256"] = digest(asset(run, "run-manifest.json").read_bytes())
    case["media_sha256"] = {
        name: value["sha256"] for name, value in manifest["media"]["files"].items()
    }
    checks = case["checks"]
    repair_exit = case["stages"]["repair"]["exit_code"]
    checks["repair_state_and_exit"] = check_status(
        (manifest["repair_status"], repair_exit)
        == (("PARTIAL", 5) if sum(unresolved) else ("REPAIRED", 0))
    )
    checks["no_unresolved_pixels"] = check_status(not any(unresolved))
    if frozen is not None:
        profile = frozen["media"]
        options = {"-c:v": "encoder", "-preset": "preset", "-crf": "crf", "-g": "gop"}
        for item in manifest["media"]["files"].values():
            argv = item["encode_argv"]
            require(
                all(
                    argv.count(flag) == 1 and argv[argv.index(flag) + 1] == str(profile[key])
                    for flag, key in options.items()
                ),
                "Encoder settings differ from the approved media profile",
            )
    case["media"] = verify_media(
        run,
        before,
        after,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        expected_hashes=case["media_sha256"],
    )
    if clip == "N0":
        checks["no_degradation_identity"] = check_status(np.array_equal(before, after))
        return
    clean = read_stack(reference, offline["clean"]["files"], "clean", count, size)
    regions = {
        role: read_mask(
            reference, role, offline["regions"][role], count, size, constant=role in {"E", "B", "G"}
        )
        for role in ("E", "B", "G", "I", "contour", "ghost_truth")
    }
    if clip == "B5":
        declared = {
            role: {
                "kind": description["kind"],
                "pixels": [record["pixel_sha256"] for record in description["files"]],
            }
            for role, description in offline["regions"].items()
        }
        require(set(declared) == set(regions), "Unexpected Motion scoring region roles")
        case["scoring_regions_sha256"] = canonical_digest(declared)
        if frozen is not None:
            require(
                case["scoring_regions_sha256"] == frozen["motion"]["scoring_regions_sha256"],
                "Motion scoring areas differ from the approved region digests",
            )
        _, source_bytes = asset_record(
            reference, offline["source_evidence"], "source-evidence.json"
        )
        checks["motion_source"] = motion_replay(
            args, reference, json.loads(source_bytes), clean, regions, frozen
        )
    repair = read_mask(run / "masks", "repair", masks["roles"]["repair"], count, size)
    read_mask(fixture / "masks", "repair", masks["roles"]["repair"], count, size)
    for role, desc in masks["roles"].items():
        if role == "repair":
            continue
        for record in desc["files"]:
            asset_record(fixture / "masks", record, record["file"])
            asset_record(run / "masks", record, record["file"])
    roi = rectangle(masks["repair_roi"], size)
    protection = rectangle(masks["repair_roi"], size, masks["feather_px"])
    photo = rectangle(config["photometric"]["roi"], size) & config["photometric"]["enabled"]
    static = masks["roles"]["static_sample"]
    stable = read_mask(
        run / "masks", "static_sample", static, count, size, constant=static["kind"] == "constant"
    )
    stable = np.broadcast_to(stable, before.shape[:3])
    options = dict(
        scoring_roi=regions["E"], background=regions["B"], ghost=regions["G"], interior=regions["I"]
    )
    case["metrics"] = metric_checks(
        measure(before, clean, **options),
        measure(after, clean, **options),
        expected,
        combined=len(expected) > 1,
    )
    for key in expected:
        oracle = case["metrics"][key]
        candidate = metrics["metrics"][key]
        matching = True
        for field in ("input", "output"):
            value = oracle[field]["value_dn"]
            candidate_value = candidate[field]
            matching &= value == candidate_value or (
                value is not None
                and candidate_value is not None
                and math.isclose(value, candidate_value, abs_tol=1e-9, rel_tol=0)
            )
        checks[f"candidate_score_matches_oracle_{key}"] = check_status(matching)
    if frozen is not None and clip == "B5":
        rule = frozen["motion"].get("segmentation")
        if rule != MOTION_SEGMENTATION:
            raise Blocked("B requires the approved authored-palette segmentation")
        segment = segment_motion
    elif clip.startswith("H"):
        segment = segmentation(offline.get("segmentation"))
    else:
        segment = development_segmenter("B" if clip == "B5" else "A")
    case["guardrails"] = guardrail_checks(
        before,
        after,
        clean,
        roi=roi,
        protection=protection,
        repair=repair,
        photometric=photo,
        local_changed=local_changes(before, after, photo, stable),
        scoring_roi=regions["E"],
        background=regions["B"],
        ghost=regions["ghost_truth"],
        interior=regions["I"],
        silhouette=regions["contour"],
        segment=segment,
        appearance_only=expected == ("M4",),
        unresolved_pixels=unresolved,
        pts=[Fraction(frame["pts"]) for frame in manifest["frames"]],
        fps=12,
        max_changed_fraction=float(
            min(Fraction(1, 4), Fraction(config["repair"]["max_changed_fraction"]))
        ),
    )


def repeat_without_reference(args, fixture, output, case, python, baseline):
    reference = fixture / "reference"
    moved = output / "isolated-reference"
    snapshot = output / "input"
    masks = fixture / "masks"
    config = fixture / "config.json"
    repeat = output / "replayed-repair"
    checksums = {config: digest(config.read_bytes()), **inventory(snapshot), **inventory(masks)}
    modes = {path: stat.S_IMODE(path.stat().st_mode) for path in checksums}
    require(not moved.exists(), "Reference quarantine path already exists")
    reference.rename(moved)
    try:
        for path in modes:
            path.chmod(stat.S_IREAD)
        code = run_stage(
            [
                *python,
                "repair",
                "--input",
                str(snapshot),
                "--masks",
                str(masks),
                "--config",
                str(config),
                "--output",
                str(repeat),
            ],
            args.candidate,
            output,
            case["stages"],
            "repair_without_reference",
            allowed=(0, 5),
        )
    finally:
        for path, mode in modes.items():
            path.chmod(mode)
        moved.rename(reference)
    replay = load_json(asset(repeat, "run-manifest.json"))
    checks_unchanged = all(
        digest(path.read_bytes()) == expected for path, expected in checksums.items()
    )
    products_unchanged = all(
        digest(asset(repeat, frame["file"]).read_bytes()) == frame["file_sha256"]
        for frame in baseline["frames"]
    )
    case["checks"]["same_environment_replay_without_reference"] = check_status(
        checks_unchanged
        and products_unchanged
        and replay["frames"] == baseline["frames"]
        and replay["semantic_digest"] == baseline["semantic_digest"]
        and code == case["stages"]["repair"]["exit_code"]
    )
    require(repeat.resolve() == repeat and repeat.parent == output, "Unsafe replay cleanup path")
    shutil.rmtree(repeat)


def run_case(args, clip, fixture, output, case, frozen):
    python = [sys.executable, "-m", "prototype.video_restoration"]
    stages = case["stages"]
    if fixture is None:
        fixture = output / "fixture"
        sample = "B" if clip == "B5" else "A"
        run_stage(
            [
                *python[:-1],
                "prototype.video_restoration.formal_fixture",
                "--sample",
                sample,
                "--clip",
                clip,
                "--output",
                str(fixture),
                "--resvg",
                args.resvg,
            ],
            args.candidate,
            output,
            stages,
            "fixture",
        )
    snapshot = output / "input"
    run = output / "run"
    reference = fixture / "reference"
    frozen_fixture = inventory(fixture)
    if frozen is not None and clip == "B5":
        policy = frozen["motion"]
        initial = load_json(asset(fixture / "source", "video-manifest.json"))
        require(
            initial["bundle_digest"] == policy["input_bundle_digest"]
            and frozen_fixture[fixture / "masks/mask-manifest.json"]
            == policy["mask_manifest_sha256"]
            and frozen_fixture[fixture / "config.json"] == policy["config_sha256"],
            "B degraded source, masks, or configuration differ from G1",
        )
    run_stage(
        [*python, "ingest", "--source", str(fixture / "source"), "--output", str(snapshot)],
        args.candidate,
        output,
        stages,
        "ingest",
    )
    ingested = inventory(snapshot)
    run_stage(
        [
            *python,
            "repair",
            "--input",
            str(snapshot),
            "--masks",
            str(fixture / "masks"),
            "--config",
            str(fixture / "config.json"),
            "--output",
            str(run),
        ],
        args.candidate,
        output,
        stages,
        "repair",
        allowed=(0, 5),
    )
    media = ["--encoder", args.ffmpeg, "--ffprobe", args.ffprobe]
    run_stage(
        [*python, "export", "--run", str(run), *media, "--diagnostic"],
        args.candidate,
        output,
        stages,
        "export",
        allowed=(0, 5),
    )
    offline = ["--run", str(run), "--reference", str(reference), "--diagnostic"]
    run_stage(
        [*python, "evaluate", *offline, "--output", str(run / "metrics.json")],
        args.candidate,
        output,
        stages,
        "evaluate",
        allowed=(0, 5),
    )
    inspect_case(args, clip, fixture, output, case, frozen)
    repeat_without_reference(
        args, fixture, output, case, python, load_json(asset(run, "run-manifest.json"))
    )
    case["checks"]["sealed_fixture_unchanged"] = check_status(
        inventory(fixture) == frozen_fixture and inventory(snapshot) == ingested
    )
    if frozen is not None:
        return
    code = run_stage(
        [
            *python,
            "verify",
            *offline,
            *media,
            "--contract",
            CONTRACT,
            "--expected-code-sha",
            args.expected_candidate_sha,
            "--output",
            str(run / "acceptance-report.json"),
        ],
        args.candidate,
        output,
        stages,
        "candidate_verify",
        allowed=(0, 5, 7),
    )
    candidate_report = asset(run, "acceptance-report.json").read_bytes()
    published = load_json(asset(run, "run-manifest.json"))
    require(
        digest(candidate_report) == published["acceptance_report_sha256"],
        "Candidate report digest mismatch",
    )
    receipt = json.loads(candidate_report)
    require(
        receipt["run_id"] == case["run_id"] and receipt["metrics_sha256"] == case["metrics_sha256"],
        "Candidate verification receipt is for a different run or score",
    )
    require(
        (code == 0) == (published["status"] == "SUCCESS"),
        "Candidate published success with inconsistent exit",
    )
    case["candidate_verification"] = {
        "exit_code": code,
        "sha256": digest(candidate_report),
        "outcome": receipt["overall"],
    }


def run(args, report):
    report["implementation_sha"] = verify_checkout(args.candidate, args.expected_candidate_sha)
    report["contract_sha"] = verify_checkout(args.contract, args.expected_contract_sha)
    frozen = None
    fixture_root = None
    if args.profile == "A-holdout" or args.require_frozen:
        frozen, seal_path, report["freeze_sha256"] = frozen_policy(args)
        report["holdout_seal_sha256"] = digest(seal_path.read_bytes())
        report["qualification"] = "FROZEN"
        if args.policy_only:
            report["overall"] = "PASS"
            return
    else:
        require(args.holdout is None, "A holdout seal requires A-holdout or --require-frozen")
    report["tools"] = {
        name: version(binary, "--version" if name == "resvg" else "-version", args.candidate)
        for name, binary in (
            ("ffmpeg", args.ffmpeg),
            ("ffprobe", args.ffprobe),
            ("resvg", args.resvg),
        )
        if name != "resvg" or args.profile == "B"
    }
    if args.profile == "B" and report["tools"]["resvg"] not in {"0.44.0", "resvg 0.44.0"}:
        raise Blocked("B requires resvg 0.44.0")
    if frozen is not None:
        linux = frozen["tools"]["linux"]
        for name, tool_version in report["tools"].items():
            expected = linux[f"{name}_version"]
            if name == "resvg":
                matches = tool_version in {expected, f"resvg {expected}"}
            else:
                prefix = f"{name} version {expected}"
                matches = tool_version == prefix or tool_version.startswith(prefix + " ")
            if not matches:
                raise Blocked(f"{name} differs from the exact G1 tool version")
    if args.profile == "A-holdout":
        tool, _ = tracked(args.contract, HOLDOUT_TOOL)
        fixture_root = args.output / "holdout-fixtures"
        run_stage(
            [
                sys.executable,
                str(tool),
                "build",
                "--output",
                str(fixture_root),
                "--seal",
                str(seal_path),
            ],
            args.contract,
            args.output,
            report["setup"],
            "holdout_seal_validation",
        )
    for clip in CASES[args.profile]:
        case = {"overall": "BLOCKED", "stages": {}, "checks": {}}
        report["cases"][clip] = case
        output = args.output / clip
        output.mkdir()
        fixture = None if fixture_root is None else fixture_root / clip
        try:
            run_case(args, clip, fixture, output, case, frozen)
        except (Blocked, FileNotFoundError) as exc:
            case["checks"]["complete_evidence"] = {"status": "BLOCKED", "reason": str(exc)}
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            case["checks"]["complete_evidence"] = {"status": "FAIL", "reason": str(exc)}
        states = [
            item["status"]
            for key in ("stages", "checks", "metrics", "guardrails")
            for item in case.get(key, {}).values()
        ]
        states.append(case.get("media", {}).get("status", "BLOCKED"))
        case["overall"] = overall(states)
        print(f"{clip}: {case['overall']}", flush=True)
    report["overall"] = overall(case["overall"] for case in report["cases"].values())
    if frozen is not None and report["overall"] == "PASS":
        for clip, case in report["cases"].items():
            run = args.output / clip / "run"
            publish_verified_run(run, case, report)
            case["acceptance_report_sha256"] = digest(
                asset(run, "acceptance-report.json").read_bytes()
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-candidate-sha", required=True)
    parser.add_argument("--expected-contract-sha", required=True)
    parser.add_argument("--profile", choices=CASES, required=True)
    parser.add_argument("--holdout", type=Path)
    parser.add_argument("--require-frozen", action="store_true")
    parser.add_argument(
        "--policy-only", action="store_true", help="check G1 before running fixtures"
    )
    for name in ("ffmpeg", "ffprobe", "resvg"):
        parser.add_argument(f"--{name}", default=name)
    args = parser.parse_args(argv)
    if args.require_frozen and args.profile == "A-development":
        parser.error("--require-frozen is only supported for A-holdout and B")
    if args.policy_only and args.profile != "A-holdout" and not args.require_frozen:
        parser.error("--policy-only requires frozen qualification")
    args.candidate, args.contract, args.output = (
        path.resolve() for path in (args.candidate, args.contract, args.output)
    )
    if any(
        root == args.output or root in args.output.parents
        for root in (args.candidate, args.contract)
    ):
        parser.error("Evidence output must be outside the candidate and contract checkouts")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": "cvr-independent-staged-r1-002@1",
        "profile": args.profile,
        "phase": "policy" if args.policy_only else "evidence",
        "qualification": "FROZEN_REQUIRED"
        if args.profile == "A-holdout" or args.require_frozen
        else "DEVELOPMENT",
        "contract": CONTRACT,
        "contract_revision": REVISION,
        "overall": "BLOCKED",
        "ci_sha": None,
        "implementation_sha": args.expected_candidate_sha,
        "contract_sha": args.expected_contract_sha,
        "cases": {},
        "setup": {},
    }
    try:
        ci_root = Path(__file__).resolve().parents[1]
        report["ci_sha"] = git(ci_root, "rev-parse", "HEAD").decode().strip()
        require(
            os.environ.get("CI_SHA", report["ci_sha"]) == report["ci_sha"],
            "CI SHA differs from workflow",
        )
        run(args, report)
    except (Blocked, FileNotFoundError) as exc:
        report["reason"] = str(exc)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        report["overall"] = "CHANGES_REQUIRED"
        report["reason"] = str(exc)
    write_report(args.output / "report.json", report)
    reason = f" ({report['reason']})" if "reason" in report else ""
    print(f"{args.profile} {report['phase']}: {report['overall']}{reason}")
    return int(report["overall"] != "PASS")


class StagedEvidenceTest(unittest.TestCase):
    def test_missing_checks_do_not_pass_qualification(self):
        self.assertEqual(overall(["PASS", "BLOCKED"]), "BLOCKED")
        self.assertEqual(overall(["BLOCKED", "FAIL"]), "CHANGES_REQUIRED")
        self.assertEqual(overall(["PASS", "NOT_APPLICABLE"]), "PASS")

    def test_approval_binds_frozen_policy_and_contract_text(self):
        seal = {"path": "seal.json", "sha256": digest(b"sealed fixtures")}
        policy = {
            "schema_version": 1,
            "contract_version": CONTRACT,
            "draft_revision": REVISION,
            "holdout_seal": seal,
            "contract_documents": dict.fromkeys(CONTRACT_DOCUMENTS, digest(b"contract text")),
            "media": {"independent_decode_filter": RGB_DECODE_FILTER},
        }
        record = {
            **policy,
            "state": "FROZEN",
            "approval": {
                "decision": "APPROVE_G1",
                "approved_by": "test reviewer",
                "approved_at": "2026-11-27T09:00:00+08:00",
                "holdout_seal_sha256": seal["sha256"],
                "policy_sha256": canonical_digest(policy),
            },
        }
        files = dict.fromkeys(CONTRACT_DOCUMENTS, b"contract text")
        files[seal["path"]] = b"sealed fixtures"

        def read_file(_root, name):
            return Path(name), json.dumps(record).encode() if name == FREEZE else files[name]

        args = argparse.Namespace(contract=Path("contract"), holdout=None)
        with patch(f"{__name__}.tracked", side_effect=read_file):
            self.assertEqual(frozen_policy(args)[0], record)
            record["media"] = {"independent_decode_filter": "different"}
            with self.assertRaisesRegex(ValueError, "not bound to the frozen policy"):
                frozen_policy(args)
            record["media"] = policy["media"]
            files["docs/c-video-repair/requirements-v0.1.md"] = b"altered"
            with self.assertRaisesRegex(ValueError, "Approved contract text differs"):
                frozen_policy(args)

    def test_signed_observed_displacement_distinguishes_local_changes(self):
        original = np.full((2, 2, 3, 3), 100, dtype=np.uint8)
        original[1] += 5
        after = original.copy()
        after[1, 1, 0] -= 5
        after[1, 1, 1] -= 6
        after[1, 1, 2] -= 5
        photo = np.array([[False, False, False], [True, True, False]])
        stable = np.broadcast_to(np.array([[True, True, True], [False, False, False]]), (2, 2, 3))
        classified = local_changes(original, after, photo, stable)
        np.testing.assert_array_equal(classified[1, 1], [False, True, True])


if __name__ == "__main__":
    raise SystemExit(main())
