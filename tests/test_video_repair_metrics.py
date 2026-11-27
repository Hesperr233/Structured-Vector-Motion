"""Independent RGB scoring checks for the R1-002 draft formulas."""

import unittest

import numpy as np


def scores(frames, reference, *, damage, background, ghost, interior):
    """Return draft measures in DN. Masks may be spatial or per-frame as appropriate."""
    residual = frames.astype(np.int16) - reference
    absolute = np.abs(residual)
    return {
        "damage_mae": float(absolute[damage].mean()),
        "flicker": float(np.abs(np.diff(residual[:, background], axis=0)).mean()),
        "ghost_residual": float(np.maximum(absolute[ghost] - 1, 0).mean()),
        "appearance_std": float(
            np.std(
                [frame[mask].mean(axis=0) for frame, mask in zip(residual, interior, strict=True)],
                axis=0,
            ).mean()
        ),
    }


class RGBMetricsTest(unittest.TestCase):
    def test_signed_temporal_residual_and_per_channel_appearance(self):
        reference = np.full((24, 256, 256, 3), 128, dtype=np.uint8)
        frames = reference.copy()
        frames[:, 0, 0] = (132, 134, 136)
        frames[::2, 0, 1] = 140
        frames[1::2, 0, 1] = 116
        frames[:, 0, 2] = (130, 132, 134)
        frames[::2, 0, 3] = (124, 126, 128)
        frames[1::2, 0, 3] = (132, 130, 128)
        masks = np.zeros((4, 256, 256), dtype=bool)
        masks[np.arange(4), 0, np.arange(4)] = True
        result = scores(
            frames,
            reference,
            damage=np.broadcast_to(masks[0], frames.shape[:-1]),
            background=masks[1],
            ghost=np.broadcast_to(masks[2], frames.shape[:-1]),
            interior=np.broadcast_to(masks[3], frames.shape[:-1]),
        )
        self.assertEqual(
            result,
            {"damage_mae": 6.0, "flicker": 24.0, "ghost_residual": 3.0, "appearance_std": 2.0},
        )
