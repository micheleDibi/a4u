import { useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  FileText,
  Loader2,
  Microscope,
  Sparkles,
  Tags,
} from "lucide-react";

import {
  coursesApi,
  type PaperAISummaryOut,
  type PaperOut,
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
import { ScrollArea } from "@/components/ui/scroll-area";
import { extractApiError } from "@/lib/errors";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orgId: string;
  courseId: string;
  paper: PaperOut | null;
}

/**
 * Dialog che mostra il riassunto AI di un paper.
 * 4 sezioni: riassunto breve, riassunto tecnico, parole chiave, limiti.
 * La mutation parte automaticamente all'apertura del dialog (`useEffect`).
 * Niente persistenza: chiusura -> tutto perso (l'utente la rigenererebbe).
 */
export function PaperAISummaryDialog({
  open,
  onOpenChange,
  orgId,
  courseId,
  paper,
}: Props) {
  const { t } = useTranslation();

  const summaryMut = useMutation({
    mutationFn: (p: PaperOut) =>
      coursesApi.papers.aiSummary(orgId, courseId, p),
    onError: (err) => toast.error(extractApiError(err).message),
  });

  // Trigger della mutation all'apertura del dialog (una volta sola per
  // ogni paper).
  useEffect(() => {
    if (open && paper) {
      summaryMut.reset();
      summaryMut.mutate(paper);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, paper?.id]);

  const data: PaperAISummaryOut | undefined = summaryMut.data;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-5 text-primary" />
            {t("courses.papers.aiSummaryDialog.title")}
          </DialogTitle>
          {paper && (
            <DialogDescription className="line-clamp-2">
              {paper.title}
            </DialogDescription>
          )}
        </DialogHeader>

        <ScrollArea className="max-h-[60vh] pr-2">
          {summaryMut.isPending ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <Loader2 className="size-8 animate-spin text-primary" />
              <p className="mt-3 text-sm text-muted-foreground">
                {t("courses.papers.aiSummaryDialog.generating")}
              </p>
            </div>
          ) : data ? (
            <div className="space-y-5">
              <Section
                icon={<FileText className="size-4 text-primary" />}
                title={t("courses.papers.aiSummaryDialog.shortSummary")}
                content={data.short_summary}
              />
              <Section
                icon={<Microscope className="size-4 text-primary" />}
                title={t("courses.papers.aiSummaryDialog.technicalSummary")}
                content={data.technical_summary}
              />
              <div>
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                  <Tags className="size-4 text-primary" />
                  {t("courses.papers.aiSummaryDialog.keywords")}
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {data.keywords.map((k, i) => (
                    <span
                      key={`${i}-${k}`}
                      className="rounded-md bg-secondary px-2 py-1 text-xs"
                    >
                      {k}
                    </span>
                  ))}
                </div>
              </div>
              <Section
                icon={<AlertTriangle className="size-4 text-amber-600" />}
                title={t("courses.papers.aiSummaryDialog.limitations")}
                content={data.study_limitations}
              />
            </div>
          ) : null}
        </ScrollArea>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("courses.papers.aiSummaryDialog.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Section({
  icon,
  title,
  content,
}: {
  icon: React.ReactNode;
  title: string;
  content: string;
}) {
  return (
    <div>
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
        {icon}
        {title}
      </h3>
      <p className="whitespace-pre-wrap text-sm text-foreground/90">
        {content}
      </p>
    </div>
  );
}
