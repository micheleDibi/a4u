import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { coursesApi, type DocumentFigure } from "@/api/courses";
import { useCourseRef } from "@/contexts/CourseRefContext";
import { cn } from "@/lib/utils";

import { FIGURE_SURFACE, FigureFrame, FigureLoading } from "./FigureFrame";
import { useCourseFigures } from "./useCourseFigures";

/**
 * Figura di fonte (asset `format="source_figure"`, `content` = id della
 * figura) in vista lezione, slide ed editor: punto UNICO del frontend.
 *
 * - l'immagine arriva come Blob dall'endpoint autenticato
 *   `…/document-figures/{id}/image` (mai un URL dello storage);
 * - la riga «Fonte» è `attribution` del catalogo, calcolata dal backend
 *   (`figure_attribution`): qui si mostra così com'è, mai ricomposta;
 * - nella dispensa la riga sta in coda alla didascalia; nelle slide in una
 *   fascia sotto la figura, a sinistra (come `.slide-attribution` del PDF
 *   slide e dei frame video, U2);
 * - figura non risolta, non rendibile o senza riga → segnaposto
 *   `courses.figures.missing` con la didascalia: mai l'immagine senza
 *   fonte.
 *
 * Il catalogo (`["document-figures", org, course]`) è condiviso fra tutte le
 * figure della pagina: una sola richiesta.
 */
export interface SourceFigureProps {
  assetId: string;
  figureId: string;
  caption: string;
  altText?: string;
  number?: number | null;
  variant?: "lesson" | "slide";
  cite?: (text: string) => string;
  className?: string;
  /** Anteprima leggera (editor): immagine ridotta dal backend. */
  preview?: boolean;
}

function useFigureImage(figure: DocumentFigure | undefined, preview: boolean) {
  const courseRef = useCourseRef();
  const query = useQuery({
    queryKey: [
      "document-figure-image",
      courseRef?.orgId,
      courseRef?.courseId,
      figure?.id,
      preview,
    ],
    queryFn: () =>
      coursesApi.documentFigures.image(
        courseRef!.orgId,
        courseRef!.courseId,
        figure!.id,
        preview,
      ),
    enabled: Boolean(courseRef && figure && figure.renderable && figure.attribution),
    staleTime: Infinity,
    gcTime: 10 * 60_000,
    retry: 1,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!query.data) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [query.data]);
  return { url, isError: query.isError, isLoading: query.isLoading };
}

export function SourceFigure({
  assetId,
  figureId,
  caption,
  altText,
  number,
  variant = "lesson",
  cite,
  className,
  preview = false,
}: SourceFigureProps) {
  const { t } = useTranslation();
  const figures = useCourseFigures();
  const figure = useMemo(
    () => figures.data?.find((f) => f.id === figureId.trim()),
    [figures.data, figureId],
  );
  const image = useFigureImage(figure, preview);
  const attribution = figure?.renderable ? figure.attribution.trim() : "";
  const shown = Boolean(figure && attribution && image.url);

  let body;
  if (shown) {
    body = (
      <div className={cn(FIGURE_SURFACE, "flex justify-center")}>
        <img
          src={image.url!}
          alt={altText || caption || ""}
          className={cn(
            "source-figure mx-auto block h-auto w-auto max-w-full",
            variant === "lesson" ? "max-h-[28rem]" : "max-h-[24rem]",
          )}
        />
      </div>
    );
  } else if (figures.isLoading || (figure && attribution && image.isLoading)) {
    body = <FigureLoading />;
  } else {
    body = (
      <div className="flex min-h-[6rem] items-center justify-center rounded border border-dashed bg-muted/20 px-3 py-2 text-xs italic text-muted-foreground">
        {t("courses.figures.missing")}
      </div>
    );
  }

  const frame = (
    <FigureFrame
      assetId={assetId}
      format="source_figure"
      caption={caption}
      altText={altText}
      number={number}
      variant={variant}
      cite={cite}
      attribution={shown && variant === "lesson" ? attribution : undefined}
      className={className}
    >
      {body}
    </FigureFrame>
  );
  if (variant === "slide" && shown) {
    return (
      <div className="source-figure-slide">
        {frame}
        <p className="slide-attribution mt-1 break-words text-left text-[0.7rem] leading-snug text-muted-foreground">
          {attribution}
        </p>
      </div>
    );
  }
  return frame;
}

export default SourceFigure;
