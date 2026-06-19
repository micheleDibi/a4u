import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { memberAvatarApi } from "@/api/avatars";
import type { MembershipOut } from "@/api/types";
import { AvatarClipCard } from "@/components/avatar/AvatarClipCard";
import { AvatarClipsBadge } from "@/components/avatar/AvatarClipsBadge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";

interface Props {
  orgId: string;
  /** Membro di cui mostrare l'avatar; `null` = dialog chiuso. */
  member: MembershipOut | null;
  onClose: () => void;
}

/**
 * Anteprima in sola lettura dell'avatar di un membro (immagine, campione
 * vocale e clip video). Riservata a chi ha `member:avatar:view`; i dati
 * arrivano dall'endpoint `GET /orgs/{orgId}/members/{userId}/avatar`.
 */
export function MemberAvatarDialog({ orgId, member, onClose }: Props) {
  const { t } = useTranslation();
  const open = !!member;
  const userId = member?.user_id;

  const query = useQuery({
    queryKey: ["org", orgId, "member-avatar", userId],
    queryFn: () => memberAvatarApi.get(orgId, userId!),
    enabled: open && !!userId,
  });

  const avatar = query.data ?? null;

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2 pr-6">
            {t("members.avatarDialog.title", {
              name: member?.user_full_name ?? "",
            })}
            {avatar && <AvatarClipsBadge status={avatar.clips_status} />}
          </DialogTitle>
        </DialogHeader>

        {query.isLoading ? (
          <div className="space-y-4">
            <Skeleton className="h-32 w-full" />
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              <Skeleton className="h-48" />
              <Skeleton className="h-48" />
              <Skeleton className="h-48" />
            </div>
          </div>
        ) : !avatar ? (
          <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
            {t("members.avatarDialog.empty")}
          </div>
        ) : (
          <div className="space-y-5">
            <div className="flex flex-wrap gap-6">
              <div className="space-y-1.5">
                <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t("members.avatarDialog.image")}
                </div>
                <img
                  src={avatar.image_url}
                  alt=""
                  className="size-32 rounded-md border border-border object-cover"
                />
              </div>
              <div className="min-w-[220px] flex-1 space-y-1.5">
                <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t("members.avatarDialog.audio")}
                </div>
                {avatar.audio_url ? (
                  <audio controls src={avatar.audio_url} className="w-full" />
                ) : (
                  <p className="text-sm text-muted-foreground">
                    {t("members.avatarDialog.noAudio")}
                  </p>
                )}
              </div>
            </div>

            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t("members.avatarDialog.clips")}
              </div>
              {avatar.clips.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("members.avatarDialog.noClips")}
                </p>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {avatar.clips.map((clip) => (
                    <AvatarClipCard key={clip.id} clip={clip} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
