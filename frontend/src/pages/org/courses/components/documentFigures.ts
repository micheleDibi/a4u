import type { CourseDocumentOut } from "@/api/courses";

/** Formati da cui il backend estrae le figure di fonte. */
export const FIGURE_EXTRACTABLE_MIME = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
];

/** Documento da cui si possono chiedere le figure (citabile, formato
 *  supportato, estrazione mai chiesta o fallita). */
export function canRequestFigures(d: CourseDocumentOut): boolean {
  return (
    d.citation_policy === "citable" &&
    FIGURE_EXTRACTABLE_MIME.includes(d.mime_type) &&
    (d.figures_status === null || d.figures_status === "failed")
  );
}
