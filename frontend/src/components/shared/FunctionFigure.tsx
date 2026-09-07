import { useQuery } from "@tanstack/react-query";
import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { coursesApi } from "@/api/courses";
import { useCourseRef } from "@/contexts/CourseRefContext";
import { describeApiError } from "@/lib/errors";
import { describeFigureParseError, svgDataUri } from "@/lib/figureFormats";
import { parseFunctionContent } from "@/lib/functionSpec";

import { FigureErrorBox, FigureFrame, FigureLoading } from "./FigureFrame";

/**
 * Figura `function` (D9) in vista lezione e slide: la spec JSON è resa dal
 * backend (`POST /lesson-assets/render-function`, numerico + sympy +
 * matplotlib) e l'SVG normalizzato arriva in un `<img src="data:svg">`;
 * la didascalia calcolata (`computed_caption`, mai persistita) diventa la
 * coda della `FigureFrame`, per questo la cornice è disegnata qui.
 *
 * react-query hasha `[orgId, courseId, assetId, content]` con `staleTime:
 * Infinity`: è la «cache in memoria per asset_id + hash» della specifica.
 * `orgId`/`courseId` arrivano dal `CourseRefContext` dei container (A21);
 * senza provider la figura mostra `courses.figures.missing`.
 */
export interface FunctionFigureProps {
  assetId: string;
  content: string;
  caption: string;
  altText?: string;
  number?: number | null;
  variant?: "lesson" | "slide";
  className?: string;
}

export function FunctionFigure({
  assetId,
  content,
  caption,
  altText,
  number,
  variant = "lesson",
  className,
}: FunctionFigureProps) {
  const { t } = useTranslation();
  const courseRef = useCourseRef();
  const parsed = useMemo(() => parseFunctionContent(content), [content]);
  const spec = parsed.spec;

  const query = useQuery({
    queryKey: [
      "function-figure",
      courseRef?.orgId,
      courseRef?.courseId,
      assetId,
      content,
    ],
    queryFn: () =>
      coursesApi.lessonAssets.renderFunction(
        courseRef!.orgId,
        courseRef!.courseId,
        spec!,
      ),
    enabled: Boolean(courseRef && spec),
    staleTime: Infinity,
    gcTime: 30 * 60_000,
    retry: 1,
  });

  let body: ReactNode;
  let extraCaption: string | undefined;
  if (!courseRef) {
    body = (
      <div className="flex min-h-[6rem] items-center justify-center rounded border border-dashed bg-muted/20 px-3 py-2 text-xs italic text-muted-foreground">
        {t("courses.figures.missing")}
      </div>
    );
  } else if (!spec) {
    body = (
      <FigureErrorBox
        detail={parsed.error ? describeFigureParseError(parsed.error, t) : null}
        source={content}
      />
    );
  } else if (query.isError) {
    body = (
      <FigureErrorBox detail={describeApiError(query.error)} source={content} />
    );
  } else if (!query.data) {
    body = <FigureLoading />;
  } else {
    extraCaption = query.data.computed_caption;
    body = (
      <img
        src={svgDataUri(query.data.svg)}
        alt={altText || caption || ""}
        className="figure-svg mx-auto block h-auto max-w-full"
      />
    );
  }

  return (
    <FigureFrame
      assetId={assetId}
      format="function"
      caption={caption}
      altText={altText}
      number={number}
      variant={variant}
      extraCaption={extraCaption}
      className={className}
    >
      {body}
    </FigureFrame>
  );
}

export default FunctionFigure;
