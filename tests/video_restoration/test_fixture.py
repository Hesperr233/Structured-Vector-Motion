import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageFilter, UnidentifiedImageError

from .fixture import COUNT, FPS, SCENARIOS, SIZE, generate, write_fixture
from .oracle import measure, validate_frames


class FixtureTest(unittest.TestCase):
    def test_reference_and_injected_effects(self) -> None:
        expected = {
            "damage": "damage_mae",
            "flicker": "flicker",
            "ghost": "ghost_mae",
            "appearance": "appearance",
        }
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                fixture = generate(scenario)
                self.assertTrue(
                    all(value == 0 for value in vars(measure(fixture, fixture.reference)).values())
                )
                scores = measure(fixture, fixture.degraded)
                self.assertEqual(scores.outside_pixels_changed, 0)
                if scenario == "clean":
                    self.assertEqual(scores.roi_mae, 0)
                else:
                    self.assertGreater(scores.roi_mae, 0)
                if scenario in expected:
                    self.assertGreater(getattr(scores, expected[scenario]), 0)
                if scenario == "combined":
                    self.assertTrue(all(getattr(scores, field) > 0 for field in expected.values()))

    def test_oracle_detects_frozen_blurred_and_unauthorized_changes(self) -> None:
        fixture = generate("clean")
        frozen = measure(fixture, [fixture.reference[0]] * COUNT)
        blurred = measure(
            fixture, [frame.filter(ImageFilter.BoxBlur(2)) for frame in fixture.reference]
        )
        self.assertEqual(frozen.flicker, 0)
        self.assertEqual(frozen.worst_edge_offset_px, 6 * (COUNT - 1))
        self.assertGreater(blurred.worst_edge_mae, 0)
        output = [frame.copy() for frame in fixture.reference]
        output[-1].putpixel((0, 0), 255)
        self.assertEqual(measure(fixture, output).outside_pixels_changed, 1)

    def test_invalid_oracle_inputs(self) -> None:
        fixture = generate("combined")
        for frames in (
            [],
            fixture.degraded[:-1],
            [Image.new("L", (4, 4))] * COUNT,
            [Image.new("RGB", SIZE)] * COUNT,
        ):
            with self.subTest(count=len(frames)), self.assertRaises(ValueError):
                validate_frames(frames)
        fixture.mask = Image.new("L", (4, 4))
        with self.assertRaisesRegex(ValueError, "mask"):
            measure(fixture, fixture.degraded)

    def test_fixture_manifest_readback_determinism_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / "a", Path(temp) / "b"
            write_fixture(first, "combined")
            write_fixture(second, "combined")
            self.assertFalse((first / "input/evaluation").exists())
            for name in ("input/fixture.json", "evaluation/truth.json"):
                payload = (first / name).read_bytes()
                self.assertEqual(payload, (second / name).read_bytes())
                manifest = json.loads(payload)
                self.assertEqual(len(manifest["frames"]), COUNT)
                self.assertEqual([item["tick"] for item in manifest["frames"]], list(range(COUNT)))
                if "fps" in manifest:
                    self.assertEqual(manifest["fps"], [FPS, 1])
                records = manifest["frames"] + ([manifest["mask"]] if "mask" in manifest else [])
                for record in records:
                    path = first / record["path"]
                    self.assertEqual(
                        record["file_sha256"],
                        "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                    with Image.open(path) as image:
                        self.assertEqual((image.format, image.mode, image.size), ("PNG", "L", SIZE))
                        self.assertEqual(
                            record["pixel_sha256"],
                            "sha256:" + hashlib.sha256(image.tobytes()).hexdigest(),
                        )
            input_frame = first / "input/0000.png"
            original = input_frame.read_bytes()
            with self.assertRaises(FileExistsError):
                write_fixture(first, "clean")
            self.assertEqual(input_frame.read_bytes(), original)
            corrupt = first / "corrupt.png"
            corrupt.write_bytes(original[:12])
            with self.assertRaises(UnidentifiedImageError):
                Image.open(corrupt)
