"""Causal background replacement from explicitly permitted past observations."""

from fractions import Fraction

import numpy as np

from .formal_bundle import rectangle


def _boundary_crossing(frame, anchor, roi, offset):
    """Decline an interior correction when the same shift continues across its boundary."""
    if not offset.any():
        return False
    outside_ring = np.zeros_like(roi)
    outside_ring[1:] |= roi[:-1]
    outside_ring[:-1] |= roi[1:]
    outside_ring[:, 1:] |= roi[:, :-1]
    outside_ring[:, :-1] |= roi[:, 1:]
    outside_ring &= ~roi
    if not outside_ring.any():
        return False
    delta = frame[outside_ring].astype(np.int16) - anchor[outside_ring]
    observed = np.median(delta, axis=0)
    return bool(
        np.linalg.norm(observed - offset) < np.linalg.norm(observed)
        and np.mean(np.max(abs(delta - offset), axis=1) <= 2) >= 0.9
    )


def _nearby_echo(current, previous, donors, y, x, radius):
    """Test for a mixture with an actually observed prior RGB within the allowed disk."""
    residual = current[y, x].astype(np.float64) - donors
    supported = np.max(abs(residual), axis=1) <= 2
    height, width = current.shape[:2]
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius:
                continue
            sy, sx = y + dy, x + dx
            valid = (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width) & ~supported
            if not valid.any():
                continue
            delta = previous[sy[valid], sx[valid]].astype(np.float64) - donors[valid]
            amplitude = np.sum(residual[valid] * delta, axis=1) / np.maximum(
                1, np.sum(delta**2, axis=1)
            )
            difference = residual[valid] - amplitude[:, None] * delta
            supported[valid] = (
                (amplitude > 0) & (amplitude < 1) & (np.max(abs(difference), axis=1) <= 2)
            )
    return supported


def restore(frames, masks, mask_manifest, config):
    t, h, w, _ = frames.shape
    area = rectangle(config["photometric"]["roi"], w, h)
    roi = rectangle(mask_manifest["repair_roi"], w, h)
    stable = masks["static_sample"]
    write_mask = masks["repair"]
    ghost = masks.get("ghost", np.zeros_like(write_mask))
    defect = masks.get("defect", write_mask & ~ghost)
    allow = masks["donor_valid"] & ~write_mask
    photo = config["photometric"]
    repair = config["repair"]
    fraction = Fraction(repair["max_changed_fraction"])
    result = frames.copy()
    normalized = frames.copy()
    usable = allow.copy()
    reports = []

    for i, raw in enumerate(frames):
        unresolved = np.zeros((h, w), bool)
        reasons = {}
        sample = stable[0] & stable[i]
        offset = np.zeros(3, dtype=np.int16)
        photometric_ok = True
        if photo["enabled"]:
            if sample.sum() < 2:
                photometric_ok = False
                reasons["insufficient_static_samples"] = int(area.sum())
            else:
                delta = raw[sample].astype(np.int16) - frames[0, sample]
                offset = np.rint(np.median(delta, axis=0)).astype(np.int16)
                luma = int(np.rint(np.median(offset)))
                if (
                    abs(luma) > photo["max_abs_luma_offset"]
                    or np.max(abs(offset - luma)) > photo["max_abs_channel_offset"]
                ):
                    photometric_ok = False
                    reasons["photometric_limit"] = int(area.sum())
                elif np.mean(np.max(abs(delta - offset), axis=1) <= 2) < 0.95:
                    photometric_ok = False
                    reasons["inconsistent_static_samples"] = int(area.sum())
                elif _boundary_crossing(raw, frames[0], area, offset):
                    photometric_ok = False
                    reasons["correction_would_create_boundary_seam"] = int(area.sum())
        if photometric_ok and offset.any():
            values = raw[area].astype(np.int16) - offset
            bounded = np.all(
                (values >= 0) & (values <= 255) & (raw[area] > 0) & (raw[area] < 255), axis=1
            )
            indices = np.nonzero(area)
            rejected = tuple(axis[~bounded] for axis in indices)
            unresolved[rejected] = True
            usable[i][rejected] = False
            normalized[i][tuple(axis[bounded] for axis in indices)] = values[bounded]
            if not bounded.all():
                reasons["clipped_observations"] = int((~bounded).sum())
        elif not photometric_ok:
            unresolved |= area
            usable[i, area] = False
        result[i] = normalized[i]
        selected = write_mask[i] & ~unresolved
        start = max(0, i - repair["window_radius"])
        past = list(range(start, i))
        counts = np.zeros((h, w), dtype=np.uint8)
        consistent = np.zeros((h, w), dtype=bool)
        if past and selected.any():
            # Only gather requested coordinates. Every candidate is original degraded
            # input (optionally normalized), never a previous reconstructed pixel.
            observations = normalized[start:i, selected].astype(np.int16)
            valid = usable[start:i, selected]
            support = valid.sum(axis=0)
            counts[selected] = support
            high = np.max(np.where(valid[..., None], observations, -1), axis=0)
            low = np.min(np.where(valid[..., None], observations, 256), axis=0)
            agrees = np.max(high - low, axis=1) <= 2
            minimum = np.where(
                ghost[i, selected],
                max(repair["minimum_observations"], config["ghost"]["min_clean_observations"]),
                repair["minimum_observations"],
            )
            eligible = (support >= minimum) & agrees
            if not config["ghost"]["enabled"]:
                eligible &= ~ghost[i, selected]
            consistent[selected] = eligible
            if eligible.any():
                samples = observations[:, eligible]
                ok = valid[:, eligible]
                masked = np.ma.array(samples, mask=np.broadcast_to(~ok[..., None], samples.shape))
                middle = np.ma.median(masked, axis=0).filled(0)
                distance = np.max(abs(samples - middle), axis=2)
                nearest = np.argmin(np.where(ok, distance, 512), axis=0)
                donors = samples[nearest, np.arange(len(nearest))]
                ys, xs = np.nonzero(consistent)
                is_ghost = ghost[i, ys, xs]
                keep = np.ones(len(ys), dtype=bool)
                keep[is_ghost] = _nearby_echo(
                    normalized[i],
                    normalized[i - 1],
                    donors[is_ghost],
                    ys[is_ghost],
                    xs[is_ghost],
                    config["ghost"]["max_displacement_px"],
                )
                if not keep.all():
                    reasons["no_observed_echo_within_displacement_bound"] = int((~keep).sum())
                consistent[ys, xs] = keep
                result[i, ys[keep], xs[keep]] = donors[keep]
            insufficient = selected & (
                counts
                < np.where(
                    ghost[i],
                    max(repair["minimum_observations"], config["ghost"]["min_clean_observations"]),
                    repair["minimum_observations"],
                )
            )
            if insufficient.any():
                reason = (
                    "short_past_window"
                    if len(past) < repair["minimum_observations"]
                    else "excluded_or_unusable_past_donors"
                )
                reasons[reason] = int(insufficient.sum())
            if np.any(~agrees & (support >= minimum)):
                reasons["past_rgb_disagreement"] = int(np.sum(~agrees & (support >= minimum)))
        elif selected.any():
            reasons["no_past_frame"] = int(selected.sum())
        unresolved |= selected & ~consistent
        changed = np.any(result[i] != raw, axis=2)
        over_limit = (
            int((changed & roi).sum()) * fraction.denominator > int(roi.sum()) * fraction.numerator
        )
        if over_limit:
            reasons["changed_fraction_limit"] = int(changed.sum())
            unresolved |= changed
        # Unresolved means original observed RGB, including when global photometry
        # would otherwise have been applicable at the same coordinate.
        result[i, unresolved] = raw[unresolved]
        changed = np.any(result[i] != raw, axis=2)
        current = int(changed.sum())
        refused = int(unresolved.sum())
        levels, frequencies = np.unique(counts[selected], return_counts=True)
        reports.append(
            {
                "status": "UNRESOLVED" if refused else "REPAIRED" if current else "UNCHANGED",
                "changed_pixels": current,
                "unresolved_pixels": refused,
                "evidence": {
                    "past_window": past,
                    "current_index": i,
                    "static_sample_pixels": int(sample.sum()),
                    "rgb_offset_from_first_observation_dn": offset.tolist(),
                    "photometric_changed_pixels": int((changed & ~write_mask[i]).sum()),
                    "changed_inside_repair": int((changed & write_mask[i]).sum()),
                    "changed_outside_authorization": int((changed & ~(write_mask[i] | area)).sum()),
                    "maximum_donor_count": int(counts.max()),
                    "minimum_observations": repair["minimum_observations"],
                    "ghost_requested": int(ghost[i].sum()),
                    "ghost_resolved": int((ghost[i] & ~unresolved).sum()),
                    "ghost_changed": int((ghost[i] & changed).sum()),
                    "ghost_unresolved": int((ghost[i] & unresolved).sum()),
                    "defect_unresolved": int((defect[i] & unresolved).sum()),
                    "donor_support_histogram": {
                        str(int(k)): int(v) for k, v in zip(levels, frequencies, strict=True)
                    },
                    "reasons": reasons,
                },
            }
        )
    return result, reports
