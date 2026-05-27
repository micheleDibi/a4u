import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { KeywordTagsInput } from "./KeywordTagsInput";

interface Proposal {
  objectives: string;
  argomenti_chiave: string[];
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  current: Proposal;
  proposed: Proposal | null;
  onApply: (next: Proposal) => void;
}

/**
 * Dialog di preview a 2 colonne: a sinistra i valori ATTUALI del corso
 * (read-only), a destra la PROPOSTA AI (editabile prima di applicare).
 *
 * L'utente puo' rifinire la proposta (modificare il testo degli obiettivi,
 * aggiungere/rimuovere argomenti) e poi premere "Applica" per
 * sovrascrivere i valori nel form del corso. Premendo "Annulla" il
 * dialog si chiude senza alcuna modifica.
 */
export function CourseObjectivesAIPreviewDialog({
  open,
  onOpenChange,
  current,
  proposed,
  onApply,
}: Props) {
  const { t } = useTranslation();
  const [editedObjectives, setEditedObjectives] = useState<string>("");
  const [editedTopics, setEditedTopics] = useState<string[]>([]);

  // Quando arriva una nuova proposal, reset dei campi editabili.
  useEffect(() => {
    if (proposed) {
      setEditedObjectives(proposed.objectives);
      setEditedTopics(proposed.argomenti_chiave);
    }
  }, [proposed]);

  const handleApply = () => {
    onApply({
      objectives: editedObjectives,
      argomenti_chiave: editedTopics,
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-5 text-primary" />
            {t("courses.objectivesAI.preview.title")}
          </DialogTitle>
          <DialogDescription>
            {t("courses.objectivesAI.preview.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {/* Colonna sinistra: ATTUALE (read-only) */}
          <div className="space-y-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {t("courses.objectivesAI.preview.currentLabel")}
            </div>
            <div className="space-y-1.5">
              <Label>
                {t("courses.objectivesAI.preview.objectivesLabel")}
              </Label>
              <Textarea
                rows={8}
                value={current.objectives}
                readOnly
                className="resize-none bg-muted/40"
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t("courses.objectivesAI.preview.topicsLabel")}</Label>
              <div className="min-h-[60px] rounded-md border border-border bg-muted/40 p-2">
                {current.argomenti_chiave.length === 0 ? (
                  <div className="text-xs italic text-muted-foreground">
                    {t("courses.objectivesAI.preview.noCurrentTopics")}
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {current.argomenti_chiave.map((topic, idx) => (
                      <span
                        key={`${idx}-${topic}`}
                        className="rounded-md bg-secondary px-2 py-1 text-xs"
                      >
                        {topic}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Colonna destra: GENERATO (editabile) */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-primary">
                {t("courses.objectivesAI.preview.generatedLabel")}
              </span>
              <Sparkles className="size-3.5 text-primary" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ai-objectives-edit">
                {t("courses.objectivesAI.preview.objectivesLabel")}
              </Label>
              <Textarea
                id="ai-objectives-edit"
                rows={8}
                value={editedObjectives}
                onChange={(e) => setEditedObjectives(e.target.value)}
                maxLength={8000}
                className="resize-none"
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t("courses.objectivesAI.preview.topicsLabel")}</Label>
              <KeywordTagsInput
                value={editedTopics}
                onChange={setEditedTopics}
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("courses.objectivesAI.preview.cancel")}
          </Button>
          <Button
            onClick={handleApply}
            disabled={!editedObjectives.trim() || editedTopics.length === 0}
          >
            <Sparkles className="me-2 size-4" />
            {t("courses.objectivesAI.preview.apply")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
