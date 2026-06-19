import { useTranslation } from "react-i18next";
import { FilmIcon, Play } from "lucide-react";

import type { CourseLessonOut } from "@/api/courses";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

import type { MediaRenderers, MediaStatusItem } from "./types";

interface Props<TItem extends MediaStatusItem> {
  lesson: CourseLessonOut;
  item: TItem;
  lessonLabelText: string;
  renderers: MediaRenderers<TItem>;
  onPlay: () => void;
}

/**
 * Card (stile "Griglia") di una lezione: tile cliccabile con ▶ (niente
 * poster reale → tile-play su sfondo neutro), "Lezione N", titolo, badge
 * stato, progress/avvisi e azioni. Click sul tile → modale.
 */
export function LessonMediaCard<TItem extends MediaStatusItem>({
  lesson,
  item,
  lessonLabelText,
  renderers,
  onPlay,
}: Props<TItem>) {
  const { t } = useTranslation();
  const inProgress = item.status === "pending" || item.status === "processing";
  const isReady = item.status === "ready" && !!item.video_url;

  return (
    <div className="flex flex-col overflow-hidden rounded-md border">
      <button
        type="button"
        onClick={isReady ? onPlay : undefined}
        disabled={!isReady}
        aria-label={isReady ? t("courses.media.play") : undefined}
        className={cn(
          "group relative flex aspect-[99/70] w-full items-center justify-center bg-muted/60 transition-colors",
          isReady ? "cursor-pointer hover:bg-muted" : "cursor-default",
        )}
      >
        {isReady ? (
          <span className="flex size-12 items-center justify-center rounded-full bg-background/90 text-primary shadow-sm transition-transform group-hover:scale-110">
            <Play className="size-6 fill-current" />
          </span>
        ) : (
          <FilmIcon className="size-10 text-muted-foreground/40" />
        )}
      </button>

      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <span className="text-sm font-semibold">{lessonLabelText}</span>
          {renderers.statusBadge(item)}
        </div>
        <div className="line-clamp-2 text-xs text-muted-foreground">
          {lesson.title}
        </div>

        {renderers.warnings(item)}

        {inProgress && (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{renderers.phaseLabel(item)}</span>
              <span>{item.progress}%</span>
            </div>
            <Progress value={item.progress} />
          </div>
        )}

        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">
          {renderers.actions(lesson, item)}
        </div>
      </div>
    </div>
  );
}
