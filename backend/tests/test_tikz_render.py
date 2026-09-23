"""Resa delle figure `tikz` (W6-T3): XeLaTeX vero, SVG e oracolo geometrico.

- catena di misura, circuito (circuitikz) e grafico (pgfplots) compilano in
  una pagina, l'SVG passa `normalize_svg`, nessun difetto geometrico;
- controlli negativi (piano, M5): nodi sovrapposti, testo che esce dal
  riquadro, figura troppo larga (testo troppo piccolo nella dispensa),
  contenuto fuori dalla pagina → difetti segnalati;
- testo giapponese con Noto Sans CJK, nessun glifo perso.

Richiede xelatex e pdftocairo (container `test` con TeX), altrimenti
`[dep:tex]`.
"""

from __future__ import annotations

import pytest

from app.services import tikz_compile_service as tex
from app.services.figure_compute import tikz_geometry as geo
from app.services.figure_compute import tikz_preamble as pre
from app.services.figure_compute.tikz_lexer import check
from app.services.svg_normalize import normalize_svg
from tests.dep_guard import require_binary
from tests.test_tikz_validator import CHAIN, CIRCUIT, PLOT


@pytest.fixture(autouse=True)
def _tex() -> None:
    require_binary("tex", "xelatex", "kpsewhich", "pdftocairo")


def _render(source: str) -> tuple[tex.CompileResult, geo.TikzGeometry]:
    check(source, max_chars=8000)
    document, lines = pre.document(source)
    result = tex.compile_document(document, preamble_lines=lines)
    return result, geo.analyze(result.pdf, has_axis=pre.uses_axis(source))


@pytest.mark.parametrize("source", [CHAIN, CIRCUIT, PLOT], ids=["chain", "circuit", "plot"])
def test_teaching_figures_render_clean(source: str) -> None:
    result, geometry = _render(source)
    svg = normalize_svg(result.svg, max_bytes=1_500_000)
    assert svg.width_px and svg.height_px
    assert "<script" not in svg.svg and "<image" not in svg.svg
    assert geometry.defects == ()
    # Soglia dei difetti (le etichette degli assi di pgfplots sono ~7,6 pt).
    assert geometry.min_font_pt is not None and geometry.min_font_pt >= geo.MIN_TEXT_PT


NEGATIVES = {
    "overlapping_nodes": (
        r"""\begin{tikzpicture}
  \node[draw] (a) at (0,0) {Condizionamento};
  \node[draw] (b) at (0.5,0) {Sensore};
\end{tikzpicture}""",
        "labels_overlap",
    ),
    "text_out_of_box": (
        r"""\begin{tikzpicture}
  \draw (0,0) rectangle (1.2,0.8);
  \node at (1.2,0.4) {Condizionamento};
\end{tikzpicture}""",
        "text_outside_owner",
    ),
    "too_wide": (
        r"""\begin{tikzpicture}[node distance=30mm]
  \node[draw] (a) {Sensore};
  \foreach \i/\j in {b/a, c/b, d/c, e/d, f/e, g/f} { \node[draw, right=of \j] (\i) {Blocco}; }
\end{tikzpicture}""",
        "text_small",
    ),
    "clipped": (
        r"""\begin{tikzpicture}
  \useasboundingbox (0,0) rectangle (1,1);
  \node[draw] at (3,0.5) {Elaborazione};
\end{tikzpicture}""",
        "content_outside_page",
    ),
}


@pytest.mark.parametrize("name", sorted(NEGATIVES))
def test_negative_controls_are_flagged(name: str) -> None:
    source, defect = NEGATIVES[name]
    _result, geometry = _render(source)
    assert any(d.startswith(defect) for d in geometry.defects), geometry.defects


def test_cjk_text_uses_the_cjk_font() -> None:
    source = r"\begin{tikzpicture}\node[draw] {センサー};\end{tikzpicture}"
    result, geometry = _render(source)
    assert geometry.defects == ()
    assert pre.font_for(source) == pre.FONT_JP
    assert result.pdf.startswith(b"%PDF")
