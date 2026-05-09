/**
 * Stale-detection helpers.
 *
 * Logic: l'utente può modificare manualmente moduli/lezioni (Fase 1),
 * struttura lezione (Fase 2) o contenuto lezione (Fase 3) DOPO che la
 * generazione AI a valle è già stata fatta. Confrontiamo i timestamp
 * `*_modified_at` (set SOLO da CRUD manuale) con i `*_generated_at`
 * (set SOLO dai worker AI) per dedurre se qualcosa a valle è disallineato.
 *
 * Comportamento: il segnale di staleness è SUGGERIMENTO ("forse vuoi
 * rigenerare"), non un blocco — il vecchio output resta visibile e
 * scaricabile. La rigenerazione costa OpenAI quindi richiede conferma
 * esplicita dell'utente.
 */
import type { CourseLessonOut, CourseModuleOut } from "@/api/courses";

function isAfter(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  return new Date(a).getTime() > new Date(b).getTime();
}

/**
 * La struttura lezioni del modulo è stale se il modulo è stato modificato
 * manualmente (titolo/descrizione/lezioni di architettura) DOPO l'ultima
 * generazione AI di Fase 2.
 */
export function isStructureStale(module: CourseModuleOut): boolean {
  if (!module.lessons_structure_generated_at) return false;
  return isAfter(
    module.architecture_modified_at,
    module.lessons_structure_generated_at,
  );
}

/**
 * Il contenuto lezione è stale se:
 * - la struttura della lezione è stata modificata dopo il content, oppure
 * - l'architettura del modulo padre è stata modificata dopo il content.
 */
export function isContentStale(
  lesson: CourseLessonOut,
  parentModule: CourseModuleOut,
): boolean {
  if (!lesson.content_generated_at) return false;
  if (isAfter(lesson.lesson_structure_modified_at, lesson.content_generated_at))
    return true;
  if (isAfter(parentModule.architecture_modified_at, lesson.content_generated_at))
    return true;
  return false;
}

/**
 * Il PDF è stale se:
 * - il contenuto è stato rigenerato dall'AI dopo il PDF, oppure
 * - il contenuto è stato modificato manualmente dopo il PDF.
 *
 * Si applica solo a PDF già pronti (`pdf_status === 'ready'`).
 */
export function isPdfStale(lesson: CourseLessonOut): boolean {
  if (lesson.pdf_status !== "ready") return false;
  if (!lesson.pdf_generated_at) return false;
  if (isAfter(lesson.content_generated_at, lesson.pdf_generated_at)) return true;
  if (isAfter(lesson.content_modified_at, lesson.pdf_generated_at)) return true;
  return false;
}

/**
 * Le slide sono stale se è cambiato qualcosa a monte:
 * - il contenuto è stato rigenerato dall'AI dopo le slide, oppure
 * - il contenuto è stato modificato manualmente dopo le slide, oppure
 * - la struttura della lezione è stata modificata dopo le slide, oppure
 * - l'architettura del modulo padre è stata modificata dopo le slide.
 *
 * Si applica solo a slide già generate (`slides_generated_at` non null).
 */
export function isSlidesStale(
  lesson: CourseLessonOut,
  parentModule: CourseModuleOut,
): boolean {
  if (!lesson.slides_generated_at) return false;
  if (isAfter(lesson.content_generated_at, lesson.slides_generated_at))
    return true;
  if (isAfter(lesson.content_modified_at, lesson.slides_generated_at))
    return true;
  if (isAfter(lesson.lesson_structure_modified_at, lesson.slides_generated_at))
    return true;
  if (isAfter(parentModule.architecture_modified_at, lesson.slides_generated_at))
    return true;
  return false;
}

/**
 * Il PDF delle slide è stale se le slide sono state rigenerate o
 * modificate manualmente dopo l'ultimo export PDF slide.
 *
 * Si applica solo a PDF già pronti (`slides_pdf_status === 'ready'`).
 */
export function isSlidesPdfStale(lesson: CourseLessonOut): boolean {
  if (lesson.slides_pdf_status !== "ready") return false;
  if (!lesson.slides_pdf_generated_at) return false;
  if (isAfter(lesson.slides_generated_at, lesson.slides_pdf_generated_at))
    return true;
  if (isAfter(lesson.slides_modified_at, lesson.slides_pdf_generated_at))
    return true;
  return false;
}
