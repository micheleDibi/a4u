import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
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
  type LessonFigureNeedsSummary,
} from "@/api/courses";
import { SourceFigureThumbnail } from "@/components/shared/SourceFigure";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { extractApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

/**
 * Figure dalle fonti della lezione (piano delle figure, doc 18 §23.7, §24).
 *
 * - `LessonFigureNeedsChip`: etichetta nella riga della lezione, sempre in
 *   parole quando la funzione è attiva: quante figure dalle fonti
 *   (documenti del corso e letteratura aperta) sono nel testo, oppure
 *   perché non ce ne sono (ricerca in corso, non ancora cercate, non
 *   previste, nessuna necessaria, tutte escluse).
 * - `LessonFigureNeedsPanel`: gruppo «Figure dalle fonti» nella finestra di
 *   modifica. Riepilogo, «Inserisci tutte», e per sezione ogni figura con
 *   lo stato in parole e l'azione giusta (miniatura e «Inserisci» se il
 *   corso ha una figura adatta, «Non serve», «Ripristina»).
 *
 * Stato e motivi arrivano già calcolati dal backend (`figure_needs_view`,
 * `figure_needs_summary`); il frontend non li ricalcola e non mostra mai
 * nomi di documenti (la «Fonte» la scrive il render). «Inserisci» modifica
 * solo la bozza (si salva con «Salva»); «Non serve» e «Ripristina» si
 * salvano subito.
 */

type Tone = "success" | "warning" | "muted" | "outline";

interface ChipState {
  text: string;
  details: string;
  tone: Tone;
}

function detailsOf(summary: LessonFigureNeedsSummary, t: TFunction) {
  const parts: string[] = [];
  if (summary.musts > 0) {
    parts.push(
      t("courses.figureNeeds.details.musts", {
        placed: summary.musts_placed,
        total: summary.musts,
      }),
    );
  }
  if (summary.shoulds > 0) {
    parts.push(
      t("courses.figureNeeds.details.shoulds", {
        placed: summary.shoulds_placed,
        total: summary.shoulds,
      }),
    );
  }
  const notCited = summary.not_cited ?? 0;
  const toFind = Math.max(0, summary.uncovered - notCited);
  if (toFind > 0) parts.push(t("courses.figureNeeds.details.toFind", { count: toFind }));
  if (notCited > 0) parts.push(t("courses.figureNeeds.details.notCited", { count: notCited }));
  if (summary.misplaced > 0) {
    parts.push(t("courses.figureNeeds.details.elsewhere", { count: summary.misplaced }));
  }
  if (summary.dismissed > 0) {
    parts.push(t("courses.figureNeeds.details.dismissed", { count: summary.dismissed }));
  }
  return parts.join(" · ");
}

function useChipState(lesson: CourseLessonOut): ChipState | null {
  const { t } = useTranslation();
  if (!lesson.figure_plan_active) return null;
  const status = lesson.figure_needs_status ?? null;
  const summary = lesson.figure_needs_summary;
  if (status === "pending" || status === "processing") {
    return {
      text: t("courses.figureNeeds.chip.computing"),
      details: t("courses.figureNeeds.state.computing"),
      tone: "muted",
    };
  }
  if (status === "skipped") {
    return {
      text: t("courses.figureNeeds.chip.notPlanned"),
      details: t("courses.figureNeeds.state.notPlanned"),
      tone: "muted",
    };
  }
  if (status === "failed") {
    return {
      text: t("courses.figureNeeds.chip.failed"),
      details: t("courses.figureNeeds.state.failed"),
      tone: "muted",
    };
  }
  if (status !== "ready" || !summary) {
    return {
      text: t("courses.figureNeeds.chip.notYet"),
      details: t("courses.figureNeeds.state.notYet"),
      tone: "muted",
    };
  }
  if (summary.musts === 0 && summary.shoulds === 0) {
    return summary.dismissed > 0
      ? {
          text: t("courses.figureNeeds.chip.allDismissed"),
          details: t("courses.figureNeeds.state.allDismissed", { count: summary.dismissed }),
          tone: "muted",
        }
      : {
          text: t("courses.figureNeeds.chip.none"),
          details: t("courses.figureNeeds.state.none"),
          tone: "muted",
        };
  }
  const details = detailsOf(summary, t);
  if (summary.musts > 0) {
    return {
      text: t("courses.figureNeeds.chip.musts", {
        placed: summary.musts_placed,
        total: summary.musts,
      }),
      details,
      tone: summary.musts_placed >= summary.musts ? "success" : "warning",
    };
  }
  return {
    text: t("courses.figureNeeds.chip.shoulds", {
      placed: summary.shoulds_placed,
      total: summary.shoulds,
    }),
    details,
    tone: "outline",
  };
}

export function LessonFigureNeedsChip({ lesson }: { lesson: CourseLessonOut }) {
  const state = useChipState(lesson);
  if (!state) return null;
  return (
    <Badge variant={state.tone} className="text-[11px] whitespace-nowrap" title={state.details}>
      {state.text}
      <span className="sr-only"> ({state.details})</span>
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
  /** «Inserisci» in corso: la finestra aspetta prima di salvare o chiudere. */
  onBusyChange?: (busy: boolean) => void;
}

interface RowState {
  icon: typeof CheckCircle2;
  text: string;
  tone: string;
}

const TONE_OK = "text-emerald-700 dark:text-emerald-400";
const TONE_WARN = "text-amber-700 dark:text-amber-400";
const TONE_INFO = "text-sky-700 dark:text-sky-400";

export function LessonFigureNeedsPanel({
  orgId,
  courseId,
  lesson,
  sections,
  assetIds,
  disabled,
  onInsert,
  onBusyChange,
}: PanelProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const view = lesson.figure_needs_view;
  const status = lesson.figure_needs_status ?? null;
  // Figure inserite nella bozza in questa sessione (need_id → asset_id).
  const [inserted, setInserted] = useState<Record<string, string>>({});
  const [working, setWorking] = useState<string | null>(null);
  // Bozza sempre aggiornata: «Inserisci» applica il testo restituito solo se
  // la sezione non è cambiata durante la richiesta.
  const sectionsRef = useRef(sections);
  sectionsRef.current = sections;
  const assetIdsRef = useRef(assetIds);
  assetIdsRef.current = assetIds;
  const insertedRef = useRef(inserted);
  insertedRef.current = inserted;
  // Finestra chiusa durante «Inserisci tutte»: il ciclo si ferma.
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const candidatesKey = ["figure-need-candidates", orgId, courseId, lesson.id];
  const candidatesQuery = useQuery({
    queryKey: candidatesKey,
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
    onSuccess: (fresh: CourseOut, vars) => {
      const detailKey = ["courses", "detail", orgId, courseId];
      qc.setQueryData(detailKey, fresh);
      qc.invalidateQueries({ queryKey: detailKey });
      // Le figure proposte dipendono dalle esclusioni: si ricalcolano.
      qc.invalidateQueries({ queryKey: candidatesKey });
      toast.success(
        vars.payload.state === "dismissed"
          ? t("courses.figureNeeds.toast.dismissed")
          : t("courses.figureNeeds.toast.restored"),
      );
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
    onBusyChange?.(true);
    let draft = sectionsRef.current;
    let ids = [...assetIdsRef.current];
    const draftAssets = { ...insertedRef.current };
    let done = 0;
    try {
      for (const { need, candidate } of items) {
        if (!aliveRef.current) return;
        const sent =
          draft.find((s) => s.section_id === need.section_id)?.content ?? "";
        const out = await coursesApi.lessonContent.insertFigureForNeed(
          orgId,
          courseId,
          lesson.id,
          need.need_id,
          {
            figure_id: candidate.figure_id,
            section_text: sent,
            asset_ids: ids,
            draft_assets: draftAssets,
          },
        );
        if (!aliveRef.current) return;
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
        draftAssets[need.need_id] = out.asset.asset_id;
        sectionsRef.current = draft;
        onInsert(out.section_id, out.section_text, out.asset);
        setInserted((prev) => ({ ...prev, [need.need_id]: out.asset.asset_id }));
        done += 1;
      }
      if (done > 0 && aliveRef.current) {
        toast.success(t("courses.figureNeeds.toast.inserted", { count: done }));
      }
    } catch (err) {
      if (aliveRef.current) {
        toast.error(
          extractApiError(err).message ?? t("courses.figureNeeds.toast.error"),
        );
      }
    } finally {
      if (aliveRef.current) {
        setWorking(null);
        onBusyChange?.(false);
      }
    }
  };

  // --- nessuna figura da mostrare ------------------------------------------
  if (!lesson.figure_plan_active) {
    return (
      <p className="text-sm text-muted-foreground">
        {t("courses.figureNeeds.state.off")}
      </p>
    );
  }
  if (status !== "ready" || !view) {
    const message =
      status === "pending" || status === "processing"
        ? t("courses.figureNeeds.state.computing")
        : status === "skipped"
          ? t("courses.figureNeeds.state.notPlanned")
          : status === "failed"
            ? t("courses.figureNeeds.state.failed")
            : t("courses.figureNeeds.state.notYet");
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
  // Nell'ordine del testo (sezioni e serie): una serie entra in ordine.
  const proposals = view
    .filter((need) => isOpen(need) && candidateOf.has(need.need_id))
    .map((need) => ({ need, candidate: candidateOf.get(need.need_id)! }));
  const busy = disabled || linkMut.isPending || working !== null;
  const loadingCandidates = candidatesQuery.isLoading || candidatesQuery.isFetching;

  const rowState = (need: LessonFigureNeedView): RowState => {
    if (inserted[need.need_id]) {
      return { icon: PlusCircle, text: t("courses.figureNeeds.row.draft"), tone: TONE_OK };
    }
    switch (need.status) {
      case "placed":
        return { icon: CheckCircle2, text: t("courses.figureNeeds.row.inText"), tone: TONE_OK };
      case "misplaced":
        return {
          icon: MoveRight,
          text: need.cited_in
            ? t("courses.figureNeeds.row.elsewhere", { section: sectionTitle(need.cited_in) })
            : t("courses.figureNeeds.row.introOrSummary"),
          tone: TONE_WARN,
        };
      case "dismissed":
        return { icon: CircleSlash, text: t("courses.figureNeeds.row.dismissed"), tone: "text-muted-foreground" };
      default:
        break;
    }
    if (need.status === "missing" && need.reason === "not_cited") {
      return { icon: MoveRight, text: t("courses.figureNeeds.row.notCited"), tone: TONE_WARN };
    }
    const candidate = candidateOf.get(need.need_id);
    if (candidate) {
      return {
        icon: PlusCircle,
        text:
          candidate.source_kind === "uploaded"
            ? t("courses.figureNeeds.row.availableDocuments")
            : t("courses.figureNeeds.row.availableLiterature"),
        tone: TONE_INFO,
      };
    }
    if (loadingCandidates) {
      return { icon: Loader2, text: t("courses.figureNeeds.row.searching"), tone: "text-muted-foreground" };
    }
    if (need.status === "missing") {
      return { icon: SearchX, text: t("courses.figureNeeds.row.planGone"), tone: TONE_WARN };
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
      tone: TONE_WARN,
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
  const allDismissed = !!summary && summary.musts === 0 && summary.shoulds === 0;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/30 px-3 py-2">
        <div className="space-y-0.5 text-sm">
          {allDismissed && summary && (
            <div className="font-medium">
              {t("courses.figureNeeds.state.allDismissed", { count: summary.dismissed })}
            </div>
          )}
          {summary && summary.musts > 0 && (
            <div className="font-medium">
              {t("courses.figureNeeds.summary.musts", {
                placed: summary.musts_placed,
                count: summary.musts,
              })}
            </div>
          )}
          {summary && summary.shoulds > 0 && (
            <div className={cn(summary.musts > 0 ? "text-muted-foreground" : "font-medium")}>
              {t("courses.figureNeeds.summary.shoulds", {
                placed: summary.shoulds_placed,
                count: summary.shoulds,
              })}
            </div>
          )}
          {draftCount > 0 && (
            <div className={cn("text-xs", TONE_OK)}>
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
      <p className="text-xs text-muted-foreground">{t("courses.figureNeeds.howItWorks")}</p>

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
                !inserted[need.need_id] &&
                need.status !== "dismissed" &&
                !(need.linked && need.status === "placed");
              const canRestore = need.status === "dismissed" || !!need.linked;
              return (
                <li key={need.need_id} className="rounded-md border px-3 py-2">
                  <div className="flex items-start gap-2">
                    <Icon
                      className={cn(
                        "mt-0.5 size-4 shrink-0",
                        state.tone,
                        Icon === Loader2 && "animate-spin",
                      )}
                      aria-hidden="true"
                    />
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
                      <SourceFigureThumbnail figureId={candidate.figure_id} alt="" />
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
