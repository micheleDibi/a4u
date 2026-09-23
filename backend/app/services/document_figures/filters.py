"""Filtri deterministici sulle figure rilevate (motivi in `FIGURE_REJECT_REASONS`).

Loghi e decorazioni ripetuti, icone, filetti, testate e piè di pagina,
pagine scansionate intere, ritagli vuoti e rilevazioni poco affidabili non
diventano figure di fonte. Le soglie sono in punti PDF.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.services.document_figures.geometry import BBox
from app.services.document_figures.phash import hamming

MIN_SIDE_PT = 48.0
MIN_AREA_FRACTION = 0.012
MAX_ASPECT = 8.0
HEADER_FOOTER_BAND = 0.08
HEADER_FOOTER_MAX_HEIGHT = 0.12
FULL_PAGE_FRACTION = 0.9
MIN_CONFIDENCE = 0.4
REPEATED_MIN_PAGES = 3
SAME_IMAGE_MAX_DISTANCE = 6


def geometry_reject_reason(
    bbox: BBox,
    *,
    page_w: float,
    page_h: float,
    is_vector: bool,
    confidence: float | None,
) -> str | None:
    if confidence is not None and confidence < MIN_CONFIDENCE:
        return "detector_noise"
    if bbox.width < MIN_SIDE_PT or bbox.height < MIN_SIDE_PT:
        return "too_small"
    if bbox.area < MIN_AREA_FRACTION * page_w * page_h:
        return "too_small"
    aspect = max(bbox.width / bbox.height, bbox.height / bbox.width)
    if aspect > MAX_ASPECT:
        return "bad_aspect"
    in_header = bbox.bottom <= HEADER_FOOTER_BAND * page_h
    in_footer = bbox.top >= (1 - HEADER_FOOTER_BAND) * page_h
    if (in_header or in_footer) and bbox.height <= HEADER_FOOTER_MAX_HEIGHT * page_h:
        return "header_footer"
    if not is_vector and bbox.area >= FULL_PAGE_FRACTION * page_w * page_h:
        # Pagina scansionata intera: non è una figura del documento.
        return "too_large"
    return None


@dataclass
class HashedFigure:
    key: str
    page: int | None
    phash: str


def repeated_and_duplicates(
    figures: list[HashedFigure],
) -> tuple[set[str], dict[str, str]]:
    """Dentro un documento: immagini presenti (quasi uguali) su almeno
    `REPEATED_MIN_PAGES` pagine → ripetute (loghi, decorazioni); le altre
    copie di una stessa immagine → duplicate della prima.

    Ritorna (chiavi ripetute, {chiave duplicata: chiave originale}).
    """
    groups: list[list[HashedFigure]] = []
    for fig in figures:
        for group in groups:
            if hamming(group[0].phash, fig.phash) <= SAME_IMAGE_MAX_DISTANCE:
                group.append(fig)
                break
        else:
            groups.append([fig])
    repeated: set[str] = set()
    duplicates: dict[str, str] = {}
    for group in groups:
        pages: dict[int | None, int] = defaultdict(int)
        for fig in group:
            pages[fig.page] += 1
        if len(pages) >= REPEATED_MIN_PAGES:
            repeated.update(fig.key for fig in group)
            continue
        for fig in group[1:]:
            duplicates[fig.key] = group[0].key
    return repeated, duplicates
