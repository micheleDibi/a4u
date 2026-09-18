"""Banda di leggibilità delle figure (D10, D11): larghezza dal corpo del testo.

Modulo leaf (solo libreria standard, nessun import da `app.*`): è importato
da `svg_normalize` e da `mermaid_prerender`, e la sua logica è specchiata
riga per riga da `frontend/src/lib/figureFormats.ts` (`fitFigureWidthMm`),
con parità provata dalla fixture `tests/fixtures/figure_scale_cases.json`.

Contratto delle unità:
- `vb_w`, `vb_h`: viewBox in unità utente (uu);
- `base_font_px`: corpo del testo di contenuto PIÙ PICCOLO dell'SVG in px
  CSS alla dimensione naturale (`font_uu × px_per_unit`, vedi
  `svg_normalize.SvgBox`): per Mermaid è misurato in Chromium accanto
  all'SVG (`mermaid_prerender.MEASURE_SVG_FONT_PX_JS`), per Vega-Lite, DOT e
  `function` è letto dagli attributi (`svg_normalize.svg_base_font_px`);
- `intrinsic_w_px`: larghezza intrinseca della radice in px quando l'SVG ne
  ha una (`<img>` con SVG normalizzato); `None` per gli SVG fluidi
  (`width="100%"`, Mermaid);
- il risultato è una larghezza in mm da mettere come `style="width:Wmm"` sul
  wrapper o sull'elemento, mai dentro l'SVG.

Politica di scala (B4): gli SVG fluidi riempiono il box e vengono ridotti
al tetto della banda; gli `<img>` intrinseci partono da scala 1, crescono
solo fino al fondo della banda, scendono al tetto se sopra; mai oltre il
box (larghezza arrotondata per DIFETTO al centesimo di mm); senza testo →
scala naturale; banda irraggiungibile → larghezza massima del box e
`in_band=False` (voce per il gate editoriale D13).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

MM_PER_PX = 25.4 / 96
PT_PER_PX = 0.75

FigureVariant = Literal["lesson", "slide"]

# Bande di leggibilità in pt per superficie (D11): la dispensa (e il web)
# 8-11 pt, slide e frame video 10-14 pt.
READABILITY_BANDS_PT: dict[str, tuple[float, float]] = {
    "lesson": (8.0, 11.0),
    "slide": (10.0, 14.0),
}
_BAND_EPS = 1e-9

# Fallback per formato quando le metriche non sono risolvibili, derivati dal
# tema e pinnati da `test_figure_scale` (nessuna costante nuova in
# `figure_theme`): Mermaid `themeVariables.fontSize` 14px; Vega-Lite
# `axis.labelFontSize` 11; DOT `edge fontsize=10` pt → 40/3 px; `function`
# `xtick.labelsize` 9 pt → 12 px (tutti i `<text>` di matplotlib sono a 9).
FALLBACK_BASE_FONT_PX: dict[str, float] = {
    "mermaid": 14.0,
    "vegalite": 11.0,
    "dot": 40.0 / 3.0,
    "function": 12.0,
}

MetricsSource = Literal["measured", "parsed", "root_rule", "unresolved", "no_text"]

# Box del contenuto di una figura: `(larghezza, altezza | None)` in mm,
# `None` sull'altezza = nessun vincolo verticale.
FigureBoxMm = tuple[float, float | None]

# Box di RIFERIMENTO della dispensa (D15), usato SOLO per decidere la
# direzione di una catena quando il box vero non è noto: la vista del
# frontend non conosce il template, e deve decidere come deciderà il PDF.
# È il box di `_compute_template_margins_cm` su A4 con margine di 20 mm
# (170 mm di contenuto meno i 2 mm di padding del wrapper Mermaid) e
# l'altezza utile di una pagina intera. Mai una larghezza di resa.
LESSON_REFERENCE_BOX_MM: FigureBoxMm = (168.0, 242.0)


@dataclass(frozen=True)
class SvgMetrics:
    """Metriche del testo di un SVG: `font_px_min` è l'unico valore che entra
    nel fit; mediana e conteggio sono diagnostici; `source` dice da dove
    viene il valore, così un fallback non è mai silenzioso.

    Geometria (D14, `figure_geometry`): `crossings` sono gli incroci arco ×
    arco della figura resa (`None` se non misurati: formato senza archi,
    misura saltata o fallita); `defects` le voci `codice: dettaglio` dei
    difetti di lettura e delle soglie editoriali superate sulla figura resa
    (tupla immutabile: il record vive nella cache condivisa)."""

    font_px_min: float | None
    font_px_median: float | None
    text_count: int
    source: MetricsSource
    crossings: int | None = None
    defects: tuple[str, ...] = ()


@dataclass(frozen=True)
class FigureFit:
    width_mm: float  # floor al centesimo: sempre ≤ box_w e con altezza ≤ box_h
    scale: float  # rispetto a ref_w (vb_w px per i fluidi, intrinsic_w_px per gli <img>)
    text_pt: float  # corpo minimo alla larghezza scelta (0.0 senza testo)
    in_band: bool


@dataclass(frozen=True)
class FigureFitEntry:
    """Voce del `fit_report` di una lezione (input del gate editoriale D13)."""

    asset_id: str
    fmt: str
    variant: str
    width_mm: float
    scale: float
    text_pt: float
    band: tuple[float, float]
    in_band: bool
    font_source: str
    text_count: int
    crossings: int | None = None
    defects: tuple[str, ...] = ()
    # D15: la figura è stata resa con la direzione verticale della catena
    # perché l'orizzontale usciva sotto la banda; `text_pt` è già quello
    # della variante scelta.
    direction_flipped: bool = False


def _half_up(value: float, digits: int) -> float:
    """Arrotondamento half-up (`floor(x·10^n + 0.5)/10^n`), identico nel
    mirror TS; mai `round()` di Python (banker's rounding)."""
    factor = 10.0**digits
    return math.floor(value * factor + 0.5) / factor


def _positive(value: float | None) -> float | None:
    if value is None:
        return None
    return value if math.isfinite(value) and value > 0 else None


def fit_figure_width_mm(
    *,
    vb_w: float,
    vb_h: float,
    base_font_px: float | None,
    box_w_mm: float | None,
    box_h_mm: float | None,
    variant: FigureVariant = "lesson",
    intrinsic_w_px: float | None = None,
) -> FigureFit | None:
    """Larghezza (mm) a cui rendere la figura perché il testo più piccolo
    cada nella banda della superficie, entro il box.

    `None` per input degeneri (viewBox non positivo, box dato ma non
    positivo, `intrinsic_w_px` dato ma non positivo); un box `None` è
    «nessun vincolo» (web: il CSS `min(100%, …)` applica la colonna).
    `base_font_px` assente, non finito o ≤ 0 vale «senza testo».
    `variant` ignota → `ValueError`.
    """
    if variant not in READABILITY_BANDS_PT:
        raise ValueError(f"variant sconosciuta: {variant!r}")
    lo, hi = READABILITY_BANDS_PT[variant]
    if _positive(vb_w) is None or _positive(vb_h) is None:
        return None
    if box_w_mm is not None and _positive(box_w_mm) is None:
        return None
    if box_h_mm is not None and _positive(box_h_mm) is None:
        return None
    if intrinsic_w_px is not None and _positive(intrinsic_w_px) is None:
        return None

    fluid = intrinsic_w_px is None
    ref_w_mm = (vb_w if intrinsic_w_px is None else intrinsic_w_px) * MM_PER_PX
    ref_h_mm = ref_w_mm * vb_h / vb_w
    s_box = math.inf
    if box_w_mm is not None:
        s_box = min(s_box, box_w_mm / ref_w_mm)
    if box_h_mm is not None:
        s_box = min(s_box, box_h_mm / ref_h_mm)

    base = _positive(base_font_px) if base_font_px is not None else None
    if base is None:
        # Senza testo: scala naturale, nessun vincolo di banda (un diagramma
        # di sole forme non viene ridotto).
        scale = min(1.0, s_box)
        text_pt = 0.0
        in_band = True
    else:
        pt_per_scale = base * PT_PER_PX
        s_lo = lo / pt_per_scale
        s_hi = hi / pt_per_scale
        s_nat = s_box if fluid else 1.0
        scale = min(min(max(s_nat, s_lo), s_hi), s_box)
        text_pt = pt_per_scale * scale
        in_band = text_pt >= lo - _BAND_EPS

    width_mm = math.floor(ref_w_mm * scale * 100) / 100
    return FigureFit(
        width_mm=width_mm,
        scale=_half_up(scale, 4),
        text_pt=_half_up(text_pt, 2),
        in_band=in_band,
    )


def resolve_base_font_px(fmt: str, metrics: SvgMetrics | None) -> tuple[float | None, str]:
    """`(base_font_px, source)` per il fit: le metriche risolte passano
    tali e quali; `no_text` → `(None, "no_text")`; `unresolved` o metriche
    assenti → costante di formato con `source="constant"`; formato ignoto
    senza metriche → `(None, "no_text")`."""
    if metrics is not None:
        if metrics.source == "no_text":
            return None, "no_text"
        if metrics.source != "unresolved" and _positive(metrics.font_px_min) is not None:
            return metrics.font_px_min, metrics.source
    fallback = FALLBACK_BASE_FONT_PX.get(fmt)
    if fallback is None:
        return None, "no_text"
    return fallback, "constant"


def format_mm(value: float) -> str:
    """`"140.76"`, `"168"`, `"0"`: stessa regola di `svg_normalize._fmt`."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "FALLBACK_BASE_FONT_PX",
    "LESSON_REFERENCE_BOX_MM",
    "MM_PER_PX",
    "PT_PER_PX",
    "READABILITY_BANDS_PT",
    "FigureBoxMm",
    "FigureFit",
    "FigureFitEntry",
    "FigureVariant",
    "MetricsSource",
    "SvgMetrics",
    "fit_figure_width_mm",
    "format_mm",
    "resolve_base_font_px",
]
