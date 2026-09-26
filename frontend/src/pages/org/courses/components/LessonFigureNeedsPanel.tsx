import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  CheckCircle2,
  CircleSlash,
  ImagePlus,
  Loader2,
  MoveRight,
  PlusCircle,
  RotateCcw,
  SearchX,
  X,
} from "lucide-react";

import {
  coursesApi,
  type CourseLessonOut,
  type CourseOut,
  type FigureNeedCandidate,
  type FigureNeedLinkInput,
  type LessonContentSection,
  type LessonContentVisualAsset,
  type LessonFigureNeedView,
} from "@/api/courses";
import { SourceFigureThumbnail } from "@/components/shared/SourceFigure";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { extractApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

/**
 * Piano delle figure della lezione (doc 18 §23.7, §24).
 *
 * - `LessonFigureNeedsChip`: etichetta nella riga della lezione, sempre in
 *   parole: quante figure dalle fonti (documenti del corso e letteratura)
 *   sono nel testo, oppure perché non ce ne sono (piano in calcolo, da
 *   rigenerare, non previsto, nessuna necessaria).
 * - `LessonFigureNeedsPanel`: gruppo «Figure dalle fonti» nella finestra di
 *   modifica. Riepilogo, «Inserisci tutte», e per sezione ogni figura con
 *   lo stato in parole e l'azione giusta (miniatura e «Inserisci» se il
 *   corso ha una figura adatta, «Non serve», «Ripristina»).
 *
 * Stato e motivi arrivano già calcolati dal backend (`figure_needs_view`,
 * `figure_needs_summary`); il frontend non li ricalcola e non mostra mai
 * nomi di documenti (la «Fonte» la scrive il render). «Inserisci» modifica
 * solo la bozza: si salva con «Salva».
 */

type Tone = "success" | "warning" | "muted" | "outline";

interface ChipState {
  text: string;
  title: string;
  tone: Tone;
}

function useChipState(lesson: CourseLessonOut): ChipState {
  const { t } = useTranslation();
  const status = lesson.figure_needs_status ?? null;
  const summary = lesson.figure_needs_summary;
  if (status === "pending" || status === "processing") {
    return {
      text: t("courses.figureNeeds.chip.computing"),
      title: t("courses.figureNeeds.state.computing"),
      tone: "muted",
    };
  }
  if (status === "skipped") {
    return {
      text: t("courses.figureNeeds.chip.notPlanned"),
      title: t("courses.figureNeeds.state.notPlanned"),
      tone: "muted",
    };
  }
  if (status !== "ready" || !summary) {
    return {
      text: t("courses.figureNeeds.chip.regenerate"),
      title:
        status === "failed"
          ? t("courses.figureNeeds.state.failed")
          : t("courses.figureNeeds.state.regenerate"),
      tone: "muted",
    };
  }
  if (summary.musts === 0 && summary.shoulds === 0) {
    return {
      text: t("courses.figureNeeds.chip.none"),
      title: t("courses.figureNeeds.state.none"),
      tone: "muted",
    };
  }
  const title = t("courses.figureNeeds.chip.details", {
    musts: summary.musts_placed,
    mustsTotal: summary.musts,
    shoulds: summary.shoulds_placed,
    shouldsTotal: summary.shoulds,
    toFind: summary.uncovered,
    elsewhere: summary.misplaced,
    dismissed: summary.dismissed,
  });
  if (summary.musts > 0) {
    return {
      text: t("courses.figureNeeds.chip.musts", {
        placed: summary.musts_placed,
        total: summary.musts,
      }),
      title,
      tone: summary.musts_placed >= summary.musts ? "success" : "warning",
    };
  }
  return {
    text: t("courses.figureNeeds.chip.shoulds", {
      placed: summary.shoulds_placed,
      total: summary.shoulds,
    }),
    title,
    tone: "outline",
  };
}

export function LessonFigureNeedsChip({ lesson }: { lesson: CourseLessonOut }) {
  const state = useChipState(lesson);
  return (
    <Badge
      variant={state.tone}
      className="text-[11px] whitespace-nowrap"
      title={state.title}
      aria-label={state.title}
    >
      {state.text}
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

interface RowState {
  icon: typeof CheckCircle2;
  text: string;
  tone: string;
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
  const status = lesson.figure_needs_status ?? null;
  // Voci inserite nella bozza in questa sessione (da salvare).
  const [inserted, setInserted] = useState<Record<string, string>>({});
  const [working, setWorking] = useState<string | null>(null);
  // Bozza sempre aggiornata: «Inserisci» applica il testo restituito solo se
  // la sezione non è cambiata durante la richiesta.
  const sectionsRef = useRef(sections);
  sectionsRef.current = sections;
  const assetIdsRef = useRef(assetIds);
  assetIdsRef.current = assetIds;

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

  // Inserimento di una o più figure, una dopo l'altra, sulla bozza corrente.
  const insertMany = async (
    items: { need: LessonFigureNeedView; candidate: FigureNeedCandidate }[],
    key: string,
  ) => {
    setWorking(key);
    let draft = sectionsRef.current;
    let ids = [...assetIdsRef.current];
    let done = 0;
    try {
      for (const { need, candidate } of items) {
        const sent =
          draft.find((s) => s.section_id === need.section_id)?.content ?? "";
        const out = await coursesApi.lessonContent.insertFigureForNeed(
          orgId,
          courseId,
          lesson.id,
          need.need_id,
          { figure_id: candidate.figure_id, section_text: sent, asset_ids: ids },
        );
        const live = sectionsRef.current.find(
          (s) => s.section_id === out.section_id,
        )?.content;
        if (live !== sent) {
          toast.error(t("courses.figureNeeds.toast.changed"));
          break;
        }
        draft = draft.map((s) =>
          s.section_id === out.section_id ? { ...s, content: out.section_text } : s,
        );
        ids = [...ids, out.asset.asset_id];
        sectionsRef.current = draft;
        onInsert(out.section_id, out.section_text, out.asset);
        setInserted((prev) => ({ ...prev, [need.need_id]: out.asset.asset_id }));
        done += 1;
      }
      if (done > 0) toast.success(t("courses.figureNeeds.toast.inserted", { count: done }));
    } catch (err) {
      toast.error(
        extractApiError(err).message ?? t("courses.figureNeeds.toast.error"),
      );
    } finally {
      setWorking(null);
    }
  };

  // --- piano non disponibile ------------------------------------------------
  if (status !== "ready" || !view) {
    const message =
      status === "pending" || status === "processing"
        ? t("courses.figureNeeds.state.computing")
        : status === "skipped"
          ? t("courses.figureNeeds.state.notPlanned")
          : status === "failed"
            ? t("courses.figureNeeds.state.failed")
            : t("courses.figureNeeds.state.regenerate");
    return <p className="text-sm text-muted-foreground">{message}</p>;
  }
  if (view.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {t("courses.figureNeeds.state.none")}
      </p>
    );
  }

  const sectionTitle = (sectionId: string) =>
    sections.find((s) => s.section_id === sectionId)?.title ??
    lesson.section_outline.find((s) => s.section_id === sectionId)?.title ??
    sectionId;

  const isOpen = (need: LessonFigureNeedView) =>
    !inserted[need.need_id] &&
    (need.status === "uncovered" ||
      (need.status === "missing" && need.reason !== "not_cited"));
  const proposals = view
    .filter((need) => isOpen(need) && candidateOf.has(need.need_id))
    .sort((a, b) => (a.priority === "must" ? 0 : 1) - (b.priority === "must" ? 0 : 1))
    .map((need) => ({ need, candidate: candidateOf.get(need.need_id)! }));
  const busy = disabled || linkMut.isPending || working !== null;

  const rowState = (need: LessonFigureNeedView): RowState => {
    if (inserted[need.need_id]) {
      return { icon: PlusCircle, text: t("courses.figureNeeds.row.draft"), tone: "text-emerald-700 dark:text-emerald-400" };
    }
    switch (need.status) {
      case "placed":
        return { icon: CheckCircle2, text: t("courses.figureNeeds.row.inText"), tone: "text-emerald-700 dark:text-emerald-400" };
      case "misplaced":
        return {
          icon: MoveRight,
          text: need.cited_in
            ? t("courses.figureNeeds.row.elsewhere", { section: sectionTitle(need.cited_in) })
            : t("courses.figureNeeds.row.introOrSummary"),
          tone: "text-amber-700 dark:text-amber-400",
        };
      case "dismissed":
        return { icon: CircleSlash, text: t("courses.figureNeeds.row.dismissed"), tone: "text-muted-foreground" };
      default:
        break;
    }
    if (need.status === "missing" && need.reason === "not_cited") {
      return { icon: MoveRight, text: t("courses.figureNeeds.row.notCited"), tone: "text-amber-700 dark:text-amber-400" };
    }
    if (candidateOf.has(need.need_id)) {
      return { icon: PlusCircle, text: t("courses.figureNeeds.row.available"), tone: "text-sky-700 dark:text-sky-400" };
    }
    if (need.status === "missing") {
      return { icon: SearchX, text: t("courses.figureNeeds.row.planGone"), tone: "text-amber-700 dark:text-amber-400" };
    }
    const reason = t(`courses.figureNeeds.reasons.${need.reason ?? "no_candidate"}`, {
      defaultValue: t("courses.figureNeeds.reasons.no_candidate"),
    });
    const literature = need.literature
      ? t(`courses.figureNeeds.literature.${need.literature}`, { defaultValue: "" })
      : "";
    return {
      icon: SearchX,
      text: [reason, literature].filter(Boolean).join(" · "),
      tone: "text-amber-700 dark:text-amber-400",
    };
  };

  // Gruppi per sezione, nell'ordine già calcolato dal backend.
  const groups: { sectionId: string; needs: LessonFigureNeedView[] }[] = [];
  for (const need of view) {
    const last = groups[groups.length - 1];
    if (last && last.sectionId === need.section_id) last.needs.push(need);
    else groups.push({ sectionId: need.section_id, needs: [need] });
  }
  const summary = lesson.figure_needs_summary;
  const draftCount = Object.keys(inserted).length;

  return (
    <section className="space-y-4" aria-label={t("courses.figureNeeds.title")}>
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/30 px-3 py-2">
        <div className="space-y-0.5 text-sm">
          {summary && summary.musts > 0 && (
            <div className="font-medium">
              {t("courses.figureNeeds.summary.musts", {
                placed: summary.musts_placed,
                total: summary.musts,
              })}
            </div>
          )}
          {summary && summary.shoulds > 0 && (
            <div className={cn(summary.musts > 0 ? "text-muted-foreground" : "font-medium")}>
              {t("courses.figureNeeds.summary.shoulds", {
                placed: summary.shoulds_placed,
                total: summary.shoulds,
              })}
            </div>
          )}
          {draftCount > 0 && (
            <div className="text-xs text-emerald-700 dark:text-emerald-400">
              {t("courses.figureNeeds.summary.draft", { count: draftCount })}
            </div>
          )}
        </div>
        {proposals.length > 0 && (
          <Button
            type="button"
            size="sm"
            disabled={busy}
            onClick={() => insertMany(proposals, "all")}
          >
            {working === "all" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <ImagePlus className="size-3.5" />
            )}
            {t("courses.figureNeeds.insertAll", { count: proposals.length })}
          </Button>
        )}
      </div>

      {groups.map((group) => (
        <div key={group.sectionId} className="space-y-2">
          <div className="text-xs font-semibold text-muted-foreground">
            «{sectionTitle(group.sectionId)}»
          </div>
          <ul className="space-y-2">
            {group.needs.map((need) => {
              const state = rowState(need);
              const Icon = state.icon;
              const candidate = isOpen(need) ? candidateOf.get(need.need_id) : undefined;
              const canDismiss =
                !inserted[need.need_id] && need.status !== "dismissed" && !need.linked;
              const canRestore = need.status === "dismissed";
              return (
                <li key={need.need_id} className="rounded-md border px-3 py-2">
                  <div className="flex items-start gap-2">
                    <Icon className={cn("mt-0.5 size-4 shrink-0", state.tone)} />
                    <div className="min-w-0 flex-1 space-y-0.5">
                      <div className="text-sm">
                        {need.subject}
                        <span className="ml-2 text-[11px] text-muted-foreground">
                          {need.priority === "must"
                            ? t("courses.figureNeeds.priority.must")
                            : t("courses.figureNeeds.priority.should")}
                          {need.sequence_group && need.sequence_index
                            ? ` · ${t("courses.figureNeeds.row.sequence", { index: need.sequence_index })}`
                            : ""}
                        </span>
                      </div>
                      <div className={cn("text-xs", state.tone)}>{state.text}</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      {canDismiss && !candidate && (
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
                  </div>
                  {candidate && (
                    <div className="mt-2 flex items-center gap-3 rounded border bg-muted/20 p-2">
                      <SourceFigureThumbnail
                        figureId={candidate.figure_id}
                        alt={candidate.caption}
                      />
                      <div className="min-w-0 flex-1 text-xs">{candidate.caption}</div>
                      <div className="flex shrink-0 items-center gap-1">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="h-7 text-xs"
                          disabled={busy}
                          onClick={() => insertMany([{ need, candidate }], need.need_id)}
                        >
                          {working === need.need_id ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <ImagePlus className="size-3.5" />
                          )}
                          {t("courses.figureNeeds.insert")}
                        </Button>
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
                      </div>
                    </div>
                  )}
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
