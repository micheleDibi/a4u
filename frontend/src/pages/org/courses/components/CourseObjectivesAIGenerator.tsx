import { useRef, useState, type DragEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Sparkles, Upload, FileText, X } from "lucide-react";

import { coursesApi } from "@/api/courses";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { extractApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

interface Props {
  orgId: string;
  courseId: string | null; // null in mode create -> bottone disabilitato
  disabled?: boolean; // setupLocked
  onGenerated: (data: {
    objectives: string;
    argomenti_chiave: string[];
  }) => void;
}

const MAX_MB = 25;
const ACCEPTED_MIME = [
  "application/pdf",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain",
  "text/markdown",
  "application/rtf",
  "text/rtf",
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Card di generazione AI: l'utente carica un PDF/DOCX/TXT/MD/RTF e
 * preme "Genera con AI". Il file e' one-shot temporaneo (non
 * persistito come documento del corso). Il risultato viene passato a
 * `onGenerated` (il parent apre il dialog di preview).
 *
 * Bottone disabilitato se:
 * - `disabled` (setupLocked)
 * - `courseId === null` (mode create, prima del primo salvataggio)
 */
export function CourseObjectivesAIGenerator({
  orgId,
  courseId,
  disabled,
  onGenerated,
}: Props) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const generateMut = useMutation({
    mutationFn: (file: File) => {
      if (!courseId) {
        return Promise.reject(new Error("course_id mancante"));
      }
      return coursesApi.objectives.generateFromFile(orgId, courseId, file);
    },
    onSuccess: (data) => {
      onGenerated(data);
      setSelectedFile(null);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const file = files[0];
    // Validazioni client (replica del server, per feedback immediato).
    if (!ACCEPTED_MIME.includes(file.type) && file.type !== "") {
      toast.error(
        t("courses.objectivesAI.errors.unsupportedFormat", {
          name: file.name,
        }),
      );
      return;
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      toast.error(
        t("courses.objectivesAI.errors.tooLarge", {
          name: file.name,
          max: MAX_MB,
        }),
      );
      return;
    }
    setSelectedFile(file);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled || !courseId) return;
    handleFiles(e.dataTransfer.files);
  };

  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (disabled || !courseId) return;
    setDragOver(true);
  };

  const onDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
  };

  const generateButtonDisabled =
    disabled ||
    !courseId ||
    !selectedFile ||
    generateMut.isPending;

  const generateButtonTooltip = (() => {
    if (disabled)
      return t("courses.objectivesAI.errors.lockedSetup") as string;
    if (!courseId)
      return t("courses.objectivesAI.errors.saveCourseFirst") as string;
    return undefined;
  })();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="size-5 text-primary" />
          {t("courses.objectivesAI.title")}
        </CardTitle>
        <CardDescription>
          {t("courses.objectivesAI.description")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          onDrop={onDrop}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          className={cn(
            "rounded-md border-2 border-dashed p-6 text-center transition-colors",
            dragOver
              ? "border-primary bg-primary/5"
              : "border-border bg-muted/30",
            (disabled || !courseId) && "opacity-50",
          )}
        >
          <Upload className="mx-auto mb-2 size-8 text-muted-foreground" />
          <p className="text-sm">
            {t("courses.objectivesAI.fileDropzoneCaption")}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("courses.objectivesAI.formatsHint", { max: MAX_MB })}
          </p>
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            accept={ACCEPTED_MIME.join(",")}
            onChange={(e) => handleFiles(e.target.files)}
            disabled={disabled || !courseId}
          />
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => inputRef.current?.click()}
            disabled={disabled || !courseId || generateMut.isPending}
          >
            {t("courses.objectivesAI.chooseFile")}
          </Button>
        </div>

        {selectedFile && (
          <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">
                  {selectedFile.name}
                </div>
                <div className="text-xs text-muted-foreground">
                  {formatBytes(selectedFile.size)}
                </div>
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="size-7 shrink-0"
              onClick={() => setSelectedFile(null)}
              disabled={generateMut.isPending}
              aria-label={t("courses.objectivesAI.removeFile")}
            >
              <X className="size-4" />
            </Button>
          </div>
        )}

        <Button
          className="w-full"
          onClick={() => selectedFile && generateMut.mutate(selectedFile)}
          disabled={generateButtonDisabled}
          title={generateButtonTooltip}
        >
          <Sparkles className="me-2 size-4" />
          {generateMut.isPending
            ? t("courses.objectivesAI.generating")
            : t("courses.objectivesAI.generateButton")}
        </Button>
      </CardContent>
    </Card>
  );
}
