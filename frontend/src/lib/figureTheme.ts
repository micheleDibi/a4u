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

export const THEME_VERSION = "2026.09.1";

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
const TINT_NOTE = "#FBF7E4";

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
 * palette (ogni tema Mermaid applica gli override, non solo `base`).
 */
export function mermaidConfig({
  useMaxWidth,
  securityLevel = "strict",
}: MermaidConfigOptions): MermaidConfig {
  const perType = { useMaxWidth };
  const themeVariables: Record<string, unknown> = {
    fontFamily: MERMAID_FONT_FAMILY,
    fontSize: "14px",
    background: "#ffffff",
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
    noteBkgColor: TINT_NOTE,
    noteBorderColor: PALETTE[3],
    noteTextColor: COLOR_INK,
    xyChart: { plotColorPalette: PALETTE.join(", ") },
  };
  PALETTE.forEach((color, i) => {
    themeVariables[`pie${i + 1}`] = color;
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
    sankey: { ...perType },
    block: { ...perType },
    radar: { ...perType },
  };
}

/** `config` Vega-Lite iniettato nella spec prima del render (mai scritto dal modello). */
export const VEGALITE_THEME_CONFIG: Record<string, unknown> = {
  font: FONT_FAMILY_PRIMARY,
  background: "transparent",
  padding: 8,
  view: { stroke: null, continuousWidth: 360, continuousHeight: 220 },
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

export function dotDefaultsPrelude(skip: ReadonlySet<DotDefaultsBlock> = new Set()): string {
  return (Object.keys(DOT_DEFAULTS) as DotDefaultsBlock[])
    .filter((k) => !skip.has(k))
    .map((k) => DOT_DEFAULTS[k])
    .join("\n");
}
