import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import {
  coursesApi,
  type CourseDocumentOut,
  type DocumentFigure,
} from "@/api/courses";
import { SourceFigureResolutionBadge } from "@/components/shared/SourceFigureResolutionBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { extractApiError } from "@/lib/errors";

/** Figure per gruppo: un manuale può averne centinaia, e ogni miniatura è
 *  una richiesta all'endpoint autenticato. */
const PAGE_SIZE = 24;

const PPTX_MIME =
  "application/vnd.openxmlformats-officedocument.presentationml.presentation";

interface Props {
  orgId: string;
  courseId: string;
  doc: CourseDocumentOut;
  /** Mostra «Non proporre più» / «Proponi di nuovo». */
  canEdit: boolean;
}

function Thumbnail({
  orgId,
  courseId,
  figure,
}: {
  orgId: string;
  courseId: string;
  figure: DocumentFigure;
}) {
  const { t } = useTranslation();
  // Stessa chiave del selettore dell'editor: la cache è condivisa.
  const query = useQuery({
    queryKey: ["document-figure-image", orgId, courseId, figure.id, true, figure.image_rev],
    queryFn: () =>
      coursesApi.documentFigures.image(orgId, courseId, figure.id, true, figure.image_rev),
    staleTime: Infinity,
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!query.data) return;
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [query.data]);
  if (query.isError) {
    return (
      <div className="flex h-32 items-center justify-center rounded bg-muted/40 text-xs text-muted-foreground">
        {t("courses.figures.renderError")}
      </div>
    );
  }
  if (!url) {
    return (
      <div className="flex h-32 items-center justify-center text-muted-foreground">
        <Loader2 className="size-5 animate-spin" />
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={figure.description || ""}
      loading="lazy"
      className="mx-auto block max-h-40 w-auto max-w-full rounded bg-white object-contain p-1"
    />
  );
}

/**
 * Figure estratte dal documento, nel riassunto strutturato: miniatura,
 * pagina, didascalia originale, descrizione e riga «Fonte» del backend (il
 * frontend non la ricompone mai). L'esclusione vale per le proposte future;
 * le lezioni già generate non cambiano.
 */
export function DocumentFiguresSection({ orgId, courseId, doc, canEdit }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [visible, setVisible] = useState(PAGE_SIZE);
  useEffect(() => setVisible(PAGE_SIZE), [doc.id]);

  const query = useQuery({
    queryKey: ["document-figures", orgId, courseId, "document", doc.id],
    queryFn: () => coursesApi.documentFigures.list(orgId, courseId, doc.id),
  });

  const toggle = useMutation({
    mutationFn: (figure: DocumentFigure) =>
      coursesApi.documentFigures.update(orgId, courseId, figure.id, {
        excluded_by_user: !figure.excluded_by_user,
      }),
    onSuccess: (updated) => {
      toast.success(
        updated.excluded_by_user
          ? t("courses.sourceFigures.excluded")
          : t("courses.docs.summary.dialog.figures.included"),
      );
      // Anche il catalogo dell'editor della dispensa.
      return qc.invalidateQueries({ queryKey: ["document-figures", orgId, courseId] });
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.sourceFigures.excludeFailed"),
      ),
  });

  if (query.isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  }
  const figures = query.data ?? [];
  if (figures.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {t("courses.docs.summary.dialog.figures.empty")}
      </p>
    );
  }
  const pageKey = doc.mime_type === PPTX_MIME ? "slide" : "page";
  const shown = figures.slice(0, visible);

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        {t("courses.docs.summary.dialog.figures.hint", { count: figures.length })}
      </p>
      <ul className="grid gap-3 sm:grid-cols-2">
        {shown.map((figure) => (
          <li
            key={figure.id}
            className="flex flex-col gap-2 rounded-md border border-border p-2"
          >
            <Thumbnail orgId={orgId} courseId={courseId} figure={figure} />
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              {figure.page !== null && (
                <Badge variant="secondary">
                  {t(`courses.sourceFigures.picker.${pageKey}`, { page: figure.page })}
                </Badge>
              )}
              {figure.excluded_by_user ? (
                <Badge variant="muted">
                  {t("courses.docs.summary.dialog.figures.excludedBadge")}
                </Badge>
              ) : (
                !figure.selectable && (
                  <Badge variant="muted">
                    {t(`courses.sourceFigures.reasons.${figure.reason ?? "unknown"}`, {
                      defaultValue: t("courses.sourceFigures.picker.notSelectable"),
                    })}
                  </Badge>
                )
              )}
              {figure.resolution?.class === "low" && (
                <SourceFigureResolutionBadge resolution={figure.resolution} />
              )}
            </div>
            {figure.source_caption && (
              <p className="text-sm leading-relaxed text-foreground">
                {figure.source_caption}
              </p>
            )}
            {figure.description && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {figure.description}
              </p>
            )}
            {figure.attribution && (
              <p className="text-[0.7rem] text-muted-foreground">{figure.attribution}</p>
            )}
            {canEdit && (
              <div className="mt-auto flex justify-end">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={toggle.isPending && toggle.variables?.id === figure.id}
                  onClick={() => toggle.mutate(figure)}
                >
                  {figure.excluded_by_user
                    ? t("courses.docs.summary.dialog.figures.include")
                    : t("courses.sourceFigures.exclude")}
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {figures.length > visible && (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setVisible((v) => v + PAGE_SIZE)}
          >
            {t("courses.docs.summary.dialog.figures.showMore", {
              count: figures.length - visible,
            })}
          </Button>
        </div>
      )}
    </div>
  );
}
