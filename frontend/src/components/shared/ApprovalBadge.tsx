import { CheckCircle2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type ApprovalLevel =
  | "architecture"
  | "module"
  | "lessonContent"
  | "lessonSlides";

export interface ApprovalBadgeProps {
  /** Livello del workflow a cui si riferisce l'approvazione. */
  level: ApprovalLevel;
  /** ISO timestamp dell'approvazione (o null) — usato nel tooltip. */
  approvedAt?: string | null;
  /** Classi extra opzionali. */
  className?: string;
}

/**
 * Badge uniforme che indica "approvato" cross-fase. Stesso visual per
 * Architettura corso, Modulo (struttura lezioni), e Contenuto lezione,
 * così l'utente ha un'unica unità di significato a colpo d'occhio.
 *
 * NB: il bottone "Approva" (CTA per `status === 'ready'`) resta inline
 * nei rispettivi view perché ha logica di mutation specifica per fase;
 * questo componente rende solo l'ESITO. Per i label CTA usa le chiavi
 * `courses.approval.{level}.approve`.
 */
export function ApprovalBadge({
  level,
  approvedAt,
  className,
}: ApprovalBadgeProps) {
  const { t } = useTranslation();
  const tooltip =
    approvedAt && Number.isFinite(new Date(approvedAt).getTime())
      ? t("courses.approval.tooltipApprovedAt", {
          time: new Date(approvedAt).toLocaleString(),
        })
      : undefined;
  return (
    <Badge
      variant="default"
      className={cn("gap-1 text-[11px]", className)}
      title={tooltip}
    >
      <CheckCircle2 className="size-3" />
      {t(`courses.approval.${level}.approved`)}
    </Badge>
  );
}
