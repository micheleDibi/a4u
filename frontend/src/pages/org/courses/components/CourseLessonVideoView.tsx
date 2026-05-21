import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  FilmIcon,
  Hourglass,
  Languages,
  Loader2,
  PlayCircle,
  RotateCcw,
  Sparkles,
  StopCircle,
} from "lucide-react";

import {
  coursesApi,
  isXttsLanguage,
  XTTS_SUPPORTED_LANGUAGES,
  type CourseOut,
  type LessonVideoStatusOut,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useBatchEta } from "@/hooks/useBatchEta";
import {
  useCancelAllVideos,
  useCancelLessonVideo,
  useCourseVideoStatus,
  useGenerateAllVideos,
  useGenerateLessonVideo,
} from "@/hooks/useLessonVideo";
import { extractApiError } from "@/lib/errors";
import { formatDuration } from "@/lib/formatDuration";
import { flagFor } from "@/i18n/flags";

interface Props {
  course: CourseOut;
  canGenerate: boolean;
  orgId: string;
}

/**
 * Vista del tab "Video" (Fase 6 §9). Mirror semplificato del tab Discorso:
 * - aggregate progress per il batch
 * - card per lezione con status, progress phase, player HTML5
 * - bottoni Genera/Rigenera/Annulla/Scarica per ogni lezione
 *
 * Polling automatico ogni 2s tramite `useCourseVideoStatus` quando almeno
 * una lezione è `pending`/`processing` (vedi hook). Tutti gli aggregati
 * sono calcolati lato BE in `LessonVideoBatchOut`.
 */
export function CourseLessonVideoView({
  course,
  canGenerate,
  orgId,
}: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const statusQuery = useCourseVideoStatus(orgId, course.id);
  const generateLessonMut = useGenerateLessonVideo();
  const generateAllMut = useGenerateAllVideos();
  const cancelLessonMut = useCancelLessonVideo();
  const cancelAllMut = useCancelAllVideos();

  // Mutation lingua TTS — patch parziale del corso. Invalida la query del
  // corso e quella del batch video per riallineare l'UI.
  const updateVideoLangMut = useMutation({
    mutationFn: (next: string | null) =>
      coursesApi.update(orgId, course.id, {
        video_language_code: next === null ? "" : next,
      }),
    onSuccess: (fresh) => {
      qc.setQueryData(["courses", "detail", orgId, course.id], fresh);
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  // Lingua effettivamente usata per il TTS (override → corso → fallback "it").
  const effectiveVideoLang =
    course.video_language_code || course.language_code || "it";
  // Lingua di default del corso supportata da XTTS? Se no, l'override è
  // obbligatorio: mostriamo un banner di avviso.
  const courseLangSupported = isXttsLanguage(course.language_code);

  const data = statusQuery.data;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const eligible = data?.eligible_count ?? 0;
  const inFlight =
    (data?.pending_count ?? 0) + (data?.processing_count ?? 0);
  const anyActive = inFlight > 0;
  const failedCount = data?.failed_count ?? 0;

  const eta = useBatchEta(
    items.map((it) => ({
      status: it.status === "ready" ? "ready" : it.status,
      completedAt: it.generated_at,
    })),
  );

  const aggregatePercent = useMemo(() => {
    if (total === 0) return 0;
    let sum = 0;
    for (const it of items) {
      if (it.status === "ready") sum += 100;
      else if (it.status === "pending" || it.status === "processing")
        sum += it.progress;
    }
    return Math.round(sum / total);
  }, [items, total]);

  const phaseLabel = (phase: LessonVideoStatusOut["progress_phase"]) => {
    switch (phase) {
      case "tts":
        return t("courses.video.phases.tts");
      case "rendering_slides":
        return t("courses.video.phases.slides");
      case "encoding":
        return t("courses.video.phases.encode");
      case "preparing":
        return t("courses.video.phases.preparing");
      default:
        return "";
    }
  };

  const statusBadge = (it: LessonVideoStatusOut) => {
    switch (it.status) {
      case "ready":
        return (
          <Badge variant="default" className="gap-1">
            <CheckCircle2 className="size-3" />
            {t("courses.video.status.completed")}
          </Badge>
        );
      case "processing":
        return (
          <Badge variant="secondary" className="gap-1">
            <Loader2 className="size-3 animate-spin" />
            {t("courses.video.status.processing")}
          </Badge>
        );
      case "pending":
        return (
          <Badge variant="secondary" className="gap-1">
            <Hourglass className="size-3" />
            {t("courses.video.status.pending")}
          </Badge>
        );
      case "failed":
        return (
          <Badge variant="destructive" className="gap-1">
            <AlertCircle className="size-3" />
            {t("courses.video.status.failed")}
          </Badge>
        );
      case "cancelled":
        return (
          <Badge variant="outline" className="gap-1">
            <StopCircle className="size-3" />
            {t("courses.video.status.cancelled")}
          </Badge>
        );
      default:
        return (
          <Badge variant="outline">
            {t("courses.video.status.empty")}
          </Badge>
        );
    }
  };

  const onGenerateOne = (lessonId: string) => {
    generateLessonMut.mutate(
      { orgId, courseId: course.id, lessonId },
      {
        onError: (err) => {
          const e = extractApiError(err);
          toast.error(
            e.code
              ? t(`courses.video.errors.${e.code}`, { defaultValue: e.message })
              : e.message,
          );
        },
      },
    );
  };

  const onCancelOne = (lessonId: string) => {
    cancelLessonMut.mutate(
      { orgId, courseId: course.id, lessonId },
      {
        onError: (err) => {
          toast.error(extractApiError(err).message);
        },
      },
    );
  };

  const onGenerateAll = () => {
    generateAllMut.mutate(
      { orgId, courseId: course.id },
      {
        onError: (err) => {
          const e = extractApiError(err);
          toast.error(
            e.code
              ? t(`courses.video.errors.${e.code}`, { defaultValue: e.message })
              : e.message,
          );
        },
      },
    );
  };

  const onCancelAll = () => {
    cancelAllMut.mutate(
      { orgId, courseId: course.id },
      {
        onError: (err) => {
          toast.error(extractApiError(err).message);
        },
      },
    );
  };

  // Map degli items per lessonId per evitare lookup O(n).
  const itemByLessonId = useMemo(() => {
    const m = new Map<string, LessonVideoStatusOut>();
    for (const it of items) m.set(it.lesson_id, it);
    return m;
  }, [items]);

  // Pre-requisiti globali (banner sopra il batch).
  const firstItem = items[0];
  const voiceAvailable = firstItem?.voice_sample_available ?? false;

  return (
    <div className="space-y-4">
      {/* Selettore lingua TTS (Fase 6 §9 rifinitura). Sopra tutto, sempre
          visibile. La lingua effettiva del TTS è override (se valorizzato)
          altrimenti la lingua del corso. */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 py-4">
          <Languages className="size-5 shrink-0 text-primary" />
          <div className="flex-1 min-w-[200px]">
            <Label className="text-sm font-medium">
              {t("courses.video.languageLabel")}
            </Label>
            <p className="text-xs text-muted-foreground">
              {t("courses.video.languageHint")}
            </p>
          </div>
          <Select
            value={effectiveVideoLang}
            onValueChange={(v) => {
              // "default" è il sentinel UI per "ripristina da corso".
              if (v === "__course_default__") {
                updateVideoLangMut.mutate(null);
              } else {
                updateVideoLangMut.mutate(v);
              }
            }}
            disabled={!canGenerate || updateVideoLangMut.isPending}
          >
            <SelectTrigger className="w-[200px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {courseLangSupported && (
                <SelectItem value="__course_default__">
                  <span className="text-muted-foreground">
                    {t("courses.video.languageFollowCourse", {
                      lang: course.language_code?.toUpperCase() ?? "",
                    })}
                  </span>
                </SelectItem>
              )}
              {XTTS_SUPPORTED_LANGUAGES.map((code) => {
                const Flag = flagFor(code.split("-")[0]);
                return (
                  <SelectItem key={code} value={code}>
                    <span className="inline-flex items-center gap-2">
                      <Flag className="size-4" />
                      <span className="uppercase">{code}</span>
                    </span>
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {/* Banner: lingua corso non supportata da XTTS — l'utente deve scegliere
          esplicitamente un override compatibile. */}
      {!courseLangSupported && !course.video_language_code && (
        <Card className="border-amber-300/60 bg-amber-50/40 dark:bg-amber-900/10">
          <CardContent className="flex items-center gap-3 py-3 text-sm">
            <AlertCircle className="size-4 shrink-0 text-amber-600" />
            <span className="flex-1">
              {t("courses.video.errors.unsupported_language")}
            </span>
          </CardContent>
        </Card>
      )}

      {/* Banner pre-requisiti — voice sample mancante */}
      {!voiceAvailable && total > 0 && (
        <Card className="border-amber-300/60 bg-amber-50/40 dark:bg-amber-900/10">
          <CardContent className="flex items-center gap-3 py-3 text-sm">
            <AlertCircle className="size-4 shrink-0 text-amber-600" />
            <span className="flex-1">
              {t("courses.video.errors.voice_sample_missing")}
            </span>
          </CardContent>
        </Card>
      )}

      {/* Aggregate banner */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3 pb-3">
          <div className="flex items-center gap-2">
            <FilmIcon className="size-5 text-primary" />
            <h2 className="text-lg font-semibold">
              {t("courses.video.tabTitle")}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            {anyActive && canGenerate && (
              <Button
                size="sm"
                variant="outline"
                onClick={onCancelAll}
                disabled={cancelAllMut.isPending}
              >
                <StopCircle className="size-4" />
                {t("courses.video.actions.cancelAll")}
              </Button>
            )}
            {canGenerate && eligible > 0 && !anyActive && (
              <Button
                size="sm"
                onClick={onGenerateAll}
                disabled={generateAllMut.isPending || !voiceAvailable}
              >
                <Sparkles className="size-4" />
                {t("courses.video.actions.generateAll", { count: eligible })}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3 pb-4">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div className="space-x-2">
              <span className="font-medium">
                {data?.ready_count ?? 0}/{total}
              </span>
              <span className="text-muted-foreground">
                {t("courses.video.completedLabel")}
              </span>
              {failedCount > 0 && (
                <Badge variant="destructive" className="ml-2">
                  {failedCount} {t("courses.video.failedSuffix")}
                </Badge>
              )}
            </div>
            {anyActive && (
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span>
                  {t("courses.video.inFlight", { n: inFlight })}
                </span>
                {eta.etaMs !== null && (
                  <span>
                    {t("courses.video.etaLabel")}: {formatDuration(eta.etaMs)}
                  </span>
                )}
              </div>
            )}
          </div>
          <Progress value={aggregatePercent} />
        </CardContent>
      </Card>

      {total === 0 && (
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <FilmIcon className="size-4" />
            {t("courses.video.noLessons")}
          </CardContent>
        </Card>
      )}

      {/* Per-lesson cards (raggruppate per modulo, riusando l'ordine dei
          moduli del corso). */}
      {course.modules.map((module) => {
        const lessonsWithItems = module.lessons
          .map((l) => ({ lesson: l, item: itemByLessonId.get(l.id) }))
          .filter((x) => x.item);
        if (lessonsWithItems.length === 0) return null;
        return (
          <div key={module.id} className="space-y-2">
            <h3 className="text-sm font-semibold text-muted-foreground">
              {module.module_code} — {module.title}
            </h3>
            {lessonsWithItems.map(({ lesson, item }) => {
              if (!item) return null;
              const inProgress =
                item.status === "pending" || item.status === "processing";
              const canGen =
                canGenerate &&
                item.speech_approved &&
                item.slides_approved &&
                voiceAvailable &&
                !inProgress;
              return (
                <Card key={lesson.id}>
                  <CardContent className="space-y-3 py-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold">
                            {lesson.lesson_code} — {lesson.title}
                          </span>
                          {statusBadge(item)}
                        </div>
                        {!item.speech_approved && (
                          <div className="text-xs text-amber-600">
                            {t("courses.video.errors.speech_not_approved")}
                          </div>
                        )}
                        {!item.slides_approved && (
                          <div className="text-xs text-amber-600">
                            {t("courses.video.errors.slides_not_approved")}
                          </div>
                        )}
                        {item.is_stale && item.status === "ready" && (
                          <div className="text-xs text-amber-600">
                            {t("courses.video.staleAlert")}
                          </div>
                        )}
                        {item.error && item.status === "failed" && (
                          <div className="text-xs text-destructive">
                            {item.error}
                          </div>
                        )}
                      </div>
                      <div className="flex shrink-0 gap-2">
                        {item.status === "ready" && item.video_url && (
                          <Button
                            size="sm"
                            variant="outline"
                            asChild
                          >
                            <a
                              href={item.video_url}
                              download={`${lesson.lesson_code}.mp4`}
                            >
                              <Download className="size-4" />
                              {t("courses.video.actions.download")}
                            </a>
                          </Button>
                        )}
                        {inProgress && canGenerate && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => onCancelOne(lesson.id)}
                            disabled={cancelLessonMut.isPending}
                          >
                            <StopCircle className="size-4" />
                            {t("courses.video.actions.cancel")}
                          </Button>
                        )}
                        {canGen && (
                          <Button
                            size="sm"
                            onClick={() => onGenerateOne(lesson.id)}
                            disabled={generateLessonMut.isPending}
                          >
                            {item.status === "ready" ? (
                              <>
                                <RotateCcw className="size-4" />
                                {t("courses.video.actions.regenerate")}
                              </>
                            ) : (
                              <>
                                <Sparkles className="size-4" />
                                {t("courses.video.actions.generate")}
                              </>
                            )}
                          </Button>
                        )}
                      </div>
                    </div>

                    {inProgress && (
                      <div className="space-y-1">
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                          <span>{phaseLabel(item.progress_phase)}</span>
                          <span>{item.progress}%</span>
                        </div>
                        <Progress value={item.progress} />
                      </div>
                    )}

                    {item.status === "ready" && item.video_url && (
                      <div className="overflow-hidden rounded-md border">
                        <video
                          controls
                          preload="metadata"
                          className="aspect-[99/70] w-full bg-black"
                          src={item.video_url}
                        >
                          <PlayCircle className="size-12" />
                        </video>
                      </div>
                    )}

                    {item.status === "ready" && item.tokens && (
                      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                        {item.tokens.audio_duration_s !== undefined && (
                          <span>
                            {t("courses.video.tokens.duration")}:{" "}
                            {formatDuration(item.tokens.audio_duration_s * 1000)}
                          </span>
                        )}
                        {item.tokens.device && (
                          <span>
                            {t("courses.video.tokens.device")}:{" "}
                            {item.tokens.device.toUpperCase()}
                          </span>
                        )}
                        {item.tokens.file_size_bytes !== undefined && (
                          <span>
                            {t("courses.video.tokens.size")}:{" "}
                            {(item.tokens.file_size_bytes / 1024 / 1024).toFixed(
                              1,
                            )}{" "}
                            MB
                          </span>
                        )}
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
