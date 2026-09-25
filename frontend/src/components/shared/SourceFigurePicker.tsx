import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { coursesApi, type DocumentFigure } from "@/api/courses";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useCourseRef } from "@/contexts/CourseRefContext";

import { FigureLoading } from "./FigureFrame";
import { useCourseFigures } from "./useCourseFigures";

/**
 * Catalogo delle figure di fonte del corso per l'editor della dispensa:
 * solo le figure PROPONIBILI adesso (predicato select del backend: politica
 * del documento, esclusione, licenza effettiva, qualità). La riga «Fonte»
 * mostrata è quella del backend; l'asset creato porta solo l'id della
 * figura, e il PATCH la ricontrolla (`source_figure_not_available`).
 */
export interface SourceFigurePickerProps {
  open: boolean;
  onClose: () => void;
  onPick: (figure: DocumentFigure) => void;
}

function Thumbnail({ figure }: { figure: DocumentFigure }) {
  const courseRef = useCourseRef();
  const query = useQuery({
    queryKey: [
      "document-figure-image",
      courseRef?.orgId,
      courseRef?.courseId,
      figure.id,
      true,
      figure.image_rev,
    ],
    queryFn: () =>
      coursesApi.documentFigures.image(
        courseRef!.orgId,
        courseRef!.courseId,
        figure.id,
        true,
        figure.image_rev,
      ),
    enabled: Boolean(courseRef),
    staleTime: Infinity,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!query.data) return;
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [query.data]);
  if (!url) return <FigureLoading className="h-24" />;
  return (
    <img
      src={url}
      alt={figure.description || ""}
      className="mx-auto block max-h-32 w-auto max-w-full rounded bg-white object-contain p-1"
    />
  );
}

export function SourceFigurePicker({ open, onClose, onPick }: SourceFigurePickerProps) {
  const { t } = useTranslation();
  const figures = useCourseFigures();
  const selectable = (figures.data ?? []).filter((f) => f.selectable);
  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("courses.sourceFigures.picker.title")}</DialogTitle>
          <DialogDescription>{t("courses.sourceFigures.picker.description")}</DialogDescription>
        </DialogHeader>
        {figures.isLoading ? (
          <FigureLoading />
        ) : selectable.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("courses.sourceFigures.picker.empty")}</p>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2">
            {selectable.map((figure) => (
              <li key={figure.id} className="space-y-2 rounded-md border bg-muted/20 p-2">
                <Thumbnail figure={figure} />
                <div className="text-xs">
                  {figure.source_caption || figure.description}
                </div>
                <div className="text-[0.7rem] text-muted-foreground">{figure.attribution}</div>
                <div className="flex justify-end">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      onPick(figure);
                      onClose();
                    }}
                  >
                    {t("courses.sourceFigures.picker.add")}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default SourceFigurePicker;
