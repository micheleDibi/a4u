import { useTranslation } from "react-i18next";
import { Loader2, XCircle } from "lucide-react";

import type { AvatarClipOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Card di una singola clip avatar: player video se pronta, stato di
 * caricamento/errore altrimenti, con etichetta "Clip N". Condivisa tra
 * "Mio avatar" e l'anteprima avatar di un membro. Chiavi i18n `myAvatar.*`.
 */
export function AvatarClipCard({ clip }: { clip: AvatarClipOut }) {
  const { t } = useTranslation();
  const isReady = clip.status === "ready" && clip.video_url;
  const isFailed = clip.status === "failed";

  return (
    <Card className="overflow-hidden">
      <div className="relative aspect-square bg-muted">
        {isReady ? (
          <video
            controls
            loop
            playsInline
            src={clip.video_url ?? undefined}
            className="size-full object-contain"
          />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-2 text-muted-foreground">
            {isFailed ? (
              <>
                <XCircle className="size-8 text-destructive" />
                <span className="text-xs">{t("myAvatar.clipFailed")}</span>
              </>
            ) : (
              <>
                <Loader2 className="size-8 animate-spin" />
                <span className="text-xs">
                  {clip.status === "processing"
                    ? t("myAvatar.clipProcessing")
                    : t("myAvatar.clipPending")}
                </span>
              </>
            )}
          </div>
        )}
        <Badge
          variant="secondary"
          className="absolute start-2 top-2 bg-black/60 font-mono text-white backdrop-blur"
        >
          #{clip.position + 1}
        </Badge>
      </div>
      <CardContent className="space-y-1 p-3">
        <p className="text-sm font-medium">
          {t("myAvatar.clipLabel", { n: clip.position + 1 })}
        </p>
        {isFailed && clip.error_message && (
          <p className="text-xs text-destructive" title={clip.error_message}>
            {clip.error_message}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
