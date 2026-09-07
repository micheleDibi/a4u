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

export function isRenderableFormat(
  format: string,
): format is RenderableFormat {
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
const SVG_FORBIDDEN_SELECTOR = [
  "script",
  "foreignObject",
  "iframe",
  "image",
  "set",
  "animate",
  "animateMotion",
  "animateTransform",
  "handler",
].join(",");

/**
 * Rende inerte un SVG prodotto dal renderer client prima di appenderlo al
 * DOM (viz-js, vega-embed): elementi attivi rimossi, `<a>` sostituiti dai
 * loro figli, gestori `on*` e `href`/`xlink:href` non interni eliminati.
 * Muta l'elemento in loco.
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
      if (name.startsWith("on") || (isHref && !attr.value.trim().startsWith("#"))) {
        el.removeAttribute(attr.name);
      }
    }
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
