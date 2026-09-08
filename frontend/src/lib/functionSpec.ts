import type {
  FunctionAnnotation,
  FunctionFigureSpec,
  FunctionKind,
  FunctionShowItem,
} from "@/api/courses";

import { parseJsonObject, type FigureParseError } from "./figureFormats";

/**
 * Helper della spec `function` (D9) condivisi da `FunctionFigure` (vista)
 * e `FunctionEditor` (modulo a campi): parse di `content`, spec di
 * partenza, normalizzazione e completezza locale prima di chiedere
 * l'anteprima al backend. I limiti sono quelli di
 * `schemas/figure_function.py` (validazione autoritativa lato server).
 */

export const FUNCTION_KINDS: readonly FunctionKind[] = [
  "function_study",
  "tangent",
  "area",
  "family",
  "level_curves",
];

export const FUNCTION_SHOW_ITEMS: readonly FunctionShowItem[] = [
  "zeros",
  "critical_points",
  "inflection_points",
  "asymptotes",
  "discontinuities",
  "formula",
];

export const ANNOTATION_KINDS = ["tangent", "area", "point"] as const;
export type AnnotationKind = (typeof ANNOTATION_KINDS)[number];

export const MAX_EXPRESSIONS = 4;
export const MAX_ANNOTATIONS = 6;
export const MAX_PARAMETER_VALUES = 6;
export const MIN_POINTS = 100;
export const MAX_POINTS = 2000;
export const DEFAULT_POINTS = 800;
export const MIN_LEVELS = 2;
export const MAX_LEVELS = 12;

/** Analisi di default quando `show` è omesso (come `DEFAULT_SHOW` backend). */
export const DEFAULT_SHOW: readonly FunctionShowItem[] = [
  "zeros",
  "critical_points",
  "asymptotes",
  "formula",
];

/** Spec di partenza di un asset `function` nuovo. */
export const DEFAULT_FUNCTION_SPEC: FunctionFigureSpec = {
  kind: "function_study",
  expressions: [{ expr: "x**2 - 2", label: "" }],
  variable: "x",
  variables: null,
  domain: [-3, 3],
  range: null,
  show: [...DEFAULT_SHOW],
  annotations: [],
  parameter: null,
  sampling: { points: DEFAULT_POINTS },
  levels: null,
};

const VAR_RE = /^[a-zA-Z]$/;

export function isVarName(value: unknown): value is string {
  return typeof value === "string" && VAR_RE.test(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isInterval(value: unknown): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    isFiniteNumber(value[0]) &&
    isFiniteNumber(value[1]) &&
    value[0] < value[1]
  );
}

export interface ParsedFunctionContent {
  spec: FunctionFigureSpec | null;
  /** Errore tipizzato (codice, non frase): il componente lo traduce con
   *  `describeFigureParseError`. */
  error: FigureParseError | null;
}

/** `content` (stringa JSON) → spec, senza controlli semantici: quelli
 *  sono del backend, che risponde con `meta.errors` per campo. */
export function parseFunctionContent(content: string): ParsedFunctionContent {
  const { value, error } = parseJsonObject(content);
  return { spec: value as FunctionFigureSpec | null, error };
}

/**
 * Spec con tutti i campi presenti (array vuoti, `null` espliciti, sampling
 * di default): lo stato dell'editor lavora su questa forma e serializza
 * SOLO i campi significativi (`serializeFunctionSpec`).
 */
export function normalizeFunctionSpec(
  raw: Partial<FunctionFigureSpec> | null | undefined,
): FunctionFigureSpec {
  const base = raw ?? {};
  const kind = FUNCTION_KINDS.includes(base.kind as FunctionKind)
    ? (base.kind as FunctionKind)
    : "function_study";
  const expressions = Array.isArray(base.expressions)
    ? base.expressions
        .filter((e) => e && typeof e === "object")
        .map((e) => ({
          expr: typeof e.expr === "string" ? e.expr : "",
          label: typeof e.label === "string" ? e.label : "",
        }))
        .slice(0, MAX_EXPRESSIONS)
    : [];
  const show = Array.isArray(base.show)
    ? base.show.filter((s): s is FunctionShowItem =>
        FUNCTION_SHOW_ITEMS.includes(s as FunctionShowItem),
      )
    : [...DEFAULT_SHOW];
  const annotations = Array.isArray(base.annotations)
    ? base.annotations
        .filter(
          (a): a is FunctionAnnotation =>
            !!a &&
            typeof a === "object" &&
            ANNOTATION_KINDS.includes((a as FunctionAnnotation).kind),
        )
        .slice(0, MAX_ANNOTATIONS)
    : [];
  const points = base.sampling?.points;
  return {
    kind,
    expressions: expressions.length > 0 ? expressions : [{ expr: "", label: "" }],
    variable: isVarName(base.variable) ? base.variable : "x",
    variables:
      Array.isArray(base.variables) && base.variables.length === 2
        ? [String(base.variables[0] ?? ""), String(base.variables[1] ?? "")]
        : null,
    domain: Array.isArray(base.domain)
      ? [Number(base.domain[0]), Number(base.domain[1])]
      : [...DEFAULT_FUNCTION_SPEC.domain],
    range: Array.isArray(base.range)
      ? [Number(base.range[0]), Number(base.range[1])]
      : null,
    show: [...new Set(show)],
    annotations,
    parameter:
      base.parameter && typeof base.parameter === "object"
        ? {
            name: String(base.parameter.name ?? ""),
            values: Array.isArray(base.parameter.values)
              ? base.parameter.values.map(Number)
              : [],
          }
        : null,
    sampling: {
      points: isFiniteNumber(points) ? Math.round(points) : DEFAULT_POINTS,
    },
    levels:
      Array.isArray(base.levels)
        ? base.levels.map(Number)
        : isFiniteNumber(base.levels)
          ? Math.round(base.levels)
          : null,
  };
}

/** Spec pronta per l'endpoint e per `content`: `extra="forbid"` nel
 *  backend, quindi niente chiavi vuote o `null` superflui. */
export function toWireSpec(spec: FunctionFigureSpec): FunctionFigureSpec {
  const out: FunctionFigureSpec = {
    kind: spec.kind,
    expressions: spec.expressions.map((e) =>
      e.label && e.label.trim() ? { expr: e.expr, label: e.label.trim() } : { expr: e.expr },
    ),
    variable: spec.variable ?? "x",
    domain: spec.domain,
  };
  if (spec.variables) out.variables = spec.variables;
  if (spec.range) out.range = spec.range;
  if (spec.show) out.show = spec.show;
  if (spec.annotations && spec.annotations.length > 0) {
    out.annotations = spec.annotations.map((a) => {
      const label = a.label && a.label.trim() ? { label: a.label.trim() } : {};
      const exprIndex = a.expr_index ? { expr_index: a.expr_index } : {};
      if (a.kind === "area") {
        return {
          kind: "area",
          between: a.between,
          ...exprIndex,
          ...(a.against != null ? { against: a.against } : {}),
          ...label,
        };
      }
      return { kind: a.kind, at: a.at, ...exprIndex, ...label };
    });
  }
  if (spec.parameter) out.parameter = spec.parameter;
  if (spec.sampling && spec.sampling.points !== DEFAULT_POINTS) {
    out.sampling = { points: spec.sampling.points };
  }
  if (spec.levels != null) out.levels = spec.levels;
  return out;
}

export function serializeFunctionSpec(spec: FunctionFigureSpec): string {
  return JSON.stringify(toWireSpec(spec));
}

/**
 * Completezza locale: abbastanza per chiedere l'anteprima al backend senza
 * un 422 scontato (campi obbligatori del `kind`, intervalli ordinati,
 * nomi di variabile di una lettera). La validazione vera resta del server.
 */
export function isLocallyComplete(spec: FunctionFigureSpec): boolean {
  if (spec.expressions.length === 0) return false;
  if (spec.expressions.some((e) => !e.expr.trim())) return false;
  if (!isInterval(spec.domain)) return false;
  if (spec.range != null && !isInterval(spec.range)) return false;
  if (!isVarName(spec.variable ?? "x")) return false;
  const annotations = spec.annotations ?? [];
  for (const a of annotations) {
    if (a.kind === "area") {
      if (!isInterval(a.between)) return false;
    } else if (!isFiniteNumber(a.at)) {
      return false;
    }
  }
  switch (spec.kind) {
    case "tangent":
      return annotations.some((a) => a.kind === "tangent");
    case "area":
      return annotations.some((a) => a.kind === "area");
    case "family":
      return (
        !!spec.parameter &&
        isVarName(spec.parameter.name) &&
        spec.parameter.values.length > 0 &&
        spec.parameter.values.every(isFiniteNumber)
      );
    case "level_curves":
      return (
        !!spec.variables &&
        isVarName(spec.variables[0]) &&
        isVarName(spec.variables[1]) &&
        spec.variables[0] !== spec.variables[1] &&
        spec.levels != null &&
        (Array.isArray(spec.levels)
          ? spec.levels.length >= MIN_LEVELS && spec.levels.every(isFiniteNumber)
          : spec.levels >= MIN_LEVELS)
      );
    default:
      return true;
  }
}

/** Adatta la spec al `kind` scelto (parametro, variabili, livelli,
 *  annotazioni obbligatorie), così il modulo mostra campi coerenti. */
export function adaptSpecToKind(
  spec: FunctionFigureSpec,
  kind: FunctionKind,
): FunctionFigureSpec {
  const [lo, hi] = spec.domain;
  const mid = Number.isFinite(lo) && Number.isFinite(hi) ? (lo + hi) / 2 : 0;
  const annotations = [...(spec.annotations ?? [])];
  const next: FunctionFigureSpec = { ...spec, kind, annotations };
  if (kind === "family") {
    next.expressions = spec.expressions.slice(0, 1);
    next.annotations = [];
    next.parameter = spec.parameter ?? { name: "a", values: [1, 2, 3] };
  } else {
    next.parameter = null;
  }
  if (kind === "level_curves") {
    next.expressions = spec.expressions.slice(0, 1);
    next.annotations = [];
    next.variables = spec.variables ?? ["x", "y"];
    next.levels = spec.levels ?? 6;
  } else {
    next.variables = null;
    next.levels = null;
  }
  if (kind === "tangent" && !annotations.some((a) => a.kind === "tangent")) {
    next.annotations = [...annotations, { kind: "tangent", at: mid, expr_index: 0, label: "" }];
  }
  if (kind === "area" && !annotations.some((a) => a.kind === "area")) {
    const a = Number.isFinite(lo) ? lo + (hi - lo) / 4 : 0;
    const b = Number.isFinite(hi) ? hi - (hi - lo) / 4 : 1;
    next.annotations = [
      ...annotations,
      { kind: "area", between: [a, b], expr_index: 0, against: null, label: "" },
    ];
  }
  return next;
}
