import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";

import { coursesApi, type LessonVideoBatchOut, type LessonVideoStatusOut } from "@/api/courses";

const REFETCH_ACTIVE_MS = 2_000;
const REFETCH_IDLE_MS = false as const;
const STALE_MS = 1_000;

const courseStatusKey = (orgId: string, courseId: string) =>
  ["course-video-status", orgId, courseId] as const;

const lessonStatusKey = (
  orgId: string,
  courseId: string,
  lessonId: string,
) => ["lesson-video-status", orgId, courseId, lessonId] as const;

/**
 * Polling-friendly status di una singola lezione. Refetch ogni 2s mentre
 * il worker è in flight (`pending`/`processing`), altrimenti si ferma.
 */
export function useLessonVideoStatus(
  orgId: string | null | undefined,
  courseId: string | null | undefined,
  lessonId: string | null | undefined,
) {
  return useQuery({
    queryKey: lessonStatusKey(orgId ?? "", courseId ?? "", lessonId ?? ""),
    queryFn: () =>
      coursesApi.lessonVideo.getLessonStatus(orgId!, courseId!, lessonId!),
    enabled: !!orgId && !!courseId && !!lessonId,
    staleTime: STALE_MS,
    refetchInterval: (q) => {
      const data = q.state.data as LessonVideoStatusOut | undefined;
      if (!data) return REFETCH_ACTIVE_MS;
      return data.status === "pending" || data.status === "processing"
        ? REFETCH_ACTIVE_MS
        : REFETCH_IDLE_MS;
    },
  });
}

/**
 * Aggregato pagina-corso: usato per la card "Genera tutti i video" e la
 * lista per-lezione. Refetch ogni 2s se almeno una lezione è in flight.
 */
export function useCourseVideoStatus(
  orgId: string | null | undefined,
  courseId: string | null | undefined,
) {
  return useQuery({
    queryKey: courseStatusKey(orgId ?? "", courseId ?? ""),
    queryFn: () => coursesApi.lessonVideo.getCourseStatus(orgId!, courseId!),
    enabled: !!orgId && !!courseId,
    staleTime: STALE_MS,
    refetchInterval: (q) => {
      const data = q.state.data as LessonVideoBatchOut | undefined;
      if (!data) return REFETCH_ACTIVE_MS;
      const inFlight =
        (data.pending_count ?? 0) + (data.processing_count ?? 0);
      return inFlight > 0 ? REFETCH_ACTIVE_MS : REFETCH_IDLE_MS;
    },
  });
}

/**
 * Mutations: invalidano sia lo status puntuale che l'aggregato per
 * mantenere coerenti la tab Video e il banner pagina-corso.
 */
export function useGenerateLessonVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      orgId: string;
      courseId: string;
      lessonId: string;
    }) =>
      coursesApi.lessonVideo.generateLesson(
        vars.orgId,
        vars.courseId,
        vars.lessonId,
      ),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({
        queryKey: courseStatusKey(vars.orgId, vars.courseId),
      });
      void qc.invalidateQueries({
        queryKey: lessonStatusKey(vars.orgId, vars.courseId, vars.lessonId),
      });
    },
  });
}

export function useGenerateAllVideos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { orgId: string; courseId: string }) =>
      coursesApi.lessonVideo.generateBatch(vars.orgId, vars.courseId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({
        queryKey: courseStatusKey(vars.orgId, vars.courseId),
      });
      void qc.invalidateQueries({
        queryKey: ["lesson-video-status", vars.orgId, vars.courseId],
      });
    },
  });
}

export function useCancelLessonVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      orgId: string;
      courseId: string;
      lessonId: string;
    }) =>
      coursesApi.lessonVideo.cancelLesson(
        vars.orgId,
        vars.courseId,
        vars.lessonId,
      ),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({
        queryKey: courseStatusKey(vars.orgId, vars.courseId),
      });
      void qc.invalidateQueries({
        queryKey: lessonStatusKey(vars.orgId, vars.courseId, vars.lessonId),
      });
    },
  });
}

export function useCancelAllVideos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { orgId: string; courseId: string }) =>
      coursesApi.lessonVideo.cancelBatch(vars.orgId, vars.courseId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({
        queryKey: courseStatusKey(vars.orgId, vars.courseId),
      });
      void qc.invalidateQueries({
        queryKey: ["lesson-video-status", vars.orgId, vars.courseId],
      });
    },
  });
}
