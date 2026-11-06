import numpy as np
import pytest

from app.bots.solver import find_gap


def test_piece_without_alpha_rejected():
    with pytest.raises(ValueError):
        find_gap(np.zeros((50, 50, 3), np.uint8), np.zeros((10, 10, 3), np.uint8))


def test_finds_known_gap():
    background = np.full((60, 200, 3), 255, np.uint8)
    background[10:40, 120] = 0
    piece = np.zeros((30, 30, 4), np.uint8)
    piece[5:25, 5:25] = (0, 0, 255, 255)
    gap = find_gap(background, piece, y_hint=10)
    assert 110 <= gap.x <= 125