import { useQuery } from "@tanstack/react-query";
import { ZoomIn } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { coursesApi, type DocumentFigure } from "@/api/courses";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

/** Immagine della figura (Blob dall'endpoint autenticato) come object URL.
 *  Chiave condivisa con i render della lezione: una sola richiesta. */
function useFigureObjectUrl(
  orgId: string | undefined,
  courseId: string | undefined,
  figure: DocumentFigure | undefined,
  preview: boolean,
  enabled: boolean,
) {
  const query = useQuery({
    queryKey: ["document-figure-image", orgId, courseId, figure?.id, preview, figure?.image_rev],
    queryFn: () =>
      coursesApi.documentFigures.image(orgId!, courseId!, figure!.id, preview, figure!.image_rev),
    enabled: Boolean(orgId && courseId && figure) && enabled,
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

function useFigureImage(figure: DocumentFigure | undefined, preview: boolean) {
  const courseRef = useCourseRef();
  return useFigureObjectUrl(
    courseRef?.orgId,
    courseRef?.courseId,
    figure,
    preview,
    Boolean(figure?.renderable && figure.attribution),
  );
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

/**
 * «Ingrandisci» per le miniature piccole (selettore dell'editor, riassunto
 * strutturato): la miniatura diventa un pulsante che apre la figura a piena
 * risoluzione in una finestra, con didascalia, descrizione e riga «Fonte» del
 * backend. L'immagine intera (non l'anteprima) si scarica solo all'apertura.
 * Dentro un'altra finestra (il selettore) Esc chiude solo lo zoom.
 */
export function SourceFigureZoom({
  orgId,
  courseId,
  figure,
  children,
}: {
  orgId: string;
  courseId: string;
  figure: DocumentFigure;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const image = useFigureObjectUrl(orgId, courseId, figure, false, open);
  const caption = figure.source_caption?.trim() ?? "";
  const description = figure.description?.trim() ?? "";
  const attribution = figure.attribution.trim();
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t("courses.sourceFigures.zoom.open")}
        title={t("courses.sourceFigures.zoom.open")}
        className="group relative block w-full cursor-zoom-in rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {children}
        <span
          aria-hidden="true"
          className="absolute right-1 top-1 rounded-md border bg-background/90 p-1 text-muted-foreground shadow-sm transition-colors group-hover:text-foreground"
        >
          <ZoomIn className="size-4" />
        </span>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[95vh] w-[95vw] max-w-5xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t("courses.sourceFigures.zoom.title")}</DialogTitle>
            <DialogDescription>{t("courses.sourceFigures.zoom.hint")}</DialogDescription>
          </DialogHeader>
          {image.url ? (
            <div className="flex justify-center rounded border bg-white p-2">
              <img
                src={image.url}
                alt={description || caption}
                className="block h-auto max-h-[70vh] w-auto max-w-full object-contain"
              />
            </div>
          ) : image.isError ? (
            <div className="flex min-h-48 items-center justify-center rounded bg-muted/40 text-sm text-muted-foreground">
              {t("courses.figures.renderError")}
            </div>
          ) : (
            <FigureLoading className="h-48" />
          )}
          {(caption || description || attribution) && (
            <div className="space-y-1">
              {caption && <p className="text-sm leading-relaxed">{caption}</p>}
              {description && description !== caption && (
                <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
              )}
              {attribution && (
                <p className="text-[0.7rem] text-muted-foreground">{attribution}</p>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
