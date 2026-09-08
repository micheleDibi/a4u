"""`core.logging._WeasyPrintSvgNoiseFilter`: il filtro è agganciato solo in
`configure_logging`, quindi si istanzia direttamente con record fittizi.

WeasyPrint segnala come «unknown property» le proprietà SVG che Mermaid,
matplotlib (`font-*`, `clip-path`), vl-convert e `dot` scrivono negli
attributi `style`: rumore non azionabile. Gli altri warning (font mancanti,
immagini rotte) e i record di altri logger passano.
"""

from __future__ import annotations

import logging

import pytest

from app.core.logging import _WEASYPRINT_SVG_NOISE_RE, _WeasyPrintSvgNoiseFilter


def _record(message: str, *, name: str = "weasyprint") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.WARNING,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )


@pytest.mark.parametrize(
    "message",
    [
        "Ignored `fill: #0072B2` at 1:1, unknown property.",
        "Ignored `stroke-width: 1.5` at 3:1, unknown property.",
        "Ignored `font-family: Noto Sans` at 1:1, unknown property.",
        "Ignored `font-size: 10px` at 1:1, unknown property.",
        "Ignored `font-weight: bold` at 1:1, unknown property.",
        "Ignored `clip-path: url(#p1)` at 1:1, unknown property.",
        "Ignored `clip-rule: evenodd` at 1:1, unknown property.",
        "Ignored `vector-effect: non-scaling-stroke` at 1:1, unknown property.",
        "Ignored `image-rendering: optimizeQuality` at 1:1, unknown property.",
        "Ignored `text-anchor: middle` at 1:1, unknown property.",
        "Ignored `dominant-baseline: central` at 1:1, unknown property.",
        "Ignored `marker-end: url(#arrow)` at 1:1, unknown property.",
    ],
)
def test_svg_style_noise_is_filtered(message: str):
    assert _WEASYPRINT_SVG_NOISE_RE.search(message)
    assert _WeasyPrintSvgNoiseFilter().filter(_record(message)) is False


@pytest.mark.parametrize(
    "message",
    [
        "Failed to load image at 'data:image/svg+xml;base64,...'",
        "Font 'Noto Sans' not found, falling back to DejaVu Sans",
        "Ignored `float: left` at 1:1, unknown property.",  # proprietà CSS vera
        "Ignored `fill: red` at 1:1, invalid value.",  # non è «unknown property»
    ],
)
def test_actionable_weasyprint_warnings_pass(message: str):
    assert _WeasyPrintSvgNoiseFilter().filter(_record(message)) is True


def test_other_loggers_are_never_filtered():
    noisy = "Ignored `fill: #000` at 1:1, unknown property."
    assert _WeasyPrintSvgNoiseFilter().filter(_record(noisy, name="app.pdf")) is True


def test_child_weasyprint_loggers_are_filtered_too():
    noisy = "Ignored `font-style: italic` at 1:1, unknown property."
    assert _WeasyPrintSvgNoiseFilter().filter(_record(noisy, name="weasyprint.css")) is False
