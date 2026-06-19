import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  videoUrl: string | null;
  downloadName: string;
  /** Metadati opzionali (es. chip token) mostrati sotto il player. */
  meta?: ReactNode;
}

/**
 * Player video in modale: sostituisce i player `<video>` incorporati che
 * allungavano a dismisura le pagine. Si apre al click sulla riga/card di
 * una lezione pronta. Include il download dell'MP4 (nuova scheda).
 */
export function VideoPlayerModal({
  open,
  onOpenChange,
  title,
  videoUrl,
  downloadName,
  meta,
}: Props) {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="pr-6 text-base">{title}</DialogTitle>
        </DialogHeader>
        {videoUrl && (
          <div className="overflow-hidden rounded-md border bg-black">
            <video
              controls
              autoPlay
              preload="metadata"
              className="aspect-[99/70] w-full bg-black"
              src={videoUrl}
            />
          </div>
        )}
        {meta && (
          <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
            {meta}
          </div>
        )}
        <DialogFooter>
          {videoUrl && (
            <Button variant="outline" asChild>
              <a
                href={videoUrl}
                download={downloadName}
                target="_blank"
                rel="noopener noreferrer"
              >
                <Download className="size-4" />
                {t("courses.media.modalDownload")}
              </a>
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
