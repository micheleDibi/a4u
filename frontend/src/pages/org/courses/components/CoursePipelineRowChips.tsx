import {
  FileText,
  Presentation,
  Smile,
  Video,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import type { CourseListLessonsProgress } from "@/api/courses";

// ---------------------------------------------------------------------------
// 4 chip compatti per la colonna "Pipeline" della lista corsi.
//
// Per ognuno dei 4 stadi (Contenuti, Slide, Video, Video con avatar):
// - icona identificativa
// - ratio `done/total` (oppure `—` se `total === 0`)
// - colore di sfondo graduato:
//     muted   (total === 0)
//     empty   (done === 0)
//     partial (0 < done < total)
//     done    (done === total)
//
// Tooltip via attributo `title` (label localizzata + dettaglio).
// ---------------------------------------------------------------------------

type Tone = "muted" | "empty" | "partial" | "done";

const TONE_CLASSES: Record<Tone, string> = {
  muted: "bg-muted/60 text-muted-foreground",
  empty:
    "bg-zinc-100 text-zinc-500 dark:bg-zinc-800/70 dark:text-zinc-400",
  partial:
    "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
  done:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
};

function toneFor(done: number, total: number): Tone {
  if (total === 0) return "muted";
  if (done === 0) return "empty";
  if (done >= total) return "done";
  return "partial";
}

interface ProgressChipProps {
  icon: LucideIcon;
  done: number;
  total: number;
  tooltipKey: string;
}

function ProgressChip({
  icon: Icon,
  done,
  total,
  tooltipKey,
}: ProgressChipProps) {
  const { t } = useTranslation();
  const tone = toneFor(done, total);
  const ratio = total > 0 ? `${done}/${total}` : "—";
  const tooltip =
    total > 0
      ? t(tooltipKey, { done, total })
      : t("courses.list.progressChip.noLessons");

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium tabular-nums transition-colors ${TONE_CLASSES[tone]}`}
      title={tooltip}
    >
      <Icon className="size-3" />
      {ratio}
    </span>
  );
}

interface CoursePipelineRowChipsProps {
  progress: CourseListLessonsProgress;
}

export function CoursePipelineRowChips({
  progress,
}: CoursePipelineRowChipsProps) {
  return (
    <div className="flex flex-wrap gap-1">
      <ProgressChip
        icon={FileText}
        done={progress.content_ready}
        total={progress.total}
        tooltipKey="courses.list.progressChip.content"
      />
      <ProgressChip
        icon={Presentation}
        done={progress.slides_ready}
        total={progress.total}
        tooltipKey="courses.list.progressChip.slides"
      />
      <ProgressChip
        icon={Video}
        done={progress.videos_ready}
        total={progress.total}
        tooltipKey="courses.list.progressChip.videos"
      />
      <ProgressChip
        icon={Smile}
        done={progress.avatar_videos_ready}
        total={progress.total}
        tooltipKey="courses.list.progressChip.avatarVideos"
      />
    </div>
  );
}
