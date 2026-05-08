import { useRef, useState, type DragEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  Eye,
  FileText,
  Loader2,
  RotateCw,
  Trash2,
  TriangleAlert,
  Upload,
} from "lucide-react";
import { coursesApi, type CourseDocumentOut } from "@/api/courses";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { extractApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { DocumentSummaryDialog } from "./DocumentSummaryDialog";

interface Props {
  orgId: string;
  courseId: string;
  documents: CourseDocumentOut[];
  onChanged?: () => void; // notify parent that the docs list has changed
  disabled?: boolean;
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

export function CourseDocumentUploader({
  orgId,
  courseId,
  documents,
  onChanged,
  disabled,
}: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [toDelete, setToDelete] = useState<CourseDocumentOut | null>(null);
  const [toReprocess, setToReprocess] = useState<CourseDocumentOut | null>(null);
  const [openSummary, setOpenSummary] = useState<CourseDocumentOut | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["courses", "detail", orgId, courseId] });
    onChanged?.();
  };

  const uploadMut = useMutation({
    mutationFn: (file: File) => coursesApi.documents.upload(orgId, courseId, file),
    onSuccess: () => {
      toast.success(t("courses.docs.uploaded"));
      invalidate();
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const deleteMut = useMutation({
    mutationFn: (docId: string) =>
      coursesApi.documents.remove(orgId, courseId, docId),
    onSuccess: () => {
      toast.success(t("courses.docs.deleted"));
      invalidate();
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const reprocessMut = useMutation({
    mutationFn: (docId: string) =>
      coursesApi.documents.reprocess(orgId, courseId, docId),
    onSuccess: () => {
      toast.success(t("courses.docs.reprocessed"));
      invalidate();
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const handleFiles = (files: FileList | File[]) => {
    if (disabled) return;
    const arr = Array.from(files);
    for (const file of arr) {
      if (file.size > MAX_MB * 1024 * 1024) {
        toast.error(
          t("courses.docs.tooLarge", { name: file.name, max: MAX_MB })
        );
        continue;
      }
      if (file.type && !ACCEPTED_MIME.includes(file.type)) {
        toast.error(
          t("courses.docs.invalidType", { name: file.name, type: file.type })
        );
        continue;
      }
      uploadMut.mutate(file);
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      handleFiles(e.dataTransfer.files);
    }
  };

  const handleRowClick = (d: CourseDocumentOut) => {
    if (d.summary_status === "ready") {
      setOpenSummary(d);
    }
  };

  const requestReprocess = (d: CourseDocumentOut) => {
    if (d.summary_status === "ready") {
      // Conferma se rigeneriamo un summary già pronto (costo token).
      setToReprocess(d);
    } else {
      // Pending/processing/failed: nessuna conferma necessaria.
      reprocessMut.mutate(d.id);
    }
  };

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          "rounded-lg border-2 border-dashed p-6 text-center transition-colors",
          dragOver
            ? "border-primary bg-primary/5"
            : "border-border bg-muted/20",
          disabled && "opacity-60"
        )}
      >
        <Upload className="mx-auto mb-2 size-6 text-muted-foreground" />
        <p className="text-sm font-medium">{t("courses.docs.dropHere")}</p>
        <p className="mb-3 text-xs text-muted-foreground">
          {t("courses.docs.types", { max: MAX_MB })}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || uploadMut.isPending}
          onClick={() => inputRef.current?.click()}
        >
          {uploadMut.isPending
            ? t("courses.docs.uploading")
            : t("courses.docs.choose")}
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          accept={ACCEPTED_MIME.join(",")}
          onChange={(e) => {
            if (e.target.files) handleFiles(e.target.files);
            if (inputRef.current) inputRef.current.value = "";
          }}
        />
      </div>

      {documents.length > 0 ? (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {documents.map((d) => {
            const isPending =
              d.summary_status === "pending" ||
              d.summary_status === "processing";
            const isReady = d.summary_status === "ready";
            const isFailed = d.summary_status === "failed";
            return (
              <li
                key={d.id}
                className={cn(
                  "flex items-center gap-3 p-3 text-sm",
                  isReady && "cursor-pointer hover:bg-muted/40"
                )}
                onClick={() => handleRowClick(d)}
              >
                <FileText className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{d.filename_original}</div>
                  <div className="text-xs text-muted-foreground">
                    {formatBytes(d.size_bytes)} · {d.mime_type}
                  </div>
                </div>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="inline-flex items-center gap-1.5">
                        {isPending && (
                          <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
                        )}
                        {isFailed && (
                          <TriangleAlert className="size-3.5 text-destructive" />
                        )}
                        <Badge
                          variant={
                            isReady
                              ? "brand"
                              : isFailed
                              ? "destructive"
                              : "muted"
                          }
                          className="shrink-0"
                        >
                          {t(`courses.docs.summary.${d.summary_status}`)}
                        </Badge>
                      </span>
                    </TooltipTrigger>
                    {isFailed && d.summary_error && (
                      <TooltipContent className="max-w-md">
                        {d.summary_error}
                      </TooltipContent>
                    )}
                  </Tooltip>
                </TooltipProvider>
                {isReady && (
                  <Button
                    variant="ghost"
                    size="icon"
                    title={t("courses.docs.viewDetail")}
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpenSummary(d);
                    }}
                  >
                    <Eye className="size-4" />
                  </Button>
                )}
                {!disabled && (
                  <>
                    <Button
                      variant="ghost"
                      size="icon"
                      title={t("courses.docs.reprocess")}
                      disabled={
                        d.summary_status === "processing" ||
                        reprocessMut.isPending
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        requestReprocess(d);
                      }}
                    >
                      <RotateCw className="size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive"
                      onClick={(e) => {
                        e.stopPropagation();
                        setToDelete(d);
                      }}
                      title={t("common.delete")}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          {t("courses.docs.empty")}
        </p>
      )}

      <ConfirmDialog
        open={!!toDelete}
        title={t("courses.docs.deleteConfirm.title")}
        message={t("courses.docs.deleteConfirm.message", {
          name: toDelete?.filename_original ?? "",
        })}
        destructive
        confirmLabel={t("common.delete")}
        onClose={() => setToDelete(null)}
        onConfirm={() => {
          if (toDelete) {
            deleteMut.mutate(toDelete.id);
            setToDelete(null);
          }
        }}
      />

      <ConfirmDialog
        open={!!toReprocess}
        title={t("courses.docs.reprocessConfirm.title")}
        message={t("courses.docs.reprocessConfirm.message", {
          name: toReprocess?.filename_original ?? "",
        })}
        confirmLabel={t("courses.docs.reprocess")}
        onClose={() => setToReprocess(null)}
        onConfirm={() => {
          if (toReprocess) {
            reprocessMut.mutate(toReprocess.id);
            setToReprocess(null);
          }
        }}
      />

      <DocumentSummaryDialog
        orgId={orgId}
        courseId={courseId}
        doc={openSummary}
        open={!!openSummary}
        onClose={() => setOpenSummary(null)}
      />
    </div>
  );
}
