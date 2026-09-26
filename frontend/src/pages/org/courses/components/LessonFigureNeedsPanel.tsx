import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Link2, RotateCcw, X } from "lucide-react";

import {
  coursesApi,
  isAssessmentRaw,
  type CourseLessonOut,
  type CourseOut,
  type FigureNeedLinkInput,
  type LessonFigureNeedStatus,
  type LessonFigureNeedView,
  type LessonFigureNeedsSummary,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCourseRef } from "@/contexts/CourseRefContext";
import { extractApiError } from "@/lib/errors";

/**
 * Piano delle figure della lezione (doc 18 §23.7): etichetta nella riga e
 * pannello «Figure consigliate». Stato, motivo ed esito della letteratura
 * arrivano già calcolati dal backend (`figure_needs_view`); il frontend non
 * li ricalcola e non mostra mai nomi di documenti (la «Fonte» la scrive il
 * render). Le azioni scrivono solo i collegamenti del docente («Non serve»,
 * figura collegata, ripristino): il contenuto della lezione non cambia.
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

export function LessonFigureNeedsPanel({
  lesson,
  canEdit,
}: {
  lesson: CourseLessonOut;
  canEdit: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const courseRef = useCourseRef();
  const view = lesson.figure_needs_view;

  const linkMut = useMutation({
    mutationFn: ({
      needId,
      payload,
    }: {
      needId: string;
      payload: FigureNeedLinkInput;
    }) => {
      if (!courseRef) throw new Error("missing course ref");
      return coursesApi.lessonContent.updateFigureNeedLink(
        courseRef.orgId,
        courseRef.courseId,
        lesson.id,
        needId,
        payload,
      );
    },
    onSuccess: (fresh: CourseOut) => {
      if (!courseRef) return;
      const detailKey = ["courses", "detail", courseRef.orgId, courseRef.courseId];
      qc.setQueryData(detailKey, fresh);
      qc.invalidateQueries({ queryKey: detailKey });
      toast.success(t("courses.figureNeeds.toast.updated"));
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.figureNeeds.toast.error"),
      ),
  });

  if (!view || view.length === 0) return null;

  const raw =
    lesson.content_raw && !isAssessmentRaw(lesson.content_raw)
      ? lesson.content_raw
      : null;
  const assets = raw?.visual_assets ?? [];
  const captionOf = (assetId: string | undefined) =>
    assets.find((a) => a.asset_id.toLowerCase() === assetId?.toLowerCase())
      ?.caption ?? "";
  const sectionTitle = (sectionId: string) =>
    lesson.section_outline.find((s) => s.section_id === sectionId)?.title ??
    sectionId;

  // Gruppi per sezione, nell'ordine già calcolato dal backend (N1…Nn).
  const groups: { sectionId: string; needs: LessonFigureNeedView[] }[] = [];
  for (const need of view) {
    const last = groups[groups.length - 1];
    if (last && last.sectionId === need.section_id) last.needs.push(need);
    else groups.push({ sectionId: need.section_id, needs: [need] });
  }
  const hasUncovered = view.some(
    (n) => n.status === "uncovered" || n.status === "missing",
  );
  const busy = linkMut.isPending;

  const detail = (need: LessonFigureNeedView): string | null => {
    if (need.status === "placed" || need.status === "misplaced") {
      const caption = captionOf(need.asset_id);
      const where =
        need.status === "misplaced"
          ? t("courses.figureNeeds.citedIn", {
              section: need.cited_in
                ? sectionTitle(need.cited_in)
                : t("courses.figureNeeds.outsideSections"),
            })
          : null;
      return [
        need.linked ? t("courses.figureNeeds.linkedByYou") : null,
        caption || need.asset_id,
        where,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    if (need.status === "missing") return t("courses.figureNeeds.missingHint");
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
    <section
      className="space-y-3 rounded-md border bg-background px-3 py-3"
      aria-label={t("courses.figureNeeds.title")}
    >
      <div>
        <h5 className="text-sm font-semibold">{t("courses.figureNeeds.title")}</h5>
        <p className="text-xs text-muted-foreground">
          {t("courses.figureNeeds.description")}
        </p>
      </div>
      {groups.map((group) => (
        <div key={group.sectionId} className="space-y-1.5">
          <div className="text-xs font-medium text-muted-foreground">
            {sectionTitle(group.sectionId)}
          </div>
          <ul className="space-y-1.5">
            {group.needs.map((need) => {
              const info = detail(need);
              const canDismiss =
                need.status !== "dismissed" && !need.linked;
              const canRestore = need.status === "dismissed" || !!need.linked;
              const canLink =
                (need.status === "uncovered" || need.status === "missing") &&
                assets.length > 0;
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
                  </div>
                  <Badge
                    variant={STATUS_VARIANT[need.status]}
                    className="text-[11px]"
                  >
                    {t(`courses.figureNeeds.status.${need.status}`)}
                  </Badge>
                  {canEdit && (
                    <div className="flex items-center gap-1">
                      {canLink && (
                        <Select
                          value=""
                          disabled={busy}
                          onValueChange={(assetId) =>
                            linkMut.mutate({
                              needId: need.need_id,
                              payload: { state: "linked", asset_id: assetId },
                            })
                          }
                        >
                          <SelectTrigger
                            className="h-7 w-auto gap-1 text-xs"
                            aria-label={t("courses.figureNeeds.link")}
                          >
                            <Link2 className="size-3.5" />
                            <SelectValue placeholder={t("courses.figureNeeds.link")} />
                          </SelectTrigger>
                          <SelectContent>
                            {assets.map((asset) => (
                              <SelectItem key={asset.asset_id} value={asset.asset_id}>
                                {asset.caption || asset.asset_id}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
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
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      {hasUncovered && canEdit && (
        <p className="text-xs text-muted-foreground">
          {t("courses.figureNeeds.uncoveredHint")}
        </p>
      )}
    </section>
  );
}
