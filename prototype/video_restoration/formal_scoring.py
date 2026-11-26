"""Offline scoring of sealed RGB8 frames against R1-002 formulas.

This implementation is a practice-line diagnostic. Formal acceptance requires the
independent CI oracle, frozen holdouts/regions, and an approved output segmentation rule.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from . import formal_bundle as bundle
from .sequence import digest

LUMA = np.array([0.2126, 0.7152, 0.0722])
FORMULAS = {
    "M1": ("roi-mae", "E", 0.5),
    "M2": ("flicker", "B", 0.2),
    "M3": ("ghost-residual", "G", 1.0),
    "M4": ("appearance", "I", 0.5),
}
REQUIRED = {
    "S1": {"M1"},
    "S2": {"M2"},
    "S3": {"M3"},
    "S4": {"M4"},
    "S5": set(FORMULAS),
    "B5": set(FORMULAS),
    "N0": set(),
}


def _file(root: Path, record: dict, expected: str) -> Path:
    bundle.require(record["file"] == expected, 13, "unexpected offline artifact path")
    path = root / expected
    bundle.regular(path)
    bundle.require(
        digest(path.read_bytes()) == record["file_sha256"], 13, "offline artifact digest mismatch"
    )
    return path


def _source(root: Path, offline: dict, video: dict) -> dict:
    record = offline["source_evidence"]
    info = bundle.json_file(_file(root, record, "source-evidence.json"))
    basis = _file(root, info["basis_stimulus"], "basis-stimulus.json")
    summary = {
        "source_evidence_sha256": record["file_sha256"],
        "basis_stimulus_sha256": digest(basis.read_bytes()),
        "kind": info["kind"],
    }
    bundle.require(info["kind"] == video["scenario_kind"], 13, "source scenario mismatch")
    if info["kind"] == "synthetic-controlled":
        return {**summary, "generator": info["generator"]}
    bundle.require(info["kind"] == "svm-motion-controlled", 13, "unknown fixture source")
    document = bundle.json_file(_file(root, info["document"], "motion/document.svm.json"))
    timebase = document["animation"]["timebase"]["ticks_per_second"]
    bundle.require(
        timebase == video["timebase_ticks_per_second"] == info["timebase_ticks_per_second"]
        and info["canvas"] == [video["width"], video["height"]]
        and info["motion"]["quality"] == "FINAL"
        and info["motion"]["stale_frames"] == 0
        and info["motion"]["all_operations_clean"] is True
        and bool(re.fullmatch(r"[0-9a-f]{40}", info["svm_code_sha"]))
        and bool(info["rasterizer"]["version"]),
        13,
        "invalid Motion source declaration",
    )
    clean = offline["clean"]["files"]
    bundle.require(
        len(info["frames"]) == len(clean) == video["frame_count"],
        13,
        "incomplete Motion source evidence",
    )
    for i, (frame, rgb) in enumerate(zip(info["frames"], clean, strict=True)):
        tick = video["frame_ticks"][i]
        _file(root, frame["svg"], f"motion/svg/{tick:06}.svg")
        _file(root, frame["mask_svg"], f"motion/mask-svg/{tick:06}.svg")
        bundle.require(
            frame["tick"] == tick
            and frame["seconds"] == str(Fraction(tick, timebase))
            and frame["quality"] == "FINAL"
            and frame["stale"] is False
            and frame["clean_pixel_sha256"] == rgb["pixel_sha256"]
            and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", frame["raster_sha256"])),
            13,
            "Motion frame identity, timing or quality mismatch",
        )
    return {
        **summary,
        "document_id": document["document_id"],
        "document_sha256": info["document"]["file_sha256"],
        "revision_id": info["revision_id"],
        "snapshot_kind": info["snapshot_kind"],
        "svm_code_sha": info["svm_code_sha"],
        "rasterizer": info["rasterizer"],
        "frames": len(clean),
        "ticks": video["frame_ticks"],
        "timebase_ticks_per_second": timebase,
        "canvas": info["canvas"],
        "view_box": info["view_box"],
        "quality": "FINAL",
        "stale_frames": 0,
        "all_operations_clean": True,
        "frame_evidence_sha256": bundle.hashed(info["frames"]),
        "checks": (
            "Document, authored timebase, per-frame SVG files, source declarations "
            "and clean pixel hashes"
        ),
        "independent_raster_replay": "NOT_RUN",
    }


def _read_region(root: Path, description: dict, role: str, video: dict) -> np.ndarray:
    constant = role in ("E", "B", "G")
    bundle.require(
        description["kind"] == ("constant" if constant else "per-frame"),
        13,
        "offline region temporal kind mismatch",
    )
    length = 1 if constant else video["frame_count"]
    values, records = bundle.sequence(
        root / role, length, video["width"], video["height"], mask=(role != "clean")
    )
    bundle.require(records == description["files"], 13, "offline frame/region record mismatch")
    return values[0] if constant else values


def _measure(images: np.ndarray, clean: np.ndarray, regions: dict) -> dict:
    e, b, g, interiors, contours = (regions[k] for k in ("E", "B", "G", "I", "contour"))
    error_sum = ghost_sum = ghost_peak = ghost_hits = 0
    previous_bg = previous_raw = None
    temporal_steps, raw_steps, bg_lumas = [], [], []
    means, spatial, gradients, support, adjacent = [], [], [], [], []
    for i, (rgb, ref, interior, contour) in enumerate(
        zip(images, clean, interiors, contours, strict=True)
    ):
        residual = rgb.astype(np.int16) - ref
        error_sum += int(abs(residual[e]).sum())
        if g.any():
            ghost = abs(residual[g])
            ghost_peak = max(ghost_peak, int(ghost.max()))
            excess = np.maximum(0, ghost - 1)
            ghost_sum += int(excess.sum())
            ghost_hits += int((excess.max(axis=1) > 8).sum())
        if b.any():
            signed_bg = residual[b]
            raw_bg = rgb[b].astype(np.int16)
            bg_lumas.append(float(raw_bg.mean(axis=0) @ LUMA))
            if previous_bg is not None:
                temporal_steps.append(float(abs(signed_bg - previous_bg).mean()))
                raw_steps.append(float(abs(raw_bg - previous_raw).mean()))
            previous_bg, previous_raw = signed_bg, raw_bg
        if interior.any():
            values = residual[interior]
            means.append(values.mean(axis=0))
            spatial.append(float(abs(values).mean()))
        ys, xs = np.nonzero(contour)
        support.append(len(ys))
        if len(ys):
            # Differences are taken on the canvas, then cropped to the inclusive contour bbox.
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            luma = rgb @ LUMA
            yy = np.arange(y0, y1)
            xx = np.arange(x0, x1)
            box = luma[y0:y1, x0:x1]
            dx = luma[np.ix_(yy, np.minimum(xx + 1, rgb.shape[1] - 1))] - box
            dy = luma[np.ix_(np.minimum(yy + 1, rgb.shape[0] - 1), xx)] - box
            gradients.append(float(np.hypot(dx, dy).mean()))
        if e.any():
            errors = {
                str(j): float(abs(rgb[e].astype(np.int16) - clean[j, e]).mean())
                for j in range(max(0, i - 1), min(i + 2, len(clean)))
            }
            adjacent.append(
                {
                    "index": i,
                    "mae_by_reference_index": errors,
                    "same_index_is_nearest": errors[str(i)] == min(errors.values()),
                }
            )
    rgb_std = np.std(means, axis=0) if len(means) == len(images) else None
    rgb_means = np.asarray(means)
    return {
        "M1": error_sum / (int(e.sum()) * len(images) * 3) if e.any() else None,
        "M2": float(np.mean(temporal_steps)) if temporal_steps else None,
        "M3": ghost_sum / (int(g.sum()) * len(images) * 3) if g.any() else None,
        "M4": float(rgb_std.mean()) if rgb_std is not None else None,
        "M2_details": {
            "per_step_mae_dn": temporal_steps,
            "max_step_dn": max(temporal_steps, default=None),
        },
        "M3_details": {
            "max_channel_residual_dn": ghost_peak if g.any() else None,
            "pixels_any_excess_above_8_dn": ghost_hits,
        },
        "M4_details": {
            "per_channel_population_std_dn": rgb_std.tolist() if rgb_std is not None else None,
            "luma_population_std_dn": float(np.std(rgb_means @ LUMA))
            if rgb_std is not None
            else None,
        },
        "spatial": float(np.mean(spatial)) if len(spatial) == len(clean) else None,
        "edge": float(np.mean(gradients)) if len(gradients) == len(clean) else None,
        "contour_support": support,
        "adjacent": adjacent,
        "background_luma_std": float(np.std(bg_lumas)) if bg_lumas else None,
        "raw_temporal_max": max(raw_steps, default=None),
    }


def _metrics(required: set, measured: tuple, region_hashes: dict, invalid: dict) -> dict:
    before, after = measured
    rows = {}
    for name, (formula, role, floor) in FORMULAS.items():
        low, high = before.get(name), after.get(name)
        proposal = 0.2 if len(required) > 1 else 0.3
        ratio = None
        if name not in required:
            status, reason = "NOT_APPLICABLE", "not a declared degradation for this clip"
        elif (
            name in invalid
            or low is None
            or high is None
            or not all(map(math.isfinite, (low, high)))
        ):
            status, reason = "METRIC_UNDEFINED", invalid.get(name, "empty required scoring support")
        elif low < floor:
            status, reason = "METRIC_UNDEFINED", "input below the proposed minimum degradation"
        else:
            ratio = 1 - high / low
            status, reason = (
                ("MET", "draft threshold met")
                if ratio >= proposal
                else ("FAILED", "improvement below the proposed threshold")
            )
        rows[name] = {
            "formula_id": f"cvr-metric-{formula}@0.1-r1.002",
            "unit": "DN",
            "required": name in required,
            "region": role,
            "region_sha256": region_hashes.get(role),
            "input": low,
            "output": high,
            "input_floor": floor,
            "relative_improvement": ratio,
            "threshold": proposal if name in required else None,
            "status": status,
            "reason": reason,
        }
        key = f"{name}_details"
        if key in before:
            rows[name]["diagnostics"] = {"input": before[key], "output": after[key]}
    return rows


def _check(test: bool, actual: object, expected: str) -> dict:
    return {"status": "MET" if test else "FAILED", "actual": actual, "expected": expected}


def development_guards(before, after, clean, contours, photo):
    """Measure actual edge/motion and seam residuals on the authored development scenes."""
    kernel = np.ones((3, 3), np.uint8)
    collar = cv2.morphologyEx(photo.astype(np.uint8), cv2.MORPH_GRADIENT, kernel).astype(bool)
    measurements = []
    motion = []
    for i, contour in enumerate(contours):
        edge = cv2.morphologyEx(contour.astype(np.uint8), cv2.MORPH_GRADIENT, kernel).astype(bool)
        current = {}
        for label, rgb in (("input", before[i]), ("output", after[i])):
            residual = abs(rgb.astype(np.int16) - clean[i])
            current[label] = {
                "silhouette_edge_mae_dn": float(residual[edge].mean()),
                "photometric_boundary_mae_dn": float(residual[collar].mean()),
            }
        measurements.append(current)
        if i:
            area = contour | contours[i - 1]
            target = clean[i, area].astype(np.int16) - clean[i - 1, area]
            observed = after[i, area].astype(np.int16) - after[i - 1, area]
            motion.append(
                {
                    "tick": i,
                    "reference_changed": int(np.any(target != 0, axis=1).sum()),
                    "output_changed": int(np.any(observed != 0, axis=1).sum()),
                    "temporal_rgb_mae_dn": float(abs(target - observed).mean()),
                }
            )
    return {
        "DEV_EDGE_AND_SEAM": _check(
            all(row["output"][k] <= row["input"][k] for row in measurements for k in row["input"]),
            measurements,
            "no frame increases RGB MAE on the silhouette or photometric boundary collar",
        ),
        "DEV_AUTHORED_MOTION": _check(
            all(not row["reference_changed"] or row["output_changed"] for row in motion),
            motion,
            "each genuinely changing reference interval remains moving",
        ),
    }


def _guards(before, after, masks, mask_manifest, config, video, manifest) -> dict:
    changed = np.any(before != after, axis=3)
    _, height, width, _ = before.shape
    x, y, r, b = mask_manifest["repair_roi"]
    f = mask_manifest["feather_px"]
    roi = bundle.rectangle([x, y, r, b], width, height)
    protection = bundle.rectangle(
        [max(0, x - f), max(0, y - f), min(width, r + f), min(height, b + f)], width, height
    )
    photo = (
        bundle.rectangle(config["photometric"]["roi"], width, height)
        & config["photometric"]["enabled"]
    )
    repair = masks["repair"]
    outside = int((changed & ~protection).sum())
    outside_auth = int((changed & ~(repair | photo)).sum())
    unknown_offset = False
    inconsistent = 0
    for i, frame in enumerate(manifest["frames"]):
        selected = changed[i] & ~repair[i] & photo
        if not selected.any():
            continue
        offset = frame.get("evidence", {}).get("rgb_offset_from_first_observation_dn")
        if (
            not isinstance(offset, list)
            or len(offset) != 3
            or any(type(x) is not int for x in offset)
        ):
            unknown_offset = True
            continue
        observed = before[i, selected].astype(np.int16) - after[i, selected]
        inconsistent += int((observed != offset).any(axis=1).sum())
    fractions = ((changed & roi).sum(axis=(1, 2)) / int(roi.sum())).tolist()
    cap = Fraction(config["repair"]["max_changed_fraction"])
    endpoints = [manifest["frames"][i]["unresolved_pixels"] for i in (0, -1)]
    endpoint_requests = [int(repair[i].sum()) for i in (0, -1)]
    attr = _check(
        outside_auth == 0 and inconsistent == 0,
        {"outside_writes": outside_auth, "inconsistent_photometric_pixels": inconsistent},
        "only authorized local writes and recorded integer photometric correction",
    )
    if unknown_offset and attr["status"] == "MET":
        attr.update(status="NOT_RUN", reason="missing photometric attribution")
    t7 = _check(
        not any(endpoints),
        {"endpoint_requested": endpoint_requests, "endpoint_unresolved": endpoints},
        "resolved or explicitly abstained; acceptance needs zero unresolved",
    )
    if any(endpoint_requests) and t7["status"] == "MET":
        t7.update(
            status="NOT_RUN",
            reason="per-coordinate endpoint donor evidence needs independent replay",
        )
    return {
        "Q1": _check(outside == 0, outside, "zero changed pixels outside protection domain"),
        "Q9": _check(outside == 0, outside, "zero changed pixels outside protection domain"),
        "Q7": _check(
            outside_auth == 0,
            {
                "inside_repair": int((changed & repair).sum()),
                "feather_extension": int((changed & protection & ~roi).sum()),
                "outside_mask_in_photometric_roi": int((changed & ~repair & photo).sum()),
                "outside_authorization": outside_auth,
            },
            "zero unauthorized changes",
        ),
        "Q8": attr,
        "Q10": _check(
            all(x <= cap for x in fractions), fractions, f"changed ROI fraction per frame <= {cap}"
        ),
        "T7": t7,
    }


def score(
    run: Path, reference: Path, manifest: dict, before: np.ndarray, after: np.ndarray
) -> dict:
    """Calculate diagnostics, rechecking the reference binding before use."""
    bundle.require(
        not (
            reference.resolve().is_relative_to(run.resolve())
            or run.resolve().is_relative_to(reference.resolve())
        ),
        14,
        "reference/run overlap",
    )
    video = bundle.json_file(run / "input/video-manifest.json")
    path = reference / "reference-manifest.json"
    offline = bundle.json_file(path)
    bundle.version(offline)
    clip = offline["clip_id"]
    holdout = clip in ("H1", "H2", "H3", "H4", "H5")
    if holdout:
        reference_format = "cvr-holdout-reference@1"
        qualification = "SEALED_HOLDOUT"
        profile = "cvr-holdout-a@1"
        scenario = "synthetic-controlled"
        metric_case = "S" + clip[1:]
    else:
        reference_format = "cvr-development-reference@1"
        qualification = "DRAFT_DEVELOPMENT_ONLY"
        profile = video["profile_id"]
        scenario = video["scenario_kind"]
        metric_case = clip
    bundle.require(
        offline.get("reference_format") == reference_format
        and offline.get("qualification") == qualification
        and offline.get("profile_id") == profile
        and offline.get("scenario_kind") == scenario,
        1,
        "unsupported reference role",
    )
    for key in (
        "frame_count",
        "width",
        "height",
        "frame_ticks",
        "bundle_digest",
        "scenario_kind",
        "clip_id",
        "profile_id",
    ):
        bundle.require(offline[key] == video[key], 13, "reference does not belong to this input")
    for key in ("bundle_digest", "config_sha256", "mask_manifest_sha256"):
        bundle.require(offline[key] == manifest[key], 13, "reference/run binding mismatch")
    bundle.require(
        metric_case in REQUIRED and set(offline["required_metrics"]) == REQUIRED[metric_case],
        13,
        "unknown or inconsistent scoring profile",
    )
    required = REQUIRED[metric_case]
    bundle.require(
        before.shape == after.shape == (video["frame_count"], video["height"], video["width"], 3)
        and before.dtype == after.dtype == np.uint8,
        13,
        "scoring frame shape mismatch",
    )
    masks, mask_manifest = bundle.read_masks(run / "masks", video)
    config = bundle.read_config(run / "config.json", video, mask_manifest)
    source = _source(reference, offline, video)
    guards = _guards(before, after, masks, mask_manifest, config, video, manifest)
    hashes = {role: bundle.hashed(rec) for role, rec in offline["regions"].items()}
    if clip == "N0":
        changed = int(np.any(before != after, axis=3).sum())
        guards["N0"] = _check(changed == 0, changed, "exact input/output pixel equality")
        rows = _metrics(required, ({}, {}), {}, {})
    else:
        clean = _read_region(reference, offline["clean"], "clean", video)
        regions = {
            key: _read_region(reference, offline["regions"][key], key, video)
            for key in ("E", "B", "G", "I", "contour", "ghost_truth")
        }
        invalid = {}
        if np.any(regions["G"] & regions["contour"]):
            invalid["M3"] = "G intersects at least one reference silhouette"
        if np.any(regions["I"] & ~regions["contour"]):
            invalid["M4"] = "I is not contained in the reference silhouette"
        bg = regions["B"]
        photo = bundle.rectangle(config["photometric"]["roi"], video["width"], video["height"])
        hazards = np.any(regions["contour"] | regions["ghost_truth"], axis=0)
        unsafe = cv2.dilate(hazards.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        static = bool(bg.any()) and np.all(clean[:, bg] == clean[0, bg]) and not np.any(bg & unsafe)
        if not static or np.any(bg & ~photo):
            invalid["M2"] = (
                "B must be nonempty, static, separated and within the photometric domain"
            )
        a = _measure(before, clean, regions)
        b = _measure(after, clean, regions)
        c = _measure(clean, clean, regions)
        rows = _metrics(required, (a, b), hashes, invalid)
        edge = [k["edge"] for k in (a, b, c)]
        ratio = (
            b["edge"] / a["edge"] if all(v is not None for v in edge) and a["edge"] > 0 else None
        )
        guards["Q5"] = _check(
            ratio is not None and ratio >= 0.9,
            {
                "Ee_input": edge[0],
                "Ee_output": edge[1],
                "Ee_reference": edge[2],
                "ratio": ratio,
                "per_frame_contour_pixels": a["contour_support"],
            },
            "Ee(out)/Ee(in) >= 0.90; bbox encloses declared reference foreground",
        )
        factor = 0.9 if metric_case == "S4" else 1.05
        s_in, s_out = a["spatial"], b["spatial"]
        guards["Q6"] = _check(
            s_in is not None and s_out is not None and s_out <= factor * s_in,
            {"S_input": s_in, "S_output": s_out},
            f"S(out) <= {factor} * S(in)",
        )
        for key, term, tolerance in (
            ("T1", "background_luma_std", 0.2),
            ("T2", "raw_temporal_max", 1.0),
        ):
            v_in, v_out = a[term], b[term]
            guards[key] = _check(
                v_in is not None and v_out is not None and v_out <= v_in + tolerance,
                {"input": v_in, "output": v_out},
                f"output <= input + {tolerance} DN",
            )
        guards["T5"] = _check(
            bool(b["adjacent"]) and all(v["same_index_is_nearest"] for v in b["adjacent"]),
            b["adjacent"],
            "same-index MAE in E <= each existing adjacent reference MAE",
        )
        guards["T6"] = _check(
            bool(static),
            {"B_support": int(bg.sum()), "within_2px": int((bg & unsafe).sum())},
            "B constant in clean sequence with two-pixel target/ghost clearance",
        )
        guards.update(development_guards(before, after, clean, regions["contour"], photo))
        for key in ("Q2", "Q3", "Q4", "T3", "T4"):
            guards[key] = {
                "status": "NOT_RUN",
                "reason": "G1 output contour segmentation rule is unavailable",
                "per_frame_reference_contour_pixels": a["contour_support"],
            }
    failures = [
        k
        for k, row in {**rows, **guards}.items()
        if row["status"] in ("FAILED", "METRIC_UNDEFINED")
    ]
    return {
        "reference_sha256": digest(path.read_bytes()),
        "scoring_region_sha256": hashes,
        "reference_clean_sha256": bundle.hashed(offline["clean"]) if "clean" in offline else None,
        "source_evidence": source,
        "scoring_profile": qualification,
        "metrics": rows,
        "guardrails": guards,
        "quality_failures": failures,
        "missing_checks": [k for k, row in guards.items() if row["status"] == "NOT_RUN"]
        + ["G1_FREEZE", "INDEPENDENT_CI_ORACLE", "HUMAN_REVIEW"],
        "independent_acceptance": False,
    }
