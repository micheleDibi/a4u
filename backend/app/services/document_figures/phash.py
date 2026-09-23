"""Hash percettivo (pHash, 64 bit) per riconoscere ritagli uguali o quasi.

DCT 2D della miniatura 32×32 in scala di grigi, blocco 8×8 delle basse
frequenze (senza la componente continua) confrontato con la mediana.
Solo numpy e Pillow.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

_SIZE = 32
_BLOCK = 8


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    matrix = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    matrix[0, :] = np.sqrt(1.0 / n)
    return matrix


_DCT = _dct_matrix(_SIZE)


def phash(image: Image.Image) -> str:
    gray = image.convert("L").resize((_SIZE, _SIZE), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float64)
    coeffs = _DCT @ pixels @ _DCT.T
    block = coeffs[:_BLOCK, :_BLOCK].flatten()[1:]
    bits = block > np.median(block)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")
