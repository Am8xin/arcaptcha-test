"""Locate the gap of an ARCcaptcha slider puzzle (OpenCV).

Adapted from the original prototype: the piece outline (from its alpha
channel) is matched against the edges of the background image using a
distance transform, restricted to the piece's current row for speed.
"""

from typing import NamedTuple

import cv2
import numpy as np


class GapPosition(NamedTuple):
    x: int
    y: int
    score: float


def find_gap(
    background: np.ndarray,
    piece: np.ndarray,
    y_hint: int | None = None,
    y_tolerance: int = 2,
) -> GapPosition:
    """Find the gap position by matching the piece outline against background edges."""
    if piece.ndim != 3 or piece.shape[2] != 4:
        raise ValueError("The puzzle piece image must have an alpha channel")

    mask = (piece[:, :, 3] > 128).astype(np.uint8)
    if not mask.any():
        raise ValueError("The puzzle piece image is fully transparent")

    outline = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    outline_y, outline_x = np.nonzero(outline)

    background_bgr = background[:, :, :3] if background.ndim == 3 else background
    gray = cv2.cvtColor(background_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 100)
    distance = cv2.distanceTransform((edges == 0).astype(np.uint8), cv2.DIST_L2, 3)

    height, width = gray.shape
    piece_height, piece_width = piece.shape[:2]
    if piece_height > height or piece_width > width:
        raise ValueError("The puzzle piece is larger than the background")

    if y_hint is None:
        y_values = range(0, height - piece_height + 1)
    else:
        low = max(0, y_hint - y_tolerance)
        high = min(height - piece_height, y_hint + y_tolerance)
        y_values = range(low, high + 1)

    best = GapPosition(x=0, y=0, score=float("inf"))
    for y in y_values:
        for x in range(0, width - piece_width + 1):
            score = float(distance[outline_y + y, outline_x + x].mean())
            if score < best.score:
                best = GapPosition(x=x, y=y, score=score)

    if not np.isfinite(best.score):
        raise ValueError("Could not locate the puzzle gap")

    return best
