"""Synthetic grayscale sequences. Only input/ is intended for the repair process."""

import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

SIZE = (256, 256)
FPS = 12
COUNT = 24
SEED = 6082026
ROI = (16, 72, 232, 184)
BACKGROUND = (20, 76, 60, 100)
SCENARIOS = ("clean", "damage", "flicker", "ghost", "appearance", "combined")


def target(tick: int) -> tuple[int, int, int, int]:
    x = 44 + 6 * tick
    return (x, 112, x + 32, 148)


def ghost(tick: int) -> tuple[int, int, int, int]:
    x, y, _, bottom = target(tick)
    return (x - 12, y, x, bottom)


@dataclass
class Sequence:
    reference: list[Image.Image]
    degraded: list[Image.Image]
    mask: Image.Image


def generate(scenario: str, seed: int = SEED) -> Sequence:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown fixture scenario: {scenario}")
    rng = random.Random(seed)
    background = Image.new("L", SIZE)
    background.putdata([48 + rng.randrange(16) for _ in range(SIZE[0] * SIZE[1])])
    mask = Image.new("L", SIZE)
    mask.paste(255, ROI)
    reference = []
    degraded = []
    for tick in range(COUNT):
        box = target(tick)
        clean = background.copy()
        clean.paste(208, box)
        damaged = clean.copy()
        if scenario in ("appearance", "combined"):
            damaged.paste(208 + (24 if tick % 2 else -24), box)
        if scenario in ("damage", "combined"):
            x, y, _, _ = box
            damaged.paste(80, (x + 5, y + 5, x + 15, y + 15))
        if scenario in ("ghost", "combined") and tick >= 2:
            damaged.paste(110, ghost(tick))
        if scenario in ("flicker", "combined"):
            patch = clean.crop(BACKGROUND)
            offset = 28 if tick % 2 else -28
            damaged.paste(
                patch.point([max(0, min(255, v + offset)) for v in range(256)]), BACKGROUND
            )
        reference.append(clean)
        degraded.append(damaged)
    return Sequence(reference, degraded, mask)


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def write_fixture(root: Path, scenario: str, seed: int = SEED) -> None:
    fixture = generate(scenario, seed)
    # Refuse an existing root so two lines cannot overwrite each other's fixture files.
    root.mkdir()
    (root / "input").mkdir()
    (root / "evaluation").mkdir()

    def save(image: Image.Image, name: str) -> dict[str, str]:
        path = root / name
        image.save(path, format="PNG")
        return {
            "path": name,
            "file_sha256": digest(path.read_bytes()),
            "pixel_sha256": digest(image.tobytes()),
        }

    def save_frames(frames: list[Image.Image], folder: str) -> list[dict]:
        return [
            {"index": index, "tick": index, **save(image, f"{folder}/{index:04d}.png")}
            for index, image in enumerate(frames)
        ]

    manifest = {
        "fixture_version": "c-ci-r1-draft-1",
        "size": SIZE,
        "fps": [FPS, 1],
        "ticks_per_second": FPS,
        "frames": save_frames(fixture.degraded, "input"),
        "mask": save(fixture.mask, "input/mask.png"),
    }
    truth = {
        "scenario": scenario,
        "seed": seed,
        "frames": save_frames(fixture.reference, "evaluation"),
    }
    for name, payload in (("input/fixture.json", manifest), ("evaluation/truth.json", truth)):
        (root / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New directory with an existing parent")
    parser.add_argument("--scenario", choices=SCENARIOS, default="combined")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    write_fixture(args.output, args.scenario, args.seed)
