"""Tema accademico unico delle figure (D3) e testi delle didascalie (D4, A4).

Modulo «leaf»: nessun import da `app.core.config`, da SQLAlchemy o da
librerie pesanti (matplotlib, sympy, vl_convert). È quindi importabile dal
processo figlio `spawn` di `figure_compute.isolated`, dai test puri e dal
frontend-parity check senza effetti collaterali.

Ogni superficie che disegna una figura legge da qui font, palette e
parametri di inizializzazione:
- validatore Playwright e pre-render Mermaid (PDF, slide, video):
  `mermaid_initialize_js`;
- vl-convert (Vega-Lite): `VEGALITE_THEME_CONFIG` iniettato nella spec;
- Graphviz `dot`: `DOT_DEFAULTS` iniettati dopo la `{` di apertura;
- matplotlib (`function`): `MATPLOTLIB_RC` in `rc_context`;
- didascalie «Figura N.» e frasi calcolate: `figure_labels`,
  `function_caption`.

La copia frontend è `frontend/src/lib/figureTheme.ts`: MANTENERE ALLINEATO
(stesse costanti, stesso ordine della palette, stessa configurazione
Mermaid). Ogni modifica visibile del tema incrementa `THEME_VERSION`, che
entra nella chiave di cache degli SVG renderizzati.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

# ---------------------------------------------------------------------------
# Versione, font, palette
# ---------------------------------------------------------------------------

# Entra nella chiave di cache `(fmt, sha256(content), THEME_VERSION, language)`
# di `figure_render_service`: un tema diverso invalida gli SVG in cache.
THEME_VERSION = "2026.09.1"

FONT_FAMILY_PRIMARY = "Noto Sans"
# Gli stessi font installati nel Dockerfile (fonts-noto-core, fonts-dejavu-core).
FONT_STACK = '"Noto Sans", "DejaVu Sans", sans-serif'
# Famiglie ammesse nell'SVG di matplotlib (guardia A14: nessun STIX né
# «DejaVu Sans Display», assenti nel container e nel browser).
FONT_ALLOWED: frozenset[str] = frozenset({"Noto Sans", "DejaVu Sans"})
# Label Mermaid (`<text>` SVG con htmlLabels:false): la famiglia CJK segue
# Noto Sans, così il latino conserva le metriche del tema e gli ideogrammi
# trovano un font installato (fonts-noto-cjk) invece di diventare «tofu».
MERMAID_FONT_FAMILY = '"Noto Sans", "Noto Sans CJK JP", "DejaVu Sans", sans-serif'

# Palette di Okabe e Ito: distinguibile in scala di grigi e con deficit cromatici. Ordine
# scelto per il contrasto sul bianco (giallo e nero in coda). Niente
# gradienti, niente ombre.
PALETTE: tuple[str, ...] = (
    "#0072B2",  # blu
    "#D55E00",  # vermiglio
    "#009E73",  # verde bluastro
    "#E69F00",  # arancio
    "#CC79A7",  # porpora
    "#56B4E9",  # celeste
    "#F0E442",  # giallo
    "#000000",  # nero
)

# Neutri condivisi (testo, assi, griglia, elementi secondari, superfici).
COLOR_INK = "#1F1F1F"
COLOR_AXIS = "#4D4D4D"
COLOR_GRID = "#D9D9D9"
COLOR_MUTED = "#888888"
COLOR_SURFACE = "#F4F6F8"
# Tinte chiare della palette per i riempimenti dei nodi Mermaid.
_TINT_BLUE = "#E8F1F8"
_TINT_ORANGE = "#FBEFD9"
_TINT_GREEN = "#E5F4EF"
_TINT_NOTE = "#FBF7E4"

# ---------------------------------------------------------------------------
# Mermaid 11 — tipi ammessi (D8) e configurazione
# ---------------------------------------------------------------------------

# I 15 tipi di D8 più gli alias storici: `graph` (flowchart) e `stateDiagram`
# (v1, accettato in lettura per i contenuti già in DB). La prima riga utile
# del sorgente (dopo `%%` e frontmatter) deve iniziare con uno di questi.
MERMAID_ALLOWED_TYPES: tuple[str, ...] = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram-v2",
    "stateDiagram",
    "erDiagram",
    "mindmap",
    "timeline",
    "pie",
    "xychart-beta",
    "quadrantChart",
    "sankey-beta",
    "block-beta",
    "gantt",
    "radar-beta",
    "treemap-beta",
)

# Esclusi da prompt, fix AI e gate statico. `journey` emette due
# `<foreignObject>` anche in 10.9.4 (non renderizzabili da WeasyPrint); gli
# altri non hanno uso didattico o dipendono da risorse esterne (icone).
MERMAID_EXCLUDED_TYPES: tuple[str, ...] = (
    "journey",
    "gitGraph",
    "kanban",
    "packet-beta",
    "architecture-beta",
)


# Campioni minimi, uno per tipo D8, riusati dal test Playwright
# `test_mermaid_no_foreignobject` (0 foreignObject in Mermaid 11 con
# htmlLabels:false top-level) e dai template dell'editor. Il mindmap porta
# una label CJK per verificare la resa dei glifi ideografici in `<text>`.
def _lines(*rows: str) -> str:
    return "\n".join(rows)


MERMAID_D8_SAMPLES: dict[str, str] = {
    "flowchart": _lines(
        "flowchart LR",
        "  A[Ipotesi] --> B{Condizione}",
        "  B -->|sì| C[Tesi]",
        "  B -->|no| D[Controesempio]",
    ),
    "sequenceDiagram": _lines(
        "sequenceDiagram",
        "  participant C as Cliente",
        "  participant S as Server",
        "  C->>S: Richiesta",
        "  S-->>C: Risposta",
    ),
    "classDiagram": _lines(
        "classDiagram",
        "  class Animale {",
        "    +String nome",
        "    +verso()",
        "  }",
        "  Animale <|-- Cane",
    ),
    "stateDiagram-v2": _lines(
        "stateDiagram-v2",
        "  [*] --> Inattivo",
        "  Inattivo --> Attivo : avvio",
        "  Attivo --> [*]",
    ),
    "erDiagram": _lines(
        "erDiagram",
        "  CORSO ||--o{ LEZIONE : contiene",
        "  LEZIONE {",
        "    string titolo",
        "  }",
    ),
    "mindmap": _lines(
        "mindmap",
        "  root((Concetti))",
        "    Definizione",
        "    Teorema 定理",
        "      Dimostrazione",
        "    Esempio",
    ),
    "timeline": _lines(
        "timeline",
        "  title Cronologia",
        "  1687 : Principia",
        "  1905 : Relatività ristretta",
    ),
    "pie": _lines(
        "pie title Ripartizione",
        '  "Teoria" : 60',
        '  "Esercizi" : 40',
    ),
    "xychart-beta": _lines(
        "xychart-beta",
        '  title "Serie"',
        "  x-axis [a, b, c]",
        '  y-axis "Valore" 0 --> 10',
        "  bar [3, 6, 9]",
    ),
    "quadrantChart": _lines(
        "quadrantChart",
        "  title Priorità",
        "  x-axis Basso costo --> Alto costo",
        "  y-axis Basso impatto --> Alto impatto",
        "  quadrant-1 Pianificare",
        "  quadrant-2 Eseguire",
        "  quadrant-3 Rinviare",
        "  quadrant-4 Delegare",
        "  A: [0.3, 0.6]",
    ),
    "sankey-beta": _lines(
        "sankey-beta",
        "",
        "Fonte A,Uso 1,10",
        "Fonte A,Uso 2,5",
    ),
    "block-beta": _lines(
        "block-beta",
        "  columns 3",
        '  A["Ingresso"] B["Elaborazione"] C["Uscita"]',
        "  A --> B",
        "  B --> C",
    ),
    "gantt": _lines(
        "gantt",
        "  title Piano",
        "  dateFormat YYYY-MM-DD",
        "  section Fase 1",
        "  Analisi :a1, 2024-01-01, 7d",
        "  Progetto :after a1, 5d",
    ),
    "radar-beta": _lines(
        "radar-beta",
        "  title Competenze",
        '  axis a["Analisi"], b["Sintesi"], c["Calcolo"]',
        '  curve s["Studente"]{3, 4, 5}',
        "  max 5",
        "  min 0",
    ),
    "treemap-beta": _lines(
        "treemap-beta",
        '"Categoria"',
        '    "Voce A": 10',
        '    "Voce B": 20',
    ),
}


def mermaid_config(*, use_max_width: bool, security_level: str = "loose") -> dict[str, Any]:
    """Oggetto di configurazione per `mermaid.initialize`.

    `htmlLabels: false` al livello TOP è la condizione che porta Mermaid 11 a
    emettere `<text>` puro (0 `<foreignObject>`) per tutti i tipi D8; le voci
    per-tipo di flowchart e class restano per compatibilità (`state` non la
    espone più in Mermaid 11). `theme: "neutral"` con
    `themeVariables` ricondotti alla palette: ogni tema Mermaid applica gli
    override (`Theme.calculate(overrides)`), non solo `base`.

    `use_max_width=True` per il pre-render destinato al PDF (l'SVG riempie il
    contenitore; il `max-width` naturale viene poi rimosso da
    `_strip_mermaid_max_width`), `False` per il solo validatore.
    `security_level`: `loose` nei Chromium headless del backend (contenuto
    nostro, nessun cookie), `strict` nel browser dell'utente.
    """
    per_type = {"useMaxWidth": use_max_width}
    theme_variables: dict[str, Any] = {
        "fontFamily": MERMAID_FONT_FAMILY,
        "fontSize": "14px",
        "background": "#ffffff",
        "primaryColor": _TINT_BLUE,
        "primaryTextColor": COLOR_INK,
        "primaryBorderColor": PALETTE[0],
        "secondaryColor": _TINT_ORANGE,
        "secondaryTextColor": COLOR_INK,
        "secondaryBorderColor": PALETTE[3],
        "tertiaryColor": _TINT_GREEN,
        "tertiaryTextColor": COLOR_INK,
        "tertiaryBorderColor": PALETTE[2],
        "lineColor": COLOR_AXIS,
        "textColor": COLOR_INK,
        "noteBkgColor": _TINT_NOTE,
        "noteBorderColor": PALETTE[3],
        "noteTextColor": COLOR_INK,
        # Tema xychart: palette delle serie.
        "xyChart": {"plotColorPalette": ", ".join(PALETTE)},
    }
    for i, color in enumerate(PALETTE, start=1):
        theme_variables[f"pie{i}"] = color
    return {
        "startOnLoad": False,
        "theme": "neutral",
        "htmlLabels": False,
        "securityLevel": security_level,
        "fontFamily": MERMAID_FONT_FAMILY,
        "themeVariables": theme_variables,
        "flowchart": {"htmlLabels": False, **per_type},
        "sequence": dict(per_type),
        "class": {"htmlLabels": False, **per_type},
        "state": dict(per_type),
        "er": dict(per_type),
        "gantt": dict(per_type),
        "pie": dict(per_type),
        "mindmap": dict(per_type),
        "timeline": dict(per_type),
        "xyChart": dict(per_type),
        "quadrantChart": dict(per_type),
        "sankey": dict(per_type),
        "block": dict(per_type),
        "radar": dict(per_type),
    }


def mermaid_initialize_js(*, use_max_width: bool, security_level: str = "loose") -> str:
    """Istruzione JS completa `mermaid.initialize({...});` da incorporare
    negli HTML del validatore e del pre-render (JSON è JavaScript valido)."""
    cfg = mermaid_config(use_max_width=use_max_width, security_level=security_level)
    return "mermaid.initialize(" + json.dumps(cfg, ensure_ascii=False, indent=2) + ");"


# ---------------------------------------------------------------------------
# Vega-Lite — `config` iniettato dal renderer (il modello non lo scrive mai)
# ---------------------------------------------------------------------------

VEGALITE_THEME_CONFIG: dict[str, Any] = {
    "font": FONT_FAMILY_PRIMARY,
    # `null` non è ammesso dallo schema per `background`: «transparent» è la
    # stringa colore che produce lo sfondo nullo richiesto da D3.
    "background": "transparent",
    "padding": 8,
    "view": {"stroke": None, "continuousWidth": 360, "continuousHeight": 220},
    "axis": {
        "labelFont": FONT_FAMILY_PRIMARY,
        "titleFont": FONT_FAMILY_PRIMARY,
        "labelFontSize": 11,
        "titleFontSize": 12,
        "titleFontWeight": "normal",
        "labelColor": COLOR_INK,
        "titleColor": COLOR_INK,
        "domainColor": COLOR_AXIS,
        "tickColor": COLOR_AXIS,
        "gridColor": COLOR_GRID,
        "gridWidth": 0.6,
        "labelLimit": 120,
    },
    "legend": {
        "labelFont": FONT_FAMILY_PRIMARY,
        "titleFont": FONT_FAMILY_PRIMARY,
        "labelFontSize": 11,
        "titleFontSize": 11,
        "titleFontWeight": "normal",
        "labelColor": COLOR_INK,
        "titleColor": COLOR_INK,
        "symbolType": "square",
    },
    "header": {
        "labelFont": FONT_FAMILY_PRIMARY,
        "titleFont": FONT_FAMILY_PRIMARY,
        "labelColor": COLOR_INK,
        "titleColor": COLOR_INK,
    },
    "title": {
        "font": FONT_FAMILY_PRIMARY,
        "fontSize": 13,
        "fontWeight": "bold",
        "anchor": "start",
        "color": COLOR_INK,
    },
    "text": {"font": FONT_FAMILY_PRIMARY, "fontSize": 11, "color": COLOR_INK},
    "range": {"category": list(PALETTE)},
    "mark": {"color": PALETTE[0]},
    "line": {"strokeWidth": 2},
    "point": {"filled": True, "size": 40},
    "area": {"opacity": 0.35, "line": True},
    "bar": {"stroke": None},
}

# ---------------------------------------------------------------------------
# Graphviz DOT — attributi di default iniettati dopo la `{` di apertura
# ---------------------------------------------------------------------------

# Ogni voce viene iniettata solo se il sorgente non definisce già lo stesso
# blocco (`graph [`, `node [`, `edge [`): il modello resta libero di
# sovrascrivere il tema in modo esplicito.
DOT_DEFAULTS: dict[str, str] = {
    "graph": (
        f'graph [fontname="{FONT_FAMILY_PRIMARY}", fontsize=11, bgcolor="transparent", '
        "pad=0.2, nodesep=0.35, ranksep=0.45];"
    ),
    "node": (
        f'node [fontname="{FONT_FAMILY_PRIMARY}", fontsize=11, shape=box, style=rounded, '
        f'color="{COLOR_AXIS}", fontcolor="{COLOR_INK}", penwidth=0.8, margin="0.12,0.06"];'
    ),
    "edge": (
        f'edge [fontname="{FONT_FAMILY_PRIMARY}", fontsize=10, color="{COLOR_AXIS}", '
        f'fontcolor="{COLOR_INK}", penwidth=0.8, arrowsize=0.7];'
    ),
}


def dot_defaults_prelude(*, skip: frozenset[str] = frozenset()) -> str:
    """Blocchi `graph/node/edge [...]` da inserire dopo la `{` di apertura,
    escludendo quelli che il sorgente definisce già (`skip`)."""
    return "\n".join(v for k, v in DOT_DEFAULTS.items() if k not in skip)


# ---------------------------------------------------------------------------
# matplotlib — rcParams per `rc_context` (formato `function`)
# ---------------------------------------------------------------------------

# Solo valori serializzabili: il modulo non importa matplotlib. Il ciclo dei
# colori è nella forma stringa accettata da `validate_cycler`. `svg.hashsalt`
# viene aggiunto per asset dal renderer (`f"a4u:{content_hash}"`) così gli
# id di clipPath e marker non collidono fra figure diverse.
MATPLOTLIB_RC: dict[str, Any] = {
    "svg.fonttype": "none",
    "font.family": "sans-serif",
    "font.sans-serif": ["Noto Sans", "DejaVu Sans"],
    "mathtext.fontset": "dejavusans",
    "text.usetex": False,
    "font.size": 10,
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "legend.frameon": False,
    "figure.facecolor": "none",
    "axes.facecolor": "none",
    "savefig.facecolor": "none",
    "savefig.edgecolor": "none",
    "axes.edgecolor": COLOR_AXIS,
    "axes.labelcolor": COLOR_INK,
    "axes.linewidth": 0.8,
    "axes.grid": False,
    "axes.prop_cycle": "cycler('color', " + json.dumps(list(PALETTE)) + ")",
    "xtick.color": COLOR_AXIS,
    "ytick.color": COLOR_AXIS,
    "xtick.labelcolor": COLOR_INK,
    "ytick.labelcolor": COLOR_INK,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "grid.color": COLOR_GRID,
    "grid.linewidth": 0.6,
    "lines.linewidth": 1.6,
    "path.simplify": True,
    "svg.image_inline": True,
}

# ---------------------------------------------------------------------------
# Localizzazione it/en delle didascalie (A4) — stesse chiavi di it.json/en.json
# ---------------------------------------------------------------------------

# Chiavi piatte con il percorso completo di `courses.figures.*` dei locale
# frontend. Le altre 22 lingue ricadono su `it`, come `_labels_for` del PDF
# e `fallbackLng: "it"` del frontend. I segnaposto `{{...}}` seguono la
# sintassi di i18next e sono sostituiti da `_interpolate`.
FIGURE_I18N: dict[str, dict[str, str]] = {
    "it": {
        "courses.figures.label": "Figura {{n}}.",
        "courses.figures.labelUnnumbered": "Figura.",
        "courses.figures.illustrativeData": "Dati illustrativi, non sperimentali.",
        "courses.figures.approxValues": "Valori approssimati.",
        "courses.figures.renderError": "Impossibile visualizzare la figura.",
        "courses.figures.loading": "Caricamento della figura…",
        "courses.figures.missing": "Figura non disponibile.",
        "courses.figures.formats.mermaid": "Diagramma Mermaid",
        "courses.figures.formats.vegalite": "Grafico Vega-Lite",
        "courses.figures.formats.dot": "Grafo Graphviz",
        "courses.figures.formats.function": "Figura matematica",
        "courses.figures.formats.image": "Immagine",
        "courses.figures.function.zeros": "Zeri in x = {{values}}.",
        "courses.figures.function.critical_points": "Punti critici in x = {{values}}.",
        "courses.figures.function.inflection_points": "Flessi in x = {{values}}.",
        "courses.figures.function.asymptote_vertical": "Asintoto verticale {{expr}}.",
        "courses.figures.function.asymptote_horizontal": "Asintoto orizzontale {{expr}}.",
        "courses.figures.function.asymptote_oblique": "Asintoto obliquo {{expr}}.",
        "courses.figures.function.integral": "Integrale su [{{a}}, {{b}}] pari a {{value}}.",
        "courses.figures.function.tangent": "Tangente in x = {{at}} con pendenza {{slope}}.",
        "courses.figures.function.levels": "Curve di livello per z = {{values}}.",
        "courses.figures.function.none": "Nessun punto notevole nel dominio considerato.",
    },
    "en": {
        "courses.figures.label": "Figure {{n}}.",
        "courses.figures.labelUnnumbered": "Figure.",
        "courses.figures.illustrativeData": "Illustrative data, not experimental.",
        "courses.figures.approxValues": "Approximate values.",
        "courses.figures.renderError": "The figure could not be rendered.",
        "courses.figures.loading": "Loading figure…",
        "courses.figures.missing": "Figure not available.",
        "courses.figures.formats.mermaid": "Mermaid diagram",
        "courses.figures.formats.vegalite": "Vega-Lite chart",
        "courses.figures.formats.dot": "Graphviz graph",
        "courses.figures.formats.function": "Mathematical figure",
        "courses.figures.formats.image": "Image",
        "courses.figures.function.zeros": "Zeros at x = {{values}}.",
        "courses.figures.function.critical_points": "Critical points at x = {{values}}.",
        "courses.figures.function.inflection_points": "Inflection points at x = {{values}}.",
        "courses.figures.function.asymptote_vertical": "Vertical asymptote {{expr}}.",
        "courses.figures.function.asymptote_horizontal": "Horizontal asymptote {{expr}}.",
        "courses.figures.function.asymptote_oblique": "Oblique asymptote {{expr}}.",
        "courses.figures.function.integral": "Integral over [{{a}}, {{b}}] equal to {{value}}.",
        "courses.figures.function.tangent": "Tangent at x = {{at}} with slope {{slope}}.",
        "courses.figures.function.levels": "Level curves for z = {{values}}.",
        "courses.figures.function.none": "No notable points in the considered domain.",
    },
}

FIGURE_I18N_FALLBACK = "it"


def figure_labels(language: str | None) -> dict[str, str]:
    """Etichette della lingua richiesta (`it`, `en`, anche `en-GB`); ogni
    altra lingua ricade su `it` (A4). Ritorna una copia: il chiamante può
    interpolare senza toccare il dizionario condiviso."""
    code = (language or FIGURE_I18N_FALLBACK).strip().lower().split("-")[0].split("_")[0]
    return dict(FIGURE_I18N.get(code) or FIGURE_I18N[FIGURE_I18N_FALLBACK])


_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _interpolate(template: str, values: Mapping[str, Any]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), template)


# ---------------------------------------------------------------------------
# Didascalia calcolata del formato `function` (D9)
# ---------------------------------------------------------------------------

# Conversione minima LaTeX → testo Unicode per le forme esatte prodotte da
# `sympy.latex` su valori `nsimplify`-ati (radicali, frazioni, π, e, ln).
_LATEX_SQRT_RE = re.compile(r"\\sqrt\{([^{}]*)\}")
_LATEX_FRAC_RE = re.compile(r"\\(?:d|t)?frac\{([^{}]*)\}\{([^{}]*)\}")
_LATEX_BRACES_RE = re.compile(r"[{}]")
_LATEX_SIMPLE: tuple[tuple[str, str], ...] = (
    (r"\left(", "("),
    (r"\right)", ")"),
    (r"\left|", "|"),
    (r"\right|", "|"),
    (r"\lvert", "|"),
    (r"\rvert", "|"),
    (r"\cdot", "·"),
    (r"\times", "×"),
    (r"\infty", "∞"),
    (r"\pi", "π"),
    (r"\ln", "ln"),
    (r"\log", "log"),
    (r"\sin", "sin"),
    (r"\cos", "cos"),
    (r"\tan", "tan"),
    (r"\exp", "exp"),
    (r"\displaystyle", ""),
    (r"\,", " "),
    (r"\;", " "),
    (r"\ ", " "),
    ("-", "−"),
)


def latex_to_unicode(latex: str) -> str:
    """Testo Unicode leggibile per una forma esatta LaTeX semplice
    (`2 - \\sqrt{3}` → «2 − √3», `\\frac{1}{3}` → «1/3»). Le forme non
    riconosciute conservano il testo privato di backslash e graffe."""
    s = latex.strip()
    s = _LATEX_SQRT_RE.sub(
        lambda m: "√" + (m.group(1) if len(m.group(1)) <= 2 else f"({m.group(1)})"), s
    )
    s = _LATEX_FRAC_RE.sub(lambda m: f"{m.group(1)}/{m.group(2)}", s)
    for src, dst in _LATEX_SIMPLE:
        s = s.replace(src, dst)
    s = _LATEX_BRACES_RE.sub("", s)
    s = s.replace("\\", "")
    return re.sub(r"\s+", " ", s).strip()


def format_number(value: float, *, digits: int = 3) -> str:
    """Numero approssimato con `digits` decimali (zeri finali rimossi),
    separatore decimale «.», segno meno tipografico."""
    if value != value or value in (float("inf"), float("-inf")):  # NaN, ±∞
        return "∞" if value > 0 else ("−∞" if value < 0 else "n.d.")
    rounded = round(float(value), digits)
    if rounded == 0:
        rounded = 0.0
    text = f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def _exact_or_approx(entry: Mapping[str, Any], *, value_key: str, exact_key: str) -> str:
    exact = entry.get(exact_key)
    if isinstance(exact, str) and exact.strip():
        return latex_to_unicode(exact)
    value = entry.get(value_key)
    if isinstance(value, (int, float)):
        return format_number(float(value))
    return "n.d."


def _join(items: list[str]) -> str:
    return ", ".join(items)


def function_caption(computed: Mapping[str, Any] | None, language: str | None) -> str:
    """Coda della didascalia composta dalle frasi `courses.figures.function.*`
    nella lingua richiesta (fallback `it`).

    `computed` è il dizionario prodotto dal renderer `function` (zeri, punti
    critici, flessi, asintoti, integrale, tangenti, livelli, `approximate`,
    `warnings`). Ogni numero proviene dal calcolo: le forme esatte (`exact*`,
    LaTeX) sono rese in Unicode con √ e π; in loro assenza il valore numerico
    è arrotondato a 3 decimali. Mai persistita: si rigenera a ogni render.
    """
    if not computed:
        return ""
    labels = figure_labels(language)
    sentences: list[str] = []

    def phrase(key: str, **values: Any) -> None:
        sentences.append(_interpolate(labels[f"courses.figures.function.{key}"], values))

    def points(entries: Any, *, value_key: str, exact_key: str) -> list[str]:
        if not isinstance(entries, list):
            return []
        return [
            _exact_or_approx(e, value_key=value_key, exact_key=exact_key)
            for e in entries
            if isinstance(e, Mapping)
        ]

    zeros = points(computed.get("zeros"), value_key="x", exact_key="exact")
    if zeros:
        phrase("zeros", values=_join(zeros))
    critical = points(computed.get("critical_points"), value_key="x", exact_key="exact_x")
    if critical:
        phrase("critical_points", values=_join(critical))
    inflection = points(computed.get("inflection_points"), value_key="x", exact_key="exact_x")
    if inflection:
        phrase("inflection_points", values=_join(inflection))

    asymptotes = computed.get("asymptotes")
    if isinstance(asymptotes, list):
        for a in asymptotes:
            if not isinstance(a, Mapping):
                continue
            kind = str(a.get("kind") or "")
            key = f"asymptote_{kind}"
            if f"courses.figures.function.{key}" not in labels:
                continue
            expr = a.get("latex") or a.get("expr") or ""
            phrase(key, expr=latex_to_unicode(str(expr)))

    integral = computed.get("integral")
    if isinstance(integral, Mapping):
        between = integral.get("between")
        if isinstance(between, (list, tuple)) and len(between) == 2:
            phrase(
                "integral",
                a=format_number(float(between[0])),
                b=format_number(float(between[1])),
                value=_exact_or_approx(integral, value_key="value", exact_key="exact"),
            )

    tangents = computed.get("tangents")
    if isinstance(tangents, list):
        for t in tangents:
            if isinstance(t, Mapping) and isinstance(t.get("at"), (int, float)):
                phrase(
                    "tangent",
                    at=format_number(float(t["at"])),
                    slope=_exact_or_approx(t, value_key="slope", exact_key="exact_slope"),
                )

    levels = computed.get("levels")
    if isinstance(levels, list) and levels:
        phrase(
            "levels",
            values=_join([format_number(float(v)) for v in levels if isinstance(v, (int, float))]),
        )

    analysis_keys = ("zeros", "critical_points", "inflection_points", "asymptotes")
    analysis_ran = any(isinstance(computed.get(k), list) for k in analysis_keys)
    if not sentences and analysis_ran:
        phrase("none")

    if computed.get("approximate") and sentences:
        sentences.append(labels["courses.figures.approxValues"])
    return " ".join(sentences)
