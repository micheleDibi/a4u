"""Markup unico delle figure (D4, Q2): `render_figure_html` + partial Jinja.

Quarto `Environment` Jinja del backend, dedicato ai partial in
`templates/partials/` e con `autoescape=True` incondizionato (gli ambienti
dei PDF hanno autoescape solo sulla dispensa; slide e discorso no): il
partial è reso in Python e inserito come stringa nel markdown della
dispensa o nel contesto del template slide, quindi l'escape deve avvenire
qui, una volta sola.

Contratto:
- `body_html` è `Markup` prodotto dai renderer (SVG Mermaid inline, `<img>`
  con data URI, placeholder legacy) e passa intatto; `None` produce il
  fallback `<pre class="figure-fallback">` con il sorgente escapato;
- la didascalia riceve `strip_figure_prefix` (solo a render) e l'etichetta
  «Figura N.» / «Figure N.» (`courses.figures.label` interpolata con `n`)
  oppure «Figura.» (`labelUnnumbered`) quando `number` è `None` (slide e
  frame video, A2);
- l'output non contiene righe vuote: nel markdown della dispensa il blocco
  è un HTML block di markdown-it, che si chiude alla prima riga vuota. Le
  righe vuote del sorgente di fallback sono rese con U+00A0 (non è spazio
  per markdown-it, è invisibile nel `<pre>`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from app.services.figure_numbering import strip_figure_prefix
from app.services.figure_theme import _interpolate, figure_labels

FigureVariant = Literal["lesson", "slide"]

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
PARTIALS_DIR = TEMPLATES_DIR / "partials"

_env = Environment(
    loader=FileSystemLoader(str(PARTIALS_DIR)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

_BLANK_LINE_RE = re.compile(r"(?m)^[ \t]*$")
_CLASS_TOKEN_RE = re.compile(r"[^a-z0-9_-]+")


def _one_line(text: str | None) -> str:
    """Collassa gli spazi bianchi (anche a capo) di un testo di didascalia:
    l'HTML li collasserebbe comunque e una riga vuota chiuderebbe l'HTML
    block nel markdown."""
    return " ".join((text or "").split())


def _fallback_text(source: str | None) -> Markup:
    """Sorgente escapato con le righe vuote neutralizzate (U+00A0)."""
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n")
    escaped = str(escape(text))
    return Markup(_BLANK_LINE_RE.sub("\u00a0", escaped))


def figure_label(labels: Mapping[str, str], number: int | None) -> str:
    """«Figura 3.» oppure «Figura.» (slide) dalla mappa `figure_labels`."""
    if number is None:
        return labels["courses.figures.labelUnnumbered"]
    return _interpolate(labels["courses.figures.label"], {"n": number})


def render_figure_html(
    *,
    body_html: Markup | None,
    caption: str,
    alt_text: str,
    asset_id: str,
    fmt: str,
    number: int | None,
    labels: Mapping[str, str] | None,
    variant: FigureVariant,
    fallback_source: str | None = None,
    extra_caption: str = "",
) -> str:
    """Rende il partial `partials/figure.html.j2` (vedi la docstring del
    modulo). `labels` è la mappa di `figure_labels(language)`; `None`
    equivale alla lingua di fallback (it)."""
    labels = labels if labels is not None else figure_labels(None)
    caption_text = _one_line(strip_figure_prefix(_one_line(caption)))
    alt = _one_line(alt_text)
    fmt_class = _CLASS_TOKEN_RE.sub("", (fmt or "").lower()) or "unknown"
    template = _env.get_template("figure.html.j2")
    html = template.render(
        variant=variant,
        fmt_class=fmt_class,
        asset_id=_one_line(asset_id),
        aria_label=alt or caption_text,
        body_html=body_html,
        fallback_source=_fallback_text(fallback_source) if body_html is None else "",
        label=figure_label(labels, number),
        caption=caption_text,
        extra_caption=_one_line(extra_caption),
    )
    return html.strip()


__all__ = ["PARTIALS_DIR", "TEMPLATES_DIR", "FigureVariant", "figure_label", "render_figure_html"]
