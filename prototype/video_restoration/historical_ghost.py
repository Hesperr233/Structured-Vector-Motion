"""Offline forensic audit of the preserved R1-002 ghost experiment (never used by repair)."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from . import motion_scene as spec
from .repair import PARAMETERS, _flicker, _stationary_donors
from .sequence import digest, load_masks, load_sequence, write_json


def audit(archive: Path, working: Path, output: Path) -> None:
    report = json.loads((archive / "run-manifest.json").read_bytes())
    options = {
        "names": tuple(f"{i:06}.png" for i in range(96)),
        "width": spec.WIDTH,
        "height": spec.HEIGHT,
    }
    raw, inputs = load_sequence(working / "input", **options)
    masks, records = load_masks(working / "mask", **options)
    if report["inputs"] != inputs or report["masks"] != records:
        raise ValueError("working input differs from the historical evidence")
    # The following reconstruction is explicitly the legacy whole-shot calculation.
    normalized, usable, _, _ = _flicker(raw, masks["roi"], masks["stable"])
    suspect = masks["ghost"] | masks["defect"]
    donors, supported = _stationary_donors(normalized, usable, suspect, masks["ghost"].any(axis=0))
    interior, _ = load_sequence(working / "evaluation/interior", mask=True, **options)
    edge, _ = load_sequence(working / "evaluation/edge", mask=True, **options)
    source = json.loads((working / "source-manifest.json").read_bytes())
    if digest((working / "source-manifest.json").read_bytes()) != report["source_manifest_sha256"]:
        raise ValueError("offline source provenance mismatch")
    clean, clean_records = load_sequence(working / "evaluation/clean", **options)
    after, after_records = load_sequence(working / "frames-repaired", **options)
    if (
        source["evaluation"]["clean_frames"] != clean_records
        or report["frames_repaired"] != after_records
    ):
        raise ValueError("sealed clean or repaired frames differ from the historical evidence")
    for role in ("interior", "edge"):
        _, rec = load_sequence(working / "evaluation" / role, mask=True, **options)
        expected = source["evaluation"][
            "object_interior_masks" if role == "interior" else "motion_edge_masks"
        ]
        if rec != expected:
            raise ValueError("offline contour digest mismatch")
    # This offline exclusion is intentionally conservative, including the moving edge
    # and its antialias fringe. It is never exposed as a causal repair input.
    kernel = np.ones((7, 7), np.uint8)
    near_object = (
        np.stack([cv2.dilate(frame.astype(np.uint8), kernel) != 0 for frame in interior]) | edge
    )
    reliable = usable & ~suspect & ~near_object
    totals = {}
    rows = []
    for i, requested in enumerate(masks["ghost"]):
        residual = np.max(abs(normalized[i].astype(np.int16) - donors), axis=2)
        k = np.ones((3, 3), np.uint8)
        span = np.max(cv2.dilate(normalized[i], k) - cv2.erode(normalized[i], k), axis=2)
        resolved = (
            requested
            & supported
            & (
                (residual == 0)
                | (
                    usable[i]
                    & (residual <= PARAMETERS["ghost_max_residual_dn"])
                    & (span <= PARAMETERS["ghost_max_local_range_dn"])
                )
            )
        )
        refused = requested & ~resolved
        old = report["frame_reports"][i]["ghost"]
        if (
            int(refused.sum()) != old["refused_pixels"]
            or int((resolved & (residual != 0)).sum()) != old["changed_pixels"]
        ):
            raise ValueError(f"legacy reproduction differs at frame {i}")
        start = max(i - 12, 0)
        ys, xs = np.nonzero(refused)
        previous = reliable[start:i, ys, xs]
        previous_clear = (usable & ~suspect)[start:i, ys, xs]
        # Compare independently observed RGB values after correcting only relative
        # exposure against the first static sample. No clean RGB enters this estimate.
        observed = raw[start:i, ys, xs].astype(np.int16)
        patch = masks["stable"][0]
        for k, j in enumerate(range(start, i)):
            delta = raw[j, patch].astype(np.int16) - raw[0, patch]
            observed[k] -= np.rint(np.median(delta, axis=0)).astype(np.int16)
        count = previous.sum(axis=0)
        if i > 0:
            hi = np.max(np.where(previous[..., None], observed, -1), axis=0)
            lo = np.min(np.where(previous[..., None], observed, 256), axis=0)
            agrees = (hi - lo).max(axis=1) <= 2
        else:
            agrees = np.zeros(len(ys), dtype=bool)
        future = reliable[i + 1 : i + 13, ys, xs].sum(axis=0)
        original_error = abs(raw[i, requested].astype(np.int16) - clean[i, requested]).max(axis=1)
        repaired_error = abs(after[i, requested].astype(np.int16) - clean[i, requested]).max(axis=1)
        evidence = {
            "reference_recovered_within_1_dn": int(
                ((original_error > 1) & (repaired_error <= 1)).sum()
            ),
            "reference_initially_within_1_dn": int((original_error <= 1).sum()),
            "reference_after_above_1_dn": int((repaired_error > 1).sum()),
            "historical_requested": old["requested_pixels"],
            "historical_changed": old["changed_pixels"],
            "historical_observed_equal": int((resolved & (residual == 0)).sum()),
            "historical_unresolved": old["refused_pixels"],
            "rejected_by_historical_majority": int((requested & ~supported).sum()),
            "rejected_by_edge_residual_or_clipping": int((refused & supported).sum()),
            "unresolved_lacking_two_past_frames": int((count < 2).sum()) if i < 2 else 0,
            "unresolved_excluded_by_possible_foreground": int(
                ((count < 2) & (previous_clear.sum(axis=0) >= 2)).sum()
            ),
            "unresolved_lacking_two_past_usable_unmasked_observations": int(
                ((previous_clear.sum(axis=0) < 2) & (i >= 2)).sum()
            ),
            "unresolved_with_past_rgb_disagreement": int(((count >= 2) & ~agrees).sum()),
            "unresolved_with_two_consistent_offline_screened_past_observations": int(
                ((count >= 2) & agrees).sum()
            ),
            "unresolved_only_future_window_has_two_observations": int(
                ((count < 2) & (future >= 2)).sum()
            ),
        }
        rows.append(
            {
                "index": i,
                "past_window": list(range(start, i)),
                "historical_refusal_reasons": old["refusal_reasons"],
                **evidence,
            }
        )
        for key, val in evidence.items():
            totals[key] = totals.get(key, 0) + val
    if output.exists():
        raise ValueError("audit output exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        output,
        {
            "kind": "offline historical investigation",
            "historical_manifest_sha256": digest((archive / "run-manifest.json").read_bytes()),
            "window_radius": 12,
            "totals": totals,
            "limitations": [
                "Original donor consensus and photometric fit included future observations.",
                "Past counts are an offline diagnostic with conservative silhouette exclusion.",
                "Historical package did not supply an authorized donor_valid channel. "
                "No historical pixel is reclassified or rewritten.",
                "The future count is hindsight only. It cannot license repair at an earlier PTS.",
            ],
            "frames": rows,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "working", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    audit(args.archive, args.working, args.output)


if __name__ == "__main__":
    main()
