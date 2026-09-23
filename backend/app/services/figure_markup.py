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
- la didascalia riceve `strip_figure_prefix` (solo a render, via
  `caption_text`, condivisa con il collector del math del PDF) e
  l'etichetta «Figura N.» / «Figure N.» (`courses.figures.label`
  interpolata con `n`) oppure «Figura.» (`labelUnnumbered`) quando
  `number` è `None` (slide e frame video, A2); con `caption_renderer` il
  testo già pre-trattato è reso da un renderer esterno (math inline del
  PDF, D9) che ritorna `Markup`; senza, l'output è byte-identico a prima;
- `cite` (rimando testuale degli asset, `AssetRefs.cite`, D4) è applicato
  al testo della didascalia SUBITO dopo `caption_text` e prima di
  qualunque altro passo, quindi vale anche per l'accessible name: un
  `[FIG:a]` nella didascalia diventa «Figura 1» sia nel `<figcaption>`
  sia in `aria-label`, come nel mirror `FigureFrame.tsx` (`const text =
  cite ? cite(stripped) : stripped` e `aria-label={altText || text}`).
  `alt_text` resta testo d'autore e non è mai citato, su entrambi i lati;
- `extra_caption` (coda calcolata di `function`, D9) è omessa quando la
  didascalia dell'autore termina già con lo stesso testo (guardia
  anti-doppia coda di Q4: `if caption.rstrip().endswith(tail): tail = ""`,
  da replicare nel frontend in `FigureFrame.extraCaption`);
- `box` (solo slide e frame video, D12) è il `FigureBox` massimo
  dell'immagine in mm, emesso come proprietà CSS personalizzate
  `--figure-w`/`--figure-h` nello `style` del `<figure>`, DOPO
  `aria-label`; la dispensa usa `max_figure_height_cm` e non passa box, e
  senza box l'output è byte-identico a prima;
- l'output non contiene righe vuote: nel markdown della dispensa il blocco
  è un HTML block di markdown-it, che si chiude alla prima riga vuota. Le
  righe vuote del sorgente di fallback sono rese con U+00A0 (non è spazio
  per markdown-it, è invisibile nel `<pre>`); le righe vuote di `body_html`
  (spazio bianco fra tag di un SVG inline: Mermaid non ne emette, le
  fixture 10 e 11 ne hanno zero) sono rimosse, così la garanzia vale per
  l'intero blocco e non solo per le parti prodotte dal partial.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from app.services.figure_numbering import strip_figure_prefix
from app.services.figure_theme import asset_label, figure_labels

FigureVariant = Literal["lesson", "slide"]


@dataclass(frozen=True)
class FigureBox:
    """Box massimo dell'immagine (o del `<pre>` di fallback) di una figura
    nelle slide, in mm, arrotondato al decimo e strettamente positivo.
    Emesso come `--figure-w`/`--figure-h` sul `<figure>` (D12); il CSS del
    template slide lo legge con `var()`."""

    w_mm: float
    h_mm: float

    def __post_init__(self) -> None:
        w = round(float(self.w_mm), 1)
        h = round(float(self.h_mm), 1)
        if not (w > 0 and h > 0):
            raise ValueError(f"FigureBox non positivo: {self.w_mm!r} × {self.h_mm!r}")
        object.__setattr__(self, "w_mm", w)
        object.__setattr__(self, "h_mm", h)

    @property
    def style(self) -> str:
        return f"--figure-w: {self.w_mm:.1f}mm; --figure-h: {self.h_mm:.1f}mm"


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
PARTIALS_DIR = TEMPLATES_DIR / "partials"

_env = Environment(
    loader=FileSystemLoader(str(PARTIALS_DIR)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

_BLANK_LINE_RE = re.compile(r"(?m)^[ \t]*$")
_BLANK_LINE_WITH_BREAK_RE = re.compile(r"(?m)^[ \t]*\r?\n")
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


def _body_without_blank_lines(body_html: Markup) -> Markup:
    """Body senza righe vuote (spazio bianco fra tag): una riga vuota
    dentro l'SVG inline chiuderebbe l'HTML block di markdown-it e il resto
    del wrapper finirebbe in un `<p>`. Per gli SVG senza righe vuote (tutti
    quelli di Mermaid) è l'identità: il body resta byte-identico (A11-L3)."""
    if "\n" not in body_html:
        return body_html
    return Markup(_BLANK_LINE_WITH_BREAK_RE.sub("", str(body_html)))


# Punteggiatura che chiude una didascalia (mirror di `CAPTION_END_RE` in
# `FigureFrame.tsx`).
_CAPTION_END_PUNCT = ".!?…:;"


def figure_label(labels: Mapping[str, str], number: int | None) -> str:
    """«Figura 3.» oppure «Figura.» (slide) dalla mappa `figure_labels`:
    wrapper di `figure_theme.asset_label` per il kind `FIG`."""
    return asset_label(labels, "FIG", number)


def caption_text(caption: str) -> str:
    """Testo della didascalia come lo vede il partial: su una riga e senza
    il prefisso «Figura N.» dell'autore. Unico pre-trattamento, usato qui
    e dal collector del math del PDF, così la chiave di una formula nella
    didascalia è la stessa in raccolta e a render."""
    return _one_line(strip_figure_prefix(_one_line(caption)))


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
    cite: Callable[[str], str] | None = None,
    caption_renderer: Callable[[str], Markup] | None = None,
    box: FigureBox | None = None,
    attribution: str = "",
) -> str:
    """Rende il partial `partials/figure.html.j2` (vedi la docstring del
    modulo). `labels` è la mappa di `figure_labels(language)`; `None`
    equivale alla lingua di fallback (it). `cite` riscrive i tag degli
    asset della didascalia nel rimando testuale, subito dopo
    `strip_figure_prefix`: è l'UNICO punto in cui la didascalia cambia
    testo, così il `<figcaption>` e l'`aria-label` dicono la stessa cosa.
    `caption_renderer` riceve il testo della didascalia DOPO
    `strip_figure_prefix`, `cite` e la guardia della coda calcolata (che
    lavorano sul testo, non sul markup) e ritorna `Markup`; l'etichetta,
    `aria_label` e la coda restano testo. `box` (slide, D12) mette
    `--figure-w`/`--figure-h` sul `<figure>`; è facoltativo per ogni
    variante: senza box il markup non cambia. `attribution` è la riga
    «Fonte» di una figura di fonte (testo del server, escapato qui), in
    coda alla didascalia; vuota, il markup non cambia."""
    labels = labels if labels is not None else figure_labels(None)
    text = caption_text(caption)
    if cite is not None:
        text = cite(text)
    alt = _one_line(alt_text)
    extra = _one_line(extra_caption)
    if extra and text.rstrip().endswith(extra.rstrip()):
        # Guardia anti-doppia coda (Q4): il docente ha copiato nella
        # didascalia la coda calcolata mostrata dall'anteprima.
        extra = ""
    caption_out = text
    if extra and text and text[-1] not in _CAPTION_END_PUNCT:
        # La coda calcolata è un periodo a sé («Zeri in x = −1, 1.»): senza
        # il punto si fonderebbe con la didascalia del docente, che il
        # prompt non obbliga a chiudere («…razionale Zeri in x = −1, 1.»).
        caption_out = f"{text}."
    fmt_class = _CLASS_TOKEN_RE.sub("", (fmt or "").lower()) or "unknown"
    template = _env.get_template("figure.html.j2")
    html = template.render(
        variant=variant,
        fmt_class=fmt_class,
        asset_id=_one_line(asset_id),
        aria_label=alt or text,
        body_html=_body_without_blank_lines(body_html) if body_html is not None else None,
        fallback_source=_fallback_text(fallback_source) if body_html is None else "",
        label=figure_label(labels, number),
        caption=caption_renderer(caption_out) if caption_renderer and caption_out else caption_out,
        extra_caption=extra,
        box_style=box.style if box is not None else "",
        attribution=_one_line(attribution),
    )
    return html.strip()


__all__ = [
    "PARTIALS_DIR",
    "TEMPLATES_DIR",
    "FigureBox",
    "FigureVariant",
    "caption_text",
    "figure_label",
    "render_figure_html",
]
