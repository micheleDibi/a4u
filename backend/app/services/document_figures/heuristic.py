"""Motore euristico di rilevazione (pdfplumber): raster e gruppi vettoriali.

Senza modelli: serve alla CI e come riserva attivabile in produzione solo
per decisione al cancello (FIGURE_EXTRACTION_ENGINE=heuristic). Più debole
di Docling su impaginazioni complesse (colonne, figure composte, tabelle
senza didascalia).
"""

from __future__ import annotations

from typing import Any

from app.services.document_figures.captions import is_figure_caption, is_table_caption
from app.services.document_figures.detection import Detection
from app.services.document_figures.geometry import BBox
from app.services.document_figures.page_text import Line, caption_near, page_lines

CLUSTER_GAP_PT = 14.0
MIN_PRIMITIVES = 3
MIN_CLUSTER_SIDE_PT = 30.0
LABEL_GAP_PT = 8.0
LABEL_MAX_CHARS = 40
MERGE_GAP_PT = 6.0


def _is_white(color: Any) -> bool:
    if color is None:
        return True
    if isinstance(color, int | float):
        return float(color) >= 0.98
    values = [float(c) for c in color if isinstance(c, int | float)]
    if len(values) == 4:  # CMYK
        return all(v <= 0.02 for v in values)
    return bool(values) and all(v >= 0.98 for v in values)


def _visible_primitives(page: Any) -> list[tuple[BBox, bool]]:
    """Rettangoli dei tracciati visibili: (bbox, è orizzontale/verticale)."""
    width, height = float(page.width), float(page.height)
    out: list[tuple[BBox, bool]] = []
    for obj in [*page.curves, *page.lines, *page.rects]:
        box = BBox(float(obj["x0"]), float(obj["top"]), float(obj["x1"]), float(obj["bottom"]))
        if box.width >= 0.95 * width and box.height >= 0.95 * height:
            continue  # sfondo della pagina
        stroked = bool(obj.get("stroke", True))
        filled = bool(obj.get("fill", False))
        if not stroked and (not filled or _is_white(obj.get("non_stroking_color"))):
            continue  # riempimento bianco o invisibile
        if box.height < 2 and box.width > 0.7 * width:
            continue  # filetto di testata o piè di pagina
        straight = obj.get("object_type") != "curve" and (box.width < 1.5 or box.height < 1.5)
        out.append((box, straight))
    return out


def _cluster(items: list[tuple[BBox, bool]]) -> list[tuple[BBox, int, bool]]:
    """Gruppi di tracciati vicini: (bbox, numero di tracciati, solo rette)."""
    clusters: list[list[tuple[BBox, bool]]] = []
    for item in items:
        near = [c for c in clusters if any(item[0].gap_to(o[0]) <= CLUSTER_GAP_PT for o in c)]
        merged = [item]
        for c in near:
            merged.extend(c)
            clusters.remove(c)
        clusters.append(merged)
    out = []
    for c in clusters:
        box = c[0][0]
        for other, _ in c[1:]:
            box = box.union(other)
        out.append((box, len(c), all(straight for _, straight in c)))
    return out


def _grow_with_labels(box: BBox, lines: list[Line]) -> BBox:
    """Include le etichette brevi attaccate al disegno (testi dei blocchi,
    tacche e titoli degli assi), non i paragrafi né le didascalie."""
    for _ in range(5):
        grown = box
        for ln in lines:
            if (
                len(ln.text) <= LABEL_MAX_CHARS
                and not is_figure_caption(ln.text)
                and not is_table_caption(ln.text)
                and ln.bbox.gap_to(grown) <= LABEL_GAP_PT
            ):
                grown = grown.union(ln.bbox)
        if grown == box:
            break
        box = grown
    return box


def _table_caption_near(box: BBox, lines: list[Line]) -> bool:
    return any(
        is_table_caption(ln.text)
        and ln.bbox.x1 > box.x0
        and ln.bbox.x0 < box.x1
        and min(abs(ln.bbox.bottom - box.top), abs(ln.bbox.top - box.bottom)) <= 45
        for ln in lines
    )


def detect_page(page: Any, page_no: int) -> list[Detection]:
    width, height = float(page.width), float(page.height)
    lines = page_lines(page.extract_words())
    regions: list[tuple[BBox, bool, float | None]] = []
    for image in page.images:
        box = BBox(
            float(image["x0"]), float(image["top"]), float(image["x1"]), float(image["bottom"])
        ).clamp(width, height)
        if box.area <= 0:
            continue
        src = image.get("srcsize") or (0, 0)
        ppi = float(src[0]) / (box.width / 72.0) if src and src[0] and box.width > 0 else None
        regions.append((box, False, ppi))
    for box, count, only_straight in _cluster(_visible_primitives(page)):
        if count < MIN_PRIMITIVES or min(box.width, box.height) < MIN_CLUSTER_SIDE_PT:
            continue
        box = _grow_with_labels(box, lines)
        if only_straight and _table_caption_near(box, lines):
            continue  # tabella disegnata a filetti, non una figura
        regions.append((box, True, None))

    # Raster e disegni che si toccano sono la stessa figura (es. foto annotata).
    merged: list[tuple[BBox, bool, float | None]] = []
    for box, is_vector, ppi in sorted(regions, key=lambda r: (r[0].top, r[0].x0)):
        for i, (other, other_vector, other_ppi) in enumerate(merged):
            if box.gap_to(other) <= MERGE_GAP_PT and (
                box.intersection_area(other) > 0 or (is_vector and other_vector)
            ):
                merged[i] = (
                    other.union(box),
                    other_vector and is_vector,
                    other_ppi or ppi,
                )
                break
        else:
            merged.append((box, is_vector, ppi))

    detections = []
    for box, is_vector, ppi in merged:
        detections.append(
            Detection(
                page=page_no,
                bbox=box,
                page_w=width,
                page_h=height,
                is_vector=is_vector,
                detector_class="vector" if is_vector else "image",
                caption=caption_near(lines, box),
                native_ppi=ppi,
            )
        )
    return detections
