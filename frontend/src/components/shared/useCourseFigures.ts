import { useQuery } from "@tanstack/react-query";

import { coursesApi } from "@/api/courses";
import { useCourseRef } from "@/contexts/CourseRefContext";

/** Catalogo delle figure di fonte del corso corrente, condiviso fra tutte
 *  le figure della pagina (una sola richiesta, `staleTime` 60 s). */
export function useCourseFigures() {
  const courseRef = useCourseRef();
  return useQuery({
    queryKey: ["document-figures", courseRef?.orgId, courseRef?.courseId],
    queryFn: () => coursesApi.documentFigures.list(courseRef!.orgId, courseRef!.courseId),
    enabled: Boolean(courseRef),
    staleTime: 60_000,
  });
}
