import { useTranslation } from "react-i18next";

import type { AvatarClipsAggregateStatus } from "@/api/types";
import { cn } from "@/lib/utils";

type AvatarDotState = "complete" | "progress" | "none";

/**
 * Stato dell'indicatore avatar di un membro:
 * - `complete` (verde): immagine + voce + clip pronte;
 * - `progress` (ambra): avatar avviato ma non completo (clip in corso /
 *   parziali / fallite, oppure clip pronte ma senza voce);
 * - `none` (grigio): nessun avatar creato.
 */
function avatarDotState(
  status: AvatarClipsAggregateStatus | null,
  audio: boolean,
): AvatarDotState {
  if (status === null) return "none";
  if (status === "ready" && audio) return "complete";
  return "progress";
}

const DOT_CLASS: Record<AvatarDotState, string> = {
  complete: "bg-emerald-500",
  progress: "bg-amber-500",
  none: "bg-muted-foreground/30",
};

interface Props {
  status: AvatarClipsAggregateStatus | null;
  audio: boolean;
  /** Se valorizzato, l'indicatore diventa un bottone (apre l'anteprima). */
  onClick?: () => void;
}

/**
 * Pallino tri-stato con etichetta per lo stato avatar di un membro,
 * mostrato nella lista membri. Cliccabile per aprire l'anteprima.
 */
export function AvatarStatusDot({ status, audio, onClick }: Props) {
  const { t } = useTranslation();
  const state = avatarDotState(status, audio);
  const label = t(`members.avatar.${state}`);

  const content = (
    <>
      <span
        className={cn("size-2.5 shrink-0 rounded-full", DOT_CLASS[state])}
        aria-hidden
      />
      <span className="text-sm">{label}</span>
    </>
  );

  if (!onClick) {
    return <span className="inline-flex items-center gap-2">{content}</span>;
  }

  return (
    <button
      type="button"
      onClick={onClick}
      title={t("members.avatar.view")}
      className="-mx-1.5 inline-flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-muted"
    >
      {content}
    </button>
  );
}
