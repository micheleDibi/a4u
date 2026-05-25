import { useState } from "react";
import { CalendarDays, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

// ---------------------------------------------------------------------------
// Date range field — Popover + 2 native `<input type="date">`.
//
// Trigger button mostra il range corrente ("Da DD/MM/YY a DD/MM/YY") o un
// placeholder. Click apre Popover con due input "Da" / "A" + bottoni
// "Pulisci" / "OK". Zero dipendenze nuove: il calendario nativo del
// browser appare al click sull'input (più gentile su mobile).
// ---------------------------------------------------------------------------

export interface DateRangeValue {
  /** ISO date string `YYYY-MM-DD` (lower bound, inclusive). */
  from?: string;
  /** ISO date string `YYYY-MM-DD` (upper bound, inclusive). */
  to?: string;
}

interface DateRangeFieldProps {
  label: string;
  value: DateRangeValue;
  onChange: (next: DateRangeValue) => void;
  /** Placeholder mostrato sul trigger quando il range è vuoto. */
  placeholder?: string;
  className?: string;
}

function fmtShort(iso: string | undefined): string {
  if (!iso) return "";
  // iso = "YYYY-MM-DD"
  const [y, m, d] = iso.split("-");
  if (!y || !m || !d) return iso;
  return `${d}/${m}/${y.slice(2)}`;
}

export function DateRangeField({
  label,
  value,
  onChange,
  placeholder,
  className,
}: DateRangeFieldProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRangeValue>(value);

  // Tieni il draft allineato al value quando arriva da fuori (es. reset filtri).
  // Usa useEffect via setState; senza, il reset esterno non si propaga.

  const hasValue = !!value.from || !!value.to;

  const triggerLabel = hasValue
    ? `${fmtShort(value.from) || "…"} — ${fmtShort(value.to) || "…"}`
    : placeholder ?? t("dateRangeField.placeholder");

  return (
    <div className={className}>
      <Label className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      <Popover
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (o) setDraft(value);
        }}
      >
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            className="mt-1 h-9 w-full justify-between font-normal"
          >
            <span className="flex items-center gap-2 truncate">
              <CalendarDays className="size-4 shrink-0 text-muted-foreground" />
              <span
                className={
                  hasValue
                    ? "truncate text-foreground"
                    : "truncate text-muted-foreground"
                }
              >
                {triggerLabel}
              </span>
            </span>
            {hasValue && (
              <span
                role="button"
                tabIndex={0}
                aria-label={t("dateRangeField.clear")}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onChange({});
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    e.stopPropagation();
                    onChange({});
                  }
                }}
                className="ms-2 grid size-5 shrink-0 cursor-pointer place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="size-3.5" />
              </span>
            )}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-72 p-3">
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label
                htmlFor="dr-from"
                className="text-xs text-muted-foreground"
              >
                {t("dateRangeField.from")}
              </Label>
              <Input
                id="dr-from"
                type="date"
                value={draft.from ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, from: e.target.value || undefined }))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label
                htmlFor="dr-to"
                className="text-xs text-muted-foreground"
              >
                {t("dateRangeField.to")}
              </Label>
              <Input
                id="dr-to"
                type="date"
                value={draft.to ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, to: e.target.value || undefined }))
                }
              />
            </div>
            <div className="flex gap-2 pt-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="flex-1"
                onClick={() => {
                  setDraft({});
                  onChange({});
                  setOpen(false);
                }}
              >
                {t("dateRangeField.clear")}
              </Button>
              <Button
                type="button"
                size="sm"
                className="flex-1"
                onClick={() => {
                  onChange(draft);
                  setOpen(false);
                }}
              >
                {t("common.apply")}
              </Button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
