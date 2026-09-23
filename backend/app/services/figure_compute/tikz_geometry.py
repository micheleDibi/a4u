"""Oracolo geometrico delle figure `tikz` (WP6): difetti leggibili dal PDF.

Il PDF di XeLaTeX (una pagina, ritagliata sulla figura) si legge con
pdfplumber: parole con il loro corpo, rettangoli, linee e curve. Difetti
deterministici, senza Vision:

- `labels_overlap`: due glifi uno sopra l'altro (confronto per carattere:
  pdfplumber fonde in una parola sola due testi sovrapposti; pedici e
  crenatura non contano);
- `text_outside_owner`: una parola attraversa il bordo di un riquadro
  (rettangolo o forma chiusa) invece di starci dentro o fuori;
- `edge_crosses_label`: un segmento attraversa una parola (non vale dentro
  un `axis`, dove assi e curve incrociano le etichette per costruzione);
- `content_outside_page`: glifi o tratti fuori dalla pagina (figura
  tagliata);
- `text_small`: il corpo minimo del testo (pedici e apici esclusi), alla
  scala con cui la figura entra nella pagina della dispensa (larghezza
  utile 168 mm), scende sotto 7 pt.

In generazione i difetti bloccano la figura (fix AI); nell'anteprima e nel
PATCH sono avvisi. `font_px_min` va nelle metriche della resa
(`figure_scale.SvgMetrics`, 1 pt = 4/3 px).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

LESSON_WIDTH_MM = 168.0
MIN_TEXT_PT = 7.0
CHAR_OVERLAP = 0.35
BORDER_CROSS_RATIO = 0.25
BORDER_TOLERANCE_PT = 0.6
SCRIPT_RATIO = 0.8
AXIS_ALIGNED_FILL = 0.85
MAX_CHARS = 2_000
_PT_PER_MM = 72 / 25.4


@dataclass(frozen=True)
class TikzGeometry:
    defects: tuple[str, ...]
    min_font_pt: float | None
    width_pt: float
    height_pt: float

    @property
    def font_px_min(self) -> float | None:
        return self.min_font_pt * 4 / 3 if self.min_font_pt is not None else None


Box = tuple[float, float, float, float]


def _area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: Box, b: Box) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _inside(inner: Box, outer: Box, tol: float = BORDER_TOLERANCE_PT) -> bool:
    return (
        inner[0] >= outer[0] - tol
        and inner[1] >= outer[1] - tol
        and inner[2] <= outer[2] + tol
        and inner[3] <= outer[3] + tol
    )


def _crosses_border(word: Box, shape: Box) -> bool:
    """La parola sta in parte dentro e in parte fuori dal riquadro (non solo
    lo sfiora, come le etichette accanto ai componenti di un circuito)."""
    overlap = _intersection(word, shape)
    return overlap > BORDER_CROSS_RATIO * _area(word) and not _inside(word, shape)


def _segment_hits_box(p: tuple[float, float], q: tuple[float, float], box: Box) -> bool:
    """Il segmento attraversa l'interno del riquadro (ridotto di 1 pt)."""
    x0, y0, x1, y1 = box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1
    if x0 >= x1 or y0 >= y1:
        return False
    # Liang-Barsky.
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for edge_p, edge_q in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if edge_p == 0:
            if edge_q < 0:
                return False
            continue
        t = edge_q / edge_p
        if edge_p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _box(obj: Any) -> Box:
    return (float(obj["x0"]), float(obj["top"]), float(obj["x1"]), float(obj["bottom"]))


def _words(page: Any) -> list[tuple[str, Box]]:
    words = page.extract_words(keep_blank_chars=False, x_tolerance=1.5)
    return [
        (str(word["text"]).strip(), _box(word))
        for word in words
        if str(word.get("text") or "").strip()
    ]


def _chars(page: Any) -> list[tuple[str, Box, float]]:
    out = []
    for char in page.chars:
        text = str(char.get("text") or "")
        if not text.strip():
            continue
        box = (float(char["x0"]), float(char["top"]), float(char["x1"]), float(char["bottom"]))
        out.append((text, box, float(char.get("size") or 0.0)))
    return out[:MAX_CHARS]


def _chars_overlap(a: Box, b: Box) -> bool:
    """Due glifi uno sopra l'altro (non solo vicini: pedici, crenatura)."""
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oy = min(a[3], b[3]) - max(a[1], b[1])
    wa, wb = a[2] - a[0], b[2] - b[0]
    ha, hb = a[3] - a[1], b[3] - b[1]
    return ox > CHAR_OVERLAP * min(wa, wb) and oy > CHAR_OVERLAP * min(ha, hb)


def _fill_ratio(points: list[Any]) -> float:
    """Area del poligono sull'area del suo ingombro (1 = rettangolo dritto)."""
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    bbox = (max(xs) - min(xs)) * (max(ys) - min(ys))
    if bbox <= 0:
        return 0.0
    area = 0.5 * abs(
        sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in pairwise(zip(xs, ys, strict=True)))
    )
    return area / bbox


def _shapes(page: Any) -> list[Box]:
    shapes: list[Box] = []
    for rect in page.rects:
        box = (float(rect["x0"]), float(rect["top"]), float(rect["x1"]), float(rect["bottom"]))
        if _area(box) > 20:
            shapes.append(box)
    for curve in page.curves:
        points = curve.get("pts") or []
        closed = len(points) > 3 and points[0] == points[-1]
        # Solo riquadri allineati agli assi (anche con angoli arrotondati):
        # l'ingombro di una forma ruotata (resistore in diagonale) coprirebbe
        # le etichette accanto.
        if closed and _fill_ratio(points) >= AXIS_ALIGNED_FILL:
            box = (
                float(curve["x0"]),
                float(curve["top"]),
                float(curve["x1"]),
                float(curve["bottom"]),
            )
            if _area(box) > 20:
                shapes.append(box)
    return shapes


def _segments(page: Any) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments = []
    for line in page.lines:
        segments.append(
            ((float(line["x0"]), float(line["top"])), (float(line["x1"]), float(line["bottom"])))
        )
    for curve in page.curves:
        points = curve.get("pts") or []
        if len(points) >= 2 and points[0] != points[-1]:
            for a, b in pairwise(points):
                segments.append(((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))))
    return segments


def analyze(pdf: bytes, *, has_axis: bool) -> TikzGeometry:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        page = document.pages[0]
        width, height = float(page.width), float(page.height)
        words = _words(page)
        chars = _chars(page)
        shapes = _shapes(page)
        segments = [] if has_axis else _segments(page)
        drawn = [*page.lines, *page.rects, *page.curves]
    defects: list[str] = []
    for i, (text_a, box_a, size_a) in enumerate(chars):
        for text_b, box_b, size_b in chars[i + 1 :]:
            # Un pedice o un apice accanto alla sua base non è una
            # sovrapposizione: si confrontano solo glifi di corpo simile.
            similar = min(size_a, size_b) >= SCRIPT_RATIO * max(size_a, size_b)
            if similar and _chars_overlap(box_a, box_b):
                defects.append(f"labels_overlap: «{text_a}» su «{text_b}»")
                break
    for text, box in words:
        if any(_crosses_border(box, shape) for shape in shapes):
            defects.append(f"text_outside_owner: «{text}»")
        if any(_segment_hits_box(p, q, box) for p, q in segments):
            defects.append(f"edge_crosses_label: «{text}»")
    tol = BORDER_TOLERANCE_PT
    outside = [
        _t
        for _t, box, _s in chars
        if box[0] < -tol or box[1] < -tol or box[2] > width + tol or box[3] > height + tol
    ] + [
        "tratto"
        for obj in drawn
        if float(obj["x0"]) < -tol
        or float(obj["top"]) < -tol
        or float(obj["x1"]) > width + tol
        or float(obj["bottom"]) > height + tol
    ]
    if outside:
        defects.append(f"content_outside_page: {len(outside)} elementi tagliati")
    sizes = sorted(size for _t, _b, size in chars if size > 0)
    min_font = None
    if sizes:
        median = sizes[len(sizes) // 2]
        # Pedici e apici (≈ 70% del corpo) non sono il corpo del testo.
        body = [size for size in sizes if size >= SCRIPT_RATIO * median]
        min_font = min(body) if body else sizes[0]
        width_mm = width / _PT_PER_MM
        effective = min_font * min(1.0, LESSON_WIDTH_MM / width_mm) if width_mm else min_font
        if effective < MIN_TEXT_PT:
            defects.append(f"text_small: {effective:.1f} pt nella dispensa")
    return TikzGeometry(
        defects=tuple(dict.fromkeys(defects)),
        min_font_pt=min_font,
        width_pt=width,
        height_pt=height,
    )
