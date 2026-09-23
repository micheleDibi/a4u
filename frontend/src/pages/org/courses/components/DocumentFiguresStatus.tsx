import { useTranslation } from "react-i18next";
import { Images, Loader2, TriangleAlert } from "lucide-react";
import type { CourseDocumentOut } from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { canRequestFigures } from "./documentFigures";

interface Props {
  doc: CourseDocumentOut;
  /** Mostra il bottone «Estrai figure» (permesso course:generate). */
  canExtract: boolean;
  pending: boolean;
  onExtract: () => void;
}

export function DocumentFiguresStatus({ doc, canExtract, pending, onExtract }: Props) {
  const { t } = useTranslation();
  const status = doc.figures_status;
  const showButton = canExtract && canRequestFigures(doc);

  if (status === null) {
    return showButton ? (
      <Button
        variant="ghost"
        size="sm"
        className="h-7 gap-1 px-2 text-xs"
        disabled={pending}
        onClick={(e) => {
          e.stopPropagation();
          onExtract();
        }}
      >
        <Images className="size-3.5" />
        {t("courses.docs.figures.extract")}
      </Button>
    ) : null;
  }

  const progress = doc.figures_progress ?? {};
  let label: string;
  if (status === "processing" && progress.stage === "describing") {
    label = t("courses.docs.figures.status.describing", {
      done: progress.candidates_done ?? 0,
      total: progress.candidates_total ?? 0,
    });
  } else if (status === "processing" && (doc.figures_pages_total ?? 0) > 0) {
    label = t("courses.docs.figures.status.progress", {
      done: doc.figures_pages_done ?? 0,
      total: doc.figures_pages_total,
    });
  } else if (status === "ready") {
    label = t("courses.docs.figures.status.ready", { count: doc.figures_count ?? 0 });
  } else {
    label = t(`courses.docs.figures.status.${status}`);
  }

  const active = status === "pending" || status === "processing";
  const partial =
    status === "ready" &&
    (doc.figures_coverage === "partial" || doc.figures_error_code === "crashed_repeatedly");
  const errorText = doc.figures_error_code
    ? t(`courses.docs.figures.errors.${doc.figures_error_code}`, {
        defaultValue: doc.figures_error_code,
      })
    : null;
  const tooltip = partial ? t("courses.docs.figures.coveragePartial") : errorText;

  return (
    <span className="inline-flex items-center gap-1.5">
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex items-center gap-1.5">
              {active && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
              {(status === "failed" || partial) && (
                <TriangleAlert
                  className={status === "failed" ? "size-3.5 text-destructive" : "size-3.5 text-amber-600"}
                />
              )}
              <Badge
                variant={
                  status === "ready" ? "brand" : status === "failed" ? "destructive" : "muted"
                }
                className="shrink-0"
              >
                <Images className="mr-1 size-3" />
                {label}
              </Badge>
            </span>
          </TooltipTrigger>
          {tooltip && <TooltipContent className="max-w-md">{tooltip}</TooltipContent>}
        </Tooltip>
      </TooltipProvider>
      {showButton && status === "failed" && (
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          disabled={pending}
          onClick={(e) => {
            e.stopPropagation();
            onExtract();
          }}
        >
          {t("courses.docs.figures.extract")}
        </Button>
      )}
    </span>
  );
}
