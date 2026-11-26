"""Regression for the combined development fixture's required ghost metric."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from . import formal_bundle as bundle
from .causal_repair import restore
from .formal_fixture import generate


class DevelopmentFixtureTests(unittest.TestCase):
    def test_combined_a_has_measurable_ghost_and_repairs_it(self):
        with TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            generate(fixture, "A", "S5")
            before, video = bundle.read_video(fixture / "source")
            masks, manifest = bundle.read_masks(fixture / "masks", video)
            config = bundle.read_config(fixture / "config.json", video, manifest)
            after, reports = restore(before, masks, manifest, config)

            reference = fixture / "reference"
            width, height = video["width"], video["height"]
            zone, _ = bundle.sequence(reference / "G", 1, width, height, mask=True)
            clean, _ = bundle.sequence(reference / "clean", len(before), width, height)
            region = zone[0]
            error = np.abs(before[:, region].astype(np.int16) - clean[:, region])
            self.assertGreaterEqual(np.maximum(error - 1, 0).mean(), 1.0)
            np.testing.assert_array_equal(after[:, region], clean[:, region])
            self.assertEqual(sum(row["unresolved_pixels"] for row in reports), 0)
