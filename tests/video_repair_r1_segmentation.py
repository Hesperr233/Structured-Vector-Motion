"""Output silhouettes for the authored R1-002 development scenes."""

from collections.abc import Callable

import numpy as np

MOTION_SEGMENTATION = {"kind": "cvr-b-authored-palette@1"}


def segment_synthetic(frame: np.ndarray) -> np.ndarray:
    # The authored target rim and mark have low green, while its warm fills
    # have high red. The stationary textured background lies between them.
    return (frame[..., 1] < 100) | (frame[..., 0] >= 180)


def segment_motion(frame: np.ndarray) -> np.ndarray:
    import cv2

    # Both moving groups stay within this part of the flat background. The
    # authored static decorations and bands lie outside it.
    crop = np.s_[44:330, 88:552]
    background = np.array([43, 53, 80], dtype=np.int16)
    foreground = np.array(
        [[116, 82, 232], [238, 230, 200], [86, 197, 150], [22, 32, 47], [238, 192, 64]],
        dtype=np.int16,
    )
    delta = frame[crop].astype(np.int16) - background
    follows_palette = np.zeros(delta.shape[:2], dtype=bool)
    inside = np.zeros_like(follows_palette)
    for color in foreground - background:
        axis = int(np.argmax(np.abs(color)))
        coverage = delta[..., axis] / color[axis]
        residual = np.max(np.abs(delta - coverage[..., None] * color), axis=2)
        matches = residual <= 2.5
        follows_palette |= matches
        inside |= matches & (coverage >= 0.49)
    # At boundaries between overlapping fills, the mix need not lie on a ray
    # from the background. Ignore small roundoff and keep visibly mixed fills.
    inside |= ~follows_palette & (np.max(np.abs(delta), axis=2) >= 20)

    # The authored background-colored mark is enclosed by the silhouette.
    filled = np.pad(inside.astype(np.uint8), 1)
    cv2.floodFill(filled, None, (0, 0), 2)
    silhouette = np.zeros(frame.shape[:2], dtype=bool)
    silhouette[crop] = filled[1:-1, 1:-1] != 2
    return silhouette


def development_segmenter(profile: str) -> Callable[[np.ndarray], np.ndarray]:
    return {"A": segment_synthetic, "B": segment_motion}[profile]
