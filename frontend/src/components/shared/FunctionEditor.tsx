import { keepPreviousData, useQuery } from "@tanstack/react-query";
import katex from "katex";
import "katex/dist/katex.min.css";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  coursesApi,
  type FunctionAnnotation,
  type FunctionFigureSpec,
  type FunctionKind,
  type FunctionShowItem,
} from "@/api/courses";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import {
  apiErrorEntries,
  apiErrorFieldKey,
  describeApiError,
  extractApiError,
  type ApiErrorEntry,
} from "@/lib/errors";
import { svgDataUri } from "@/lib/figureFormats";
import {
  ANNOTATION_KINDS,
  DEFAULT_FUNCTION_SPEC,
  FUNCTION_KINDS,
  FUNCTION_SHOW_ITEMS,
  MAX_ANNOTATIONS,
  MAX_EXPRESSIONS,
  MAX_PARAMETER_VALUES,
  MAX_POINTS,
  MIN_POINTS,
  adaptSpecToKind,
  isLocallyComplete,
  normalizeFunctionSpec,
  parseFunctionContent,
  serializeFunctionSpec,
  toWireSpec,
  type AnnotationKind,
} from "@/lib/functionSpec";
import { cn } from "@/lib/utils";

import { FigureErrorBox, FigureFrame, FigureLoading } from "./FigureFrame";

/**
 * Editor a campi di una figura `function` (D9): il docente compila tipo di
 * figura, espressioni, variabile, dominio/intervallo, analisi da mostrare,
 * annotazioni, parametro e campionamento; la spec è riserializzata in JSON
 * a ogni modifica (`content` dell'asset) e il docente non vede mai JSON.
 *
 * Anteprima: `useDebouncedValue(spec, 700)` + `useQuery` su
 * `coursesApi.lessonAssets.renderFunction` (`placeholderData:
 * keepPreviousData` per non far sparire la figura mentre si digita);
 * l'SVG entra in una `FigureFrame` con la didascalia calcolata come coda;
 * il LaTeX restituito per ogni espressione è reso con KaTeX accanto al
 * campo. Gli errori 422 `meta.errors` sono mappati sul campo
 * (`mapErrorsToFields`) con `aria-invalid`; quelli non mappabili e il 422
 * per asset del PATCH (`error`) stanno nel box in testa.
 */
interface FunctionEditorProps {
  value: string;
  onChange: (content: string) => void;
  disabled?: boolean;
  className?: string;
  orgId: string;
  courseId: string;
  /** 422 per asset del PATCH, mostrato in testa. */
  error?: string | null;
}

const PREFIX = "courses.lessonsContent.editorUI.function";

type FieldErrors = Record<string, string>;

/** Chiave `loc` del 422 → slot del modulo (gli indici degli intervalli e i
 *  tag delle union Pydantic collassano sul campo che li contiene). */
const SLOT_RULES: Array<[RegExp, (m: RegExpMatchArray) => string]> = [
  [/^kind$/, () => "kind"],
  [/^expressions$/, () => "expressions"],
  [/^expressions\.(\d+)(?:\.expr)?$/, (m) => `expressions.${m[1]}.expr`],
  [/^expressions\.(\d+)\.label$/, (m) => `expressions.${m[1]}.label`],
  [/^variable$/, () => "variable"],
  [/^variables(?:\..*)?$/, () => "variables"],
  [/^domain(?:\..*)?$/, () => "domain"],
  [/^range(?:\..*)?$/, () => "range"],
  [/^show(?:\..*)?$/, () => "show"],
  [/^annotations$/, () => "annotations"],
  [/^annotations\.(\d+)(?:\.kind)?$/, (m) => `annotations.${m[1]}`],
  [
    /^annotations\.(\d+)\.(at|expr_index|against|label)$/,
    (m) => `annotations.${m[1]}.${m[2]}`,
  ],
  [/^annotations\.(\d+)\.between(?:\..*)?$/, (m) => `annotations.${m[1]}.between`],
  [/^parameter(?:\.name)?$/, () => "parameter.name"],
  [/^parameter\.values(?:\..*)?$/, () => "parameter.values"],
  [/^levels(?:\..*)?$/, () => "levels"],
  [/^sampling(?:\.points)?$/, () => "sampling.points"],
];

function fieldSlot(key: string): string | null {
  // Le annotazioni sono una union discriminata: Pydantic inserisce il tag
  // (`annotations.0.tangent.at`) nella `loc`.
  const normalized = key.replace(/^(annotations\.\d+)\.(tangent|area|point)\./, "$1.");
  for (const [re, slot] of SLOT_RULES) {
    const m = normalized.match(re);
    if (m) return slot(m);
  }
  return null;
}

function mapErrorsToFields(entries: ApiErrorEntry[]): {
  fieldErrors: FieldErrors;
  otherErrors: string[];
} {
  const fieldErrors: FieldErrors = {};
  const otherErrors: string[] = [];
  for (const entry of entries) {
    const key = apiErrorFieldKey(entry);
    const slot = key ? fieldSlot(key) : null;
    if (slot) {
      fieldErrors[slot] = fieldErrors[slot] ? `${fieldErrors[slot]}; ${entry.msg}` : entry.msg;
    } else {
      otherErrors.push(key ? `${key}: ${entry.msg}` : entry.msg);
    }
  }
  return { fieldErrors, otherErrors };
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="text-xs text-destructive">{message}</p>;
}

interface NumberFieldProps {
  value: number | null;
  onChange: (value: number) => void;
  disabled?: boolean;
  invalid?: boolean;
  className?: string;
  placeholder?: string;
  ariaLabel?: string;
}

/** Campo numerico con stato testuale locale: permette di digitare `-`,
 *  `1.` o la virgola senza che il valore controllato «rimbalzi». Propaga
 *  solo numeri finiti; si riallinea quando la prop cambia dall'esterno. */
function NumberField({
  value,
  onChange,
  disabled,
  invalid,
  className,
  placeholder,
  ariaLabel,
}: NumberFieldProps) {
  const [text, setText] = useState(value == null ? "" : String(value));
  const lastValue = useRef(value);
  useEffect(() => {
    if (value !== lastValue.current) {
      lastValue.current = value;
      setText(value == null ? "" : String(value));
    }
  }, [value]);
  return (
    <Input
      type="text"
      inputMode="decimal"
      value={text}
      disabled={disabled}
      placeholder={placeholder}
      aria-label={ariaLabel}
      aria-invalid={invalid || undefined}
      className={cn("font-mono text-xs", invalid && "border-destructive", className)}
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        const n = Number(raw.replace(",", "."));
        if (raw.trim() !== "" && Number.isFinite(n)) {
          lastValue.current = n;
          onChange(n);
        }
      }}
    />
  );
}

interface CsvNumbersFieldProps {
  values: number[];
  onChange: (values: number[]) => void;
  max: number;
  disabled?: boolean;
  invalid?: boolean;
  placeholder?: string;
}

function parseCsvNumbers(text: string): number[] {
  return text
    .split(/[,;\s]+/)
    .map((tok) => tok.trim().replace(",", "."))
    .filter((tok) => tok !== "")
    .map(Number)
    .filter((n) => Number.isFinite(n));
}

/** Elenco di numeri separati da virgola, con lo stesso stato testuale
 *  locale di `NumberField`. */
function CsvNumbersField({
  values,
  onChange,
  max,
  disabled,
  invalid,
  placeholder,
}: CsvNumbersFieldProps) {
  const [text, setText] = useState(values.join(", "));
  const lastKey = useRef(JSON.stringify(values));
  useEffect(() => {
    const key = JSON.stringify(values);
    if (key !== lastKey.current) {
      lastKey.current = key;
      setText(values.join(", "));
    }
  }, [values]);
  return (
    <Input
      type="text"
      inputMode="decimal"
      value={text}
      disabled={disabled}
      placeholder={placeholder}
      aria-invalid={invalid || undefined}
      className={cn("font-mono text-xs", invalid && "border-destructive")}
      onChange={(e) => {
        setText(e.target.value);
        const parsed = parseCsvNumbers(e.target.value).slice(0, max);
        lastKey.current = JSON.stringify(parsed);
        onChange(parsed);
      }}
    />
  );
}

/** Formula KaTeX in linea, montata nel DOM tramite ref (`katex.render`). */
function KatexInline({ latex, className }: { latex: string; className?: string }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    katex.render(latex, ref.current, {
      throwOnError: false,
      trust: false,
      displayMode: false,
      strict: "ignore",
    });
  }, [latex]);
  return <span ref={ref} className={className} />;
}

export function FunctionEditor({
  value,
  onChange,
  disabled = false,
  className,
  orgId,
  courseId,
  error,
}: FunctionEditorProps) {
  const { t } = useTranslation();
  const lastEmitted = useRef<string | null>(null);
  const [spec, setSpec] = useState<FunctionFigureSpec>(() => {
    const parsed = parseFunctionContent(value);
    return normalizeFunctionSpec(parsed.spec ?? DEFAULT_FUNCTION_SPEC);
  });

  // `value` è la sorgente di verità del genitore: un asset nuovo (content
  // vuoto) riceve subito la spec di partenza; un cambiamento esterno del
  // content (diverso dall'ultima serializzazione emessa) rialimenta il modulo.
  useEffect(() => {
    if (value === lastEmitted.current) return;
    const parsed = parseFunctionContent(value);
    if (parsed.spec) {
      lastEmitted.current = value;
      setSpec(normalizeFunctionSpec(parsed.spec));
    } else if (!value.trim()) {
      const initial = normalizeFunctionSpec(DEFAULT_FUNCTION_SPEC);
      const serialized = serializeFunctionSpec(initial);
      lastEmitted.current = serialized;
      setSpec(initial);
      onChange(serialized);
    }
  }, [value, onChange]);

  const update = (next: FunctionFigureSpec) => {
    setSpec(next);
    const serialized = serializeFunctionSpec(next);
    lastEmitted.current = serialized;
    onChange(serialized);
  };

  const debounced = useDebouncedValue(spec, 700);
  const wire = useMemo(() => toWireSpec(debounced), [debounced]);
  const wireKey = JSON.stringify(wire);
  const complete = isLocallyComplete(debounced);

  const query = useQuery({
    queryKey: ["function-preview", orgId, courseId, wireKey],
    queryFn: () => coursesApi.lessonAssets.renderFunction(orgId, courseId, wire),
    enabled: complete,
    retry: false,
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  });

  const { fieldErrors, otherErrors } = useMemo(() => {
    if (!query.error) return { fieldErrors: {} as FieldErrors, otherErrors: [] as string[] };
    const body = extractApiError(query.error);
    const entries = apiErrorEntries(body);
    if (entries.length === 0) return { fieldErrors: {} as FieldErrors, otherErrors: [body.message] };
    return mapErrorsToFields(entries);
  }, [query.error]);
  const hasFieldErrors = Object.keys(fieldErrors).length > 0;

  const kind = spec.kind;
  const isFamily = kind === "family";
  const isLevelCurves = kind === "level_curves";
  const singleExpression = isFamily || isLevelCurves;
  const showAnnotations = !isFamily && !isLevelCurves;
  const expressions = spec.expressions;
  const annotations = spec.annotations ?? [];
  const show = spec.show ?? [];
  const latex = query.data?.latex ?? [];
  const approximate = query.data?.computed?.approximate === true;

  const updateExpression = (i: number, patch: { expr?: string; label?: string }) =>
    update({
      ...spec,
      expressions: expressions.map((e, j) => (j === i ? { ...e, ...patch } : e)),
    });

  const updateAnnotation = (i: number, next: FunctionAnnotation) =>
    update({ ...spec, annotations: annotations.map((a, j) => (j === i ? next : a)) });

  const changeAnnotationKind = (i: number, next: AnnotationKind) => {
    const current = annotations[i];
    const [lo, hi] = spec.domain;
    const mid = (lo + hi) / 2;
    const base = { expr_index: current.expr_index ?? 0, label: current.label ?? "" };
    const at = current.kind === "area" ? mid : current.at;
    const replaced: FunctionAnnotation =
      next === "area"
        ? { kind: "area", between: [lo + (hi - lo) / 4, hi - (hi - lo) / 4], against: null, ...base }
        : { kind: next, at, ...base };
    updateAnnotation(i, replaced);
  };

  const expressionLabel = (i: number) =>
    expressions[i]?.label?.trim() || ["f", "g", "h", "k"][i % 4];

  const variableNames = spec.variables ?? [spec.variable ?? "x"];

  const kindLabel = (k: FunctionKind) => t(`${PREFIX}.kinds.${k}`);

  return (
    <div
      className={cn(
        "rounded-md border bg-background",
        disabled && "pointer-events-none opacity-60",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b bg-muted/30 px-2 py-1.5">
        <span className="text-xs font-medium text-muted-foreground">
          {t(`${PREFIX}.title`)}
        </span>
        <Select
          value={kind}
          onValueChange={(k) => update(adaptSpecToKind(spec, k as FunctionKind))}
          disabled={disabled}
        >
          <SelectTrigger
            className={cn("h-7 w-56 text-xs", fieldErrors.kind && "border-destructive")}
            aria-invalid={fieldErrors.kind ? true : undefined}
          >
            <SelectValue placeholder={t(`${PREFIX}.kind`)} />
          </SelectTrigger>
          <SelectContent>
            {FUNCTION_KINDS.map((k) => (
              <SelectItem key={k} value={k}>
                {kindLabel(k)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {(error || otherErrors.length > 0) && (
        <div className="m-2 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <div className="font-semibold">
            {t("courses.lessonsContent.editor.assetRejected")}
          </div>
          {error && <div className="mt-0.5 break-words font-mono">{error}</div>}
          {otherErrors.map((msg, i) => (
            <div key={i} className="mt-0.5 break-words font-mono">
              {msg}
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-3 p-2 md:grid-cols-2">
        <div className="space-y-3">
          {/* Espressioni */}
          <div className="space-y-1.5">
            <Label className="text-xs">{t(`${PREFIX}.expressions`)}</Label>
            <FieldError message={fieldErrors.expressions} />
            {expressions.map((e, i) => {
              const exprKey = `expressions.${i}.expr`;
              const labelKey = `expressions.${i}.label`;
              return (
                <div key={i} className="space-y-1">
                  <div className="flex items-center gap-1.5">
                    <Input
                      value={e.label ?? ""}
                      onChange={(ev) => updateExpression(i, { label: ev.target.value })}
                      disabled={disabled}
                      maxLength={24}
                      placeholder={expressionLabel(i)}
                      aria-label={t(`${PREFIX}.expressionLabel`)}
                      aria-invalid={fieldErrors[labelKey] ? true : undefined}
                      className={cn(
                        "w-16 font-mono text-xs",
                        fieldErrors[labelKey] && "border-destructive",
                      )}
                    />
                    <span className="text-xs text-muted-foreground">
                      ({variableNames.join(", ")}) =
                    </span>
                    <Input
                      value={e.expr}
                      onChange={(ev) => updateExpression(i, { expr: ev.target.value })}
                      disabled={disabled}
                      maxLength={200}
                      spellCheck={false}
                      placeholder="x**2 - 2"
                      aria-label={t(`${PREFIX}.expression`)}
                      aria-invalid={fieldErrors[exprKey] ? true : undefined}
                      className={cn(
                        "min-w-0 flex-1 font-mono text-xs",
                        fieldErrors[exprKey] && "border-destructive",
                      )}
                    />
                    {expressions.length > 1 && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-8 px-2"
                        disabled={disabled}
                        title={t("common.delete")}
                        onClick={() =>
                          update({
                            ...spec,
                            expressions: expressions.filter((_, j) => j !== i),
                            annotations: annotations.filter(
                              (a) => (a.expr_index ?? 0) !== i && (a.kind !== "area" || a.against !== i),
                            ),
                          })
                        }
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    )}
                  </div>
                  <FieldError message={fieldErrors[exprKey]} />
                  <FieldError message={fieldErrors[labelKey]} />
                  {latex[i] && (
                    <div className="px-1 text-sm">
                      <KatexInline latex={latex[i]} />
                    </div>
                  )}
                </div>
              );
            })}
            {!singleExpression && expressions.length < MAX_EXPRESSIONS && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled}
                onClick={() =>
                  update({ ...spec, expressions: [...expressions, { expr: "", label: "" }] })
                }
              >
                <Plus className="size-3.5" />
                {t(`${PREFIX}.addExpression`)}
              </Button>
            )}
            <p className="text-[11px] text-muted-foreground">{t(`${PREFIX}.syntaxHint`)}</p>
          </div>

          {/* Variabili */}
          {isLevelCurves ? (
            <div className="space-y-1.5">
              <Label className="text-xs">{t(`${PREFIX}.variables`)}</Label>
              <div className="flex items-center gap-2">
                {[0, 1].map((idx) => (
                  <Input
                    key={idx}
                    value={spec.variables?.[idx] ?? ""}
                    maxLength={1}
                    disabled={disabled}
                    aria-invalid={fieldErrors.variables ? true : undefined}
                    className={cn(
                      "w-14 font-mono text-xs",
                      fieldErrors.variables && "border-destructive",
                    )}
                    onChange={(ev) => {
                      const next: [string, string] = [
                        spec.variables?.[0] ?? "x",
                        spec.variables?.[1] ?? "y",
                      ];
                      next[idx] = ev.target.value;
                      update({ ...spec, variables: next });
                    }}
                  />
                ))}
              </div>
              <FieldError message={fieldErrors.variables} />
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label className="text-xs">{t(`${PREFIX}.variable`)}</Label>
              <Input
                value={spec.variable ?? "x"}
                maxLength={1}
                disabled={disabled}
                aria-invalid={fieldErrors.variable ? true : undefined}
                className={cn("w-14 font-mono text-xs", fieldErrors.variable && "border-destructive")}
                onChange={(ev) => update({ ...spec, variable: ev.target.value })}
              />
              <FieldError message={fieldErrors.variable} />
            </div>
          )}

          {/* Dominio e intervallo */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="text-xs">{t(`${PREFIX}.domain`)}</Label>
              <div className="flex items-center gap-1.5">
                <NumberField
                  value={spec.domain[0]}
                  invalid={Boolean(fieldErrors.domain)}
                  disabled={disabled}
                  ariaLabel={t(`${PREFIX}.min`)}
                  onChange={(n) => update({ ...spec, domain: [n, spec.domain[1]] })}
                />
                <span className="text-xs text-muted-foreground">–</span>
                <NumberField
                  value={spec.domain[1]}
                  invalid={Boolean(fieldErrors.domain)}
                  disabled={disabled}
                  ariaLabel={t(`${PREFIX}.max`)}
                  onChange={(n) => update({ ...spec, domain: [spec.domain[0], n] })}
                />
              </div>
              <FieldError message={fieldErrors.domain} />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t(`${PREFIX}.range`)}</Label>
              <label className="flex items-center gap-2 text-xs">
                <Checkbox
                  checked={spec.range == null}
                  disabled={disabled}
                  onCheckedChange={(checked) =>
                    update({
                      ...spec,
                      range: checked === true ? null : [spec.domain[0], spec.domain[1]],
                    })
                  }
                />
                {t(`${PREFIX}.rangeAuto`)}
              </label>
              {spec.range && (
                <div className="flex items-center gap-1.5">
                  <NumberField
                    value={spec.range[0]}
                    invalid={Boolean(fieldErrors.range)}
                    disabled={disabled}
                    ariaLabel={t(`${PREFIX}.min`)}
                    onChange={(n) => update({ ...spec, range: [n, spec.range![1]] })}
                  />
                  <span className="text-xs text-muted-foreground">–</span>
                  <NumberField
                    value={spec.range[1]}
                    invalid={Boolean(fieldErrors.range)}
                    disabled={disabled}
                    ariaLabel={t(`${PREFIX}.max`)}
                    onChange={(n) => update({ ...spec, range: [spec.range![0], n] })}
                  />
                </div>
              )}
              <FieldError message={fieldErrors.range} />
            </div>
          </div>

          {/* Analisi da mostrare */}
          {!isLevelCurves && (
            <div className="space-y-1.5">
              <Label className="text-xs">{t(`${PREFIX}.show`)}</Label>
              <div className="grid grid-cols-2 gap-1.5">
                {FUNCTION_SHOW_ITEMS.map((item: FunctionShowItem) => (
                  <label key={item} className="flex items-center gap-2 text-xs">
                    <Checkbox
                      checked={show.includes(item)}
                      disabled={disabled}
                      onCheckedChange={(checked) =>
                        update({
                          ...spec,
                          show:
                            checked === true
                              ? [...show.filter((s) => s !== item), item]
                              : show.filter((s) => s !== item),
                        })
                      }
                    />
                    {t(`${PREFIX}.showItems.${item}`)}
                  </label>
                ))}
              </div>
              <FieldError message={fieldErrors.show} />
            </div>
          )}

          {/* Parametro (famiglia di curve) */}
          {isFamily && (
            <div className="grid gap-3 sm:grid-cols-[6rem_1fr]">
              <div className="space-y-1.5">
                <Label className="text-xs">{t(`${PREFIX}.parameterName`)}</Label>
                <Input
                  value={spec.parameter?.name ?? ""}
                  maxLength={1}
                  disabled={disabled}
                  aria-invalid={fieldErrors["parameter.name"] ? true : undefined}
                  className={cn(
                    "w-14 font-mono text-xs",
                    fieldErrors["parameter.name"] && "border-destructive",
                  )}
                  onChange={(ev) =>
                    update({
                      ...spec,
                      parameter: { name: ev.target.value, values: spec.parameter?.values ?? [] },
                    })
                  }
                />
                <FieldError message={fieldErrors["parameter.name"]} />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">{t(`${PREFIX}.parameterValues`)}</Label>
                <CsvNumbersField
                  values={spec.parameter?.values ?? []}
                  max={MAX_PARAMETER_VALUES}
                  disabled={disabled}
                  invalid={Boolean(fieldErrors["parameter.values"])}
                  placeholder="1, 2, 3"
                  onChange={(values) =>
                    update({
                      ...spec,
                      parameter: { name: spec.parameter?.name ?? "a", values },
                    })
                  }
                />
                <FieldError message={fieldErrors["parameter.values"]} />
              </div>
            </div>
          )}

          {/* Livelli (curve di livello) */}
          {isLevelCurves && (
            <div className="space-y-1.5">
              <Label className="text-xs">{t(`${PREFIX}.levels`)}</Label>
              <CsvNumbersField
                values={Array.isArray(spec.levels) ? spec.levels : spec.levels != null ? [spec.levels] : []}
                max={12}
                disabled={disabled}
                invalid={Boolean(fieldErrors.levels)}
                placeholder="6"
                onChange={(values) =>
                  update({
                    ...spec,
                    levels: values.length === 1 ? Math.round(values[0]) : values.length > 0 ? values : null,
                  })
                }
              />
              <p className="text-[11px] text-muted-foreground">{t(`${PREFIX}.levelsHint`)}</p>
              <FieldError message={fieldErrors.levels} />
            </div>
          )}

          {/* Annotazioni */}
          {showAnnotations && (
            <div className="space-y-1.5">
              <Label className="text-xs">{t(`${PREFIX}.annotations`)}</Label>
              <FieldError message={fieldErrors.annotations} />
              {annotations.map((a, i) => {
                const slot = `annotations.${i}`;
                return (
                  <div key={i} className="space-y-1.5 rounded-md border bg-background p-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Select
                        value={a.kind}
                        onValueChange={(k) => changeAnnotationKind(i, k as AnnotationKind)}
                        disabled={disabled}
                      >
                        <SelectTrigger className="h-7 w-36 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {ANNOTATION_KINDS.map((k) => (
                            <SelectItem key={k} value={k}>
                              {t(`${PREFIX}.annotationKinds.${k}`)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {expressions.length > 1 && (
                        <Select
                          value={String(a.expr_index ?? 0)}
                          onValueChange={(v) => updateAnnotation(i, { ...a, expr_index: Number(v) })}
                          disabled={disabled}
                        >
                          <SelectTrigger className="h-7 w-20 text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {expressions.map((_, j) => (
                              <SelectItem key={j} value={String(j)}>
                                {expressionLabel(j)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                      {a.kind === "area" ? (
                        <>
                          <NumberField
                            value={a.between[0]}
                            invalid={Boolean(fieldErrors[`${slot}.between`])}
                            disabled={disabled}
                            className="w-20"
                            ariaLabel={t(`${PREFIX}.min`)}
                            onChange={(n) => updateAnnotation(i, { ...a, between: [n, a.between[1]] })}
                          />
                          <span className="text-xs text-muted-foreground">–</span>
                          <NumberField
                            value={a.between[1]}
                            invalid={Boolean(fieldErrors[`${slot}.between`])}
                            disabled={disabled}
                            className="w-20"
                            ariaLabel={t(`${PREFIX}.max`)}
                            onChange={(n) => updateAnnotation(i, { ...a, between: [a.between[0], n] })}
                          />
                          {expressions.length > 1 && (
                            <Select
                              value={a.against == null ? "none" : String(a.against)}
                              onValueChange={(v) =>
                                updateAnnotation(i, { ...a, against: v === "none" ? null : Number(v) })
                              }
                              disabled={disabled}
                            >
                              <SelectTrigger className="h-7 w-28 text-xs">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="none">{t(`${PREFIX}.againstAxis`)}</SelectItem>
                                {expressions.map((_, j) =>
                                  j === (a.expr_index ?? 0) ? null : (
                                    <SelectItem key={j} value={String(j)}>
                                      {expressionLabel(j)}
                                    </SelectItem>
                                  ),
                                )}
                              </SelectContent>
                            </Select>
                          )}
                        </>
                      ) : (
                        <NumberField
                          value={a.at}
                          invalid={Boolean(fieldErrors[`${slot}.at`])}
                          disabled={disabled}
                          className="w-24"
                          ariaLabel={t(`${PREFIX}.at`)}
                          placeholder={t(`${PREFIX}.at`)}
                          onChange={(n) => updateAnnotation(i, { ...a, at: n })}
                        />
                      )}
                      <Input
                        value={a.label ?? ""}
                        maxLength={40}
                        disabled={disabled}
                        placeholder={t(`${PREFIX}.annotationLabel`)}
                        aria-invalid={fieldErrors[`${slot}.label`] ? true : undefined}
                        className={cn(
                          "min-w-[6rem] flex-1 text-xs",
                          fieldErrors[`${slot}.label`] && "border-destructive",
                        )}
                        onChange={(ev) => updateAnnotation(i, { ...a, label: ev.target.value })}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-8 px-2"
                        disabled={disabled}
                        title={t("common.delete")}
                        onClick={() =>
                          update({ ...spec, annotations: annotations.filter((_, j) => j !== i) })
                        }
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                    <FieldError message={fieldErrors[slot]} />
                    <FieldError message={fieldErrors[`${slot}.at`]} />
                    <FieldError message={fieldErrors[`${slot}.between`]} />
                    <FieldError message={fieldErrors[`${slot}.expr_index`]} />
                    <FieldError message={fieldErrors[`${slot}.against`]} />
                    <FieldError message={fieldErrors[`${slot}.label`]} />
                  </div>
                );
              })}
              {annotations.length < MAX_ANNOTATIONS && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={disabled}
                  onClick={() =>
                    update({
                      ...spec,
                      annotations: [
                        ...annotations,
                        {
                          kind: "point",
                          at: (spec.domain[0] + spec.domain[1]) / 2,
                          expr_index: 0,
                          label: "",
                        },
                      ],
                    })
                  }
                >
                  <Plus className="size-3.5" />
                  {t(`${PREFIX}.addAnnotation`)}
                </Button>
              )}
            </div>
          )}

          {/* Avanzate */}
          <details className="rounded-md border bg-background px-2 py-1.5">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
              {t(`${PREFIX}.advanced`)}
            </summary>
            <div className="mt-2 space-y-1.5">
              <Label className="text-xs">
                {t(`${PREFIX}.samplingPoints`, { min: MIN_POINTS, max: MAX_POINTS })}
              </Label>
              <NumberField
                value={spec.sampling?.points ?? null}
                invalid={Boolean(fieldErrors["sampling.points"])}
                disabled={disabled}
                className="w-28"
                onChange={(n) => update({ ...spec, sampling: { points: Math.round(n) } })}
              />
              <FieldError message={fieldErrors["sampling.points"]} />
            </div>
          </details>
        </div>

        {/* Anteprima */}
        <div className="space-y-1.5">
          <div className="px-1 text-xs font-medium text-muted-foreground">
            {t(`${PREFIX}.preview`)}
          </div>
          <div className="rounded-md border bg-muted/20 p-1">
            {!complete ? (
              <div className="flex min-h-[8rem] items-center justify-center px-3 py-2 text-center text-xs italic text-muted-foreground">
                {t(`${PREFIX}.previewIncomplete`)}
              </div>
            ) : query.isError ? (
              hasFieldErrors ? (
                <div className="flex min-h-[8rem] items-center justify-center px-3 py-2 text-center text-xs italic text-muted-foreground">
                  {t(`${PREFIX}.previewInvalid`)}
                </div>
              ) : (
                <FigureErrorBox detail={describeApiError(query.error)} />
              )
            ) : !query.data ? (
              <FigureLoading />
            ) : (
              <FigureFrame
                assetId="preview"
                format="function"
                caption=""
                extraCaption={query.data.computed_caption}
                className="my-2"
              >
                <img
                  src={svgDataUri(query.data.svg)}
                  alt=""
                  className={cn(
                    "figure-svg mx-auto block h-auto max-w-full",
                    query.isFetching && "opacity-60",
                  )}
                />
              </FigureFrame>
            )}
          </div>
          {approximate && (
            <p className="px-1 text-[11px] text-muted-foreground">
              {t("courses.figures.approxValues")}
            </p>
          )}
          {query.data && query.data.warnings.length > 0 && (
            <p className="px-1 font-mono text-[11px] text-muted-foreground">
              {query.data.warnings.join("; ")}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default FunctionEditor;
