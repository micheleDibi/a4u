import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { coursesApi } from "@/api/courses";
import { useCourseRef } from "@/contexts/CourseRefContext";
import { describeApiError } from "@/lib/errors";
import { svgDataUri } from "@/lib/figureFormats";
import { cn } from "@/lib/utils";

import {
  FIGURE_SURFACE,
  FigureErrorBox,
  FigureFrame,
  FigureLoading,
} from "./FigureFrame";

/**
 * Figura `tikz` (WP6) in vista lezione e slide: l'SVG lo compila il
 * backend (XeLaTeX nella sandbox) con `POST /lesson-assets/tikz-view`, che
 * rende SOLO un sorgente già salvato come asset `tikz` di una lezione del
 * corso (COURSE_VIEW: chi ha la sola vista non fa compilare sorgenti
 * arbitrari). Un sorgente non ancora salvato risponde 404: l'anteprima
 * delle modifiche è quella di `TikzEditor`.
 *
 * L'SVG arriva in un `<img src="data:svg">`, così gli id dei glifi di
 * pdftocairo non collidono fra figure. Cache react-query su
 * `[orgId, courseId, assetId, content]` con `staleTime: Infinity`, come
 * `FunctionFigure`; senza `CourseRefContext` la figura mostra
 * `courses.figures.missing`.
 */
export interface TikzFigureProps {
  assetId: string;
  content: string;
  caption: string;
  altText?: string;
  number?: number | null;
  variant?: "lesson" | "slide";
  /** Rimando testuale degli asset per la didascalia (vedi `FigureFrame`). */
  cite?: (text: string) => string;
  className?: string;
}

export function TikzFigure({
  assetId,
  content,
  caption,
  altText,
  number,
  variant = "lesson",
  cite,
  className,
}: TikzFigureProps) {
  const { t } = useTranslation();
  const courseRef = useCourseRef();

  const query = useQuery({
    queryKey: ["tikz-figure", courseRef?.orgId, courseRef?.courseId, assetId, content],
    queryFn: () =>
      coursesApi.lessonAssets.tikzView(
        courseRef!.orgId,
        courseRef!.courseId,
        assetId,
        content,
      ),
    enabled: Boolean(courseRef && content.trim()),
    staleTime: Infinity,
    gcTime: 30 * 60_000,
    retry: 1,
  });

  let body: ReactNode;
  if (!courseRef) {
    body = (
      <div className="flex min-h-[6rem] items-center justify-center rounded border border-dashed bg-muted/20 px-3 py-2 text-xs italic text-muted-foreground">
        {t("courses.figures.missing")}
      </div>
    );
  } else if (query.isError) {
    body = <FigureErrorBox detail={describeApiError(query.error)} source={content} />;
  } else if (!query.data) {
    body = <FigureLoading />;
  } else {
    body = (
      <div className={cn(FIGURE_SURFACE, "flex justify-center")}>
        <img
          src={svgDataUri(query.data.svg)}
          alt={altText || caption || ""}
          className={cn(
            "figure-svg mx-auto block h-auto max-w-full",
            variant === "lesson" ? "max-h-[28rem]" : "max-h-[24rem]",
          )}
        />
      </div>
    );
  }

  return (
    <FigureFrame
      assetId={assetId}
      format="tikz"
      caption={caption}
      altText={altText}
      number={number}
      variant={variant}
      cite={cite}
      className={className}
    >
      {body}
    </FigureFrame>
  );
}

export default TikzFigure;
