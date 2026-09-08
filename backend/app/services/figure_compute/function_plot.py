"""Disegno del formato `function` con matplotlib (API a oggetti, in thread).

Deterministico: `rc_context(MATPLOTLIB_RC | {"svg.hashsalt": "a4u:<hash>"})`,
`Figure` + `FigureCanvasAgg` (nessun `pyplot`),
`savefig(format="svg", metadata={"Date": None, "Creator": None})`; due
render della stessa spec sono byte-identici. `rc_context` modifica
`matplotlib.rcParams`, che è GLOBALE al processo: il blocco
`rc_context … savefig` è serializzato da un `threading.Lock` di modulo
(`_DRAW_LOCK`), altrimenti due render concorrenti in thread si
scambierebbero `svg.hashsalt` e gli altri parametri a metà disegno e
lascerebbero rcParams inquinati. Il disegno dura 0,2-0,5 s: la
serializzazione è accettabile.

Il numero di punti notevoli disegnati è limitato per categoria a
`function_numeric.MAX_NOTABLE_POINTS` (il calcolo li ha già troncati; qui
è una difesa in profondità): il costo del disegno — un `TextPath` e un
parse mathtext per etichetta — resta bounded dai limiti della spec (A13).
Anche la formula è bounded: il testo (LaTeX del figlio ≤ 160 caratteri,
altrimenti l'AST dell'espressione ≤ 200) viene misurato con `TextPath`
contro la larghezza degli assi; se eccede, il corpo scende fino a
`MIN_MATH_SIZE_PT` e, se ancora non entra, si prova il candidato
successivo (LaTeX sympy → mathtext dall'AST → testo tondo); quando nessuno
entra resta il solo nome `f(x)` con l'avvertenza `formula_too_wide`. I
numeri (tick, etichette, coefficienti) usano la notazione scientifica da
1e6 in modulo (i tick e le costanti dell'AST anche sotto 1e-3).

Testo matematico come geometria (A14): formula, coordinate esatte e ogni
stringa mathtext sono disegnate con `TextPath` + `PathPatch` (font DejaVu
Sans bundled da matplotlib: nessuna dipendenza da STIX o «DejaVu Sans
Display», assenti nel container e nel browser). Il resto del testo (tick,
legenda, nomi degli assi, valori approssimati) resta `<text>` con la
famiglia del tema (`svg.fonttype: none`). Mai raster: `contour` e
`fill_between` sono path, `imshow` non è usato, quindi l'SVG non contiene
`<image>` (che `svg_normalize` rifiuta).

I `gid` diventano `id=` nell'SVG e sono i ganci dei test: `formula`,
`branch-{i}-{j}`, `zero-{k}`, `critical-{k}`, `inflection-{k}`,
`asymptote-{k}`, `discontinuity-{k}`, `tangent-{k}`, `area-{k}`,
`point-{k}`, `levels`.
"""

from __future__ import annotations

import ast
import io
import re
import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from app.services.figure_compute.function_numeric import MAX_NOTABLE_POINTS, NumericStudy
from app.services.figure_theme import (
    COLOR_AXIS,
    COLOR_GRID,
    COLOR_INK,
    COLOR_MUTED,
    MATPLOTLIB_RC,
    PALETTE,
    SCIENTIFIC_MAX_ABS,
    SCIENTIFIC_MIN_ABS,
    format_number,
)

if TYPE_CHECKING:
    from app.schemas.figure_function import FunctionFigureSpec

FIGSIZE = (5.2, 3.6)
DPI = 200
MARGINS = {"left": 0.08, "right": 0.97, "top": 0.94, "bottom": 0.10}
MATH_SIZE_PT = 9.0
# Corpo minimo della formula ridotta per entrare nella figura.
MIN_MATH_SIZE_PT = 6.5
LABEL_SIZE_PT = 8.0
# La formula è ancorata a destra a questa frazione degli assi e può
# estendersi a sinistra fino a `FORMULA_LEFT_FRACTION`.
FORMULA_ANCHOR_FRACTION = 0.985
FORMULA_LEFT_FRACTION = 0.015
FORMULA_TOO_WIDE = "formula_too_wide"
# Font bundled di matplotlib: la geometria è identica su ogni macchina.
_TEXTPATH_FAMILY = "DejaVu Sans"
# Il testo va SOPRA le curve (i `PathPatch` di matplotlib stanno a zorder 1,
# le linee a 2: senza questo la formula e le coordinate esatte finiscono
# sotto il grafico) e su un alone bianco che lo stacca da ciò che passa
# sotto. L'alone è un tratto sul contorno del `TextPath`, quindi resta
# geometria (A14) e non tocca il resto del testo, che rimane `<text>`.
TEXT_ZORDER = 5.0
HALO_WIDTH_PT = 2.2
# Alone delle etichette `<text>` (tick, coordinate approssimate): un
# riquadro bianco dietro il testo, che così resta testo estraibile.
TEXT_HALO_BBOX: dict[str, Any] = {
    "boxstyle": "square,pad=0.12",
    "facecolor": "white",
    "edgecolor": "none",
    "alpha": 0.82,
}

_MATHTEXT_UNSUPPORTED = ("\\begin{", "\\end{", "\\over")
_MATHTEXT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("\\left.", ""),
    ("\\right.", ""),
    ("\\left", ""),
    ("\\right", ""),
    ("\\tfrac", "\\frac"),
    ("\\dfrac", "\\frac"),
    ("\\lvert", "|"),
    ("\\rvert", "|"),
    ("\\lVert", "\\|"),
    ("\\rVert", "\\|"),
    ("\\displaystyle", ""),
    ("\\operatorname{", "\\mathrm{"),
)
_METADATA_RE = re.compile(r"\s*<metadata>.*?</metadata>", re.DOTALL)
_WS_RE = re.compile(r"\s{2,}")
_SPACE_BEFORE_CLOSE_RE = re.compile(r"\s+([)\]|])")
_SPACE_AFTER_OPEN_RE = re.compile(r"([(\[|])\s+")

# `rcParams` è globale: un solo disegno alla volta per processo.
_DRAW_LOCK = threading.Lock()

# Precedenze della scrittura mathtext dall'AST (`expr_to_mathtext`).
_PREC_ADD = 0
_PREC_MUL = 1
_PREC_UNARY = 2
_PREC_POW = 3
_PREC_ATOM = 4
_MATHTEXT_FUNCTIONS = {
    "sin": r"\sin",
    "cos": r"\cos",
    "tan": r"\tan",
    "log": r"\ln",
    "sinh": r"\sinh",
    "cosh": r"\cosh",
    "tanh": r"\tanh",
    "asin": r"\arcsin",
    "acos": r"\arccos",
    "atan": r"\arctan",
}


def to_mathtext(latex: str) -> str | None:
    """Testo mathtext (senza `$`) per una stringa LaTeX di sympy, `None` se
    mathtext non la può rendere (`\\begin{…}`, `\\over` o errore del
    parser nella prova preventiva)."""
    text = (latex or "").strip()
    if not text or any(marker in text for marker in _MATHTEXT_UNSUPPORTED):
        return None
    for old, new in _MATHTEXT_REPLACEMENTS:
        text = text.replace(old, new)
    text = _WS_RE.sub(" ", text).strip()
    # `\left( x \right)` → `( x )`: gli spazi accanto ai delimitatori sono
    # residui di `\left`/`\right`.
    text = _SPACE_BEFORE_CLOSE_RE.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN_RE.sub(r"\1", text)
    if not mathtext_parses("$" + text + "$"):
        return None
    return text


def mathtext_parses(s: str) -> bool:
    from matplotlib.mathtext import MathTextParser

    try:
        MathTextParser("path").parse(s)
    except Exception:  # ValueError del parser, ma anche errori di font
        return False
    return True


def _escape_text(s: str) -> str:
    """Testo tondo dentro una stringa mixed text/mathtext: niente `$`."""
    return s.replace("$", "")


def _number_mathtext(value: float) -> str:
    """Costante dell'AST in mathtext: intero, decimale a 6 cifre
    significative o, fuori da [1e-3, 1e6) in modulo, `1.5 \\cdot 10^{7}`
    (mai «1e+07» né 145 cifre)."""
    magnitude = abs(float(value))
    if magnitude >= SCIENTIFIC_MAX_ABS or 0.0 < magnitude < SCIENTIFIC_MIN_ABS:
        mantissa, _, exponent = f"{value:.5e}".partition("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return rf"{mantissa} \cdot 10^{{{int(exponent)}}}"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6g}"


def _wrap(text: str, prec: int, minimum: int) -> str:
    return f"({text})" if prec < minimum else text


def _node_mathtext(node: ast.AST) -> tuple[str, int]:
    """`(mathtext, precedenza)` di un nodo dell'AST già accettato dal
    passo 1 (`function_parse`); solleva `ValueError` su nodi imprevisti."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        text = _number_mathtext(float(node.value))
        if text.startswith("-"):
            return (text, _PREC_UNARY)
        return (text, _PREC_MUL if r"\cdot" in text else _PREC_ATOM)
    if isinstance(node, ast.Name):
        if node.id == "pi":
            return (r"\pi", _PREC_ATOM)
        return ("e" if node.id == "E" else node.id, _PREC_ATOM)
    if isinstance(node, ast.UnaryOp):
        inner, prec = _node_mathtext(node.operand)
        sign = "-" if isinstance(node.op, ast.USub) else ""
        return (sign + _wrap(inner, prec, _PREC_UNARY), _PREC_UNARY)
    if isinstance(node, ast.BinOp):
        left, lp = _node_mathtext(node.left)
        right, rp = _node_mathtext(node.right)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            # Il secondo addendo è parentesizzato se additivo o negativo
            # (`x - (-2)`, `x + (y - 1)`).
            minimum = _PREC_POW if right.startswith("-") else _PREC_MUL
            sign = "+" if isinstance(node.op, ast.Add) else "-"
            return (f"{left} {sign} {_wrap(right, rp, minimum)}", _PREC_ADD)
        if isinstance(node.op, ast.Mult):
            left, right = _wrap(left, lp, _PREC_MUL), _wrap(right, rp, _PREC_MUL)
            joiner = r" \cdot " if right[:1].isdigit() or right[:1] == "-" else r"\,"
            return (f"{left}{joiner}{right}", _PREC_MUL)
        if isinstance(node.op, ast.Div):
            return (rf"\frac{{{left}}}{{{right}}}", _PREC_ATOM)
        if isinstance(node.op, ast.Pow):
            return (f"{_wrap(left, lp, _PREC_ATOM)}^{{{right}}}", _PREC_POW)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        args = [_node_mathtext(a)[0] for a in node.args]
        name = node.func.id
        if name == "sqrt":
            return (rf"\sqrt{{{args[0]}}}", _PREC_ATOM)
        if name == "abs":
            return (f"|{args[0]}|", _PREC_ATOM)
        if name == "exp":
            return (f"e^{{{args[0]}}}", _PREC_POW)
        if name == "floor":
            return (rf"\lfloor {args[0]} \rfloor", _PREC_ATOM)
        if name == "log" and len(args) == 2:
            return (rf"\log_{{{args[1]}}}({args[0]})", _PREC_ATOM)
        if name in _MATHTEXT_FUNCTIONS:
            return (f"{_MATHTEXT_FUNCTIONS[name]}({args[0]})", _PREC_ATOM)
    raise ValueError(f"nodo non convertibile: {type(node).__name__}")


def expr_to_mathtext(source: str) -> str | None:
    """Mathtext (senza `$`) scritto direttamente dall'AST dell'espressione
    (`x**2 - 2` → `x^{2} - 2`, `(x**2-1)/(x-2)` → `\\frac{x^{2} - 1}{x - 2}`):
    ripiego della formula quando il figlio sympy non ha fornito il LaTeX
    (timeout, errore, sympy assente). `None` se la conversione o la prova
    preventiva di mathtext falliscono."""
    try:
        tree = ast.parse((source or "").strip(), mode="eval")
        text, _prec = _node_mathtext(tree.body)
    except (SyntaxError, ValueError, RecursionError, IndexError):
        return None
    return text if mathtext_parses("$" + text + "$") else None


class _Canvas:
    """Stato del disegno (figura, assi, contatori dei gid)."""

    def __init__(self, fig: Any, ax: Any) -> None:
        self.fig = fig
        self.ax = ax
        self.warnings: list[str] = []

    def axes_width_pt(self) -> float:
        """Larghezza degli assi in punti tipografici."""
        return float(self.ax.get_position().width * self.fig.get_figwidth() * 72.0)

    def draw_math(
        self,
        s: str,
        *,
        anchor: tuple[float, float],
        transform: Any,
        offset_pt: tuple[float, float] = (0.0, 0.0),
        ha: str = "left",
        va: str = "baseline",
        size: float = MATH_SIZE_PT,
        color: str = COLOR_INK,
        gid: str,
    ) -> None:
        """`TextPath` + `PathPatch` ancorati a `anchor` (nel sistema
        `transform`) con spostamento in punti: la geometria resta in
        punti tipografici qualunque sia il dpi del backend."""
        from matplotlib.font_manager import FontProperties
        from matplotlib.patches import PathPatch
        from matplotlib.patheffects import withStroke
        from matplotlib.textpath import TextPath
        from matplotlib.transforms import Affine2D, ScaledTranslation

        path = TextPath((0, 0), s, size=size, prop=FontProperties(family=_TEXTPATH_FAMILY))
        bbox = path.get_extents()
        dx = -bbox.x0
        if ha == "right":
            dx = -bbox.x1
        elif ha == "center":
            dx = -(bbox.x0 + bbox.x1) / 2.0
        dy = 0.0
        if va == "top":
            dy = -bbox.y1
        elif va == "bottom":
            dy = -bbox.y0
        elif va == "center":
            dy = -(bbox.y0 + bbox.y1) / 2.0
        trans = (
            Affine2D().translate(dx + offset_pt[0], dy + offset_pt[1]).scale(1.0 / 72.0)
            + self.fig.dpi_scale_trans
            + ScaledTranslation(anchor[0], anchor[1], transform)
        )
        patch = PathPatch(
            path,
            facecolor=color,
            edgecolor="none",
            linewidth=0,
            transform=trans,
            clip_on=False,
            zorder=TEXT_ZORDER,
        )
        patch.set_path_effects([withStroke(linewidth=HALO_WIDTH_PT, foreground="white")])
        patch.set_gid(gid)
        self.ax.add_patch(patch)

    def draw_label(
        self,
        text: str,
        *,
        is_math: bool,
        xy: tuple[float, float],
        offset_pt: tuple[float, float],
        gid: str,
        ha: str = "left",
        va: str = "bottom",
        color: str = COLOR_INK,
    ) -> None:
        """Etichetta di un punto in coordinate dati: geometria se mathtext,
        `<text>` altrimenti."""
        if is_math:
            self.draw_math(
                text,
                anchor=xy,
                transform=self.ax.transData,
                offset_pt=offset_pt,
                ha=ha,
                va=va,
                size=LABEL_SIZE_PT,
                color=color,
                gid=gid,
            )
            return
        self.ax.annotate(
            text,
            xy=xy,
            xycoords="data",
            xytext=offset_pt,
            textcoords="offset points",
            ha=ha,
            va=va,
            fontsize=LABEL_SIZE_PT,
            color=color,
            gid=gid,
            annotation_clip=False,
            zorder=TEXT_ZORDER,
            bbox=dict(TEXT_HALO_BBOX),
        )


def _value_label(exact: Any, value: float) -> tuple[str, bool]:
    """`(testo, è mathtext)`: forma esatta come mathtext se resa, altrimenti
    il valore approssimato a 3 decimali."""
    if isinstance(exact, str) and exact.strip():
        mt = to_mathtext(exact)
        if mt is not None:
            return (mt, True)
    return (format_number(value), False)


def _pair_label(entry: Mapping[str, Any]) -> tuple[str, bool]:
    x_text, x_math = _value_label(entry.get("exact_x"), float(entry.get("x", 0.0)))
    y_val = entry.get("y")
    if not isinstance(y_val, (int, float)):
        return (f"${x_text}$" if x_math else x_text, x_math)
    y_text, y_math = _value_label(entry.get("exact_y"), float(y_val))
    if x_math or y_math:
        return (f"$({x_text},\\ {y_text})$", True)
    return (f"({x_text}, {y_text})", False)


def _spine_positions(study: NumericStudy) -> tuple[float, float]:
    xlo, xhi = study.xlim
    ylo, yhi = study.ylim
    sx = 0.0 if xlo <= 0.0 <= xhi else xlo
    sy = 0.0 if ylo <= 0.0 <= yhi else ylo
    return sx, sy


def _setup_axes(canvas: _Canvas, study: NumericStudy, *, names: tuple[str, str]) -> None:
    from matplotlib.ticker import FuncFormatter

    ax = canvas.ax
    ax.set_xlim(*study.xlim)
    ax.set_ylim(*study.ylim)
    sx, sy = _spine_positions(study)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_position(("data", sx))
    ax.spines["bottom"].set_position(("data", sy))
    # Tick in notazione scientifica anche sotto 1e-3 (domini stretti,
    # ampiezza minima 1e-3); un tick a 1e-17 per rumore di virgola mobile
    # è «0», non «1×10⁻¹⁷».
    x_eps = 1e-9 * abs(study.xlim[1] - study.xlim[0])
    y_eps = 1e-9 * abs(study.ylim[1] - study.ylim[0])

    def tick(v: float, eps: float) -> str:
        return format_number(0.0 if abs(v) < eps else v, scientific_small=True)

    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: tick(v, x_eps)))
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _p: "" if (sy == 0.0 and abs(v) < y_eps) else tick(v, y_eps))
    )
    ax.tick_params(length=3, pad=2)
    # Con le spine a zero i tick stanno DENTRO l'area dati e matplotlib
    # disegna l'asse sotto le curve: senza questo un ramo che passa per un
    # tick lo cancella. Assi sopra i dati (la griglia delle curve di
    # livello è disegnata a mano, sotto) e riquadro bianco sotto ogni
    # etichetta, che resta `<text>` (TIP-5).
    ax.set_axisbelow(False)
    for label in (*ax.get_xticklabels(), *ax.get_yticklabels()):
        label.set_bbox(dict(TEXT_HALO_BBOX))
    ax.plot(
        [1.0],
        [sy],
        marker=">",
        color=COLOR_AXIS,
        markersize=5,
        linestyle="none",
        transform=ax.get_yaxis_transform(),
        clip_on=False,
        gid="axis-arrow-x",
    )
    ax.plot(
        [sx],
        [1.0],
        marker="^",
        color=COLOR_AXIS,
        markersize=5,
        linestyle="none",
        transform=ax.get_xaxis_transform(),
        clip_on=False,
        gid="axis-arrow-y",
    )
    ax.annotate(
        names[0],
        xy=(1.0, sy),
        xycoords=ax.get_yaxis_transform(),
        # Sopra la freccia, non sotto: l'etichetta del tick dell'estremo
        # destro è centrata sulla fine dell'asse e a `va="top"` i due
        # testi si toccavano, leggendosi come un unico token («8x»).
        xytext=(3, 4),
        textcoords="offset points",
        ha="left",
        va="bottom",
        fontsize=9,
        color=COLOR_INK,
        gid="axis-name-x",
        annotation_clip=False,
    )
    ax.annotate(
        names[1],
        xy=(sx, 1.0),
        xycoords=ax.get_xaxis_transform(),
        xytext=(5, -2),
        textcoords="offset points",
        ha="left",
        va="top",
        fontsize=9,
        color=COLOR_INK,
        gid="axis-name-y",
        annotation_clip=False,
    )


_MATH_MARKUP_RE = re.compile(r"[$\s]")


def _label_plain(text: str) -> str:
    """Etichetta ridotta alla forma confrontabile con un tick: senza `$` né
    spazi e con il segno meno tipografico usato da `format_number`."""
    return _MATH_MARKUP_RE.sub("", text).replace("-", "\u2212")


def _x_tick_conflict(ax: Any, x: float, text: str) -> str:
    """Rapporto fra l'etichetta di un punto notevole in `x` e i tick già
    disegnati: `"duplicate"` se in quel punto c'è un tick con lo STESSO
    testo (`0` sopra `0`), `"shift"` se ce n'è uno abbastanza vicino da
    sovrapporsi (`−π` sopra `−3`), `""` se il posto è libero. Le posizioni
    vengono dal locator, che dipende solo dai limiti già fissati: l'esito
    è deterministico."""
    lo, hi = ax.get_xlim()
    tolerance = 0.025 * abs(hi - lo)
    formatter = ax.xaxis.get_major_formatter()
    conflict = ""
    for i, value in enumerate(ax.get_xticks()):
        if not lo <= value <= hi or abs(value - x) > tolerance:
            continue
        if _label_plain(str(formatter(value, i))) == _label_plain(text):
            return "duplicate"
        conflict = "shift"
    return conflict


def _draw_curves(canvas: _Canvas, study: NumericStudy) -> bool:
    """Rami delle curve; ritorna `True` se serve la legenda."""
    ax = canvas.ax
    labelled = 0
    for curve in study.curves:
        # `index` è l'espressione (studio) o la serie (famiglia): in
        # entrambi i casi numera colore e gid.
        color = PALETTE[curve.index % len(PALETTE)]
        for j, (xs, ys) in enumerate(curve.branches):
            ax.plot(
                xs,
                ys,
                color=color,
                gid=f"branch-{curve.index}-{j}",
                label=curve.label if j == 0 else None,
            )
            if j == 0:
                labelled += 1
    return labelled > 1


def _draw_asymptotes(canvas: _Canvas, computed: Mapping[str, Any]) -> None:
    ax = canvas.ax
    entries = computed.get("asymptotes")
    if not isinstance(entries, list):
        return
    # Verticali ≤ MAX_NOTABLE_POINTS più al massimo due code.
    for k, entry in enumerate(entries[: MAX_NOTABLE_POINTS + 2]):
        kind = entry.get("kind")
        style = {"linestyle": "--", "color": COLOR_MUTED, "linewidth": 0.9}
        if kind == "vertical" and isinstance(entry.get("x"), (int, float)):
            ax.axvline(float(entry["x"]), gid=f"asymptote-{k}", **style)
        elif kind in ("horizontal", "oblique"):
            m, q = entry.get("m"), entry.get("q")
            if isinstance(m, (int, float)) and isinstance(q, (int, float)):
                ax.axline((0.0, float(q)), slope=float(m), gid=f"asymptote-{k}", **style)


def _draw_points(canvas: _Canvas, computed: Mapping[str, Any]) -> None:
    ax = canvas.ax
    zeros = computed.get("zeros")
    if isinstance(zeros, list):
        for k, entry in enumerate(zeros[:MAX_NOTABLE_POINTS]):
            x = float(entry["x"])
            ax.plot([x], [0.0], "o", markersize=4, color=COLOR_INK, gid=f"zero-{k}")
            text, is_math = _value_label(entry.get("exact"), x)
            label = f"${text}$" if is_math else text
            conflict = _x_tick_conflict(ax, x, label)
            if conflict == "duplicate":
                # Il tick dice già lo stesso: l'etichetta sarebbe «0₀».
                continue
            canvas.draw_label(
                label,
                is_math=is_math,
                xy=(x, 0.0),
                # Seconda riga quando il tick è lì ma dice altro (`−π`
                # sopra `−3`): l'informazione esatta resta, senza fusione.
                offset_pt=(3.0, -20.0 if conflict else -10.0),
                gid=f"zero-label-{k}",
                va="top",
            )
    intervals = computed.get("zero_intervals")
    if isinstance(intervals, list):
        for k, pair in enumerate(intervals[:MAX_NOTABLE_POINTS]):
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                continue
            a, b = float(pair[0]), float(pair[1])
            ax.plot(
                [a, b],
                [0.0, 0.0],
                color=COLOR_INK,
                linewidth=2.4,
                solid_capstyle="round",
                gid=f"zero-interval-{k}",
            )
            canvas.draw_label(
                f"[{format_number(a)}, {format_number(b)}]",
                is_math=False,
                xy=(0.5 * (a + b), 0.0),
                offset_pt=(0.0, -10.0),
                gid=f"zero-interval-label-{k}",
                ha="center",
                va="top",
            )
    for key, marker, color, offset in (
        ("critical_points", "o", PALETTE[1], (4.0, 4.0)),
        ("inflection_points", "s", PALETTE[2], (4.0, -12.0)),
    ):
        entries = computed.get(key)
        if not isinstance(entries, list):
            continue
        prefix = "critical" if key == "critical_points" else "inflection"
        for k, entry in enumerate(entries[:MAX_NOTABLE_POINTS]):
            x, y = float(entry["x"]), entry.get("y")
            if not isinstance(y, (int, float)):
                continue
            ax.plot([x], [float(y)], marker, markersize=4, color=color, gid=f"{prefix}-{k}")
            text, is_math = _pair_label(entry)
            canvas.draw_label(
                text,
                is_math=is_math,
                xy=(x, float(y)),
                offset_pt=offset,
                gid=f"{prefix}-label-{k}",
                va="bottom" if offset[1] >= 0 else "top",
            )


def _draw_discontinuities(
    canvas: _Canvas, study: NumericStudy, computed: Mapping[str, Any]
) -> None:
    entries = computed.get("discontinuities")
    if not isinstance(entries, list) or study.fn is None:
        return
    ax = canvas.ax
    for k, x in enumerate(entries[:MAX_NOTABLE_POINTS]):
        if not isinstance(x, (int, float)):
            continue
        h = 1e-6 * study.width
        left, right = study.fn(float(x) - h), study.fn(float(x) + h)
        finite = [v for v in (left, right) if v == v and abs(v) != float("inf")]
        if not finite:
            continue
        y = sum(finite) / len(finite)
        ax.plot(
            [float(x)],
            [y],
            "o",
            markersize=4,
            markerfacecolor="white",
            markeredgecolor=COLOR_INK,
            gid=f"discontinuity-{k}",
        )


def _draw_annotations(canvas: _Canvas, study: NumericStudy, computed: Mapping[str, Any]) -> None:
    import numpy as np

    ax = canvas.ax
    tangents = computed.get("tangents")
    for k, t in enumerate(study.tangents):
        color = PALETTE[(t.index + 3) % len(PALETTE)]
        ax.axline((t.at, t.y), slope=t.slope, color=color, linewidth=1.1, gid=f"tangent-{k}")
        ax.plot([t.at], [t.y], "o", markersize=4, color=color, gid=f"tangent-point-{k}")
        exact_slope = None
        if isinstance(tangents, list) and k < len(tangents):
            exact_slope = tangents[k].get("exact_slope")
        slope_text, is_math = _value_label(exact_slope, t.slope)
        custom = t.label.strip()
        if custom:
            label, is_math = _escape_text(custom), False
        elif is_math:
            label = f"m = ${slope_text}$"
        else:
            label = f"m = {slope_text}"
        canvas.draw_label(
            label,
            is_math=is_math,
            xy=(t.at, t.y),
            offset_pt=(5.0, 5.0),
            gid=f"tangent-label-{k}",
            color=color,
        )
    for k, area in enumerate(study.areas):
        color = PALETTE[area.index % len(PALETTE)]
        mask = np.isfinite(area.lower) & np.isfinite(area.upper)
        ax.fill_between(
            area.xs,
            area.lower,
            area.upper,
            where=mask,
            alpha=0.25,
            color=color,
            linewidth=0,
            gid=f"area-{k}",
        )
        if area.label.strip():
            mid = 0.5 * (area.between[0] + area.between[1])
            ax.annotate(
                _escape_text(area.label.strip()),
                xy=(mid, 0.0),
                xycoords="data",
                xytext=(0, 4),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=LABEL_SIZE_PT,
                color=COLOR_INK,
                gid=f"area-label-{k}",
                annotation_clip=False,
            )
    for k, p in enumerate(study.points):
        color = PALETTE[p.index % len(PALETTE)]
        ax.plot([p.at], [p.y], "o", markersize=4, color=color, gid=f"point-{k}")
        text = p.label.strip() or f"({format_number(p.at)}, {format_number(p.y)})"
        canvas.draw_label(
            _escape_text(text),
            is_math=False,
            xy=(p.at, p.y),
            offset_pt=(4.0, 4.0),
            gid=f"point-label-{k}",
            color=color,
        )


def text_width_pt(text: str, *, size: float) -> float:
    """Larghezza in punti di `text` (tondo o misto mathtext) al corpo
    `size`, misurata con `TextPath` sul font bundled: la stessa geometria
    del disegno, quindi la stima è esatta per `draw_math` e prossima per
    `<text>` (Noto Sans ha metriche vicine a DejaVu Sans)."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    bbox = TextPath(
        (0, 0), text, size=size, prop=FontProperties(family=_TEXTPATH_FAMILY)
    ).get_extents()
    return float(bbox.x1 - bbox.x0)


def fit_size(text: str, *, available_pt: float) -> float | None:
    """Corpo con cui `text` entra in `available_pt`: `MATH_SIZE_PT` se
    basta, altrimenti ridotto in proporzione (la larghezza di `TextPath` è
    lineare nel corpo) ma non sotto `MIN_MATH_SIZE_PT`; `None` se non
    entra nemmeno al minimo."""
    width = text_width_pt(text, size=MATH_SIZE_PT)
    if width <= available_pt:
        return MATH_SIZE_PT
    if width <= 0.0:
        return MATH_SIZE_PT
    size = MATH_SIZE_PT * available_pt / width
    return size if size >= MIN_MATH_SIZE_PT else None


def _formula_candidates(item_expr: str, source: str | None) -> list[tuple[str, bool]]:
    """`(testo dopo «=», è mathtext)` in ordine di preferenza: LaTeX del
    figlio sympy, poi mathtext scritto dall'AST dell'espressione (timeout,
    errore, LaTeX assente o troppo lungo); l'espressione in chiaro (sintassi
    Python) solo quando mathtext non rende nessuna delle due. Ogni candidato
    ha lunghezza bounded (LaTeX ≤ `MAX_FORMULA_LATEX`, espressione ≤ 200
    caratteri)."""
    out: list[tuple[str, bool]] = []
    mt = to_mathtext(source) if isinstance(source, str) else None
    if mt is not None:
        out.append((mt, True))
    from_ast = expr_to_mathtext(item_expr)
    if from_ast is not None and from_ast != mt:
        out.append((from_ast, True))
    if not out:
        out.append((item_expr, False))
    return out


def _draw_formulas(
    canvas: _Canvas,
    spec: FunctionFigureSpec,
    latex: Sequence[str | None],
    *,
    variables: str,
) -> None:
    """Formule in alto a destra, una riga per espressione, sempre dentro la
    figura: ogni candidato è misurato contro la larghezza disponibile e
    ridotto di corpo fino a `MIN_MATH_SIZE_PT`; se nessuno entra resta il
    solo nome (`f(x)`) con l'avvertenza `formula_too_wide` (l'espressione
    completa è nell'editor e nella didascalia)."""
    ax = canvas.ax
    line_pt = MATH_SIZE_PT * 1.9
    available = (FORMULA_ANCHOR_FRACTION - FORMULA_LEFT_FRACTION) * canvas.axes_width_pt()
    for i, item in enumerate(spec.expressions):
        gid = "formula" if i == 0 else f"formula-{i}"
        head = f"{_escape_text(spec.expression_label(i))}({variables})"
        source = latex[i] if i < len(latex) else None
        chosen: tuple[str, bool, float] | None = None
        for body, is_math in _formula_candidates(item.expr, source):
            text = f"{head} = ${body}$" if is_math else f"{head} = {body}"
            size = fit_size(text, available_pt=available)
            if size is not None:
                chosen = (text, is_math, size)
                break
        if chosen is None:
            canvas.warnings.append(FORMULA_TOO_WIDE)
            chosen = (head, False, MATH_SIZE_PT)
        text, is_math, size = chosen
        if not is_math and text != head:
            # Mathtext non rende né il LaTeX né l'AST: espressione in
            # chiaro (sintassi Python visibile).
            canvas.warnings.append("formula_not_mathtext")
        offset = (0.0, -i * line_pt)
        if is_math:
            canvas.draw_math(
                text,
                anchor=(FORMULA_ANCHOR_FRACTION, 0.985),
                transform=ax.transAxes,
                offset_pt=offset,
                ha="right",
                va="top",
                size=size,
                gid=gid,
            )
            continue
        ax.annotate(
            text,
            xy=(FORMULA_ANCHOR_FRACTION, 0.985),
            xycoords="axes fraction",
            xytext=offset,
            textcoords="offset points",
            ha="right",
            va="top",
            fontsize=size,
            color=COLOR_INK,
            gid=gid,
            annotation_clip=False,
        )


def _draw_levels(canvas: _Canvas, study: NumericStudy) -> None:
    ax = canvas.ax
    if study.grid is None or not study.levels:
        return
    xx, yy, zz = study.grid
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(study.levels))]
    contours = ax.contour(xx, yy, zz, levels=study.levels, colors=colors, linewidths=1.2)
    contours.set_gid("levels")
    ax.clabel(contours, fmt=lambda v: format_number(v), fontsize=LABEL_SIZE_PT, inline=True)
    # Griglia disegnata a mano SOTTO le curve: `ax.grid(True)` la
    # affiderebbe all'asse, che ora sta sopra i dati per non farsi
    # cancellare i tick (TIP-5).
    xlo, xhi = ax.get_xlim()
    ylo, yhi = ax.get_ylim()
    for value in ax.get_xticks():
        if xlo <= value <= xhi:
            ax.axvline(value, color=COLOR_GRID, linewidth=0.6, zorder=0.5)
    for value in ax.get_yticks():
        if ylo <= value <= yhi:
            ax.axhline(value, color=COLOR_GRID, linewidth=0.6, zorder=0.5)
    ax.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)


def render_svg(
    spec: FunctionFigureSpec,
    study: NumericStudy,
    computed: Mapping[str, Any],
    *,
    content_hash: str,
) -> tuple[str, list[str]]:
    """SVG grezzo (da passare a `normalize_svg`) e avvertenze del disegno."""
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    rc: Any = {**MATPLOTLIB_RC, "svg.hashsalt": f"a4u:{content_hash}"}
    with _DRAW_LOCK, rc_context(rc):
        fig = Figure(figsize=FIGSIZE, dpi=DPI)
        FigureCanvasAgg(fig)
        fig.subplots_adjust(**MARGINS)
        ax = fig.add_subplot(111)
        canvas = _Canvas(fig, ax)
        if spec.kind == "level_curves":
            assert spec.variables is not None  # garantito da `check_function_spec`
            names = (spec.variables[0], spec.variables[1])
            _setup_axes(canvas, study, names=names)
            _draw_levels(canvas, study)
            _draw_formulas(canvas, spec, computed.get("latex") or [], variables=", ".join(names))
        else:
            _setup_axes(canvas, study, names=(spec.variable, "y" if spec.variable != "y" else "z"))
            needs_legend = _draw_curves(canvas, study)
            _draw_asymptotes(canvas, computed)
            _draw_annotations(canvas, study, computed)
            _draw_points(canvas, computed)
            _draw_discontinuities(canvas, study, computed)
            if "formula" in spec.show:
                _draw_formulas(canvas, spec, computed.get("latex") or [], variables=spec.variable)
            if needs_legend:
                ax.legend(loc="upper left")
        buf = io.BytesIO()
        fig.savefig(buf, format="svg", metadata={"Date": None, "Creator": None})
    svg = buf.getvalue().decode("utf-8")
    svg = _METADATA_RE.sub("", svg, count=1)
    return svg, canvas.warnings


__all__ = [
    "FORMULA_TOO_WIDE",
    "MIN_MATH_SIZE_PT",
    "expr_to_mathtext",
    "fit_size",
    "mathtext_parses",
    "render_svg",
    "text_width_pt",
    "to_mathtext",
]
