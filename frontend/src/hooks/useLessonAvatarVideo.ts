import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  coursesApi,
  type LessonAvatarVideoBatchOut,
} from "@/api/courses";

const REFETCH_ACTIVE_MS = 2_000;
const REFETCH_IDLE_MS = false as const;
const STALE_MS = 1_000;

const courseStatusKey = (orgId: string, courseId: string) =>
  ["course-avatar-video-status", orgId, courseId] as const;

const lessonStatusKey = (orgId: string, courseId: string, lessonId: string) =>
  ["lesson-avatar-video-status", orgId, courseId, lessonId] as const;

/**
 * Aggregato pagina-corso per la scheda «Video con Avatar»: card "Genera
 * tutti" + lista per-lezione. Refetch ogni 2s se almeno una lezione è in
 * flight (`pending`/`processing`), altrimenti si ferma.
 */
export function useCourseAvatarVideoStatus(
  orgId: string | null | undefined,
  courseId: string | null | undefined,
) {
  return useQuery({
    queryKey: courseStatusKey(orgId ?? "", courseId ?? ""),
    queryFn: () =>
      coursesApi.lessonAvatarVideo.getCourseStatus(orgId!, courseId!),
    enabled: !!orgId && !!courseId,
    staleTime: STALE_MS,
    refetchInterval: (q) => {
      const data = q.state.data as LessonAvatarVideoBatchOut | undefined;
      if (!data) return REFETCH_ACTIVE_MS;
      const inFlight =
        (data.pending_count ?? 0) + (data.processing_count ?? 0);
      return inFlight > 0 ? REFETCH_ACTIVE_MS : REFETCH_IDLE_MS;
    },
  });
}

/**
 * Mutations: invalidano sia lo status puntuale che l'aggregato per
 * mantenere coerenti la scheda e il banner pagina-corso.
 */
export function useGenerateLessonAvatarVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      orgId: string;
      courseId: string;
      lessonId: string;
    }) =>
      coursesApi.lessonAvatarVideo.generateLesson(
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

export function useGenerateAllAvatarVideos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { orgId: string; courseId: string }) =>
      coursesApi.lessonAvatarVideo.generateBatch(vars.orgId, vars.courseId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({
        queryKey: courseStatusKey(vars.orgId, vars.courseId),
      });
      void qc.invalidateQueries({
        queryKey: ["lesson-avatar-video-status", vars.orgId, vars.courseId],
      });
    },
  });
}

export function useCancelLessonAvatarVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      orgId: string;
      courseId: string;
      lessonId: string;
    }) =>
      coursesApi.lessonAvatarVideo.cancelLesson(
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

export function useCancelAllAvatarVideos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { orgId: string; courseId: string }) =>
      coursesApi.lessonAvatarVideo.cancelBatch(vars.orgId, vars.courseId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({
        queryKey: courseStatusKey(vars.orgId, vars.courseId),
      });
      void qc.invalidateQueries({
        queryKey: ["lesson-avatar-video-status", vars.orgId, vars.courseId],
      });
    },
  });
}
