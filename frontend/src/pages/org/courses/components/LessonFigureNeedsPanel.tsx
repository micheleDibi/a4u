import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ImagePlus, Loader2, RotateCcw, X } from "lucide-react";

import {
  coursesApi,
  type CourseLessonOut,
  type CourseOut,
  type FigureNeedLinkInput,
  type LessonContentSection,
  type LessonContentVisualAsset,
  type LessonFigureNeedStatus,
  type LessonFigureNeedView,
  type LessonFigureNeedsSummary,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { extractApiError } from "@/lib/errors";

/**
 * Piano delle figure della lezione (doc 18 §23.7, §24): etichetta nella
 * riga della lezione e, nella finestra di modifica, il pannello «Figure
 * consigliate». Stato, motivo ed esito della letteratura arrivano già
 * calcolati dal backend (`figure_needs_view`); il frontend non li
 * ricalcola e non mostra mai nomi di documenti (la «Fonte» la scrive il
 * render). Il sistema inserisce da solo le figure disponibili; qui il
 * docente inserisce nella bozza quelle proposte («Inserisci»: frase e
 * figura nella sezione, si salva con «Salva») o dice che una non serve.
 */

const STATUS_VARIANT: Record<
  LessonFigureNeedStatus,
  "success" | "warning" | "muted" | "outline"
> = {
  placed: "success",
  misplaced: "warning",
  missing: "warning",
  uncovered: "outline",
  dismissed: "muted",
};

export function LessonFigureNeedsChip({
  summary,
}: {
  summary: LessonFigureNeedsSummary | null | undefined;
}) {
  const { t } = useTranslation();
  if (!summary || summary.needs === 0) return null;
  const complete =
    summary.musts_placed >= summary.musts && summary.misplaced === 0;
  const title = t("courses.figureNeeds.chipTitle", {
    placed: summary.musts_placed,
    total: summary.musts,
    uncovered: summary.uncovered,
    misplaced: summary.misplaced,
  });
  return (
    <Badge
      variant={summary.musts === 0 ? "muted" : complete ? "success" : "warning"}
      className="text-[11px]"
      title={title}
      aria-label={title}
    >
      {summary.musts > 0
        ? t("courses.figureNeeds.chip", {
            placed: summary.musts_placed,
            total: summary.musts,
          })
        : t("courses.figureNeeds.chipOptional", { count: summary.needs })}
    </Badge>
  );
}

interface PanelProps {
  orgId: string;
  courseId: string;
  lesson: CourseLessonOut;
  /** Sezioni e asset della BOZZA: «Inserisci» parte dal testo attuale. */
  sections: LessonContentSection[];
  assetIds: string[];
  disabled: boolean;
  onInsert: (
    sectionId: string,
    sectionText: string,
    asset: LessonContentVisualAsset,
  ) => void;
}

export function LessonFigureNeedsPanel({
  orgId,
  courseId,
  lesson,
  sections,
  assetIds,
  disabled,
  onInsert,
}: PanelProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const view = lesson.figure_needs_view;
  // Voci inserite nella bozza in questa sessione (da salvare).
  const [inserted, setInserted] = useState<Record<string, string>>({});

  const candidatesQuery = useQuery({
    queryKey: ["figure-need-candidates", orgId, courseId, lesson.id],
    queryFn: () =>
      coursesApi.lessonContent.figureNeedCandidates(orgId, courseId, lesson.id),
    enabled: !!view && view.length > 0,
    staleTime: 0,
  });
  const candidateOf = new Map(
    (candidatesQuery.data ?? []).map((c) => [c.need_id, c]),
  );

  const linkMut = useMutation({
    mutationFn: ({
      needId,
      payload,
    }: {
      needId: string;
      payload: FigureNeedLinkInput;
    }) =>
      coursesApi.lessonContent.updateFigureNeedLink(
        orgId,
        courseId,
        lesson.id,
        needId,
        payload,
      ),
    onSuccess: (fresh: CourseOut) => {
      const detailKey = ["courses", "detail", orgId, courseId];
      qc.setQueryData(detailKey, fresh);
      qc.invalidateQueries({ queryKey: detailKey });
      toast.success(t("courses.figureNeeds.toast.updated"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.figureNeeds.toast.error"),
      ),
  });

  const insertMut = useMutation({
    mutationFn: ({ needId, figureId, sectionId }: {
      needId: string;
      figureId: string;
      sectionId: string;
    }) => {
      const section = sections.find((s) => s.section_id === sectionId);
      return coursesApi.lessonContent.insertFigureForNeed(
        orgId,
        courseId,
        lesson.id,
        needId,
        {
          figure_id: figureId,
          section_text: section?.content ?? "",
          asset_ids: assetIds,
        },
      );
    },
    onSuccess: (out, vars) => {
      onInsert(out.section_id, out.section_text, out.asset);
      setInserted((prev) => ({ ...prev, [vars.needId]: out.asset.asset_id }));
      toast.success(t("courses.figureNeeds.toast.inserted"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.figureNeeds.toast.error"),
      ),
  });

  if (!view || view.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {t("courses.figureNeeds.empty")}
      </p>
    );
  }

  const sectionTitle = (sectionId: string) =>
    sections.find((s) => s.section_id === sectionId)?.title ??
    lesson.section_outline.find((s) => s.section_id === sectionId)?.title ??
    sectionId;

  // Gruppi per sezione, nell'ordine già calcolato dal backend (N1…Nn).
  const groups: { sectionId: string; needs: LessonFigureNeedView[] }[] = [];
  for (const need of view) {
    const last = groups[groups.length - 1];
    if (last && last.sectionId === need.section_id) last.needs.push(need);
    else groups.push({ sectionId: need.section_id, needs: [need] });
  }
  const busy = disabled || linkMut.isPending || insertMut.isPending;

  const detail = (need: LessonFigureNeedView): string | null => {
    if (need.status === "placed" || need.status === "misplaced") {
      const where =
        need.status === "misplaced"
          ? t("courses.figureNeeds.citedIn", {
              section: need.cited_in
                ? sectionTitle(need.cited_in)
                : t("courses.figureNeeds.outsideSections"),
            })
          : null;
      return [need.linked ? t("courses.figureNeeds.linkedByYou") : null, where]
        .filter(Boolean)
        .join(" · ");
    }
    if (need.status === "missing") {
      return need.reason === "not_cited"
        ? t("courses.figureNeeds.notCitedHint")
        : t("courses.figureNeeds.missingHint");
    }
    if (need.status === "uncovered") {
      const reason = t(`courses.figureNeeds.reasons.${need.reason ?? "no_candidate"}`, {
        defaultValue: t("courses.figureNeeds.reasons.no_candidate"),
      });
      const literature = need.literature
        ? t(`courses.figureNeeds.literature.${need.literature}`, {
            defaultValue: "",
          })
        : "";
      return [reason, literature].filter(Boolean).join(" · ");
    }
    return null;
  };

  return (
    <section className="space-y-3" aria-label={t("courses.figureNeeds.title")}>
      <p className="text-xs text-muted-foreground">
        {t("courses.figureNeeds.description")}
      </p>
      {groups.map((group) => (
        <div key={group.sectionId} className="space-y-1.5">
          <div className="text-xs font-medium text-muted-foreground">
            {sectionTitle(group.sectionId)}
          </div>
          <ul className="space-y-1.5">
            {group.needs.map((need) => {
              const insertedAs = inserted[need.need_id];
              const open =
                !insertedAs &&
                (need.status === "uncovered" ||
                  (need.status === "missing" && need.reason !== "not_cited"));
              const candidate = open ? candidateOf.get(need.need_id) : undefined;
              const info = insertedAs ? null : detail(need);
              const canDismiss =
                !insertedAs && need.status !== "dismissed" && !need.linked;
              const canRestore = need.status === "dismissed" || !!need.linked;
              return (
                <li
                  key={need.need_id}
                  className="flex flex-wrap items-start gap-2 rounded border px-2 py-1.5"
                >
                  <Badge variant="outline" className="font-mono text-[11px]">
                    {need.label}
                  </Badge>
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <div className="text-sm">{need.subject}</div>
                    <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                      <span>
                        {need.priority === "must"
                          ? t("courses.figureNeeds.priority.must")
                          : t("courses.figureNeeds.priority.should")}
                      </span>
                      {need.sequence_group && need.sequence_index ? (
                        <span>
                          ·{" "}
                          {t("courses.figureNeeds.sequence", {
                            index: need.sequence_index,
                          })}
                        </span>
                      ) : null}
                      {info ? <span>· {info}</span> : null}
                    </div>
                    {candidate ? (
                      <div className="text-[11px]">
                        {t("courses.figureNeeds.proposed", {
                          caption: candidate.caption,
                        })}
                      </div>
                    ) : null}
                  </div>
                  <Badge
                    variant={insertedAs ? "success" : STATUS_VARIANT[need.status]}
                    className="text-[11px]"
                  >
                    {insertedAs
                      ? t("courses.figureNeeds.inserted")
                      : t(`courses.figureNeeds.status.${need.status}`)}
                  </Badge>
                  <div className="flex items-center gap-1">
                    {candidate && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs"
                        disabled={busy}
                        onClick={() =>
                          insertMut.mutate({
                            needId: need.need_id,
                            figureId: candidate.figure_id,
                            sectionId: need.section_id,
                          })
                        }
                      >
                        {insertMut.isPending &&
                        insertMut.variables?.needId === need.need_id ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <ImagePlus className="size-3.5" />
                        )}
                        {t("courses.figureNeeds.insert")}
                      </Button>
                    )}
                    {canDismiss && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7 text-xs"
                        disabled={busy}
                        onClick={() =>
                          linkMut.mutate({
                            needId: need.need_id,
                            payload: { state: "dismissed" },
                          })
                        }
                      >
                        <X className="size-3.5" />
                        {t("courses.figureNeeds.dismiss")}
                      </Button>
                    )}
                    {canRestore && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7 text-xs"
                        disabled={busy}
                        onClick={() =>
                          linkMut.mutate({
                            needId: need.need_id,
                            payload: { state: null },
                          })
                        }
                      >
                        <RotateCcw className="size-3.5" />
                        {t("courses.figureNeeds.restore")}
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <p className="text-xs text-muted-foreground">
        {t("courses.figureNeeds.uncoveredHint")}
      </p>
    </section>
  );
}
