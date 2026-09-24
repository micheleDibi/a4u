import type { CourseDocumentOut } from "@/api/courses";

/** Formati da cui il backend estrae le figure di fonte. */
export const FIGURE_EXTRACTABLE_MIME = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
];

/** Documento da cui si possono chiedere le figure (non escluso: le fonti
 *  riservate sono materiale del docente; formato supportato; estrazione mai
 *  chiesta, fallita, saltata perché era spenta o saltata con la regola
 *  precedente sulle fonti riservate). */
export function canRequestFigures(d: CourseDocumentOut): boolean {
  return (
    d.citation_policy !== "excluded" &&
    FIGURE_EXTRACTABLE_MIME.includes(d.mime_type) &&
    (d.figures_status === null ||
      d.figures_status === "failed" ||
      (d.figures_status === "skipped" &&
        (d.figures_error_code === "extraction_disabled" ||
          d.figures_error_code === "policy_content_only")) ||
      canResumeFigures(d))
  );
}

/** Estrazione pronta con pagine ancora da analizzare (tetto di pagine di
 *  una versione precedente): la richiesta riprende dal punto raggiunto. */
export function canResumeFigures(d: CourseDocumentOut): boolean {
  const total = d.figures_pages_total ?? 0;
  const next = d.figures_progress?.next_page ?? 0;
  return (
    d.figures_status === "ready" &&
    d.figures_coverage === "partial" &&
    next > 0 &&
    next <= total
  );
}
