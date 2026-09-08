import type { TFunction } from "i18next";

import type { LessonContentVisualAssetFormat } from "@/api/courses";

/**
 * Formati degli asset visivi (alias `VisualAssetFormat` del backend,
 * `schemas/course_lesson_content.py`) e loro etichette localizzate.
 *
 * Le etichette dei formati di figura vivono in `courses.figures.formats.*`
 * (specchio di `figure_theme.FIGURE_I18N`, verificato dal test backend
 * `test_figure_i18n_mirrors_frontend`); le altre etichette usate dalle
 * multi-select delle slide (`table`, `equation`, `example`, suffisso «nuovo»)
 * stanno in `courses.lessonsSlides.editor.assetKinds.*`.
 */

/** Formati che l'editor produce oggi (menu «Aggiungi asset visivo»). */
export const VISUAL_FORMATS = [
  "mermaid",
  "vegalite",
  "dot",
  "function",
  "image",
] as const satisfies readonly LessonContentVisualAssetFormat[];

export type VisualFormat = (typeof VISUAL_FORMATS)[number];

/** Formati resi da un renderer (mai `image`, mai i legacy). */
export const RENDERABLE_FORMATS = [
  "mermaid",
  "vegalite",
  "dot",
  "function",
] as const satisfies readonly LessonContentVisualAssetFormat[];

export type RenderableFormat = (typeof RENDERABLE_FORMATS)[number];

/** Formati legacy (solo lettura): presenti nei corsi pre-refactor, resi
 *  come placeholder testuale; l'editor non li produce più. */
export const LEGACY_FORMATS = [
  "image_prompt",
  "image_search_query",
  "description",
] as const satisfies readonly LessonContentVisualAssetFormat[];

export type LegacyFormat = (typeof LEGACY_FORMATS)[number];

export function isLegacyFormat(format: string): format is LegacyFormat {
  return (LEGACY_FORMATS as readonly string[]).includes(format);
}

export function isRenderableFormat(format: string): format is RenderableFormat {
  return (RENDERABLE_FORMATS as readonly string[]).includes(format);
}

const FIGURE_FORMAT_KEYS: Record<VisualFormat, string> = {
  mermaid: "courses.figures.formats.mermaid",
  vegalite: "courses.figures.formats.vegalite",
  dot: "courses.figures.formats.dot",
  function: "courses.figures.formats.function",
  image: "courses.figures.formats.image",
};

const OTHER_KIND_KEYS: Record<string, string> = {
  table: "courses.lessonsSlides.editor.assetKinds.table",
  equation: "courses.lessonsSlides.editor.assetKinds.equation",
  example: "courses.lessonsSlides.editor.assetKinds.example",
};

export interface FormatLabelOptions {
  /** Asset creato in Fase 4 (`new_assets`, `new_tables`, ...): aggiunge il
   *  suffisso localizzato «, nuovo». */
  isNew?: boolean;
}

/**
 * Etichetta leggibile e localizzata di un formato di asset («Grafico
 * Vega-Lite», «Tabella», ...). I formati legacy e quelli sconosciuti tornano
 * come stringa grezza: non hanno una chiave e non devono mai sparire.
 */
export function formatLabel(
  format: string,
  t: TFunction,
  { isNew = false }: FormatLabelOptions = {},
): string {
  const key =
    (FIGURE_FORMAT_KEYS as Record<string, string>)[format] ??
    OTHER_KIND_KEYS[format];
  const base = key ? t(key) : format;
  if (!isNew) return base;
  return `${base}, ${t("courses.lessonsSlides.editor.assetKinds.new")}`;
}

const FENCE_OPEN_RE = /^```[a-zA-Z0-9_-]*\n?/;
const FENCE_CLOSE_RE = /\n?```\s*$/;

/** Carattere di controllo C0 (salvo tab, newline, carriage return), DEL o
 *  C1 (U+0080–U+009F): stessa classe di `_CONTROL_CHARS_RE` del backend. */
function isControlChar(ch: string): boolean {
  const code = ch.charCodeAt(0);
  return (
    (code < 32 && code !== 9 && code !== 10 && code !== 13) ||
    (code >= 0x7f && code <= 0x9f)
  );
}

/**
 * Rimuove i caratteri di controllo e un eventuale code-fence che avvolge
 * l'intero contenuto (specchio di `_strip_fence_and_control` del registro
 * backend, usato per Vega-Lite e DOT). Nessun'altra trasformazione.
 */
export function stripFenceAndControl(content: string): string {
  let v = Array.from(content || "")
    .filter((ch) => !isControlChar(ch))
    .join("")
    .trim();
  if (v.startsWith("```")) {
    v = v.replace(FENCE_OPEN_RE, "").replace(FENCE_CLOSE_RE, "").trim();
  }
  return v;
}

/**
 * Esito negativo del parse di un sorgente JSON (spec Vega-Lite o
 * `function`) o di un sorgente testuale (DOT): un codice, non una frase,
 * così il componente lo traduce con `describeFigureParseError`. Il
 * messaggio nativo di `JSON.parse` (o del renderer) resta come dettaglio
 * tecnico in `invalid`.
 */
export type FigureParseError =
  | { code: "empty" }
  | { code: "not_object" }
  | { code: "invalid"; message: string };

export function figureErrorFromException(exc: unknown): FigureParseError {
  return {
    code: "invalid",
    message: exc instanceof Error ? exc.message : String(exc),
  };
}

/** Testo localizzato di un `FigureParseError`, da mostrare nel dettaglio
 *  del `FigureErrorBox`. */
export function describeFigureParseError(
  error: FigureParseError,
  t: TFunction,
): string {
  switch (error.code) {
    case "empty":
      return t("courses.lessonsContent.render.figure.emptySource");
    case "not_object":
      return t("courses.lessonsContent.render.figure.notAnObject");
    default:
      return error.message;
  }
}

/** Chiave i18n di un'avvertenza del motore `function`: le forme indicizzate
 *  (`expression_0_undefined`) e la famiglia `symbolic_*` collassano su una
 *  chiave sola, il resto è una corrispondenza diretta. `null` per un codice
 *  che non conosciamo (mostrato tale e quale). */
function functionWarningKey(code: string): string | null {
  if (/^expression_\d+_undefined$/.test(code)) return "expressionUndefined";
  if (/^series_\d+_undefined$/.test(code)) return "seriesUndefined";
  if (code === "symbolic_timeout") return "symbolicTimeout";
  if (code.startsWith("symbolic_")) return "symbolic";
  const direct: Record<string, string> = {
    zero_interval: "zeroInterval",
    stationary_interval: "stationaryInterval",
    too_many_points: "tooManyPoints",
    tangent_undefined: "tangentUndefined",
    integral_undefined: "integralUndefined",
    point_undefined: "pointUndefined",
    levels_undefined: "levelsUndefined",
    formula_not_mathtext: "formulaNotMathtext",
    formula_too_wide: "formulaTooWide",
  };
  return direct[code] ?? null;
}

/** Frase localizzata di un'avvertenza del motore `function`, per l'anteprima
 *  dell'editor: il docente legge una spiegazione, non il codice interno. */
export function describeFunctionWarning(code: string, t: TFunction): string {
  const key = functionWarningKey(code);
  return key === null
    ? code
    : t(`courses.lessonsContent.editorUI.function.warnings.${key}`);
}

export interface ParsedJsonObject {
  value: Record<string, unknown> | null;
  error: FigureParseError | null;
}

/** Sorgente (eventualmente in un code-fence) → oggetto JSON; array, scalari
 *  e sorgente vuoto sono errori tipizzati. Nessun controllo semantico. */
export function parseJsonObject(raw: string): ParsedJsonObject {
  const text = stripFenceAndControl(raw);
  if (!text) return { value: null, error: { code: "empty" } };
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return { value: null, error: { code: "not_object" } };
    }
    return { value: value as Record<string, unknown>, error: null };
  } catch (exc) {
    return { value: null, error: figureErrorFromException(exc) };
  }
}

// Elementi che `svg_normalize.normalize_svg` rifiuta nel backend: nel DOM
// dell'anteprima vengono rimossi (il sorgente persistito passa comunque dal
// gate server-side; qui è difesa in profondità per l'auto-anteprima).
// `<style>` è compreso: un foglio interno può contenere `@import url(…)`
// e riferimenti `url(http…)`, che i renderer client non producono mai.
const SVG_FORBIDDEN_SELECTOR = [
  "script",
  "style",
  "foreignObject",
  "iframe",
  "image",
  "set",
  "animate",
  "animateMotion",
  "animateTransform",
  "handler",
].join(",");

// `url(` che non punta a un frammento interno (`url(#gradiente)` resta:
// vega lo usa per i gradienti): stessa classe di `svg_normalize`. I
// riferimenti interni sono tolti prima del test, così non serve un
// lookahead negativo (che il backtracking su `\s*` renderebbe eludibile).
const INTERNAL_URL_RE = /url\(\s*['"]?\s*#/gi;
const ANY_URL_RE = /url\(/i;

function hasExternalUrl(value: string): boolean {
  return ANY_URL_RE.test(value.replace(INTERNAL_URL_RE, ""));
}

/**
 * Rende inerte un SVG prodotto dal renderer client prima di appenderlo al
 * DOM (viz-js, vega-embed): elementi attivi e fogli di stile interni
 * rimossi, `<a>` sostituiti dai loro figli, gestori `on*`, `href`/`xlink:href`
 * non interni e attributi con `url(…)` esterni eliminati. Muta l'elemento
 * in loco.
 */
export function sanitizeSvgElement(root: Element): void {
  for (const el of Array.from(root.querySelectorAll(SVG_FORBIDDEN_SELECTOR))) {
    el.remove();
  }
  for (const anchor of Array.from(root.querySelectorAll("a"))) {
    anchor.replaceWith(...Array.from(anchor.childNodes));
  }
  for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const isHref = name === "href" || name === "xlink:href";
      if (
        name.startsWith("on") ||
        (isHref && !attr.value.trim().startsWith("#")) ||
        hasExternalUrl(attr.value)
      ) {
        el.removeAttribute(attr.name);
      }
    }
  }
}

// Elementi che un SVG Mermaid D8 non contiene mai (`htmlLabels: false`,
// tipi ammessi dal gate) e che caricano o eseguono qualcosa: `<image>`
// nasce solo dalle shape `img:`/`icon:` e da `sequenceDiagram properties`,
// gli elementi SMIL possono riscrivere `href` o `on*` a tempo di
// esecuzione. `<style>` NON è qui, al contrario di `SVG_FORBIDDEN_SELECTOR`:
// Mermaid ci mette il tema (colori, font, tratti) e toglierlo
// smonterebbe la figura; il suo CSS viene ripulito, non rimosso.
// `<foreignObject>` non è qui perché con `htmlLabels: false` non compare
// (misurato su tutti e 15 i tipi) e rimuoverlo cancellerebbe una label.
const MERMAID_ACTIVE_SELECTOR = [
  "script",
  "iframe",
  "image",
  "set",
  "animate",
  "animateMotion",
  "animateTransform",
  "handler",
].join(",");

const CSS_IMPORT_RE = /@import[^;}]*;?/gi;
const CSS_EXTERNAL_URL_RE = /url\(\s*(?!\s*['"]?#)[^)]*\)/gi;

/** CSS di un `<style>` interno senza `@import` e senza `url(…)` esterni
 *  (i riferimenti a un frammento `url(#id)` restano). */
function sanitizeSvgCss(css: string): string {
  return css.replace(CSS_IMPORT_RE, "").replace(CSS_EXTERNAL_URL_RE, "none");
}

/**
 * Rende inerte l'SVG di Mermaid prima che entri nel documento: `<image>`
 * ed elementi attivi rimossi, `<a>` sostituiti dai propri figli (il testo
 * del nodo resta, il collegamento no), gestori `on*`, `href`/`xlink:href`
 * non interni e `url(…)` esterni eliminati dagli attributi, `@import` e
 * `url(…)` esterni tolti dal CSS interno. Muta l'elemento in loco.
 *
 * Non è un doppione del gate del PATCH: nell'editor e nella vista lezione
 * il diagramma è reso da Mermaid nel browser di CHI GUARDA, senza passare
 * dal backend, quindi un sorgente che il gate statico non riconosce
 * farebbe partire la richiesta da quel browser (SEC-1, residuo del giro 4).
 */
export function sanitizeMermaidSvgElement(root: Element): void {
  for (const el of Array.from(root.querySelectorAll(MERMAID_ACTIVE_SELECTOR))) {
    el.remove();
  }
  for (const anchor of Array.from(root.querySelectorAll("a"))) {
    anchor.replaceWith(...Array.from(anchor.childNodes));
  }
  for (const style of Array.from(root.querySelectorAll("style"))) {
    const css = style.textContent ?? "";
    const clean = sanitizeSvgCss(css);
    if (clean !== css) style.textContent = clean;
  }
  for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const isHref = name === "href" || name.endsWith(":href");
      if (
        name.startsWith("on") ||
        (isHref && !attr.value.trim().startsWith("#")) ||
        hasExternalUrl(attr.value)
      ) {
        el.removeAttribute(attr.name);
      }
    }
  }
}

/**
 * `sanitizeMermaidSvgElement` sul markup che `mermaid.render` restituisce:
 * la stringa è analizzata in un documento INERTE (`DOMParser`, nessun
 * contesto di navigazione: le risorse non vengono scaricate) e riserializzata,
 * così l'`<image href="http://…">` sparisce PRIMA di toccare il documento
 * vivo. Stringa vuota se il markup non contiene un `<svg>`.
 */
export function sanitizeMermaidSvg(svg: string): string {
  const doc = new DOMParser().parseFromString(svg || "", "text/html");
  const root = doc.body.querySelector("svg");
  if (!root) return "";
  sanitizeMermaidSvgElement(root);
  return new XMLSerializer().serializeToString(root);
}

export interface SvgSize {
  width: number;
  height: number;
}

const SVG_ROOT_RE = /<svg\b[^>]*>/i;
const NUM = String.raw`[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?`;
const VIEWBOX_RE = new RegExp(
  String.raw`\bviewBox\s*=\s*["']\s*${NUM}[\s,]+${NUM}[\s,]+(${NUM})[\s,]+(${NUM})\s*["']`,
  "i",
);
const WIDTH_RE = new RegExp(
  String.raw`\bwidth\s*=\s*["']\s*(${NUM})(?:px)?\s*["']`,
  "i",
);
const HEIGHT_RE = new RegExp(
  String.raw`\bheight\s*=\s*["']\s*(${NUM})(?:px)?\s*["']`,
  "i",
);

/**
 * Dimensioni intrinseche di un SVG (testo) dal `viewBox` del tag radice,
 * in subordine da `width`/`height` numerici (non percentuali); `null` se
 * non determinabili o non positive. Mermaid emette sempre il `viewBox`.
 */
export function svgIntrinsicSize(svg: string): SvgSize | null {
  const root = SVG_ROOT_RE.exec(svg || "")?.[0];
  if (!root) return null;
  const vb = VIEWBOX_RE.exec(root);
  let width = vb ? Number(vb[1]) : Number.NaN;
  let height = vb ? Number(vb[2]) : Number.NaN;
  if (!(width > 0 && height > 0)) {
    width = Number(WIDTH_RE.exec(root)?.[1]);
    height = Number(HEIGHT_RE.exec(root)?.[1]);
  }
  return width > 0 && height > 0 ? { width, height } : null;
}

/** 28rem a 16px: `max_figure_height_cm` del PDF e `max-h-[28rem]` delle
 *  immagini caricate. */
export const FULL_WIDTH_SVG_CAP_PX = 448;

/**
 * Tetto d'altezza (px) di un SVG reso a larghezza piena (`MermaidDiagram`):
 * un diagramma orizzontale (larghezza ≥ altezza) non si dilata oltre
 * `capPx` di altezza, ma non scende mai sotto la propria altezza naturale;
 * un diagramma verticale (sequence, flowchart TD, class) non ha tetto e
 * conserva la geometria a larghezza piena, perché un `max-height` unito a
 * `width: 100%` lo farebbe scalare in `meet` fino a renderlo illeggibile.
 * `null` = nessun tetto.
 */
export function fullWidthSvgMaxHeightPx(
  size: SvgSize | null,
  capPx: number = FULL_WIDTH_SVG_CAP_PX,
): number | null {
  if (!size || size.width < size.height) return null;
  return Math.max(capPx, Math.ceil(size.height));
}

/** `data:image/svg+xml;base64,...` di un SVG (testo UTF-8), come
 *  `svg_normalize.svg_to_data_uri` nel backend: le figure `function`
 *  arrivano già normalizzate dal server e sono mostrate in un `<img>`. */
export function svgDataUri(svg: string): string {
  const bytes = new TextEncoder().encode(svg);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return `data:image/svg+xml;base64,${btoa(binary)}`;
}
