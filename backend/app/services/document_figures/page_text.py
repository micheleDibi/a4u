"""Testo di una pagina fisica in righe con posizione (da pdfplumber).

Serve al motore euristico (etichette dentro la figura, didascalie) e al
contesto della figura (`context_excerpt`) per entrambi i motori. Le pagine
sono quelle fisiche del PDF, non gli span del riassunto (che contano solo
le pagine con testo).
"""

from __future__ import annotations

from collections.abc import Sequence
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


def _vertical_gap(line: Line, box: BBox) -> float | None:
    """Distanza verticale fra la riga e il bbox se la riga sta sopra o sotto
    (sovrapposta in orizzontale); None altrimenti."""
    if not (line.bbox.x1 > box.x0 and line.bbox.x0 < box.x1):
        return None
    if line.bbox.top >= box.bottom - 2:
        return line.bbox.top - box.bottom
    if line.bbox.bottom <= box.top + 2:
        return box.top - line.bbox.bottom
    return None


def _closer_to_another(line: Line, bbox: BBox, others: Sequence[BBox]) -> bool:
    """La didascalia appartiene alla figura più vicina: con figure impilate
    («Fig. 1», figura 1, «Fig. 2», figura 2) la riga sotto la figura 1 è la
    didascalia della 2 se sta più vicina alla 2."""
    mine = _vertical_gap(line, bbox)
    if mine is None:
        return False
    for other in others:
        if other == bbox:
            continue
        gap = _vertical_gap(line, other)
        if gap is not None and gap < mine:
            return True
    return False


def caption_near(
    lines: list[Line], bbox: BBox, *, max_gap: float = 45.0, others: Sequence[BBox] = ()
) -> str | None:
    """Didascalia di figura subito sotto (o, in subordine, sopra) il bbox:
    la riga con l'etichetta più le righe che la seguono a passo di riga.
    `others`: le altre figure della pagina (una didascalia più vicina a
    un'altra figura non viene assegnata a questa)."""
    below = [
        ln
        for ln in lines
        if ln.bbox.top >= bbox.bottom - 2
        and ln.bbox.top - bbox.bottom <= max_gap
        and ln.bbox.x1 > bbox.x0
        and ln.bbox.x0 < bbox.x1
        and is_figure_caption(ln.text)
        and not _closer_to_another(ln, bbox, others)
    ]
    above = [
        ln
        for ln in lines
        if ln.bbox.bottom <= bbox.top + 2
        and bbox.top - ln.bbox.bottom <= max_gap
        and ln.bbox.x1 > bbox.x0
        and ln.bbox.x0 < bbox.x1
        and is_figure_caption(ln.text)
        and not _closer_to_another(ln, bbox, others)
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
        # Pagina a due colonne: le righe dell'altra colonna (nessuna
        # sovrapposizione orizzontale con la didascalia) si saltano.
        if ln.bbox.x1 <= first.bbox.x0 or ln.bbox.x0 >= max(first.bbox.x1, cursor.x1):
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
