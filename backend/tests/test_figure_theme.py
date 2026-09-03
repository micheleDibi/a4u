"""Fondamenta delle figure accademiche (WP2a): tema, etichette, alias formato.

Test puri (nessun DB, nessun binario): `figure_theme` è un modulo leaf e
deve restare importabile senza config, SQLAlchemy o librerie pesanti.
"""

from __future__ import annotations

import json

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
            "critical_points",
            "inflection_points",
            "asymptote_vertical",
            "asymptote_horizontal",
            "asymptote_oblique",
            "integral",
            "tangent",
            "levels",
            "none",
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
    assert theme.VEGALITE_THEME_CONFIG["view"]["stroke"] is None
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
