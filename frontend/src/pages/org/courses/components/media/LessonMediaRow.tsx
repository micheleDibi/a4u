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
 * Riga compatta (stile "Lista") di una lezione: ▶ + "Lezione N · Titolo" +
 * badge stato + azioni; progress inline quando in corso. Niente player
 * incorporato — il ▶ apre la modale.
 */
export function LessonMediaRow<TItem extends MediaStatusItem>({
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
    <div className="rounded-md border">
      <div className="flex items-center gap-3 px-3 py-2">
        <button
          type="button"
          onClick={isReady ? onPlay : undefined}
          disabled={!isReady}
          aria-label={isReady ? t("courses.media.play") : undefined}
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-md border bg-muted/60 transition-colors",
            isReady
              ? "cursor-pointer text-primary hover:bg-primary/10"
              : "cursor-default text-muted-foreground/60",
          )}
        >
          {isReady ? (
            <Play className="size-4 fill-current" />
          ) : (
            <FilmIcon className="size-4" />
          )}
        </button>

        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium">
              {lessonLabelText} · {lesson.title}
            </span>
            {renderers.statusBadge(item)}
          </div>
          {renderers.warnings(item)}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {renderers.actions(lesson, item)}
        </div>
      </div>

      {inProgress && (
        <div className="space-y-1 px-3 pb-2">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{renderers.phaseLabel(item)}</span>
            <span>{item.progress}%</span>
          </div>
          <Progress value={item.progress} />
        </div>
      )}
    </div>
  );
}
