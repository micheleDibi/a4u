import { Suspense, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { cn } from "@/lib/utils";

import { FigureLoading } from "./FigureFrame";

/**
 * Base degli editor «sorgente + anteprima» dei formati testuali
 * (`VegaLiteEditor`, `DotEditor`), sul modello di `MermaidEditor`: header
 * con titolo e Select dei template, griglia sorgente/anteprima, anteprima
 * con debounce (`useDebouncedValue`), stato vuoto localizzato.
 *
 * Errori: `serverError` (il 422 per asset del PATCH) è mostrato in testa
 * con lo stesso box di `LatexEditor`; l'errore del renderer client è
 * mostrato dal componente di anteprima (`FigureErrorBox`) al posto della
 * figura, quindi sotto l'intestazione «Anteprima».
 */
export interface SourceTemplate {
  id: string;
  labelKey: string;
  /**
   * Chiave i18n della FAMIGLIA D'USO (confronto, distribuzione, andamento…):
   * il menu raggruppa i template consecutivi che la condividono, così il
   * docente cerca per uso e non per nome. I template senza `groupKey`
   * restano in un gruppo senza intestazione, nell'ordine di dichiarazione.
   */
  groupKey?: string;
  code: string;
}

interface TemplateGroup {
  key: string;
  items: SourceTemplate[];
}

/** Gruppi consecutivi per `groupKey`, nell'ordine di dichiarazione. */
function groupTemplates(templates: readonly SourceTemplate[]): TemplateGroup[] {
  const out: TemplateGroup[] = [];
  for (const tpl of templates) {
    const key = tpl.groupKey ?? "";
    const last = out.at(-1);
    if (last && last.key === key) last.items.push(tpl);
    else out.push({ key, items: [tpl] });
  }
  return out;
}

export interface TemplateSelectProps {
  templates: readonly SourceTemplate[];
  /** Testo già tradotto del segnaposto («Inserisci template…»). */
  placeholder: string;
  onPick: (id: string) => void;
  disabled?: boolean;
}

/**
 * Select dei template raggruppato per famiglia d'uso, condiviso da
 * `FigureSourceEditor` (Vega-Lite, DOT) e da `MermaidEditor`.
 */
export function TemplateSelect({
  templates,
  placeholder,
  onPick,
  disabled = false,
}: TemplateSelectProps) {
  const { t } = useTranslation();
  return (
    <Select onValueChange={onPick} disabled={disabled}>
      <SelectTrigger className="h-7 w-56 text-xs">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {groupTemplates(templates).map((group) => (
          <SelectGroup key={group.key || "ungrouped"}>
            {group.key ? (
              <SelectLabel className="text-xs font-semibold text-muted-foreground">
                {t(group.key)}
              </SelectLabel>
            ) : null}
            {group.items.map((tpl) => (
              <SelectItem key={tpl.id} value={tpl.id}>
                {t(tpl.labelKey)}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}

export interface FigureSourceEditorProps {
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  className?: string;
  rows?: number;
  /** Messaggio del 422 per asset (validazione del PATCH), mostrato in testa. */
  serverError?: string | null;
  /** Prefisso delle chiavi i18n (`courses.lessonsContent.editorUI.vegalite`). */
  i18nPrefix: string;
  templates: readonly SourceTemplate[];
  placeholder: string;
  /** Renderer dell'anteprima per il sorgente (già con debounce). */
  renderPreview: (source: string) => ReactNode;
  debounceMs?: number;
}

export function FigureSourceEditor({
  value,
  onChange,
  disabled = false,
  className,
  rows = 10,
  serverError,
  i18nPrefix,
  templates,
  placeholder,
  renderPreview,
  debounceMs = 500,
}: FigureSourceEditorProps) {
  const { t } = useTranslation();
  const debounced = useDebouncedValue(value, debounceMs);

  const applyTemplate = (id: string) => {
    const tpl = templates.find((entry) => entry.id === id);
    if (tpl) onChange(tpl.code);
  };

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
          {t(`${i18nPrefix}.title`)}
        </span>
        <TemplateSelect
          templates={templates}
          placeholder={t(`${i18nPrefix}.insertTemplate`)}
          onPick={applyTemplate}
          disabled={disabled}
        />
      </div>
      {serverError && (
        <div className="m-2 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <div className="font-semibold">
            {t("courses.lessonsContent.editor.assetRejected")}
          </div>
          <div className="mt-0.5 break-words font-mono">{serverError}</div>
        </div>
      )}
      <div className="grid gap-2 p-2 md:grid-cols-2">
        <div className="space-y-1.5">
          <div className="px-1 text-xs font-medium text-muted-foreground">
            {t(`${i18nPrefix}.code`)}
          </div>
          <Textarea
            rows={rows}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            disabled={disabled}
            className="font-mono text-xs"
            placeholder={placeholder}
            spellCheck={false}
          />
        </div>
        <div className="space-y-1.5">
          <div className="px-1 text-xs font-medium text-muted-foreground">
            {t(`${i18nPrefix}.preview`)}
          </div>
          {debounced.trim() ? (
            <div className="rounded-md border bg-muted/20 p-1">
              <Suspense fallback={<FigureLoading />}>
                {renderPreview(debounced)}
              </Suspense>
            </div>
          ) : (
            <div className="flex min-h-[8rem] items-center justify-center rounded-md border border-dashed bg-muted/20 px-3 py-2 text-xs italic text-muted-foreground">
              {t(`${i18nPrefix}.previewEmpty`)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default FigureSourceEditor;
