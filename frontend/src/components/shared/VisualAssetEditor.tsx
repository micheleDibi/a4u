import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, EyeOff, Loader2, Sparkles, Trash2 } from "lucide-react";
import type { FigureVariant } from "@/lib/figureFormats";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { coursesApi, type LessonContentVisualAsset } from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { CourseRefContext } from "@/contexts/CourseRefContext";
import { extractApiError } from "@/lib/errors";
import { formatLabel, isLegacyFormat } from "@/lib/figureFormats";
import { mediaUrl } from "@/lib/media";

import { DotEditor } from "./DotEditor";
import { FunctionEditor } from "./FunctionEditor";
import { MermaidEditor } from "./MermaidEditor";
import { SourceFigure } from "./SourceFigure";
import { VegaLiteEditor } from "./VegaLiteEditor";

/**
 * Card di modifica di un asset visivo, condivisa dai dialog delle Dispense
 * (`LessonContentEditDialog`) e delle Slide (`LessonSlidesEditDialog`), che
 * prima ne duplicavano ~220 righe. Le differenze restano ai chiamanti:
 * `idSlot` (il `RefIdField` con il token `[FIG:id]` e la rinomina dei
 * riferimenti nelle Dispense, un `Input` nelle slide), `headerActions`
 * (pulsante «Evidenzia dove usato»), `labels` (chiavi diverse).
 *
 * Per formato: Mermaid, Vega-Lite, DOT e `function` con il rispettivo
 * editor e anteprima; `image` con la digitalizzazione in Mermaid; legacy
 * in sola lettura. `error` è il 422 per asset del PATCH: gli editor dei
 * formati nuovi lo mostrano in testa, per gli altri lo mostra la card.
 *
 * Figure di fonte (`source_figure`): anteprima con la riga «Fonte» del
 * backend (`SourceFigure`, mai ricomposta qui), riferimento alla figura
 * NON modificabile (solo didascalia e testo alternativo), «Non proporre
 * più» (esclusione dalle proposte, non retroattiva) e gli avvisi del
 * revisore delle ridondanze (`notices`). `onMoveUp`/`onMoveDown` riordinano
 * le card (per tutti i formati): la numerazione resta quella della prima
 * citazione `[FIG:id]` nel testo.
 */
export interface VisualAssetEditorLabels {
  caption: string;
  altText: string;
  remove: string;
}

export interface VisualAssetEditorProps {
  /** Superficie di destinazione dell'asset: decide il box con cui
   * l'anteprima sceglie la direzione della catena. */
  variant?: FigureVariant;
  orgId: string;
  courseId: string;
  asset: LessonContentVisualAsset;
  onChange: (patch: Partial<LessonContentVisualAsset>) => void;
  onDelete: () => void;
  disabled: boolean;
  idSlot: ReactNode;
  headerActions?: ReactNode;
  labels: VisualAssetEditorLabels;
  error?: string | null;
  /** Avvisi del revisore delle figure di fonte (solo testo). */
  notices?: string[];
  onMoveUp?: () => void;
  onMoveDown?: () => void;
}

export function VisualAssetEditor({
  variant = "lesson",
  orgId,
  courseId,
  asset,
  onChange,
  onDelete,
  disabled,
  idSlot,
  headerActions,
  labels,
  error,
  notices,
  onMoveUp,
  onMoveDown,
}: VisualAssetEditorProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [converting, setConverting] = useState(false);
  const [excluding, setExcluding] = useState(false);
  const isSource = asset.format === "source_figure";

  const handleExclude = async () => {
    setExcluding(true);
    try {
      await coursesApi.documentFigures.update(orgId, courseId, asset.content.trim(), {
        excluded_by_user: true,
      });
      await queryClient.invalidateQueries({
        queryKey: ["document-figures", orgId, courseId],
      });
      toast.success(t("courses.sourceFigures.excluded"));
    } catch (err) {
      toast.error(extractApiError(err).message ?? t("courses.sourceFigures.excludeFailed"));
    } finally {
      setExcluding(false);
    }
  };

  const isLegacy = isLegacyFormat(asset.format);
  const editorShowsError =
    asset.format === "vegalite" ||
    asset.format === "dot" ||
    asset.format === "function";

  const handleConvertToMermaid = async () => {
    if (asset.format !== "image" || !asset.content) return;
    setConverting(true);
    try {
      const { mermaid_code } = await coursesApi.lessonAssets.convertToMermaid(
        orgId,
        courseId,
        asset.content,
      );
      onChange({ format: "mermaid", content: mermaid_code });
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
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">{idSlot}</div>
        <Badge variant="outline" className="text-[11px]">
          {formatLabel(asset.format, t)}
        </Badge>
        {headerActions}
        {onMoveUp && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={onMoveUp}
            disabled={disabled}
            aria-label={t("courses.sourceFigures.moveUp")}
            title={t("courses.sourceFigures.moveUp")}
          >
            <ArrowUp className="size-3.5" />
          </Button>
        )}
        {onMoveDown && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={onMoveDown}
            disabled={disabled}
            aria-label={t("courses.sourceFigures.moveDown")}
            title={t("courses.sourceFigures.moveDown")}
          >
            <ArrowDown className="size-3.5" />
          </Button>
        )}
      </div>

      {notices && notices.length > 0 && (
        <div className="rounded-md border border-amber-300/60 bg-amber-50/60 px-3 py-2 text-xs text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-200">
          <div className="font-semibold">{t("courses.sourceFigures.notices.title")}</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {notices.map((notice, i) => (
              <li key={i}>{notice}</li>
            ))}
          </ul>
        </div>
      )}

      {error && !editorShowsError && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          <div className="font-semibold">
            {t("courses.lessonsContent.editor.assetRejected")}
          </div>
          <div className="mt-0.5 break-words font-mono">{error}</div>
        </div>
      )}

      {asset.format === "mermaid" && (
        <MermaidEditor
          value={asset.content}
          onChange={(code) => onChange({ content: code })}
          disabled={disabled}
          variant={variant}
        />
      )}

      {asset.format === "vegalite" && (
        <VegaLiteEditor
          value={asset.content}
          onChange={(code) => onChange({ content: code })}
          disabled={disabled}
          error={error}
        />
      )}

      {asset.format === "dot" && (
        <DotEditor
          value={asset.content}
          onChange={(code) => onChange({ content: code })}
          disabled={disabled}
          error={error}
        />
      )}

      {asset.format === "function" && (
        <FunctionEditor
          value={asset.content}
          onChange={(content) => onChange({ content })}
          disabled={disabled}
          orgId={orgId}
          courseId={courseId}
          error={error}
        />
      )}

      {asset.format === "image" && (
        <div className="space-y-2">
          <div className="overflow-hidden rounded-md border bg-background">
            <img
              src={mediaUrl(asset.content)}
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

      {isSource && (
        <div className="space-y-2">
          <CourseRefContext.Provider value={{ orgId, courseId }}>
            <SourceFigure
              assetId={asset.asset_id}
              figureId={asset.content}
              caption={asset.caption}
              altText={asset.alt_text}
              preview
            />
          </CourseRefContext.Provider>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleExclude}
            disabled={disabled || excluding}
          >
            {excluding ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <EyeOff className="size-3.5" />
            )}
            {t("courses.sourceFigures.exclude")}
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
          <Label>{labels.caption}</Label>
          <Input
            value={asset.caption}
            onChange={(e) => onChange({ caption: e.target.value })}
            disabled={disabled}
          />
        </div>
        <div className="space-y-1.5">
          <Label>{labels.altText}</Label>
          <Input
            value={asset.alt_text}
            onChange={(e) => onChange({ alt_text: e.target.value })}
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
          onClick={onDelete}
          disabled={disabled}
        >
          <Trash2 className="size-3.5" />
          {labels.remove}
        </Button>
      </div>
    </div>
  );
}

export default VisualAssetEditor;
