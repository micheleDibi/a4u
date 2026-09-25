"""Risoluzione effettiva delle figure di fonte (doc 18 §22, WP2).

Modulo puro (niente DB né `get_settings()`): classe di risoluzione e
larghezza di resa in dispensa, nel PDF delle slide e nei frame del video.
Le stesse funzioni servono al render, al catalogo della Fase 3, al
selettore e al DTO dell'editor, così la classe mostrata al docente è
quella con cui la figura esce davvero.

Metrica: ppi effettivi = pixel d'informazione / larghezza stampata (in
pollici).

- Pixel d'informazione: `width · min(1, native_ppi / dpi)`. Un ritaglio v1
  reso a 150 dpi da un raster a 76 ppi ha il doppio dei pixel ma non
  l'informazione; per i vettoriali contano tutti i pixel resi.
- Misura naturale N: la larghezza della figura nell'originale,
  normalizzata alla pagina (`min(1, 612 / page_w)`: una slide 16:9 larga
  960 pt non vale come un foglio da 34 cm). Senza N: 90 mm per convenzione
  (file della letteratura non ritagliati da un PDF) o per stima (righe
  storiche senza dati: mai `unusable`, perché mancano i dati per dirlo).
- Classe: misurata alla larghezza di riferimento R = min(colonna, N), con
  colonna di riferimento 170 mm. Misurarla sulla larghezza stampata
  sarebbe circolare: la stampa si adatta ai pixel, quindi sarebbe sempre
  «good» (discrepanza D13 del piano).

Classi (costanti con test): good ≥ 200 ppi, acceptable ≥ 150, low ≥ 100,
unusable < 100.

Regola di stampa in dispensa (colonna C):

- good: `min(C, 1,25·N, px/200)`;
- acceptable: R (misura naturale, 150-199 ppi: deviazione 2 dichiarata,
  per la leggibilità delle etichette);
- low: `max(min(C, 0,8·N), px/150)`, mai oltre R;
- unusable già collocata (U1): `px · 0,254 mm`, cioè 100 ppi.

Invariante: mai sotto 100 ppi, in qualunque classe e con qualunque base.

Slide e frame: `W = min(box_w, box_h · w/h, 1,25 · px / 6,667)`, cioè al
più 1,25 pixel del frame (1980 px su 297 mm) per pixel d'informazione, e
almeno ~135 ppi nel PDF delle slide.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

# --- Soglie (ppi effettivi alla larghezza di riferimento) ---------------------
GOOD_PPI = 200.0
ACCEPTABLE_PPI = 150.0
LOW_PPI = 100.0

RESOLUTION_CLASSES: tuple[str, ...] = ("good", "acceptable", "low", "unusable")
_RANK = {name: rank for rank, name in enumerate(RESOLUTION_CLASSES)}

# --- Geometria ------------------------------------------------------------------
# Colonna di riferimento della classe (A4 con margini di 20 mm): la classe
# di selezione non dipende dal template dell'organizzazione.
REFERENCE_COLUMN_MM = 170.0
# Pagina di riferimento per la normalizzazione della misura naturale
# (Letter; A4 è 595 pt e resta invariata).
REFERENCE_PAGE_W_PT = 612.0
# Ingrandimento massimo oltre la misura naturale (solo per le good).
NATURAL_WIDTH_SCALE = 1.25
# Pavimento delle low: non si rimpiccioliscono sotto 0,8 volte la misura
# naturale solo per raggiungere 150 ppi.
LOW_FLOOR_SCALE = 0.8
# Misura naturale convenzionale senza dati (Commons raster, righe storiche).
CONVENTIONAL_NATURAL_MM = 90.0
# Frame del video: 1980 px su 297 mm (`lesson_slides_video_render_service`).
FRAME_PX_PER_MM = 1980.0 / 297.0
# Ingrandimento massimo nei frame (pixel del frame per pixel d'informazione).
SLIDE_MAX_UPSCALE = 1.25
# Riquadro di riferimento della classe delle slide: pagina di sola figura
# con titolo su una riga (`slide_geometry`, 255 × 86,6 mm).
SLIDE_REFERENCE_BOX_MM = (255.0, 86.6)

MM_PER_INCH = 25.4

# Basi della misura naturale, dalla più affidabile.
NATURAL_BASES: tuple[str, ...] = ("measured", "bbox", "render", "convention", "estimate")


@dataclass(frozen=True)
class ResolutionInputs:
    """Dati della riga `course_document_figure` che servono alla classe."""

    width_px: int | None
    height_px: int | None
    # dpi del RENDER del ritaglio (None per Office, Commons, righe storiche
    # della letteratura).
    dpi: float | None
    native_ppi: float | None
    natural_width_mm: float | None
    is_vector: bool | None
    # Dal bbox (righe v1 senza `natural_width_mm`).
    bbox_w_pt: float | None = None
    page_w_pt: float | None = None
    source_kind: str = "uploaded"

    @classmethod
    def from_figure(cls, fig: Any) -> ResolutionInputs:
        bbox = fig.bbox if isinstance(getattr(fig, "bbox", None), dict) else {}
        bbox_w: float | None = None
        page_w: float | None = None
        try:
            left, right = float(bbox["l"]), float(bbox["r"])
            bbox_w = right - left if right > left else None
            page_w = float(bbox["page_w"]) if float(bbox["page_w"]) > 0 else None
        except (KeyError, TypeError, ValueError):
            bbox_w = page_w = None
        return cls(
            width_px=getattr(fig, "width", None),
            height_px=getattr(fig, "height", None),
            dpi=getattr(fig, "dpi", None),
            native_ppi=getattr(fig, "native_ppi", None),
            natural_width_mm=getattr(fig, "natural_width_mm", None),
            is_vector=getattr(fig, "is_vector", None),
            bbox_w_pt=bbox_w,
            page_w_pt=page_w,
            source_kind=str(getattr(fig, "source_kind", None) or "uploaded"),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, source_kind: str) -> ResolutionInputs:
        """Dagli stessi campi della riga in un dizionario (eventi del figlio,
        candidate della letteratura prima del salvataggio)."""
        return cls.from_figure(SimpleNamespace(**{**dict(data), "source_kind": source_kind}))


@dataclass(frozen=True)
class Assessment:
    """Classe alla larghezza di riferimento R = min(colonna, N)."""

    resolution_class: str
    ppi_at_reference: float
    reference_mm: float
    natural_mm: float
    natural_basis: str
    information_px: float


@dataclass(frozen=True)
class PrintPlan:
    width_mm: float
    ppi: float
    resolution_class: str
    natural_basis: str


@dataclass(frozen=True)
class SlidePlan:
    width_mm: float
    # Pixel del frame per pixel d'informazione (≤ SLIDE_MAX_UPSCALE).
    upscale: float
    resolution_class: str


def _floor_mm(width: float) -> float:
    """Arrotondamento al decimo di mm per DIFETTO: la larghezza scritta nel
    CSS non deve portare i ppi sotto la soglia né l'ingrandimento oltre il
    tetto."""
    return max(1.0, math.floor(width * 10.0 + 1e-9) / 10.0)


def page_factor(page_w_pt: float | None) -> float:
    """`min(1, 612 / page_w)`: 1 per A4 e Letter, < 1 per le pagine larghe."""
    if not page_w_pt or page_w_pt <= 0:
        return 1.0
    return min(1.0, REFERENCE_PAGE_W_PT / float(page_w_pt))


def natural_width_from_bbox(bbox_w_pt: float, page_w_pt: float | None) -> float:
    """Misura naturale (mm) di un bbox in punti, normalizzata alla pagina."""
    return bbox_w_pt / 72.0 * MM_PER_INCH * page_factor(page_w_pt)


def information_width_px(inputs: ResolutionInputs) -> float | None:
    if not inputs.width_px or inputs.width_px <= 0:
        return None
    width = float(inputs.width_px)
    if inputs.is_vector or not inputs.native_ppi or not inputs.dpi or inputs.dpi <= 0:
        return width
    return width * min(1.0, float(inputs.native_ppi) / float(inputs.dpi))


def natural_width(inputs: ResolutionInputs) -> tuple[float, str]:
    """(misura naturale in mm, base). Mai None: senza dati, 90 mm."""
    if inputs.natural_width_mm and inputs.natural_width_mm > 0:
        return float(inputs.natural_width_mm), "measured"
    if inputs.bbox_w_pt and inputs.bbox_w_pt > 0:
        return natural_width_from_bbox(inputs.bbox_w_pt, inputs.page_w_pt), "bbox"
    if inputs.width_px and inputs.dpi and inputs.dpi > 0:
        natural = inputs.width_px / float(inputs.dpi) * MM_PER_INCH
        return natural * page_factor(inputs.page_w_pt), "render"
    if inputs.source_kind == "wikimedia":
        return CONVENTIONAL_NATURAL_MM, "convention"
    return CONVENTIONAL_NATURAL_MM, "estimate"


def class_for_ppi(ppi: float) -> str:
    if ppi >= GOOD_PPI:
        return "good"
    if ppi >= ACCEPTABLE_PPI:
        return "acceptable"
    if ppi >= LOW_PPI:
        return "low"
    return "unusable"


def class_rank(resolution_class: str | None) -> int:
    """0 = good … 3 = unusable (ordinamento: prima le migliori)."""
    return _RANK.get(str(resolution_class), _RANK["acceptable"])


def assess(
    inputs: ResolutionInputs, *, column_mm: float = REFERENCE_COLUMN_MM
) -> Assessment | None:
    """Classe della figura alla larghezza di riferimento. None senza pixel
    (la figura non si rende comunque)."""
    info = information_width_px(inputs)
    if info is None:
        return None
    natural, basis = natural_width(inputs)
    reference = max(1.0, min(float(column_mm), natural))
    ppi = info / (reference / MM_PER_INCH)
    klass = class_for_ppi(ppi)
    if klass == "unusable" and basis == "estimate":
        # Righe storiche senza dati: la misura è una stima, quindi non basta
        # per escludere la figura (resta la stampa a ≥ 100 ppi).
        klass = "low"
    return Assessment(
        resolution_class=klass,
        ppi_at_reference=ppi,
        reference_mm=reference,
        natural_mm=natural,
        natural_basis=basis,
        information_px=info,
    )


def selection_class(inputs: ResolutionInputs) -> str | None:
    """Classe di selezione (catalogo, selettore, PATCH): quella di stampa
    alla colonna di riferimento. None = ignota (nessun pixel)."""
    assessment = assess(inputs)
    return assessment.resolution_class if assessment is not None else None


def _aspect(inputs: ResolutionInputs) -> float | None:
    if inputs.width_px and inputs.height_px and inputs.width_px > 0 and inputs.height_px > 0:
        return inputs.height_px / inputs.width_px
    return None


def plan_print(
    inputs: ResolutionInputs,
    *,
    column_mm: float = REFERENCE_COLUMN_MM,
    max_height_mm: float | None = None,
) -> PrintPlan | None:
    """Larghezza di stampa in dispensa (mm) e ppi effettivi risultanti."""
    assessment = assess(inputs, column_mm=column_mm)
    if assessment is None:
        return None
    info = assessment.information_px
    column = float(column_mm)
    natural = assessment.natural_mm
    klass = assessment.resolution_class
    if klass == "good":
        width = min(column, NATURAL_WIDTH_SCALE * natural, info / GOOD_PPI * MM_PER_INCH)
        width = max(width, assessment.reference_mm)
    elif klass == "acceptable":
        width = assessment.reference_mm
    elif klass == "low":
        floor = min(column, LOW_FLOOR_SCALE * natural)
        width = min(assessment.reference_mm, max(floor, info / ACCEPTABLE_PPI * MM_PER_INCH))
    else:
        width = info / LOW_PPI * MM_PER_INCH
    # Invariante: mai sotto 100 ppi (e mai oltre la colonna).
    width = min(width, column, info / LOW_PPI * MM_PER_INCH)
    aspect = _aspect(inputs)
    if max_height_mm and max_height_mm > 0 and aspect:
        width = min(width, max_height_mm / aspect)
    width = _floor_mm(width)
    return PrintPlan(
        width_mm=width,
        ppi=info / (width / MM_PER_INCH),
        resolution_class=klass,
        natural_basis=assessment.natural_basis,
    )


def plan_slide(inputs: ResolutionInputs, *, box_w_mm: float, box_h_mm: float) -> SlidePlan | None:
    """Larghezza della figura nel riquadro della slide (PDF e frame)."""
    info = information_width_px(inputs)
    if info is None or box_w_mm <= 0:
        return None
    aspect = _aspect(inputs)
    fill = float(box_w_mm)
    if aspect and box_h_mm > 0:
        fill = min(fill, float(box_h_mm) / aspect)
    cap = SLIDE_MAX_UPSCALE * info / FRAME_PX_PER_MM
    width = _floor_mm(min(fill, cap))
    upscale = width * FRAME_PX_PER_MM / info
    return SlidePlan(
        width_mm=width,
        upscale=upscale,
        resolution_class=slide_class(inputs) or "acceptable",
    )


def slide_class(inputs: ResolutionInputs) -> str | None:
    """Classe nelle slide (solo spareggio e informazione nell'editor):
    ingrandimento necessario per riempire il riquadro di riferimento.
    good ≤ 1,0; acceptable ≤ 1,25; oltre, low (la figura esce più piccola
    del riquadro). Nelle slide non esiste `unusable`."""
    info = information_width_px(inputs)
    if info is None:
        return None
    box_w, box_h = SLIDE_REFERENCE_BOX_MM
    aspect = _aspect(inputs)
    fill = min(box_w, box_h / aspect) if aspect else box_w
    upscale = fill * FRAME_PX_PER_MM / info
    if upscale <= 1.0:
        return "good"
    if upscale <= SLIDE_MAX_UPSCALE:
        return "acceptable"
    return "low"
