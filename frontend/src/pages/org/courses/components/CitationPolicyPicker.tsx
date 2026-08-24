import { useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  BookOpenCheck,
  Check,
  ChevronDown,
  CircleOff,
  EyeOff,
  Loader2,
  type LucideIcon,
} from "lucide-react";

import type { CitationPolicy } from "@/api/courses";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/**
 * Selettore della politica di citazione ("visibilità delle fonti").
 *
 * Due forme, stessa semantica e stessi toni:
 * - `CitationPolicyCards`: radio-card con icona tonale, titolo e
 *   descrizione — per l'area di upload (disclosure progressiva: la
 *   spiegazione è nella card, niente testo di aiuto separato).
 * - `CitationPolicyChip`: pill compatta che apre un popover con le 3
 *   opzioni; opzionalmente con conferma inline (nota + Applica) per i
 *   documenti già caricati, dove il cambio ha effetti (scrub +
 *   rielaborazione del riassunto).
 *
 * Toni: brand (citabile) / ambra (riservata) / neutro (escluso) — mai
 * solo colore: icona + etichetta sempre presenti.
 */

const CITATION_POLICIES: CitationPolicy[] = [
  "citable",
  "content_only",
  "excluded",
];

interface PolicyMeta {
  Icon: LucideIcon;
  labelKey: string;
  descKey: string;
  /** Tile dell'icona (sfondo tinta + colore icona). */
  tile: string;
  /** Ring/sfondo della card selezionata. */
  selected: string;
  /** Pill (trigger del popover). */
  pill: string;
}

const META: Record<CitationPolicy, PolicyMeta> = {
  citable: {
    Icon: BookOpenCheck,
    labelKey: "courses.docs.citationPolicy.citable",
    descKey: "courses.docs.citationPolicy.citableDesc",
    tile: "bg-brand/12 text-brand",
    selected: "ring-brand bg-brand/5",
    pill: "border-brand/25 bg-brand/10 text-brand hover:bg-brand/15",
  },
  content_only: {
    Icon: EyeOff,
    labelKey: "courses.docs.citationPolicy.contentOnly",
    descKey: "courses.docs.citationPolicy.contentOnlyDesc",
    tile: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    selected: "ring-amber-500 bg-amber-50 dark:bg-amber-500/10",
    pill:
      "border-amber-300/60 bg-amber-50 text-amber-800 hover:bg-amber-100 " +
      "dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300 dark:hover:bg-amber-500/20",
  },
  excluded: {
    Icon: CircleOff,
    labelKey: "courses.docs.citationPolicy.excluded",
    descKey: "courses.docs.citationPolicy.excludedDesc",
    tile: "bg-muted text-muted-foreground",
    selected: "ring-foreground/40 bg-muted/60",
    pill: "border-border bg-muted text-muted-foreground hover:bg-muted/80",
  },
};

// ---------------------------------------------------------------------
// Radio-card (area upload)
// ---------------------------------------------------------------------

interface CardsProps {
  value: CitationPolicy;
  onChange: (next: CitationPolicy) => void;
  disabled?: boolean;
  className?: string;
}

export function CitationPolicyCards({
  value,
  onChange,
  disabled,
  className,
}: CardsProps) {
  const { t } = useTranslation();

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    const idx = CITATION_POLICIES.indexOf(value);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      onChange(CITATION_POLICIES[(idx + 1) % CITATION_POLICIES.length]);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      onChange(
        CITATION_POLICIES[
          (idx - 1 + CITATION_POLICIES.length) % CITATION_POLICIES.length
        ],
      );
    }
  };

  return (
    <div className={cn("space-y-2", className)}>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {t("courses.docs.citationPolicy.pickerTitle")}
      </p>
      <div
        role="radiogroup"
        aria-label={t("courses.docs.citationPolicy.label")}
        onKeyDown={onKeyDown}
        className="grid grid-cols-1 gap-2 sm:grid-cols-3"
      >
        {CITATION_POLICIES.map((policy) => {
          const meta = META[policy];
          const checked = policy === value;
          return (
            <button
              key={policy}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked ? 0 : -1}
              disabled={disabled}
              onClick={() => onChange(policy)}
              className={cn(
                "group relative flex min-h-[44px] items-start gap-3 rounded-lg border bg-card p-3 text-left",
                "transition-all duration-200 ease-out",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                checked
                  ? cn("border-transparent ring-2", meta.selected)
                  : "hover:border-foreground/25 hover:bg-muted/40",
                disabled && "cursor-not-allowed opacity-60",
              )}
            >
              <span
                className={cn(
                  "flex size-9 shrink-0 items-center justify-center rounded-md",
                  meta.tile,
                )}
              >
                <meta.Icon className="size-4.5" strokeWidth={1.75} />
              </span>
              <span className="min-w-0 flex-1 pr-5">
                <span className="block text-sm font-medium leading-tight">
                  {t(meta.labelKey)}
                </span>
                <span className="mt-1 block text-xs leading-snug text-muted-foreground">
                  {t(meta.descKey)}
                </span>
              </span>
              <span
                aria-hidden
                className={cn(
                  "absolute right-2 top-2 flex size-5 items-center justify-center rounded-full bg-foreground text-background",
                  "transition-all duration-200 ease-out",
                  checked ? "scale-100 opacity-100" : "scale-50 opacity-0",
                )}
              >
                <Check className="size-3" strokeWidth={3} />
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------
// Pill + popover (riga documento, pannello import paper)
// ---------------------------------------------------------------------

interface ChipProps {
  value: CitationPolicy;
  onChange: (next: CitationPolicy) => void;
  /** Sola lettura: pill non interattiva (nessun popover). */
  readOnly?: boolean;
  /** Mostra la nota di conferma + Applica prima di applicare (documenti
   *  già caricati). Senza: applica subito (scelta per un batch futuro). */
  confirm?: boolean;
  /** Nome del documento per la nota di conferma. */
  docName?: string;
  /** Mutazione in corso: pill con spinner e popover disabilitato. */
  pending?: boolean;
  /** Prefisso della pill (es. "Fonti:"), utile in barre compatte. */
  prefix?: string;
  className?: string;
}

export function CitationPolicyChip({
  value,
  onChange,
  readOnly,
  confirm,
  docName,
  pending,
  prefix,
  className,
}: ChipProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<CitationPolicy | null>(null);
  const current = META[value];

  const close = () => {
    setOpen(false);
    setDraft(null);
  };

  const pick = (policy: CitationPolicy) => {
    if (policy === value) {
      close();
      return;
    }
    if (confirm) {
      setDraft(policy);
      return;
    }
    onChange(policy);
    close();
  };

  const pill = (
    <span
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium",
        "transition-colors duration-150",
        current.pill,
        !readOnly && "cursor-pointer",
        className,
      )}
    >
      {pending ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <current.Icon className="size-3.5" strokeWidth={2} />
      )}
      {prefix && <span className="opacity-70">{prefix}</span>}
      <span className="truncate">{t(current.labelKey)}</span>
      {!readOnly && <ChevronDown className="size-3 opacity-60" />}
    </span>
  );

  if (readOnly) {
    return pill;
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => (next ? setOpen(true) : close())}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={pending}
          aria-label={t("courses.docs.citationPolicy.chipAria", {
            label: t(current.labelKey),
          })}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex min-h-[44px] items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-wait"
        >
          {pill}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-80 p-1.5"
        onClick={(e) => e.stopPropagation()}
      >
        <div role="radiogroup" aria-label={t("courses.docs.citationPolicy.label")}>
          {CITATION_POLICIES.map((policy) => {
            const meta = META[policy];
            const checked = (draft ?? value) === policy;
            return (
              <button
                key={policy}
                type="button"
                role="radio"
                aria-checked={checked}
                onClick={() => pick(policy)}
                className={cn(
                  "flex w-full items-start gap-3 rounded-md p-2 text-left",
                  "transition-colors duration-150 hover:bg-muted focus-visible:outline-none focus-visible:bg-muted",
                  checked && "bg-muted/70",
                )}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md",
                    meta.tile,
                  )}
                >
                  <meta.Icon className="size-4" strokeWidth={1.75} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium leading-tight">
                    {t(meta.labelKey)}
                  </span>
                  <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">
                    {t(meta.descKey)}
                  </span>
                </span>
                <Check
                  className={cn(
                    "mt-1 size-4 shrink-0 transition-opacity duration-150",
                    policy === value ? "opacity-100" : "opacity-0",
                  )}
                />
              </button>
            );
          })}
        </div>

        {confirm && draft && (
          <div className="mt-1.5 space-y-2 rounded-md border border-border bg-muted/40 p-2.5 animate-in fade-in-0 slide-in-from-top-1 duration-200">
            <p className="text-xs leading-snug text-muted-foreground">
              {t(
                draft === "content_only"
                  ? "courses.docs.citationPolicy.confirmContentOnly"
                  : "courses.docs.citationPolicy.confirmGeneric",
                { name: docName ?? "" },
              )}
            </p>
            <div className="flex justify-end gap-1.5">
              <Button variant="ghost" size="sm" onClick={close}>
                {t("common.cancel")}
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  onChange(draft);
                  close();
                }}
              >
                {t("common.apply")}
              </Button>
            </div>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
