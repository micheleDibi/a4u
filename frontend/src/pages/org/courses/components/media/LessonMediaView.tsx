import { useState } from "react";

import type { CourseLessonOut, CourseModuleOut, CourseOut } from "@/api/courses";
import { useCourseLabels } from "@/lib/courseLabels";

import { LessonMediaCard } from "./LessonMediaCard";
import { LessonMediaRow } from "./LessonMediaRow";
import { MediaModuleSection } from "./MediaModuleSection";
import { MediaViewToggle } from "./MediaViewToggle";
import { useMediaView } from "./useMediaView";
import { VideoPlayerModal } from "./VideoPlayerModal";
import type { MediaRenderers, MediaStatusItem } from "./types";

interface Props<TItem extends MediaStatusItem> {
  course: CourseOut;
  /** Discrimina chiavi localStorage e nome file di download. */
  variant: "video" | "avatar";
  itemByLessonId: Map<string, TItem>;
  renderers: MediaRenderers<TItem>;
}

interface Playing<TItem> {
  lesson: CourseLessonOut;
  module: CourseModuleOut;
  item: TItem;
}

/**
 * Render condiviso dei tab media (Video / Video con Avatar): toggle
 * Lista/Griglia, moduli collassabili con contatore "X/Y pronti", righe o
 * card per lezione (etichette "Modulo N"/"Lezione N", mai i codici) e
 * player in modale. Le parti specifiche per variante arrivano via
 * `renderers`; le pagine mantengono header/banner/dati propri.
 */
export function LessonMediaView<TItem extends MediaStatusItem>({
  course,
  variant,
  itemByLessonId,
  renderers,
}: Props<TItem>) {
  const { moduleLabel, lessonLabel } = useCourseLabels();
  const { viewMode, setViewMode, collapsed, toggleModule } = useMediaView(
    course.id,
    variant,
  );
  const [playing, setPlaying] = useState<Playing<TItem> | null>(null);

  // Moduli che hanno almeno una lezione con item di stato (gli unici da
  // mostrare): preserva l'ordine dei moduli del corso.
  const modulesWithItems = course.modules
    .map((module) => ({
      module,
      lessons: module.lessons
        .map((lesson) => ({ lesson, item: itemByLessonId.get(lesson.id) }))
        .filter(
          (x): x is { lesson: CourseLessonOut; item: TItem } => !!x.item,
        ),
    }))
    .filter((m) => m.lessons.length > 0);

  if (modulesWithItems.length === 0) return null;

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <MediaViewToggle value={viewMode} onChange={setViewMode} />
      </div>

      {modulesWithItems.map(({ module, lessons }) => {
        const readyCount = lessons.filter(
          (x) => x.item.status === "ready",
        ).length;
        return (
          <MediaModuleSection
            key={module.id}
            title={`${moduleLabel(module.module_code)} · ${module.title}`}
            readyCount={readyCount}
            totalCount={lessons.length}
            collapsed={collapsed.has(module.id)}
            onToggle={() => toggleModule(module.id)}
          >
            {viewMode === "list" ? (
              <div className="space-y-2">
                {lessons.map(({ lesson, item }) => (
                  <LessonMediaRow
                    key={lesson.id}
                    lesson={lesson}
                    item={item}
                    lessonLabelText={lessonLabel(lesson.lesson_code)}
                    renderers={renderers}
                    onPlay={() => setPlaying({ lesson, module, item })}
                  />
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {lessons.map(({ lesson, item }) => (
                  <LessonMediaCard
                    key={lesson.id}
                    lesson={lesson}
                    item={item}
                    lessonLabelText={lessonLabel(lesson.lesson_code)}
                    renderers={renderers}
                    onPlay={() => setPlaying({ lesson, module, item })}
                  />
                ))}
              </div>
            )}
          </MediaModuleSection>
        );
      })}

      <VideoPlayerModal
        open={!!playing}
        onOpenChange={(open) => {
          if (!open) setPlaying(null);
        }}
        title={
          playing
            ? `${moduleLabel(playing.module.module_code)} · ${lessonLabel(
                playing.lesson.lesson_code,
              )} — ${playing.lesson.title}`
            : ""
        }
        videoUrl={playing?.item.video_url ?? null}
        downloadName={
          playing ? renderers.downloadName(playing.lesson, playing.item) : ""
        }
        meta={playing ? renderers.tokens(playing.item) : null}
      />
    </div>
  );
}
