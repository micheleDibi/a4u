"""Fondamenta delle figure accademiche (WP2a): tema, etichette, alias formato.

Test puri (nessun DB, nessun binario): `figure_theme` è un modulo leaf e
deve restare importabile senza config, SQLAlchemy o librerie pesanti.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.course_lesson_content import LessonContentVisualAsset
from app.schemas.course_lesson_slides import LessonSlideNewAsset
from app.services import figure_theme as theme

EXPECTED_KEYS = {
    "courses.figures.label",
    "courses.figures.labelUnnumbered",
    "courses.figures.illustrativeData",
    "courses.figures.approxValues",
    "courses.figures.renderError",
    "courses.figures.loading",
    "courses.figures.missing",
    *(f"courses.figures.formats.{f}" for f in ("mermaid", "vegalite", "dot", "function", "image")),
    *(
        f"courses.figures.function.{k}"
        for k in (
            "zeros",
            "zero_intervals",
            "critical_points",
            "inflection_points",
            "asymptote_vertical",
            "asymptote_horizontal",
            "asymptote_oblique",
            "integral",
            "tangent",
            "levels",
            "none",
            "truncated",
        )
    ),
}


# ---------------------------------------------------------------------------
# Localizzazione
# ---------------------------------------------------------------------------


def test_figure_i18n_it_en_same_keys():
    assert set(theme.FIGURE_I18N) == {"it", "en"}
    assert set(theme.FIGURE_I18N["it"]) == set(theme.FIGURE_I18N["en"]) == EXPECTED_KEYS
    for lang in ("it", "en"):
        for key, text in theme.FIGURE_I18N[lang].items():
            assert key.startswith("courses.figures."), key
            assert text.strip(), key
    assert theme.FIGURE_I18N["it"]["courses.figures.label"] == "Figura {{n}}."
    assert theme.FIGURE_I18N["en"]["courses.figures.label"] == "Figure {{n}}."
    assert theme.FIGURE_I18N["it"]["courses.figures.labelUnnumbered"] == "Figura."


def test_figure_labels_fallback_it():
    it = theme.figure_labels("it")
    assert theme.figure_labels("de") == it
    assert theme.figure_labels(None) == it
    assert theme.figure_labels("") == it
    assert theme.figure_labels("ja-JP") == it
    en = theme.figure_labels("en")
    assert en["courses.figures.label"] == "Figure {{n}}."
    assert theme.figure_labels("en-GB") == en
    assert theme.figure_labels("EN") == en
    # Copia: il chiamante non deve poter alterare il dizionario condiviso.
    it["courses.figures.label"] = "X"
    assert theme.FIGURE_I18N["it"]["courses.figures.label"] == "Figura {{n}}."


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


def _parse_initialize(js: str) -> dict:
    assert js.startswith("mermaid.initialize(") and js.endswith(");")
    return json.loads(js[len("mermaid.initialize(") : -len(");")])


def test_mermaid_initialize_js_html_labels_false_top_level():
    cfg = _parse_initialize(theme.mermaid_initialize_js(use_max_width=True))
    assert cfg["htmlLabels"] is False
    assert cfg["startOnLoad"] is False
    assert cfg["theme"] == "neutral"
    assert cfg["securityLevel"] == "loose"
    assert cfg["themeVariables"]["fontFamily"] == theme.MERMAID_FONT_FAMILY
    assert "Noto Sans" in cfg["themeVariables"]["fontFamily"]
    for diagram in ("flowchart", "class"):
        assert cfg[diagram]["htmlLabels"] is False
    # `state` non espone più `htmlLabels` in Mermaid 11: vale il top-level.
    assert "htmlLabels" not in cfg["state"]
    for diagram in ("flowchart", "class", "state", "er", "pie", "xyChart", "radar"):
        assert cfg[diagram]["useMaxWidth"] is True
    assert cfg["themeVariables"]["pie1"] == theme.PALETTE[0]

    validator = _parse_initialize(
        theme.mermaid_initialize_js(use_max_width=False, security_level="strict")
    )
    assert validator["htmlLabels"] is False
    assert validator["securityLevel"] == "strict"
    assert validator["flowchart"]["useMaxWidth"] is False
    assert validator["sequence"]["useMaxWidth"] is False


def test_mermaid_types_and_samples():
    assert not set(theme.MERMAID_ALLOWED_TYPES) & set(theme.MERMAID_EXCLUDED_TYPES)
    assert "journey" in theme.MERMAID_EXCLUDED_TYPES
    assert len(theme.MERMAID_D8_SAMPLES) == 15
    assert set(theme.MERMAID_D8_SAMPLES) <= set(theme.MERMAID_ALLOWED_TYPES)
    for kind, code in theme.MERMAID_D8_SAMPLES.items():
        assert code.split("\n", 1)[0].startswith(kind), kind
    # Un campione porta una label CJK (guardia sui glifi ideografici).
    assert any(any(ord(ch) > 0x2E80 for ch in code) for code in theme.MERMAID_D8_SAMPLES.values())


# ---------------------------------------------------------------------------
# Tema: palette, Vega-Lite, DOT, matplotlib
# ---------------------------------------------------------------------------


def test_palette_and_theme_configs():
    assert len(theme.PALETTE) == 8 and len(set(theme.PALETTE)) == 8
    assert theme.VEGALITE_THEME_CONFIG["font"] == theme.FONT_FAMILY_PRIMARY
    assert theme.VEGALITE_THEME_CONFIG["range"]["category"] == list(theme.PALETTE)
    view = theme.VEGALITE_THEME_CONFIG["view"]
    assert view["stroke"] is None
    # Le scale band/point non leggono `continuous*`: senza `discrete*` un
    # grafico a barre userebbe il passo di default (20 px) e uscirebbe alto
    # e stretto accanto a uno scatter da 360 px (TIP-6).
    assert view["discreteWidth"] == view["continuousWidth"] == 360
    assert view["discreteHeight"] == view["continuousHeight"] == 220
    # Etichette dell'asse discreto in orizzontale, non ruotate di -90° come
    # vuole il default di Vega-Lite (TIP-8).
    assert theme.VEGALITE_THEME_CONFIG["axisX"]["labelAngle"] == 0
    # Il config Vega-Lite deve restare serializzabile (viene fuso nella spec).
    json.dumps(theme.VEGALITE_THEME_CONFIG)
    assert set(theme.DOT_DEFAULTS) == {"graph", "node", "edge"}
    for block, text in theme.DOT_DEFAULTS.items():
        assert text.startswith(f"{block} [") and text.endswith("];")
        assert f'fontname="{theme.FONT_FAMILY_PRIMARY}"' in text
    prelude = theme.dot_defaults_prelude(skip=frozenset({"node"}))
    assert "graph [" in prelude and "edge [" in prelude and "node [" not in prelude
    assert theme.MATPLOTLIB_RC["svg.fonttype"] == "none"
    assert set(theme.MATPLOTLIB_RC["font.sans-serif"]) <= theme.FONT_ALLOWED


def test_matplotlib_rc_is_accepted_by_matplotlib():
    matplotlib = pytest.importorskip("matplotlib")
    # `rc_context` valida chiavi e valori: un rcParam sconosciuto o un
    # valore malformato solleva qui, non al primo render in produzione.
    with matplotlib.rc_context(theme.MATPLOTLIB_RC):
        assert matplotlib.rcParams["svg.fonttype"] == "none"
        colors = [c["color"] for c in matplotlib.rcParams["axes.prop_cycle"]]
        assert [c.upper() for c in colors] == list(theme.PALETTE)


# ---------------------------------------------------------------------------
# Didascalia calcolata di `function`
# ---------------------------------------------------------------------------

_COMPUTED = {
    "approximate": False,
    "latex": [r"\frac{x^{2} - 1}{x - 2}"],
    "zeros": [{"x": -1.0, "exact": "-1"}, {"x": 1.0, "exact": "1"}],
    "critical_points": [
        {"x": 0.2679, "y": 0.5359, "exact_x": r"2 - \sqrt{3}", "exact_y": None, "type": "max"},
        {"x": 3.7321, "y": 7.4641, "exact_x": r"2 + \sqrt{3}", "exact_y": None, "type": "min"},
    ],
    "inflection_points": [],
    "asymptotes": [
        {"kind": "vertical", "expr": "x = 2", "latex": "x = 2"},
        {"kind": "oblique", "expr": "y = x + 2", "latex": "y = x + 2"},
    ],
    "discontinuities": [2.0],
    "integral": {"between": [0, 1], "value": 0.33333, "exact": r"\frac{1}{3}"},
    "tangents": [{"at": 3.0, "slope": 0.0, "exact_slope": "0"}],
    "levels": None,
    "warnings": [],
}


def test_function_caption_it_and_en():
    it = theme.function_caption(_COMPUTED, "it")
    assert it == (
        "Zeri in x = −1, 1. "
        "Punti critici in x = 2 − √3, 2 + √3. "
        "Asintoto verticale x = 2. "
        "Asintoto obliquo y = x + 2. "
        "Integrale su [0, 1] pari a 1/3. "
        "Tangente in x = 3 con pendenza 0."
    )
    en = theme.function_caption(_COMPUTED, "en")
    assert en.startswith("Zeros at x = −1, 1. Critical points at x = 2 − √3, 2 + √3.")
    assert "Oblique asymptote y = x + 2." in en
    # Lingua non coperta → italiano (A4).
    assert theme.function_caption(_COMPUTED, "de") == it


def test_function_caption_approximate_none_and_empty():
    assert theme.function_caption(None, "it") == ""
    assert theme.function_caption({}, "it") == ""
    approx = {"approximate": True, "zeros": [{"x": 1.41421356, "exact": None}], "asymptotes": []}
    assert theme.function_caption(approx, "it") == "Zeri in x = 1.414. Valori approssimati."
    nothing = {"approximate": False, "zeros": [], "critical_points": [], "asymptotes": []}
    assert theme.function_caption(nothing, "en") == "No notable points in the considered domain."
    # Senza analisi (es. `family`) e senza risultati: nessuna coda.
    assert theme.function_caption({"approximate": False, "latex": ["a x"]}, "it") == ""


def test_latex_to_unicode_and_format_number():
    assert theme.latex_to_unicode(r"2 - \sqrt{3}") == "2 − √3"
    assert theme.latex_to_unicode(r"\frac{\pi}{2}") == "π/2"
    assert theme.latex_to_unicode(r"\sqrt{x + 1}") == "√(x + 1)"
    assert theme.latex_to_unicode(r"\ln{2}") == "ln2"
    assert theme.format_number(2.0) == "2"
    assert theme.format_number(-0.0001) == "0"
    assert theme.format_number(-2.7182818) == "−2.718"
    assert theme.format_number(float("inf")) == "∞"
    # Giro 2 di WP7 (cambiamento dichiarato): da 1e6 in modulo la notazione
    # è scientifica con 3 cifre significative ed esponente in apice, così
    # tick, didascalie e rette asintotiche non superano mai una quindicina
    # di caratteri (`(10**12)**12*x` produceva numeri di 145 cifre).
    assert theme.format_number(1e144) == "1×10¹⁴⁴"
    assert theme.format_number(123456789.0) == "1.23×10⁸"
    assert theme.format_number(-2.5e6) == "−2.5×10⁶"
    assert theme.format_number(999999.0) == "999999"
    assert theme.format_number(1e6) == "1×10⁶"
    # Sotto 1e-3 solo su richiesta (tick degli assi sui domini stretti): nelle
    # didascalie i valori approssimati restano a 3 decimali.
    assert theme.format_number(0.0002) == "0"
    assert theme.format_number(0.0002, scientific_small=True) == "2×10⁻⁴"
    assert theme.format_number(-0.0001, scientific_small=True) == "−1×10⁻⁴"
    assert theme.format_number(0.0, scientific_small=True) == "0"
    assert theme.format_number(0.5, scientific_small=True) == "0.5"


def test_function_caption_uses_the_spec_variable():
    """`computed["variable"]` entra nelle frasi con «x =» (zeri, punti
    critici, flessi, tangente); assente o vuota → «x»."""
    computed = {
        "approximate": True,
        "variable": "t",
        "zeros": [{"x": 1.0, "exact": "1"}],
        "critical_points": [{"x": 0.5, "y": 1.0, "exact_x": None}],
        "inflection_points": [{"x": 2.0, "exact_x": "2"}],
        "tangents": [{"at": 3.0, "slope": 1.0, "exact_slope": "1"}],
    }
    assert theme.function_caption(computed, "it") == (
        "Zeri in t = 1. Punti critici in t = 0.5. Flessi in t = 2. "
        "Tangente in t = 3 con pendenza 1. Valori approssimati."
    )
    assert theme.function_caption(computed, "en") == (
        "Zeros at t = 1. Critical points at t = 0.5. Inflection points at t = 2. "
        "Tangent at t = 3 with slope 1. Approximate values."
    )
    assert theme.function_caption({"variable": "", "zeros": [{"x": 0, "exact": "0"}]}, "it") == (
        "Zeri in x = 0."
    )
    assert theme.function_caption({"variable": 3, "zeros": [{"x": 0, "exact": "0"}]}, "en") == (
        "Zeros at x = 0."
    )


# ---------------------------------------------------------------------------
# Alias `VisualAssetFormat` condiviso Fase 3 / Fase 4 (D1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["mermaid", "vegalite", "dot", "function", "image", "description"])
def test_visual_asset_format_shared_by_content_and_slides(fmt: str):
    content = LessonContentVisualAsset(asset_id="A1", format=fmt, content="x")
    slide = LessonSlideNewAsset(asset_id="A1_new", format=fmt, content="x")
    assert content.format == slide.format == fmt


def test_visual_asset_format_rejects_unknown():
    with pytest.raises(ValidationError):
        LessonContentVisualAsset(asset_id="A1", format="tikz", content="x")
    with pytest.raises(ValidationError):
        LessonSlideNewAsset(asset_id="A1", format="tikz", content="x")


# ---------------------------------------------------------------------------
# Tema Mermaid: le variabili derivate del tema neutral sono fissate (D3)
# ---------------------------------------------------------------------------

_TINTS = {"#E8F1F8", "#FBEFD9", "#E5F4EF", "#FAF3F7", "#FBF7E4"}
_NEUTRALS = {
    theme.COLOR_INK,
    theme.COLOR_AXIS,
    theme.COLOR_GRID,
    theme.COLOR_SURFACE,
    theme.COLOR_WHITE,
}
# Variabili che i `getStyles` dei tipi D8 leggono e che il tema neutral NON
# ricava da `primaryColor` (verificato su Mermaid 11.14 e 11.17).
_DERIVED_KEYS = {
    "mainBkg": "#E8F1F8",
    "nodeBorder": theme.PALETTE[0],
    "border1": theme.PALETTE[0],
    "clusterBkg": theme.COLOR_SURFACE,
    "clusterBorder": theme.COLOR_AXIS,
    "edgeLabelBackground": theme.COLOR_WHITE,
    "actorBkg": "#E8F1F8",
    "actorBorder": theme.PALETTE[0],
    "signalColor": theme.COLOR_AXIS,
    "labelBoxBkgColor": "#E8F1F8",
    "labelBoxBorderColor": theme.PALETTE[0],
    "classText": theme.COLOR_INK,
    "transitionColor": theme.COLOR_AXIS,
    "stateBkg": "#E8F1F8",
    "stateBorder": theme.PALETTE[0],
    "sectionBkgColor": "#E8F1F8",
    "taskBkgColor": theme.PALETTE[0],
    "taskBorderColor": theme.PALETTE[0],
    "vertLineColor": theme.PALETTE[1],
    "git0": theme.PALETTE[0],
    "gitBranchLabel0": theme.COLOR_WHITE,
}


def test_mermaid_theme_variables_follow_palette():
    cfg = theme.mermaid_config(use_max_width=True)
    tv = cfg["themeVariables"]
    for key, value in _DERIVED_KEYS.items():
        assert tv.get(key) == value, key
    for i in range(12):
        assert tv[f"cScale{i}"] == theme.PALETTE[i % 8]
        assert tv[f"cScaleLabel{i}"] == theme.PALETTE_LABEL[i % 8]
        assert tv[f"pie{i + 1}"] == theme.PALETTE[i % 8]
    assert tv["useGradient"] is False and tv["dropShadow"] == "none"
    # Senza `pieOpacity` Mermaid 11 disegna le fette a 0.7: colori diversi
    # dalla palette e dai riquadri della legenda (TIP-7).
    assert tv["pieOpacity"] == "1"
    assert tv["xyChart"]["plotColorPalette"] == ", ".join(theme.PALETTE)
    assert tv["radar"] == {"axisColor": theme.COLOR_AXIS, "graticuleColor": theme.COLOR_GRID}
    # Ogni colore del tema appartiene alla palette, alle sue tinte o ai neutri.
    allowed = set(theme.PALETTE) | _TINTS | _NEUTRALS
    for key, value in tv.items():
        values = value.values() if isinstance(value, dict) else [value]
        for v in values:
            if isinstance(v, str) and v.startswith("#"):
                for color in v.split(", "):  # `plotColorPalette` è una lista CSV
                    assert color in allowed, (key, v)
    # Sankey: i link seguono il nodo sorgente (nessun gradiente, D3).
    assert cfg["sankey"] == {"linkColor": "source", "useMaxWidth": True}
    assert theme.THEME_VERSION == "2026.09.3"


def test_figure_theme_ts_mirror_is_aligned():
    """Guardia contro la deriva della copia frontend: stessa versione, stessa
    palette, ogni chiave di `themeVariables` presente in figureTheme.ts."""
    ts_path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "figureTheme.ts"
    if not ts_path.is_file():
        pytest.skip("frontend/src/lib/figureTheme.ts non presente")
    ts = ts_path.read_text(encoding="utf-8")
    assert f'THEME_VERSION = "{theme.THEME_VERSION}"' in ts
    assert re.search(r"MANTENERE ALLINEATO", ts)
    palette_in_ts = re.findall(r'"(#[0-9A-Fa-f]{6})",\s*//', ts.split("as const", 1)[0])
    assert palette_in_ts == list(theme.PALETTE)
    tv = theme.mermaid_config(use_max_width=True)["themeVariables"]
    generated = re.compile(r"^(?:cScale(?:Label|Inv)?|pie|git(?:BranchLabel)?)\d+$")
    for key, value in tv.items():
        if generated.match(key):
            continue
        assert re.search(rf"^\s*{re.escape(key)}:", ts, re.M), key
        if isinstance(value, dict):
            for sub in value:
                assert re.search(rf"\b{re.escape(sub)}:", ts), (key, sub)
    for needle in ("`cScale${i}`", "`cScaleLabel${i}`", "`pie${i + 1}`", "`git${i}`"):
        assert needle in ts, needle
    assert 'sankey: { linkColor: "source"' in ts
    assert "useGradient: false" in ts


# ---------------------------------------------------------------------------
# `latex_to_unicode`: forme reali di `sympy.latex` (frazioni annidate, radici)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (r"\frac{\ln{\left(3 \right)}}{2}", "ln(3)/2"),
        (r"\sqrt[3]{2}", "∛2"),
        (r"\sqrt[4]{5}", "∜5"),
        (r"\sqrt[5]{2}", "⁵√2"),
        (r"\frac{1}{x - 2}", "1/(x − 2)"),
        (r"\ln{\left(2 \right)}", "ln(2)"),
        (r"- \sqrt{2}", "−√2"),
        (r"- \frac{1}{2} + \frac{\sqrt{5}}{2}", "−1/2 + √5/2"),
        (r"\frac{-1 + \sqrt{5}}{2}", "(−1 + √5)/2"),
        (r"\frac{x^{2} - 1}{x - 2}", "(x² − 1)/(x − 2)"),
        (r"\frac{1}{2 \pi}", "1/(2π)"),
        (r"\frac{3 \pi}{2}", "3π/2"),
        (r"\frac{\sqrt{2}}{2}", "√2/2"),
        (r"\frac{1}{\sqrt{2}}", "1/√2"),
        (r"\frac{1}{e^{2}}", "1/e²"),
        (r"\frac{\ln{\left(x \right)}}{\ln{\left(2 \right)}}", "ln(x)/ln(2)"),
        (r"\frac{\frac{1}{2}}{\frac{3}{4}}", "(1/2)/(3/4)"),
        (r"\frac{2}{3} + \ln{\left(2 \right)}", "2/3 + ln(2)"),
        (r"2 \sqrt{3}", "2√3"),
        (r"\sqrt{x + 1}", "√(x + 1)"),
        (r"\sqrt{\sqrt{2}}", "√(√2)"),
        (r"e^{2}", "e²"),
        (r"e^{-2}", "e⁻²"),
        (r"e^{2 x}", "e^(2x)"),
        (r"2^{x}", "2^x"),
        (r"\operatorname{atan}{\left(x \right)}", "atan(x)"),
        (r"\left|{x - 1}\right|", "|x − 1|"),
        (r"y = 2 x - 1", "y = 2x − 1"),
        (r"\infty", "∞"),
        (r"-\infty", "−∞"),
        (r"a \cdot b \times c \pm d", "a · b × c ± d"),
        # Input malformati: mai un'eccezione, testo comunque leggibile.
        (r"\frac{1}{x - 2", "1/(x − 2)"),
        # Uguaglianze con secondo membro negativo (asintoti verticali del
        # formato `function`): lo spazio dopo «=» resta.
        (r"x = - \frac{\pi}{2}", "x = −π/2"),
        (r"y = - 2 x + 1", "y = −2x + 1"),
        (r"\foo{bar}", "foobar"),
        ("", ""),
    ],
)
def test_latex_to_unicode_nested_forms(latex: str, expected: str):
    assert theme.latex_to_unicode(latex) == expected


def test_latex_to_unicode_matches_real_sympy_output():
    sp = pytest.importorskip("sympy")
    x = sp.Symbol("x", real=True)
    cases = {
        sp.log(3) / 2: "ln(3)/2",
        sp.cbrt(2): "∛2",
        1 / (x - 2): "1/(x − 2)",
        -sp.sqrt(2): "−√2",
        (-1 + sp.sqrt(5)) / 2: "−1/2 + √5/2",
        sp.pi / 4: "π/4",
        sp.E**2: "e²",
        sp.Rational(-1, 3): "−1/3",
        sp.Eq(sp.Symbol("y"), x + 2): "y = x + 2",
    }
    for value, expected in cases.items():
        latex = sp.latex(value, ln_notation=True, fold_short_frac=False)
        assert theme.latex_to_unicode(latex) == expected, latex


def test_function_caption_ignores_malformed_entries():
    # Valori non numerici, booleani o liste al posto di mappe: nessuna
    # eccezione e nessun numero inventato.
    assert theme.function_caption({"integral": {"between": ["a", "b"], "value": 1}}, "it") == ""
    assert theme.function_caption({"integral": {"between": [0, None], "value": 1}}, "it") == ""
    assert theme.function_caption({"zeros": "x", "asymptotes": {"kind": "v"}}, "it") == ""
    assert theme.function_caption({"zeros": [{"x": True}]}, "it") == "Zeri in x = n.d.."
    assert theme.function_caption({"tangents": [{"at": "3", "slope": 1}]}, "it") == ""
    assert theme.function_caption({"levels": [float("nan"), "z", 2]}, "it") == (
        "Curve di livello per z = n.d., 2."
    )
    assert theme.function_caption([1, 2, 3], "it") == ""  # type: ignore[arg-type]
