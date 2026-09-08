import type { MermaidConfig } from "mermaid";

/**
 * Tema accademico unico delle figure (D3) — copia frontend di
 * `backend/app/services/figure_theme.py`: MANTENERE ALLINEATO (stesse
 * costanti, stesso ordine della palette, stessa configurazione Mermaid,
 * stesso `THEME_VERSION`). Il backend è la sorgente di verità: ogni
 * modifica si fa prima lì e si replica qui.
 *
 * Usato da MermaidDiagram (initialize), VegaLiteDiagram (config iniettato
 * nella spec, come fa il renderer server-side), DotDiagram (attributi di
 * default) e FigureFrame.
 */

export const THEME_VERSION = "2026.09.3";

export const FONT_FAMILY_PRIMARY = "Noto Sans";
export const FONT_STACK = '"Noto Sans", "DejaVu Sans", sans-serif';
// Label Mermaid: la famiglia CJK segue Noto Sans (glifi ideografici).
export const MERMAID_FONT_FAMILY =
  '"Noto Sans", "Noto Sans CJK JP", "DejaVu Sans", sans-serif';

// Okabe–Ito, ordine per contrasto sul bianco (giallo e nero in coda).
export const PALETTE = [
  "#0072B2", // blu
  "#D55E00", // vermiglio
  "#009E73", // verde bluastro
  "#E69F00", // arancio
  "#CC79A7", // porpora
  "#56B4E9", // celeste
  "#F0E442", // giallo
  "#000000", // nero
] as const;

export const COLOR_INK = "#1F1F1F";
export const COLOR_AXIS = "#4D4D4D";
export const COLOR_GRID = "#D9D9D9";
export const COLOR_MUTED = "#888888";
export const COLOR_SURFACE = "#F4F6F8";
const TINT_BLUE = "#E8F1F8";
const TINT_ORANGE = "#FBEFD9";
const TINT_GREEN = "#E5F4EF";
const TINT_PURPLE = "#FAF3F7";
const TINT_NOTE = "#FBF7E4";
export const COLOR_WHITE = "#ffffff";
// Colore del testo sopra ogni colore pieno della palette (bianco solo dove
// il contrasto WCAG con l'inchiostro è inferiore: blu, nero).
export const PALETTE_LABEL = [
  COLOR_WHITE,
  COLOR_INK,
  COLOR_INK,
  COLOR_INK,
  COLOR_INK,
  COLOR_INK,
  COLOR_INK,
  COLOR_WHITE,
] as const;

// I 15 tipi di D8 più gli alias storici `graph` e `stateDiagram` (v1).
export const MERMAID_ALLOWED_TYPES = [
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
] as const;

export type MermaidAllowedType = (typeof MERMAID_ALLOWED_TYPES)[number];

export const MERMAID_EXCLUDED_TYPES = [
  "journey",
  "gitGraph",
  "kanban",
  "packet-beta",
  "architecture-beta",
] as const;

export interface MermaidConfigOptions {
  useMaxWidth: boolean;
  /** `strict` nel browser dell'utente (default); `loose` solo nei Chromium headless del backend. */
  securityLevel?: "strict" | "loose";
}

/**
 * Configurazione per `mermaid.initialize`. `htmlLabels: false` al livello
 * TOP porta Mermaid 11 a emettere `<text>` puro (0 `<foreignObject>`) per
 * i tipi D8; `theme: "neutral"` con `themeVariables` ricondotti alla
 * palette. Il tema neutral non deriva da `primaryColor` i riempimenti e i
 * bordi principali (`mainBkg`, `nodeBorder`, `actorBkg`, `signalColor`,
 * `cScale0..11`, gantt, stato): ogni variabile derivata letta dai 15 tipi
 * D8 è fissata qui, con gli stessi valori di `figure_theme.mermaid_config`.
 * Limite noto: i nodi di `sankey-beta` usano `schemeTableau10` di d3,
 * hard-coded nel renderer; i link seguono il nodo sorgente (nessun gradiente).
 */
export function mermaidConfig({
  useMaxWidth,
  securityLevel = "strict",
}: MermaidConfigOptions): MermaidConfig {
  const perType = { useMaxWidth };
  const themeVariables: Record<string, unknown> = {
    fontFamily: MERMAID_FONT_FAMILY,
    fontSize: "14px",
    background: COLOR_WHITE,
    // Colori base (letti dai temi come punto di partenza).
    primaryColor: TINT_BLUE,
    primaryTextColor: COLOR_INK,
    primaryBorderColor: PALETTE[0],
    secondaryColor: TINT_ORANGE,
    secondaryTextColor: COLOR_INK,
    secondaryBorderColor: PALETTE[3],
    tertiaryColor: TINT_GREEN,
    tertiaryTextColor: COLOR_INK,
    tertiaryBorderColor: PALETTE[2],
    lineColor: COLOR_AXIS,
    textColor: COLOR_INK,
    text: COLOR_INK,
    contrast: COLOR_AXIS,
    mainBkg: TINT_BLUE,
    secondBkg: COLOR_SURFACE,
    border1: PALETTE[0],
    border2: COLOR_AXIS,
    arrowheadColor: COLOR_AXIS,
    titleColor: COLOR_INK,
    errorBkgColor: TINT_ORANGE,
    errorTextColor: PALETTE[1],
    // Niente gradienti né ombre (D3).
    useGradient: false,
    dropShadow: "none",
    // Flowchart, block, class, er, state: nodi, cluster, archi.
    nodeBkg: TINT_BLUE,
    nodeBorder: PALETTE[0],
    nodeTextColor: COLOR_INK,
    clusterBkg: COLOR_SURFACE,
    clusterBorder: COLOR_AXIS,
    defaultLinkColor: COLOR_AXIS,
    edgeLabelBackground: COLOR_WHITE,
    classText: COLOR_INK,
    attributeBackgroundColorOdd: COLOR_WHITE,
    attributeBackgroundColorEven: COLOR_SURFACE,
    // Sequence: attori, segnali, riquadri loop/alt, attivazioni, note.
    actorBkg: TINT_BLUE,
    actorBorder: PALETTE[0],
    actorTextColor: COLOR_INK,
    actorLineColor: COLOR_AXIS,
    signalColor: COLOR_AXIS,
    signalTextColor: COLOR_INK,
    labelBoxBkgColor: TINT_BLUE,
    labelBoxBorderColor: PALETTE[0],
    labelTextColor: COLOR_INK,
    loopTextColor: COLOR_INK,
    activationBkgColor: COLOR_SURFACE,
    activationBorderColor: COLOR_AXIS,
    sequenceNumberColor: COLOR_WHITE,
    noteBkgColor: TINT_NOTE,
    noteBorderColor: PALETTE[3],
    noteTextColor: COLOR_INK,
    // State (v2): transizioni, stati, compositi, stati speciali.
    transitionColor: COLOR_AXIS,
    transitionLabelColor: COLOR_INK,
    stateLabelColor: COLOR_INK,
    stateBkg: TINT_BLUE,
    stateBorder: PALETTE[0],
    labelBackgroundColor: COLOR_WHITE,
    compositeBackground: COLOR_WHITE,
    compositeTitleBackground: TINT_BLUE,
    altBackground: COLOR_SURFACE,
    innerEndBackground: PALETTE[0],
    specialStateColor: COLOR_INK,
    // Gantt: sezioni, attività, griglia, attività critiche e completate.
    sectionBkgColor: TINT_BLUE,
    sectionBkgColor2: TINT_BLUE,
    altSectionBkgColor: COLOR_WHITE,
    taskBkgColor: PALETTE[0],
    taskBorderColor: PALETTE[0],
    taskTextColor: COLOR_WHITE,
    taskTextLightColor: COLOR_WHITE,
    taskTextDarkColor: COLOR_INK,
    taskTextOutsideColor: COLOR_INK,
    taskTextClickableColor: PALETTE[0],
    activeTaskBkgColor: TINT_BLUE,
    activeTaskBorderColor: PALETTE[0],
    doneTaskBkgColor: COLOR_GRID,
    doneTaskBorderColor: COLOR_AXIS,
    critical: PALETTE[1],
    critBkgColor: PALETTE[1],
    critBorderColor: PALETTE[1],
    todayLineColor: PALETTE[1],
    vertLineColor: PALETTE[1],
    done: COLOR_GRID,
    gridColor: COLOR_GRID,
    excludeBkgColor: COLOR_SURFACE,
    // Quadrant: quattro tinte della palette, testo e punti.
    quadrant1Fill: TINT_BLUE,
    quadrant2Fill: TINT_ORANGE,
    quadrant3Fill: TINT_GREEN,
    quadrant4Fill: TINT_PURPLE,
    quadrant1TextFill: COLOR_INK,
    quadrant2TextFill: COLOR_INK,
    quadrant3TextFill: COLOR_INK,
    quadrant4TextFill: COLOR_INK,
    quadrantPointFill: PALETTE[0],
    quadrantPointTextFill: COLOR_INK,
    quadrantXAxisTextFill: COLOR_INK,
    quadrantYAxisTextFill: COLOR_INK,
    quadrantTitleFill: COLOR_INK,
    quadrantInternalBorderStrokeFill: COLOR_AXIS,
    quadrantExternalBorderStrokeFill: COLOR_AXIS,
    // Pie: testi (le fette usano pie1..12 sotto).
    pieTitleTextColor: COLOR_INK,
    pieSectionTextColor: COLOR_INK,
    pieLegendTextColor: COLOR_INK,
    pieStrokeColor: COLOR_WHITE,
    pieOuterStrokeColor: COLOR_WHITE,
    // Senza questa Mermaid 11 default a 0.7: fette schiarite e diverse
    // dai riquadri della legenda.
    pieOpacity: "1",
    // xychart: palette delle serie, assi e titolo.
    xyChart: {
      backgroundColor: COLOR_WHITE,
      titleColor: COLOR_INK,
      xAxisTitleColor: COLOR_INK,
      xAxisLabelColor: COLOR_INK,
      xAxisTickColor: COLOR_AXIS,
      xAxisLineColor: COLOR_AXIS,
      yAxisTitleColor: COLOR_INK,
      yAxisLabelColor: COLOR_INK,
      yAxisTickColor: COLOR_AXIS,
      yAxisLineColor: COLOR_AXIS,
      plotColorPalette: PALETTE.join(", "),
    },
    // Radar: assi e graticola neutri; le curve usano cScale0..n.
    radar: { axisColor: COLOR_AXIS, graticuleColor: COLOR_GRID },
  };
  // Scala categoriale cScale0..11 (mindmap, timeline, radar, treemap):
  // palette ciclica; `cScaleLabel` è il testo sopra il colore pieno,
  // `cScaleInv` (sottolineature) resta neutro.
  for (let i = 0; i < 12; i += 1) {
    themeVariables[`cScale${i}`] = PALETTE[i % PALETTE.length];
    themeVariables[`cScaleLabel${i}`] = PALETTE_LABEL[i % PALETTE.length];
    themeVariables[`cScaleInv${i}`] = COLOR_AXIS;
  }
  // Fette pie1..12 e radice di mindmap/timeline (git0, gitBranchLabel0).
  for (let i = 0; i < 12; i += 1) {
    themeVariables[`pie${i + 1}`] = PALETTE[i % PALETTE.length];
  }
  PALETTE.forEach((color, i) => {
    themeVariables[`git${i}`] = color;
    themeVariables[`gitBranchLabel${i}`] = PALETTE_LABEL[i];
  });
  return {
    startOnLoad: false,
    theme: "neutral",
    htmlLabels: false,
    securityLevel,
    fontFamily: MERMAID_FONT_FAMILY,
    themeVariables,
    flowchart: { htmlLabels: false, ...perType },
    sequence: { ...perType },
    class: { htmlLabels: false, ...perType },
    state: { ...perType },
    er: { ...perType },
    gantt: { ...perType },
    pie: { ...perType },
    mindmap: { ...perType },
    timeline: { ...perType },
    xyChart: { ...perType },
    quadrantChart: { ...perType },
    sankey: { linkColor: "source", ...perType },
    block: { ...perType },
    radar: { ...perType },
  };
}

/** `config` Vega-Lite iniettato nella spec prima del render (mai scritto dal modello). */
export const VEGALITE_THEME_CONFIG: Record<string, unknown> = {
  font: FONT_FAMILY_PRIMARY,
  background: "transparent",
  padding: 8,
  // `discrete*`: senza, le scale band/point userebbero il passo di
  // default (20 px) e le barre uscirebbero minuscole.
  view: {
    stroke: null,
    continuousWidth: 360,
    continuousHeight: 220,
    discreteWidth: 360,
    discreteHeight: 220,
  },
  axis: {
    labelFont: FONT_FAMILY_PRIMARY,
    titleFont: FONT_FAMILY_PRIMARY,
    labelFontSize: 11,
    titleFontSize: 12,
    titleFontWeight: "normal",
    labelColor: COLOR_INK,
    titleColor: COLOR_INK,
    domainColor: COLOR_AXIS,
    tickColor: COLOR_AXIS,
    gridColor: COLOR_GRID,
    gridWidth: 0.6,
    labelLimit: 120,
  },
  // Asse x discreto in orizzontale (il default di Vega-Lite lo ruota).
  axisX: { labelAngle: 0, labelOverlap: "greedy" },
  legend: {
    labelFont: FONT_FAMILY_PRIMARY,
    titleFont: FONT_FAMILY_PRIMARY,
    labelFontSize: 11,
    titleFontSize: 11,
    titleFontWeight: "normal",
    labelColor: COLOR_INK,
    titleColor: COLOR_INK,
    symbolType: "square",
  },
  header: {
    labelFont: FONT_FAMILY_PRIMARY,
    titleFont: FONT_FAMILY_PRIMARY,
    labelColor: COLOR_INK,
    titleColor: COLOR_INK,
  },
  title: {
    font: FONT_FAMILY_PRIMARY,
    fontSize: 13,
    fontWeight: "bold",
    anchor: "start",
    color: COLOR_INK,
  },
  text: { font: FONT_FAMILY_PRIMARY, fontSize: 11, color: COLOR_INK },
  range: { category: [...PALETTE] },
  mark: { color: PALETTE[0] },
  line: { strokeWidth: 2 },
  point: { filled: true, size: 40 },
  area: { opacity: 0.35, line: true },
  bar: { stroke: null },
};

/** Attributi DOT di default, iniettati dopo la `{` di apertura se il sorgente non li definisce. */
export const DOT_DEFAULTS = {
  graph: `graph [fontname="${FONT_FAMILY_PRIMARY}", fontsize=11, bgcolor="transparent", pad=0.2, nodesep=0.35, ranksep=0.45];`,
  node: `node [fontname="${FONT_FAMILY_PRIMARY}", fontsize=11, shape=box, style=rounded, color="${COLOR_AXIS}", fontcolor="${COLOR_INK}", penwidth=0.8, margin="0.12,0.06"];`,
  edge: `edge [fontname="${FONT_FAMILY_PRIMARY}", fontsize=10, color="${COLOR_AXIS}", fontcolor="${COLOR_INK}", penwidth=0.8, arrowsize=0.7];`,
} as const;

export type DotDefaultsBlock = keyof typeof DOT_DEFAULTS;

export function dotDefaultsPrelude(
  skip: ReadonlySet<DotDefaultsBlock> = new Set(),
): string {
  return (Object.keys(DOT_DEFAULTS) as DotDefaultsBlock[])
    .filter((k) => !skip.has(k))
    .map((k) => DOT_DEFAULTS[k])
    .join("\n");
}
