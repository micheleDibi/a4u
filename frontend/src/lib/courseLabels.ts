import { useTranslation } from "react-i18next";

/**
 * Etichette leggibili per moduli/lezioni a partire dai codici tecnici.
 *
 * I codici interni sono `M{n}` (modulo) e `M{n}.L{m}` (lezione); l'utente
 * non deve mai vederli (niente "M1.L1"). Questo hook centralizza la
 * conversione in "Modulo N" / "Lezione N" usando le chiavi i18n già
 * esistenti (`courses.architecture.moduleLabel` / `lessonLabel`).
 *
 * Pattern originariamente duplicato in `CourseLessonStructureView` e
 * `CourseLessonContentView`; estratto qui per riuso (tab Video / Avatar).
 */
export function useCourseLabels() {
  const { t } = useTranslation();

  const moduleLabel = (code: string) => {
    const m = code.match(/^M(\d+)$/);
    return m ? t("courses.architecture.moduleLabel", { n: m[1] }) : code;
  };

  const lessonLabel = (code: string) => {
    const m = code.match(/^M\d+\.L(\d+)$/);
    return m ? t("courses.architecture.lessonLabel", { n: m[1] }) : code;
  };

  return { moduleLabel, lessonLabel };
}
