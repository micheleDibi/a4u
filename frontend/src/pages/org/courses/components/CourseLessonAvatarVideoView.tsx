import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertCircle,
  CheckCircle2,
  Clapperboard,
  Download,
  Hourglass,
  Loader2,
  PlayCircle,
  RotateCcw,
  Sparkles,
  StopCircle,
} from "lucide-react";

import type { CourseOut, LessonAvatarVideoStatusOut } from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useBatchEta } from "@/hooks/useBatchEta";
import {
  useCancelAllAvatarVideos,
  useCancelLessonAvatarVideo,
  useCourseAvatarVideoStatus,
  useGenerateAllAvatarVideos,
  useGenerateLessonAvatarVideo,
} from "@/hooks/useLessonAvatarVideo";
import { extractApiError } from "@/lib/errors";
import { formatDuration } from "@/lib/formatDuration";

interface Props {
  course: CourseOut;
  canGenerate: boolean;
  orgId: string;
}

/**
 * Vista della scheda "Video con Avatar" (Fase 6b §9b). Prende il video
 * MP4 già generato della lezione e ci sovrappone in basso a destra un
 * avatar parlante con lip-sync MuseTalk, sincronizzato sull'audio del
 * video stesso.
 *
 * Mirror del tab "Video": aggregate progress per il batch, card per
 * lezione con status/progress/player, bottoni Genera/Rigenera/Annulla/
 * Scarica. Polling automatico ogni 2s via `useCourseAvatarVideoStatus`.
 */
export function CourseLessonAvatarVideoView({
  course,
  canGenerate,
  orgId,
}: Props) {
  const { t } = useTranslation();
  const statusQuery = useCourseAvatarVideoStatus(orgId, course.id);
  const generateLessonMut = useGenerateLessonAvatarVideo();
  const generateAllMut = useGenerateAllAvatarVideos();
  const cancelLessonMut = useCancelLessonAvatarVideo();
  const cancelAllMut = useCancelAllAvatarVideos();

  const data = statusQuery.data;
  const items = useMemo(() => data?.items ?? [], [data]);
  const total = data?.total ?? 0;
  const eligible = data?.eligible_count ?? 0;
  const inFlight =
    (data?.pending_count ?? 0) + (data?.processing_count ?? 0);
  const anyActive = inFlight > 0;
  const failedCount = data?.failed_count ?? 0;
  const avatarClipsReady = data?.avatar_clips_ready ?? false;

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

  const phaseLabel = (phase: LessonAvatarVideoStatusOut["progress_phase"]) => {
    switch (phase) {
      case "preparing":
        return t("courses.avatarVideo.phases.preparing");
      case "lipsync":
        return t("courses.avatarVideo.phases.lipsync");
      case "overlay":
        return t("courses.avatarVideo.phases.overlay");
      default:
        return "";
    }
  };

  const statusBadge = (it: LessonAvatarVideoStatusOut) => {
    switch (it.status) {
      case "ready":
        return (
          <Badge variant="default" className="gap-1">
            <CheckCircle2 className="size-3" />
            {t("courses.avatarVideo.status.completed")}
          </Badge>
        );
      case "processing":
        return (
          <Badge variant="secondary" className="gap-1">
            <Loader2 className="size-3 animate-spin" />
            {t("courses.avatarVideo.status.processing")}
          </Badge>
        );
      case "pending":
        return (
          <Badge variant="secondary" className="gap-1">
            <Hourglass className="size-3" />
            {t("courses.avatarVideo.status.pending")}
          </Badge>
        );
      case "failed":
        return (
          <Badge variant="destructive" className="gap-1">
            <AlertCircle className="size-3" />
            {t("courses.avatarVideo.status.failed")}
          </Badge>
        );
      case "cancelled":
        return (
          <Badge variant="outline" className="gap-1">
            <StopCircle className="size-3" />
            {t("courses.avatarVideo.status.cancelled")}
          </Badge>
        );
      default:
        return (
          <Badge variant="outline">
            {t("courses.avatarVideo.status.empty")}
          </Badge>
        );
    }
  };

  const toastError = (err: unknown) => {
    const e = extractApiError(err);
    toast.error(
      e.code
        ? t(`courses.avatarVideo.errors.${e.code}`, {
            defaultValue: e.message,
          })
        : e.message,
    );
  };

  const onGenerateOne = (lessonId: string) => {
    generateLessonMut.mutate(
      { orgId, courseId: course.id, lessonId },
      { onError: toastError },
    );
  };

  const onCancelOne = (lessonId: string) => {
    cancelLessonMut.mutate(
      { orgId, courseId: course.id, lessonId },
      { onError: (err) => toast.error(extractApiError(err).message) },
    );
  };

  const onGenerateAll = () => {
    generateAllMut.mutate(
      { orgId, courseId: course.id },
      { onError: toastError },
    );
  };

  const onCancelAll = () => {
    cancelAllMut.mutate(
      { orgId, courseId: course.id },
      { onError: (err) => toast.error(extractApiError(err).message) },
    );
  };

  // Map degli items per lessonId per evitare lookup O(n).
  const itemByLessonId = useMemo(() => {
    const m = new Map<string, LessonAvatarVideoStatusOut>();
    for (const it of items) m.set(it.lesson_id, it);
    return m;
  }, [items]);

  return (
    <div className="space-y-4">
      {/* Banner pre-requisiti — avatar dell'assegnatario senza clip */}
      {!avatarClipsReady && total > 0 && (
        <Card className="border-amber-300/60 bg-amber-50/40 dark:bg-amber-900/10">
          <CardContent className="flex items-center gap-3 py-3 text-sm">
            <AlertCircle className="size-4 shrink-0 text-amber-600" />
            <span className="flex-1">
              {t("courses.avatarVideo.errors.avatar_clips_not_ready")}
            </span>
          </CardContent>
        </Card>
      )}

      {/* Aggregate banner */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3 pb-3">
          <div className="flex items-center gap-2">
            <Clapperboard className="size-5 text-primary" />
            <h2 className="text-lg font-semibold">
              {t("courses.avatarVideo.tabTitle")}
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
                {t("courses.avatarVideo.actions.cancelAll")}
              </Button>
            )}
            {canGenerate && eligible > 0 && !anyActive && (
              <Button
                size="sm"
                onClick={onGenerateAll}
                disabled={generateAllMut.isPending}
              >
                <Sparkles className="size-4" />
                {t("courses.avatarVideo.actions.generateAll", {
                  count: eligible,
                })}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3 pb-4">
          <p className="text-sm text-muted-foreground">
            {t("courses.avatarVideo.description")}
          </p>
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div className="space-x-2">
              <span className="font-medium">
                {data?.ready_count ?? 0}/{total}
              </span>
              <span className="text-muted-foreground">
                {t("courses.avatarVideo.completedLabel")}
              </span>
              {failedCount > 0 && (
                <Badge variant="destructive" className="ml-2">
                  {failedCount} {t("courses.avatarVideo.failedSuffix")}
                </Badge>
              )}
            </div>
            {anyActive && (
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span>{t("courses.avatarVideo.inFlight", { n: inFlight })}</span>
                {eta.etaMs !== null && (
                  <span>
                    {t("courses.avatarVideo.etaLabel")}:{" "}
                    {formatDuration(eta.etaMs)}
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
            <Clapperboard className="size-4" />
            {t("courses.avatarVideo.noLessons")}
          </CardContent>
        </Card>
      )}

      {/* Per-lesson cards, raggruppate per modulo. */}
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
                item.lesson_video_ready &&
                avatarClipsReady &&
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
                        {!item.lesson_video_ready && (
                          <div className="text-xs text-amber-600">
                            {t("courses.avatarVideo.errors.lesson_video_not_ready")}
                          </div>
                        )}
                        {item.is_stale && item.status === "ready" && (
                          <div className="text-xs text-amber-600">
                            {t("courses.avatarVideo.staleAlert")}
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
                          <Button size="sm" variant="outline" asChild>
                            <a
                              href={item.video_url}
                              download={`${lesson.lesson_code}-avatar.mp4`}
                            >
                              <Download className="size-4" />
                              {t("courses.avatarVideo.actions.download")}
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
                            {t("courses.avatarVideo.actions.cancel")}
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
                                {t("courses.avatarVideo.actions.regenerate")}
                              </>
                            ) : (
                              <>
                                <Sparkles className="size-4" />
                                {t("courses.avatarVideo.actions.generate")}
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
                        {item.tokens.audio_duration_s !== undefined &&
                          item.tokens.audio_duration_s !== null && (
                            <span>
                              {t("courses.avatarVideo.tokens.duration")}:{" "}
                              {formatDuration(
                                item.tokens.audio_duration_s * 1000,
                              )}
                            </span>
                          )}
                        {item.tokens.num_ready_clips !== undefined && (
                          <span>
                            {t("courses.avatarVideo.tokens.clips")}:{" "}
                            {item.tokens.num_ready_clips}
                          </span>
                        )}
                        {item.tokens.file_size_bytes !== undefined && (
                          <span>
                            {t("courses.avatarVideo.tokens.size")}:{" "}
                            {(
                              item.tokens.file_size_bytes /
                              1024 /
                              1024
                            ).toFixed(1)}{" "}
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
