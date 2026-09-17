"""`figure_scale` (D10, D11): banda di leggibilità e larghezza delle figure.

- casi della fixture condivisa `tests/fixtures/figure_scale_cases.json`
  (specchio di `lib/figureFormats.ts`: `fitFigureWidthMm`, `svgIntrinsicBox`,
  `formatMm`), eseguiti in Python e, con Node (`--experimental-strip-types`),
  sulla copia frontend: il runner Node FALLISCE (non salta) finché la copia
  frontend non espone le tre funzioni;
- invarianti property-based del fit: mai oltre il box (larghezza per
  difetto al centesimo), mai sopra il tetto della banda, ingrandimento
  solo per i fluidi o per raggiungere il fondo della banda, fuori banda
  solo quando il box è il vincolo attivo;
- costanti di fallback derivate dal tema (`THEME_VERSION` invariato).
"""

from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.services import figure_scale as fs
from app.services import figure_theme as theme
from app.services.svg_normalize import svg_base_font_px, svg_intrinsic_box

_FIXTURE = Path(__file__).parent / "fixtures" / "figure_scale_cases.json"
_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_FRONTEND_MODULE = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "figureFormats.ts"
)
_ABS = 1e-9


def _fit_kwargs(inp: dict[str, Any]) -> dict[str, Any]:
    return {
        "vb_w": inp["vb_w"],
        "vb_h": inp["vb_h"],
        "base_font_px": inp["base_font_px"],
        "box_w_mm": inp["box_w_mm"],
        "box_h_mm": inp["box_h_mm"],
        "variant": inp["variant"],
        "intrinsic_w_px": inp["intrinsic_w_px"],
    }


def _assert_close(got: Any, expected: Any, label: str) -> None:
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        assert got == expected, label
    else:
        assert got == pytest.approx(expected, abs=_ABS), label


# ---------------------------------------------------------------------------
# Fixture: fit, svg_box, svg_font, format_mm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _DATA["fit"], ids=[c["name"][:60] for c in _DATA["fit"]])
def test_fit_fixture_case(case: dict[str, Any]) -> None:
    fit = fs.fit_figure_width_mm(**_fit_kwargs(case["input"]))
    expected = case["expected"]
    if expected is None:
        assert fit is None, case["name"]
        return
    assert fit is not None, case["name"]
    assert fit.width_mm == pytest.approx(expected["width_mm"], abs=_ABS), case["name"]
    assert fit.scale == pytest.approx(expected["scale"], abs=_ABS), case["name"]
    assert fit.text_pt == pytest.approx(expected["text_pt"], abs=_ABS), case["name"]
    assert fit.in_band is expected["in_band"], case["name"]
    # La larghezza è arrotondata per difetto: mai oltre il box.
    box_w = case["input"]["box_w_mm"]
    if box_w is not None:
        assert fit.width_mm <= box_w


@pytest.mark.parametrize("case", _DATA["svg_box"], ids=[c["name"][:60] for c in _DATA["svg_box"]])
def test_svg_box_fixture_case(case: dict[str, Any]) -> None:
    box = svg_intrinsic_box(case["svg"])
    expected = case["expected"]
    if expected is None:
        assert box is None, case["name"]
        return
    assert box is not None, case["name"]
    for key, value in expected.items():
        _assert_close(getattr(box, key), value, f"{case['name']}: {key}")


@pytest.mark.parametrize("case", _DATA["svg_font"], ids=[c["name"][:60] for c in _DATA["svg_font"]])
def test_svg_font_fixture_case(case: dict[str, Any]) -> None:
    metrics = svg_base_font_px(case["svg"])
    for key, value in case["expected"].items():
        _assert_close(getattr(metrics, key), value, f"{case['name']}: {key}")


@pytest.mark.parametrize("case", _DATA["format_mm"])
def test_format_mm_fixture_case(case: dict[str, Any]) -> None:
    assert fs.format_mm(case["input"]) == case["output"]


def test_fit_rejects_unknown_variant() -> None:
    with pytest.raises(ValueError, match="variant"):
        fs.fit_figure_width_mm(
            vb_w=10, vb_h=10, base_font_px=14, box_w_mm=100, box_h_mm=100, variant="web"
        )


def test_nan_base_font_is_treated_as_no_text() -> None:
    fit = fs.fit_figure_width_mm(
        vb_w=100, vb_h=100, base_font_px=math.nan, box_w_mm=168, box_h_mm=242
    )
    assert fit == fs.FigureFit(width_mm=26.45, scale=1.0, text_pt=0.0, in_band=True)
    assert (
        fs.fit_figure_width_mm(
            vb_w=100, vb_h=100, base_font_px=math.inf, box_w_mm=168, box_h_mm=242
        )
        == fit
    )


def test_resolve_base_font_px_sources() -> None:
    measured = fs.SvgMetrics(16.0, 16.0, 6, "measured")
    assert fs.resolve_base_font_px("mermaid", measured) == (16.0, "measured")
    parsed = fs.SvgMetrics(11.0, 11.0, 9, "parsed")
    assert fs.resolve_base_font_px("vegalite", parsed) == (11.0, "parsed")
    root = fs.SvgMetrics(14.0, 14.0, 6, "root_rule")
    assert fs.resolve_base_font_px("mermaid", root) == (14.0, "root_rule")
    no_text = fs.SvgMetrics(None, None, 0, "no_text")
    assert fs.resolve_base_font_px("mermaid", no_text) == (None, "no_text")
    unresolved = fs.SvgMetrics(None, None, 3, "unresolved")
    assert fs.resolve_base_font_px("mermaid", unresolved) == (14.0, "constant")
    assert fs.resolve_base_font_px("dot", None) == (pytest.approx(40 / 3), "constant")
    assert fs.resolve_base_font_px("function", None) == (12.0, "constant")
    assert fs.resolve_base_font_px("image", None) == (None, "no_text")


# ---------------------------------------------------------------------------
# Invarianti property-based
# ---------------------------------------------------------------------------


def test_fit_invariants_property_based() -> None:
    rng = random.Random(20260916)
    for _ in range(2000):
        vb_w = rng.uniform(1, 3000)
        vb_h = rng.uniform(1, 3000)
        base = None if rng.random() < 0.15 else rng.uniform(1, 60)
        box_w = None if rng.random() < 0.2 else rng.uniform(5, 400)
        box_h = None if rng.random() < 0.2 else rng.uniform(5, 400)
        variant = rng.choice(["lesson", "slide"])
        intrinsic = None if rng.random() < 0.5 else rng.uniform(1, 3000)
        fit = fs.fit_figure_width_mm(
            vb_w=vb_w,
            vb_h=vb_h,
            base_font_px=base,
            box_w_mm=box_w,
            box_h_mm=box_h,
            variant=variant,
            intrinsic_w_px=intrinsic,
        )
        assert fit is not None
        lo, hi = fs.READABILITY_BANDS_PT[variant]
        fluid = intrinsic is None
        ref_w = (vb_w if intrinsic is None else intrinsic) * fs.MM_PER_PX
        ref_h = ref_w * vb_h / vb_w
        s_box = math.inf
        if box_w is not None:
            s_box = min(s_box, box_w / ref_w)
        if box_h is not None:
            s_box = min(s_box, box_h / ref_h)
        # Mai oltre il box, in larghezza e in altezza (floor al centesimo).
        if box_w is not None:
            assert fit.width_mm <= box_w
        if box_h is not None:
            assert fit.width_mm * vb_h / vb_w <= box_h + 1e-9
        assert fit.width_mm == math.floor(fit.width_mm * 100 + 1e-6) / 100
        # Mai sopra il tetto della banda.
        assert fit.text_pt <= hi + 1e-9
        if base is None:
            assert fit.text_pt == 0.0 and fit.in_band is True and fit.scale <= 1.0 + 1e-9
            continue
        # Ingrandimento solo per i fluidi o per raggiungere il fondo della banda.
        natural_pt = base * fs.PT_PER_PX
        if fit.scale > 1.0:
            assert fluid or natural_pt < lo
        # Fuori banda solo quando il box è il vincolo attivo.
        if not fit.in_band:
            assert math.isfinite(s_box)
            assert fit.scale == pytest.approx(s_box, abs=5e-5 + 1e-9)


# ---------------------------------------------------------------------------
# Parità con la copia frontend (Node)
# ---------------------------------------------------------------------------

_FRONTEND_RUNNER = """
import * as ff from {module!r};
import {{ readFileSync }} from "node:fs";
const data = JSON.parse(readFileSync({fixture!r}, "utf8"));
const out = {{ fit: [], svg_box: [], format_mm: [] }};
for (const c of data.fit) {{
  const i = c.input;
  out.fit.push(ff.fitFigureWidthMm({{
    vbW: i.vb_w, vbH: i.vb_h, baseFontPx: i.base_font_px, boxWMm: i.box_w_mm,
    boxHMm: i.box_h_mm, variant: i.variant, intrinsicWPx: i.intrinsic_w_px,
  }}));
}}
for (const c of data.svg_box) out.svg_box.push(ff.svgIntrinsicBox(c.svg));
for (const c of data.format_mm) out.format_mm.push(ff.formatMm(c.input));
process.stdout.write(JSON.stringify(out));
"""

_FIT_KEYS = {"width_mm": "widthMm", "scale": "scale", "text_pt": "textPt", "in_band": "inBand"}
_BOX_KEYS = {
    "vb_w": "vbW",
    "vb_h": "vbH",
    "width_px": "widthPx",
    "height_px": "heightPx",
    "px_per_unit": "pxPerUnit",
}


def test_frontend_copy_matches_fixture() -> None:
    """La copia frontend produce gli stessi numeri della fixture, campo per
    campo. Fallisce (non salta) finché `figureFormats.ts` non espone
    `fitFigureWidthMm`, `svgIntrinsicBox` e `formatMm`."""
    node = shutil.which("node")
    assert node is not None, "node non disponibile: parità frontend non verificabile"
    assert _FRONTEND_MODULE.is_file(), f"modulo frontend assente: {_FRONTEND_MODULE}"
    script = _FRONTEND_RUNNER.format(module=str(_FRONTEND_MODULE), fixture=str(_FIXTURE))
    proc = subprocess.run(
        [node, "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    for case, fe in zip(_DATA["fit"], got["fit"], strict=True):
        expected = case["expected"]
        if expected is None:
            assert fe is None, case["name"]
            continue
        assert fe is not None, case["name"]
        for py_key, ts_key in _FIT_KEYS.items():
            _assert_close(fe[ts_key], expected[py_key], f"{case['name']}: {py_key}")
    for case, fe in zip(_DATA["svg_box"], got["svg_box"], strict=True):
        expected = case["expected"]
        if expected is None:
            assert fe is None, case["name"]
            continue
        assert fe is not None, case["name"]
        for py_key, ts_key in _BOX_KEYS.items():
            _assert_close(fe[ts_key], expected[py_key], f"{case['name']}: {py_key}")
    for case, fe in zip(_DATA["format_mm"], got["format_mm"], strict=True):
        assert fe == case["output"], case


# ---------------------------------------------------------------------------
# Costanti di fallback derivate dal tema
# ---------------------------------------------------------------------------


def test_fallback_constants_derive_from_the_theme() -> None:
    """Nessuna costante nuova nel tema: i fallback sono letti da
    `figure_theme` (Mermaid 14px, Vega-Lite `labelFontSize` 11, DOT
    `edge fontsize=10` pt → 40/3 px, matplotlib `xtick.labelsize` 9 pt →
    12 px) e `THEME_VERSION` non cambia."""
    variables = theme.mermaid_config(use_max_width=True)["themeVariables"]
    mermaid_px = float(variables["fontSize"][:-2])
    assert fs.FALLBACK_BASE_FONT_PX["mermaid"] == mermaid_px == 14.0
    vegalite_px = theme.VEGALITE_THEME_CONFIG["axis"]["labelFontSize"]
    assert fs.FALLBACK_BASE_FONT_PX["vegalite"] == vegalite_px == 11
    assert "fontsize=10" in theme.DOT_DEFAULTS["edge"]
    assert fs.FALLBACK_BASE_FONT_PX["dot"] == pytest.approx(10 * 4 / 3)
    assert fs.FALLBACK_BASE_FONT_PX["function"] == theme.MATPLOTLIB_RC["xtick.labelsize"] * 4 / 3
    assert theme.THEME_VERSION == "2026.09.4"
    assert fs.READABILITY_BANDS_PT == {"lesson": (8.0, 11.0), "slide": (10.0, 14.0)}
    assert fs.MM_PER_PX == 25.4 / 96 and fs.PT_PER_PX == 0.75
