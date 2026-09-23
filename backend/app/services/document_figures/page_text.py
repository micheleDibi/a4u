"""Testo di una pagina fisica in righe con posizione (da pdfplumber).

Serve al motore euristico (etichette dentro la figura, didascalie) e al
contesto della figura (`context_excerpt`) per entrambi i motori. Le pagine
sono quelle fisiche del PDF, non gli span del riassunto (che contano solo
le pagine con testo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.document_figures.captions import clip_caption, is_figure_caption
from app.services.document_figures.geometry import BBox

_LINE_TOLERANCE = 3.0
_MAX_CONTEXT_CHARS = 700
_HEADER_FOOTER_BAND = 0.08


@dataclass(frozen=True)
class Line:
    text: str
    bbox: BBox


def page_lines(words: list[dict[str, Any]]) -> list[Line]:
    """Raggruppa le parole di `page.extract_words()` in righe (stessa `top`
    entro una tolleranza e vicine in orizzontale)."""
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"]))):
        for row in rows:
            last = row[-1]
            if (
                abs(float(last["top"]) - float(word["top"])) <= _LINE_TOLERANCE
                and float(word["x0"]) - float(last["x1"]) < 25
            ):
                row.append(word)
                break
        else:
            rows.append([word])
    lines = []
    for row in rows:
        row.sort(key=lambda w: float(w["x0"]))
        box = BBox(
            min(float(w["x0"]) for w in row),
            min(float(w["top"]) for w in row),
            max(float(w["x1"]) for w in row),
            max(float(w["bottom"]) for w in row),
        )
        lines.append(Line(" ".join(str(w["text"]) for w in row), box))
    lines.sort(key=lambda line: (line.bbox.top, line.bbox.x0))
    return lines


def caption_near(lines: list[Line], bbox: BBox, *, max_gap: float = 45.0) -> str | None:
    """Didascalia di figura subito sotto (o, in subordine, sopra) il bbox:
    la riga con l'etichetta più le righe che la seguono a passo di riga."""
    below = [
        ln
        for ln in lines
        if ln.bbox.top >= bbox.bottom - 2
        and ln.bbox.top - bbox.bottom <= max_gap
        and ln.bbox.x1 > bbox.x0
        and ln.bbox.x0 < bbox.x1
        and is_figure_caption(ln.text)
    ]
    above = [
        ln
        for ln in lines
        if ln.bbox.bottom <= bbox.top + 2
        and bbox.top - ln.bbox.bottom <= max_gap
        and ln.bbox.x1 > bbox.x0
        and ln.bbox.x0 < bbox.x1
        and is_figure_caption(ln.text)
    ]
    # Didascalia di fianco (figure affiancate al testo, «wrapfigure»): riga
    # con l'etichetta accanto al bbox e sovrapposta in verticale.
    beside = [
        ln
        for ln in lines
        if ln.bbox.top < bbox.bottom
        and ln.bbox.bottom > bbox.top
        and min(abs(ln.bbox.x0 - bbox.x1), abs(bbox.x0 - ln.bbox.x1)) <= 40
        and is_figure_caption(ln.text)
    ]
    candidates = (
        sorted(below, key=lambda ln: ln.bbox.top)
        or sorted(above, key=lambda ln: -ln.bbox.bottom)
        or sorted(beside, key=lambda ln: ln.bbox.top)
    )
    if not candidates:
        return None
    first = candidates[0]
    parts = [first.text]
    cursor = first.bbox
    for ln in lines:
        if ln.bbox.top <= cursor.top:
            continue
        step = ln.bbox.top - cursor.bottom
        if step > 6 or abs(ln.bbox.x0 - first.bbox.x0) > 30 or is_figure_caption(ln.text):
            break
        parts.append(ln.text)
        cursor = ln.bbox
        if len(parts) >= 6:
            break
    return clip_caption(" ".join(parts))


def context_excerpt(
    lines: list[Line],
    bbox: BBox,
    *,
    caption: str | None,
    page_h: float | None = None,
    max_chars: int = _MAX_CONTEXT_CHARS,
) -> str | None:
    """Testo della pagina vicino alla figura (le righe più vicine in
    verticale, esclusi il contenuto della figura, la didascalia, testate e
    piè di pagina), rimesso in ordine di lettura."""
    band = _HEADER_FOOTER_BAND * page_h if page_h else 0.0
    outside = [
        ln
        for ln in lines
        if bbox.coverage_of(ln.bbox) < 0.5
        and not (caption and ln.text in caption)
        and not (page_h and (ln.bbox.bottom <= band or ln.bbox.top >= page_h - band))
    ]

    def distance(ln: Line) -> float:
        if ln.bbox.bottom <= bbox.top:
            return bbox.top - ln.bbox.bottom
        if ln.bbox.top >= bbox.bottom:
            return ln.bbox.top - bbox.bottom
        return 0.0

    chosen: list[Line] = []
    total = 0
    for ln in sorted(outside, key=distance):
        if total + len(ln.text) + 1 > max_chars:
            break
        chosen.append(ln)
        total += len(ln.text) + 1
    if not chosen:
        return None
    chosen.sort(key=lambda ln: (ln.bbox.top, ln.bbox.x0))
    return " ".join(ln.text for ln in chosen)
