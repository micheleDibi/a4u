import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Languages, X } from "lucide-react";

import {
  coursesApi,
  type CourseDuplicationJobCompact,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { flagFor } from "@/i18n/flags";
import { useLanguages } from "@/hooks/useLanguages";
import { extractApiError } from "@/lib/errors";

interface Props {
  orgId: string;
  job: CourseDuplicationJobCompact;
}

/**
 * Badge "Duplicazione in corso XX%" + bandiera lingua target + bottone
 * Annulla. Visibile sulla riga del corso target nella lista corsi
 * quando `course.duplication_job` è valorizzato (status ∈
 * pending|processing).
 */
export function CourseDuplicationBadge({ orgId, job }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const languages = useLanguages();
  const targetLang = languages.find((l) => l.code === job.target_language_code);
  const Flag = flagFor(
    job.target_language_code,
    targetLang?.flag_country_code,
  );

  const cancelMut = useMutation({
    mutationFn: () => coursesApi.cancelDuplication(orgId, job.id),
    onSuccess: () => {
      toast.success(t("courses.duplicate.badge.cancelled"));
      qc.invalidateQueries({ queryKey: ["courses", "list", orgId] });
    },
    onError: (err) =>
      toast.error(
        extractApiError(err).message ?? t("courses.duplicate.toast.error"),
      ),
  });

  return (
    <div className="flex flex-col gap-1.5 min-w-[180px]">
      <div className="flex items-center justify-between gap-2">
        <Badge variant="warning" className="gap-1">
          <Languages className="size-3" />
          {t("courses.duplicate.badge.label")}
          <Flag className="size-3 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
        </Badge>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          onClick={() => cancelMut.mutate()}
          disabled={cancelMut.isPending}
          title={t("courses.duplicate.badge.cancel")}
        >
          <X className="size-3.5" />
        </Button>
      </div>
      <div className="flex items-center gap-2">
        <Progress value={job.progress} className="h-1.5 flex-1" />
        <span className="text-xs tabular-nums text-muted-foreground">
          {job.progress}%
        </span>
      </div>
    </div>
  );
}
