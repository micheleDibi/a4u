import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";

import type {
  LessonAssessmentRaw,
  LessonAssessmentUpdateInput,
} from "@/api/courses";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

// Le opzioni vengono ri-lettarate A,B,C,... in submit: lo stato locale
// traccia l'opzione corretta per indice (`correctIndex`), non per id.
const OPTION_LETTERS = ["A", "B", "C", "D", "E", "F"];
const MAX_OPTIONS = OPTION_LETTERS.length;

interface EditMC {
  question_id: string;
  text: string;
  options: string[];
  correctIndex: number;
}

interface EditOpen {
  question_id: string;
  text: string;
  expected_answer: string;
}

interface Props {
  open: boolean;
  isPending: boolean;
  lessonLabel: string;
  initial: LessonAssessmentRaw;
  onClose: () => void;
  onSubmit: (payload: LessonAssessmentUpdateInput) => void;
}

function newQuestionId(): string {
  return `q-${crypto.randomUUID().slice(0, 8)}`;
}

/**
 * Editor dedicato della verifica delle competenze: domande a scelta
 * multipla (testo + opzioni + opzione corretta) e domande aperte (testo +
 * risposta attesa). Parent conditional-render → `useState` lazy-init.
 */
export function LessonAssessmentEditDialog({
  open,
  isPending,
  lessonLabel,
  initial,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();

  const [mc, setMc] = useState<EditMC[]>(() =>
    (initial.multiple_choice_questions ?? []).map((q) => {
      const idx = q.options.findIndex(
        (o) => o.option_id === q.correct_option_id,
      );
      return {
        question_id: q.question_id,
        text: q.text,
        options: q.options.map((o) => o.text),
        correctIndex: idx >= 0 ? idx : 0,
      };
    }),
  );
  const [openQ, setOpenQ] = useState<EditOpen[]>(() =>
    (initial.open_questions ?? []).map((q) => ({
      question_id: q.question_id,
      text: q.text,
      expected_answer: q.expected_answer,
    })),
  );

  // --- mutators: scelta multipla ---
  const patchMc = (idx: number, patch: Partial<EditMC>) =>
    setMc((prev) =>
      prev.map((q, i) => (i === idx ? { ...q, ...patch } : q)),
    );
  const setMcOption = (qi: number, oi: number, value: string) =>
    setMc((prev) =>
      prev.map((q, i) =>
        i === qi
          ? { ...q, options: q.options.map((o, j) => (j === oi ? value : o)) }
          : q,
      ),
    );
  const addMcOption = (qi: number) =>
    setMc((prev) =>
      prev.map((q, i) =>
        i === qi && q.options.length < MAX_OPTIONS
          ? { ...q, options: [...q.options, ""] }
          : q,
      ),
    );
  const removeMcOption = (qi: number, oi: number) =>
    setMc((prev) =>
      prev.map((q, i) => {
        if (i !== qi || q.options.length <= 2) return q;
        const options = q.options.filter((_, j) => j !== oi);
        let correctIndex = q.correctIndex;
        if (oi === correctIndex) correctIndex = 0;
        else if (oi < correctIndex) correctIndex -= 1;
        return { ...q, options, correctIndex };
      }),
    );
  const addMc = () =>
    setMc((prev) => [
      ...prev,
      {
        question_id: newQuestionId(),
        text: "",
        options: ["", "", "", ""],
        correctIndex: 0,
      },
    ]);
  const removeMc = (idx: number) =>
    setMc((prev) => prev.filter((_, i) => i !== idx));

  // --- mutators: domande aperte ---
  const patchOpen = (idx: number, patch: Partial<EditOpen>) =>
    setOpenQ((prev) =>
      prev.map((q, i) => (i === idx ? { ...q, ...patch } : q)),
    );
  const addOpen = () =>
    setOpenQ((prev) => [
      ...prev,
      { question_id: newQuestionId(), text: "", expected_answer: "" },
    ]);
  const removeOpen = (idx: number) =>
    setOpenQ((prev) => prev.filter((_, i) => i !== idx));

  const submit = () => {
    if (isPending) return;
    const payload: LessonAssessmentUpdateInput = {
      multiple_choice_questions: mc.map((q) => ({
        question_id: q.question_id,
        text: q.text.trim(),
        options: q.options.map((text, i) => ({
          option_id: OPTION_LETTERS[i],
          text: text.trim(),
        })),
        correct_option_id:
          OPTION_LETTERS[
            Math.min(Math.max(q.correctIndex, 0), q.options.length - 1)
          ],
      })),
      open_questions: openQ.map((q) => ({
        question_id: q.question_id,
        text: q.text.trim(),
        expected_answer: q.expected_answer.trim(),
      })),
    };
    onSubmit(payload);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            {t("courses.lessonsContent.assessment.editor.title", {
              lesson: lessonLabel,
            })}
          </DialogTitle>
          <DialogDescription>
            {t("courses.lessonsContent.assessment.editor.description")}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[65vh] pr-3">
          <div className="space-y-6 py-1">
            {/* Domande a scelta multipla */}
            <section className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">
                  {t("courses.lessonsContent.assessment.editor.mcSection")}
                </h3>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addMc}
                  disabled={isPending}
                >
                  <Plus className="size-4" />
                  {t("courses.lessonsContent.assessment.editor.addMc")}
                </Button>
              </div>
              {mc.map((q, qi) => (
                <div
                  key={q.question_id}
                  className="space-y-2 rounded-md border border-border bg-muted/10 p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-mono text-xs text-muted-foreground">
                      #{qi + 1}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-7 text-destructive"
                      onClick={() => removeMc(qi)}
                      disabled={isPending}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                  <Textarea
                    value={q.text}
                    onChange={(e) => patchMc(qi, { text: e.target.value })}
                    placeholder={t(
                      "courses.lessonsContent.assessment.editor.questionText",
                    )}
                    rows={2}
                    disabled={isPending}
                    className="resize-y"
                  />
                  <div className="space-y-1.5">
                    {q.options.map((opt, oi) => (
                      <div key={oi} className="flex items-center gap-2">
                        <label
                          className={cn(
                            "flex shrink-0 items-center gap-1.5 text-xs",
                            oi === q.correctIndex &&
                              "font-medium text-emerald-700 dark:text-emerald-400",
                          )}
                        >
                          <input
                            type="radio"
                            name={`correct-${q.question_id}`}
                            checked={oi === q.correctIndex}
                            onChange={() =>
                              patchMc(qi, { correctIndex: oi })
                            }
                            disabled={isPending}
                          />
                          <span className="font-mono">
                            {OPTION_LETTERS[oi]}
                          </span>
                        </label>
                        <Input
                          value={opt}
                          onChange={(e) =>
                            setMcOption(qi, oi, e.target.value)
                          }
                          placeholder={t(
                            "courses.lessonsContent.assessment.editor.optionText",
                          )}
                          disabled={isPending}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-7 shrink-0 text-destructive"
                          onClick={() => removeMcOption(qi, oi)}
                          disabled={isPending || q.options.length <= 2}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    ))}
                    {q.options.length < MAX_OPTIONS && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => addMcOption(qi)}
                        disabled={isPending}
                      >
                        <Plus className="size-4" />
                        {t(
                          "courses.lessonsContent.assessment.editor.addOption",
                        )}
                      </Button>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {t("courses.lessonsContent.assessment.editor.markCorrect")}
                  </p>
                </div>
              ))}
            </section>

            {/* Domande aperte */}
            <section className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">
                  {t("courses.lessonsContent.assessment.editor.openSection")}
                </h3>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addOpen}
                  disabled={isPending}
                >
                  <Plus className="size-4" />
                  {t("courses.lessonsContent.assessment.editor.addOpen")}
                </Button>
              </div>
              {openQ.map((q, qi) => (
                <div
                  key={q.question_id}
                  className="space-y-2 rounded-md border border-border bg-muted/10 p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-mono text-xs text-muted-foreground">
                      #{qi + 1}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-7 text-destructive"
                      onClick={() => removeOpen(qi)}
                      disabled={isPending}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                  <Textarea
                    value={q.text}
                    onChange={(e) => patchOpen(qi, { text: e.target.value })}
                    placeholder={t(
                      "courses.lessonsContent.assessment.editor.questionText",
                    )}
                    rows={2}
                    disabled={isPending}
                    className="resize-y"
                  />
                  <div className="space-y-1">
                    <Label className="text-xs">
                      {t(
                        "courses.lessonsContent.assessment.editor.expectedAnswer",
                      )}
                    </Label>
                    <Textarea
                      value={q.expected_answer}
                      onChange={(e) =>
                        patchOpen(qi, { expected_answer: e.target.value })
                      }
                      rows={3}
                      disabled={isPending}
                      className="resize-y"
                    />
                  </div>
                </div>
              ))}
            </section>
          </div>
        </ScrollArea>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={isPending} className="min-w-[120px]">
            {isPending ? t("common.saving") : t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default LessonAssessmentEditDialog;
