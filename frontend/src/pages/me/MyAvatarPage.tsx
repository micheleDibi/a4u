import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  ImageIcon,
  Info,
  Loader2,
  Mic,
  Play,
  Quote,
  RefreshCcw,
  Trash2,
  XCircle,
} from "lucide-react";
import i18n from "@/i18n";
import { myAvatarApi } from "@/api/avatars";
import type { AvatarClipOut, AvatarOut } from "@/api/types";
import { useLanguages } from "@/hooks/useLanguages";
import { FormAvatarImageInput } from "@/components/forms/FormAvatarImageInput";
import { FormAudioInput } from "@/components/forms/FormAudioInput";
import { SlideTemplatePreview } from "@/components/templates/SlideTemplatePreview";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { PageHeader } from "@/components/layout/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { extractApiError } from "@/lib/errors";
import { flagFor } from "@/i18n/flags";

const POLLING_INTERVAL_MS = 5000;

export default function MyAvatarPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const langs = useLanguages();

  const [imageFile, setImageFile] = useState<File | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioLang, setAudioLang] = useState<string>(
    i18n.language?.split("-")[0] ?? "it"
  );
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [tab, setTab] = useState<"image" | "audio">("image");

  const query = useQuery({
    queryKey: ["my-avatar"],
    queryFn: () => myAvatarApi.get(),
    refetchInterval: (q) => {
      const data = q.state.data;
      if (!data) return false;
      // Polling finché i clip MiniMax sono in lavorazione.
      const clipsActive =
        data.clips_status === "pending" || data.clips_status === "processing";
      return clipsActive ? POLLING_INTERVAL_MS : false;
    },
  });

  useEffect(() => {
    if (query.data) {
      setAudioLang(query.data.audio_lang ?? i18n.language?.split("-")[0] ?? "it");
    }
  }, [query.data?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const scriptQuery = useQuery({
    queryKey: ["my-avatar", "voice-script", audioLang],
    queryFn: () => myAvatarApi.getVoiceScript(audioLang),
    enabled: !!audioLang,
  });

  const upsert = useMutation({
    mutationFn: () =>
      myAvatarApi.upsert(
        { audio_lang: audioLang },
        { image: imageFile, audio: audioFile }
      ),
    onSuccess: () => {
      toast.success(t("myAvatar.saved"));
      setImageFile(null);
      setAudioFile(null);
      qc.invalidateQueries({ queryKey: ["my-avatar"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const remove = useMutation({
    mutationFn: () => myAvatarApi.remove(),
    onSuccess: () => {
      toast.success(t("myAvatar.deleted"));
      qc.invalidateQueries({ queryKey: ["my-avatar"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const regen = useMutation({
    mutationFn: () => myAvatarApi.regenerateClips(),
    onSuccess: () => {
      toast.success(t("myAvatar.regenerated"));
      qc.invalidateQueries({ queryKey: ["my-avatar"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const data = query.data ?? null;
  const hasAvatar = !!data;
  const dirty = !!imageFile || !!audioFile;
  const imageWillChange = !!imageFile && hasAvatar;
  const imageReady = !!imageFile || !!data?.image_url;
  const canSave =
    !upsert.isPending && (hasAvatar ? dirty : !!imageFile && !!audioFile);

  if (query.isLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title={t("myAvatar.title")}
          description={t("myAvatar.subtitle")}
        />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(440px,580px)]">
          <Skeleton className="h-[480px]" />
          <Skeleton className="h-[340px]" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-24">
      <PageHeader
        title={t("myAvatar.title")}
        description={t("myAvatar.subtitle")}
      />

      {hasAvatar && (
        <StatusBanner
          avatar={data}
          onRegenerate={() => regen.mutate()}
          onDelete={() => setConfirmDelete(true)}
          regenLoading={regen.isPending}
        />
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(440px,580px)]">
        {/* === FORM SECTION === */}
        <Card>
          <CardContent className="p-0">
            <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
              <div className="border-b border-border px-6 pt-5">
                <TabsList className="h-10 bg-transparent p-0">
                  <TabStep
                    value="image"
                    n={1}
                    label={t("myAvatar.tabs.image")}
                    icon={<ImageIcon className="size-4" />}
                  />
                  <TabStep
                    value="audio"
                    n={2}
                    label={t("myAvatar.tabs.audio")}
                    icon={<Mic className="size-4" />}
                    disabled={!imageReady}
                  />
                </TabsList>
              </div>

              <TabsContent value="image" className="m-0 space-y-5 p-6">
                <SectionHeader
                  title={t("myAvatar.image")}
                  description={t("myAvatar.imageHint")}
                />
                <FormAvatarImageInput
                  label=""
                  value={imageFile}
                  existingUrl={data?.image_url ?? null}
                  onChange={setImageFile}
                />
                <div className="flex justify-end pt-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!imageReady}
                    onClick={() => setTab("audio")}
                  >
                    {t("myAvatar.tabs.next")}
                  </Button>
                </div>
              </TabsContent>

              <TabsContent value="audio" className="m-0 space-y-5 p-6">
                <SectionHeader
                  title={t("myAvatar.audio")}
                  description={t("myAvatar.audioHint")}
                />

                <div className="grid gap-4 sm:grid-cols-[200px_1fr] sm:items-start">
                  <div className="space-y-1.5">
                    <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                      {t("myAvatar.audioLang")}
                    </Label>
                    <Select value={audioLang} onValueChange={setAudioLang}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {langs.map((l) => {
                          const Flag = flagFor(l.code, l.flag_country_code);
                          return (
                            <SelectItem key={l.code} value={l.code}>
                              <span className="inline-flex items-center gap-2">
                                <Flag className="size-4" />
                                <span className="uppercase">{l.code}</span>
                                <span className="text-muted-foreground">
                                  — {l.name_native}
                                </span>
                              </span>
                            </SelectItem>
                          );
                        })}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <ScriptToReadCard
                  text={scriptQuery.data?.text ?? null}
                  scriptLang={scriptQuery.data?.language_code ?? null}
                  requestedLang={audioLang}
                  isLoading={scriptQuery.isLoading}
                />

                <FormAudioInput
                  label=""
                  value={audioFile}
                  existingUrl={data?.audio_url ?? null}
                  onChange={setAudioFile}
                />
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>

        {/* === PREVIEW SECTION === */}
        <div className="space-y-3 lg:sticky lg:top-6 lg:self-start">
          <SectionHeader
            title={t("myAvatar.slidePreviewTitle")}
            description={t("myAvatar.slidePreviewHint")}
            compact
          />
          <Card className="overflow-hidden">
            <CardContent className="p-4">
              <AvatarSlidePreview
                avatarFile={imageFile}
                existingAvatarUrl={data?.image_url ?? null}
              />
            </CardContent>
          </Card>
        </div>
      </div>

      {/* === SAVE BAR (sticky bottom) === */}
      <div className="sticky bottom-0 z-10 -mx-3 mt-6 border-t border-border bg-background/95 px-3 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/80 sm:-mx-6 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3 text-sm text-muted-foreground">
            {imageWillChange ? (
              <span className="inline-flex items-center gap-2 text-amber-700 dark:text-amber-400">
                <AlertTriangle className="size-4 shrink-0" />
                {t("myAvatar.saveWarn")}
              </span>
            ) : dirty ? (
              <span>{t("myAvatar.unsavedChanges")}</span>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            {tab === "audio" && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => setTab("image")}
              >
                {t("myAvatar.tabs.back")}
              </Button>
            )}
            <Button
              size="lg"
              onClick={() => upsert.mutate()}
              disabled={!canSave}
            >
              {upsert.isPending
                ? t("common.saving")
                : hasAvatar
                ? t("common.save")
                : t("myAvatar.create")}
            </Button>
          </div>
        </div>
      </div>

      {/* === CLIPS SECTION === */}
      {hasAvatar && (
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold">
                {t("myAvatar.clipsTitle")}
              </h2>
              <p className="text-xs text-muted-foreground">
                {t("myAvatar.clipsCount", { count: data.clips.length })}
              </p>
            </div>
            <ClipsAggregateBadge status={data.clips_status} />
          </div>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {data.clips.length === 0 && (
              <div className="col-span-full rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                {t("myAvatar.clipsEmpty")}
              </div>
            )}
            {data.clips.map((clip) => (
              <ClipCard key={clip.id} clip={clip} />
            ))}
          </div>
        </section>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={t("myAvatar.deleteConfirm.title")}
        message={t("myAvatar.deleteConfirm.message")}
        destructive
        confirmLabel={t("common.delete")}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => {
          remove.mutate();
          setConfirmDelete(false);
        }}
      />
    </div>
  );
}

// ───────────────────────────── Sub-components ─────────────────────────────

function SectionHeader({
  title,
  description,
  compact,
}: {
  title: string;
  description?: string;
  compact?: boolean;
}) {
  return (
    <div className={compact ? "space-y-0.5" : "space-y-1"}>
      <h3 className={compact ? "text-sm font-semibold" : "text-base font-semibold"}>
        {title}
      </h3>
      {description && (
        <p className="text-xs leading-snug text-muted-foreground">{description}</p>
      )}
    </div>
  );
}

function TabStep({
  value,
  n,
  label,
  icon,
  disabled,
}: {
  value: string;
  n: number;
  label: string;
  icon: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <TabsTrigger
      value={value}
      disabled={disabled}
      className="relative h-10 gap-2 rounded-none border-b-2 border-transparent bg-transparent px-4 py-2 text-muted-foreground shadow-none data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none"
    >
      <span className="grid size-5 place-items-center rounded-full bg-muted text-[11px] font-semibold">
        {n}
      </span>
      {icon}
      {label}
    </TabsTrigger>
  );
}

function StatusBanner({
  avatar,
  onRegenerate,
  onDelete,
  regenLoading,
}: {
  avatar: AvatarOut;
  onRegenerate: () => void;
  onDelete: () => void;
  regenLoading: boolean;
}) {
  const { t } = useTranslation();
  const ready = avatar.clips.filter((c) => c.status === "ready").length;
  const failed = avatar.clips.filter((c) => c.status === "failed").length;
  const total = avatar.clips.length;
  const Flag = avatar.audio_lang ? flagFor(avatar.audio_lang) : null;

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-4 p-4">
        <div className="size-14 shrink-0 overflow-hidden rounded-md bg-muted">
          <img src={avatar.image_url} alt="" className="size-full object-cover" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold">
              {t("myAvatar.statusReady")}
            </span>
            <ClipsAggregateBadge status={avatar.clips_status} />
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("myAvatar.statusSummary", {
              ready,
              total,
              failed,
            })}
            {Flag && avatar.audio_lang && (
              <span className="ms-2 inline-flex items-center gap-1">
                · <Flag className="size-3" />
                <span className="uppercase">{avatar.audio_lang}</span>
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {avatar.audio_url && (
            <Button asChild variant="ghost" size="sm">
              <a href={avatar.audio_url} target="_blank" rel="noreferrer">
                <Play className="size-4" />
                {t("myAvatar.playAudio")}
              </a>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={onRegenerate}
            disabled={regenLoading}
          >
            <RefreshCcw className={`size-4 ${regenLoading ? "animate-spin" : ""}`} />
            {t("myAvatar.regenerate")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={onDelete}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

interface AvatarSlidePreviewProps {
  avatarFile: File | null;
  existingAvatarUrl: string | null;
}

function AvatarSlidePreview({
  avatarFile,
  existingAvatarUrl,
}: AvatarSlidePreviewProps) {
  const fileUrl = useMemo(
    () => (avatarFile ? URL.createObjectURL(avatarFile) : null),
    [avatarFile]
  );
  useEffect(
    () => () => {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
    },
    [fileUrl]
  );
  const avatarUrl = fileUrl ?? existingAvatarUrl ?? null;
  return (
    <SlideTemplatePreview
      textColor="#0F172A"
      primaryColor="#2563EB"
      secondaryColor="#0EA5E9"
      fontFamily="Inter"
      slideSize="16:9"
      avatarUrl={avatarUrl}
    />
  );
}

interface ScriptToReadCardProps {
  text: string | null;
  scriptLang: string | null;
  requestedLang: string;
  isLoading: boolean;
}

function ScriptToReadCard({
  text,
  scriptLang,
  requestedLang,
  isLoading,
}: ScriptToReadCardProps) {
  const { t } = useTranslation();
  const isFallback =
    !!text && scriptLang !== null && scriptLang !== requestedLang;

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        {t("common.loading")}
      </div>
    );
  }

  if (!text) {
    return (
      <Alert>
        <Info className="size-4" />
        <AlertDescription>{t("myAvatar.scriptMissing")}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-muted/30 p-5">
      <div className="flex items-center justify-between gap-2 pb-3">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
          <Quote className="size-3.5" />
          {t("myAvatar.scriptTitle")}
        </div>
        {isFallback && scriptLang && (
          <Badge variant="secondary" className="font-mono text-[10px]">
            {scriptLang.toUpperCase()}
          </Badge>
        )}
      </div>
      <p className="text-[15px] leading-relaxed whitespace-pre-line">
        {text}
      </p>
      {isFallback && (
        <p className="mt-3 flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400">
          <Info className="size-3.5 mt-0.5 shrink-0" />
          {t("myAvatar.scriptFallback", { lang: scriptLang?.toUpperCase() })}
        </p>
      )}
    </div>
  );
}

function ClipsAggregateBadge({ status }: { status: string }) {
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

function ClipCard({ clip }: { clip: AvatarClipOut }) {
  const { t } = useTranslation();
  const isReady = clip.status === "ready" && clip.video_url;
  const isFailed = clip.status === "failed";

  return (
    <Card className="overflow-hidden">
      <div className="relative aspect-video bg-muted">
        {isReady ? (
          <video
            controls
            loop
            playsInline
            src={clip.video_url ?? undefined}
            className="size-full object-cover"
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
        <p className="line-clamp-2 text-xs text-muted-foreground" title={clip.prompt_text}>
          {clip.prompt_text}
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
