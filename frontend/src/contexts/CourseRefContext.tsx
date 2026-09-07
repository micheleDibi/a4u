import { createContext, useContext } from "react";

/**
 * Riferimento al corso corrente (`orgId`, `courseId`) per i componenti di
 * rendering che chiamano il backend, oggi `FunctionFigure` (anteprima
 * `POST /lesson-assets/render-function`).
 *
 * Fornito dai container `CourseLessonContentView` e `CourseLessonSlidesView`
 * (A21): i montaggi di `LessonContentView`/`LessonSlidesView` stanno nelle
 * righe di lezione, che non hanno `orgId`/`courseId`, e un prop-drilling di
 * cinque livelli per lato avrebbe toccato il comparatore `memo` di
 * `LessonContentView` (che resta sul solo `content`). Senza provider il
 * valore è `null` e il componente mostra il placeholder
 * `courses.figures.missing`.
 */
export interface CourseRef {
  orgId: string;
  courseId: string;
}

export const CourseRefContext = createContext<CourseRef | null>(null);

export function useCourseRef(): CourseRef | null {
  return useContext(CourseRefContext);
}
