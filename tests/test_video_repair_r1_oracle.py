"""Independent R1-002 draft pixel metrics and guardrails.

The caller binds masks, segmentation and provenance to the fixture. These checks
do not publish a run state or treat an unapproved fixture as acceptance evidence.
"""

import math
import unittest
from collections.abc import Callable, Sequence
from fractions import Fraction

import numpy as np
from numpy.typing import NDArray

RGB = NDArray[np.uint8]
Mask = NDArray[np.bool_]
LUMA = np.array([0.2126, 0.7152, 0.0722])
MIN_INPUT_DN = {"M1": 0.5, "M2": 0.2, "M3": 1.0, "M4": 0.5}


def _shape(*clips: RGB) -> tuple[int, int, int]:
    shape = clips[0].shape
    if (
        len(shape) != 4
        or shape[-1] != 3
        or not all(shape[:3])
        or any(clip.dtype != np.uint8 or clip.shape != shape for clip in clips)
    ):
        raise ValueError(
            "Expected matching nonempty RGB8 clips with shape (time, height, width, 3)"
        )
    return shape[:3]


def _mask(value: Mask | None, shape: tuple[int, ...], name: str) -> Mask | None:
    if value is not None and (value.shape != shape or value.dtype != np.bool_):
        raise ValueError(f"{name} must be a boolean mask with shape {shape}")
    return value


def _frame_masks(value: Mask | None, shape: tuple[int, int, int], name: str) -> Mask | None:
    if value is not None and value.shape == shape[1:]:
        value = np.broadcast_to(value, shape)
    return _mask(value, shape, name)


def _residual(frame: RGB, reference: RGB, mask: Mask) -> NDArray[np.int16]:
    return frame[mask].astype(np.int16) - reference[mask]


def measure(
    frames: RGB,
    reference: RGB,
    *,
    scoring_roi: Mask | None = None,
    background: Mask | None = None,
    ghost: Mask | None = None,
    interior: Mask | None = None,
) -> dict:
    """Compute M1–M4 on frozen regions with frame-by-frame signed RGB arithmetic."""
    shape = _shape(frames, reference)
    scoring_roi = _mask(scoring_roi, shape[1:], "E")
    background = _mask(background, shape[1:], "B")
    ghost = _mask(ghost, shape[1:], "G")
    interior = _mask(interior, shape, "I")
    result = {key: {"value_dn": None, "reason": "empty or missing region"} for key in MIN_INPUT_DN}
    if scoring_roi is not None and scoring_roi.any():
        values = [
            np.abs(_residual(frame, clean, scoring_roi)).mean()
            for frame, clean in zip(frames, reference, strict=True)
        ]
        result["M1"] = {"value_dn": float(np.mean(values))}
    if background is not None and background.any() and shape[0] > 1:
        steps = []
        previous = None
        for frame, clean in zip(frames, reference, strict=True):
            current = _residual(frame, clean, background)
            if previous is not None:
                steps.append(float(np.abs(current - previous).mean()))
            previous = current
        result["M2"] = {
            "value_dn": float(np.mean(steps)),
            "max_step_dn": max(steps),
            "steps_dn": steps,
        }
    if ghost is not None and ghost.any():
        total = max_channel = large_pixels = 0
        for frame, clean in zip(frames, reference, strict=True):
            absolute = np.abs(_residual(frame, clean, ghost))
            excess = np.maximum(absolute - 1, 0)
            total += int(excess.sum())
            max_channel = max(max_channel, int(absolute.max()))
            large_pixels += np.count_nonzero(np.any(excess > 8, axis=1))
        result["M3"] = {
            "value_dn": total / (shape[0] * np.count_nonzero(ghost) * 3),
            "max_channel_residual_dn": max_channel,
            "pixels_exceeding_8_dn_after_floor": int(large_pixels),
        }
    if interior is not None and np.all(np.any(interior, axis=(1, 2))):
        channel_means = np.array(
            [
                _residual(frame, clean, region).mean(axis=0)
                for frame, clean, region in zip(frames, reference, interior, strict=True)
            ]
        )
        channel_std = channel_means.std(axis=0)
        result["M4"] = {
            "value_dn": float(channel_std.mean()),
            "rgb_std_dn": channel_std.tolist(),
            "luma_std_dn": float((channel_means @ LUMA).std()),
        }
    return result


def metric_checks(before: dict, after: dict, required: Sequence[str], *, combined: bool) -> dict:
    """Apply draft thresholds to each required metric separately."""
    if not set(required) <= MIN_INPUT_DN.keys():
        raise ValueError("Unknown required metric")
    if combined and set(required) != MIN_INPUT_DN.keys():
        raise ValueError("Combined cases require M1 through M4")
    percent = 20 if combined else 30
    threshold = percent / 100
    result = {}
    for key, minimum in MIN_INPUT_DN.items():
        original, output = before[key]["value_dn"], after[key]["value_dn"]
        detail = {"input": before[key], "output": after[key], "improvement": None}
        if key not in required:
            result[key] = {
                "status": "NOT_APPLICABLE",
                "reason": "not required by scenario",
                **detail,
            }
        elif (
            original is None
            or output is None
            or not math.isfinite(original)
            or not math.isfinite(output)
            or original < minimum
        ):
            result[key] = {"status": "FAIL", "reason": "METRIC_UNDEFINED", **detail}
        else:
            improvement = 1 - output / original
            result[key] = {
                **detail,
                "status": "PASS" if 100 * output <= (100 - percent) * original else "FAIL",
                "threshold": threshold,
                "improvement": improvement,
            }
    return result


def _check(passed: bool, **detail: object) -> dict:
    return {"status": "PASS" if passed else "FAIL", **detail}


def _blocked(reason: str) -> dict:
    return {"status": "BLOCKED", "reason": reason}


def _authorization(
    before: RGB,
    after: RGB,
    roi: Mask,
    protection: Mask,
    repair: Mask | None,
    photometric: Mask | None,
    local_changed: Mask | None,
    max_changed_fraction: float,
) -> dict:
    outside = feather = in_repair = photometric_only = unauthorized = stage_mismatch = 0
    fractions = []
    for i, (original, output) in enumerate(zip(before, after, strict=True)):
        changed = np.any(original != output, axis=-1)
        outside += np.count_nonzero(changed & ~protection[i])
        feather += np.count_nonzero(changed & protection[i] & ~roi[i])
        roi_size = np.count_nonzero(roi[i])
        if not roi_size:
            raise ValueError("ROI must contain pixels in every frame")
        fractions.append(np.count_nonzero(changed & roi[i]) / roi_size)
        if repair is not None and photometric is not None:
            in_repair += np.count_nonzero(changed & repair[i])
            photometric_only += np.count_nonzero(changed & photometric[i] & ~repair[i])
            unauthorized += np.count_nonzero(changed & ~(repair[i] | photometric[i]))
            if local_changed is not None:
                stage_mismatch += np.count_nonzero(local_changed[i] & ~repair[i])
                stage_mismatch += np.count_nonzero(changed & ~(local_changed[i] | photometric[i]))
    if repair is None or photometric is None:
        scope = _blocked("requires repair and photometric authorization masks")
    else:
        counts = {
            "repair_mask_pixels": int(in_repair),
            "feather_extension_pixels": int(feather),
            "photometric_only_pixels": int(photometric_only),
            "unauthorized_pixels": int(unauthorized),
        }
        if unauthorized or stage_mismatch or local_changed is not None:
            scope = _check(
                unauthorized == stage_mismatch == 0, stage_mismatch=int(stage_mismatch), **counts
            )
        else:
            scope = {**_blocked("requires observed local replacement mask"), **counts}
    return {
        "Q1/Q9": _check(outside == 0, changed=int(outside)),
        "Q7/Q8": scope,
        "Q10": _check(
            max(fractions) <= max_changed_fraction,
            max_fraction=max(fractions),
            per_frame=fractions,
            threshold=max_changed_fraction,
        ),
    }


def _contours(after: RGB, silhouette: Mask | None, segment: Callable[[RGB], Mask] | None) -> dict:
    keys = ("Q2", "Q3", "Q4", "T4")
    if silhouette is None or segment is None:
        return {
            key: _blocked("requires frozen silhouette and output segmentation rule") for key in keys
        }
    ious, output_centers, reference_centers, support = [], [], [], []
    for original, frame in zip(silhouette, after, strict=True):
        prediction = _mask(segment(frame), original.shape, "prediction")
        if prediction is None or not prediction.any() or not original.any():
            return {key: _check(False, reason="empty reference or output contour") for key in keys}
        ious.append(
            np.count_nonzero(original & prediction) / np.count_nonzero(original | prediction)
        )
        support.append((int(np.count_nonzero(original)), int(np.count_nonzero(prediction))))
        reference_centers.append(np.argwhere(original).mean(axis=0))
        output_centers.append(np.argwhere(prediction).mean(axis=0))
    goal, actual = np.array(reference_centers), np.array(output_centers)
    errors = np.linalg.norm(goal - actual, axis=1)
    tied, mismatched = [], []
    for i, center in enumerate(actual):
        for j in (i - 1, i + 1):
            if 0 <= j < len(goal):
                distance = np.linalg.norm(center - goal[j])
                same_position = np.array_equal(goal[i], goal[j])
                if math.isclose(distance, errors[i], abs_tol=1e-12, rel_tol=0):
                    (tied if same_position else mismatched).append((i, j))
                elif distance < errors[i]:
                    mismatched.append((i, j))
    ref_moves = bool(np.any(goal != goal[0]))
    output_moves = bool(np.any(actual != actual[0]))
    return {
        "Q2": _check(min(ious) >= 0.98, iou=ious, min_iou=min(ious), support=support),
        "Q3": _check(float(errors.max()) <= 0.5, max_error_px=float(errors.max())),
        "Q4": _check(not mismatched, adjacent_mismatches=mismatched, identical_position_ties=tied),
        "T4": _check(
            not ref_moves or output_moves, reference_moves=ref_moves, output_moves=output_moves
        ),
    }


def _edge_mean(clip: RGB, silhouette: Mask) -> float:
    edges = []
    for frame, mask in zip(clip, silhouette, strict=True):
        y, x = np.nonzero(mask)
        ys = np.arange(y.min(), y.max() + 1)
        xs = np.arange(x.min(), x.max() + 1)
        center = frame[np.ix_(ys, xs)] @ LUMA
        right = frame[np.ix_(ys, np.minimum(xs + 1, frame.shape[1] - 1))] @ LUMA
        down = frame[np.ix_(np.minimum(ys + 1, frame.shape[0] - 1), xs)] @ LUMA
        edges.append(np.hypot(right - center, down - center).mean())
    return float(np.mean(edges))


def _interior_mae(clip: RGB, reference: RGB, interior: Mask) -> float:
    return float(
        np.mean(
            [
                np.abs(_residual(frame, clean, mask)).mean()
                for frame, clean, mask in zip(clip, reference, interior, strict=True)
            ]
        )
    )


def _background_time(clip: RGB, mask: Mask) -> tuple[float, float]:
    means = []
    peak = 0.0
    previous = None
    for frame in clip:
        samples = frame[mask].astype(np.int16)
        means.append(float(samples.mean(axis=0) @ LUMA))
        if previous is not None:
            peak = max(peak, float(np.abs(samples - previous).mean()))
        previous = samples
    return float(np.std(means)), peak


def _static_support(
    reference: RGB,
    background: Mask | None,
    silhouette: Mask | None,
    ghost: Mask | None,
    photometric: Mask | None,
) -> dict:
    if any(region is None for region in (background, silhouette, ghost, photometric)):
        return _blocked("requires background, reference silhouette, ghost and photometric masks")
    if not background.any():
        return _check(False, reason="empty static region")
    padded = np.pad(background, 1)
    nearby = np.zeros_like(background)
    height, width = background.shape
    for y in range(3):
        for x in range(3):
            nearby |= padded[y : y + height, x : x + width]
    clean = reference[0][background]
    for i, frame in enumerate(reference):
        if (
            np.any((silhouette[i] | ghost[i]) & nearby)
            or not np.all(photometric[i][background])
            or not np.array_equal(frame[background], clean)
        ):
            return _check(False, first_invalid_frame=i)
    return _check(True)


def _endpoint_check(
    repair: Mask | None,
    donor_counts: NDArray[np.integer] | None,
    unresolved_pixels: Sequence[int] | None,
    shape: tuple[int, int, int],
) -> dict:
    if unresolved_pixels is None:
        return _blocked("requires unresolved pixel counts for each frame")
    if len(unresolved_pixels) != shape[0] or any(
        not isinstance(count, (int, np.integer)) or count < 0 for count in unresolved_pixels
    ):
        raise ValueError("Invalid unresolved pixel counts")
    unresolved = sum(map(int, unresolved_pixels))
    if unresolved:
        return _check(False, unresolved_pixels=unresolved)
    if repair is None:
        return _blocked("requires a declared local repair mask")
    endpoints = repair[[0, -1]]
    if not endpoints.any():
        return _check(True, unresolved_pixels=0)
    if donor_counts is None:
        return _blocked("requires validated donor counts for repaired endpoint pixels")
    if (
        donor_counts.shape != endpoints.shape
        or not np.issubdtype(donor_counts.dtype, np.integer)
        or np.any(donor_counts < 0)
    ):
        raise ValueError("Invalid endpoint donor counts")
    insufficient = int(np.count_nonzero(endpoints & (donor_counts < 2)))
    return _check(insufficient == 0, insufficient_endpoint_pixels=insufficient, unresolved_pixels=0)


def guardrail_checks(
    before: RGB,
    after: RGB,
    reference: RGB,
    *,
    roi: Mask,
    protection: Mask | None = None,
    repair: Mask | None = None,
    photometric: Mask | None = None,
    local_changed: Mask | None = None,
    scoring_roi: Mask | None = None,
    background: Mask | None = None,
    ghost: Mask | None = None,
    interior: Mask | None = None,
    silhouette: Mask | None = None,
    segment: Callable[[RGB], Mask] | None = None,
    appearance_only: bool = False,
    max_changed_fraction: float = 0.25,
    unresolved_pixels: Sequence[int] | None = None,
    endpoint_donor_counts: NDArray[np.integer] | None = None,
    pts: Sequence[Fraction] | None = None,
    fps: Fraction | int | None = None,
) -> dict:
    """Check Q/T rails. With no explicit protection mask, ROI is the zero-feather domain."""
    shape = _shape(before, after, reference)
    roi = _frame_masks(roi, shape, "ROI")
    if roi is None or not 0 <= max_changed_fraction <= 1:
        raise ValueError("Invalid ROI or changed fraction")
    protection = roi if protection is None else _frame_masks(protection, shape, "protection")
    repair = _frame_masks(repair, shape, "repair")
    photometric = _frame_masks(photometric, shape, "photometric")
    local_changed = _frame_masks(local_changed, shape, "local replacement")
    ghost = _frame_masks(ghost, shape, "ghost")
    silhouette = _mask(silhouette, shape, "silhouette")
    scoring_roi = _mask(scoring_roi, shape[1:], "E")
    background = _mask(background, shape[1:], "B")
    interior = _mask(interior, shape, "I")

    result = _authorization(
        before, after, roi, protection, repair, photometric, local_changed, max_changed_fraction
    )
    result.update(_contours(after, silhouette, segment))
    result["Q5"] = _blocked("requires reference contour bounding box")
    if silhouette is not None:
        if not np.all(np.any(silhouette, axis=(1, 2))):
            result["Q5"] = _check(False, reason="empty reference contour")
        else:
            values = [_edge_mean(clip, silhouette) for clip in (before, after, reference)]
            ratio = values[1] / values[0] if values[0] else None
            result["Q5"] = _check(
                ratio is not None and ratio >= 0.90, ratio=ratio, input_output_reference_dn=values
            )
    result["Q6"] = _blocked("requires reference interior mask")
    if interior is not None:
        if not np.all(np.any(interior, axis=(1, 2))):
            result["Q6"] = _check(False, reason="empty reference interior")
        else:
            original, output = [
                _interior_mae(clip, reference, interior) for clip in (before, after)
            ]
            threshold = 0.90 if appearance_only else 1.05
            result["Q6"] = _check(
                output <= threshold * original,
                input_dn=original,
                output_dn=output,
                max_fraction=threshold,
            )

    result["T1"] = result["T2"] = _blocked("requires static background region and adjacent frames")
    if background is not None and background.any() and shape[0] > 1:
        original, original_peak = _background_time(before, background)
        output, output_peak = _background_time(after, background)
        result["T1"] = _check(output <= original + 0.2, input_dn=original, output_dn=output)
        result["T2"] = _check(
            output_peak <= original_peak + 1.0, input_dn=original_peak, output_dn=output_peak
        )
    result["T3"] = _blocked("requires exact presentation PTS and fps")
    if pts is not None and fps is not None and fps > 0:
        expected = [Fraction(i) / fps for i in range(shape[0])]
        result["T3"] = _check(list(pts) == expected, frame_count=len(pts))
    result["T5"] = _blocked("requires fixed scoring region and adjacent frames")
    if scoring_roi is not None and scoring_roi.any() and shape[0] > 1:
        mismatches = []
        for i, frame in enumerate(after):
            exact = np.abs(_residual(frame, reference[i], scoring_roi)).mean()
            for j in (i - 1, i + 1):
                if (
                    0 <= j < shape[0]
                    and np.abs(_residual(frame, reference[j], scoring_roi)).mean() < exact
                ):
                    mismatches.append((i, j))
        result["T5"] = _check(not mismatches, adjacent_mismatches=mismatches)
    result["T6"] = _static_support(reference, background, silhouette, ghost, photometric)
    result["T7"] = _endpoint_check(repair, endpoint_donor_counts, unresolved_pixels, shape)
    return result


class DraftOracleTest(unittest.TestCase):
    def test_exact_improvement_thresholds_are_inclusive(self):
        before = {key: {"value_dn": 10.0} for key in MIN_INPUT_DN}
        after = {key: {"value_dn": 8.0} for key in MIN_INPUT_DN}
        checks = metric_checks(before, after, tuple(MIN_INPUT_DN), combined=True)
        self.assertTrue(all(row["status"] == "PASS" for row in checks.values()))
        after["M1"]["value_dn"] = 7.0
        self.assertEqual(
            metric_checks(before, after, ("M1",), combined=False)["M1"]["status"], "PASS"
        )

    def test_metrics_and_undefined_gates(self) -> None:
        reference = np.full((4, 1, 4, 3), 128, dtype=np.uint8)
        before = reference.copy()
        before[:, 0, 0] = 140
        before[::2, 0, 1] = 140
        before[1::2, 0, 1] = 116
        before[:, 0, 2] = (118, 126, 130)
        before[::2, 0, 3] = (124, 126, 128)
        before[1::2, 0, 3] = (132, 130, 128)
        scoring_roi, background, ghost, interior = np.eye(4, dtype=bool).reshape(4, 1, 4)
        kwargs = {
            "scoring_roi": scoring_roi,
            "background": background,
            "ghost": ghost,
            "interior": np.broadcast_to(interior, reference.shape[:-1]),
        }
        original = measure(before, reference, **kwargs)
        clean = measure(reference, reference, **kwargs)
        self.assertEqual([original[k]["value_dn"] for k in MIN_INPUT_DN], [12, 24, 11 / 3, 2])
        self.assertEqual(original["M2"]["steps_dn"], [24, 24, 24])
        self.assertEqual(original["M3"]["pixels_exceeding_8_dn_after_floor"], 4)
        self.assertTrue(
            all(
                row["status"] == "PASS"
                for row in metric_checks(
                    original, clean, tuple(MIN_INPUT_DN), combined=True
                ).values()
            )
        )
        self.assertEqual(
            metric_checks(clean, clean, ["M1"], combined=False)["M1"]["reason"], "METRIC_UNDEFINED"
        )

    def test_motion_identity_and_shift(self) -> None:
        reference = np.zeros((3, 7, 8, 3), dtype=np.uint8)
        for i, frame in enumerate(reference):
            frame[2:4, i + 2 : i + 4] = 180
        silhouettes = reference[..., 0] > 100
        roi = np.zeros((7, 8), dtype=bool)
        roi[1:5, 1:7] = True
        params = {
            "roi": roi,
            "silhouette": silhouettes,
            "interior": silhouettes,
            "scoring_roi": roi,
            "segment": lambda frame: frame[..., 0] > 100,
            "pts": [Fraction(i, 12) for i in range(3)],
            "fps": 12,
        }
        checks = guardrail_checks(reference, reference, reference, **params)
        for key in ("Q1/Q9", "Q2", "Q3", "Q4", "Q5", "Q6", "Q10", "T3", "T4", "T5"):
            self.assertEqual(checks[key]["status"], "PASS", key)
        shifted = reference[[1, 2, 2]].copy()
        shifted[0, 0, 0] = 255
        checks = guardrail_checks(reference, shifted, reference, **params)
        for key in ("Q1/Q9", "Q2", "Q3", "Q4", "T5"):
            self.assertEqual(checks[key]["status"], "FAIL", key)

    def test_stage_attribution_and_insufficient_endpoint_donors_fail(self) -> None:
        source = np.zeros((2, 2, 4, 3), dtype=np.uint8)
        after = source.copy()
        after[:, 0, 0] = 2
        roi = np.ones((2, 4), dtype=bool)
        repair = np.array([[True, False, False, False], [False, False, False, False]])
        local = np.broadcast_to(repair, source.shape[:-1])
        params = {
            "roi": roi,
            "repair": repair,
            "photometric": np.zeros_like(roi),
            "unresolved_pixels": [0, 0],
            "endpoint_donor_counts": np.ones((2, 2, 4), dtype=int),
        }
        missing = guardrail_checks(source, after, source, **params)
        self.assertEqual(missing["Q7/Q8"]["status"], "BLOCKED")
        self.assertEqual(missing["T7"]["insufficient_endpoint_pixels"], 2)
        checks = guardrail_checks(source, after, source, local_changed=local, **params)
        self.assertEqual(checks["Q7/Q8"]["status"], "PASS")
        self.assertEqual(checks["T7"]["status"], "FAIL")
