import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  Languages,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import {
  avatarConfigApi,
  type AvatarClipPromptCreate,
  type AvatarClipPromptUpdate,
} from "@/api/avatarConfig";
import type { AvatarClipPromptOut, AvatarVoiceScriptOut } from "@/api/types";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useLanguages } from "@/hooks/useLanguages";
import { flagFor } from "@/i18n/flags";
import { extractApiError } from "@/lib/errors";

export default function AvatarConfigPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<AvatarClipPromptCreate>({
    prompt: "",
    label_it: "",
    is_active: true,
  });
  const [toDelete, setToDelete] = useState<AvatarClipPromptOut | null>(null);

  const query = useQuery({
    queryKey: ["avatar-config", "prompts"],
    queryFn: avatarConfigApi.listPrompts,
  });

  const createMut = useMutation({
    mutationFn: () => avatarConfigApi.createPrompt(draft),
    onSuccess: () => {
      toast.success(t("avatarConfig.created"));
      setCreating(false);
      setDraft({ prompt: "", label_it: "", is_active: true });
      qc.invalidateQueries({ queryKey: ["avatar-config", "prompts"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: AvatarClipPromptUpdate }) =>
      avatarConfigApi.updatePrompt(id, payload),
    onSuccess: () => {
      toast.success(t("avatarConfig.updated"));
      qc.invalidateQueries({ queryKey: ["avatar-config", "prompts"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => avatarConfigApi.deletePrompt(id),
    onSuccess: () => {
      toast.success(t("avatarConfig.deleted"));
      qc.invalidateQueries({ queryKey: ["avatar-config", "prompts"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const reorderMut = useMutation({
    mutationFn: (orderedIds: string[]) => avatarConfigApi.reorderPrompts(orderedIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["avatar-config", "prompts"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const prompts = query.data ?? [];
  const moveTo = (idx: number, delta: number) => {
    const next = [...prompts];
    const target = idx + delta;
    if (target < 0 || target >= next.length) return;
    [next[idx], next[target]] = [next[target], next[idx]];
    reorderMut.mutate(next.map((p) => p.id));
  };

  const [tab, setTab] = useState<"prompts" | "scripts">("prompts");

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("avatarConfig.title")}
        description={t("avatarConfig.subtitle")}
      />

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList className="grid w-full max-w-md grid-cols-2">
          <TabsTrigger value="prompts">
            <Sparkles className="size-4" />
            {t("avatarConfig.tabs.prompts")}
          </TabsTrigger>
          <TabsTrigger value="scripts">
            <Languages className="size-4" />
            {t("avatarConfig.tabs.scripts")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="prompts" className="space-y-4 pt-4">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm text-muted-foreground">
              {t("avatarConfig.count", { count: prompts.length })}
            </div>
            <Button onClick={() => setCreating(true)} disabled={creating}>
              <Plus className="size-4" />
              {t("avatarConfig.addPrompt")}
            </Button>
          </div>

          {creating && (
            <Card>
              <CardContent className="space-y-3 p-6">
                <div className="space-y-1.5">
                  <Label>{t("avatarConfig.fields.label")}</Label>
                  <Input
                    value={draft.label_it ?? ""}
                    onChange={(e) => setDraft({ ...draft, label_it: e.target.value })}
                    placeholder={t("avatarConfig.fields.labelPlaceholder")}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>{t("avatarConfig.fields.prompt")}</Label>
                  <Textarea
                    rows={3}
                    value={draft.prompt}
                    onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
                    placeholder="Subtle thoughtful head nod..."
                  />
                </div>
                <div className="flex items-center gap-2">
                  <Switch
                    id="new-active"
                    checked={draft.is_active ?? true}
                    onCheckedChange={(v) => setDraft({ ...draft, is_active: v })}
                  />
                  <Label htmlFor="new-active">{t("avatarConfig.fields.active")}</Label>
                </div>
                <div className="flex justify-end gap-2">
                  <Button variant="ghost" onClick={() => setCreating(false)}>
                    {t("common.cancel")}
                  </Button>
                  <Button
                    onClick={() => createMut.mutate()}
                    disabled={!draft.prompt.trim() || createMut.isPending}
                  >
                    {createMut.isPending ? t("common.saving") : t("common.add")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          <div className="space-y-3">
            {prompts.map((p, idx) => (
              <PromptRow
                key={p.id}
                prompt={p}
                isFirst={idx === 0}
                isLast={idx === prompts.length - 1}
                onMoveUp={() => moveTo(idx, -1)}
                onMoveDown={() => moveTo(idx, +1)}
                onUpdate={(payload) => updateMut.mutate({ id: p.id, payload })}
                onDelete={() => setToDelete(p)}
              />
            ))}
            {prompts.length === 0 && !creating && (
              <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                {t("avatarConfig.empty")}
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="scripts" className="space-y-4 pt-4">
          <VoiceScriptsSection />
        </TabsContent>
      </Tabs>

      <ConfirmDialog
        open={!!toDelete}
        title={t("avatarConfig.deleteConfirm.title")}
        message={t("avatarConfig.deleteConfirm.message", {
          name: toDelete?.label_it ?? "",
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
    </div>
  );
}

function VoiceScriptsSection() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const langs = useLanguages();

  const query = useQuery({
    queryKey: ["avatar-config", "voice-scripts"],
    queryFn: avatarConfigApi.listVoiceScripts,
  });

  const upsertMut = useMutation({
    mutationFn: ({ lang, text }: { lang: string; text: string }) =>
      avatarConfigApi.upsertVoiceScript(lang, text),
    onSuccess: () => {
      toast.success(t("avatarConfig.voiceScript.saved"));
      qc.invalidateQueries({ queryKey: ["avatar-config", "voice-scripts"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const deleteMut = useMutation({
    mutationFn: (lang: string) => avatarConfigApi.deleteVoiceScript(lang),
    onSuccess: () => {
      toast.success(t("avatarConfig.voiceScript.deleted"));
      qc.invalidateQueries({ queryKey: ["avatar-config", "voice-scripts"] });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const scripts = query.data ?? [];
  const [newLang, setNewLang] = useState<string>("");
  const [newText, setNewText] = useState<string>("");
  const [toRemove, setToRemove] = useState<string | null>(null);

  // Lingue ancora senza script (non duplicare).
  const availableLangs = langs.filter(
    (l) => !scripts.some((s) => s.language_code === l.code)
  );

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {t("avatarConfig.voiceScript.subtitle")}
      </p>

      <div className="space-y-3">
        {scripts.map((s) => (
          <VoiceScriptRow
            key={s.language_code}
            script={s}
            flagCountryCode={
              langs.find((l) => l.code === s.language_code)?.flag_country_code
            }
            onSave={(text) => upsertMut.mutate({ lang: s.language_code, text })}
            onDelete={() => setToRemove(s.language_code)}
          />
        ))}
        {scripts.length === 0 && (
          <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            {t("avatarConfig.voiceScript.empty")}
          </div>
        )}
      </div>

      {availableLangs.length > 0 && (
        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Plus className="size-4" />
              {t("avatarConfig.voiceScript.add")}
            </div>
            <div className="grid gap-3 md:grid-cols-[200px_1fr_auto]">
              <Select value={newLang} onValueChange={setNewLang}>
                <SelectTrigger>
                  <SelectValue placeholder={t("avatarConfig.voiceScript.selectLang")} />
                </SelectTrigger>
                <SelectContent>
                  {availableLangs.map((l) => {
                    const Flag = flagFor(l.code, l.flag_country_code);
                    return (
                      <SelectItem key={l.code} value={l.code}>
                        <span className="inline-flex items-center gap-2">
                          <Flag className="size-4" />
                          <span className="uppercase">{l.code}</span>
                          <span className="text-muted-foreground">— {l.name_native}</span>
                        </span>
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
              <Textarea
                rows={3}
                value={newText}
                onChange={(e) => setNewText(e.target.value)}
                placeholder={t("avatarConfig.voiceScript.placeholder")}
              />
              <Button
                disabled={!newLang || !newText.trim() || upsertMut.isPending}
                onClick={() => {
                  upsertMut.mutate(
                    { lang: newLang, text: newText },
                    {
                      onSuccess: () => {
                        setNewLang("");
                        setNewText("");
                      },
                    }
                  );
                }}
              >
                {t("common.add")}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <ConfirmDialog
        open={!!toRemove}
        title={t("avatarConfig.voiceScript.deleteConfirm.title")}
        message={t("avatarConfig.voiceScript.deleteConfirm.message", {
          lang: toRemove?.toUpperCase() ?? "",
        })}
        destructive
        confirmLabel={t("common.delete")}
        onClose={() => setToRemove(null)}
        onConfirm={() => {
          if (toRemove) {
            deleteMut.mutate(toRemove);
            setToRemove(null);
          }
        }}
      />
    </div>
  );
}

interface VoiceScriptRowProps {
  script: AvatarVoiceScriptOut;
  flagCountryCode?: string | null;
  onSave: (text: string) => void;
  onDelete: () => void;
}

function VoiceScriptRow({
  script,
  flagCountryCode,
  onSave,
  onDelete,
}: VoiceScriptRowProps) {
  const { t } = useTranslation();
  const [text, setText] = useState(script.text);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    setText(script.text);
  }, [script.text]);
  const Flag = flagFor(script.language_code, flagCountryCode);
  const dirty = text !== script.text;

  const cancel = () => {
    setText(script.text);
    setEditing(false);
  };
  const save = () => {
    if (!text.trim() || !dirty) {
      setEditing(false);
      return;
    }
    onSave(text);
    setEditing(false);
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center gap-2">
          <BookOpen className="size-4 text-muted-foreground" />
          <Flag className="size-4" />
          <span className="font-mono text-sm uppercase">{script.language_code}</span>
          <div className="flex-1" />
          {!editing && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditing(true)}
              >
                <Pencil className="size-4" />
                {t("common.edit")}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="text-destructive"
                onClick={onDelete}
                title={t("common.delete")}
              >
                <Trash2 className="size-4" />
              </Button>
            </>
          )}
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t("avatarConfig.voiceScript.text")}</Label>
          {editing ? (
            <Textarea
              rows={6}
              value={text}
              onChange={(e) => setText(e.target.value)}
              autoFocus
            />
          ) : (
            <p className="whitespace-pre-line rounded-md border border-border bg-muted/30 p-3 text-sm leading-relaxed">
              {script.text}
            </p>
          )}
        </div>
        {editing && (
          <div className="flex items-center justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={cancel}>
              <X className="size-4" />
              {t("common.cancel")}
            </Button>
            <Button size="sm" onClick={save} disabled={!dirty || !text.trim()}>
              {t("common.save")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface PromptRowProps {
  prompt: AvatarClipPromptOut;
  isFirst: boolean;
  isLast: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onUpdate: (payload: AvatarClipPromptUpdate) => void;
  onDelete: () => void;
}

function PromptRow({
  prompt,
  isFirst,
  isLast,
  onMoveUp,
  onMoveDown,
  onUpdate,
  onDelete,
}: PromptRowProps) {
  const { t } = useTranslation();
  const [label, setLabel] = useState(prompt.label_it ?? "");
  const [text, setText] = useState(prompt.prompt);

  const labelChanged = (label || null) !== (prompt.label_it ?? null);
  const textChanged = text !== prompt.prompt;
  const dirty = labelChanged || textChanged;

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center gap-2">
          <Badge variant="secondary" className="font-mono">
            #{prompt.position + 1}
          </Badge>
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="icon"
            disabled={isFirst}
            onClick={onMoveUp}
            title={t("avatarConfig.moveUp")}
          >
            <ArrowUp className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            disabled={isLast}
            onClick={onMoveDown}
            title={t("avatarConfig.moveDown")}
          >
            <ArrowDown className="size-4" />
          </Button>
          <div className="flex items-center gap-2 ps-3">
            <Switch
              id={`active-${prompt.id}`}
              checked={prompt.is_active}
              onCheckedChange={(v) => onUpdate({ is_active: v })}
            />
            <Label htmlFor={`active-${prompt.id}`} className="text-xs">
              {t("avatarConfig.fields.active")}
            </Label>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="text-destructive"
            onClick={onDelete}
            title={t("common.delete")}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
        <div className="grid gap-3 md:grid-cols-[180px_1fr]">
          <div className="space-y-1.5">
            <Label className="text-xs">{t("avatarConfig.fields.label")}</Label>
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              onBlur={() => {
                if (labelChanged) onUpdate({ label_it: label || null });
              }}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">{t("avatarConfig.fields.prompt")}</Label>
            <Textarea
              rows={3}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onBlur={() => {
                if (textChanged && text.trim()) onUpdate({ prompt: text });
              }}
            />
          </div>
        </div>
        {dirty && (
          <div className="text-xs text-muted-foreground">
            {t("avatarConfig.unsavedHint")}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
