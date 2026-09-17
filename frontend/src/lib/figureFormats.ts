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

/** La sola parte di Mermaid che serve qui: il modulo è importato a
 *  richiesta da `MermaidDiagram.tsx` e non deve entrare in questo bundle,
 *  nemmeno come tipo. */
interface MermaidRenderer {
  render(id: string, code: string): Promise<{ svg: string }>;
}

/**
 * `mermaid.render` con il nodo di misura sempre rimosso.
 *
 * Mermaid calcola la geometria del diagramma dentro un `<div id="d<id>">`
 * che ATTACCA al `<body>`, e lo toglie solo quando il render arriva in
 * fondo: se `draw` lancia, quel div resta nella pagina — in flusso
 * normale, largo quanto la finestra, con dentro l'SVG. In produzione
 * l'innesco è certo, perché la Content-Security-Policy della pagina fa
 * fallire il caricamento di una shape `img:` esterna (`EncodingError`), e
 * l'editor rende a ogni battuta: dieci tentativi lasciavano dieci copie
 * impilate sotto l'applicazione (Fase D, giro 8). Il `finally` è un no-op
 * sul percorso felice, dove il nodo l'ha già tolto Mermaid.
 *
 * Vale con `securityLevel: "strict"` (quello di `figureTheme`): in
 * `sandbox` il nodo temporaneo sarebbe l'`<iframe id="i<id>">`.
 */
export async function renderMermaidSvg(
  mermaid: MermaidRenderer,
  id: string,
  code: string,
): Promise<string> {
  try {
    const { svg } = await mermaid.render(id, code);
    return svg;
  } finally {
    document.getElementById(`d${id}`)?.remove();
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

// ---------------------------------------------------------------------------
// Banda di leggibilità (D10, D11): mirror di `app/services/figure_scale.py`
// e di `svg_normalize.svg_intrinsic_box`, con parità provata dalla fixture
// condivisa `backend/tests/fixtures/figure_scale_cases.json`
// (`test_figure_scale.py::test_frontend_copy_matches_fixture`, Node con
// `--experimental-strip-types`: qui solo import di tipo, niente DOM a
// import). Stesso ordine di operazioni e stessi arrotondamenti del
// Python: larghezza per DIFETTO al centesimo di mm (`Math.floor`), scala e
// corpo half-up (`Math.floor(x·10^n + 0.5)/10^n`), mai `toFixed` sui numeri
// confrontati.
// ---------------------------------------------------------------------------

/** Millimetri per px CSS (96 px = 25,4 mm). */
export const MM_PER_PX = 25.4 / 96;
/** Punti tipografici per px CSS. */
export const PT_PER_PX = 0.75;

export type FigureVariant = "lesson" | "slide";

/** Bande di leggibilità in pt per superficie (D11): la dispensa e il web
 *  8-11 pt, slide e frame video 10-14 pt. */
export const READABILITY_BANDS_PT: Readonly<
  Record<FigureVariant, readonly [number, number]>
> = {
  lesson: [8, 11],
  slide: [10, 14],
};
const BAND_EPS = 1e-9;

/** Corpo di Mermaid quando la misura nel DOM fallisce: `themeVariables.
 *  fontSize` del tema (`figureTheme.ts`, 14px), lo stesso fallback di
 *  `figure_scale.FALLBACK_BASE_FONT_PX["mermaid"]`. */
export const MERMAID_FALLBACK_FONT_PX = 14;

export interface SvgBox {
  /** viewBox in unità utente. */
  vbW: number;
  vbH: number;
  /** Dimensione intrinseca della radice in px (`null` per `width="100%"`). */
  widthPx: number | null;
  heightPx: number | null;
  /** `widthPx / vbW`, 1.0 per gli SVG fluidi. */
  pxPerUnit: number;
}

export interface FigureFit {
  /** Larghezza da mettere sul wrapper: floor al centesimo di mm, sempre
   *  entro il box. */
  widthMm: number;
  /** Rispetto a `vbW` px (fluidi) o a `intrinsicWPx` (`<img>`). */
  scale: number;
  /** Corpo minimo alla larghezza scelta (0 senza testo). */
  textPt: number;
  inBand: boolean;
}

export interface FigureFitInput {
  vbW: number;
  vbH: number;
  baseFontPx: number | null;
  boxWMm: number | null;
  boxHMm: number | null;
  variant?: FigureVariant;
  intrinsicWPx?: number | null;
}

export interface SvgFontMetrics {
  /** Corpo più piccolo fra i testi di contenuto, in unità utente
   *  (`getComputedStyle().fontSize`), `null` senza testi. */
  min: number | null;
  median: number | null;
  count: number;
}

const SVG_ROOT_RE = /<svg\b([^>]*)>/i;
const SVG_ATTR_RE = /([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
const LENGTH_RE = /^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-z%]*)\s*$/;
const NUMBER_RE = /[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/g;
const UNIT_TO_PX: Record<string, number> = {
  "": 1,
  px: 1,
  pt: 96 / 72,
  pc: 16,
  mm: 96 / 25.4,
  cm: 96 / 2.54,
  in: 96,
};

/** Lunghezza CSS/SVG in px; `null` per assente, percentuale o unità non
 *  convertibile (mirror di `svg_normalize._length_px`). */
function lengthPx(value: string | undefined): number | null {
  if (value === undefined) return null;
  const m = LENGTH_RE.exec(value);
  if (!m) return null;
  const factor = UNIT_TO_PX[m[2].toLowerCase()];
  if (factor === undefined) return null;
  const px = Number(m[1]) * factor;
  return px > 0 ? px : null;
}

function parseViewBox(value: string | undefined): [number, number] | null {
  if (!value) return null;
  const nums = value.match(NUMBER_RE);
  if (!nums || nums.length !== 4) return null;
  const w = Number(nums[2]);
  const h = Number(nums[3]);
  if (w <= 0 || h <= 0) return null;
  return [w, h];
}

/** Attributi del tag radice con i nomi in minuscolo (l'ultimo vince);
 *  `null` senza `<svg>`. */
function svgRootAttrs(svg: string): Record<string, string> | null {
  const root = SVG_ROOT_RE.exec(svg || "");
  if (!root) return null;
  const out: Record<string, string> = {};
  for (const m of root[1].matchAll(SVG_ATTR_RE)) {
    out[m[1].toLowerCase()] = m[2] !== undefined ? m[2] : (m[3] ?? "");
  }
  return out;
}

/**
 * Geometria della radice di un SVG (testo): viewBox in unità utente,
 * dimensione intrinseca in px se dichiarata e `pxPerUnit`; senza `viewBox`
 * valgono `width`/`height` numerici (stessa regola di `normalize_svg`).
 * `null` se non determinabile (nessuna radice, viewBox degenere, sola
 * larghezza in percentuale). Mirror di `svg_normalize.svg_intrinsic_box`.
 */
export function svgIntrinsicBox(svg: string): SvgBox | null {
  const attrs = svgRootAttrs(svg);
  if (!attrs) return null;
  const widthPx = lengthPx(attrs.width);
  const heightPx = lengthPx(attrs.height);
  let viewBox = parseViewBox(attrs.viewbox);
  if (!viewBox && widthPx !== null && heightPx !== null) {
    viewBox = [widthPx, heightPx];
  }
  if (!viewBox) return null;
  const [vbW, vbH] = viewBox;
  return {
    vbW,
    vbH,
    widthPx,
    heightPx,
    pxPerUnit: widthPx !== null ? widthPx / vbW : 1,
  };
}

export interface SvgSize {
  width: number;
  height: number;
}

/** Dimensioni intrinseche in unità utente: proiezione di `svgIntrinsicBox`. */
export function svgIntrinsicSize(svg: string): SvgSize | null {
  const box = svgIntrinsicBox(svg);
  return box ? { width: box.vbW, height: box.vbH } : null;
}

function halfUp(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.floor(value * factor + 0.5) / factor;
}

function positive(value: number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  return Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * Larghezza (mm) a cui rendere la figura perché il testo più piccolo cada
 * nella banda della superficie, entro il box (mirror riga per riga di
 * `figure_scale.fit_figure_width_mm`).
 *
 * Politica di scala: gli SVG fluidi (`intrinsicWPx` assente: Mermaid,
 * `width="100%"`) riempiono il box e vengono ridotti al tetto della banda;
 * gli `<img>` intrinseci partono da scala 1, crescono solo fino al fondo
 * della banda, scendono al tetto se sopra; mai oltre il box; senza testo
 * scala naturale; banda irraggiungibile → larghezza massima del box e
 * `inBand: false`. Un box `null` è «nessun vincolo» (web: la colonna la
 * applica il CSS `min(100%, …)`). `null` per input degeneri; `variant`
 * ignota → eccezione.
 */
export function fitFigureWidthMm({
  vbW,
  vbH,
  baseFontPx,
  boxWMm,
  boxHMm,
  variant = "lesson",
  intrinsicWPx = null,
}: FigureFitInput): FigureFit | null {
  const band = READABILITY_BANDS_PT[variant];
  if (band === undefined) throw new Error(`variant sconosciuta: ${String(variant)}`);
  const [lo, hi] = band;
  if (positive(vbW) === null || positive(vbH) === null) return null;
  if (boxWMm !== null && positive(boxWMm) === null) return null;
  if (boxHMm !== null && positive(boxHMm) === null) return null;
  if (intrinsicWPx !== null && positive(intrinsicWPx) === null) return null;

  const fluid = intrinsicWPx === null;
  const refWMm = (intrinsicWPx === null ? vbW : intrinsicWPx) * MM_PER_PX;
  const refHMm = (refWMm * vbH) / vbW;
  let sBox = Number.POSITIVE_INFINITY;
  if (boxWMm !== null) sBox = Math.min(sBox, boxWMm / refWMm);
  if (boxHMm !== null) sBox = Math.min(sBox, boxHMm / refHMm);

  const base = baseFontPx !== null ? positive(baseFontPx) : null;
  let scale: number;
  let textPt: number;
  let inBand: boolean;
  if (base === null) {
    // Senza testo: scala naturale, nessun vincolo di banda.
    scale = Math.min(1, sBox);
    textPt = 0;
    inBand = true;
  } else {
    const ptPerScale = base * PT_PER_PX;
    const sLo = lo / ptPerScale;
    const sHi = hi / ptPerScale;
    const sNat = fluid ? sBox : 1;
    scale = Math.min(Math.min(Math.max(sNat, sLo), sHi), sBox);
    textPt = ptPerScale * scale;
    inBand = textPt >= lo - BAND_EPS;
  }

  const widthMm = Math.floor(refWMm * scale * 100) / 100;
  return {
    widthMm,
    scale: halfUp(scale, 4),
    textPt: halfUp(textPt, 2),
    inBand,
  };
}

/** `"140.76"`, `"168"`, `"0"`: stessa regola di `figure_scale.format_mm`
 *  (`.2f` senza zeri finali); il valore arriva già al centesimo. */
export function formatMm(value: number): string {
  const text = value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return text || "0";
}

/**
 * Corpo del testo di un SVG misurato nel DOM: `{min, median, count}` sui
 * `text`/`tspan` con nodo di testo proprio non vuoto e non `display:none`,
 * in unità utente (`getComputedStyle().fontSize` non dipende da viewBox né
 * dalla larghezza resa). Mirror funzionale (non copia letterale) di
 * `MEASURE_SVG_FONT_PX_JS` nel pre-render backend (`mermaid_prerender.py`):
 * stessa selezione, stesso filtro, stessa mediana; parità provata in
 * Chromium da `test_frontend_figure_layout.py`. Host fuori schermo, MAI
 * `visibility:hidden` (azzererebbe i testi), rimosso in `finally`. `null`
 * se la misura fallisce (nessun DOM, eccezione): il chiamante ripiega sul
 * fallback del tema.
 */
export function measureSvgFontPx(svg: string): SvgFontMetrics | null {
  let host: HTMLDivElement | null = null;
  try {
    host = document.createElement("div");
    host.style.cssText = "position:absolute;left:-100000px;top:0;width:1000px";
    host.innerHTML = svg;
    document.body.appendChild(host);
    const sizes: number[] = [];
    for (const el of host.querySelectorAll("text, tspan")) {
      const own = Array.from(el.childNodes).some(
        (n) => n.nodeType === 3 && (n.textContent ?? "").trim() !== "",
      );
      if (!own) continue;
      const cs = getComputedStyle(el);
      if (cs.display === "none") continue;
      const px = parseFloat(cs.fontSize);
      if (Number.isFinite(px) && px > 0) sizes.push(px);
    }
    if (sizes.length === 0) return { min: null, median: null, count: 0 };
    sizes.sort((a, b) => a - b);
    const mid = sizes.length >> 1;
    const median =
      sizes.length % 2 === 1 ? sizes[mid] : (sizes[mid - 1] + sizes[mid]) / 2;
    return { min: sizes[0], median, count: sizes.length };
  } catch {
    return null;
  } finally {
    host?.remove();
  }
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
