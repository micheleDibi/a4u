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
// Il bottone trigger include la `label` come prefisso (così il campo è
// auto-esplicativo anche senza Label esterno): "📅 Creato" quando vuoto,
// "📅 Creato: 01/03/26 — 31/03/26" quando popolato. Click apre Popover
// con due input "Da" / "A" + bottoni "Pulisci" / "Applica". Zero
// dipendenze nuove.
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
  className,
}: DateRangeFieldProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRangeValue>(value);

  const hasValue = !!value.from || !!value.to;
  const rangeText = hasValue
    ? `${fmtShort(value.from) || "…"} — ${fmtShort(value.to) || "…"}`
    : null;

  return (
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
          className={
            "h-9 justify-between font-normal " + (className ?? "")
          }
        >
          <span className="flex min-w-0 items-center gap-1.5 truncate">
            <CalendarDays className="size-4 shrink-0 text-muted-foreground" />
            <span className="truncate text-foreground">{label}</span>
            {rangeText && (
              <span className="truncate text-muted-foreground">
                : {rangeText}
              </span>
            )}
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
  );
}
