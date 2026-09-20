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

Impaginazione: lo spazio si RISERVA, non si occupa due volte. Formule e
legenda stanno in una banda propria SOPRA gli assi, in coordinate figura
(prima erano ancorate dentro l'area degli assi — la formula a (0,985,
0,985) e la legenda a `loc="upper left"` — e finivano sopra curve ed
etichette). La banda usa PRIMA il margine vuoto che la figura ha già
sopra gli assi (`TOP_PAD_PT`) e alza la figura solo di quello che non ci
entra (`_plan_band`, `_Band.figure_height`): una riga costa 4,1 pt, non
23,1, e una figura senza formule né legenda resta esattamente quella di
prima (374,4 × 259,2 pt). Il risparmio non è estetico: il box figura
della slide di riferimento è vincolato in ALTEZZA, quindi ogni punto di
banda rimpicciolisce il testo reso in proporzione. Dai soli limiti dello
schema segue che la banda non supera `MAX_BAND_CONTENT_PT` e la figura
non supera `MAX_FIG_H_PT` < `FIG_W_PT`: non è MAI più alta che larga.

Le etichette dei punti non hanno più uno scostamento fisso:
`_place_label` misura il riquadro dell'etichetta e prova più candidati
(alto-destra, alto-sinistra, basso-destra, basso-sinistra, due posizioni
centrate più lontane in verticale e infine le stesse riportate dentro il
riquadro degli assi, per l'etichetta più larga dello spazio che le resta
accanto al punto), scartando quelli che si sovrappongono a un testo già
registrato, alla banda o al bordo degli assi; se nessuno è libero vince
il male minore e il disegno registra l'avvertenza `labels_crowded`.

Oracolo dell'impaginazione: OGNI testo della figura ha il suo riquadro
(`TextBox`, punti tipografici nel sistema della figura) in
`DrawResult.boxes`, restituito da `draw` accanto all'SVG e alle
avvertenze, per una di due vie. `_Canvas.place` colloca e registra
(`kind` `label`, `formula`, `legend`, `axis`), misurando con `TextPath`
sul font bundled: identica su ogni macchina, esatta per la geometria e
prossima per i `<text>` (fino a ~4,6 pt di scarto su una stringa lunga,
perché il disegno usa Noto Sans). `_Canvas.register` registra invece il
riquadro di un testo che ha collocato matplotlib, misurato sull'artista
vero (`_Canvas.artist_box`, `get_window_extent`): le etichette dei tick
(`_register_tick_labels`, `kind` `tick`) e quelle dei livelli
(`_label_levels`, `kind` `contour`). Entrambe le vie producono ostacoli,
quindi un'etichetta non finisce più sopra il numero di un tick; i test
misurano lì che nessuna coppia si intersechi.

I `gid` diventano `id=` nell'SVG e sono i ganci dei test: `formula`,
`branch-{i}-{j}`, `zero-{k}`, `critical-{k}`, `inflection-{k}`,
`asymptote-{k}`, `discontinuity-{k}`, `tangent-{k}`, `area-{k}`,
`point-{k}`, `levels`, più le etichette (`{cosa}-label-{k}`), le voci
della legenda (`legend-{i}`, `legend-handle-{i}`), i nomi degli assi
(`axis-name-x`, `axis-name-y`), le etichette dei tick (`tick-x-{i}`,
`tick-y-{i}`) e quelle dei livelli (`level-label-{k}`).
"""

from __future__ import annotations

import ast
import io
import math
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.schemas.figure_function import MAX_EXPRESSIONS, MAX_PARAMETER_VALUES
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
# Il margine sinistro ospita i valori dell'asse y, che con lo zero a
# sinistra cadono FUORI dagli assi: con 0,08 un `-0,75` sporgeva di 2,77 pt
# dalla figura e il renderer lo tagliava. Allargarlo non tocca il corpo del
# testo (il viewBox resta lo stesso), toglie il 4% di larghezza al disegno.
MARGINS = {"left": 0.12, "right": 0.97, "top": 0.94, "bottom": 0.10}
MATH_SIZE_PT = 9.0
# Corpo minimo della formula ridotta per entrare nella figura.
MIN_MATH_SIZE_PT = 6.5
LABEL_SIZE_PT = 8.0
LEGEND_SIZE_PT = 9.0

# Geometria in punti tipografici. Gli assi restano quelli di sempre
# (stessa larghezza, stessa altezza, stesso margine sotto): la banda
# cresce SOPRA di loro, quindi una figura senza formule né legenda è
# identica a prima.
FIG_W_PT = FIGSIZE[0] * 72.0
BASE_H_PT = FIGSIZE[1] * 72.0
AXES_LEFT_PT = MARGINS["left"] * FIG_W_PT
AXES_WIDTH_PT = (MARGINS["right"] - MARGINS["left"]) * FIG_W_PT
AXES_BOTTOM_PT = MARGINS["bottom"] * BASE_H_PT
AXES_HEIGHT_PT = (MARGINS["top"] - MARGINS["bottom"]) * BASE_H_PT
AXES_TOP_PT = AXES_BOTTOM_PT + AXES_HEIGHT_PT
# Spazio già vuoto sopra gli assi nella figura di base: la banda lo
# USA invece di impilarcisi sopra, e la figura cresce solo di quello
# che non ci entra.
TOP_PAD_PT = (1.0 - MARGINS["top"]) * BASE_H_PT
# La formula è ancorata a destra a questa frazione degli assi e può
# estendersi a sinistra fino a `FORMULA_LEFT_FRACTION`.
FORMULA_ANCHOR_FRACTION = 0.985
FORMULA_LEFT_FRACTION = 0.015
FORMULA_AVAILABLE_PT = (FORMULA_ANCHOR_FRACTION - FORMULA_LEFT_FRACTION) * AXES_WIDTH_PT
FORMULA_TOO_WIDE = "formula_too_wide"
# Interlinea DENTRO la banda (moltiplicatore del corpo). Una riga
# della banda è una formula o una fila di voci, non un paragrafo: a
# 1,30 restano 2,7 pt di stacco sopra i 9,0 pt di corsa
# dell'inchiostro. Il numero è stretto per un motivo misurabile: il
# box figura della slide di riferimento (255,0 × 86,6 mm) è vincolato
# in ALTEZZA, quindi ogni punto di banda in più rimpicciolisce il
# testo reso in proporzione (D13).
BAND_LINE_FACTOR = 1.30
# Stacco fra il fondo della banda e il bordo alto degli assi (copre la
# sporgenza dell'etichetta dell'ultimo tick y, centrata sul tick) e fra
# la cima della banda e il bordo alto della figura.
BAND_GAP_PT = 5.0
BAND_TOP_PAD_PT = 3.0
# Legenda disegnata a mano: segmento del colore della curva, stacco,
# testo; le voci sono impaccate per riga sulla larghezza degli assi.
LEGEND_HANDLE_PT = 16.0
LEGEND_HANDLE_GAP_PT = 5.0
LEGEND_COL_GAP_PT = 14.0
LEGEND_ROW_PT = LEGEND_SIZE_PT * BAND_LINE_FACTOR
# Altezza massima della banda e della figura, dai soli limiti dello
# schema: al più `MAX_EXPRESSIONS` righe di formula e al più
# `max(MAX_EXPRESSIONS, MAX_PARAMETER_VALUES)` voci di legenda, ognuna
# sulla sua riga (il peggio dell'impaccamento). Serve a dimostrare che
# la figura non diventa MAI più alta che larga, cioè che il fit non la
# rimpicciolisce per l'altezza in un box largo.
MAX_BAND_CONTENT_PT = (
    MAX_EXPRESSIONS * MATH_SIZE_PT * BAND_LINE_FACTOR
    + max(MAX_EXPRESSIONS, MAX_PARAMETER_VALUES) * LEGEND_ROW_PT
)
MAX_FIG_H_PT = AXES_TOP_PT + BAND_GAP_PT + MAX_BAND_CONTENT_PT + BAND_TOP_PAD_PT
# Etichette dei punti: stacco minimo dal punto e da ogni altro riquadro,
# e salto del candidato «più lontano in verticale».
LABEL_GAP_PT = 3.0
# Margine minimo fra una scritta e il bordo della figura: sotto questo
# valore il renderer taglia il testo al viewBox.
FIGURE_MARGIN_PT = 1.0
LABEL_FAR_PT = 11.0
# Stacco minimo di un'etichetta di contorno da ogni altro riquadro: sotto
# questo la si toglie invece di lasciarla sopra un altro numero.
CONTOUR_GAP_PT = 1.0
# Nessun candidato libero per un'etichetta: la voce finisce nelle
# warnings del disegno (e quindi nei log del render).
LABELS_CROWDED = "labels_crowded"
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


@dataclass(frozen=True)
class TextBox:
    """Riquadro di un testo collocato, in punti tipografici nel sistema
    della FIGURA (origine in basso a sinistra, y verso l'alto).

    È il riquadro dell'inchiostro misurato con `TextPath` sul font
    bundled: esatto per il testo disegnato come geometria (formule ed
    etichette mathtext), prossimo per i `<text>` (Noto Sans ha metriche
    vicine a DejaVu Sans). `kind` dice che cosa è: `label` (etichetta di
    un punto), `formula`, `legend` (voce della legenda), `axis` (nome di
    un asse), `band` (lo spazio riservato a formule e legenda, non un
    testo: è solo un ostacolo)."""

    gid: str
    kind: str
    x0: float
    y0: float
    x1: float
    y1: float

    def overlap(self, other: TextBox) -> tuple[float, float]:
        """`(larghezza, altezza)` dell'intersezione, negative se disgiunti."""
        return (
            min(self.x1, other.x1) - max(self.x0, other.x0),
            min(self.y1, other.y1) - max(self.y0, other.y0),
        )

    def overlap_area(self, other: TextBox) -> float:
        dx, dy = self.overlap(other)
        return dx * dy if dx > 0.0 and dy > 0.0 else 0.0

    def inflated(self, pad: float) -> TextBox:
        return TextBox(
            self.gid, self.kind, self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad
        )

    def outside_area(self, frame: TextBox) -> float:
        """Area del riquadro che esce da `frame` (0 se è tutto dentro)."""
        inside = max(0.0, min(self.x1, frame.x1) - max(self.x0, frame.x0)) * max(
            0.0, min(self.y1, frame.y1) - max(self.y0, frame.y0)
        )
        return max(0.0, (self.x1 - self.x0) * (self.y1 - self.y0) - inside)


class _Canvas:
    """Stato del disegno: figura, assi, avvertenze e riquadri dei testi.

    Ogni testo collocato passa da `place`, che ne registra il riquadro in
    `boxes` (l'oracolo misurabile dai test) e lo aggiunge agli ostacoli
    che la collocazione delle etichette deve evitare."""

    def __init__(self, fig: Any, ax: Any) -> None:
        self.fig = fig
        self.ax = ax
        self.warnings: list[str] = []
        self.boxes: list[TextBox] = []
        self.obstacles: list[TextBox] = []
        self._renderer: Any = None

    # --- misura e conversioni ---------------------------------------------

    def fig_size_pt(self) -> tuple[float, float]:
        return (self.fig.get_figwidth() * 72.0, self.fig.get_figheight() * 72.0)

    def ink_box(self, s: str, *, size: float) -> tuple[float, float, float, float]:
        """Riquadro dell'inchiostro di `s` rispetto all'origine della
        penna (base della prima lettera), in punti."""
        from matplotlib.font_manager import FontProperties
        from matplotlib.textpath import TextPath

        bbox = TextPath(
            (0, 0), s, size=size, prop=FontProperties(family=_TEXTPATH_FAMILY)
        ).get_extents()
        return (float(bbox.x0), float(bbox.y0), float(bbox.x1), float(bbox.y1))

    def artist_box(self, artist: Any, *, gid: str, kind: str) -> TextBox:
        """Riquadro VERO di un testo che matplotlib ha già collocato da sé
        (etichetta di un tick, etichetta di un contorno): `get_window_extent`
        sul renderer, quindi allineamento e rotazione compresi, convertito
        in punti della figura."""
        if self._renderer is None:
            self._renderer = self.fig.canvas.get_renderer()
        bbox = artist.get_window_extent(self._renderer)
        k = 72.0 / float(self.fig.dpi)
        x0, y0, x1, y1 = (float(v) * k for v in (bbox.x0, bbox.y0, bbox.x1, bbox.y1))
        return TextBox(gid, kind, x0, y0, x1, y1)

    def register(self, box: TextBox) -> TextBox:
        """Registra un riquadro misurato su un artista di matplotlib: entra
        fra i riquadri (l'oracolo) e fra gli ostacoli, ma non è disegnato
        qui."""
        self.boxes.append(box)
        self.obstacles.append(box)
        return box

    def data_to_pt(self, x: float, y: float) -> tuple[float, float]:
        """Punto in coordinate dati → punti tipografici della figura."""
        px, py = self.ax.transData.transform((x, y))
        k = 72.0 / float(self.fig.dpi)
        return (float(px) * k, float(py) * k)

    def axes_frame(self) -> TextBox:
        """Bordo degli assi: le etichette dei punti restano qui dentro."""
        return TextBox(
            "axes",
            "frame",
            AXES_LEFT_PT,
            AXES_BOTTOM_PT,
            AXES_LEFT_PT + AXES_WIDTH_PT,
            AXES_BOTTOM_PT + AXES_HEIGHT_PT,
        )

    def collision(self, box: TextBox, *, frame: TextBox | None = None) -> float:
        """Quanto `box` è in conflitto: area sovrapposta agli ostacoli più
        l'area fuori dal riquadro `frame`. 0 se il posto è libero."""
        cost = sum(box.overlap_area(other) for other in self.obstacles)
        if frame is not None:
            cost += box.outside_area(frame)
        return cost

    # --- disegno -----------------------------------------------------------

    def place(
        self,
        text: str,
        *,
        is_math: bool,
        origin: tuple[float, float],
        gid: str,
        kind: str,
        size: float = MATH_SIZE_PT,
        color: str = COLOR_INK,
        ink: tuple[float, float, float, float] | None = None,
    ) -> TextBox:
        """Colloca `text` con l'origine della penna in `origin` (punti
        della figura), registra il riquadro e lo aggiunge agli ostacoli.
        Geometria (`TextPath` + `PathPatch`) se mathtext, `<text>`
        altrimenti: lo stesso modello di riquadro per entrambi."""
        x0, y0, x1, y1 = ink if ink is not None else self.ink_box(text, size=size)
        box = TextBox(gid, kind, origin[0] + x0, origin[1] + y0, origin[0] + x1, origin[1] + y1)
        if is_math:
            self._draw_math(text, origin=origin, size=size, color=color, gid=gid)
        else:
            # Alone solo per le etichette dei punti, che passano sopra le
            # curve: nella banda il testo è su fondo vuoto.
            self._draw_text(
                text, origin=origin, size=size, color=color, gid=gid, halo=kind == "label"
            )
        self.boxes.append(box)
        self.obstacles.append(box)
        return box

    def _draw_math(
        self, s: str, *, origin: tuple[float, float], size: float, color: str, gid: str
    ) -> None:
        """`TextPath` + `PathPatch` con la base della prima lettera in
        `origin`: la geometria resta in punti tipografici qualunque sia il
        dpi del backend."""
        from matplotlib.font_manager import FontProperties
        from matplotlib.patches import PathPatch
        from matplotlib.patheffects import withStroke
        from matplotlib.textpath import TextPath
        from matplotlib.transforms import Affine2D

        path = TextPath((0, 0), s, size=size, prop=FontProperties(family=_TEXTPATH_FAMILY))
        trans = (
            Affine2D().translate(origin[0], origin[1]).scale(1.0 / 72.0) + self.fig.dpi_scale_trans
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

    def _draw_text(
        self,
        s: str,
        *,
        origin: tuple[float, float],
        size: float,
        color: str,
        gid: str,
        halo: bool,
    ) -> None:
        w, h = self.fig_size_pt()
        self.fig.text(
            origin[0] / w,
            origin[1] / h,
            s,
            ha="left",
            va="baseline",
            fontsize=size,
            color=color,
            gid=gid,
            zorder=TEXT_ZORDER,
            bbox=dict(TEXT_HALO_BBOX) if halo else None,
        )


def _label_candidates(
    at: tuple[float, float], w: float, h: float, *, prefer: str, drop: float, frame: TextBox
) -> list[tuple[float, float]]:
    """Angoli in basso a sinistra dei riquadri candidati per un'etichetta
    del punto `at`, in ordine di preferenza: i quattro quadranti attorno
    al punto, due posizioni centrate più lontane in verticale e infine le
    stesse riportate dentro `frame` in orizzontale. `drop` allontana
    ancora i candidati bassi (etichetta di uno zero sotto un tick che dice
    altro)."""
    x, y = at
    gap = LABEL_GAP_PT
    far = gap + LABEL_FAR_PT
    above = [(x + gap, y + gap), (x - gap - w, y + gap), (x - w / 2.0, y + far)]
    below = [
        (x + gap, y - gap - h - drop),
        (x - gap - w, y - gap - h - drop),
        (x - w / 2.0, y - far - h - drop),
    ]
    order = [*below, *above] if prefer == "below" else [*above, *below]
    # Un'etichetta più larga dello spazio che le resta accanto al punto
    # uscirebbe dal viewBox in ogni candidato ancorato al punto: gli
    # ultimi candidati la riportano dentro il riquadro degli assi, dove
    # almeno si legge tutta.
    if frame.x1 - frame.x0 >= w:
        inside = frame.x1 - w
        order.extend(
            dict.fromkeys(
                (min(max(x0, frame.x0), inside), y0)
                for x0, y0 in order
                if not frame.x0 <= x0 <= inside
            )
        )
    return order


def _clamp_to_figure(
    canvas: _Canvas, origin: tuple[float, float], probe: TextBox
) -> tuple[tuple[float, float], TextBox]:
    """Riporta il riquadro dentro la figura, con `MARGIN_PT` di margine:
    l'SVG taglia ciò che esce dal viewBox, e un'etichetta tagliata è
    illeggibile. Lo spostamento si applica anche all'origine del testo."""
    height = float(canvas.fig.get_figheight()) * 72.0
    dx = 0.0
    dy = 0.0
    if probe.x0 < FIGURE_MARGIN_PT:
        dx = FIGURE_MARGIN_PT - probe.x0
    elif probe.x1 > FIG_W_PT - FIGURE_MARGIN_PT:
        dx = (FIG_W_PT - FIGURE_MARGIN_PT) - probe.x1
    if probe.y0 < FIGURE_MARGIN_PT:
        dy = FIGURE_MARGIN_PT - probe.y0
    elif probe.y1 > height - FIGURE_MARGIN_PT:
        dy = (height - FIGURE_MARGIN_PT) - probe.y1
    if dx == 0.0 and dy == 0.0:
        return origin, probe
    moved = TextBox(
        probe.gid, probe.kind, probe.x0 + dx, probe.y0 + dy, probe.x1 + dx, probe.y1 + dy
    )
    return (origin[0] + dx, origin[1] + dy), moved


def _place_label(
    canvas: _Canvas,
    text: str,
    *,
    is_math: bool,
    at: tuple[float, float],
    gid: str,
    color: str = COLOR_INK,
    prefer: str = "above",
    drop: float = 0.0,
) -> None:
    """Etichetta di un punto in coordinate dati, collocata dove non
    incontra nulla: si misura il riquadro e si prova un candidato dopo
    l'altro, scartando quelli che si sovrappongono a un testo già
    collocato (etichette, banda delle formule, legenda, nomi degli assi)
    o che escono dal bordo degli assi. Se nessuno è libero vince quello
    con il conflitto minore e il disegno registra `labels_crowded`."""
    ink = canvas.ink_box(text, size=LABEL_SIZE_PT)
    w, h = ink[2] - ink[0], ink[3] - ink[1]
    anchor = canvas.data_to_pt(*at)
    frame = canvas.axes_frame()
    best: tuple[float, float] | None = None
    best_probe: TextBox | None = None
    best_cost = (math.inf, math.inf)
    for x0, y0 in _label_candidates(anchor, w, h, prefer=prefer, drop=drop, frame=frame):
        origin = (x0 - ink[0], y0 - ink[1])
        probe = TextBox(gid, "label", x0, y0, x0 + w, y0 + h)
        # Il costo ordina i candidati su DUE chiavi, in quest'ordine: la
        # sovrapposizione VERA e poi quella del riquadro allargato di
        # `LABEL_GAP_PT`. Con la sola seconda chiave un posto davvero
        # libero ma senz'aria poteva perdere contro una sovrapposizione
        # vera ma piccola, e le scritte restavano accavallate.
        real = canvas.collision(probe, frame=frame)
        air = canvas.collision(probe.inflated(LABEL_GAP_PT), frame=frame)
        cost = (real, air)
        if real <= 0.0 and air <= 0.0:
            best, best_probe, best_cost = origin, probe, cost
            break
        if cost < best_cost:
            best, best_probe, best_cost = origin, probe, cost
    assert best is not None and best_probe is not None  # `_label_candidates` non è mai vuota
    # Nessuna etichetta esce dalla FIGURA: fuori dal viewBox il testo viene
    # tagliato dal renderer, e un'etichetta tagliata è peggio di una
    # accostata. Il riquadro scelto viene riportato dentro, conservando lo
    # scostamento fra origine e riquadro.
    best, best_probe = _clamp_to_figure(canvas, best, best_probe)
    # L'avvertenza guarda invece il riquadro VERO: un'etichetta che perde
    # l'aria ma non tocca nulla è collocata bene, e dirlo al docente
    # sarebbe un falso allarme.
    if canvas.collision(best_probe, frame=frame) > 0.0:
        canvas.warnings.append(LABELS_CROWDED)
    canvas.place(
        text, is_math=is_math, origin=best, gid=gid, kind="label", size=LABEL_SIZE_PT, color=color
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
    # Nomi degli assi collocati come ogni altro testo (riquadro
    # registrato, quindi ostacolo per le etichette dei punti). Quello
    # della x sta SOPRA la freccia, non sotto: l'etichetta del tick
    # dell'estremo destro è centrata sulla fine dell'asse e più in basso
    # i due testi si toccavano, leggendosi come un unico token («8x»).
    x_end = canvas.data_to_pt(ax.get_xlim()[1], sy)
    canvas.place(
        names[0],
        is_math=False,
        origin=(x_end[0] + 3.0, x_end[1] + 4.0),
        gid="axis-name-x",
        kind="axis",
        size=9.0,
    )
    y_end = canvas.data_to_pt(sx, ax.get_ylim()[1])
    y_ink = canvas.ink_box(names[1], size=9.0)
    canvas.place(
        names[1],
        is_math=False,
        origin=(y_end[0] + 5.0, y_end[1] - 2.0 - y_ink[3]),
        gid="axis-name-y",
        kind="axis",
        size=9.0,
        ink=y_ink,
    )


def _warn_if_ticks_stick_out(canvas: _Canvas) -> None:
    """Avvertenza se un valore sugli assi sporge dalla figura: il margine
    sinistro è dimensionato per i numeri consueti (`-0,75` e simili), ma una
    scala con valori molto larghi può uscire, e fuori dal viewBox il
    renderer taglia. Non si rimpicciolisce il corpo dei valori: sarebbe il
    testo più piccolo della figura, cioè quello su cui il fit misura la
    leggibilità, e si risolverebbe la sovrapposizione peggiorando la
    lettura."""
    ax = canvas.ax
    worst = min(
        (
            canvas.artist_box(label, gid="probe", kind="tick").x0
            for label in ax.get_yticklabels()
            if (label.get_text() or "").strip()
        ),
        default=FIGURE_MARGIN_PT,
    )
    if worst < FIGURE_MARGIN_PT:
        canvas.warnings.append(LABELS_CROWDED)


def _register_tick_labels(canvas: _Canvas) -> None:
    """Etichette dei tick fra i riquadri e fra gli ostacoli: sono testo
    già collocato da matplotlib, e finché non contavano un'etichetta di un
    punto notevole poteva finirci sopra. Si misurano sull'artista vero
    (`get_window_extent`), non si spostano: gli assi sono i loro."""
    _warn_if_ticks_stick_out(canvas)
    ax = canvas.ax
    axes = ((ax.xaxis, "x", ax.get_xlim()), (ax.yaxis, "y", ax.get_ylim()))
    for axis, name, limits in axes:
        lo, hi = min(limits), max(limits)
        locs = list(axis.get_ticklocs())
        labels = list(axis.get_ticklabels())
        for i, (loc, label) in enumerate(zip(locs, labels, strict=False)):
            if not lo <= loc <= hi or not (label.get_text() or "").strip():
                continue
            gid = f"tick-{name}-{i}"
            label.set_gid(gid)
            canvas.register(canvas.artist_box(label, gid=gid, kind="tick"))


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


def _draw_curves(canvas: _Canvas, study: NumericStudy) -> None:
    """Rami delle curve (la legenda è disegnata a parte, nella banda)."""
    ax = canvas.ax
    for curve in study.curves:
        # `index` è l'espressione (studio) o la serie (famiglia): in
        # entrambi i casi numera colore e gid.
        color = PALETTE[curve.index % len(PALETTE)]
        for j, (xs, ys) in enumerate(curve.branches):
            ax.plot(xs, ys, color=color, gid=f"branch-{curve.index}-{j}")


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
            _place_label(
                canvas,
                label,
                is_math=is_math,
                at=(x, 0.0),
                gid=f"zero-label-{k}",
                prefer="below",
                # Seconda riga quando il tick è lì ma dice altro (`−π`
                # sopra `−3`): l'informazione esatta resta, senza fusione.
                drop=10.0 if conflict else 0.0,
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
            _place_label(
                canvas,
                f"[{format_number(a)}, {format_number(b)}]",
                is_math=False,
                at=(0.5 * (a + b), 0.0),
                gid=f"zero-interval-label-{k}",
                prefer="below",
            )
    for key, marker, color, prefer in (
        ("critical_points", "o", PALETTE[1], "above"),
        ("inflection_points", "s", PALETTE[2], "below"),
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
            _place_label(
                canvas,
                text,
                is_math=is_math,
                at=(x, float(y)),
                gid=f"{prefix}-label-{k}",
                prefer=prefer,
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
        _place_label(
            canvas,
            label,
            is_math=is_math,
            at=(t.at, t.y),
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
            _place_label(
                canvas,
                _escape_text(area.label.strip()),
                is_math=False,
                at=(mid, 0.0),
                gid=f"area-label-{k}",
            )
    for k, p in enumerate(study.points):
        color = PALETTE[p.index % len(PALETTE)]
        ax.plot([p.at], [p.y], "o", markersize=4, color=color, gid=f"point-{k}")
        text = p.label.strip() or f"({format_number(p.at)}, {format_number(p.y)})"
        _place_label(
            canvas,
            _escape_text(text),
            is_math=False,
            at=(p.at, p.y),
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


@dataclass(frozen=True)
class _FormulaLine:
    text: str
    is_math: bool
    size: float
    width: float
    gid: str


@dataclass(frozen=True)
class _LegendEntry:
    label: str
    color: str
    width: float
    index: int


@dataclass(frozen=True)
class _Band:
    """Spazio riservato SOPRA gli assi a formule e legenda: una riga per
    formula, le voci della legenda impaccate su una o più righe, e la
    legenda accanto alla prima formula quando ci sta in larghezza.

    `content` è l'altezza delle sole righe; `height` è quanto la figura
    cresce, cioè quello che non entra nello spazio già vuoto sopra gli
    assi (`TOP_PAD_PT`). Senza formula né legenda sono entrambi 0 e la
    figura è identica a quella di prima."""

    formulas: tuple[_FormulaLine, ...]
    legend_rows: tuple[tuple[_LegendEntry, ...], ...]
    side_by_side: bool
    content: float
    height: float
    warnings: tuple[str, ...]

    @staticmethod
    def figure_height(content: float) -> float:
        """Altezza della figura che ospita `content` punti di banda."""
        if content <= 0.0:
            return BASE_H_PT
        return max(BASE_H_PT, AXES_TOP_PT + BAND_GAP_PT + content + BAND_TOP_PAD_PT)


def _plan_formulas(
    spec: FunctionFigureSpec, latex: Sequence[str | None], *, variables: str
) -> tuple[list[_FormulaLine], list[str]]:
    """Una riga per espressione, sempre entro la larghezza degli assi:
    ogni candidato è misurato contro la larghezza disponibile e ridotto di
    corpo fino a `MIN_MATH_SIZE_PT`; se nessuno entra resta il solo nome
    (`f(x)`) con l'avvertenza `formula_too_wide` (l'espressione completa è
    nell'editor e nella didascalia)."""
    lines: list[_FormulaLine] = []
    warnings: list[str] = []
    for i, item in enumerate(spec.expressions):
        gid = "formula" if i == 0 else f"formula-{i}"
        head = f"{_escape_text(spec.expression_label(i))}({variables})"
        source = latex[i] if i < len(latex) else None
        chosen: tuple[str, bool, float] | None = None
        for body, is_math in _formula_candidates(item.expr, source):
            text = f"{head} = ${body}$" if is_math else f"{head} = {body}"
            size = fit_size(text, available_pt=FORMULA_AVAILABLE_PT)
            if size is not None:
                chosen = (text, is_math, size)
                break
        if chosen is None:
            warnings.append(FORMULA_TOO_WIDE)
            chosen = (head, False, MATH_SIZE_PT)
        text, is_math, size = chosen
        if not is_math and text != head:
            # Mathtext non rende né il LaTeX né l'AST: espressione in
            # chiaro (sintassi Python visibile).
            warnings.append("formula_not_mathtext")
        lines.append(_FormulaLine(text, is_math, size, text_width_pt(text, size=size), gid))
    return lines, warnings


def _legend_entries(study: NumericStudy) -> list[_LegendEntry]:
    """Voci della legenda (una per curva disegnata con la sua label), vuota
    quando la curva è una sola: il nome sta già nella formula."""
    entries: list[_LegendEntry] = []
    for curve in study.curves:
        if not curve.branches:
            continue
        label = _escape_text(curve.label)
        width = LEGEND_HANDLE_PT + LEGEND_HANDLE_GAP_PT + text_width_pt(label, size=LEGEND_SIZE_PT)
        color = PALETTE[curve.index % len(PALETTE)]
        entries.append(_LegendEntry(label, color, width, curve.index))
    return entries if len(entries) > 1 else []


def _pack_legend(entries: Sequence[_LegendEntry]) -> list[tuple[_LegendEntry, ...]]:
    """Voci impaccate per riga sulla larghezza degli assi (le legende
    corte, come `k = 0,12`, stanno tutte su una riga sola)."""
    rows: list[tuple[_LegendEntry, ...]] = []
    row: list[_LegendEntry] = []
    used = 0.0
    for entry in entries:
        step = entry.width if not row else LEGEND_COL_GAP_PT + entry.width
        if row and used + step > AXES_WIDTH_PT:
            rows.append(tuple(row))
            row, used, step = [], 0.0, entry.width
        row.append(entry)
        used += step
    if row:
        rows.append(tuple(row))
    return rows


def _plan_band(
    spec: FunctionFigureSpec,
    study: NumericStudy,
    latex: Sequence[str | None],
    *,
    variables: str,
    with_formulas: bool,
) -> _Band:
    """Piano della banda: le formule e la legenda non stanno più dentro
    l'area degli assi (dove coprivano curve ed etichette) ma sopra, in uno
    spazio riservato che alza la figura di quanto serve."""
    formulas, warnings = (
        _plan_formulas(spec, latex, variables=variables) if with_formulas else ([], [])
    )
    rows = _pack_legend(_legend_entries(study))
    if not formulas and not rows:
        return _Band((), (), False, 0.0, 0.0, tuple(warnings))
    legend_width = (
        sum(e.width for e in rows[0]) + LEGEND_COL_GAP_PT * (len(rows[0]) - 1) if rows else 0.0
    )
    # La legenda va accanto alle formule solo se sta su una riga sola e
    # resta lo spazio per la prima formula, che è la più larga da
    # allineare a destra; altrimenti prende righe proprie.
    side_by_side = bool(
        formulas
        and len(rows) == 1
        and legend_width + LEGEND_COL_GAP_PT + formulas[0].width <= AXES_WIDTH_PT
    )
    formula_height = sum(line.size * BAND_LINE_FACTOR for line in formulas)
    legend_height = len(rows) * LEGEND_ROW_PT
    if side_by_side:
        first = formulas[0].size * BAND_LINE_FACTOR
        content = formula_height - first + max(first, LEGEND_ROW_PT)
    else:
        content = formula_height + legend_height
    return _Band(
        tuple(formulas),
        tuple(rows),
        side_by_side,
        content,
        _Band.figure_height(content) - BASE_H_PT,
        tuple(warnings),
    )


def _draw_band(canvas: _Canvas, band: _Band) -> None:
    """Disegna la banda in coordinate FIGURA e registra il rettangolo
    riservato fra gli ostacoli: formule allineate a destra, legenda a
    sinistra (segmento del colore della curva più il nome)."""
    from matplotlib.lines import Line2D

    if band.content <= 0.0:
        return
    fig_w, fig_h = canvas.fig_size_pt()
    # La banda parte dal bordo alto degli assi più lo stacco e sale: così
    # lo spazio già vuoto sopra gli assi è USATO, non sprecato.
    top = AXES_TOP_PT + BAND_GAP_PT + band.content
    canvas.obstacles.append(
        TextBox("band", "band", AXES_LEFT_PT, AXES_TOP_PT, AXES_LEFT_PT + AXES_WIDTH_PT, top)
    )
    right = AXES_LEFT_PT + FORMULA_ANCHOR_FRACTION * AXES_WIDTH_PT
    cursor = top
    for i, line in enumerate(band.formulas):
        row = line.size * BAND_LINE_FACTOR
        if i == 0 and band.side_by_side:
            row = max(row, LEGEND_ROW_PT)
        ink = canvas.ink_box(line.text, size=line.size)
        canvas.place(
            line.text,
            is_math=line.is_math,
            origin=(right - ink[2], cursor - row + (row - (ink[3] - ink[1])) / 2.0 - ink[1]),
            gid=line.gid,
            kind="formula",
            size=line.size,
            ink=ink,
        )
        cursor -= row
    if not band.legend_rows:
        return
    if band.side_by_side:
        cursor = top
    for row_entries in band.legend_rows:
        x = AXES_LEFT_PT
        middle = cursor - LEGEND_ROW_PT / 2.0
        if band.side_by_side and band.formulas:
            middle = cursor - max(band.formulas[0].size * BAND_LINE_FACTOR, LEGEND_ROW_PT) / 2.0
        for entry in row_entries:
            handle = Line2D(
                [x / fig_w, (x + LEGEND_HANDLE_PT) / fig_w],
                [middle / fig_h, middle / fig_h],
                transform=canvas.fig.transFigure,
                color=entry.color,
                linewidth=1.5,
                gid=f"legend-handle-{entry.index}",
            )
            canvas.fig.add_artist(handle)
            ink = canvas.ink_box(entry.label, size=LEGEND_SIZE_PT)
            canvas.place(
                entry.label,
                is_math=False,
                origin=(
                    x + LEGEND_HANDLE_PT + LEGEND_HANDLE_GAP_PT,
                    middle - (ink[1] + ink[3]) / 2.0,
                ),
                gid=f"legend-{entry.index}",
                kind="legend",
                size=LEGEND_SIZE_PT,
                ink=ink,
            )
            x += entry.width + LEGEND_COL_GAP_PT
        cursor -= LEGEND_ROW_PT


def _draw_levels(canvas: _Canvas, study: NumericStudy) -> Any:
    """Contorni e griglia. Le etichette dei livelli NON sono disegnate
    qui: le colloca `_label_levels`, dopo che tick e banda sono fra gli
    ostacoli."""
    ax = canvas.ax
    if study.grid is None or not study.levels:
        return None
    xx, yy, zz = study.grid
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(study.levels))]
    contours = ax.contour(xx, yy, zz, levels=study.levels, colors=colors, linewidths=1.2)
    contours.set_gid("levels")
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
    return contours


def _label_levels(canvas: _Canvas, contours: Any) -> None:
    """Etichette dei contorni. Le colloca matplotlib lungo le curve
    (`ax.clabel`), quindi qui si MISURANO sull'artista vero e si
    registrano fra i riquadri: prima non passavano dall'oracolo e due
    numeri potevano restare uno sopra l'altro. Quella che cade su un testo
    già collocato — un'altra etichetta, il nome di un asse, un tick, la
    banda — viene tolta, perché lungo il contorno non c'è dove spostarla;
    `inline=False` con l'alone bianco fa sì che toglierla non lasci il
    buco che `inline=True` apre nella curva."""
    if contours is None:
        return
    labels = canvas.ax.clabel(
        contours, fmt=lambda v: format_number(v), fontsize=LABEL_SIZE_PT, inline=False
    )
    crowded = False
    for k, label in enumerate(labels or ()):
        gid = f"level-label-{k}"
        label.set_bbox(dict(TEXT_HALO_BBOX))
        label.set_zorder(TEXT_ZORDER)
        label.set_gid(gid)
        box = canvas.artist_box(label, gid=gid, kind="contour")
        if canvas.collision(box.inflated(CONTOUR_GAP_PT)) > 0.0:
            label.set_visible(False)
            crowded = True
            continue
        canvas.register(box)
    if crowded:
        canvas.warnings.append(LABELS_CROWDED)


@dataclass(frozen=True)
class DrawResult:
    """Esito del disegno: SVG grezzo, avvertenze e riquadri di OGNI testo
    collocato (`TextBox`, in punti della figura). I riquadri sono
    l'oracolo dell'impaginazione: due testi non devono intersecarsi."""

    svg: str
    warnings: list[str]
    boxes: tuple[TextBox, ...]


def draw(
    spec: FunctionFigureSpec,
    study: NumericStudy,
    computed: Mapping[str, Any],
    *,
    content_hash: str,
) -> DrawResult:
    """Disegno completo: piano della banda (formule e legenda sopra gli
    assi), figura alta quanto serve, curve, etichette collocate senza
    collisioni, SVG."""
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    level_curves = spec.kind == "level_curves"
    if level_curves:
        assert spec.variables is not None  # garantito da `check_function_spec`
        names = (spec.variables[0], spec.variables[1])
        variables = ", ".join(names)
    else:
        names = (spec.variable, "y" if spec.variable != "y" else "z")
        variables = spec.variable
    rc: Any = {**MATPLOTLIB_RC, "svg.hashsalt": f"a4u:{content_hash}"}
    with _DRAW_LOCK, rc_context(rc):
        band = _plan_band(
            spec,
            study,
            computed.get("latex") or [],
            variables=variables,
            with_formulas=level_curves or "formula" in spec.show,
        )
        height_pt = BASE_H_PT + band.height
        fig = Figure(figsize=(FIGSIZE[0], height_pt / 72.0), dpi=DPI)
        FigureCanvasAgg(fig)
        fig.subplots_adjust(
            left=MARGINS["left"],
            right=MARGINS["right"],
            bottom=AXES_BOTTOM_PT / height_pt,
            top=(AXES_BOTTOM_PT + AXES_HEIGHT_PT) / height_pt,
        )
        ax = fig.add_subplot(111)
        canvas = _Canvas(fig, ax)
        canvas.warnings.extend(band.warnings)
        # Ordine: prima tutto ciò che matplotlib colloca da sé (assi,
        # curve, contorni, etichette dei tick), poi la banda, poi le
        # etichette, che sono le uniche a potersi spostare.
        _setup_axes(canvas, study, names=names)
        if level_curves:
            contours = _draw_levels(canvas, study)
            _register_tick_labels(canvas)
            _draw_band(canvas, band)
            _label_levels(canvas, contours)
        else:
            _draw_curves(canvas, study)
            _draw_asymptotes(canvas, computed)
            _register_tick_labels(canvas)
            # La banda prima delle etichette: il suo spazio è un ostacolo
            # per la collocazione, non un posto dove scrivere sopra.
            _draw_band(canvas, band)
            _draw_annotations(canvas, study, computed)
            _draw_points(canvas, computed)
            _draw_discontinuities(canvas, study, computed)
        buf = io.BytesIO()
        fig.savefig(buf, format="svg", metadata={"Date": None, "Creator": None})
    svg = buf.getvalue().decode("utf-8")
    svg = _METADATA_RE.sub("", svg, count=1)
    return DrawResult(svg, canvas.warnings, tuple(canvas.boxes))


def render_svg(
    spec: FunctionFigureSpec,
    study: NumericStudy,
    computed: Mapping[str, Any],
    *,
    content_hash: str,
) -> tuple[str, list[str]]:
    """SVG grezzo (da passare a `normalize_svg`) e avvertenze del disegno."""
    result = draw(spec, study, computed, content_hash=content_hash)
    return result.svg, result.warnings


__all__ = [
    "FORMULA_TOO_WIDE",
    "LABELS_CROWDED",
    "MIN_MATH_SIZE_PT",
    "DrawResult",
    "TextBox",
    "draw",
    "expr_to_mathtext",
    "fit_size",
    "mathtext_parses",
    "render_svg",
    "text_width_pt",
    "to_mathtext",
]
