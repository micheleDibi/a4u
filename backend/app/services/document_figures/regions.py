"""Contenuto di una pagina PDF per il ritaglio v2 (pypdfium2).

Immagini raster e segni visibili (testo, tracciati, sfumature) nello
spazio di visualizzazione della pagina: CropBox, origine in alto a
sinistra, rotazione della pagina applicata (lo stesso spazio dei bbox del
rilevatore e di `PdfPage.render`). Le matrici si compongono attraverso i
Form XObject annidati.

Per ogni raster: rettangolo sulla pagina, pixel lungo gli assi della
pagina, ppi nativi per asse e se il render può allinearsi alla sua griglia
(assi multipli di 90°). Il testo invisibile (modo 3, lo strato OCR delle
scansioni) e i tracciati senza tratto né riempimento non sono segni.

Modulo puro: niente settings, niente I/O; gira nel sottoprocesso.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services.document_figures.geometry import BBox

# Tetto di oggetti letti per pagina: le pagine vettoriali patologiche
# (decine di migliaia di tracciati) non devono bloccare il figlio. Oltre il
# tetto la pagina conta come «con segni» (render misto, mai sbagliato).
MAX_OBJECTS = 20_000
# Oggetti più piccoli di così (pt) non contano come segni (puntini, filetti).
_MIN_MARK_PT = 0.5
# Assi «allineati» se le componenti fuori asse sono sotto questa soglia (pt).
_AXIS_EPS_PT = 1e-3
# Filtri con perdita dell'oggetto immagine.
_LOSSY_FILTERS = frozenset({"DCTDecode", "JPXDecode", "DCT", "JPX"})

Mapper = Callable[[float, float], tuple[float, float]]


@dataclass(frozen=True)
class RasterRegion:
    """Un'immagine raster come appare sulla pagina."""

    rect: BBox
    # Ordine di disegno sulla pagina (gli oggetti dopo coprono quelli prima).
    order: int
    # Pixel lungo X e lungo Y della pagina (scambiati se l'immagine è
    # ruotata di 90°).
    px_x: int
    px_y: int
    ppi_x: float
    ppi_y: float
    # Render allineabile alla griglia: assi dell'immagine paralleli a quelli
    # della pagina (rotazioni multiple di 90°, ribaltamenti).
    axis_aligned: bool
    lossy: bool

    @property
    def ppi(self) -> float:
        return min(self.ppi_x, self.ppi_y)


@dataclass(frozen=True)
class Mark:
    """Testo, tracciato o sfumatura visibile."""

    rect: BBox
    order: int


@dataclass(frozen=True)
class PageRegions:
    rasters: tuple[RasterRegion, ...]
    marks: tuple[Mark, ...]
    # Pagina con più oggetti del tetto: segni non elencati tutti.
    truncated: bool = False


def display_mapper(page: Any) -> Mapper:
    """Spazio utente del PDF → spazio di visualizzazione (pt, origine in
    alto a sinistra della CropBox, rotazione della pagina applicata): la
    stessa trasformazione che PDFium compone prima della matrice di
    `FPDF_RenderPageBitmapWithMatrix`."""
    left, bottom, right, top = (float(v) for v in page.get_cropbox())
    rotation = int(page.get_rotation() or 0) % 360
    if rotation == 90:
        return lambda x, y: (y - bottom, x - left)
    if rotation == 180:
        return lambda x, y: (right - x, y - bottom)
    if rotation == 270:
        return lambda x, y: (top - y, right - x)
    return lambda x, y: (x - left, top - y)


def composed_matrix(obj: Any) -> tuple[float, float, float, float, float, float]:
    """Matrice dell'oggetto composta con quelle dei Form che lo contengono."""
    matrix = obj.get_matrix()
    container = getattr(obj, "container", None)
    while container is not None:
        matrix = matrix.multiply(container.get_matrix())
        container = getattr(container, "container", None)
    a, b, c, d, e, f = matrix.get()
    return float(a), float(b), float(c), float(d), float(e), float(f)


def _container_points(obj: Any, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Punti nello spazio del Form che contiene `obj` → spazio della pagina."""
    container = getattr(obj, "container", None)
    while container is not None:
        a, b, c, d, e, f = container.get_matrix().get()
        points = [(a * x + c * y + e, b * x + d * y + f) for x, y in points]
        container = getattr(container, "container", None)
    return points


def _rect(points: list[tuple[float, float]], mapper: Mapper) -> BBox:
    shown = [mapper(x, y) for x, y in points]
    xs = [p[0] for p in shown]
    ys = [p[1] for p in shown]
    return BBox(min(xs), min(ys), max(xs), max(ys))


def raster_region(obj: Any, mapper: Mapper, order: int = 0) -> RasterRegion | None:
    """Regione di un oggetto immagine; None se degenere o illeggibile."""
    a, b, c, d, e, f = composed_matrix(obj)
    try:
        width, height = obj.get_px_size()
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    p00 = mapper(e, f)
    p10 = mapper(a + e, b + f)
    p01 = mapper(c + e, d + f)
    ux, uy = p10[0] - p00[0], p10[1] - p00[1]
    vx, vy = p01[0] - p00[0], p01[1] - p00[1]
    rect = BBox(
        min(p00[0], p10[0], p01[0], p10[0] + vx),
        min(p00[1], p10[1], p01[1], p10[1] + vy),
        max(p00[0], p10[0], p01[0], p10[0] + vx),
        max(p00[1], p10[1], p01[1], p10[1] + vy),
    )
    if rect.width < _MIN_MARK_PT or rect.height < _MIN_MARK_PT:
        return None
    if abs(uy) < _AXIS_EPS_PT and abs(vx) < _AXIS_EPS_PT:
        px_x, px_y, aligned = int(width), int(height), True
    elif abs(ux) < _AXIS_EPS_PT and abs(vy) < _AXIS_EPS_PT:
        px_x, px_y, aligned = int(height), int(width), True
    else:
        # Rotazione qualsiasi o inclinazione: ppi dalle lunghezze dei lati.
        u_len = (ux * ux + uy * uy) ** 0.5
        v_len = (vx * vx + vy * vy) ** 0.5
        ppi_u = width / (u_len / 72.0) if u_len else 0.0
        ppi_v = height / (v_len / 72.0) if v_len else 0.0
        ppi = min(ppi_u, ppi_v)
        return RasterRegion(
            rect=rect,
            order=order,
            px_x=int(width),
            px_y=int(height),
            ppi_x=ppi,
            ppi_y=ppi,
            axis_aligned=False,
            lossy=_is_lossy(obj),
        )
    return RasterRegion(
        rect=rect,
        order=order,
        px_x=px_x,
        px_y=px_y,
        ppi_x=px_x / (rect.width / 72.0),
        ppi_y=px_y / (rect.height / 72.0),
        axis_aligned=aligned,
        lossy=_is_lossy(obj),
    )


def _is_lossy(obj: Any) -> bool:
    try:
        filters = obj.get_filters()
    except Exception:
        return False
    return any(str(f) in _LOSSY_FILTERS for f in filters or ())


def _visible_mark(obj: Any, raw: Any) -> bool:
    kind = obj.type
    if kind == raw.FPDF_PAGEOBJ_TEXT:
        try:
            mode = raw.FPDFTextObj_GetTextRenderMode(obj.raw)
        except Exception:
            return True
        return int(mode) != int(raw.FPDF_TEXTRENDERMODE_INVISIBLE)
    if kind == raw.FPDF_PAGEOBJ_PATH:
        fill = ctypes.c_int(0)
        stroke = ctypes.c_int(0)
        try:
            ok = raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
        except Exception:
            return True
        if not ok:
            return True
        if stroke.value:
            return True
        # Solo riempimento: un fondo bianco (o trasparente) su pagina bianca
        # non si vede; il testo che ci sta sopra conta comunque.
        return bool(fill.value) and not _white_fill(obj, raw)
    return True


def _white_fill(obj: Any, raw: Any) -> bool:
    r, g, b, a = (ctypes.c_uint(0) for _ in range(4))
    try:
        ok = raw.FPDFPageObj_GetFillColor(
            obj.raw, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a)
        )
    except Exception:
        return False
    if not ok:
        return False
    return a.value == 0 or min(r.value, g.value, b.value) >= 250


def page_regions(page: Any, *, max_objects: int = MAX_OBJECTS) -> PageRegions:
    """Raster e segni visibili della pagina, nello spazio di visualizzazione."""
    import pypdfium2.raw as raw

    mapper = display_mapper(page)
    wanted = [
        raw.FPDF_PAGEOBJ_IMAGE,
        raw.FPDF_PAGEOBJ_TEXT,
        raw.FPDF_PAGEOBJ_PATH,
        raw.FPDF_PAGEOBJ_SHADING,
    ]
    rasters: list[RasterRegion] = []
    marks: list[Mark] = []
    truncated = False
    for count, obj in enumerate(page.get_objects(filter=wanted, max_depth=15)):
        if count >= max_objects:
            truncated = True
            break
        if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
            region = raster_region(obj, mapper, count)
            if region is not None:
                rasters.append(region)
            continue
        if not _visible_mark(obj, raw):
            continue
        try:
            left, bottom, right, top = obj.get_bounds()
        except Exception:
            continue
        points = _container_points(
            obj, [(left, bottom), (right, bottom), (left, top), (right, top)]
        )
        box = _rect(points, mapper)
        if box.width >= _MIN_MARK_PT or box.height >= _MIN_MARK_PT:
            marks.append(Mark(rect=box, order=count))
    return PageRegions(rasters=tuple(rasters), marks=tuple(marks), truncated=truncated)


def overlay_marks(box: BBox, regions: PageRegions) -> list[Mark]:
    """Segni visibili nel bbox che non stanno SOTTO un raster: un segno
    disegnato prima di un'immagine che lo contiene tutto (lo sfondo bianco
    dietro una foto) non si vede."""
    out: list[Mark] = []
    for mark in regions.marks:
        if mark.rect.intersection_area(box) <= 0:
            continue
        hidden = any(
            raster.order > mark.order and raster.rect.coverage_of(mark.rect) >= 0.999
            for raster in regions.rasters
        )
        if not hidden:
            out.append(mark)
    return out


def union_coverage(box: BBox, rects: list[BBox], *, grid: int = 48) -> float:
    """Quota di `box` coperta dall'UNIONE dei rettangoli (campionamento su
    una griglia): la somma delle intersezioni contava due volte le
    sovrapposizioni (discrepanza D7)."""
    if box.area <= 0 or not rects:
        return 0.0
    inside = [r for r in rects if r.intersection_area(box) > 0]
    if not inside:
        return 0.0
    hits = 0
    for i in range(grid):
        x = box.x0 + (i + 0.5) * box.width / grid
        for j in range(grid):
            y = box.top + (j + 0.5) * box.height / grid
            if any(r.x0 <= x <= r.x1 and r.top <= y <= r.bottom for r in inside):
                hits += 1
    return hits / float(grid * grid)
