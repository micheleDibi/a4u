import { useTranslation } from "react-i18next";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/**
 * Badge che riassume lo stato aggregato delle clip di un avatar
 * (`clips_status`). Condiviso tra "Mio avatar" e l'anteprima avatar di un
 * membro. Usa le chiavi i18n `myAvatar.*`.
 */
export function AvatarClipsBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  if (status === "ready") {
    return (
      <Badge
        variant="secondary"
        className="bg-emerald-100 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-300"
      >
        <CheckCircle2 className="size-3" /> {t("myAvatar.allReady")}
      </Badge>
    );
  }
  if (status === "failed") {
    return (
      <Badge variant="destructive">
        <XCircle className="size-3" /> {t("myAvatar.allFailed")}
      </Badge>
    );
  }
  if (status === "partial") {
    return (
      <Badge
        variant="secondary"
        className="bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-300"
      >
        {t("myAvatar.partial")}
      </Badge>
    );
  }
  return (
    <Badge variant="secondary">
      <Loader2 className="size-3 animate-spin" />
      {status === "processing" ? t("myAvatar.processing") : t("myAvatar.pending")}
    </Badge>
  );
}
