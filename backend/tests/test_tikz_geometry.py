"""Oracolo geometrico delle figure `tikz`, parti pure (senza TeX).

- pedici e apici riconosciuti dal glifo vicino, anche quando sono la
  maggioranza (`V_{out}`), ma al più `MAX_SCRIPT_CHAIN` glifi: una scritta
  piccola accanto a un nodo grande resta testo del corpo;
- costo lineare nei glifi (griglia spaziale), non quadratico;
- accenti matematici fuori dal confronto delle sovrapposizioni;
- soglia `CHAR_OVERLAP` fissata.
"""

from __future__ import annotations

import time

from app.services.figure_compute import tikz_geometry as geo


def _row(text: str, *, x: float, size: float, top: float = 0.0) -> list[tuple[str, geo.Box, float]]:
    width = 0.55 * size
    return [
        (ch, (x + i * width, top, x + (i + 1) * width, top + size), size)
        for i, ch in enumerate(text)
    ]


def test_subscripts_are_found_even_when_they_are_the_majority() -> None:
    base = _row("V", x=0.0, size=9.0)
    sub = _row("out", x=base[-1][1][2], size=6.0, top=4.0)
    flags = geo._script_flags(base + sub)
    assert flags == [False, True, True, True]


def test_a_long_small_label_is_body_text_not_a_subscript() -> None:
    node = _row("Nodo", x=0.0, size=9.0)
    small = _row("etichettamoltopiccola", x=node[-1][1][2], size=4.2, top=4.8)
    flags = geo._script_flags(node + small)
    # I glifi entro mezzo corpo dal nodo sono semi; da ciascuno la catena
    # si ferma a MAX_SCRIPT_CHAIN: il resto della scritta è corpo del testo.
    assert sum(flags[len(node) :]) <= geo.MAX_SCRIPT_CHAIN + 2
    assert not any(flags[len(node) + geo.MAX_SCRIPT_CHAIN + 2 :])


def test_script_detection_is_linear_in_the_glyphs() -> None:
    chars = []
    for line in range(40):
        chars += _row("Sensore di prova ADC " * 3, x=0.0, size=9.0, top=line * 14.0)
    assert len(chars) > 2000
    started = time.perf_counter()
    geo._script_flags(chars[: geo.MAX_CHARS])
    assert time.perf_counter() - started < 1.0


def test_accents_are_not_glyphs_that_overlap() -> None:
    # Circonflesso (U+02C6, hat), punto (U+02D9, dot), macron (U+00AF, bar).
    assert all(geo._is_accent(ch) for ch in ("\u02c6", "\u02d9", "\u00af"))
    assert not geo._is_accent("a") and not geo._is_accent("ab")


def test_overlap_threshold_is_pinned() -> None:
    a = (0.0, 0.0, 10.0, 10.0)
    assert geo._chars_overlap(a, (5.0, 0.0, 15.0, 10.0))  # metà
    assert not geo._chars_overlap(a, (8.0, 0.0, 18.0, 10.0))  # un quinto
    assert geo.CHAR_OVERLAP == 0.35
