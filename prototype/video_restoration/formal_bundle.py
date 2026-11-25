"""Canonical external frame packages and the closed repair input vocabulary."""

from __future__ import annotations

import json
import re
import stat
from fractions import Fraction
from pathlib import Path

import numpy as np

from .sequence import digest, load_sequence, save_sequence, write_json

CONTRACT = "c-video-repair-contract@0.1"
REVISION = "R1-002"
VERSION = {"contract_version": CONTRACT, "draft_revision": REVISION}
ALGORITHM = "cvr-causal-stationary-background@1"
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
MASK_PURPOSE = {
    "repair": "local background write authorization",
    "ghost": "suspected displaced background contamination",
    "defect": "suspected local background defect",
    "donor_valid": "permitted same-coordinate background observations",
    "static_sample": "static observations for photometric estimation",
}


class StageError(ValueError):
    def __init__(self, number: int, message: str):
        super().__init__(message)
        self.code = f"CVR-E{number:03d}"
        self.exit_code = next(
            code
            for limit, code in (
                (12, 2),
                (14, 3),
                (20, 4),
                (24, 5),
                (26, 6),
                (29, 7),
                (30, 8),
                (31, 7),
            )
            if number <= limit
        )


def require(condition: object, number: int, message: str) -> None:
    if not condition:
        raise StageError(number, message)


def hashed(value: object) -> str:
    data = json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return digest(data)


def regular(path: Path) -> None:
    for entry in (path, *path.parents):
        info = entry.lstat()
        require(
            not entry.is_symlink()
            and not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT),
            14,
            "linked inputs are forbidden",
        )


def fields(value: object, allowed: set[str], code: int) -> None:
    require(isinstance(value, dict) and set(value) == allowed, code, "unexpected fields")


def version(data: dict) -> None:
    require(all(data.get(k) == v for k, v in VERSION.items()), 1, "unsupported contract")


def json_file(path: Path) -> dict:
    regular(path)
    try:
        value = json.loads(path.read_bytes())
    except (UnicodeError, ValueError) as exc:
        raise StageError(1, "invalid JSON object") from exc
    require(isinstance(value, dict), 1, "expected JSON object")
    return value


def names(count: int) -> tuple[str, ...]:
    return tuple(f"frame-{i:04}.png" for i in range(count))


def sequence(root: Path, count: int, width: int, height: int, *, mask: bool = False):
    regular(root)
    ordered = names(count)
    require(
        sorted(p.name for p in root.iterdir()) == list(ordered), 5, "unexpected sequence membership"
    )
    for name in ordered:
        regular(root / name)
    try:
        pixels, records = load_sequence(root, names=ordered, width=width, height=height, mask=mask)
    except (OSError, ValueError, SyntaxError) as exc:
        raise StageError(8 if mask else 6, "invalid mask or frame PNG") from exc
    return pixels, [
        record(i, root.name, item, pixels[i] if mask else None) for i, item in enumerate(records)
    ]


def record(index: int, folder: str, item: dict, mask: np.ndarray | None = None) -> dict:
    entry = {
        "index": index,
        "file": f"{folder}/{item['file']}",
        "file_sha256": item["sha256"],
        "pixel_sha256": item["pixel_sha256"],
    }
    if mask is not None:
        entry["support_pixels"] = int(mask.sum())
    return entry


def bundle_digest(data: dict) -> str:
    return hashed(
        {
            **{k: data[k] for k in BUNDLE_FIELDS},
            "frames": [
                {k: f[k] for k in ("index", "file_sha256", "pixel_sha256")} for f in data["frames"]
            ],
        }
    )


def make_video(
    root: Path, frames: np.ndarray, scenario_kind: str, clip_id: str, profile_id: str
) -> dict:
    t, h, w, _ = frames.shape
    root.mkdir(exist_ok=True, parents=True)
    saved = save_sequence(root / "frames", frames, names=names(t))
    duration = Fraction(t, 12)
    data = {
        **VERSION,
        "scenario_kind": scenario_kind,
        "clip_id": clip_id,
        "profile_id": profile_id,
        "width": w,
        "height": h,
        "frame_count": t,
        "fps": {"numerator": 12, "denominator": 1},
        "pixel_format": "RGB8",
        "color_space": "sRGB",
        "timebase_ticks_per_second": 12,
        "frame_ticks": list(range(t)),
        "duration_seconds": f"{duration.numerator}/{duration.denominator}",
        "endpoint_rule": "left-closed-right-open",
        "clean_reference_in_input": False,
        "frames": [record(i, "frames", item) for i, item in enumerate(saved)],
    }
    data["bundle_digest"] = bundle_digest(data)
    write_json(root / "video-manifest.json", data)
    return data


def read_video(root: Path):
    regular(root)
    entries = {p.name for p in root.iterdir()}
    require(
        {"video-manifest.json", "frames"}
        <= entries
        <= {"video-manifest.json", "frames", "ingest-record.json", "source"},
        14,
        "only declared degraded frames and metadata may enter repair",
    )
    v = json_file(root / "video-manifest.json")
    version(v)
    fields(
        v,
        {
            *BUNDLE_FIELDS,
            "scenario_kind",
            "clip_id",
            "bundle_digest",
            "clean_reference_in_input",
            "frames",
        },
        14,
    )
    require(v["clean_reference_in_input"] is False, 14, "reference data in input")
    require(
        all(
            isinstance(v[k], str) and re.fullmatch(r"[A-Za-z0-9_@.:-]{1,96}", v[k])
            for k in ("clip_id", "profile_id", "scenario_kind")
        ),
        1,
        "invalid profile identity",
    )
    t, w, h = (v[k] for k in ("frame_count", "width", "height"))
    require(type(t) is int and t in (24, 96), 2, "unsupported development frame count")
    require((w, h) == ((256, 256) if t == 24 else (640, 360)), 3, "unsupported dimensions")
    require(v["pixel_format"] == "RGB8" and v["color_space"] == "sRGB", 15, "unsupported color")
    require(
        v["fps"] == {"numerator": 12, "denominator": 1}
        and v["timebase_ticks_per_second"] == 12
        and v["frame_ticks"] == list(range(t))
        and all(type(i) is int for i in v["frame_ticks"])
        and v["duration_seconds"] == f"{t // 12}/1"
        and v["endpoint_rule"] == "left-closed-right-open",
        4,
        "unsupported CFR, duration or ticks",
    )
    pixels, records = sequence(root / "frames", t, w, h)
    require(
        v["frames"] == records and v["bundle_digest"] == bundle_digest(v),
        13,
        "degraded input digest mismatch",
    )
    return pixels, v


def make_masks(root: Path, roles: dict, repair_roi: list[int], feather_px: int = 0) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    descriptions = {}
    for role, mask in roles.items():
        h, w = mask.shape[-2:]
        constant = mask.ndim == 2
        arrays = mask[None] if constant else mask
        recs = save_sequence(root / role, arrays.astype(np.uint8) * 255, names=names(len(arrays)))
        descriptions[role] = {
            "kind": "constant" if constant else "per-frame",
            "purpose": MASK_PURPOSE[role],
            "origin": "controlled-test-stimulus",
            "files": [record(i, role, r, arrays[i]) for i, r in enumerate(recs)],
        }
    m = {
        **VERSION,
        "width": w,
        "height": h,
        "repair_roi": list(repair_roi),
        "feather_px": feather_px,
        "mask_semantics": "255=role-active,0=role-inactive",
        "roles": descriptions,
    }
    write_json(root / "mask-manifest.json", m)
    return m


def rectangle(bounds: list[int], w: int, h: int) -> np.ndarray:
    require(
        isinstance(bounds, list) and len(bounds) == 4 and all(type(k) is int for k in bounds),
        7,
        "invalid ROI",
    )
    x, y, r, b = bounds
    require(0 <= x < r <= w and 0 <= y < b <= h, 7, "out-of-bounds ROI")
    result = np.zeros((h, w), dtype=bool)
    result[y:b, x:r] = True
    return result


def read_masks(root: Path, video: dict):
    m = json_file(root / "mask-manifest.json")
    version(m)
    fields(
        m, {*VERSION, "width", "height", "repair_roi", "feather_px", "mask_semantics", "roles"}, 8
    )
    w, h, t = (video[k] for k in ("width", "height", "frame_count"))
    require(
        m["width"] == w
        and m["height"] == h
        and m["mask_semantics"] == "255=role-active,0=role-inactive",
        8,
        "mask specification mismatch",
    )
    roi = rectangle(m["repair_roi"], w, h)
    require(type(m["feather_px"]) is int and 0 <= m["feather_px"] <= 8, 11, "invalid feather")
    roles = m["roles"]
    require(
        isinstance(roles, dict)
        and {"repair", "donor_valid", "static_sample"} <= roles.keys() <= MASK_PURPOSE.keys(),
        8,
        "invalid mask roles",
    )
    require(
        {p.name for p in root.iterdir()} == {"mask-manifest.json", *roles},
        14,
        "unlisted mask artifacts",
    )
    result = {}
    for role, desc in roles.items():
        fields(desc, {"kind", "purpose", "origin", "files"}, 8)
        require(
            desc["origin"] in ("controlled-test-stimulus", "user-provided")
            and desc["purpose"] == MASK_PURPOSE[role],
            8,
            "unknown mask origin or purpose",
        )
        require(desc["kind"] in ("constant", "per-frame"), 8, "unknown mask kind")
        n = 1 if desc["kind"] == "constant" else t
        arr, rec = sequence(root / role, n, w, h, mask=True)
        require(desc["files"] == rec, 13, "mask digest mismatch")
        result[role] = np.broadcast_to(arr, (t, h, w))
    repair = result["repair"]
    require(
        repair.any()
        and not repair.all(axis=(1, 2)).any()
        and result["static_sample"].any(axis=(1, 2)).all(),
        9,
        "invalid mask support",
    )
    require(
        not (repair & ~roi).any() and not (repair & result["donor_valid"]).any(),
        10,
        "write authorization or donor exclusion violated",
    )
    ghost = result.get("ghost", False)
    defect = result.get("defect", False)
    require(
        not np.any(ghost & defect) and not np.any((ghost | defect) & ~repair),
        10,
        "overlapping or unauthorized roles",
    )
    return result, m


def default_config(clip_id: str, photometric_roi: list[int]) -> dict:
    return {
        **VERSION,
        "clip_id": clip_id,
        "repair": {
            "mode": "temporal-median",
            "window_radius": 12,
            "minimum_observations": 2,
            "max_changed_fraction": "1/4",
            "spatial_fallback": None,
        },
        "photometric": {
            "enabled": True,
            "roi": list(photometric_roi),
            "max_abs_luma_offset": 32,
            "max_abs_channel_offset": 16,
        },
        "ghost": {"enabled": True, "min_clean_observations": 2, "max_displacement_px": 4},
        "determinism": {"rounding": "round-half-to-even", "random_seed": None},
    }


def read_config(path: Path, video: dict, mask: dict) -> dict:
    c = json_file(path)
    version(c)
    defaults = default_config(video["clip_id"], mask["repair_roi"])
    fields(c, set(defaults), 18)
    require(
        c["clip_id"] == video["clip_id"] and c["determinism"] == defaults["determinism"],
        18,
        "configuration identity or rounding mismatch",
    )
    for section in ("repair", "photometric", "ghost"):
        fields(c[section], set(defaults[section]), 18)
    r, p, g = (c[k] for k in ("repair", "photometric", "ghost"))
    require(r["spatial_fallback"] is None, 19, "spatial fallback forbidden")
    require(
        r["mode"] == "temporal-median"
        and type(r["window_radius"]) is int
        and type(r["minimum_observations"]) is int
        and 2 <= r["minimum_observations"] <= r["window_radius"] <= 96,
        18,
        "unsupported temporal config",
    )
    try:
        fraction = Fraction(r["max_changed_fraction"])
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise StageError(18, "invalid changed fraction") from exc
    require(
        isinstance(r["max_changed_fraction"], str) and 0 < fraction <= 1, 18, "invalid change limit"
    )
    require(
        type(p["enabled"]) is bool
        and type(g["enabled"]) is bool
        and type(p["max_abs_luma_offset"]) is int
        and 0 <= p["max_abs_luma_offset"] <= 32
        and type(p["max_abs_channel_offset"]) is int
        and 0 <= p["max_abs_channel_offset"] <= 16
        and type(g["min_clean_observations"]) is int
        and 2 <= g["min_clean_observations"] <= r["window_radius"]
        and g["max_displacement_px"] == 4,
        18,
        "unsupported photometric/ghost config",
    )
    w, h = video["width"], video["height"]
    area = rectangle(p["roi"], w, h)
    x, y, bottom_x, bottom_y = mask["repair_roi"]
    f = mask["feather_px"]
    protected = rectangle(
        [max(0, x - f), max(0, y - f), min(w, bottom_x + f), min(h, bottom_y + f)], w, h
    )
    require(not (area & ~protected).any(), 10, "photometric ROI outside protection domain")
    return c
