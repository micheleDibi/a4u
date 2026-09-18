"""Direzione delle catene nella dispensa resa (D15): oracolo end-to-end.

Il difetto segnalato dal docente: un flowchart che è una CATENA LINEARE
dichiarata `flowchart LR` cresce solo in larghezza; nel box della dispensa
(168 × 242 mm) la scala la impone la larghezza e il corpo del testo crolla.
Misurato con il motore reale (Mermaid 11 in Chromium) e con la geometria
letta da WeasyPrint sulla pagina resa:

    catena di  8 nodi   LR  1922 × 77 uu →  3,47 pt     TB → 11,00 pt
    catena di 12 nodi   LR  2923 × 62 uu →  2,28 pt     TB →  8,59 pt

Qui si verifica che la resa MISURA le due varianti e tiene quella con il
corpo più grande, che il sorgente salvato non cambia, e che nessuna figura
fuori dal caso viene toccata: grafo con diramazioni
(`fig_market_structure` dell'export del docente, 7 nodi e 9 archi),
sorgente già `TB`, catena corta che sta già in banda, sorgente con
`subgraph`, e i quindici modelli Mermaid degli editor.

Serve Chromium (pre-render Mermaid) e WeasyPrint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.services import course_lesson_pdf_service as pdf
from app.services import course_lesson_slides_pdf_service as slides_pdf
from app.services import figure_render_service as frs
from app.services.figure_compute.chain_layout import vertical_chain_variant
from app.services.figure_scale import (
    READABILITY_BANDS_PT,
    FigureFitEntry,
    fit_figure_width_mm,
    resolve_base_font_px,
)
from app.services.svg_normalize import svg_intrinsic_box
from tests.test_frontend_figure_templates import MERMAID

_MM = 96.0 / 25.4
# Tolleranza sulla lettura dei box di WeasyPrint (passo di 0,001 mm) e
# sull'arrotondamento per difetto della larghezza al centesimo.
_PT_TOL = 0.05

_NODES_8 = (
    "Sorgente laser",
    "Fascio verso il bersaglio",
    "Superficie vibrante",
    "Luce riflessa o diffusa",
    "Interferometro e riferimento ottico",
    "Demodulazione",
    "Segnale di velocità",
    "Analisi nel tempo e in frequenza",
)
_NODES_12 = (
    "Luce interferometrica",
    "Fotodetettore",
    "Corrente foto-generata",
    "Amplificatore transimpedenza",
    "Tensione condizionata",
    "Filtro e adattamento di livello",
    "Acquisizione digitale",
    "Demodulazione Doppler",
    "Velocità vibratoria",
    "Integrazione controllata",
    "Spostamento ricostruito",
    "Analisi spettrale",
)
_PIPELINE = (
    "Acquisizione",
    "Pulizia",
    "Normalizzazione",
    "Estrazione delle feature",
    "Addestramento",
    "Validazione incrociata",
    "Messa in esercizio",
)


def _chain(nodes: tuple[str, ...], direction: str) -> str:
    lines = [f"flowchart {direction}"]
    lines += [f'    N{i}["{label}"]' for i, label in enumerate(nodes)]
    lines += [f"    N{i} --> N{i + 1}" for i in range(len(nodes) - 1)]
    return "\n".join(lines)


def _subgraph_chain(nodes: tuple[str, ...]) -> str:
    """Catena orizzontale dentro un `subgraph` con `direction LR` proprio:
    è larga quanto la catena nuda (1677 × 132 uu → 3,98 pt) ma il gruppo
    ferma il riconoscimento."""
    lines = ["flowchart LR", '    subgraph P ["Pipeline"]', "    direction LR"]
    lines += [f'        N{i}["{label}"]' for i, label in enumerate(nodes)]
    lines += [f"        N{i} --> N{i + 1}" for i in range(len(nodes) - 1)]
    lines.append("    end")
    return "\n".join(lines)


# `fig_market_structure` dell'export di quattro lezioni reali: 7 nodi, 9
# archi, tre diramazioni da A e quattro confluenze su F.
_MARKET = (
    "flowchart LR\n"
    "    A[Strumenti finanziari] --> B[Luogo fisico di scambio]\n"
    "    A --> C[Circuito organizzato]\n"
    "    A --> D[Piattaforma elettronica]\n"
    "    A --> E[Mercato OTC]\n"
    "    B --> F[Domanda e offerta]\n"
    "    C --> F\n"
    "    D --> F\n"
    "    E --> F\n"
    "    F --> G[Prezzi e transazioni]"
)
_SHORT = 'flowchart LR\n    A["Ipotesi"] --> B["Modello"]\n    B --> C["Verifica"]'

# (asset_id, sorgente, variante attesa, pt PRIMA, pt DOPO)
_CASES: list[tuple[str, str, bool, float, float]] = [
    ("c8", _chain(_NODES_8, "LR"), True, 3.47, 11.00),
    ("c12", _chain(_NODES_12, "LR"), True, 2.28, 8.59),
    ("tb12", _chain(_NODES_12, "TB"), False, 8.59, 8.59),
    ("branch", _MARKET, False, 7.33, 7.33),
    ("short", _SHORT, False, 11.00, 11.00),
    ("sub", _subgraph_chain(_PIPELINE), False, 3.98, 3.98),
]
_ASSETS = [{"asset_id": aid, "format": "mermaid", "content": src} for aid, src, *_ in _CASES]


def _weasyprint() -> Any:
    try:
        import weasyprint
    except (ImportError, OSError) as exc:  # su macOS serve DYLD_FALLBACK_LIBRARY_PATH
        pytest.skip(f"weasyprint non importabile: {exc}")
    return weasyprint


def _walk(box: Any) -> Iterator[Any]:
    yield box
    children = getattr(box, "all_children", None)
    for child in children() if children else getattr(box, "children", []):
        yield from _walk(child)


def _mermaid_box(weasyprint: Any, html: str) -> tuple[float, float]:
    """Larghezza e altezza (px CSS) del `div.mermaid-svg` nella pagina resa."""
    for page in weasyprint.HTML(string=html).render().pages:
        for box in _walk(page._page_box):
            element = getattr(box, "element", None)
            classes = (element.get("class") or "").split() if element is not None else []
            if box.element_tag == "div" and "mermaid-svg" in classes:
                return float(box.width), float(box.height)
    raise AssertionError("nessun `div.mermaid-svg` nella pagina resa")


@pytest.fixture(scope="module")
def rendered() -> dict[str, frs.RenderedFigure]:
    """Le sei figure rese una volta sola, con il passo delle varianti
    (D15) sul box vero della dispensa."""
    if not frs.REGISTRY["mermaid"].available():
        pytest.skip("renderer Mermaid non disponibile")
    figures = asyncio.run(frs.render_figure_map(_ASSETS, language="it"))
    assert set(figures) == {aid for aid, *_ in _CASES}, sorted(figures)
    return asyncio.run(
        frs.render_chain_variants(
            _ASSETS,
            figures,
            box_mm=pdf.lesson_mermaid_box_mm(None, language="it"),
            variant="lesson",
            language="it",
            lesson_code="M1.L1",
        )
    )


def test_the_reference_box_of_the_lesson_is_the_measured_one() -> None:
    """168 × 242 mm: la larghezza del contenuto su A4 con margine di 20 mm
    meno il padding del wrapper Mermaid, per l'altezza utile della pagina."""
    assert pdf.lesson_mermaid_box_mm(None, language="it") == (168.0, 242.0)


@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
def test_only_the_horizontal_chains_get_a_variant(
    case: tuple[str, str, bool, float, float], rendered: dict[str, frs.RenderedFigure]
) -> None:
    """La variante è resa per le sole catene orizzontali fuori banda: non
    per il grafo con diramazioni, non per il sorgente già `TB`, non per la
    catena corta che sta già in banda, non per il sorgente con `subgraph`."""
    asset_id, source, expected, _before, _after = case
    fig = rendered[asset_id]
    assert (fig.chain_variant is not None) is expected, asset_id
    if asset_id == "short":
        # Riconosciuta come catena, ma già in banda: nessuna resa in più.
        assert vertical_chain_variant(source) is not None
    elif expected:
        assert vertical_chain_variant(source) is not None
    else:
        assert vertical_chain_variant(source) is None, asset_id


@pytest.mark.parametrize("case", _CASES, ids=[c[0] for c in _CASES])
def test_the_lesson_pdf_measures_both_and_keeps_the_bigger(
    case: tuple[str, str, bool, float, float], rendered: dict[str, frs.RenderedFigure]
) -> None:
    """Oracolo end-to-end: il corpo del testo LETTO dalla pagina resa da
    WeasyPrint è quello atteso dopo la scelta, la voce del `fit_report`
    porta lo stesso valore e `direction_flipped` dice se la direzione è
    cambiata."""
    weasyprint = _weasyprint()
    asset_id, source, expected, before, after = case
    fig = rendered[asset_id]
    content = {
        "introduction": "Vedi [FIG:A].",
        "sections": [],
        "summary": "",
        "visual_assets": [
            {
                "asset_id": "A",
                "format": "mermaid",
                "content": source,
                "caption": "Catena",
                "alt_text": "",
            }
        ],
    }
    report: list[FigureFitEntry] = []
    html = pdf.render_lesson_html(
        course=Course(title="Corso", language_code="it", cfu=6),
        lesson=CourseLesson(lesson_code="M1.L1", title="Lezione", content_raw=content),
        organization=None,
        pdf_template=None,
        visual_svg_map={"A": fig},
        fit_report=report,
    )
    assert len(report) == 1
    entry = report[0]
    assert entry.direction_flipped is expected, asset_id
    assert entry.text_pt == pytest.approx(after, abs=_PT_TOL), asset_id

    # Corpo del testo alla geometria REALE della pagina: base × 0,75 × scala.
    chosen = fig.chain_variant if expected else fig
    assert chosen is not None
    sbox = svg_intrinsic_box(chosen.svg)
    assert sbox is not None and chosen.metrics is not None
    assert chosen.metrics.font_px_min is not None
    w_px, h_px = _mermaid_box(weasyprint, html)
    scale = min(w_px / sbox.vb_w, h_px / sbox.vb_h)
    measured = chosen.metrics.font_px_min / sbox.px_per_unit * 0.75 * scale
    assert measured == pytest.approx(after, abs=_PT_TOL), asset_id

    # Il PRIMA resta quello della figura originale, misurato nello stesso box.
    original = svg_intrinsic_box(fig.svg)
    assert original is not None and fig.metrics is not None
    assert fig.metrics.font_px_min is not None
    original_scale = min(168.0 * _MM / original.vb_w, 242.0 * _MM / original.vb_h)
    original_pt = fig.metrics.font_px_min / original.px_per_unit * 0.75 * original_scale
    # Il fit non supera mai il tetto della banda: `short` riempie il box a
    # 11,55 pt e viene riportata a 11,00.
    ceiling = READABILITY_BANDS_PT["lesson"][1]
    assert min(original_pt, ceiling) == pytest.approx(before, abs=_PT_TOL), asset_id


def test_the_saved_source_never_changes(rendered: dict[str, frs.RenderedFigure]) -> None:
    """Nessun backfill: il `content` degli asset è quello di partenza, e la
    variante è il sorgente con il SOLO token di direzione cambiato."""
    for asset, (asset_id, source, expected, _b, _a) in zip(_ASSETS, _CASES, strict=True):
        assert asset["content"] == source, asset_id
        if expected:
            flipped = vertical_chain_variant(source)
            assert flipped is not None
            assert flipped == source.replace("flowchart LR", "flowchart TB", 1)
            assert len(flipped) == len(source)


def test_the_untouched_figures_keep_the_very_same_svg() -> None:
    """Il passo delle varianti non tocca gli SVG: per le figure non
    interessate il record della mappa è lo STESSO oggetto del pre-render."""
    if not frs.REGISTRY["mermaid"].available():
        pytest.skip("renderer Mermaid non disponibile")
    figures = asyncio.run(frs.render_figure_map(_ASSETS, language="it"))
    after = asyncio.run(
        frs.render_chain_variants(
            _ASSETS,
            figures,
            box_mm=pdf.lesson_mermaid_box_mm(None, language="it"),
            variant="lesson",
            language="it",
        )
    )
    flipped = {aid for aid, _s, expected, _b, _a in _CASES if expected}
    for asset_id, fig in figures.items():
        if asset_id in flipped:
            assert after[asset_id] is not fig
            assert after[asset_id].svg == fig.svg  # l'SVG originale è intatto
        else:
            assert after[asset_id] is fig, asset_id


def test_no_editor_template_is_a_horizontal_chain() -> None:
    """I quindici modelli Mermaid degli editor non sono catene lineari
    orizzontali: nessuno di loro è nemmeno candidato, quindi i loro SVG
    restano quelli di sempre."""
    assert len(MERMAID) == 15
    assert [tpl.name for tpl in MERMAID if vertical_chain_variant(tpl.code) is not None] == []


def test_the_slide_surface_uses_its_own_reference_box() -> None:
    """La slide di riferimento è 255 × 86,6 mm (un blocco, titolo e
    didascalia di una riga): è il box su cui il passo delle varianti decide
    per le slide, e non è quello della dispensa."""
    assert slides_pdf.reference_slide_figure_box_mm() == (255.0, 86.6)


def test_on_a_slide_the_horizontal_chain_wins_and_stays() -> None:
    """La slide è larga e bassa: lì la variante verticale PERDE, e la
    misura lo constata invece di ribaltare per regola. Catena di 12 nodi
    sul box della slide di riferimento: 3,46 pt in `LR`, 3,07 pt in `TB`."""
    if not frs.REGISTRY["mermaid"].available():
        pytest.skip("renderer Mermaid non disponibile")
    source = _chain(_NODES_12, "LR")
    assets = [{"asset_id": "A", "format": "mermaid", "content": source}]
    box = slides_pdf.reference_slide_figure_box_mm()
    figures = asyncio.run(frs.render_figure_map(assets, language="it"))
    after = asyncio.run(
        frs.render_chain_variants(assets, figures, box_mm=box, variant="slide", language="it")
    )
    fig = after["A"]
    assert fig.chain_variant is not None, "la variante va comunque resa e misurata"
    measured = {}
    for name, candidate in (("LR", fig), ("TB", fig.chain_variant)):
        sbox = svg_intrinsic_box(candidate.svg)
        assert sbox is not None
        base, _source = resolve_base_font_px("mermaid", candidate.metrics)
        fit = fit_figure_width_mm(
            vb_w=sbox.vb_w,
            vb_h=sbox.vb_h,
            base_font_px=base,
            box_w_mm=box[0],
            box_h_mm=box[1],
            variant="slide",
            intrinsic_w_px=sbox.width_px,
        )
        assert fit is not None
        measured[name] = fit.text_pt
    assert measured["LR"] == pytest.approx(3.46, abs=_PT_TOL)
    assert measured["TB"] == pytest.approx(3.07, abs=_PT_TOL)
    assert measured["TB"] < measured["LR"], measured

    # E la resa della slide, di conseguenza, NON ribalta.
    report: list[FigureFitEntry] = []
    style, chosen = pdf._figure_width_style(
        fig,
        fmt="mermaid",
        variant="slide",
        box=box,
        asset_id="A",
        lesson_code="M1.L1",
        fit_report=report,
    )
    assert chosen is fig and "width:255mm" in style
    assert report[0].direction_flipped is False
