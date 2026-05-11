import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Plus, Save, Trash2 } from "lucide-react";

import type {
  LessonSpeechRaw,
  LessonSpeechSegment,
  LessonSpeechUpdateInput,
  LessonSlideSegmentsMapEntry,
  LessonSlidesRaw,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

interface Props {
  open: boolean;
  isPending: boolean;
  lessonLabel: string;
  initial: LessonSpeechRaw;
  slidesRaw: LessonSlidesRaw | null;
  /** target durata totale = `lesson_duration_minutes * 60` (sec). */
  targetDurationSeconds: number;
  /** Lingua del corso, per il calcolo automatico durata da word count. */
  languageCode: string;
  onClose: () => void;
  onSubmit: (payload: LessonSpeechUpdateInput) => void;
}

// Convenzioni words-per-minute (mirror del BE).
const WORDS_PER_MINUTE: Record<string, number> = {
  it: 130,
  en: 150,
};
function wpmFor(lang: string): number {
  if (!lang) return 130;
  const key = lang.trim().toLowerCase().slice(0, 2);
  return WORDS_PER_MINUTE[key] ?? 130;
}

// Caratteri proibiti TTS-safe (mirror del BE).
const TTS_FORBIDDEN_CHARS = ["*", "_", "`", "#", "\\", "$"];
const TTS_ABBR_RE =
  /\b(es\.|etc\.|ca\.|p\.es\.|i\.e\.|e\.g\.)/i;
// eslint-disable-next-line no-useless-escape
const TTS_LATEX_RE =
  /\\(frac|sum|int|cdot|alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega|sqrt|infty|partial|nabla|times|leq|geq|neq|approx|propto|in|notin|forall|exists|emptyset|cap|cup|subset|supset|begin|end|left|right|mathrm|mathbf|mathit|text|textbf|textit|hline|cline|cr|displaystyle|over|underline|overline|hat|tilde|bar|vec|dot|ddot|prime|widehat|widetilde)\b/;

function hasTtsViolation(text: string): boolean {
  if (!text) return false;
  for (const c of TTS_FORBIDDEN_CHARS) {
    if (text.includes(c)) return true;
  }
  if (TTS_ABBR_RE.test(text)) return true;
  if (TTS_LATEX_RE.test(text)) return true;
  return false;
}

function wordCount(text: string): number {
  if (!text) return 0;
  return text.split(/\s+/).filter((w) => w.trim()).length;
}

function autoDuration(text: string, lang: string): number {
  const wpm = wpmFor(lang);
  const words = wordCount(text);
  if (words === 0) return 1;
  return Math.max(1, Math.round((words / wpm) * 60));
}

let __id_counter = 0;
function nextSegmentId(existing: Set<string>): string {
  for (let i = 1; i <= 9999; i++) {
    const candidate = `SEG${String(i).padStart(3, "0")}`;
    if (!existing.has(candidate)) return candidate;
  }
  // Fallback (improbabile)
  __id_counter += 1;
  return `SEGX${__id_counter}`;
}

interface DraftSegment extends LessonSpeechSegment {
  // marker locale per UI (non inviato al BE)
  _key: string;
}

/**
 * Editor del discorso temporizzato (Fase 5 §8).
 *
 * Layout: lista di slide (lookup da `slidesRaw`). Per ciascuna slide,
 * lista dei segmenti che la coprono con campi: ID (read-only), text,
 * estimated_duration_seconds (con bottone "Auto" che ricalcola da
 * word count × 60/wpm), delivery_notes, slide_id select.
 *
 * Validazione client: TTS-safety inline (warning chip), durata totale
 * fuori range ±5% (warning footer). Hard validation server-side.
 *
 * Submit ricostruisce `slide_to_segments_map` dal raggruppamento per
 * slide_id dei segmenti, somma durate per slide_total_duration_seconds.
 */
export function LessonSpeechEditDialog({
  open,
  isPending,
  lessonLabel,
  initial,
  slidesRaw,
  targetDurationSeconds,
  languageCode,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();

  const slideOptions = useMemo(() => {
    if (!slidesRaw) return [];
    return slidesRaw.slides.map((s) => ({
      value: s.slide_id,
      label: t("courses.lessonsSpeech.render.slideHeader", {
        number: s.slide_number,
        title: s.title,
      }),
      number: s.slide_number,
    }));
  }, [slidesRaw, t]);

  // Lazy init: clona `initial.speech_segments` UNA volta al mount. Vedi note
  // in LessonStructureEditDialog: senza questo, l'effect su `[open, initial]`
  // (o sue sotto-proprietà) rifirerebbe ad ogni re-render del parent
  // (TanStack Query polling cambia il riferimento dell'array) — resettando
  // lo state mentre l'utente aggiunge/modifica segmenti.
  const [segments, setSegments] = useState<DraftSegment[]>(() =>
    initial.speech_segments.map((s, idx) => ({ ...s, _key: `init-${idx}` })),
  );

  const segmentIdsSet = useMemo(
    () => new Set(segments.map((s) => s.segment_id)),
    [segments],
  );

  const sumDurations = useMemo(
    () =>
      segments.reduce((acc, s) => acc + (s.estimated_duration_seconds || 0), 0),
    [segments],
  );
  const lowBound = Math.round(targetDurationSeconds * 0.95);
  const highBound = Math.round(targetDurationSeconds * 1.05);
  const durationOk =
    sumDurations >= lowBound && sumDurations <= highBound;

  // Raggruppa visivamente per slide.
  const segmentsBySlide = useMemo(() => {
    const map = new Map<string, DraftSegment[]>();
    for (const s of segments) {
      const arr = map.get(s.slide_id) ?? [];
      arr.push(s);
      map.set(s.slide_id, arr);
    }
    return map;
  }, [segments]);

  const slidesInOrder = slidesRaw?.slides ?? [];

  // Aggiunge un nuovo segmento sotto una specifica slide.
  const addSegmentForSlide = (slideId: string) => {
    const newId = nextSegmentId(segmentIdsSet);
    setSegments((prev) => [
      ...prev,
      {
        segment_id: newId,
        slide_id: slideId,
        text: "",
        estimated_duration_seconds: 30,
        delivery_notes: "",
        _key: `new-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      },
    ]);
  };

  const removeSegment = (key: string) => {
    setSegments((prev) => prev.filter((s) => s._key !== key));
  };

  const updateSegment = (
    key: string,
    patch: Partial<LessonSpeechSegment>,
  ) => {
    setSegments((prev) =>
      prev.map((s) => (s._key === key ? { ...s, ...patch } : s)),
    );
  };

  const handleAutoDuration = (key: string) => {
    setSegments((prev) =>
      prev.map((s) =>
        s._key === key
          ? {
              ...s,
              estimated_duration_seconds: autoDuration(s.text, languageCode),
            }
          : s,
      ),
    );
  };

  const handleSubmit = () => {
    // Costruisce `slide_to_segments_map` raggruppando per slide_id
    // nell'ordine di apparizione delle slide originali (le slide non
    // referenziate restano fuori).
    const order = new Map<string, number>();
    if (slidesRaw) {
      slidesRaw.slides.forEach((s, idx) => order.set(s.slide_id, idx));
    }
    const grouped = new Map<string, LessonSpeechSegment[]>();
    for (const s of segments) {
      const arr = grouped.get(s.slide_id) ?? [];
      const { _key, ...clean } = s;
      void _key;
      arr.push(clean);
      grouped.set(s.slide_id, arr);
    }
    const orderedEntries: LessonSlideSegmentsMapEntry[] = [];
    const sortedSlideIds = Array.from(grouped.keys()).sort((a, b) => {
      const aIdx = order.get(a) ?? 999_999;
      const bIdx = order.get(b) ?? 999_999;
      return aIdx - bIdx;
    });
    for (const sid of sortedSlideIds) {
      const arr = grouped.get(sid) ?? [];
      orderedEntries.push({
        slide_id: sid,
        segment_ids: arr.map((s) => s.segment_id),
        slide_total_duration_seconds: arr.reduce(
          (acc, s) => acc + (s.estimated_duration_seconds || 0),
          0,
        ),
      });
    }

    // I segmenti vengono riordinati seguendo l'ordine slide.
    const orderedSegments: LessonSpeechSegment[] = [];
    for (const entry of orderedEntries) {
      for (const sid of entry.segment_ids) {
        const seg = (grouped.get(entry.slide_id) ?? []).find(
          (s) => s.segment_id === sid,
        );
        if (seg) orderedSegments.push(seg);
      }
    }

    onSubmit({
      speech_segments: orderedSegments,
      slide_to_segments_map: orderedEntries,
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {t("courses.lessonsSpeech.dialog.edit.title", {
              lesson: lessonLabel,
            })}
          </DialogTitle>
          <DialogDescription>
            {t("courses.lessonsSpeech.dialog.edit.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {slidesInOrder.length === 0 ? (
            <p className="text-sm italic text-muted-foreground">
              (nessuna slide disponibile)
            </p>
          ) : (
            slidesInOrder.map((slide) => {
              const slideSegments = segmentsBySlide.get(slide.slide_id) ?? [];
              return (
                <div
                  key={slide.slide_id}
                  className="rounded-md border bg-muted/10"
                >
                  <div className="flex items-center gap-2 border-b bg-muted/20 px-3 py-2">
                    <Badge variant="outline" className="font-mono text-[11px]">
                      {slide.slide_number}
                    </Badge>
                    <h4 className="text-sm font-semibold flex-1 truncate">
                      {slide.title}
                    </h4>
                    {slideSegments.length === 0 && (
                      <Badge
                        variant="warning"
                        className="text-[10px] gap-1"
                        title={t(
                          "courses.lessonsSpeech.editor.uncoveredSlide",
                        )}
                      >
                        <AlertTriangle className="size-3" />
                        {t("courses.lessonsSpeech.editor.uncoveredSlide")}
                      </Badge>
                    )}
                  </div>
                  <div className="space-y-3 px-3 py-3">
                    {slideSegments.map((seg) => (
                      <SegmentEditor
                        key={seg._key}
                        segment={seg}
                        slideOptions={slideOptions}
                        onChange={(patch) => updateSegment(seg._key, patch)}
                        onRemove={() => removeSegment(seg._key)}
                        onAutoDuration={() => handleAutoDuration(seg._key)}
                      />
                    ))}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => addSegmentForSlide(slide.slide_id)}
                      disabled={isPending}
                    >
                      <Plus className="size-3.5" />
                      {t("courses.lessonsSpeech.editor.addSegment")}
                    </Button>
                  </div>
                </div>
              );
            })
          )}

          {/* Footer warning durata */}
          <div
            className={
              durationOk
                ? "rounded-md border border-emerald-300 bg-emerald-50 p-3 text-xs text-emerald-900 dark:border-emerald-700/50 dark:bg-emerald-500/10 dark:text-emerald-200"
                : "rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-700/50 dark:bg-amber-500/10 dark:text-amber-200"
            }
          >
            {durationOk
              ? t("courses.lessonsSpeech.editor.durationOk", {
                  actual: sumDurations,
                  target: targetDurationSeconds,
                })
              : t("courses.lessonsSpeech.editor.durationOutOfRange", {
                  actual: sumDurations,
                  low: lowBound,
                  high: highBound,
                  target: targetDurationSeconds,
                })}
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={isPending}>
            <Save className="size-4" />
            {isPending
              ? t("courses.lessonsSpeech.dialog.edit.saving")
              : t("courses.lessonsSpeech.dialog.edit.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface SegmentEditorProps {
  segment: DraftSegment;
  slideOptions: { value: string; label: string; number: number }[];
  onChange: (patch: Partial<LessonSpeechSegment>) => void;
  onRemove: () => void;
  onAutoDuration: () => void;
}

function SegmentEditor({
  segment,
  slideOptions,
  onChange,
  onRemove,
  onAutoDuration,
}: SegmentEditorProps) {
  const { t } = useTranslation();
  const ttsViolation = hasTtsViolation(segment.text);

  return (
    <div className="space-y-2 rounded-md border bg-background p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="muted" className="font-mono text-[10px]">
          {segment.segment_id}
        </Badge>
        <div className="flex items-center gap-1.5">
          <Label className="text-xs text-muted-foreground">
            {t("courses.lessonsSpeech.editor.slideId")}:
          </Label>
          <Select
            value={segment.slide_id}
            onValueChange={(v) => onChange({ slide_id: v })}
          >
            <SelectTrigger className="h-7 w-[260px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {slideOptions.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <Label className="text-xs text-muted-foreground">
            {t("courses.lessonsSpeech.editor.durationSeconds")}:
          </Label>
          <Input
            type="number"
            min={1}
            max={600}
            value={segment.estimated_duration_seconds}
            onChange={(e) =>
              onChange({
                estimated_duration_seconds: Math.max(
                  1,
                  Math.min(600, parseInt(e.target.value || "1", 10) || 1),
                ),
              })
            }
            className="h-7 w-[78px] text-xs tabular-nums"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 px-2 text-[11px]"
            onClick={onAutoDuration}
            title={`${wordCount(segment.text)} parole`}
          >
            {t("courses.lessonsSpeech.editor.autoDuration")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 text-destructive hover:text-destructive"
            onClick={onRemove}
            title={t("courses.lessonsSpeech.editor.removeSegment")}
          >
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>

      <div>
        <Label className="text-xs text-muted-foreground">
          {t("courses.lessonsSpeech.editor.text")}
        </Label>
        <Textarea
          rows={4}
          value={segment.text}
          onChange={(e) => onChange({ text: e.target.value })}
          placeholder={t("courses.lessonsSpeech.editor.textPlaceholder")}
          className={
            ttsViolation
              ? "border-amber-400 focus-visible:ring-amber-300"
              : undefined
          }
        />
        {ttsViolation && (
          <div className="mt-1 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300">
            <AlertTriangle className="size-3" />
            {t("courses.lessonsSpeech.editor.ttsSafetyWarning")}
          </div>
        )}
      </div>

      <div>
        <Label className="text-xs text-muted-foreground">
          {t("courses.lessonsSpeech.editor.deliveryNotes")}
        </Label>
        <Textarea
          rows={1}
          value={segment.delivery_notes}
          onChange={(e) => onChange({ delivery_notes: e.target.value })}
          placeholder={t(
            "courses.lessonsSpeech.editor.deliveryNotesPlaceholder",
          )}
        />
      </div>
    </div>
  );
}
