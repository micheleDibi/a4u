"""Geometria degli SVG resi (D14): incroci arco per arco e difetti di lettura.

Modulo puro (solo libreria standard, nessun import da `app.*`): lavora sulla
stringa SVG già prodotta da un renderer e non la modifica mai. È il gemello
Python della misura che per Mermaid vive nella pagina del pre-render
(`mermaid_prerender.MEASURE_SVG_GEOMETRY_JS`, `window.__measureSvg`): stesse
classi degli archi, stesso passo, stesse tolleranze, stesso conteggio. La
parità è provata in Chromium sui diciotto modelli DOT dell'editor
(`tests/test_figure_geometry.py`).

Incroci arco × arco
-------------------
- Tracciati considerati (elenco unico, specchiato dal JS):
  * DOT: i `<path>` figli di `g.edge` (le punte di freccia sono
    `<polygon>` e restano fuori); il gruppo è l'unità «arco», quindi due
    tracciati dello stesso `g.edge` (colori paralleli) non si contano.
    Con `tooltip`, `edgetooltip`, `URL` e simili Graphviz avvolge il
    tracciato in `<g id="a_edgeN"><a …>`: gli involucri `<a>` e i `<g>`
    senza classe con id `a_…` sono attraversati per trovare il gruppo
    (lo stesso vale per i testi e le forme dei nodi e dei cluster);
  * Mermaid: `<path>`, `<line>`, `<polyline>` con una classe che comincia
    per `edge-thickness-` (flowchart, block, state, class, er, mindmap:
    renderer unificato) o per `messageLine` (sequence), oppure una fra
    `flowchart-link`, `transition`, `relation`, `relationshipLine`, e i
    `<path>` figli di `g.edgePath` (layout storico); ogni elemento è un arco.
- Punti ciechi dichiarati: i `<line>` di quadrant e gantt sono griglia e
  assi, non archi (le due mediane del quadrant si incrociano per
  costruzione: contarle darebbe un incrocio fisso a ogni figura); i link
  del sankey (`g.link > path`, bande che si incrociano per natura), le
  curve del radar e le serie dello xychart non sono archi; treemap, pie e
  timeline non ne hanno. Per questi tipi `crossings` vale 0.
- Campionamento a passo `SAMPLE_STEP` (2 unità utente della radice, dopo
  ogni trasformazione): le curve (C/S/Q/T) sono suddivise sul poligono di
  controllo già trasformato, i tratti rettilinei in pezzi della stessa
  lunghezza. Il JS percorre il tracciato in unità locali e le porta nella
  radice: il suo passo locale è `step / k`, con `k` l'allungamento massimo
  della trasformazione (valore singolare maggiore; 1 senza scala), quindi
  anche lì i segmenti misurano al più un passo della radice e il tetto di
  segmenti li conta in unità della radice (giro 3, V3-N1: prima il JS
  campionava a 2 unità locali e con `scale(10)` una gobba stretta tagliata
  due volte valeva 0 incroci in Chromium e 2 in Python); l'arco ellittico
  (`A`) è convertito in cubiche di al più 90° (SVG 1.1, F.6.5; scarto
  radiale sotto lo 0,03 % del raggio), mentre Chromium campiona l'arco
  vero. Un tracciato con più sotto-tracciati (`M` intermedio) è campionato
  a pezzi separati: il JS, che percorre il tracciato con
  `getPointAtLength`, riconosce il salto fra due campioni (distanza oltre
  la lunghezza percorsa) e ne cerca il confine per bisezione, così il salto
  non diventa un segmento in nessuno dei due conteggi. Punto cieco comune:
  i flag dell'arco scritti senza separatore (`A5 5 0 015 5`) non sono
  letti e il tracciato si interrompe lì.
- Stesso conteggio degli incroci, non degli stessi segmenti: Python
  campiona per parametro sul pezzo, Chromium per lunghezza sull'intero
  tracciato, quindi `segments` può differire di qualche unità (di più con
  una scala non uniforme, dove il JS usa l'allungamento massimo su tutto
  il tracciato: limite dichiarato, i conteggi possono divergere; Graphviz
  scala in modo uniforme e Mermaid non scala). Secondo limite dichiarato:
  con `splines=ortho` e `splines=polyline` i conteggi possono divergere
  anche senza scala (K5,5 ortogonale: Python 42, Chromium 34), perché
  Python tiene gli angoli dei percorsi come vertici e conta i contatti a T
  sui canali condivisi, Chromium campiona per lunghezza e taglia gli
  angoli. In produzione i DOT si misurano solo in Python e la misura è
  diagnostica.
- Test di intersezione con i quattro prodotti vettoriali, estremi inclusi
  (un incrocio che cade esattamente su un vertice del campionamento non va
  perso), segmenti collineari esclusi (un fascio parallelo vale 0); solo
  fra tracciati di archi diversi.
- Un punto a meno di `ENDPOINT_TOLERANCE` (2 unità) da un estremo di uno
  dei due tracciati è scartato: è l'attacco condiviso sul bordo di un nodo,
  non un incrocio.
- I punti a meno di `CLUSTER_RADIUS` (3 unità) l'uno dall'altro formano un
  solo incrocio: `crossings` conta i punti distinti, `crossing_pairs` le
  coppie di archi incidenti in ciascun punto (tre archi concorrenti in un
  punto triplo: 1 incrocio, 3 coppie).
Costo limitato per costruzione
------------------------------
Ogni ciclo della misura ha un numero di passi noto PRIMA di eseguirlo e
confrontato con un tetto; oltre, la misura è saltata (`skipped` col
motivo, `crossings=None`, SVG intatto) e la figura resta valida.
- Segmenti: il numero di campioni di ogni tracciato si calcola dalla sua
  lunghezza in unità della radice, prima di campionare; oltre
  `MAX_MEASURE_SEGMENTS` la misura è saltata (`figure_segment_cap`). Un
  tracciato con coordinate non finite salta la misura
  (`geometry_out_of_range`).
- Griglia dei confronti (giro 3, V3-F1: prima il passo era fisso a 16
  unità e l'etichetta di un nodo con `fontsize=8000`, o di un grafo con
  `size="3000,3000!"`, enumerava milioni di celle prima di ogni tetto:
  7,7 s e 1,3 GB, oltre 2 GB nel secondo caso). La tela è il viewBox (il
  riquadro unione di segmenti ed etichette se manca), il passo è
  `max(GRID_CELL, (L + H) / (2 · (√MAX_GRID_CELLS − 2)))` e ogni
  riquadro (segmenti, etichette, punti) è RITAGLIATO alla tela prima di
  diventare celle, con indici relativi all'origine. Colonne e righe sono
  al più `L/passo + 1` e `H/passo + 1`, la cui somma vale al più
  `2 · (√MAX_GRID_CELLS − 2) + 2`, e il prodotto di due numeri a somma
  fissa è massimo quando sono uguali: la tela ha al più
  `MAX_GRID_CELLS` celle, qualunque sia la geometria (il passo sul solo
  rapporto area / celle non basta: una tela lunga e bassa avrebbe colonne
  illimitate). Sui modelli normali (`L + H` fino a 15.936 unità) il passo
  resta 16. Il ritaglio è monotono, quindi due riquadri che si toccano
  hanno ancora una cella in comune: i confronti, e i conteggi, restano
  quelli di una griglia infinita.
- Le celle di ogni riquadro si contano in aritmetica (`colonne × righe`)
  e si sommano al lavoro PRIMA di enumerarle; la griglia delle etichette
  è costruita una volta e letta dalle sovrapposizioni, dal lavoro e
  dall'attraversamento degli archi.
- Coppie: gli occupanti di ogni cella danno le coppie candidate
  (segmento × segmento, etichetta × etichetta, etichetta × segmento),
  sommate al lavoro prima dei confronti. Una coppia è provata solo nella
  cella d'angolo dell'intersezione delle due impronte (nessun insieme dei
  visti: memoria lineare nelle celle).
- Raggruppamento dei punti (un fascio di 100 rette per un punto solo dà
  decine di migliaia di punti coincidenti: 2,6 s sotto il tetto delle
  coppie, quando ogni punto era confrontato con i vicini già visti): celle
  di lato appena sotto `CLUSTER_RADIUS / √2`, i punti di una cella sono
  uniti senza confronti, ogni coppia di celle vicine (fino a due passi) è
  decisa dai riquadri dei loro punti e solo le coppie incerte si
  confrontano punto per punto. Il costo (`2 · punti + 12 · celle +
  Σ |A| · |B|` sulle coppie incerte) è contato prima; il risultato è lo
  stesso di prima (componenti del grafo «distanza ≤ raggio»).
- Riquadri delle forme dei nodi: estremi esatti delle Bézier (derivata
  nulla), non campionamento: il costo segue i pezzi del tracciato, non la
  sua lunghezza.
- Tetti: il lavoro (`work`: celle, coppie e controlli del raggruppamento)
  oltre `MAX_MEASURE_WORK` salta la misura (`figure_work_cap`), oltre il
  residuo del batch (`work_left`, `MAX_BATCH_MEASURE_WORK` per batch) è
  `batch_work_cap`; `spent` è la parte già eseguita, quella che il batch
  sottrae al residuo anche quando la misura è saltata. Il costo in Python
  è di 0,3-0,6 µs per unità di lavoro (misura del 17 settembre 2026): un
  bipartito 16×16 ha 49.216 segmenti, sotto il primo tetto, ma 12,5
  milioni di coppie (4,2 s senza tetto, oltre il timeout del batch DOT).
  Riferimenti: il grafo più costoso entro le soglie editoriali ne ha 0,76
  milioni (0,24 s), i diciotto modelli dell'editor meno di 1.600.
- La pagina Chromium applica lo stesso schema (tela, passo, ritaglio,
  conteggio delle celle, delle coppie e del raggruppamento prima di
  eseguirli) con i tetti `MAX_BROWSER_MEASURE_WORK` e
  `MAX_BATCH_BROWSER_MEASURE_WORK`, tarati sulla sua velocità.

Difetti di lettura (solo DOT, coordinate esplicite)
---------------------------------------------------
I quattro controlli dell'oracolo `_DOT_GEOMETRIA_JS` dei test del frontend,
misurati qui senza browser: testo fuori dalla tela (viewBox), testo fuori
dal nodo o dal cluster che lo possiede, etichette sovrapposte, arco che
attraversa un'etichetta non sua. Il riquadro del testo è STIMATO con le
larghezze di avanzamento della famiglia dichiarata nel `font-family` (il
primo nome): Noto Sans (il font del tema, `figure_theme`; anche in
assenza dell'attributo), Times-Roman per `Times…`/`serif` (il default di
Graphviz quando il sorgente dichiara un proprio blocco `node [...]` o
un'etichetta HTML-like: il tema allora non inietta `fontname`), passo
fisso di 600 millesimi per `Courier…`/`monospace`. Punto cieco
dichiarato: un testo in un'altra famiglia (`Helvetica`, `Arial`, …) non
ha stima e i quattro controlli lo saltano. `ascent` 0,9 em e `descent`
0,25 em (riquadro d'inchiostro: il passo di riga di Graphviz è 1,2 em, il
riquadro del font di 1,362 em farebbe sovrapporre le righe di ogni
etichetta multiriga), ancoraggio da `text-anchor`, niente crenatura. La
stima è ristretta di `ESTIMATE_SLACK` (2 % della larghezza) per lato:
assorbe crenatura, versione del font e sostituzione del font sulla
macchina che rende (Graphviz misura con i font installati; senza Noto
Sans il nodo è dimensionato su un altro font). Tolleranza
`TEXT_TOLERANCE` (0,5 unità) come l'oracolo JS; per l'attraversamento il
riquadro è ristretto di 1 unità in più per lato. Le sovrapposizioni sono
cercate sulla griglia dei confronti (costo lineare nelle etichette, non
quadratico). Un testo o una forma con coordinate non finite non ha
riquadro e non è controllato.
"""

from __future__ import annotations

import contextlib
import html
import itertools
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

SAMPLE_STEP = 2.0
ENDPOINT_TOLERANCE = 2.0
CLUSTER_RADIUS = 3.0
TEXT_TOLERANCE = 0.5
# Tolleranza del solo controllo «testo fuori dalla tela» di Mermaid (em box
# del titolo del radar a filo del bordo, come `_OVERFLOW_TOLLERATO_PX`).
MERMAID_TEXT_TOLERANCE = 4.0
# Tetti di lavoro (misura del 16 settembre, §3(f) del piano): per figura e,
# nella pagina del pre-render Mermaid, per l'intero batch.
MAX_MEASURE_SEGMENTS = 50_000
MAX_BATCH_MEASURE_SEGMENTS = 150_000
# Tetti della misura Python, in unità di lavoro (celle, coppie candidate,
# controlli del raggruppamento; docstring): circa 0,6 s per figura e 1,2 s
# per batch DOT sulla macchina della misura, entro i 20 s di
# `figure_render_timeout_seconds` anche su un server tre volte più lento.
MAX_MEASURE_WORK = 1_000_000
MAX_BATCH_MEASURE_WORK = 2_000_000
# Gli stessi tetti nella pagina Chromium, dove un'unità di lavoro costa
# 9-54 ns (taratura nel commento di `mermaid_prerender.
# MEASURE_SVG_GEOMETRY_JS`): al più circa 0,5 s per figura e 1 s per batch;
# il flowchart più denso entro le soglie editoriali ne vale 0,34 milioni.
MAX_BROWSER_MEASURE_WORK = 10_000_000
MAX_BATCH_BROWSER_MEASURE_WORK = 20_000_000
# Griglia dei confronti: lato minimo della cella e celle al più sull'intera
# tela (docstring, «Costo limitato per costruzione»).
GRID_CELL = 16.0
MAX_GRID_CELLS = 250_000
# Voci di difetto conservate per figura (il resto è contato, non elencato).
MAX_DEFECTS = 20
# Frazione della larghezza stimata tolta a ciascun lato del riquadro.
ESTIMATE_SLACK = 0.02

SKIP_FIGURE_CAP = "figure_segment_cap"
SKIP_BATCH_CAP = "batch_segment_cap"
SKIP_WORK_CAP = "figure_work_cap"
SKIP_BATCH_WORK_CAP = "batch_work_cap"
SKIP_RANGE = "geometry_out_of_range"

DEFECT_TEXT_OUTSIDE_CANVAS = "text_outside_canvas"
DEFECT_TEXT_OUTSIDE_OWNER = "text_outside_owner"
DEFECT_LABELS_OVERLAP = "labels_overlap"
DEFECT_EDGE_CROSSES_LABEL = "edge_crosses_label"

# Classi degli archi Mermaid: prefissi e nomi esatti (elenco del docstring).
EDGE_CLASS_PREFIXES: tuple[str, ...] = ("edge-thickness-", "messageLine")
EDGE_CLASS_NAMES: frozenset[str] = frozenset(
    {"flowchart-link", "transition", "relation", "relationshipLine"}
)
# Gruppi che fanno da unità «arco» per i tracciati figli.
EDGE_GROUP_CLASSES: frozenset[str] = frozenset({"edge", "edgePath"})

_EPS = 1e-9
_TEXT_ASCENT_EM = 0.9
_TEXT_DESCENT_EM = 0.25
_LABEL_CHARS = 40

# Avanzamenti di Noto Sans Regular per i caratteri 32-126, in millesimi di
# em (tabella `hmtx`, 1000 unità per em).
_NOTO_ASCII_WIDTHS: tuple[int, ...] = (
    260, 269, 408, 646, 572, 831, 732, 225, 300, 300, 551, 572, 268, 322, 268, 372,
    572, 572, 572, 572, 572, 572, 572, 572, 572, 572, 268, 268, 572, 572, 572, 434,
    899, 639, 650, 632, 730, 556, 519, 728, 741, 339, 273, 619, 524, 907, 760, 781,
    605, 781, 622, 549, 556, 731, 600, 930, 586, 566, 572, 329, 372, 329, 572, 444,
    281, 561, 615, 480, 615, 564, 344, 615, 618, 258, 258, 534, 258, 935, 618, 605,
    615, 615, 413, 479, 361, 618, 508, 786, 529, 510, 470, 380, 551, 380, 572,
)  # fmt: skip
# Avanzamenti di Times-Roman (metriche AFM Adobe) per i caratteri 32-126:
# coincidono con il Times di Chromium carattere per carattere (verifica del
# 17 settembre 2026 con `measureText`, somma 48.458 in entrambi).
_TIMES_ASCII_WIDTHS: tuple[int, ...] = (
    250, 333, 408, 500, 500, 833, 778, 180, 333, 333, 500, 564, 250, 333, 250, 278,
    500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 278, 278, 564, 564, 564, 444,
    921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722,
    556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500,
    333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778, 500, 500,
    500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541,
)  # fmt: skip
_MONO_WIDTH = 600
_SPACE_WIDTH = 260
_DEFAULT_WIDTH = 572
_WIDE_WIDTH = 1000

FONT_SANS = "sans"
FONT_SERIF = "serif"
FONT_MONO = "mono"
# Tabella di avanzamenti e larghezza di default (spazi, altri caratteri).
_FONT_TABLES: dict[str, tuple[tuple[int, ...] | None, int, int]] = {
    FONT_SANS: (_NOTO_ASCII_WIDTHS, _SPACE_WIDTH, _DEFAULT_WIDTH),
    FONT_SERIF: (_TIMES_ASCII_WIDTHS, 250, 500),
    FONT_MONO: (None, _MONO_WIDTH, _MONO_WIDTH),
}

Point = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


@dataclass(frozen=True)
class GeometryReport:
    """Esito della misura di un SVG. `crossings` e `crossing_pairs` sono
    `None` quando la misura è saltata (`skipped` ne dice il motivo);
    `segments` è il numero di segmenti campionati (o stimati, se saltata);
    `defects` sono le voci `codice: dettaglio` dei difetti di lettura;
    `work` è il lavoro contato prima di eseguirlo (celle, coppie,
    controlli del raggruppamento; 0 nelle uscite anticipate), `spent` la
    parte eseguita davvero, che il batch sottrae al residuo (uguale a
    `work` per una misura completa)."""

    crossings: int | None
    crossing_pairs: int | None
    edges: int
    segments: int
    defects: tuple[str, ...] = ()
    skipped: str | None = None
    work: int = 0
    spent: int = 0


# ---------------------------------------------------------------------------
# Scansione minima del documento
# ---------------------------------------------------------------------------

_NODE_RE = re.compile(
    r"<!--.*?-->"
    r"|<!\[CDATA\[(?P<cdata>.*?)\]\]>"
    r"|<[!?][^>]*>"
    r"|<(?P<close>/)?(?P<name>[A-Za-z_][\w:.-]*)"
    r"(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(?P<void>/)?\s*>"
    r"|(?P<text>[^<]+)",
    re.DOTALL,
)
_ATTR_RE = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.DOTALL)
_RAW_TEXT_TAGS = frozenset({"style", "script"})
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
_PATH_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    ctm: Matrix
    parent: _Element | None
    index: int
    children: list[_Element] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()


def _unescape(text: str) -> str:
    """`html.unescape` che non solleva (gemello di quello di `graph_rules` e
    di `svg_normalize`): `&#` seguito da più cifre del limite di
    conversione degli interi (4.300) dà `ValueError`, e il testo resta
    com'è. Graphviz ricopia il riferimento tale e quale in `id` e `class`."""
    with contextlib.suppress(ValueError):
        return html.unescape(text)
    return text


def _attrs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(raw):
        value = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        out.setdefault(m.group(1).lower(), _unescape(value))
    return out


def _multiply(m: Matrix, n: Matrix) -> Matrix:
    """`m · n` (prima `n`, poi `m`) nella forma `(a, b, c, d, e, f)` SVG."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + c * b2,
        b * a2 + d * b2,
        a * c2 + c * d2,
        b * c2 + d * d2,
        a * e2 + c * f2 + e,
        b * e2 + d * f2 + f,
    )


def parse_transform(value: str | None) -> Matrix:
    """Matrice dell'attributo `transform` (`matrix`, `translate`, `scale`,
    `rotate` con centro facoltativo, `skewX`, `skewY`), composta da sinistra
    a destra come nel DOM; una funzione ignota o malformata vale identità."""
    out = _IDENTITY
    for name, args in _TRANSFORM_RE.findall(value or ""):
        nums = [float(n) for n in _NUMBER_RE.findall(args)]
        fn = name.lower()
        step: Matrix = _IDENTITY
        if fn == "matrix" and len(nums) == 6:
            step = (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
        elif fn == "translate" and nums:
            step = (1.0, 0.0, 0.0, 1.0, nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif fn == "scale" and nums:
            step = (nums[0], 0.0, 0.0, nums[1] if len(nums) > 1 else nums[0], 0.0, 0.0)
        elif fn == "rotate" and nums:
            rad = math.radians(nums[0])
            cos, sin = math.cos(rad), math.sin(rad)
            step = (cos, sin, -sin, cos, 0.0, 0.0)
            if len(nums) == 3:
                cx, cy = nums[1], nums[2]
                step = _multiply(
                    _multiply((1.0, 0.0, 0.0, 1.0, cx, cy), step),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy),
                )
        elif fn == "skewx" and nums:
            step = (1.0, 0.0, math.tan(math.radians(nums[0])), 1.0, 0.0, 0.0)
        elif fn == "skewy" and nums:
            step = (1.0, math.tan(math.radians(nums[0])), 0.0, 1.0, 0.0, 0.0)
        out = _multiply(out, step)
    return out


def _apply(m: Matrix, p: Point) -> Point:
    a, b, c, d, e, f = m
    x, y = p
    return (a * x + c * y + e, b * x + d * y + f)


def _parse_svg(svg: str) -> tuple[_Element | None, list[_Element]]:
    """Radice `<svg>` ed elenco degli elementi in ordine di documento, con la
    trasformazione cumulata verso le unità utente della radice (quella
    della radice stessa esclusa: il viewBox è il sistema di riferimento).
    Il testo dei nodi è accumulato sul `<text>` più vicino."""
    body = svg or ""
    stack: list[_Element] = []
    elements: list[_Element] = []
    root: _Element | None = None
    pos = 0
    while pos < len(body):
        m = _NODE_RE.match(body, pos)
        if m is None:
            pos += 1
            continue
        pos = m.end()
        text = m.group("text") if m.group("cdata") is None else m.group("cdata")
        if text is not None:
            for el in reversed(stack):
                if el.tag == "text":
                    el.text.append(_unescape(text))
                    break
            continue
        name = m.group("name")
        if name is None:
            continue
        tag = name.rsplit(":", 1)[-1].lower()
        if m.group("close"):
            for i in range(len(stack) - 1, -1, -1):
                if stack[i].tag == tag:
                    del stack[i:]
                    break
            continue
        if tag in _RAW_TEXT_TAGS:
            if not m.group("void"):
                end = re.compile(rf"<\s*/\s*{tag}\s*>", re.IGNORECASE).search(body, pos)
                pos = end.end() if end is not None else len(body)
            continue
        attrs = _attrs(m.group("attrs"))
        parent = stack[-1] if stack else None
        if parent is None or root is None:
            ctm = _IDENTITY
        else:
            ctm = _multiply(parent.ctm, parse_transform(attrs.get("transform")))
        el = _Element(tag=tag, attrs=attrs, ctm=ctm, parent=parent, index=len(elements))
        if parent is not None:
            parent.children.append(el)
        elements.append(el)
        if root is None and tag == "svg":
            root = el
        if not m.group("void"):
            stack.append(el)
    return root, elements


def _viewbox(root: _Element) -> tuple[float, float, float, float] | None:
    nums = [float(n) for n in _NUMBER_RE.findall(root.attrs.get("viewbox", ""))]
    if len(nums) == 4 and nums[2] > 0 and nums[3] > 0:
        return nums[0], nums[1], nums[2], nums[3]
    width = _NUMBER_RE.match(root.attrs.get("width", ""))
    height = _NUMBER_RE.match(root.attrs.get("height", ""))
    if width and height and float(width.group(0)) > 0 and float(height.group(0)) > 0:
        return 0.0, 0.0, float(width.group(0)), float(height.group(0))
    return None


# ---------------------------------------------------------------------------
# Tracciati: parsing, lunghezza stimata, campionamento
# ---------------------------------------------------------------------------


def _dist(p: Point, q: Point) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _pieces(length: float, step: float) -> int:
    return max(1, math.ceil(length / step)) if length > 0 else 1


def _path_commands(d: str) -> Iterator[tuple[str, list[float]]]:
    """`(comando, argomenti)` con le ripetizioni implicite esplose (dopo `M`
    le coppie successive valgono `L`)."""
    arity = {"m": 2, "l": 2, "h": 1, "v": 1, "c": 6, "s": 4, "q": 4, "t": 2, "a": 7, "z": 0}
    tokens = _PATH_TOKEN_RE.findall(d or "")
    i, n = 0, len(tokens)
    cmd = ""
    while i < n:
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                yield cmd, []
                continue
        elif not cmd or cmd in "Zz":
            return
        size = arity[cmd.lower()]
        args = tokens[i : i + size]
        if len(args) < size or any(a.isalpha() for a in args):
            return
        yield cmd, [float(a) for a in args]
        i += size
        if cmd == "M":
            cmd = "L"
        elif cmd == "m":
            cmd = "l"


def _arc_curves(
    p0: Point, p1: Point, rx: float, ry: float, angle: float, large: bool, sweep: bool
) -> list[tuple[Point, ...]]:
    """Arco ellittico SVG dai parametri d'estremo (SVG 1.1, F.6.5) come
    cubiche di al più 90° ciascuna; raggio nullo: il segmento; estremi
    coincidenti: nessun pezzo (l'arco è omesso, come nel browser)."""
    if p0 == p1:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < _EPS or ry < _EPS:
        return [(p0, p1)]
    phi = math.radians(angle % 360)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    hx, hy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1 = cos_p * hx + sin_p * hy
    y1 = -sin_p * hx + cos_p * hy
    ratio = (x1 / rx) ** 2 + (y1 / ry) ** 2
    if ratio > 1:  # raggi troppo piccoli: si scalano fino a toccare gli estremi
        rx, ry = rx * math.sqrt(ratio), ry * math.sqrt(ratio)
    num = (rx * ry) ** 2 - (rx * y1) ** 2 - (ry * x1) ** 2
    den = (rx * y1) ** 2 + (ry * x1) ** 2
    # `den` va a zero per sotto-flusso con estremi quasi coincidenti
    # (1e-320): l'arco degenera nel centro sul punto medio.
    ratio_c = num / den if den > 0 else 0.0
    coef = math.sqrt(max(0.0, ratio_c)) if math.isfinite(ratio_c) else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1 / ry, -coef * ry * x1 / rx
    cx = cos_p * cxp - sin_p * cyp + (p0[0] + p1[0]) / 2
    cy = sin_p * cxp + cos_p * cyp + (p0[1] + p1[1]) / 2
    ux, uy = (x1 - cxp) / rx, (y1 - cyp) / ry
    vx, vy = (-x1 - cxp) / rx, (-y1 - cyp) / ry
    theta = math.atan2(uy, ux)
    delta = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    count = max(1, math.ceil(abs(delta) / (math.pi / 2) - 1e-9))
    part = delta / count
    k = 4 / 3 * math.tan(part / 4)

    def at(u: float, v: float) -> Point:
        return (cx + rx * u * cos_p - ry * v * sin_p, cy + rx * u * sin_p + ry * v * cos_p)

    curves: list[tuple[Point, ...]] = []
    start = p0
    for i in range(count):
        c1, s1 = math.cos(theta + i * part), math.sin(theta + i * part)
        c2, s2 = math.cos(theta + (i + 1) * part), math.sin(theta + (i + 1) * part)
        end = p1 if i == count - 1 else at(c2, s2)
        curves.append((start, at(c1 - k * s1, s1 + k * c1), at(c2 + k * s2, s2 - k * c2), end))
        start = end
    return curves


def _path_pieces(d: str, ctm: Matrix) -> list[list[tuple[Point, ...]]]:
    """Pezzi di ogni sotto-tracciato di un attributo `d`: un pezzo è la
    tupla dei suoi punti di controllo già trasformati nel sistema della
    radice (2 per un tratto, 3 per una quadratica, 4 per una cubica),
    l'inizio incluso. Le curve si trasformano PRIMA del campionamento: una
    mappa affine conserva le Bézier e il passo resta in unità della radice."""
    subpaths: list[list[tuple[Point, ...]]] = []
    cur: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_ctrl: Point | None = None
    last_cmd = ""
    pieces: list[tuple[Point, ...]] = []

    def flush() -> None:
        nonlocal pieces
        if pieces:
            subpaths.append(pieces)
        pieces = []

    def add(*ctrl: Point) -> None:
        pieces.append(tuple(_apply(ctm, p) for p in ctrl))

    for cmd, a in _path_commands(d):
        rel = cmd.islower()
        op = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        if op == "M":
            flush()
            cur = (a[0] + ox, a[1] + oy)
            start = cur
            last_ctrl = None
        elif op == "L":
            nxt = (a[0] + ox, a[1] + oy)
            add(cur, nxt)
            cur, last_ctrl = nxt, None
        elif op == "H":
            nxt = (a[0] + ox, cur[1])
            add(cur, nxt)
            cur, last_ctrl = nxt, None
        elif op == "V":
            nxt = (cur[0], a[0] + (cur[1] if rel else 0.0))
            add(cur, nxt)
            cur, last_ctrl = nxt, None
        elif op in ("C", "S"):
            if op == "C":
                c1 = (a[0] + ox, a[1] + oy)
                c2 = (a[2] + ox, a[3] + oy)
                end = (a[4] + ox, a[5] + oy)
            else:
                reflect = last_ctrl if last_cmd in ("C", "S") and last_ctrl else cur
                c1 = (2 * cur[0] - reflect[0], 2 * cur[1] - reflect[1])
                c2 = (a[0] + ox, a[1] + oy)
                end = (a[2] + ox, a[3] + oy)
            add(cur, c1, c2, end)
            cur, last_ctrl = end, c2
        elif op in ("Q", "T"):
            if op == "Q":
                c1 = (a[0] + ox, a[1] + oy)
                end = (a[2] + ox, a[3] + oy)
            else:
                reflect = last_ctrl if last_cmd in ("Q", "T") and last_ctrl else cur
                c1 = (2 * cur[0] - reflect[0], 2 * cur[1] - reflect[1])
                end = (a[0] + ox, a[1] + oy)
            add(cur, c1, end)
            cur, last_ctrl = end, c1
        elif op == "A":
            end = (a[5] + ox, a[6] + oy)
            for ctrl in _arc_curves(cur, end, a[0], a[1], a[2], a[3] != 0, a[4] != 0):
                add(*ctrl)
            cur, last_ctrl = end, None
        elif op == "Z":
            if cur != start:
                add(cur, start)
            cur, last_ctrl = start, None
            flush()
        last_cmd = op
    flush()
    return subpaths


def _piece_length(piece: tuple[Point, ...]) -> float:
    """Lunghezza stimata: la corda per un tratto, la media fra corda e
    poligono di controllo per le curve (errore sotto l'1 % a questo passo)."""
    chord = _dist(piece[0], piece[-1])
    if len(piece) == 2:
        return chord
    poly = sum(_dist(piece[i], piece[i + 1]) for i in range(len(piece) - 1))
    return (chord + poly) / 2


def _bezier(piece: tuple[Point, ...], t: float) -> Point:
    u = 1 - t
    if len(piece) == 2:
        (x0, y0), (x1, y1) = piece
        return (u * x0 + t * x1, u * y0 + t * y1)
    if len(piece) == 3:
        (x0, y0), (x1, y1), (x2, y2) = piece
        return (
            u * u * x0 + 2 * u * t * x1 + t * t * x2,
            u * u * y0 + 2 * u * t * y1 + t * t * y2,
        )
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = piece
    return (
        u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
        u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3,
    )


def _sample_pieces(pieces: Sequence[tuple[Point, ...]], step: float) -> list[Point]:
    points: list[Point] = [pieces[0][0]]
    for piece in pieces:
        n = _pieces(_piece_length(piece), step)
        points.extend(_bezier(piece, i / n) for i in range(1, n + 1))
    return points


@dataclass(frozen=True)
class _Track:
    unit: int
    subpaths: tuple[tuple[tuple[Point, ...], ...], ...]  # pezzi per sotto-tracciato
    endpoints: tuple[Point, ...]
    estimate: int  # segmenti stimati (0 se `finite` è falso)
    finite: bool  # tutte le lunghezze dei pezzi sono finite

    def sampled(self, step: float) -> list[list[Point]]:
        return [_sample_pieces(sub, step) for sub in self.subpaths if sub]


def _polyline_pieces(points: Sequence[Point]) -> tuple[tuple[Point, ...], ...]:
    return tuple((points[i], points[i + 1]) for i in range(len(points) - 1))


def _element_pieces(el: _Element) -> list[tuple[tuple[Point, ...], ...]]:
    """Pezzi (già trasformati) di un `<path>`, `<line>` o `<polyline>`."""
    if el.tag == "path":
        return [tuple(pieces) for pieces in _path_pieces(el.attrs.get("d", ""), el.ctm)]
    if el.tag == "line":
        try:
            p = (float(el.attrs.get("x1", 0)), float(el.attrs.get("y1", 0)))
            q = (float(el.attrs.get("x2", 0)), float(el.attrs.get("y2", 0)))
        except ValueError:
            return []
        return [((_apply(el.ctm, p), _apply(el.ctm, q)),)]
    nums = [float(n) for n in _NUMBER_RE.findall(el.attrs.get("points", ""))]
    pts = [_apply(el.ctm, (nums[i], nums[i + 1])) for i in range(0, len(nums) - 1, 2)]
    return [_polyline_pieces(pts)] if len(pts) >= 2 else []


def is_edge_class(name: str) -> bool:
    """Una classe d'arco Mermaid (elenco del docstring)."""
    return name in EDGE_CLASS_NAMES or name.startswith(EDGE_CLASS_PREFIXES)


def is_anchor_wrapper(el: _Element) -> bool:
    """Involucro di un collegamento di Graphviz (`tooltip`, `URL`, …): un
    `<a>` o il `<g>` senza classe con id `a_…` che lo contiene."""
    if el.tag == "a":
        return True
    return el.tag == "g" and not el.classes and el.attrs.get("id", "").startswith("a_")


def _owner(el: _Element) -> _Element | None:
    """Il genitore di `el` saltando gli involucri dei collegamenti."""
    parent = el.parent
    while parent is not None and is_anchor_wrapper(parent):
        parent = parent.parent
    return parent


def _own_children(group: _Element) -> Iterator[_Element]:
    """I figli di un gruppo, con quelli degli involucri dei collegamenti."""
    for child in group.children:
        if is_anchor_wrapper(child):
            yield from _own_children(child)
        else:
            yield child


def _edge_unit(el: _Element) -> int | None:
    """Indice dell'unità «arco» di un tracciato, `None` se non è un arco."""
    if el.tag not in ("path", "line", "polyline"):
        return None
    parent = _owner(el)
    if parent is not None and parent.tag == "g" and EDGE_GROUP_CLASSES & set(parent.classes):
        return parent.index if el.tag == "path" else None
    if any(is_edge_class(c) for c in el.classes):
        return el.index
    return None


def _edge_tracks(elements: Iterable[_Element], step: float) -> list[_Track]:
    tracks: list[_Track] = []
    for el in elements:
        unit = _edge_unit(el)
        if unit is None:
            continue
        subpaths = tuple(sub for sub in _element_pieces(el) if sub)
        if not subpaths:
            continue
        # Lunghezze in unità della radice, PRIMA di campionare: un valore
        # non finito (coordinate o trasformazioni assurde) non ha stima.
        lengths = [_piece_length(p) for sub in subpaths for p in sub]
        finite = all(math.isfinite(v) for v in lengths)
        estimate = sum(_pieces(v, step) for v in lengths) if finite else 0
        endpoints = (subpaths[0][0][0], subpaths[-1][-1][-1])
        tracks.append(
            _Track(
                unit=unit,
                subpaths=subpaths,
                endpoints=endpoints,
                estimate=estimate,
                finite=finite,
            )
        )
    return tracks


# ---------------------------------------------------------------------------
# Incroci
# ---------------------------------------------------------------------------


def _orient(a: Point, b: Point, c: Point) -> float:
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return 0.0 if abs(value) < _EPS else value


def segment_intersection(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    """Punto d'incontro dei segmenti `ab` e `cd`, estremi inclusi; `None` se
    disgiunti o collineari."""
    d1 = _orient(c, d, a)
    d2 = _orient(c, d, b)
    if (d1 > 0 and d2 > 0) or (d1 < 0 and d2 < 0) or (d1 == 0 and d2 == 0):
        return None
    d3 = _orient(a, b, c)
    d4 = _orient(a, b, d)
    if (d3 > 0 and d4 > 0) or (d3 < 0 and d4 < 0):
        return None
    if not all(math.isfinite(v) for v in (d1, d2, d3, d4)):
        return None  # coordinate al limite dei float: nessun punto affidabile
    t = d1 / (d1 - d2)
    point = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return point if math.isfinite(point[0]) and math.isfinite(point[1]) else None


def _near(p: Point, targets: Iterable[Point], tol: float) -> bool:
    return any(_dist(p, q) < tol for q in targets)


Box = tuple[float, float, float, float]  # x0, y0, x1, y1
Span = tuple[int, int, int, int]  # colonna e riga minime, colonna e riga massime
_Segment = tuple[Point, Point, int, int]  # (p, q, unità, tracciato)


def _finite_box(box: Box) -> bool:
    """Riquadro con coordinate e semiperimetro finiti."""
    return all(math.isfinite(v) for v in box) and math.isfinite(
        (box[2] - box[0]) + (box[3] - box[1])
    )


@dataclass(frozen=True)
class _Grid:
    """Griglia uniforme dei confronti sulla tela (docstring, «Costo
    limitato per costruzione»): passo adattivo, coordinate ritagliate alla
    tela e indici relativi alla sua origine, quindi al più
    `MAX_GRID_CELLS` celle. La chiave di una cella è `colonna · rows +
    riga`."""

    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    rows: int

    @classmethod
    def over(cls, canvas: Box) -> _Grid:
        x0, y0, x1, y1 = canvas
        side = math.isqrt(MAX_GRID_CELLS)
        size = max(GRID_CELL, ((x1 - x0) + (y1 - y0)) / (2 * (side - 2)))
        return cls(x0, y0, x1, y1, size, math.floor((y1 - y0) / size) + 1)

    def spans(self, boxes: Iterable[Box]) -> list[Span]:
        """Impronta ritagliata di ogni riquadro, in aritmetica."""
        x0, y0, x1, y1, size = self.x0, self.y0, self.x1, self.y1, self.size
        floor = math.floor
        out: list[Span] = []
        for bx0, by0, bx1, by1 in boxes:
            bx0 = x0 if bx0 < x0 else x1 if bx0 > x1 else bx0
            bx1 = x0 if bx1 < x0 else x1 if bx1 > x1 else bx1
            by0 = y0 if by0 < y0 else y1 if by0 > y1 else by0
            by1 = y0 if by1 < y0 else y1 if by1 > y1 else by1
            out.append(
                (
                    floor((bx0 - x0) / size),
                    floor((by0 - y0) / size),
                    floor((bx1 - x0) / size),
                    floor((by1 - y0) / size),
                )
            )
        return out

    def key(self, x: float, y: float) -> int:
        """Chiave della cella di un punto (ritagliato alla tela)."""
        x = self.x0 if x < self.x0 else self.x1 if x > self.x1 else x
        y = self.y0 if y < self.y0 else self.y1 if y > self.y1 else y
        column = math.floor((x - self.x0) / self.size)
        return column * self.rows + math.floor((y - self.y0) / self.size)


def _cell_count(spans: Iterable[Span]) -> int:
    """Celle occupate dalle impronte, senza enumerarle."""
    return sum((s[2] - s[0] + 1) * (s[3] - s[1] + 1) for s in spans)


@dataclass(frozen=True)
class _Index:
    """Impronte (una per riquadro, nell'ordine dei riquadri) e griglia
    `chiave → indici` sulla stessa `_Grid`: costruito una volta sola, dopo
    che `_cell_count` ne ha passato il tetto."""

    grid: _Grid
    spans: list[Span]
    cells: dict[int, list[int]]

    @classmethod
    def build(cls, grid: _Grid, spans: list[Span]) -> _Index:
        cells: dict[int, list[int]] = defaultdict(list)
        rows = grid.rows
        for i, (gx0, gy0, gx1, gy1) in enumerate(spans):
            for gx in range(gx0, gx1 + 1):
                base = gx * rows
                for gy in range(gy0, gy1 + 1):
                    cells[base + gy].append(i)
        return cls(grid, spans, cells)

    def pairs(self) -> Iterator[tuple[int, int]]:
        """Coppie `(i, j)` con `i < j` che condividono una cella, ciascuna
        una volta sola: nella cella d'angolo (colonna e riga minime)
        dell'intersezione delle due impronte."""
        rows, spans = self.grid.rows, self.spans
        for key, members in self.cells.items():
            gx, gy = divmod(key, rows)
            for x, i in enumerate(members):
                ax, ay = spans[i][0], spans[i][1]
                for j in members[x + 1 :]:
                    bx, by = spans[j][0], spans[j][1]
                    if (ax if ax > bx else bx) == gx and (ay if ay > by else by) == gy:
                        yield i, j


def _segments(
    tracks: Sequence[tuple[int, Sequence[Sequence[Point]]]],
) -> tuple[list[_Segment], list[tuple[Point, Point]]]:
    """Segmenti dei tracciati campionati ed estremi di ogni tracciato."""
    segs: list[_Segment] = []
    ends: list[tuple[Point, Point]] = []
    for t_index, (unit, subs) in enumerate(tracks):
        polys = [list(s) for s in subs if len(s) >= 2]
        if not polys:
            ends.append(((math.inf, math.inf), (math.inf, math.inf)))
            continue
        ends.append((polys[0][0], polys[-1][-1]))
        for poly in polys:
            segs.extend((poly[i], poly[i + 1], unit, t_index) for i in range(len(poly) - 1))
    return segs, ends


def _segment_boxes(segs: Iterable[_Segment]) -> Iterator[Box]:
    for (px, py), (qx, qy), _u, _t in segs:
        yield (px if px < qx else qx, py if py < qy else qy,
               qx if px < qx else px, qy if py < qy else py)  # fmt: skip


def _pair_work(sizes: Iterable[int]) -> int:
    """Coppie candidate di una griglia, dati gli occupanti di ogni cella."""
    return sum(n * (n - 1) // 2 for n in sizes)


def count_crossings(
    tracks: Sequence[tuple[int, Sequence[Sequence[Point]]]],
    *,
    endpoint_tolerance: float = ENDPOINT_TOLERANCE,
    cluster_radius: float = CLUSTER_RADIUS,
) -> tuple[int, int]:
    """`(punti distinti, coppie incidenti)` fra tracciati di archi diversi.

    `tracks` è una sequenza di `(unità, sotto-tracciati campionati)` con
    punti finiti; gli estremi di ogni tracciato sono il primo punto del
    primo sotto-tracciato e l'ultimo dell'ultimo (come
    `getPointAtLength(0)` e `(L)` nel DOM). La tela è il riquadro unione
    dei segmenti. Nessun tetto di lavoro: lo applica `measure_svg`."""
    segs, ends = _segments(tracks)
    grid = _Grid.over(_union(_segment_boxes(segs)) or (0.0, 0.0, 0.0, 0.0))
    index = _Index.build(grid, grid.spans(_segment_boxes(segs)))
    hits = _grid_hits(segs, ends, index, endpoint_tolerance=endpoint_tolerance)
    return _cluster(hits, cluster_radius)


def _grid_hits(
    segs: Sequence[_Segment],
    ends: Sequence[tuple[Point, Point]],
    index: _Index,
    *,
    endpoint_tolerance: float,
) -> list[tuple[Point, int, int]]:
    """Punti d'incontro fra segmenti di archi diversi, lontani dagli estremi
    dei due tracciati; ogni coppia della griglia è provata una volta."""
    hits: list[tuple[Point, int, int]] = []
    for i, j in index.pairs():
        p, q, ui, ti = segs[i]
        r, s, uj, tj = segs[j]
        if ui == uj:
            continue
        point = segment_intersection(p, q, r, s)
        if point is None:
            continue
        if _near(point, ends[ti], endpoint_tolerance) or _near(point, ends[tj], endpoint_tolerance):
            continue
        hits.append((point, min(ui, uj), max(ui, uj)))
    return hits


# Coppie di celle vicine del raggruppamento (metà della finestra 5 × 5).
_CLUSTER_OFFSETS: tuple[tuple[int, int], ...] = tuple(
    (dx, dy) for dx in range(3) for dy in range(-2, 3) if dx > 0 or dy > 0
)
# Margine relativo delle decisioni sui riquadri (errori di arrotondamento).
_CLUSTER_MARGIN = 1e-9
_CellKey = tuple[int, int]


@dataclass(frozen=True)
class _ClusterPlan:
    """Celle dei punti, coppie di celle da unire senza confronti, coppie
    incerte e il costo del raggruppamento (`work`), noto prima di
    confrontare un solo punto."""

    cells: dict[_CellKey, list[int]]
    joined: list[tuple[_CellKey, _CellKey]]
    unsure: list[tuple[_CellKey, _CellKey]]
    work: int


def _cluster_plan(hits: Sequence[tuple[Point, int, int]], radius: float) -> _ClusterPlan:
    """Celle di lato appena sotto `radius / √2`: due punti della stessa
    cella distano meno di `radius` e due punti entro `radius` stanno in
    celle distanti al più due passi. Per ogni coppia di celle vicine i
    riquadri dei loro punti dicono se tutte le coppie sono entro il raggio
    (unite), se nessuna lo è (ignorate) o se servono i confronti (al più
    `|A| · |B|`, contati). Il costo è `2 · punti + 12 · celle +
    confronti incerti`."""
    side = radius / math.sqrt(2) * (1 - _CLUSTER_MARGIN)
    cells: dict[_CellKey, list[int]] = defaultdict(list)
    boxes: dict[_CellKey, list[float]] = {}
    for i, ((x, y), _a, _b) in enumerate(hits):
        key = (math.floor(x / side), math.floor(y / side))
        cells[key].append(i)
        box = boxes.get(key)
        if box is None:
            boxes[key] = [x, y, x, y]
        else:
            box[0], box[1] = min(box[0], x), min(box[1], y)
            box[2], box[3] = max(box[2], x), max(box[3], y)
    near_max = radius * (1 + _CLUSTER_MARGIN)
    far_max = radius * (1 - _CLUSTER_MARGIN)
    joined: list[tuple[_CellKey, _CellKey]] = []
    unsure: list[tuple[_CellKey, _CellKey]] = []
    checks = 0
    for (gx, gy), members in cells.items():
        a = boxes[(gx, gy)]
        for dx, dy in _CLUSTER_OFFSETS:
            other = (gx + dx, gy + dy)
            b = boxes.get(other)
            if b is None:
                continue
            near = math.hypot(
                max(0.0, b[0] - a[2], a[0] - b[2]), max(0.0, b[1] - a[3], a[1] - b[3])
            )
            if near > near_max:
                continue
            far = math.hypot(max(a[2] - b[0], b[2] - a[0]), max(a[3] - b[1], b[3] - a[1]))
            if far <= far_max:
                joined.append(((gx, gy), other))
            else:
                unsure.append(((gx, gy), other))
                checks += len(members) * len(cells[other])
    work = 2 * len(hits) + len(_CLUSTER_OFFSETS) * len(cells) + checks
    return _ClusterPlan(cells, joined, unsure, work)


def _cluster(
    hits: Sequence[tuple[Point, int, int]], radius: float, plan: _ClusterPlan | None = None
) -> tuple[int, int]:
    """Raggruppa i punti entro `radius` (componenti connesse del grafo
    «distanza ≤ radius», union-find sul piano di `_cluster_plan`) e ritorna
    `(gruppi, coppie di unità distinte sommate sui gruppi)`."""
    if plan is None:
        plan = _cluster_plan(hits, radius)
    parent = list(range(len(hits)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for members in plan.cells.values():
        for i in members[1:]:
            parent[find(i)] = find(members[0])
    for a, b in plan.joined:
        parent[find(plan.cells[a][0])] = find(plan.cells[b][0])
    for a, b in plan.unsure:
        first, second = plan.cells[a], plan.cells[b]
        if find(first[0]) == find(second[0]):
            continue
        if any(_dist(hits[i][0], hits[j][0]) <= radius for i in first for j in second):
            parent[find(first[0])] = find(second[0])
    pairs: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for i, (_point, a_unit, b_unit) in enumerate(hits):
        pairs[find(i)].add((a_unit, b_unit))
    return len(pairs), sum(len(v) for v in pairs.values())


# ---------------------------------------------------------------------------
# Riquadri dei testi e difetti di lettura (DOT)
# ---------------------------------------------------------------------------


def font_kind(family: str) -> str | None:
    """Tabella di avanzamenti per un valore di `font-family`, letto dal primo
    nome: `FONT_SANS` (Noto Sans, o attributo assente), `FONT_SERIF`
    (`Times…`, `serif`), `FONT_MONO` (`Courier…`, `monospace`); `None` per
    ogni altra famiglia (nessuna stima: punto cieco del docstring)."""
    first = family.split(",")[0].strip().strip("'\"").strip().lower()
    if first in ("", "noto sans"):
        return FONT_SANS
    if first.startswith("times") or first == "serif":
        return FONT_SERIF
    if first.startswith("courier") or first == "monospace":
        return FONT_MONO
    return None


def _char_width(ch: str, kind: str) -> int:
    table, space, default = _FONT_TABLES[kind]
    code = ord(ch)
    if 32 <= code <= 126:
        return table[code - 32] if table is not None else default
    if ch.isspace():
        return space
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return _WIDE_WIDTH
    base = unicodedata.normalize("NFD", ch)[0]
    if base != ch and 32 <= ord(base) <= 126:
        return table[ord(base) - 32] if table is not None else default
    return default


def estimate_text_width(text: str, font_size: float, *, kind: str = FONT_SANS) -> float:
    """Larghezza stimata di una riga in unità utente (avanzamenti della
    tabella `kind`, Noto Sans per default; niente crenatura)."""
    return sum(_char_width(ch, kind) for ch in text) * font_size / 1000.0


def _union(boxes: Iterable[Box]) -> Box | None:
    out: Box | None = None
    for b in boxes:
        out = b if out is None else (min(out[0], b[0]), min(out[1], b[1]),
                                     max(out[2], b[2]), max(out[3], b[3]))  # fmt: skip
    return out


def _points_box(points: Iterable[Point]) -> Box | None:
    pts = list(points)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _derivative_roots(values: Sequence[float]) -> list[float]:
    """Parametri in (0, 1) dove si annulla la derivata di una coordinata di
    una quadratica o di una cubica (forma stabile della formula
    risolutiva)."""
    if len(values) == 3:
        p0, p1, p2 = values
        den = p0 - 2 * p1 + p2
        roots = [(p0 - p1) / den] if den != 0 else []
    elif len(values) == 4:
        d0, d1, d2 = values[1] - values[0], values[2] - values[1], values[3] - values[2]
        a, b, c = d0 - 2 * d1 + d2, 2 * (d1 - d0), d0
        disc = b * b - 4 * a * c
        if a == 0:
            roots = [-c / b] if b != 0 else []
        elif disc < 0:
            roots = []
        else:
            q = -(b + math.copysign(math.sqrt(disc), b)) / 2
            roots = [q / a] + ([c / q] if q != 0 else [])
    else:
        roots = []
    return [t for t in roots if 0 < t < 1]


def _piece_box(piece: tuple[Point, ...]) -> Box:
    """Riquadro esatto di un pezzo: gli estremi e i punti a derivata nulla
    (costo costante, qualunque sia la lunghezza)."""
    points = [piece[0], piece[-1]]
    if len(piece) > 2:
        for axis in (0, 1):
            points.extend(_bezier(piece, t) for t in _derivative_roots([p[axis] for p in piece]))
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _finite_or_none(box: Box | None) -> Box | None:
    return box if box is not None and _finite_box(box) else None


def _shape_box(el: _Element) -> Box | None:
    """Riquadro di una forma di nodo o di cluster nel sistema della radice;
    `None` se vuoto o non finito."""
    if el.tag == "ellipse":
        try:
            cx, cy = float(el.attrs.get("cx", 0)), float(el.attrs.get("cy", 0))
            rx, ry = float(el.attrs.get("rx", 0)), float(el.attrs.get("ry", 0))
        except ValueError:
            return None
        corners = [(cx - rx, cy - ry), (cx + rx, cy - ry), (cx - rx, cy + ry), (cx + rx, cy + ry)]
        return _finite_or_none(_points_box(_apply(el.ctm, p) for p in corners))
    if el.tag == "polygon":
        nums = [float(n) for n in _NUMBER_RE.findall(el.attrs.get("points", ""))]
        return _finite_or_none(
            _points_box(_apply(el.ctm, (nums[i], nums[i + 1])) for i in range(0, len(nums) - 1, 2))
        )
    pieces = _element_pieces(el)
    return _finite_or_none(_union(_piece_box(p) for sub in pieces for p in sub))


def _font_size(el: _Element) -> float:
    node: _Element | None = el
    while node is not None:
        raw = node.attrs.get("font-size")
        if raw is None:
            style = re.search(r"font-size\s*:\s*([-+.\d]+)", node.attrs.get("style", ""))
            raw = style.group(1) if style else None
        if raw is not None:
            m = _NUMBER_RE.match(raw.strip())
            if m and float(m.group(0)) > 0:
                return float(m.group(0))
        node = node.parent
    return 14.0


def _font_family(el: _Element) -> str:
    node: _Element | None = el
    while node is not None:
        raw = node.attrs.get("font-family")
        if raw is None:
            style = re.search(r"font-family\s*:\s*([^;]+)", node.attrs.get("style", ""))
            raw = style.group(1) if style else None
        if raw is not None:
            return raw
        node = node.parent
    return ""


def _text_box(el: _Element) -> tuple[str, Box] | None:
    """Testo e riquadro stimato (già ristretto di `ESTIMATE_SLACK` per
    lato); `None` se vuoto, senza coordinate o in una famiglia senza
    stima."""
    raw = "".join(el.text)
    preserve = el.attrs.get("xml:space") == "preserve"
    content = raw.replace("\n", " ") if preserve else " ".join(raw.split())
    if not content.strip():
        return None
    kind = font_kind(_font_family(el))
    if kind is None:
        return None
    try:
        x = float(_NUMBER_RE.findall(el.attrs.get("x", "0"))[0])
        y = float(_NUMBER_RE.findall(el.attrs.get("y", "0"))[0])
    except (IndexError, ValueError):
        return None
    size = _font_size(el)
    width = estimate_text_width(content, size, kind=kind)
    anchor = el.attrs.get("text-anchor", "start")
    x0 = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
    left, right = x0 + ESTIMATE_SLACK * width, x0 + width - ESTIMATE_SLACK * width
    corners = [
        (left, y - _TEXT_ASCENT_EM * size),
        (right, y - _TEXT_ASCENT_EM * size),
        (left, y + _TEXT_DESCENT_EM * size),
        (right, y + _TEXT_DESCENT_EM * size),
    ]
    box = _finite_or_none(_points_box(_apply(el.ctm, p) for p in corners))
    return (content.strip(), box) if box is not None else None


def _overflow(inner: Box, outer: Box) -> float:
    """Quanto `inner` sborda da `outer` (negativo se tutto dentro)."""
    return max(outer[0] - inner[0], inner[2] - outer[2], outer[1] - inner[1], inner[3] - outer[3])


def _short(text: str) -> str:
    return text if len(text) <= _LABEL_CHARS else text[: _LABEL_CHARS - 1] + "…"


@dataclass(frozen=True)
class _Label:
    text: str
    box: Box
    group: int
    kind: str
    owner: Box | None


def _dot_labels(elements: Sequence[_Element]) -> list[_Label]:
    labels: list[_Label] = []
    for g in elements:
        if g.tag != "g":
            continue
        kinds = [c for c in g.classes if c in ("node", "cluster", "edge", "graph")]
        if not kinds:
            continue
        kind = kinds[0]
        children = list(_own_children(g))
        owner = None
        if kind in ("node", "cluster"):
            shapes = [c for c in children if c.tag in ("ellipse", "polygon", "path", "polyline")]
            owner = _union(b for b in (_shape_box(s) for s in shapes) if b is not None)
        for child in children:
            if child.tag != "text":
                continue
            measured = _text_box(child)
            if measured is not None:
                labels.append(_Label(measured[0], measured[1], g.index, kind, owner))
    return labels


def _label_index(labels: Sequence[_Label], grid: _Grid | None = None) -> _Index:
    """Griglia delle etichette (riquadri interi) su `grid` o, se manca, sul
    riquadro unione delle etichette."""
    boxes = [lab.box for lab in labels]
    if grid is None:
        grid = _Grid.over(_union(boxes) or (0.0, 0.0, 0.0, 0.0))
    return _Index.build(grid, grid.spans(boxes))


def _label_work(labels: _Index, segments: _Index) -> int:
    """Coppie candidate dei controlli sulle etichette, sulla stessa
    griglia dei segmenti: etichetta × etichetta (sovrapposizioni) ed
    etichetta × segmento (attraversamenti: ogni punto campionato è estremo
    di un segmento della sua cella, quindi i punti di una cella sono al
    più il doppio dei suoi segmenti)."""
    crossing = sum(len(v) * len(segments.cells.get(k, ())) for k, v in labels.cells.items())
    return _pair_work(len(v) for v in labels.cells.values()) + crossing


def _overlaps(labels: Sequence[_Label], index: _Index) -> list[tuple[_Label, _Label, float]]:
    """Coppie sovrapposte oltre `TEXT_TOLERANCE`, cercate sulla griglia
    `index` e riportate nell'ordine dei riquadri per ascissa (a sinistra
    la prima)."""
    order = sorted(range(len(labels)), key=lambda i: labels[i].box[0])
    rank = {position: pos for pos, position in enumerate(order)}
    found: dict[tuple[int, int], float] = {}
    for i, j in index.pairs():
        a, b = labels[i].box, labels[j].box
        overlap = min(min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1]))
        if overlap > TEXT_TOLERANCE:
            key = (rank[i], rank[j]) if rank[i] < rank[j] else (rank[j], rank[i])
            found[key] = overlap
    return [(labels[order[p]], labels[order[q]], found[(p, q)]) for p, q in sorted(found)]


def _dot_defects(
    labels: Sequence[_Label],
    index: _Index,
    edges: Sequence[tuple[int, list[list[Point]]]],
    canvas: Box | None,
) -> list[str]:
    """I quattro difetti di lettura; `index` è la griglia delle etichette
    già costruita (e già contata) da `measure_svg`."""
    defects: list[str] = []
    owner_word = {"node": "nodo", "cluster": "cluster"}
    for lab in labels:
        if canvas is not None:
            out = _overflow(lab.box, canvas)
            if out > TEXT_TOLERANCE:
                defects.append(
                    f"{DEFECT_TEXT_OUTSIDE_CANVAS}: «{_short(lab.text)}» fuori dalla tela di "
                    f"{out:.1f}"
                )
        if lab.owner is not None:
            out = _overflow(lab.box, lab.owner)
            if out > TEXT_TOLERANCE:
                defects.append(
                    f"{DEFECT_TEXT_OUTSIDE_OWNER}: «{_short(lab.text)}» fuori dal suo "
                    f"{owner_word[lab.kind]} di {out:.1f}"
                )
    defects.extend(
        f"{DEFECT_LABELS_OVERLAP}: «{_short(a.text)}»/«{_short(b.text)}» "
        f"sovrapposte di {overlap:.1f}"
        for a, b, overlap in _overlaps(labels, index)
    )
    # Attraversamento: candidati dalla griglia dei riquadri interi, prova
    # sul riquadro ristretto di 1 unità per lato.
    grid, cells = index.grid, index.cells
    crossed: set[int] = set()
    for group, polys in edges:
        for poly in polys:
            for x, y in poly:
                for i in cells.get(grid.key(x, y), ()):
                    lab = labels[i]
                    if i in crossed or lab.group == group:
                        continue
                    box = lab.box
                    if box[0] + 1 < x < box[2] - 1 and box[1] + 1 < y < box[3] - 1:
                        crossed.add(i)
    defects.extend(
        f"{DEFECT_EDGE_CROSSES_LABEL}: un arco attraversa «{_short(labels[i].text)}»"
        for i in sorted(crossed)
    )
    return list(dict.fromkeys(defects))


# ---------------------------------------------------------------------------
# Ingressi pubblici
# ---------------------------------------------------------------------------


def measure_svg(
    svg: str,
    *,
    dot_defects: bool = False,
    max_segments: int = MAX_MEASURE_SEGMENTS,
    max_work: int = MAX_MEASURE_WORK,
    work_left: int | None = None,
    step: float = SAMPLE_STEP,
) -> GeometryReport:
    """Incroci arco × arco di un SVG reso (qualunque renderer, stesse classi
    del JS) e, con `dot_defects=True`, i quattro difetti di lettura DOT.

    Tetti (docstring del modulo): oltre `max_segments` segmenti stimati,
    oltre `max_work` unità di lavoro o oltre `work_left` (residuo del
    batch; `None` = nessun batch, `<= 0` = esaurito: nemmeno il parsing)
    la misura è saltata e `work` riporta il lavoro contato, `spent` quello
    eseguito. Il lavoro è contato in tre tappe, ciascuna PRIMA di
    eseguirla: celle delle impronte, coppie candidate, controlli del
    raggruppamento. Non solleva su SVG malformati: senza radice torna un
    report vuoto con `skipped`."""
    if work_left is not None and work_left <= 0:
        return GeometryReport(None, None, 0, 0, skipped=SKIP_BATCH_WORK_CAP)
    root, elements = _parse_svg(svg)
    if root is None:
        return GeometryReport(None, None, 0, 0, skipped="no_svg")
    tracks = _edge_tracks(elements, step)
    units = len({t.unit for t in tracks})
    if not all(t.finite for t in tracks):
        return GeometryReport(None, None, units, 0, skipped=SKIP_RANGE)
    estimate = sum(t.estimate for t in tracks)
    if estimate > max_segments:
        return GeometryReport(None, None, units, estimate, skipped=SKIP_FIGURE_CAP)
    sampled = [(t.unit, t.sampled(step)) for t in tracks]
    segments = sum(max(0, len(p) - 1) for _u, polys in sampled for p in polys)
    segs, ends = _segments(sampled)
    labels = _dot_labels(elements) if dot_defects else []
    vb = _viewbox(root)
    view = (vb[0], vb[1], vb[0] + vb[2], vb[1] + vb[3]) if vb is not None else None
    if view is not None and _finite_box(view):
        canvas: Box | None = view
    else:
        union = _union(itertools.chain(_segment_boxes(segs), (lab.box for lab in labels)))
        canvas = _finite_or_none(union or (0.0, 0.0, 0.0, 0.0))
    if canvas is None:
        return GeometryReport(None, None, units, segments, skipped=SKIP_RANGE)

    def capped(work: int, spent: int) -> GeometryReport | None:
        if work > max_work:
            reason = SKIP_WORK_CAP
        elif work_left is not None and work > work_left:
            reason = SKIP_BATCH_WORK_CAP
        else:
            return None
        return GeometryReport(None, None, units, segments, skipped=reason, work=work, spent=spent)

    grid = _Grid.over(canvas)
    seg_spans = grid.spans(_segment_boxes(segs))
    label_spans = grid.spans(lab.box for lab in labels)
    work = _cell_count(seg_spans) + _cell_count(label_spans)
    if (report := capped(work, 0)) is not None:
        return report
    seg_index = _Index.build(grid, seg_spans)
    label_index = _Index.build(grid, label_spans)
    spent = work
    seg_pairs = _pair_work(len(v) for v in seg_index.cells.values())
    work += seg_pairs + (_label_work(label_index, seg_index) if dot_defects else 0)
    if (report := capped(work, spent)) is not None:
        return report
    hits = _grid_hits(segs, ends, seg_index, endpoint_tolerance=ENDPOINT_TOLERANCE)
    spent += seg_pairs
    plan = _cluster_plan(hits, CLUSTER_RADIUS)
    work += plan.work
    if (report := capped(work, spent)) is not None:
        return report
    crossings, pairs = _cluster(hits, CLUSTER_RADIUS, plan)
    defects: list[str] = []
    if dot_defects:
        dot_edges = [
            (t.unit, polys)
            for t, (_u, polys) in zip(tracks, sampled, strict=True)
            if _is_dot_edge_group(elements[t.unit])
        ]
        defects = _dot_defects(labels, label_index, dot_edges, view)
    return GeometryReport(
        crossings=crossings,
        crossing_pairs=pairs,
        edges=units,
        segments=segments,
        defects=tuple(defects[:MAX_DEFECTS]),
        work=work,
        spent=work,
    )


def _is_dot_edge_group(el: _Element) -> bool:
    return el.tag == "g" and "edge" in el.classes


def measure_dot_svg(
    svg: str,
    *,
    max_segments: int = MAX_MEASURE_SEGMENTS,
    max_work: int = MAX_MEASURE_WORK,
    work_left: int | None = None,
) -> GeometryReport:
    """Misura completa di un SVG di Graphviz: incroci e difetti di lettura,
    con i tetti di `measure_svg`."""
    return measure_svg(
        svg,
        dot_defects=True,
        max_segments=max_segments,
        max_work=max_work,
        work_left=work_left,
    )


__all__ = [
    "CLUSTER_RADIUS",
    "DEFECT_EDGE_CROSSES_LABEL",
    "DEFECT_LABELS_OVERLAP",
    "DEFECT_TEXT_OUTSIDE_CANVAS",
    "DEFECT_TEXT_OUTSIDE_OWNER",
    "EDGE_CLASS_NAMES",
    "EDGE_CLASS_PREFIXES",
    "EDGE_GROUP_CLASSES",
    "ENDPOINT_TOLERANCE",
    "ESTIMATE_SLACK",
    "FONT_MONO",
    "FONT_SANS",
    "FONT_SERIF",
    "GRID_CELL",
    "MAX_BATCH_BROWSER_MEASURE_WORK",
    "MAX_BATCH_MEASURE_SEGMENTS",
    "MAX_BATCH_MEASURE_WORK",
    "MAX_BROWSER_MEASURE_WORK",
    "MAX_DEFECTS",
    "MAX_GRID_CELLS",
    "MAX_MEASURE_SEGMENTS",
    "MAX_MEASURE_WORK",
    "MERMAID_TEXT_TOLERANCE",
    "SAMPLE_STEP",
    "SKIP_BATCH_CAP",
    "SKIP_BATCH_WORK_CAP",
    "SKIP_FIGURE_CAP",
    "SKIP_RANGE",
    "SKIP_WORK_CAP",
    "TEXT_TOLERANCE",
    "GeometryReport",
    "count_crossings",
    "estimate_text_width",
    "font_kind",
    "is_anchor_wrapper",
    "is_edge_class",
    "measure_dot_svg",
    "measure_svg",
    "parse_transform",
    "segment_intersection",
]
