import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Languages } from "lucide-react";

import { coursesApi, type CourseListItemOut } from "@/api/courses";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useLanguages } from "@/hooks/useLanguages";
import { flagFor } from "@/i18n/flags";
import { extractApiError } from "@/lib/errors";

interface Props {
  orgId: string;
  course: CourseListItemOut;
  onClose: () => void;
}

/**
 * Dialog "Duplica corso in altra lingua". Mostra Select delle lingue
 * disponibili (escludendo la lingua corrente del corso) e avvia il job
 * di duplicazione tramite `coursesApi.duplicate`. Al successo invalida
 * la lista corsi per far comparire il corso target col badge di
 * duplicazione attiva.
 */
export function DuplicateCourseDialog({ orgId, course, onClose }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const languages = useLanguages();
  const [targetLang, setTargetLang] = useState<string | undefined>(undefined);

  const availableLangs = useMemo(
    () => languages.filter((l) => l.code !== course.language_code),
    [languages, course.language_code],
  );

  const mut = useMutation({
    mutationFn: (target: string) =>
      coursesApi.duplicate(orgId, course.id, target),
    onSuccess: () => {
      toast.success(t("courses.duplicate.toast.success"));
      // Invalidate immediata + re-invalidate ogni 2s per 16s totali.
      // Il backend crea il `target_course` solo nella phase
      // `cloning_structure` del worker (~5-10s dopo il job pending).
      // Senza questi refetch ravvicinati, l'utente non vede comparire
      // la nuova riga finché il polling regolare (3s) non scatta — e
      // quello parte solo dopo che `target_course_id` è valorizzato.
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
      for (let i = 1; i <= 8; i++) {
        window.setTimeout(() => {
          qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
        }, i * 2000);
      }
      onClose();
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.duplicate.toast.error"),
      ),
  });

  const handleConfirm = () => {
    if (!targetLang) return;
    mut.mutate(targetLang);
  };

  return (
    <Dialog open={true} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Languages className="size-5" />
            {t("courses.duplicate.dialog.title")}
          </DialogTitle>
          <DialogDescription>
            {t("courses.duplicate.dialog.message")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <Select value={targetLang} onValueChange={setTargetLang}>
            <SelectTrigger className="h-9">
              <SelectValue
                placeholder={t("courses.duplicate.dialog.targetLanguage")}
              />
            </SelectTrigger>
            <SelectContent>
              {availableLangs.map((l) => {
                const Flag = flagFor(l.code, l.flag_country_code);
                return (
                  <SelectItem key={l.code} value={l.code}>
                    <span className="inline-flex items-center gap-2">
                      <Flag className="size-4 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
                      <span>{l.name_native}</span>
                    </span>
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={mut.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={!targetLang || mut.isPending}
          >
            {t("courses.duplicate.dialog.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
