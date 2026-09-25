import { useTranslation } from "react-i18next";

import type { FigureResolution } from "@/api/courses";
import { Badge } from "@/components/ui/badge";

import { useCourseFigures } from "./useCourseFigures";

/**
 * Risoluzione effettiva di una figura di fonte (doc 18 §22): nessun badge
 * per le classi `good` e `acceptable`; «Bassa risoluzione» (avviso) per
 * `low`, usata solo senza alternative migliori; «Risoluzione insufficiente»
 * per `unusable`, che non si propone più (una figura già collocata resta ed
 * esce a 100 ppi). Il tooltip riporta misura e ppi di stampa calcolati dal
 * backend con la stessa regola del PDF: il frontend non li ricalcola.
 */
export function SourceFigureResolutionBadge({
  resolution,
  className,
}: {
  resolution: FigureResolution | null | undefined;
  className?: string;
}) {
  const { t } = useTranslation();
  if (!resolution || (resolution.class !== "low" && resolution.class !== "unusable")) {
    return null;
  }
  const detail = t("courses.sourceFigures.resolution.detail", {
    width: Math.round(resolution.print_width_mm),
    ppi: resolution.print_ppi,
  });
  return (
    <Badge
      variant={resolution.class === "low" ? "warning" : "destructive"}
      title={detail}
      aria-label={detail}
      className={className}
    >
      {resolution.class === "low"
        ? t("courses.sourceFigures.resolution.low")
        : t("courses.sourceFigures.resolution.unusable")}
    </Badge>
  );
}

/** Lo stesso badge per una figura già collocata, cercata nel catalogo
 *  del corso (richiede `CourseRefContext`). */
export function PlacedFigureResolutionBadge({ figureId }: { figureId: string }) {
  const figures = useCourseFigures();
  const figure = figures.data?.find((f) => f.id === figureId.trim());
  return <SourceFigureResolutionBadge resolution={figure?.resolution} />;
}
