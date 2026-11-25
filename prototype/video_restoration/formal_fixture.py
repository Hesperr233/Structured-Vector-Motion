"""Build diagnostic A/B inputs, using the preserved source generators outside repair."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np
from PIL import Image

from . import formal_bundle as bundle
from . import motion, motion_scene, synthetic
from .sequence import digest, write_json

STIMULUS = "cvr-controlled-development-stimulus@1"
# Authored before repair or scoring. All coordinates are half-open.
AREAS = {
    "A": {
        "repair": [16, 36, 240, 222],
        "photo": [46, 110, 183, 147],
        "sample": [47, 110, 60, 116],
        "B": [48, 118, 56, 123],
        "ghost_patch": [80, 147, 117, 154],
        "G": [85, 149, 105, 151],
        "donor_ghost": [76, 147, 123, 160],
        "ghost_frames": [4, 5, 7, 8, 10, 11],
    },
    "B": {
        "repair": [8, 6, 632, 354],
        "photo": [152, 68, 508, 210],
        "sample": [153, 69, 168, 77],
        "B": [490, 72, 502, 79],
        "ghost_patch": [274, 310, 302, 316],
        "G": [282, 311, 294, 313],
        "donor_ghost": [265, 310, 315, 322],
        "ghost_frames": [22, 23, 25, 26, 27, 29, 30, 32, 33],
    },
}
REQUIRED = {
    "S1": ["M1"],
    "S2": ["M2"],
    "S3": ["M3"],
    "S4": ["M4"],
    "S5": ["M1", "M2", "M3", "M4"],
    "B5": ["M1", "M2", "M3", "M4"],
    "N0": [],
}


def _read_stack(folder: Path) -> np.ndarray:
    images = []
    for path in sorted(folder.glob("*.png")):
        with Image.open(path) as image:
            images.append(np.asarray(image))
    return np.stack(images)


def _asset(root: Path, path: Path) -> dict:
    return {"file": path.relative_to(root).as_posix(), "file_sha256": digest(path.read_bytes())}


def _records(root: Path, role: str, images: np.ndarray, *, binary: bool) -> dict:
    constant = binary and images.ndim == 2
    directory = root / role
    directory.mkdir()
    records = []
    for index, image in enumerate(images[None] if constant else images):
        pixels = image.astype(np.uint8) * 255 if binary else image
        path = directory / f"frame-{index:04}.png"
        Image.fromarray(pixels).save(path, compress_level=6)
        entry = {"index": index, **_asset(root, path), "pixel_sha256": digest(pixels.tobytes())}
        if binary:
            entry["support_pixels"] = int(np.count_nonzero(image))
        records.append(entry)
    return {"kind": "constant" if constant else "per-frame", "files": records}


def _motion_basis(legacy: Path, reference: Path, resvg: str) -> tuple[np.ndarray, dict]:
    motion.source(legacy, document_path=None, resvg=resvg, log=[])
    source = json.loads((legacy / "source-manifest.json").read_bytes())
    directory = reference / "motion"
    directory.mkdir()
    document = directory / "document.svm.json"
    shutil.copyfile(legacy / document.name, document)
    for name in ("svg", "mask-svg"):
        shutil.copytree(legacy / name, directory / name)
    masks = []
    frames = []
    for record in source["frames"]:
        tick = record["tick"]
        svg = directory / "svg" / f"{tick:06}.svg"
        mask_svg = directory / "mask-svg" / svg.name
        mask_png = legacy / "offline-mask.png"
        motion._rasterize(resvg, mask_svg, mask_png, background="white")
        masks.append(motion._load_silhouette(mask_png))
        if digest(svg.read_bytes()) != record["svg_sha256"]:
            raise ValueError("Motion source SVG digest mismatch")
        frames.append(
            {
                "tick": tick,
                "seconds": record["seconds"],
                "quality": record["quality"],
                "stale": record["stale"],
                "svg": _asset(reference, svg),
                "mask_svg": _asset(reference, mask_svg),
                "raster_sha256": record["png_sha256"],
                "clean_pixel_sha256": record["clean_pixel_sha256"],
                "basis_degraded_pixel_sha256": record["input_pixel_sha256"],
            }
        )
    return np.stack(masks), {
        "kind": "svm-motion-controlled",
        "basis_profile": source["profile"],
        "document": _asset(reference, document),
        "revision_id": None,
        "snapshot_kind": "read-only standalone Document",
        "svm_code_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, text=True
        ).strip(),
        "motion": source["motion"],
        "timebase_ticks_per_second": source["timebase_ticks_per_second"],
        "canvas": [source["width"], source["height"]],
        "view_box": list(motion_scene.VIEW_BOX),
        "rasterizer": {k: v for k, v in source["rasterizer"].items() if k != "svg_sha256"},
        "frames": frames,
    }


def _synthetic_basis(legacy: Path, seed: int) -> tuple[np.ndarray, dict]:
    synthetic.generate(legacy, seed)
    description = json.loads((legacy / "evaluation/fixture.json").read_bytes())
    y, x = np.indices((synthetic.SIZE, synthetic.SIZE))
    rx, ry = description["scene"]["target_radii_xy"]
    contours = np.stack(
        [
            ((x - cx) * ry) ** 2 + ((y - cy) * rx) ** 2 <= (rx * ry) ** 2
            for cx, cy in description["scene"]["target_center_xy_by_frame"]
        ]
    )
    return contours, {
        "kind": "synthetic-controlled",
        "generator": description["generator"],
        "seed": seed,
        "libraries": description["libraries"],
    }


def _build(stage: Path, scratch: Path, sample: str, clip: str, resvg: str) -> None:
    reference = stage / "reference"
    reference.mkdir(parents=True)
    legacy = scratch / "basis"
    if sample == "B":
        contours, provenance = _motion_basis(legacy, reference, resvg)
    else:
        contours, provenance = _synthetic_basis(legacy, synthetic.DEFAULT_SEED + (clip == "N0"))
    source_stimulus = reference / "basis-stimulus.json"
    shutil.copyfile(legacy / "evaluation/fixture.json", source_stimulus)
    provenance["basis_stimulus"] = _asset(reference, source_stimulus)
    clean = _read_stack(legacy / "evaluation/clean")
    interior = _read_stack(legacy / "evaluation/interior") != 0
    basis_degraded = _read_stack(legacy / "input")
    basis = json.loads(source_stimulus.read_bytes())
    count, height, width = contours.shape
    design = AREAS[sample]
    required = REQUIRED[clip]
    photo, static, background, patch, zone, donor_strip = (
        bundle.rectangle(design[key], width, height)
        for key in ("photo", "sample", "B", "ghost_patch", "G", "donor_ghost")
    )
    x, y, right, bottom = basis["regions"]["defect_xyxy"]
    defect_area = bundle.rectangle([x, y, right, bottom], width, height)
    donor_defect = bundle.rectangle([x - 3, y - 3, right + 3, bottom + 3], width, height)
    donor_area = donor_defect | donor_strip
    if (contours & (donor_area | static | background | zone)).any():
        raise ValueError("authored static support intersects the reference contour")

    # Only the offline constructor sees these clean frames, contours or injection values.
    # Public suspicion windows are entire predeclared rectangles. Donors are separately
    # authorized stationary strips, minus all currently suspect pixels.
    degraded = clean.copy()
    defect = np.zeros_like(contours)
    ghost = np.zeros_like(contours)
    actual_ghost = np.zeros_like(contours)
    offsets = []
    for i in range(count):
        if "M1" in required and i in basis["defect"]["frames_zero_based"]:
            defect[i] = defect_area
            # Preserve the legacy local defect observation, subtract its legacy global flicker.
            degraded[i, defect_area] = (
                basis_degraded[i, defect_area].astype(np.int16)
                - basis["flicker"]["additive_rgb_offset_by_frame"][i]
            )
        if "M3" in required and i in design["ghost_frames"]:
            ghost[i] = patch
            # Replay a short delayed RGB observation at a disclosed displacement of +4 y.
            shifted = np.zeros_like(patch)
            shifted[4:] = contours[i - 1, :-4]
            separated = ~cv2.dilate(contours[i].astype(np.uint8), np.ones((5, 5), np.uint8)).astype(
                bool
            )
            hit = patch & shifted & separated
            actual_ghost[i] = hit
            ys, xs = np.nonzero(hit)
            current = clean[i, ys, xs].astype(np.int16)
            echo = clean[i - 1, ys - 4, xs].astype(np.int16)
            degraded[i, ys, xs] = (3 * current + 2 * echo + 2) // 5
        # Independent slowly varying chromatic and alternating common-mode terms.
        # First frame is the unperturbed observation available to the causal estimator.
        phase = i % 16
        drift = min(phase, 16 - phase) if "M4" in required else 0
        flicker = (0, 6, -6, 8, -8, 4, -4, 0)[i % 8] if "M2" in required else 0
        offset = [flicker + drift, flicker, flicker - drift]
        offsets.append(offset)
        altered = degraded[i, photo].astype(np.int16) + offset
        if altered.min() < 0 or altered.max() > 255:
            raise ValueError("development photometric stimulus would clip")
        degraded[i, photo] = altered

    # Even no-local-degradation tests exercise a genuine clean, suspected region.
    if not (defect | ghost).any():
        defect[5] = defect_area
    repair = defect | ghost
    donor = donor_area & ~repair
    scenario = provenance["kind"]
    profile = f"cvr-development-{sample.lower()}@1"
    video = bundle.make_video(stage / "source", degraded, scenario, clip, profile)
    bundle.make_masks(
        stage / "masks",
        {
            "repair": repair,
            "defect": defect,
            "ghost": ghost,
            "donor_valid": donor,
            "static_sample": static,
        },
        design["repair"],
    )
    config = bundle.default_config(clip, design["photo"])
    write_json(stage / "config.json", config)
    source_info = reference / "source-evidence.json"
    write_json(source_info, provenance)
    offline = {
        **bundle.VERSION,
        "reference_format": "cvr-development-reference@1",
        "qualification": "DRAFT_DEVELOPMENT_ONLY",
        "clip_id": clip,
        "profile_id": profile,
        "scenario_kind": scenario,
        "frame_count": count,
        "width": width,
        "height": height,
        "frame_ticks": video["frame_ticks"],
        "bundle_digest": video["bundle_digest"],
        "mask_manifest_sha256": digest((stage / "masks/mask-manifest.json").read_bytes()),
        "config_sha256": digest((stage / "config.json").read_bytes()),
        "required_metrics": required,
        "source_evidence": _asset(reference, source_info),
        "stimulus": {
            "id": STIMULUS,
            "areas": design,
            "photometric_offsets_rgb": offsets,
            "echo_displacement_xy": [0, 4],
            "echo_blend": "2/5",
            "local_defect": "preserved basis defect minus basis frame-wide flicker",
            "scope": "development; no frozen holdout or segmentation rule",
        },
        "regions": {},
    }
    if clip != "N0":
        offline["clean"] = _records(reference, "clean", clean, binary=False)
        for role, array in (
            ("E", defect_area),
            ("B", background),
            ("G", zone),
            ("I", interior),
            ("contour", contours),
            ("ghost_truth", actual_ghost),
        ):
            offline["regions"][role] = _records(reference, role, array, binary=True)
    write_json(reference / "reference-manifest.json", offline)


def generate(output: Path, sample: str, clip: str | None = None, *, resvg: str = "resvg") -> None:
    if sample not in AREAS:
        raise ValueError("unknown sample")
    clip = clip or ("S5" if sample == "A" else "B5")
    if clip not in REQUIRED or (clip == "B5") != (sample == "B"):
        raise ValueError("unsupported development clip for sample")
    if output.exists():
        raise ValueError("fixture output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix=".cvr-fixture-") as temporary:
        scratch = Path(temporary)
        stage = scratch / "package"
        _build(stage, scratch, sample, clip, resvg)
        stage.rename(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True, choices=("A", "B"))
    parser.add_argument("--clip", choices=sorted(REQUIRED))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resvg", default="resvg")
    args = parser.parse_args()
    try:
        generate(args.output, args.sample, args.clip, resvg=args.resvg)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Unable to construct the development fixture: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
