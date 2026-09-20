"""Impaginazione del formato `function`: niente scritte accavallate.

Il difetto veniva dalla produzione: nei grafici di funzione la formula e
la legenda erano disegnate DENTRO l'area degli assi e le etichette dei
punti avevano uno scostamento fisso dal punto, senza alcun controllo di
collisione. Cinque schermate del PDF mostravano cinque modi di
accavallarsi (etichetta sopra la formula, legenda sopra le etichette, due
etichette che si toccano, legenda sopra la formula, legenda sopra
l'etichetta di un picco); il corpus della fixture
`function_label_layout_cases.json` li ricostruisce come spec `function`
reali e aggiunge tre casi avversari (etichette lunghissime, cinque punti
ravvicinati, legenda con sei voci).

L'oracolo è il riquadro (`function_plot.TextBox`, in punti tipografici
nel sistema della figura) che il disegno registra per OGNI testo della
figura: quelli che colloca lui (etichette, formule, voci della legenda,
nomi degli assi) e quelli che colloca matplotlib e lui si limita a
misurare sull'artista vero (etichette dei tick e dei contorni). `draw`
li restituisce accanto all'SVG e alle avvertenze. I test misurano su
quei riquadri (nessuna coppia si interseca, ogni etichetta resta dentro
il bordo degli assi, banda sopra gli assi), verificano che i riquadri
descrivano il disegno vero (coordinate dei `<path>`, ancore dei `<text>`
e aloni nell'SVG), che la figura resti dentro il box della dispensa E in
quello della slide — che è il vincolante, perché è vincolato in ALTEZZA
— e che l'SVG resti deterministico.

Salta se mancano numpy, matplotlib o sympy.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from app.schemas.figure_function import FunctionFigureSpec, parse_function_spec
from app.services import figure_function_service as ffs
from app.services import figure_scale as fs
from app.services.course_lesson_slides_pdf_service import reference_slide_figure_box_mm
from app.services.figure_compute import function_numeric as fnum
from app.services.figure_compute import function_plot as fplot
from app.services.svg_normalize import svg_base_font_px, svg_intrinsic_box

_DEPS = ffs.dependencies_available()
needs_deps = pytest.mark.skipif(not _DEPS, reason="numpy, matplotlib o sympy assenti")

_FIXTURE = Path(__file__).parent / "fixtures" / "function_label_layout_cases.json"
_CASES: list[dict[str, Any]] = json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]
_NAMES = [c["name"] for c in _CASES]

# Tolleranza dichiarata sull'intersezione di due riquadri: mezzo punto
# tipografico, sotto il quale due inchiostri non si distinguono in stampa.
_OVERLAP_TOL_PT = 0.5
# Tolleranza della verifica di fedeltà fra riquadro registrato e disegno
# vero: i punti di controllo delle curve dei glifi possono uscire di poco
# dal riquadro dell'inchiostro.
_FIDELITY_TOL_PT = 2.0
# Tolleranza della verifica di fedeltà dei riquadri MISURATI su un
# artista di matplotlib: l'alone bianco che l'SVG disegna dietro il testo
# è il riquadro allargato del `pad` di `TEXT_HALO_BBOX` (0,12 em, cioè al
# più 1,08 pt a corpo 9).
_HALO_PAD_PT = 1.1
# Box di riferimento della dispensa (D15) e banda di leggibilità.
_BOX_W_MM, _BOX_H_MM = fs.LESSON_REFERENCE_BOX_MM
_BAND_LO, _BAND_HI = fs.READABILITY_BANDS_PT["lesson"]
# Corpo minimo del testo reso nel box figura della slide di riferimento
# (255,0 × 86,6 mm), che è vincolato in ALTEZZA: ogni punto di banda in
# più lo rimpicciolisce in proporzione. Il pin serve a impedire che la
# banda si allarghi di nascosto — la banda di leggibilità della slide
# (10-14 pt) le figure `function` non la raggiungono comunque, né prima
# né dopo la banda, e questo è il difetto D13 che vive per conto suo.
_SLIDE_FLOOR_PT = 6.8

_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _spec(data: dict[str, Any]) -> FunctionFigureSpec:
    spec, issues = parse_function_spec(json.dumps(data))
    assert spec is not None, issues
    return spec


def _draw(spec: FunctionFigureSpec) -> fplot.DrawResult:
    """Disegno completo della spec, con il calcolo simbolico vero (le
    formule della banda sono quelle della produzione)."""
    study = fnum.analyze(spec)
    sym, _warnings = ffs._run_symbolic(spec, timeout=20.0, target=ffs.SYMBOLIC_TARGET)
    computed, _approximate = ffs.build_computed(spec, study, sym)
    return fplot.draw(spec, study, computed, content_hash=spec.content_hash())


@pytest.fixture(scope="module")
def drawn() -> dict[str, fplot.DrawResult]:
    """Un disegno per caso, riusato da tutti i test del modulo."""
    if not _DEPS:
        pytest.skip("numpy, matplotlib o sympy assenti")
    return {case["name"]: _draw(_spec(case["spec"])) for case in _CASES}


# Riquadri che il disegno COLLOCA (`_Canvas.place`) e riquadri che
# MISURA su un artista collocato da matplotlib (`_Canvas.register`).
_PLACED = ("label", "formula", "legend", "axis")
_MEASURED = ("tick", "contour")


def _texts(result: fplot.DrawResult) -> list[fplot.TextBox]:
    return [b for b in result.boxes if b.kind != "band"]


def _placed(result: fplot.DrawResult) -> list[fplot.TextBox]:
    return [b for b in result.boxes if b.kind in _PLACED]


def _measured(result: fplot.DrawResult) -> list[fplot.TextBox]:
    return [b for b in result.boxes if b.kind in _MEASURED]


def _overlapping_pairs(boxes: list[fplot.TextBox]) -> list[tuple[str, str, float, float]]:
    """Coppie di riquadri che si intersecano oltre la tolleranza."""
    pairs: list[tuple[str, str, float, float]] = []
    for i, first in enumerate(boxes):
        for second in boxes[i + 1 :]:
            dx, dy = first.overlap(second)
            if dx > _OVERLAP_TOL_PT and dy > _OVERLAP_TOL_PT:
                pairs.append((first.gid, second.gid, round(dx, 2), round(dy, 2)))
    return pairs


# ---------------------------------------------------------------------------
# Oracolo: nessuna coppia di riquadri si interseca
# ---------------------------------------------------------------------------


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_no_text_overlaps_another(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """Il difetto delle cinque schermate: sul codice di prima ognuno di
    questi casi aveva almeno una coppia sovrapposta (etichetta/formula,
    etichetta/legenda, etichetta/etichetta, legenda/formula). Le coppie
    contate sono TUTTE, comprese quelle con le etichette dei tick e dei
    contorni, che prima non erano nemmeno registrate."""
    boxes = _texts(drawn[name])
    assert boxes, name
    assert {b.kind for b in boxes} <= set(_PLACED) | set(_MEASURED), name
    assert _overlapping_pairs(boxes) == []


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_labels_stay_inside_the_axes(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """Nessuna etichetta esce dal bordo degli assi (prima «quarto picco
    positivo» finiva fuori dalla figura, tagliata dal viewBox)."""
    frame = fplot.TextBox(
        "axes",
        "frame",
        fplot.AXES_LEFT_PT,
        fplot.AXES_BOTTOM_PT,
        fplot.AXES_LEFT_PT + fplot.AXES_WIDTH_PT,
        fplot.AXES_BOTTOM_PT + fplot.AXES_HEIGHT_PT,
    )
    outside = [
        b.gid
        for b in drawn[name].boxes
        if b.kind == "label" and b.outside_area(frame) > _OVERLAP_TOL_PT
    ]
    assert outside == []


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_band_is_above_the_axes(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """Formule e legenda stanno nella loro banda, sopra il bordo alto
    degli assi: non coprono più curve ed etichette."""
    top = fplot.AXES_BOTTOM_PT + fplot.AXES_HEIGHT_PT
    result = drawn[name]
    band = [b for b in result.boxes if b.kind in ("formula", "legend")]
    assert [b.gid for b in band if b.y0 < top] == []
    # La banda alza la figura solo quando c'è qualcosa da metterci: il
    # caso dei cinque punti non ha né formula né legenda e resta a 259,2.
    box = svg_intrinsic_box(result.svg)
    assert box is not None
    assert (box.vb_h > fplot.BASE_H_PT + 1e-6) is bool(band)


# ---------------------------------------------------------------------------
# Fedeltà: i riquadri registrati descrivono il disegno vero
# ---------------------------------------------------------------------------


def _group(svg: str, gid: str) -> str:
    """Contenuto del gruppo `gid`, con i `<g>` annidati (l'alone bianco di
    un `<text>` è un gruppo dentro il gruppo del testo)."""
    start = re.search(rf'<g id="{re.escape(gid)}">', svg)
    assert start is not None, gid
    depth, pos = 1, start.end()
    for token in re.finditer(r"<g[ >]|</g>", svg[start.end() :]):
        depth += 1 if token.group(0) != "</g>" else -1
        if depth == 0:
            pos = start.end() + token.start()
            break
    return svg[start.end() : pos]


def _svg_path_box(
    svg: str, gid: str, *, halo: bool = False
) -> tuple[float, float, float, float] | None:
    """Estremi dei `<path>` del gruppo `gid` (coordinate assolute del
    viewBox: matplotlib scrive i `PathPatch` senza `transform`); `None`
    se il gruppo è un `<text>` e non si sta cercando il suo alone."""
    group = _group(svg, gid)
    if "<text" in group and not halo:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for d in re.findall(r'\sd="([^"]+)"', group):
        numbers = [float(t) for t in _NUMBER_RE.findall(d)]
        xs.extend(numbers[0::2])
        ys.extend(numbers[1::2])
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _svg_text_anchor(svg: str, gid: str) -> tuple[float, float] | None:
    """`(x, y)` dell'ancora del `<text>` del gruppo `gid` (base della
    prima lettera: `ha="left"`, `va="baseline"`)."""
    block = re.search(
        rf'<g id="{re.escape(gid)}">.*?<text[^>]*?x="([\d.]+)" y="([\d.]+)"', svg, re.S
    )
    if block is None:
        return None
    return (float(block.group(1)), float(block.group(2)))


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_recorded_boxes_describe_the_drawing(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """I riquadri non sono una dichiarazione d'intenti: per ogni testo
    disegnato come geometria le coordinate dei `<path>` dell'SVG cadono
    nel riquadro registrato, e per ogni `<text>` ci cade l'ancora."""
    result = drawn[name]
    box = svg_intrinsic_box(result.svg)
    assert box is not None
    height = box.vb_h
    checked = 0
    for recorded in _placed(result):
        path_box = _svg_path_box(result.svg, recorded.gid)
        if path_box is not None:
            x0, y0, x1, y1 = path_box
            # L'SVG ha la y verso il basso: `fig_y = vb_h − svg_y`.
            assert x0 >= recorded.x0 - _FIDELITY_TOL_PT, (name, recorded.gid)
            assert x1 <= recorded.x1 + _FIDELITY_TOL_PT, (name, recorded.gid)
            assert height - y1 >= recorded.y0 - _FIDELITY_TOL_PT, (name, recorded.gid)
            assert height - y0 <= recorded.y1 + _FIDELITY_TOL_PT, (name, recorded.gid)
            checked += 1
            continue
        anchor = _svg_text_anchor(result.svg, recorded.gid)
        assert anchor is not None, (name, recorded.gid)
        assert recorded.x0 - _FIDELITY_TOL_PT <= anchor[0] <= recorded.x0 + _FIDELITY_TOL_PT
        assert recorded.y0 - _FIDELITY_TOL_PT <= height - anchor[1] <= recorded.y1
        checked += 1
    assert checked == len(_placed(result))


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_measured_boxes_describe_the_drawing(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """Le etichette dei tick e dei contorni le colloca matplotlib: il
    disegno le misura sull'artista e le registra. La verifica indipendente
    è l'ALONE bianco che l'SVG disegna dietro ognuna, cioè il riquadro del
    testo allargato del `pad` di `TEXT_HALO_BBOX`: deve contenere il
    riquadro registrato e non eccederlo di più del `pad`."""
    result = drawn[name]
    box = svg_intrinsic_box(result.svg)
    assert box is not None
    height = box.vb_h
    measured = _measured(result)
    assert measured, name  # ogni figura ha almeno le etichette dei tick
    for recorded in measured:
        halo = _svg_path_box(result.svg, recorded.gid, halo=True)
        assert halo is not None, (name, recorded.gid)
        x0, y0, x1, y1 = halo
        alone = fplot.TextBox(recorded.gid, "halo", x0, height - y1, x1, height - y0)
        assert alone.outside_area(recorded.inflated(_HALO_PAD_PT + _FIDELITY_TOL_PT)) == 0.0, (
            name,
            recorded.gid,
        )
        assert recorded.outside_area(alone.inflated(_FIDELITY_TOL_PT)) == 0.0, (
            name,
            recorded.gid,
        )


# ---------------------------------------------------------------------------
# Geometria: box della dispensa, banda di leggibilità, determinismo
# ---------------------------------------------------------------------------


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_figure_fits_the_lesson_box_in_band(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """La figura è più alta di prima (la banda è spazio riservato, non
    sovrapposizione) ma resta larga 374,4 pt: nel box della dispensa
    (168 × 242 mm) entra a scala naturale e il corpo del testo resta in
    banda. Se uscisse, uscirebbe nel `fit_report` come per le altre."""
    svg = drawn[name].svg
    box = svg_intrinsic_box(svg)
    assert box is not None and box.vb_w == pytest.approx(fplot.FIG_W_PT)
    base, _source = fs.resolve_base_font_px("function", svg_base_font_px(svg))
    fit = fs.fit_figure_width_mm(
        vb_w=box.vb_w,
        vb_h=box.vb_h,
        base_font_px=base,
        box_w_mm=_BOX_W_MM,
        box_h_mm=_BOX_H_MM,
        variant="lesson",
        intrinsic_w_px=box.width_px,
    )
    assert fit is not None
    assert fit.width_mm <= _BOX_W_MM
    assert fit.width_mm * box.vb_h / box.vb_w <= _BOX_H_MM
    assert fit.in_band and _BAND_LO <= fit.text_pt <= _BAND_HI, (name, fit)


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_figure_fits_the_slide_box(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """Il box figura della slide di RIFERIMENTO (255,0 × 86,6 mm) è
    vincolato in altezza, non in larghezza: il fit scala la figura per
    l'altezza e ogni punto di banda in più rimpicciolisce il testo reso in
    proporzione. È il vincolo che la prima stesura della banda non aveva
    misurato, e che l'aveva fatta crescere fino a 402 pt.

    La banda di leggibilità della slide (10-14 pt) le figure `function`
    non la raggiungono comunque — non la raggiungevano nemmeno prima che
    la banda esistesse — quindi qui si pinna il PAVIMENTO misurato, non
    `in_band`."""
    svg = drawn[name].svg
    box = svg_intrinsic_box(svg)
    assert box is not None
    slide_box = reference_slide_figure_box_mm()
    base, _source = fs.resolve_base_font_px("function", svg_base_font_px(svg))
    fit = fs.fit_figure_width_mm(
        vb_w=box.vb_w,
        vb_h=box.vb_h,
        base_font_px=base,
        box_w_mm=slide_box[0],
        box_h_mm=slide_box[1],
        variant="slide",
        intrinsic_w_px=box.width_px,
    )
    assert fit is not None
    assert fit.width_mm <= slide_box[0]
    assert fit.width_mm * box.vb_h / box.vb_w <= slide_box[1] + 1e-6
    assert fit.text_pt >= _SLIDE_FLOOR_PT, (name, fit)


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_the_figure_is_never_taller_than_wide(
    name: str, drawn: dict[str, fplot.DrawResult]
) -> None:
    """Una figura più alta che larga viene rimpicciolita dal fit in ogni
    box largo e basso (la slide). Non ne esistono: il limite vale per
    costruzione, non caso per caso."""
    box = svg_intrinsic_box(drawn[name].svg)
    assert box is not None
    assert box.vb_h <= fplot.MAX_FIG_H_PT + 1e-6, (name, box.vb_h)
    assert box.vb_h < box.vb_w


@needs_deps
def test_the_tallest_band_the_schema_allows_stays_wider_than_tall() -> None:
    """Il limite è aritmetico: al più `MAX_EXPRESSIONS` righe di formula e
    al più `max(MAX_EXPRESSIONS, MAX_PARAMETER_VALUES)` voci di legenda,
    una per riga. Sopra ci sono il caso peggiore DAVVERO consentito dallo
    schema — quattro espressioni con l'etichetta da 24 caratteri, il
    massimo di `ExpressionSpec.label`, che l'impaccamento mette su quattro
    righe — e la famiglia con sei valori."""
    assert fplot.MAX_FIG_H_PT < fplot.FIG_W_PT
    worst = _spec(
        {
            "kind": "function_study",
            "expressions": [
                {"expr": "sin(x)", "label": "W" * 24},
                {"expr": "sin(2*x)/2", "label": "M" * 24},
                {"expr": "sin(3*x)/3", "label": "W" * 23 + "M"},
                {"expr": "sin(4*x)/4", "label": "M" * 23 + "W"},
            ],
            "domain": [-3.2, 3.2],
            "show": ["formula"],
        }
    )
    family = _spec(
        {
            "kind": "family",
            "expressions": [{"expr": "exp(-k*x)*sin(2*pi*x)", "label": "W" * 24}],
            "domain": [0, 3],
            "parameter": {"name": "k", "values": [0, 0.1, 0.2, 0.35, 0.5, 0.8]},
            "show": ["formula"],
        }
    )
    for spec in (worst, family):
        result = _draw(spec)
        box = svg_intrinsic_box(result.svg)
        assert box is not None
        assert box.vb_h <= fplot.MAX_FIG_H_PT + 1e-6, box.vb_h
        assert box.vb_h < box.vb_w, box
        assert _overlapping_pairs(_texts(result)) == []


@needs_deps
@pytest.mark.parametrize("name", _NAMES)
def test_drawing_is_deterministic(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """Stessa spec, stesso SVG byte per byte e stessi riquadri: la
    collocazione delle etichette è misurata con il font bundled di
    matplotlib, non con l'ambiente."""
    case = next(c for c in _CASES if c["name"] == name)
    again = _draw(_spec(case["spec"]))
    first = drawn[name]
    assert hashlib.sha256(again.svg.encode("utf-8")).hexdigest() == (
        hashlib.sha256(first.svg.encode("utf-8")).hexdigest()
    )
    assert again.boxes == first.boxes


# ---------------------------------------------------------------------------
# Banda: cresce solo quando serve, e la figura senza banda è quella di prima
# ---------------------------------------------------------------------------


@needs_deps
def test_a_figure_without_formula_or_legend_keeps_the_old_geometry() -> None:
    """Una figura senza formula e con una sola curva non ha banda: resta
    374,4 × 259,2 pt, la geometria di sempre (nessun ricalcolo a valle)."""
    spec = _spec(
        {
            "kind": "function_study",
            "expressions": [{"expr": "x**2 - 1"}],
            "domain": [-3, 3],
            "show": ["zeros"],
        }
    )
    box = svg_intrinsic_box(_draw(spec).svg)
    assert box is not None
    assert box.vb_w == pytest.approx(fplot.FIG_W_PT)
    assert box.vb_h == pytest.approx(fplot.BASE_H_PT)


@needs_deps
def test_the_band_uses_the_empty_space_above_the_axes_first() -> None:
    """La figura di base ha già `TOP_PAD_PT` di vuoto sopra gli assi: la
    banda lo USA, e la figura cresce solo di quello che non ci entra. Una
    riga di formula costa 4,1 pt di figura, non 23,1 come quando la banda
    si impilava sopra il vuoto."""
    spec = _spec(
        {
            "kind": "function_study",
            "expressions": [{"expr": "sin(2*pi*x)", "label": "segnale"}],
            "domain": [0, 2.2],
            "show": ["formula"],
        }
    )
    box = svg_intrinsic_box(_draw(spec).svg)
    assert box is not None
    una_riga = fplot.MATH_SIZE_PT * fplot.BAND_LINE_FACTOR
    atteso = fplot.AXES_TOP_PT + fplot.BAND_GAP_PT + una_riga + fplot.BAND_TOP_PAD_PT
    assert box.vb_h == pytest.approx(atteso, abs=0.05)
    assert box.vb_h - fplot.BASE_H_PT < fplot.TOP_PAD_PT


@needs_deps
def test_a_label_too_wide_is_brought_back_inside_the_axes() -> None:
    """Due etichette d'area da 40 caratteri (il massimo dello schema) sono
    larghe quasi quanto gli assi: ancorate al punto uscirebbero dal
    viewBox, com'è successo in produzione. Gli ultimi candidati le
    riportano dentro il riquadro degli assi."""
    spec = _spec(
        {
            "kind": "area",
            "expressions": [{"expr": "sin(x)", "label": "onda"}],
            "domain": [0, 6.2],
            "show": ["formula"],
            "annotations": [
                {"kind": "area", "between": [0, 3.14], "label": "W" * 40},
                {"kind": "area", "between": [3.14, 6.2], "label": "M" * 40},
            ],
        }
    )
    result = _draw(spec)
    frame = fplot.TextBox(
        "axes",
        "frame",
        fplot.AXES_LEFT_PT,
        fplot.AXES_BOTTOM_PT,
        fplot.AXES_LEFT_PT + fplot.AXES_WIDTH_PT,
        fplot.AXES_BOTTOM_PT + fplot.AXES_HEIGHT_PT,
    )
    labels = [b for b in result.boxes if b.kind == "label"]
    assert len(labels) == 2
    for label in labels:
        assert label.x1 - label.x0 > 0.8 * fplot.AXES_WIDTH_PT, label
        assert label.outside_area(frame) <= _OVERLAP_TOL_PT, label


@needs_deps
def test_tick_labels_are_obstacles_for_the_point_labels() -> None:
    """Le etichette dei tick sono testo già collocato: entrano fra i
    riquadri e quindi fra gli ostacoli. Prima non contavano e l'etichetta
    di uno zero poteva finire sopra il numero di un tick."""
    spec = _spec(
        {
            "kind": "tangent",
            "expressions": [{"expr": "x**3 - 3*x", "label": "cubica"}],
            "domain": [-3, 3],
            "show": ["zeros", "formula"],
            "annotations": [{"kind": "tangent", "at": 1.5, "label": "tangente"}],
        }
    )
    result = _draw(spec)
    ticks = [b for b in result.boxes if b.kind == "tick"]
    assert len(ticks) >= 4
    assert _overlapping_pairs([*ticks, *[b for b in result.boxes if b.kind == "label"]]) == []


@needs_deps
def test_contour_labels_go_through_the_oracle() -> None:
    """Le etichette dei contorni le colloca `ax.clabel` lungo le curve:
    prima non passavano dall'oracolo e due numeri potevano restare uno
    sopra l'altro (misurati: 16,3 × 7,3 pt). Ora sono registrate, e quella
    che non ha posto viene tolta con l'avvertenza."""
    spec = _spec(
        {
            "kind": "level_curves",
            "expressions": [{"expr": "sin(p)*cos(q)", "label": "campo"}],
            "variables": ["p", "q"],
            "domain": [-3, 3],
            "range": [-3, 3],
            "levels": 12,
            "show": ["formula"],
        }
    )
    result = _draw(spec)
    contours = [b for b in result.boxes if b.kind == "contour"]
    assert len(contours) >= 6
    assert _overlapping_pairs(_texts(result)) == []
    # Qualcuna è stata tolta, e il disegno lo dice invece di nasconderlo.
    assert fplot.LABELS_CROWDED in result.warnings


@needs_deps
def test_crowded_labels_are_reported_not_hidden() -> None:
    """Quando nessun candidato è libero il disegno sceglie il male minore
    e lo dichiara con `labels_crowded`: il caso si vede nei log invece di
    restare nascosto sotto un'etichetta."""
    spec = _spec(
        {
            "kind": "function_study",
            "expressions": [{"expr": "0.02*x", "label": "retta quasi orizzontale"}],
            "domain": [0, 1],
            "show": [],
            "annotations": [
                {"kind": "point", "at": 0.50, "label": "campione molto vicino al primo"},
                {"kind": "point", "at": 0.52, "label": "campione molto vicino al secondo"},
                {"kind": "point", "at": 0.54, "label": "campione molto vicino al terzo"},
                {"kind": "point", "at": 0.56, "label": "campione molto vicino al quarto"},
                {"kind": "point", "at": 0.58, "label": "campione molto vicino al quinto"},
                {"kind": "point", "at": 0.60, "label": "campione molto vicino al sesto"},
            ],
        }
    )
    result = _draw(spec)
    assert fplot.LABELS_CROWDED in result.warnings
    # Anche stretto, il disegno resta il migliore possibile: le
    # sovrapposizioni sono quelle inevitabili, non quelle dello
    # scostamento fisso (sei etichette larghe ~120 pt su 40 pt di spazio).
    assert len(_overlapping_pairs(_texts(result))) < math.comb(6, 2)


@pytest.mark.parametrize("name", _NAMES)
def test_no_text_is_clipped_by_the_figure(name: str, drawn: dict[str, fplot.DrawResult]) -> None:
    """Nessuna scritta esce dal viewBox: fuori dal bordo il renderer la
    taglia, e un numero tagliato si legge male quanto una scritta
    sovrapposta. Il caso vero erano i valori dell'asse y con lo zero a
    sinistra (`-0,75` sporgeva di 2,77 pt): ora il corpo dei valori scende
    a passi finché rientrano. Vale anche per le etichette dei punti, che
    l'algoritmo riporta dentro con `_clamp_to_figure`."""
    result = drawn[name]
    height = result.height_pt if hasattr(result, "height_pt") else None
    fuori = [
        (b.gid, round(b.x0, 2), round(b.x1, 2))
        for b in result.boxes
        if b.kind != "band"
        and (
            b.x0 < -0.01
            or b.x1 > fplot.FIG_W_PT + 0.01
            or b.y0 < -0.01
            or (height is not None and b.y1 > height + 0.01)
        )
    ]
    assert fuori == [], name
