import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type StalenessKind =
  | "structure"
  | "content"
  | "pdf"
  | "slides"
  | "speech"
  | "speechPdf";

export interface StalenessAlertProps {
  /** Tipo di disallineamento downstream da segnalare. */
  kind: StalenessKind;
  /** Callback per la rigenerazione (apre dialog/trigger). */
  onAction?: () => void;
  /** Hide the action button (read-only contexts). */
  hideAction?: boolean;
  /** Classi extra. */
  className?: string;
  /** Variant compatto (riga lezione) o esteso (header modulo). */
  variant?: "inline" | "block";
}

/**
 * Alert che segnala "qualcosa a monte è cambiato dopo l'ultima generazione
 * a valle". Non blocca, suggerisce: l'utente decide se rigenerare.
 *
 * Le label sono in `courses.staleness.{kind}.{label,action}`. La logica di
 * stale-detection è in `lib/staleness.ts`.
 */
export function StalenessAlert({
  kind,
  onAction,
  hideAction,
  className,
  variant = "inline",
}: StalenessAlertProps) {
  const { t } = useTranslation();
  const label = t(`courses.staleness.${kind}.label`);
  const action = t(`courses.staleness.${kind}.action`);

  if (variant === "inline") {
    return (
      <div className={cn("flex items-center gap-2 flex-wrap", className)}>
        <Badge variant="warning" className="gap-1 text-[11px]">
          <AlertTriangle className="size-3" />
          {label}
        </Badge>
        {!hideAction && onAction && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            onClick={onAction}
          >
            {action}
          </Button>
        )}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-amber-900 dark:border-amber-700/50 dark:bg-amber-500/10 dark:text-amber-200",
        className,
      )}
    >
      <AlertTriangle className="size-4 mt-0.5 shrink-0" />
      <div className="flex-1 text-sm">{label}</div>
      {!hideAction && onAction && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs border-amber-400 bg-white hover:bg-amber-100 dark:bg-transparent dark:hover:bg-amber-500/20"
          onClick={onAction}
        >
          {action}
        </Button>
      )}
    </div>
  );
}
