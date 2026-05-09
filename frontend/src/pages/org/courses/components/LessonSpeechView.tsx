import { useTranslation } from "react-i18next";

import type {
  LessonSpeechRaw,
  LessonSpeechSegment,
  LessonSlidesRaw,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";

interface Props {
  speech: LessonSpeechRaw;
  slides: LessonSlidesRaw | null;
}

/**
 * Viewer read-only del discorso temporizzato di una lezione (Fase 5 §8).
 *
 * Layout: lista verticale RAGGRUPPATA per slide (mirror del PDF). Per
 * ciascuna slide, mostra titolo + durata totale + segmenti con range
 * temporale cumulativo `[mm:ss — mm:ss]` e (opzionale) note al docente.
 *
 * La timeline cumulativa è ricalcolata lato FE sommando le durate dei
 * segmenti precedenti — non ci affidiamo al campo
 * `slide_total_duration_seconds` salvato perché il rendering deve
 * mostrare il tempo cumulativo di ciascun segmento, non il totale slide.
 */
export function LessonSpeechView({ speech, slides }: Props) {
  const { t } = useTranslation();

  // Index per lookup veloce.
  const segById = new Map<string, LessonSpeechSegment>();
  for (const seg of speech.speech_segments) {
    segById.set(seg.segment_id, seg);
  }

  // Durata cumulativa di ciascun segmento (offset dall'inizio della lezione).
  // Calcolata seguendo l'ordine slide → segment_ids[] del map.
  const cumulativeStart = new Map<string, number>();
  let runningTotal = 0;
  for (const entry of speech.slide_to_segments_map) {
    for (const sid of entry.segment_ids) {
      cumulativeStart.set(sid, runningTotal);
      const seg = segById.get(sid);
      if (seg) runningTotal += seg.estimated_duration_seconds;
    }
  }

  const slideTitleByCode = new Map<
    string,
    { number: number; title: string }
  >();
  if (slides) {
    for (const s of slides.slides) {
      slideTitleByCode.set(s.slide_id, {
        number: s.slide_number,
        title: s.title,
      });
    }
  }

  return (
    <div className="space-y-4">
      {/* Header globale: durata totale + word count */}
      <div className="flex flex-wrap items-center gap-3 rounded-md border bg-muted/20 px-3 py-2 text-xs">
        <Badge variant="muted" className="font-mono text-[11px]">
          {t("courses.lessonsSpeech.render.totalDuration", {
            duration: formatMmSs(speech.estimated_total_duration_seconds),
          })}
        </Badge>
        <Badge variant="muted" className="font-mono text-[11px]">
          {t("courses.lessonsSpeech.render.totalWordCount", {
            count: speech.estimated_total_word_count,
          })}
        </Badge>
      </div>

      {/* Lista slide con segmenti */}
      <div className="space-y-3">
        {speech.slide_to_segments_map.map((entry) => {
          const slideMeta = slideTitleByCode.get(entry.slide_id);
          return (
            <Card key={entry.slide_id}>
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {slideMeta ? (
                      <>
                        <Badge
                          variant="outline"
                          className="font-mono text-[11px]"
                        >
                          {slideMeta.number}
                        </Badge>
                        <h4 className="text-base font-semibold">
                          {slideMeta.title}
                        </h4>
                      </>
                    ) : (
                      <span className="text-sm italic text-destructive">
                        {t("courses.lessonsSpeech.render.slideMissing", {
                          slideId: entry.slide_id,
                        })}
                      </span>
                    )}
                  </div>
                  <Badge variant="muted" className="font-mono text-[10px]">
                    {formatMmSs(entry.slide_total_duration_seconds)}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                {entry.segment_ids.length === 0 ? (
                  <p className="text-xs italic text-muted-foreground">
                    {t("courses.lessonsSpeech.render.noSegments")}
                  </p>
                ) : (
                  entry.segment_ids.map((sid) => {
                    const seg = segById.get(sid);
                    if (!seg) return null;
                    const start = cumulativeStart.get(sid) ?? 0;
                    const end = start + seg.estimated_duration_seconds;
                    return (
                      <SegmentBlock
                        key={sid}
                        segment={seg}
                        timelineStart={start}
                        timelineEnd={end}
                      />
                    );
                  })
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

interface SegmentBlockProps {
  segment: LessonSpeechSegment;
  timelineStart: number;
  timelineEnd: number;
}

function SegmentBlock({
  segment,
  timelineStart,
  timelineEnd,
}: SegmentBlockProps) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1.5 border-l-2 border-primary/30 pl-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono tabular-nums text-primary/80">
          {t("courses.lessonsSpeech.render.slideTimeline", {
            start: formatMmSs(timelineStart),
            end: formatMmSs(timelineEnd),
          })}
        </span>
        <span className="font-mono tabular-nums text-muted-foreground">
          {t("courses.lessonsSpeech.render.durationLabel", {
            seconds: segment.estimated_duration_seconds,
          })}
        </span>
      </div>
      <p className="text-sm leading-relaxed">{segment.text}</p>
      {segment.delivery_notes && (
        <p className="text-xs italic text-muted-foreground">
          <span className="font-medium not-italic">
            {t("courses.lessonsSpeech.render.deliveryNotes")}:
          </span>{" "}
          {segment.delivery_notes}
        </p>
      )}
    </div>
  );
}

/** Format `seconds` (number) as `mm:ss`. */
export function formatMmSs(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

export default LessonSpeechView;
