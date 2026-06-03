import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  ChevronRight,
  Image as ImageIcon,
  Loader2,
  Plus,
  Save,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import {
  coursesApi,
  type LessonContentRaw,
  type LessonSlideItem,
  type LessonSlideNewAsset,
  type LessonSlidesRaw,
  type LessonSlidesUpdateInput,
  type SlideType,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { MermaidEditor } from "@/components/shared/MermaidEditor";
import { extractApiError } from "@/lib/errors";
import { listAvailableAssets } from "@/lib/slides";

const SLIDE_TYPES: SlideType[] = [
  "title",
  "agenda",
  "prerequisites",
  "concept",
  "definition",
  "diagram",
  "formula",
  "table",
  "example",
  "case_study",
  "exercise",
  "discussion",
  "summary",
  "takeaways",
  "references",
  "bibliography",
];

// L'editor degli asset nuovi è identico a quello delle Dispense (content
// editor): upload immagine + Mermaid scritto a mano con anteprima live +
// conversione immagine→Mermaid. `asset_type` è stato rimosso dal modello.

interface Props {
  open: boolean;
  isPending: boolean;
  lessonLabel: string;
  orgId: string;
  courseId: string;
  initial: LessonSlidesRaw;
  contentRaw: LessonContentRaw | null;
  onClose: () => void;
  onSubmit: (payload: LessonSlidesUpdateInput) => void;
}

/**
 * Editor delle slide della lezione (Fase 4 §7).
 *
 * Layout: lista verticale di slide collapsabili. Ogni slide ha
 * title, type select, bullets (list editabile), references_assets
 * multi-select dall'unione contentRaw + new_assets, source_section_id
 * select dalle sezioni di Fase 3.
 *
 * Sezione separata in basso per gli asset NUOVI (specifici di Fase 4).
 *
 * Validazione lato client: hard fail su submit se slide_id duplicati o
 * vuoti, new_asset_id duplicati. Le altre regole le applica il BE.
 */
export function LessonSlidesEditDialog({
  open,
  isPending,
  lessonLabel,
  orgId,
  courseId,
  initial,
  contentRaw,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  // Lazy init: clona `initial` UNA volta al mount. Vedi note in
  // LessonStructureEditDialog: `initial` è inline-object dal parent e cambia
  // riferimento ad ogni re-render (polling TanStack Query), quindi un effect
  // con `[open, initial]` resetterebbe lo state mentre l'utente sta
  // modificando — facendo sparire le slide/asset appena aggiunti.
  const [slides, setSlides] = useState<LessonSlideItem[]>(() => initial.slides);
  const [newAssets, setNewAssets] = useState<LessonSlideNewAsset[]>(
    () => initial.new_assets,
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Asset disponibili per le multi-select references_assets.
  const availableAssets = useMemo(
    () => listAvailableAssets(contentRaw, newAssets),
    [contentRaw, newAssets],
  );

  // Sezioni disponibili per source_section_id.
  const availableSections = useMemo(() => {
    if (!contentRaw) return [];
    return contentRaw.sections.map((s) => ({
      id: s.section_id,
      title: s.title,
    }));
  }, [contentRaw]);

  const toggleExpanded = (slideId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(slideId)) next.delete(slideId);
      else next.add(slideId);
      return next;
    });
  };

  const updateSlide = (idx: number, patch: Partial<LessonSlideItem>) => {
    setSlides((prev) =>
      prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)),
    );
  };

  const addSlide = () => {
    const nextNum = slides.length + 1;
    const newSlide: LessonSlideItem = {
      slide_number: nextNum,
      slide_id: `S${String(nextNum).padStart(2, "0")}`,
      type: "concept",
      title: "",
      body: "",
      bullets: [],
      references_assets: [],
      source_section_id: "",
    };
    setSlides((prev) => [...prev, newSlide]);
    setExpanded((prev) => new Set(prev).add(newSlide.slide_id));
  };

  const removeSlide = (idx: number) => {
    setSlides((prev) => {
      const next = prev.filter((_, i) => i !== idx);
      // Rinumera slide_number sequenziali.
      return next.map((s, i) => ({ ...s, slide_number: i + 1 }));
    });
  };

  const addBullet = (slideIdx: number) => {
    updateSlide(slideIdx, {
      bullets: [...slides[slideIdx].bullets, ""],
    });
  };

  const updateBullet = (slideIdx: number, bulletIdx: number, value: string) => {
    const newBullets = [...slides[slideIdx].bullets];
    newBullets[bulletIdx] = value;
    updateSlide(slideIdx, { bullets: newBullets });
  };

  const removeBullet = (slideIdx: number, bulletIdx: number) => {
    updateSlide(slideIdx, {
      bullets: slides[slideIdx].bullets.filter((_, i) => i !== bulletIdx),
    });
  };

  const toggleAssetRef = (slideIdx: number, assetId: string) => {
    const current = slides[slideIdx].references_assets;
    const next = current.includes(assetId)
      ? current.filter((a) => a !== assetId)
      : [...current, assetId];
    updateSlide(slideIdx, { references_assets: next });
  };

  const nextNewAssetId = () => {
    const existing = new Set(newAssets.map((a) => a.asset_id));
    let n = newAssets.length + 1;
    while (existing.has(`asset_new_${n}`)) n += 1;
    return `asset_new_${n}`;
  };

  const addMermaidAsset = () => {
    setNewAssets((prev) => [
      ...prev,
      {
        asset_id: nextNewAssetId(),
        format: "mermaid",
        content: "",
        caption: "",
        alt_text: "",
      },
    ]);
  };

  const addImageAsset = (path: string) => {
    setNewAssets((prev) => [
      ...prev,
      {
        asset_id: nextNewAssetId(),
        format: "image",
        content: path,
        caption: "",
        alt_text: "",
      },
    ]);
  };

  const updateNewAsset = (
    idx: number,
    patch: Partial<LessonSlideNewAsset>,
  ) => {
    setNewAssets((prev) =>
      prev.map((a, i) => (i === idx ? { ...a, ...patch } : a)),
    );
  };

  const removeNewAsset = (idx: number) => {
    setNewAssets((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmit = () => {
    // Scarta gli asset nuovi senza contenuto (card aggiunta ma lasciata
    // vuota): il BE richiede content non vuoto e li rifiuterebbe con un
    // 422. Mermaid vuoto o immagine non caricata = asset incompleto.
    const cleanedAssets = newAssets.filter((a) => a.content.trim().length > 0);
    const payload: LessonSlidesUpdateInput = {
      slides,
      new_assets: cleanedAssets,
    };
    onSubmit(payload);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>
            {t("courses.lessonsSlides.dialog.edit.title", {
              lesson: lessonLabel,
            })}
          </DialogTitle>
          <DialogDescription>
            {t("courses.lessonsSlides.dialog.edit.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {slides.map((slide, idx) => (
            <SlideEditCard
              key={slide.slide_id + ":" + idx}
              slide={slide}
              idx={idx}
              expanded={expanded.has(slide.slide_id)}
              onToggle={() => toggleExpanded(slide.slide_id)}
              onUpdate={(patch) => updateSlide(idx, patch)}
              onRemove={() => removeSlide(idx)}
              onAddBullet={() => addBullet(idx)}
              onUpdateBullet={(bidx, val) => updateBullet(idx, bidx, val)}
              onRemoveBullet={(bidx) => removeBullet(idx, bidx)}
              onToggleAssetRef={(aid) => toggleAssetRef(idx, aid)}
              availableAssets={availableAssets}
              availableSections={availableSections}
              t={t}
            />
          ))}

          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={addSlide}
            disabled={isPending}
          >
            <Plus className="size-4" />
            {t("courses.lessonsSlides.editor.addSlide")}
          </Button>
        </div>

        {/* New assets */}
        <div className="space-y-3 border-t pt-4">
          <h4 className="text-sm font-semibold">
            {t("courses.lessonsSlides.editor.newAssets")}
          </h4>
          {newAssets.map((asset, idx) => (
            <NewAssetEditCard
              key={asset.asset_id + ":" + idx}
              orgId={orgId}
              courseId={courseId}
              asset={asset}
              onUpdate={(patch) => updateNewAsset(idx, patch)}
              onRemove={() => removeNewAsset(idx)}
              disabled={isPending}
              t={t}
            />
          ))}
          <AddNewAssetMenu
            orgId={orgId}
            courseId={courseId}
            onAddMermaid={addMermaidAsset}
            onAddImage={addImageAsset}
            disabled={isPending}
            t={t}
          />
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={isPending}>
            <Save className="size-4" />
            {isPending
              ? t("courses.lessonsSlides.dialog.edit.saving")
              : t("courses.lessonsSlides.dialog.edit.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// SlideEditCard
// ---------------------------------------------------------------------------

interface SlideEditCardProps {
  slide: LessonSlideItem;
  idx: number;
  expanded: boolean;
  onToggle: () => void;
  onUpdate: (patch: Partial<LessonSlideItem>) => void;
  onRemove: () => void;
  onAddBullet: () => void;
  onUpdateBullet: (bidx: number, val: string) => void;
  onRemoveBullet: (bidx: number) => void;
  onToggleAssetRef: (assetId: string) => void;
  availableAssets: ReturnType<typeof listAvailableAssets>;
  availableSections: { id: string; title: string }[];
  t: ReturnType<typeof useTranslation>["t"];
}

function SlideEditCard({
  slide,
  idx: _idx,
  expanded,
  onToggle,
  onUpdate,
  onRemove,
  onAddBullet,
  onUpdateBullet,
  onRemoveBullet,
  onToggleAssetRef,
  availableAssets,
  availableSections,
  t,
}: SlideEditCardProps) {
  return (
    <div className="rounded-md border bg-card">
      <div className="flex w-full items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex flex-1 items-center gap-2 text-left"
          onClick={onToggle}
        >
          {expanded ? (
            <ChevronDown className="size-4 shrink-0" />
          ) : (
            <ChevronRight className="size-4 shrink-0" />
          )}
          <Badge variant="outline" className="font-mono text-[11px]">
            {slide.slide_number}
          </Badge>
          <Badge variant="secondary" className="text-[11px]">
            {t(`courses.lessonsSlides.render.types.${slide.type}`, {
              defaultValue: slide.type,
            })}
          </Badge>
          <span className="flex-1 truncate text-sm font-medium">
            {slide.title || "—"}
          </span>
        </button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7 text-destructive"
          onClick={onRemove}
          title={t("courses.lessonsSlides.editor.removeSlide")}
        >
          <Trash2 className="size-3.5" />
        </Button>
      </div>

      {expanded && (
        <div className="space-y-3 border-t px-4 py-3">
          {/* Title + Type */}
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5 sm:col-span-2">
              <Label>{t("courses.lessonsSlides.editor.title")}</Label>
              <Input
                value={slide.title}
                onChange={(e) => onUpdate({ title: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t("courses.lessonsSlides.editor.type")}</Label>
              <Select
                value={slide.type}
                onValueChange={(v) => onUpdate({ type: v as SlideType })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SLIDE_TYPES.map((tp) => (
                    <SelectItem key={tp} value={tp}>
                      {t(`courses.lessonsSlides.render.types.${tp}`, {
                        defaultValue: tp,
                      })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Body (prosa breve) */}
          <div className="space-y-1.5">
            <Label>{t("courses.lessonsSlides.editor.body")}</Label>
            <Textarea
              rows={3}
              value={slide.body}
              onChange={(e) => onUpdate({ body: e.target.value })}
              placeholder={t("courses.lessonsSlides.editor.bodyPlaceholder", {
                defaultValue:
                  "Prosa breve di 1-3 frasi (opzionale). Lascia vuoto per slide bullet-only.",
              })}
            />
          </div>

          {/* Bullets */}
          <div className="space-y-1.5">
            <Label>{t("courses.lessonsSlides.editor.bullets")}</Label>
            <div className="space-y-2">
              {slide.bullets.map((b, bidx) => (
                <div key={bidx} className="flex items-start gap-2">
                  <Textarea
                    rows={2}
                    value={b}
                    onChange={(e) => onUpdateBullet(bidx, e.target.value)}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0 text-destructive"
                    onClick={() => onRemoveBullet(bidx)}
                    title={t("courses.lessonsSlides.editor.removeBullet")}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={onAddBullet}
              >
                <Plus className="size-3.5" />
                {t("courses.lessonsSlides.editor.addBullet")}
              </Button>
            </div>
          </div>

          {/* Source section */}
          <div className="space-y-1.5">
            <Label>{t("courses.lessonsSlides.editor.sourceSection")}</Label>
            <Select
              value={slide.source_section_id || "__none__"}
              onValueChange={(v) =>
                onUpdate({ source_section_id: v === "__none__" ? "" : v })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">
                  {t("courses.lessonsSlides.editor.noSourceSection")}
                </SelectItem>
                {availableSections.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.id} — {s.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Asset references multi-select */}
          <div className="space-y-1.5">
            <Label>
              {t("courses.lessonsSlides.editor.referencesAssets")}
            </Label>
            <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border p-2">
              {availableAssets.length === 0 ? (
                <p className="text-xs text-muted-foreground">—</p>
              ) : (
                availableAssets.map((opt) => {
                  const checked = slide.references_assets.includes(opt.id);
                  return (
                    <label
                      key={opt.id}
                      className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 text-xs hover:bg-muted/50"
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => onToggleAssetRef(opt.id)}
                      />
                      <span className="flex-1">
                        <span className="font-mono">{opt.label}</span>
                        {opt.caption && (
                          <span className="ml-2 text-muted-foreground">
                            — {opt.caption}
                          </span>
                        )}
                      </span>
                    </label>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// NewAssetEditCard
// ---------------------------------------------------------------------------

// Formati legacy (solo lettura) eventualmente presenti in slides_raw
// generati da AI prima del refactor asset.
const LEGACY_SLIDE_FORMATS: ReadonlyArray<LessonSlideNewAsset["format"]> = [
  "image_prompt",
  "image_search_query",
  "description",
];

type TFn = ReturnType<typeof useTranslation>["t"];

interface NewAssetEditCardProps {
  orgId: string;
  courseId: string;
  asset: LessonSlideNewAsset;
  onUpdate: (patch: Partial<LessonSlideNewAsset>) => void;
  onRemove: () => void;
  disabled: boolean;
  t: TFn;
}

function NewAssetEditCard({
  orgId,
  courseId,
  asset,
  onUpdate,
  onRemove,
  disabled,
  t,
}: NewAssetEditCardProps) {
  const [converting, setConverting] = useState(false);
  const isLegacy = LEGACY_SLIDE_FORMATS.includes(asset.format);

  const handleConvertToMermaid = async () => {
    if (asset.format !== "image" || !asset.content) return;
    setConverting(true);
    try {
      const { mermaid_code } = await coursesApi.lessonAssets.convertToMermaid(
        orgId,
        courseId,
        asset.content,
      );
      onUpdate({ format: "mermaid", content: mermaid_code });
      toast.success(
        t("courses.lessonsContent.editor.assetActions.convertedToMermaid"),
      );
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsContent.editor.assetActions.convertToMermaidFailed"),
      );
    } finally {
      setConverting(false);
    }
  };

  return (
    <div className="space-y-3 rounded-md border bg-muted/20 p-3">
      <div className="space-y-1.5">
        <Label>{t("courses.lessonsSlides.editor.assetIdLabel")}</Label>
        <Input
          value={asset.asset_id}
          onChange={(e) => onUpdate({ asset_id: e.target.value })}
          disabled={disabled || isLegacy}
        />
      </div>

      {asset.format === "mermaid" && (
        <MermaidEditor
          value={asset.content}
          onChange={(code) => onUpdate({ content: code })}
          disabled={disabled}
        />
      )}

      {asset.format === "image" && (
        <div className="space-y-2">
          <div className="overflow-hidden rounded-md border bg-background">
            <img
              src={`/uploads/${asset.content}`}
              alt={asset.alt_text || ""}
              className="block max-h-80 w-full object-contain"
            />
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleConvertToMermaid}
            disabled={disabled || converting}
          >
            {converting ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                {t("courses.lessonsContent.editor.assetActions.convertingToMermaid")}
              </>
            ) : (
              <>
                <Sparkles className="size-3.5" />
                {t("courses.lessonsContent.editor.assetActions.convertToMermaid")}
              </>
            )}
          </Button>
        </div>
      )}

      {isLegacy && (
        <div className="space-y-2">
          <div className="rounded-md border border-amber-400/40 bg-amber-50/40 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            {t("courses.lessonsContent.editor.legacyAssetBanner")}
          </div>
          <Textarea
            rows={4}
            value={asset.content}
            readOnly
            disabled={disabled}
            className="font-mono text-xs"
          />
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label>{t("courses.lessonsSlides.editor.assetCaption")}</Label>
          <Input
            value={asset.caption}
            onChange={(e) => onUpdate({ caption: e.target.value })}
            disabled={disabled}
          />
        </div>
        <div className="space-y-1.5">
          <Label>{t("courses.lessonsSlides.editor.assetAltText")}</Label>
          <Input
            value={asset.alt_text}
            onChange={(e) => onUpdate({ alt_text: e.target.value })}
            disabled={disabled}
          />
        </div>
      </div>
      <div className="flex justify-end">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-destructive"
          onClick={onRemove}
          disabled={disabled}
        >
          <Trash2 className="size-3.5" />
          {t("courses.lessonsSlides.editor.removeNewAsset")}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddNewAssetMenu — dropdown "Carica immagine" / "Scrivi Mermaid"
// (stesso flusso del content editor delle Dispense)
// ---------------------------------------------------------------------------

interface AddNewAssetMenuProps {
  orgId: string;
  courseId: string;
  onAddMermaid: () => void;
  onAddImage: (path: string) => void;
  disabled: boolean;
  t: TFn;
}

function AddNewAssetMenu({
  orgId,
  courseId,
  onAddMermaid,
  onAddImage,
  disabled,
  t,
}: AddNewAssetMenuProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);

  const triggerFilePicker = () => {
    if (disabled || uploading) return;
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    try {
      const { path } = await coursesApi.lessonAssets.upload(
        orgId,
        courseId,
        file,
      );
      onAddImage(path);
      toast.success(
        t("courses.lessonsContent.editor.assetActions.imageUploaded"),
      );
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsContent.editor.assetActions.imageUploadFailed"),
      );
    } finally {
      setUploading(false);
    }
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={handleFileChange}
      />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || uploading}
          >
            {uploading ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                {t("courses.lessonsContent.editor.assetActions.uploading")}
              </>
            ) : (
              <>
                <Plus className="size-4" />
                {t("courses.lessonsSlides.editor.addNewAsset")}
              </>
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onClick={triggerFilePicker} disabled={uploading}>
            <Upload className="size-3.5" />
            {t("courses.lessonsContent.editor.assetActions.uploadImage")}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={onAddMermaid}>
            <ImageIcon className="size-3.5" />
            {t("courses.lessonsContent.editor.assetActions.writeMermaid")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

export default LessonSlidesEditDialog;
