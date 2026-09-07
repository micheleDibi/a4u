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
from typing import Any, TypeGuard

# ---------------------------------------------------------------------------
# Versione, font, palette
# ---------------------------------------------------------------------------

# Entra nella chiave di cache `(fmt, sha256(content), THEME_VERSION, language)`
# di `figure_render_service`: un tema diverso invalida gli SVG in cache.
THEME_VERSION = "2026.09.2"

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
_TINT_PURPLE = "#FAF3F7"
_TINT_NOTE = "#FBF7E4"
COLOR_WHITE = "#ffffff"
# Colore del testo sopra ogni colore pieno della palette (bianco solo dove il
# contrasto WCAG con l'inchiostro è inferiore: blu 3.2 vs 5.2, nero).
PALETTE_LABEL: tuple[str, ...] = (
    COLOR_WHITE,
    COLOR_INK,
    COLOR_INK,
    COLOR_INK,
    COLOR_INK,
    COLOR_INK,
    COLOR_INK,
    COLOR_WHITE,
)

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

# Alias storici accettati in lettura ma non proposti al modello: i prompt di
# fix e di digitalizzazione chiedono la forma canonica (`flowchart`,
# `stateDiagram-v2`).
MERMAID_LEGACY_ALIASES: tuple[str, ...] = ("graph", "stateDiagram")

# I 15 tipi di D8 senza gli alias: unica proiezione condivisa dai prompt
# (`openai_asset_fix_service`, `openai_image_to_mermaid_service`).
MERMAID_D8_TYPES: tuple[str, ...] = tuple(
    t for t in MERMAID_ALLOWED_TYPES if t not in MERMAID_LEGACY_ALIASES
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
    espone più in Mermaid 11).

    `theme: "neutral"` con `themeVariables` ricondotti alla palette. Il tema
    neutral NON deriva da `primaryColor` i riempimenti e i bordi principali:
    in `Theme.updateColors` `nodeBkg = mainBkg` («#eee»), `nodeBorder =
    border1` («#999»), `clusterBkg/Border` da `contrast`, `actorBkg =
    mainBkg`, `signalColor = text`, `cScale0..11` grigi fissi, gantt da
    `contrast`, stato da `transitionColor = "#000"`. Ogni variabile derivata
    che i 15 tipi D8 leggono nei loro `getStyles` viene quindi fissata qui in
    modo esplicito (l'override vince perché `calculate` ricopia gli override
    dopo `updateColors`). Verificato sull'output reale di Mermaid 11.17.2 dal
    test `test_mermaid_theme_palette` (Playwright, salta senza CDN).

    Limite noto: `sankey-beta` colora i nodi con `schemeTableau10` di d3,
    hard-coded nel renderer e non esposto come variabile di tema; i link
    seguono il colore del nodo sorgente (`linkColor: "source"`, nessun
    gradiente come richiede D3).

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
        "background": COLOR_WHITE,
        # Colori base (letti dai temi come punto di partenza).
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
        "text": COLOR_INK,
        "contrast": COLOR_AXIS,
        "mainBkg": _TINT_BLUE,
        "secondBkg": COLOR_SURFACE,
        "border1": PALETTE[0],
        "border2": COLOR_AXIS,
        "arrowheadColor": COLOR_AXIS,
        "titleColor": COLOR_INK,
        "errorBkgColor": _TINT_ORANGE,
        "errorTextColor": PALETTE[1],
        # Niente gradienti né ombre (D3): il tema neutral li accende per il
        # look «neo» e inserisce comunque un `<linearGradient>` nei defs.
        "useGradient": False,
        "dropShadow": "none",
        # Flowchart, block, class, er, state: nodi, cluster, archi.
        "nodeBkg": _TINT_BLUE,
        "nodeBorder": PALETTE[0],
        "nodeTextColor": COLOR_INK,
        "clusterBkg": COLOR_SURFACE,
        "clusterBorder": COLOR_AXIS,
        "defaultLinkColor": COLOR_AXIS,
        "edgeLabelBackground": COLOR_WHITE,
        "classText": COLOR_INK,
        "attributeBackgroundColorOdd": COLOR_WHITE,
        "attributeBackgroundColorEven": COLOR_SURFACE,
        # Sequence: attori, segnali, riquadri loop/alt, attivazioni, note.
        "actorBkg": _TINT_BLUE,
        "actorBorder": PALETTE[0],
        "actorTextColor": COLOR_INK,
        "actorLineColor": COLOR_AXIS,
        "signalColor": COLOR_AXIS,
        "signalTextColor": COLOR_INK,
        "labelBoxBkgColor": _TINT_BLUE,
        "labelBoxBorderColor": PALETTE[0],
        "labelTextColor": COLOR_INK,
        "loopTextColor": COLOR_INK,
        "activationBkgColor": COLOR_SURFACE,
        "activationBorderColor": COLOR_AXIS,
        "sequenceNumberColor": COLOR_WHITE,
        "noteBkgColor": _TINT_NOTE,
        "noteBorderColor": PALETTE[3],
        "noteTextColor": COLOR_INK,
        # State (v2): transizioni, stati, compositi, stati speciali.
        "transitionColor": COLOR_AXIS,
        "transitionLabelColor": COLOR_INK,
        "stateLabelColor": COLOR_INK,
        "stateBkg": _TINT_BLUE,
        "stateBorder": PALETTE[0],
        "labelBackgroundColor": COLOR_WHITE,
        "compositeBackground": COLOR_WHITE,
        "compositeTitleBackground": _TINT_BLUE,
        "altBackground": COLOR_SURFACE,
        "innerEndBackground": PALETTE[0],
        "specialStateColor": COLOR_INK,
        # Gantt: sezioni, attività, griglia, attività critiche e completate.
        "sectionBkgColor": _TINT_BLUE,
        "sectionBkgColor2": _TINT_BLUE,
        "altSectionBkgColor": COLOR_WHITE,
        "taskBkgColor": PALETTE[0],
        "taskBorderColor": PALETTE[0],
        "taskTextColor": COLOR_WHITE,
        "taskTextLightColor": COLOR_WHITE,
        "taskTextDarkColor": COLOR_INK,
        "taskTextOutsideColor": COLOR_INK,
        "taskTextClickableColor": PALETTE[0],
        "activeTaskBkgColor": _TINT_BLUE,
        "activeTaskBorderColor": PALETTE[0],
        "doneTaskBkgColor": COLOR_GRID,
        "doneTaskBorderColor": COLOR_AXIS,
        "critical": PALETTE[1],
        "critBkgColor": PALETTE[1],
        "critBorderColor": PALETTE[1],
        "todayLineColor": PALETTE[1],
        "vertLineColor": PALETTE[1],
        "done": COLOR_GRID,
        "gridColor": COLOR_GRID,
        "excludeBkgColor": COLOR_SURFACE,
        # Quadrant: quattro tinte della palette, testo e punti.
        "quadrant1Fill": _TINT_BLUE,
        "quadrant2Fill": _TINT_ORANGE,
        "quadrant3Fill": _TINT_GREEN,
        "quadrant4Fill": _TINT_PURPLE,
        "quadrant1TextFill": COLOR_INK,
        "quadrant2TextFill": COLOR_INK,
        "quadrant3TextFill": COLOR_INK,
        "quadrant4TextFill": COLOR_INK,
        "quadrantPointFill": PALETTE[0],
        "quadrantPointTextFill": COLOR_INK,
        "quadrantXAxisTextFill": COLOR_INK,
        "quadrantYAxisTextFill": COLOR_INK,
        "quadrantTitleFill": COLOR_INK,
        "quadrantInternalBorderStrokeFill": COLOR_AXIS,
        "quadrantExternalBorderStrokeFill": COLOR_AXIS,
        # Pie: testi (le fette usano pie1..12 sotto).
        "pieTitleTextColor": COLOR_INK,
        "pieSectionTextColor": COLOR_INK,
        "pieLegendTextColor": COLOR_INK,
        "pieStrokeColor": COLOR_WHITE,
        "pieOuterStrokeColor": COLOR_WHITE,
        # xychart: palette delle serie, assi e titolo.
        "xyChart": {
            "backgroundColor": COLOR_WHITE,
            "titleColor": COLOR_INK,
            "xAxisTitleColor": COLOR_INK,
            "xAxisLabelColor": COLOR_INK,
            "xAxisTickColor": COLOR_AXIS,
            "xAxisLineColor": COLOR_AXIS,
            "yAxisTitleColor": COLOR_INK,
            "yAxisLabelColor": COLOR_INK,
            "yAxisTickColor": COLOR_AXIS,
            "yAxisLineColor": COLOR_AXIS,
            "plotColorPalette": ", ".join(PALETTE),
        },
        # Radar: assi e graticola neutri; le curve usano cScale0..n.
        "radar": {"axisColor": COLOR_AXIS, "graticuleColor": COLOR_GRID},
    }
    # Scala categoriale cScale0..11 (mindmap, timeline, radar, treemap):
    # palette ciclica; `cScaleLabel` è il testo sopra il colore pieno,
    # `cScaleInv` (sottolineature) resta neutro.
    for i in range(12):
        theme_variables[f"cScale{i}"] = PALETTE[i % len(PALETTE)]
        theme_variables[f"cScaleLabel{i}"] = PALETTE_LABEL[i % len(PALETTE)]
        theme_variables[f"cScaleInv{i}"] = COLOR_AXIS
    # Fette pie1..12 e radice di mindmap/timeline (git0, gitBranchLabel0).
    for i in range(12):
        theme_variables[f"pie{i + 1}"] = PALETTE[i % len(PALETTE)]
    for i, color in enumerate(PALETTE):
        theme_variables[f"git{i}"] = color
        theme_variables[f"gitBranchLabel{i}"] = PALETTE_LABEL[i]
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
        "sankey": {"linkColor": "source", **per_type},
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

# Conversione LaTeX → testo Unicode per le forme esatte che `sympy.latex(...,
# ln_notation=True, fold_short_frac=False)` produce sui valori `nsimplify`-ati:
# frazioni anche annidate, radicali (anche n-esimi), potenze, funzioni con
# `\left( \right)`, π, e, ∞. Parser a graffe bilanciate: ogni gruppo viene
# convertito dall'interno verso l'esterno, così `\frac{\ln{\left(3 \right)}}{2}`
# diventa «ln(3)/2» e `\frac{1}{x - 2}` diventa «1/(x − 2)».
_LATEX_CMD_RE = re.compile(r"\\([a-zA-Z]+|.)", re.DOTALL)
_LATEX_SYMBOLS: dict[str, str] = {
    "cdot": "·",
    "times": "×",
    "infty": "∞",
    "pi": "π",
    "pm": "±",
    "mp": "∓",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "ne": "≠",
    "neq": "≠",
    "to": "→",
    "lvert": "|",
    "rvert": "|",
    "vert": "|",
    "lbrace": "{",
    "rbrace": "}",
    "quad": " ",
    "qquad": " ",
    ",": " ",
    ";": " ",
    ":": " ",
    " ": " ",
    "!": "",
    "displaystyle": "",
    "textstyle": "",
    "big": "",
    "Big": "",
    "bigl": "",
    "bigr": "",
    "Bigl": "",
    "Bigr": "",
}
# Comandi il cui unico argomento è testo da conservare (`\mathrm{e}` → «e»).
_LATEX_WRAPPERS = frozenset(
    {"mathrm", "mathit", "mathbf", "mathsf", "mathtt", "text", "textrm", "operatorname"}
)
_SUPERSCRIPT_MAP = str.maketrans("0123456789+−-()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁽⁾")
_ROOT_PREFIX = {"2": "√", "3": "∛", "4": "∜"}
# Token che non richiedono parentesi come argomento di √ o esponente.
_LATEX_ATOM_RE = re.compile(r"^(?:[0-9]+(?:\.[0-9]+)?|[a-zA-Zπ∞])$")
_LATEX_INT_RE = re.compile(r"^−?[0-9]+$")
# Denominatori leggibili senza parentesi: numero, simbolo, potenza (`e²`),
# radicale (`√2`, `∛(x)`), chiamata di funzione (`ln(3)`). Un prodotto
# («2π») va tra parentesi: «1/(2π)», non «1/2π».
_LATEX_DEN_ATOM_RE = re.compile(
    r"^(?:[0-9]+(?:\.[0-9]+)?|[a-zA-Zπ∞]|[a-zA-Zπ0-9][⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+"
    r"|[⁰¹²³⁴⁵⁶⁷⁸⁹]*[√∛∜](?:[0-9a-zA-Zπ]+|\(.*\))|[a-zA-Z]+\(.*\))$"
)


def _latex_group(s: str, i: int) -> tuple[str, int]:
    """Con `s[i] == "{"` ritorna il contenuto del gruppo bilanciato e
    l'indice successivo alla graffa di chiusura (gruppo non chiuso → resto
    della stringa)."""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    return s[i + 1 :], len(s)


def _latex_arg(s: str, i: int) -> tuple[str, int]:
    """Argomento grezzo di un comando: gruppo `{…}`, comando `\\x` oppure
    singolo carattere (spazi iniziali ignorati)."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        return _latex_group(s, i)
    m = _LATEX_CMD_RE.match(s, i)
    if m:
        return m.group(0), m.end()
    return s[i], i + 1


def _latex_optional(s: str, i: int) -> tuple[str | None, int]:
    """Argomento opzionale `[…]` (indice di `\\sqrt`)."""
    if i < len(s) and s[i] == "[":
        j = s.find("]", i)
        if j != -1:
            return s[i + 1 : j], j + 1
    return None, i


def _latex_tidy(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"([(\[])\s+", r"\1", t)
    t = re.sub(r"\s+([)\]])", r"\1", t)
    # Meno unario: «− √2» → «−√2», «(− 1 + √5)» → «(−1 + √5)»; dopo «=» e
    # «,» resta uno spazio: «x = − π/2» → «x = −π/2».
    t = re.sub(r"(^|[(\[/^])\s*−\s+", r"\1−", t)
    t = re.sub(r"([=,])\s*−\s+", r"\1 −", t)
    # Coefficienti: «2 √3» → «2√3», «3 π» → «3π», «2 x» → «2x»; «2 ln(3)» resta.
    t = re.sub(r"(?<=[0-9])\s+(?=[√∛∜π∞]|[a-zA-Z](?![a-zA-Z]))", "", t)
    return t


def _latex_is_atomic(t: str, *, allow_sign: bool) -> bool:
    """Vero se `t` non contiene operatori o spazi al livello esterno delle
    parentesi (quindi non richiede parentesi in una frazione)."""
    depth = 0
    for k, ch in enumerate(t):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch in " +−·×/^=":
            if ch == "−" and k == 0 and allow_sign:
                continue
            return False
    return True


def _latex_fraction(num: str, den: str) -> str:
    num, den = _latex_tidy(num), _latex_tidy(den)
    n = num if _latex_is_atomic(num, allow_sign=True) else f"({num})"
    den_atomic = _latex_is_atomic(den, allow_sign=False) and bool(_LATEX_DEN_ATOM_RE.match(den))
    d = den if den_atomic else f"({den})"
    return f"{n}/{d}"


def _latex_root(arg: str, index: str | None) -> str:
    arg = _latex_tidy(arg)
    body = arg if _LATEX_ATOM_RE.match(arg) else f"({arg})"
    idx = _latex_tidy(_latex_convert(index)) if index else "2"
    prefix = _ROOT_PREFIX.get(idx)
    if prefix is not None:
        return prefix + body
    if idx.isdigit():
        return idx.translate(_SUPERSCRIPT_MAP) + "√" + body
    return f"{body}^(1/{idx})"


def _latex_superscript(exp: str) -> str:
    exp = _latex_tidy(exp)
    if _LATEX_INT_RE.match(exp):
        return exp.translate(_SUPERSCRIPT_MAP)
    return "^" + (exp if _LATEX_ATOM_RE.match(exp) else f"({exp})")


def _latex_convert(s: str) -> str:
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            m = _LATEX_CMD_RE.match(s, i)
            if m is None:
                i += 1
                continue
            cmd = m.group(1)
            i = m.end()
            if cmd in ("frac", "dfrac", "tfrac"):
                num, i = _latex_arg(s, i)
                den, i = _latex_arg(s, i)
                out.append(_latex_fraction(_latex_convert(num), _latex_convert(den)))
            elif cmd == "sqrt":
                index, i = _latex_optional(s, i)
                arg, i = _latex_arg(s, i)
                out.append(_latex_root(_latex_convert(arg), index))
            elif cmd in _LATEX_WRAPPERS:
                arg, i = _latex_arg(s, i)
                out.append(_latex_convert(arg))
            elif cmd in ("left", "right"):
                delim, i = _latex_arg(s, i)
                out.append("" if delim == "." else _latex_convert(delim))
            else:
                # Funzioni (`\ln`, `\sin`, …) e comandi ignoti: il nome.
                out.append(_LATEX_SYMBOLS.get(cmd, cmd))
        elif c == "{":
            inner, i = _latex_group(s, i)
            out.append(_latex_convert(inner))
        elif c == "}":
            i += 1
        elif c == "^":
            arg, i = _latex_arg(s, i + 1)
            out.append(_latex_superscript(_latex_convert(arg)))
        elif c == "_":
            arg, i = _latex_arg(s, i + 1)
            sub = _latex_tidy(_latex_convert(arg))
            out.append("_" + sub if _LATEX_ATOM_RE.match(sub) else f"_({sub})")
        elif c == "-":
            out.append("−")
            i += 1
        elif c == "$":
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def latex_to_unicode(latex: str) -> str:
    """Testo Unicode leggibile per una forma esatta LaTeX (`2 - \\sqrt{3}` →
    «2 − √3», `\\frac{-1 + \\sqrt{5}}{2}` → «(−1 + √5)/2», `\\sqrt[3]{2}` →
    «∛2», `e^{2}` → «e²»). Numeratore e denominatore vengono parentesizzati
    quando contengono operatori; i comandi non riconosciuti conservano il
    nome privato del backslash."""
    return _latex_tidy(_latex_convert(latex.strip()))


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


def _is_number(value: object) -> TypeGuard[float]:
    """Numero reale del calcolo (int o float); i booleani sono esclusi."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _exact_or_approx(entry: Mapping[str, Any], *, value_key: str, exact_key: str) -> str:
    exact = entry.get(exact_key)
    if isinstance(exact, str) and exact.strip():
        return latex_to_unicode(exact)
    value = entry.get(value_key)
    if _is_number(value):
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
    if not isinstance(computed, Mapping) or not computed:
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
            lo, hi = between
            if _is_number(lo) and _is_number(hi):
                phrase(
                    "integral",
                    a=format_number(float(lo)),
                    b=format_number(float(hi)),
                    value=_exact_or_approx(integral, value_key="value", exact_key="exact"),
                )

    tangents = computed.get("tangents")
    if isinstance(tangents, list):
        for t in tangents:
            if isinstance(t, Mapping) and _is_number(t.get("at")):
                phrase(
                    "tangent",
                    at=format_number(float(t["at"])),
                    slope=_exact_or_approx(t, value_key="slope", exact_key="exact_slope"),
                )

    levels = computed.get("levels")
    if isinstance(levels, list) and levels:
        phrase("levels", values=_join([format_number(float(v)) for v in levels if _is_number(v)]))

    analysis_keys = ("zeros", "critical_points", "inflection_points", "asymptotes")
    analysis_ran = any(isinstance(computed.get(k), list) for k in analysis_keys)
    if not sentences and analysis_ran:
        phrase("none")

    if computed.get("approximate") and sentences:
        sentences.append(labels["courses.figures.approxValues"])
    return " ".join(sentences)
