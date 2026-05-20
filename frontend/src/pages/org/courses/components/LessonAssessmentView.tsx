import { useTranslation } from "react-i18next";
import { CheckCircle2 } from "lucide-react";

import type { LessonAssessmentRaw } from "@/api/courses";
import { cn } from "@/lib/utils";

interface Props {
  assessment: LessonAssessmentRaw;
}

/**
 * Render read-only di una lezione di verifica delle competenze: elenco
 * di domande a scelta multipla (con l'opzione corretta evidenziata) e
 * di domande aperte (con la traccia di risposta attesa).
 */
export function LessonAssessmentView({ assessment }: Props) {
  const { t } = useTranslation();
  const mc = assessment.multiple_choice_questions ?? [];
  const open = assessment.open_questions ?? [];

  if (mc.length === 0 && open.length === 0) {
    return (
      <article className="rounded-lg border bg-card px-6 py-8 text-sm text-muted-foreground shadow-sm">
        {t("courses.lessonsContent.assessment.render.empty")}
      </article>
    );
  }

  return (
    <article className="space-y-8 rounded-lg border bg-card px-6 py-8 shadow-sm sm:px-10 sm:py-10">
      {mc.length > 0 && (
        <section className="space-y-5">
          <h2 className="text-lg font-semibold">
            {t("courses.lessonsContent.assessment.render.mcTitle")}
          </h2>
          <ol className="space-y-6">
            {mc.map((q, i) => (
              <li key={q.question_id} className="space-y-2">
                <p className="font-medium leading-relaxed">
                  <span className="text-muted-foreground">{i + 1}. </span>
                  {q.text}
                </p>
                <ul className="space-y-1.5 ps-5">
                  {q.options.map((opt) => {
                    const correct = opt.option_id === q.correct_option_id;
                    return (
                      <li
                        key={opt.option_id}
                        className={cn(
                          "flex items-start gap-2 text-sm",
                          correct &&
                            "font-medium text-emerald-700 dark:text-emerald-400",
                        )}
                      >
                        <span className="font-mono">{opt.option_id}.</span>
                        <span className="flex-1">{opt.text}</span>
                        {correct && (
                          <span className="inline-flex shrink-0 items-center gap-1 text-xs">
                            <CheckCircle2 className="size-3.5" />
                            {t(
                              "courses.lessonsContent.assessment.render.correctAnswer",
                            )}
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ol>
        </section>
      )}

      {open.length > 0 && (
        <section className="space-y-5">
          <h2 className="text-lg font-semibold">
            {t("courses.lessonsContent.assessment.render.openTitle")}
          </h2>
          <ol className="space-y-4">
            {open.map((q, i) => (
              <li key={q.question_id} className="space-y-1.5">
                <p className="font-medium leading-relaxed">
                  <span className="text-muted-foreground">{i + 1}. </span>
                  {q.text}
                </p>
                <div className="rounded-md border border-dashed bg-muted/30 px-3 py-2">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {t(
                      "courses.lessonsContent.assessment.render.expectedAnswer",
                    )}
                  </span>
                  <p className="mt-1 whitespace-pre-line text-sm text-muted-foreground">
                    {q.expected_answer}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}

export default LessonAssessmentView;
