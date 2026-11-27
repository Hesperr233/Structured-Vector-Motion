"""Independent measurements in 8-bit grayscale levels, with draft regions."""

from dataclasses import dataclass
from statistics import mean

from PIL import Image, ImageChops, ImageStat

from .fixture import BACKGROUND, COUNT, SIZE, Sequence, ghost, target


@dataclass(frozen=True)
class Scores:
    roi_mae: float
    damage_mae: float
    flicker: float
    ghost_mae: float
    appearance: float
    worst_edge_mae: float
    worst_edge_offset_px: int
    outside_pixels_changed: int


def validate_frames(frames: list[Image.Image]) -> None:
    if len(frames) != COUNT:
        raise ValueError(f"Expected {COUNT} frames, got {len(frames)}")
    for index, frame in enumerate(frames):
        if frame.size != SIZE or frame.mode != "L":
            raise ValueError(f"Frame {index}: expected {SIZE} grayscale L pixels")


def measure(fixture: Sequence, output: list[Image.Image]) -> Scores:
    validate_frames(output)
    validate_frames(fixture.reference)
    validate_frames(fixture.degraded)
    mask = fixture.mask
    if mask.size != SIZE or mask.mode != "L" or set(mask.tobytes()) != {0, 255}:
        raise ValueError("Expected a matching nonempty binary L mask with protected pixels")

    roi_errors, damage_errors, ghost_errors, edge_errors, offsets = [], [], [], [], []
    background_residuals, appearance_residuals = [], []
    outside = 0
    for index, (clean, degraded, result) in enumerate(
        zip(fixture.reference, fixture.degraded, output, strict=True)
    ):
        diff = ImageChops.difference(clean, result)
        roi_errors.append(ImageStat.Stat(diff, mask).mean[0])
        change = ImageChops.difference(degraded, result)
        outside += sum(ImageChops.multiply(change, ImageChops.invert(mask)).histogram()[1:])
        x, y, right, bottom = target(index)
        damage_errors.append(ImageStat.Stat(diff.crop((x + 5, y + 5, x + 15, y + 15))).mean[0])
        if index >= 2:
            ghost_errors.append(ImageStat.Stat(diff.crop(ghost(index))).mean[0])
        edges = ((x - 3, y, x + 3, bottom), (right - 3, y, right + 3, bottom))
        edge_errors.extend(ImageStat.Stat(diff.crop(edge)).mean[0] for edge in edges)
        scan = result.crop((0, y + 20, SIZE[0], y + 21))
        bright = scan.point([0] * 150 + [255] * 106).getbbox()
        offsets.append(max(abs(bright[0] - x), abs(bright[2] - right)) if bright else SIZE[0])
        background_residuals.append(
            tuple(
                a - b
                for a, b in zip(
                    result.crop(BACKGROUND).tobytes(), clean.crop(BACKGROUND).tobytes(), strict=True
                )
            )
        )
        interior = (x + 18, y + 5, right - 4, bottom - 5)
        appearance_residuals.append(ImageStat.Stat(result.crop(interior)).mean[0] - 208)
    return Scores(
        roi_mae=mean(roi_errors),
        damage_mae=mean(damage_errors),
        flicker=mean(
            abs(current - previous)
            for left, right in zip(background_residuals, background_residuals[1:], strict=False)
            for previous, current in zip(left, right, strict=True)
        ),
        ghost_mae=mean(ghost_errors),
        appearance=mean(
            abs(current - previous)
            for previous, current in zip(
                appearance_residuals, appearance_residuals[1:], strict=False
            )
        ),
        worst_edge_mae=max(edge_errors),
        worst_edge_offset_px=max(offsets),
        outside_pixels_changed=outside,
    )
