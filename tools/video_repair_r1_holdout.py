"""Construct and seal independent R1-002 holdout inputs without running repair."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

VERSION = {"contract_version": "c-video-repair-contract@0.1", "draft_revision": "R1-002"}
GENERATOR = "cvr-independent-synthetic-holdout@1"
PROFILE = "cvr-holdout-a@1"
SEEDS = {"H1": 419281, "H2": 913763, "H3": 306511, "H4": 537233, "H5": 786433}
METRICS = {"H1": ["M1"], "H2": ["M2"], "H3": ["M3"], "H4": ["M4"], "H5": ["M1", "M2", "M3", "M4"]}
MASK_PURPOSE = {
    "repair": "local background write authorization",
    "ghost": "suspected displaced background contamination",
    "defect": "suspected local background defect",
    "donor_valid": "permitted same-coordinate background observations",
    "static_sample": "static observations for photometric estimation",
}
SEGMENTATION = {
    "kind": "rgb-thresholds@1",
    "red_minimum": 180,
    "red_minus_green_minimum": 70,
}
FRAMES = 24
SIDE = 256


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical_digest(data: object) -> str:
    return _digest(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _json(path: Path, data: dict) -> dict:
    encoded = (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    path.write_bytes(encoded)
    return {"file": path.name, "file_sha256": _digest(encoded)}


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(pixels: np.ndarray, mask: bool) -> bytes:
    # Fixed filter-zero scanlines and stored DEFLATE blocks keep the file digest
    # independent of both Pillow and the platform's zlib compression choices.
    scanlines = b"".join(b"\x00" + row.tobytes() for row in pixels)
    stream = bytearray(b"\x78\x01")
    for start in range(0, len(scanlines), 65_535):
        block = scanlines[start : start + 65_535]
        final = start + len(block) == len(scanlines)
        stream.extend(struct.pack("<BHH", final, len(block), 65_535 - len(block)))
        stream.extend(block)
    stream.extend(struct.pack(">I", zlib.adler32(scanlines)))
    header = struct.pack(">IIBBBBB", SIDE, SIDE, 8, 0 if mask else 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", bytes(stream))
        + _chunk(b"IEND", b"")
    )


def _sequence(root: Path, role: str, images: np.ndarray, *, mask: bool = False) -> dict:
    constant = mask and images.ndim == 2
    directory = root / role
    directory.mkdir()
    files = []
    for index, image in enumerate(images[None] if constant else images):
        pixels = image.astype(np.uint8) * 255 if mask else image
        png = _png(pixels, mask)
        name = f"frame-{index:04}.png"
        (directory / name).write_bytes(png)
        record = {
            "index": index,
            "file": f"{role}/{name}",
            "file_sha256": _digest(png),
            "pixel_sha256": _digest(pixels.tobytes()),
        }
        if mask:
            record["support_pixels"] = int(np.count_nonzero(image))
        files.append(record)
    return {"kind": "constant" if constant else "per-frame", "files": files}


def _box(bounds: list[int]) -> np.ndarray:
    x, y, right, bottom = bounds
    mask = np.zeros((SIDE, SIDE), dtype=bool)
    mask[y:bottom, x:right] = True
    return mask


def _tree_sha(root: Path) -> str:
    return _canonical_digest(
        {
            file.relative_to(root).as_posix(): _digest(file.read_bytes())
            for file in root.rglob("*")
            if file.is_file()
        }
    )


def _case(root: Path, clip: str, seed: int) -> dict:
    metrics = METRICS[clip]
    parameters = hashlib.sha256(f"{GENERATOR}:{seed}".encode()).digest()
    x_start = 76 + parameters[0] % 13
    cy = 133 + parameters[1] % 7
    rx = 24 + parameters[2] % 5
    ry = 13 + parameters[3] % 4
    trajectory = [x_start + i for i in range(FRAMES)]
    repair_roi = [12, 32, 244, 222]
    photo_roi = [x_start - rx - 8, cy - ry - 24, trajectory[-1] + rx + 8, cy + ry + 1]
    edge = cy + ry + 3
    areas = {
        "E": [31, cy + 34, 42, cy + 43],
        "B": [x_start - 7, cy - ry - 18, x_start + 3, cy - ry - 11],
        "G": [x_start - rx, edge, trajectory[-1] + rx + 1, edge + 1],
        "static_sample": [x_start - 18, cy - ry - 20, x_start - 8, cy - ry - 14],
    }
    area = {role: _box(bounds) for role, bounds in areas.items()}
    photo = _box(photo_roi)
    y, x = np.indices((SIDE, SIDE))
    shade = (x // 17 + y // 21 + parameters[4] % 9) % 13
    background = np.stack((61 + shade, 82 + shade, 107 + shade), axis=-1)
    target = [213 + parameters[5] % 13, 77 + parameters[6] % 9, 52 + parameters[7] % 10]
    clean = np.empty((FRAMES, SIDE, SIDE, 3), dtype=np.uint8)
    contour = np.empty((FRAMES, SIDE, SIDE), dtype=bool)
    interior = np.empty_like(contour)
    for i, cx in enumerate(trajectory):
        contour[i] = ((x - cx) * ry) ** 2 + ((y - cy) * rx) ** 2 <= (rx * ry) ** 2
        ix, iy = rx - 2, ry - 2
        interior[i] = ((x - cx) * iy) ** 2 + ((y - cy) * ix) ** 2 <= (ix * iy) ** 2
        clean[i] = np.where(contour[i, ..., None], target, background)

    degraded = clean.copy()
    defect = np.zeros_like(contour)
    ghost = np.zeros_like(contour)
    ghost_truth = np.zeros_like(contour)
    offsets = []
    phase_offset = parameters[8] % 3
    defect_frames = range(5 + phase_offset, FRAMES - 1, 4)
    ghost_frames = range(4 + phase_offset, FRAMES - 1, 2)
    for i in range(FRAMES):
        if "M1" in metrics and i in defect_frames:
            defect[i] = area["E"]
            degraded[i, defect[i]] = [113, 43, 131]
        if "M3" in metrics and i in ghost_frames:
            ghost[i] = area["G"]
            ghost_truth[i, 4:] = area["G"][4:] & contour[i - 1, :-4]
            ys, xs = np.nonzero(ghost_truth[i])
            current = clean[i, ys, xs].astype(np.int16)
            echo = clean[i - 1, ys - 4, xs].astype(np.int16)
            degraded[i, ys, xs] = (3 * current + 2 * echo + 2) // 5
        common = (0, 7, -7, 6, -6, 4, -4, 0)[i % 8] if "M2" in metrics else 0
        phase = i % 18
        drift = min(phase, 18 - phase) if "M4" in metrics else 0
        offset = [common + drift, common, common - drift]
        offsets.append(offset)
        degraded[i, photo] = degraded[i, photo].astype(np.int16) + offset
    if not (defect | ghost).any():
        # A clean suspicion window exercises donor validation without injecting a defect.
        defect[5] = area["E"]
    repair = defect | ghost
    source = root / "source"
    masks = root / "masks"
    reference = root / "reference"
    for directory in (source, masks, reference):
        directory.mkdir(parents=True)
    frames = _sequence(source, "frames", degraded)["files"]
    video = {
        **VERSION,
        "profile_id": PROFILE,
        "width": SIDE,
        "height": SIDE,
        "pixel_format": "RGB8",
        "color_space": "sRGB",
        "frame_count": FRAMES,
        "fps": {"numerator": 12, "denominator": 1},
        "timebase_ticks_per_second": 12,
        "frame_ticks": list(range(FRAMES)),
        "duration_seconds": "2/1",
        "endpoint_rule": "left-closed-right-open",
    }
    bundle_digest = _canonical_digest(
        {
            **video,
            "frames": [{k: v for k, v in frame.items() if k != "file"} for frame in frames],
        }
    )
    _json(
        source / "video-manifest.json",
        {
            **video,
            "clip_id": clip,
            "scenario_kind": "synthetic-controlled",
            "bundle_digest": bundle_digest,
            "clean_reference_in_input": False,
            "frames": frames,
        },
    )
    roles = {
        "repair": repair,
        "defect": defect,
        "ghost": ghost,
        "donor_valid": (area["E"] | area["G"]) & ~repair,
        "static_sample": area["static_sample"],
    }
    mask_manifest = _json(
        masks / "mask-manifest.json",
        {
            **VERSION,
            "width": SIDE,
            "height": SIDE,
            "repair_roi": repair_roi,
            "feather_px": 0,
            "mask_semantics": "255=role-active,0=role-inactive",
            "roles": {
                role: {
                    **_sequence(masks, role, values, mask=True),
                    "purpose": MASK_PURPOSE[role],
                    "origin": "controlled-test-stimulus",
                }
                for role, values in roles.items()
            },
        },
    )
    config = _json(
        root / "config.json",
        {
            **VERSION,
            "clip_id": clip,
            "repair": {
                "mode": "temporal-median",
                "window_radius": 12,
                "minimum_observations": 2,
                "max_changed_fraction": "1/4",
                "spatial_fallback": None,
            },
            "photometric": {
                "enabled": True,
                "roi": photo_roi,
                "max_abs_luma_offset": 32,
                "max_abs_channel_offset": 16,
            },
            "ghost": {"enabled": True, "min_clean_observations": 2, "max_displacement_px": 4},
            "determinism": {"rounding": "round-half-to-even", "random_seed": None},
        },
    )
    stimulus = {
        "id": GENERATOR,
        "seed": seed,
        "target_centers_x": trajectory,
        "target_center_y": cy,
        "target_radii_xy": [rx, ry],
        "areas": areas,
        "photometric_offsets_rgb": offsets,
        "echo_displacement_xy": [0, 4],
        "echo_blend": "2/5",
        "output_segmentation": SEGMENTATION,
    }
    basis = _json(reference / "basis-stimulus.json", stimulus)
    source_evidence = _json(
        reference / "source-evidence.json",
        {
            "kind": "synthetic-controlled",
            "generator": GENERATOR,
            "seed": seed,
            "basis_stimulus": basis,
        },
    )
    score_masks = {
        role: _sequence(reference, role, values, mask=True)
        for role, values in {
            "E": area["E"],
            "B": area["B"],
            "G": area["G"],
            "I": interior,
            "contour": contour,
            "ghost_truth": ghost_truth,
        }.items()
    }
    offline = _json(
        reference / "reference-manifest.json",
        {
            **VERSION,
            "reference_format": "cvr-holdout-reference@1",
            "qualification": "SEALED_HOLDOUT",
            "clip_id": clip,
            "profile_id": PROFILE,
            "scenario_kind": "synthetic-controlled",
            "frame_count": FRAMES,
            "width": SIDE,
            "height": SIDE,
            "frame_ticks": list(range(FRAMES)),
            "bundle_digest": bundle_digest,
            "mask_manifest_sha256": mask_manifest["file_sha256"],
            "config_sha256": config["file_sha256"],
            "required_metrics": metrics,
            "source_evidence": source_evidence,
            "stimulus": stimulus,
            "segmentation": SEGMENTATION,
            "regions": score_masks,
            "clean": _sequence(reference, "clean", clean),
        },
    )
    return {
        "seed": seed,
        "required_metrics": metrics,
        "input_bundle_digest": bundle_digest,
        "input_sha256": _tree_sha(source),
        "authorization_sha256": _tree_sha(masks),
        "config_sha256": config["file_sha256"],
        "reference_sha256": _tree_sha(reference),
        "reference_manifest_sha256": offline["file_sha256"],
        "scoring_masks_sha256": _canonical_digest(score_masks),
    }


def build(output: Path, expected_seal: Path | None = None) -> None:
    if output.exists():
        raise ValueError("holdout output already exists")
    output.parent.mkdir(exist_ok=True, parents=True)
    with TemporaryDirectory(dir=output.parent, prefix=".cvr-holdout-") as temporary:
        stage = Path(temporary) / "stage"
        seal = {
            **VERSION,
            "schema_version": "cvr-r1-002-holdout-seal@1",
            "generator": GENERATOR,
            "generator_sha256": _digest(Path(__file__).read_bytes().replace(b"\r\n", b"\n")),
            "cases": {clip: _case(stage / clip, clip, seed) for clip, seed in SEEDS.items()},
        }
        if expected_seal is not None and seal != json.loads(expected_seal.read_bytes()):
            raise ValueError("generated holdout bytes do not match the pinned seal")
        _json(stage / "seal.json", seal)
        stage.rename(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seal", type=Path, help="require exact agreement with this recorded seal")
    args = parser.parse_args()
    try:
        build(args.output, args.seal)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Unable to seal holdouts: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
