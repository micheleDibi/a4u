import { useTranslation } from "react-i18next";
import { Check, Lock } from "lucide-react";

import type { CourseOut } from "@/api/courses";
import { cn } from "@/lib/utils";

// Mirror di `backend/app/core/course_phase_order.COURSE_STATUS_RANK`.
// Tenere allineato col BE: nuovi stati vanno aggiunti in entrambi i lati.
export const COURSE_STATUS_RANK: Record<string, number> = {
  draft: 0,
  architecture_pending: 1,
  architecture_ready: 2,
  architecture_approved: 3,
  lessons_structure_pending: 4,
  lessons_structure_ready: 5,
  lessons_structure_approved: 6,
  content_pending: 7,
  content_ready: 8,
  content_approved: 9,
  slides_pending: 10,
  slides_ready: 11,
  slides_approved: 12,
  speech_pending: 13,
  speech_ready: 14,
  speech_approved: 15,
  video_pending: 16,
  video_ready: 17,
  avatar_video_pending: 18,
  avatar_video_ready: 19,
  published: 20,
  archived: 21,
};

/**
 * `true` se `status` ha raggiunto o superato `milestone` nella pipeline
 * del corso. Usato per gate di accessibilita' delle sub-tab (es. una
 * sub-tab della fase "Contenuti" e' attiva sse status >= lessons_structure_approved).
 */
export function isCourseAtLeast(status: string, milestone: string): boolean {
  const a = COURSE_STATUS_RANK[status] ?? -1;
  const b = COURSE_STATUS_RANK[milestone] ?? Infinity;
  return a >= b;
}

export const PHASES = [
  {
    id: "setup",
    labelKey: "courses.phases.setup",
    tabs: ["base", "didactic", "objectives", "documents"],
  },
  {
    id: "architecture",
    labelKey: "courses.phases.architecture",
    tabs: ["architecture", "lessons-structure"],
  },
  {
    id: "content",
    labelKey: "courses.phases.content",
    tabs: ["lesson-content", "lesson-slides", "lesson-speech"],
  },
  {
    id: "media",
    labelKey: "courses.phases.media",
    tabs: ["lesson-video", "lesson-avatar-video"],
  },
] as const;

export type PhaseId = (typeof PHASES)[number]["id"];
export type PhaseStatus = "done" | "in_progress" | "locked" | "idle";

export function phaseOfTab(tabId: string): PhaseId {
  for (const phase of PHASES) {
    if ((phase.tabs as readonly string[]).includes(tabId)) {
      return phase.id;
    }
  }
  return "setup";
}

export function computePhaseStatus(
  phaseId: PhaseId,
  course: CourseOut | null,
  setupLocked: boolean,
): PhaseStatus {
  if (phaseId === "setup") {
    return setupLocked ? "done" : "in_progress";
  }
  if (!course) return "locked";
  const s = course.status;

  if (phaseId === "architecture") {
    if (!setupLocked) return "locked";
    // L'architettura e' "done" non appena il corso ha superato la fase
    // della struttura lezioni — include automaticamente tutti gli stati
    // successivi (content, slides, speech, video, avatar_video, published).
    if (isCourseAtLeast(s, "lessons_structure_approved")) return "done";
    return "in_progress";
  }

  if (phaseId === "content") {
    if (!setupLocked) return "locked";
    if (!isCourseAtLeast(s, "lessons_structure_approved")) return "locked";
    // done quando tutte le lezioni hanno speech approved.
    const allLessons = (course.modules ?? []).flatMap((m) => m.lessons ?? []);
    if (
      allLessons.length > 0 &&
      allLessons.every((l) => l.speech_status === "approved")
    ) {
      return "done";
    }
    return "in_progress";
  }

  if (phaseId === "media") {
    if (!setupLocked) return "locked";
    const anyReady = (course.modules ?? []).some((m) =>
      (m.lessons ?? []).some(
        (l) =>
          l.speech_status === "approved" && l.slides_status === "approved",
      ),
    );
    if (!anyReady) return "locked";
    if (isCourseAtLeast(s, "published")) return "done";
    return "in_progress";
  }

  return "idle";
}

interface Props {
  activePhase: PhaseId;
  course: CourseOut | null;
  setupLocked: boolean;
  onNavigate: (phaseId: PhaseId) => void;
}

/**
 * Stepper orizzontale delle 4 macro-fasi dell'editor corso. Sostituisce
 * la lista piatta di 11 tab. Ogni fase mostra il proprio stato (done /
 * in_progress / locked / idle) come pallino+icona; click su fase non
 * locked naviga alla prima sub-tab della fase. Sotto lo stepper, la
 * `TabsList` mostra solo le sub-tab della fase corrente.
 */
export function CoursePhaseStepper({
  activePhase,
  course,
  setupLocked,
  onNavigate,
}: Props) {
  const { t } = useTranslation();

  return (
    <div className="flex w-full items-stretch gap-0 overflow-x-auto rounded-lg border border-border bg-card p-1">
      {PHASES.map((phase, idx) => {
        const status = computePhaseStatus(phase.id, course, setupLocked);
        const isActive = phase.id === activePhase;
        const isLocked = status === "locked";
        const isLast = idx === PHASES.length - 1;
        return (
          <PhaseStep
            key={phase.id}
            index={idx + 1}
            label={t(phase.labelKey)}
            lockedHint={
              isLocked
                ? (t(`courses.phases.lockedHint.${phase.id}`) as string)
                : undefined
            }
            status={status}
            active={isActive}
            disabled={isLocked}
            isLast={isLast}
            onClick={() => !isLocked && onNavigate(phase.id)}
          />
        );
      })}
    </div>
  );
}

interface StepProps {
  index: number;
  label: string;
  lockedHint?: string;
  status: PhaseStatus;
  active: boolean;
  disabled: boolean;
  isLast: boolean;
  onClick: () => void;
}

function PhaseStep({
  index,
  label,
  lockedHint,
  status,
  active,
  disabled,
  isLast,
  onClick,
}: StepProps) {
  const dot = (() => {
    if (status === "done") {
      return (
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-white">
          <Check className="size-4" strokeWidth={3} />
        </span>
      );
    }
    if (status === "locked") {
      return (
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Lock className="size-3.5" />
        </span>
      );
    }
    if (status === "in_progress") {
      return (
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground font-semibold text-xs">
          {index}
        </span>
      );
    }
    return (
      <span className="flex size-7 shrink-0 items-center justify-center rounded-full border-2 border-muted-foreground/30 text-muted-foreground font-semibold text-xs">
        {index}
      </span>
    );
  })();

  return (
    <>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        title={lockedHint}
        aria-current={active ? "step" : undefined}
        className={cn(
          "group flex flex-1 min-w-0 items-center gap-2.5 rounded-md px-3 py-2 text-left transition-colors",
          active && "bg-primary/10",
          !active && !disabled && "hover:bg-muted",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        {dot}
        <span className="flex min-w-0 flex-col">
          <span
            className={cn(
              "truncate text-sm font-medium leading-tight",
              active ? "text-foreground" : "text-foreground/80",
              disabled && "text-muted-foreground",
            )}
          >
            {label}
          </span>
          {status === "done" && (
            <span className="text-[10px] uppercase tracking-wide text-emerald-600 dark:text-emerald-500">
              ✓
            </span>
          )}
        </span>
      </button>
      {!isLast && (
        <div className="flex shrink-0 items-center px-1">
          <div
            className={cn(
              "h-px w-3 sm:w-6",
              status === "done" ? "bg-emerald-500/60" : "bg-border",
            )}
          />
        </div>
      )}
    </>
  );
}
