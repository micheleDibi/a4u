import { useTranslation } from "react-i18next";

import type { StatusCount } from "@/api/adminMetrics";
import {
  COURSE_BUCKET_COLORS,
  COURSE_MACRO_ORDER,
  type CourseMacroBucket,
  courseBucketFor,
  statusColor,
} from "@/lib/statusColors";

// ---------------------------------------------------------------------------
// Widget: pipeline corsi — vista dettagliata.
//
// Mostra TUTTI i 17 stati raggruppati nelle 8 macro-fasi della pipeline
// (draft / architecture / structure / content / slides / speech /
// published / archived). Per le 5 fasi intermedie (architecture..speech)
// espone anche il breakdown per sub-stato (pending / ready / approved).
//
// CSS-only, nessuna libreria charts.
// ---------------------------------------------------------------------------

type CourseStageStatus = "pending" | "ready" | "approved";

interface CourseStageData {
  bucket: CourseMacroBucket;
  total: number;
  /** Solo per le fasi intermedie con sub-stati. */
  states?: Record<CourseStageStatus, number>;
}

const PHASES_WITH_SUBSTATES: ReadonlySet<CourseMacroBucket> = new Set<CourseMacroBucket>(
  ["architecture", "structure", "content", "slides", "speech"],
);

function buildStageData(raw: StatusCount[]): CourseStageData[] {
  const map = new Map<CourseMacroBucket, CourseStageData>();
  for (const b of COURSE_MACRO_ORDER) {
    map.set(b, { bucket: b, total: 0 });
  }
  for (const item of raw) {
    const b = courseBucketFor(item.status);
    if (!b) continue;
    const data = map.get(b)!;
    data.total += item.count;
    if (PHASES_WITH_SUBSTATES.has(b)) {
      const parts = item.status.split("_");
      const sub = parts[parts.length - 1] as CourseStageStatus;
      if (sub === "pending" || sub === "ready" || sub === "approved") {
        data.states = data.states ?? { pending: 0, ready: 0, approved: 0 };
        data.states[sub] += item.count;
      }
    }
  }
  return Array.from(map.values());
}

interface CoursePipelineDetailProps {
  items: StatusCount[];
  total: number;
  emptyLabel?: string;
}

export function CoursePipelineDetail({
  items,
  total,
  emptyLabel,
}: CoursePipelineDetailProps) {
  const { t } = useTranslation();

  if (total === 0) {
    return (
      <div className="py-12 text-center text-sm text-muted-foreground">
        {emptyLabel ?? "—"}
      </div>
    );
  }

  const stages = buildStageData(items);

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {stages.map((s) => (
        <StageCard key={s.bucket} stage={s} total={total} t={t} />
      ))}
    </div>
  );
}

function StageCard({
  stage,
  total,
  t,
}: {
  stage: CourseStageData;
  total: number;
  t: (k: string) => string;
}) {
  const pct = total > 0 ? (stage.total / total) * 100 : 0;
  const color = COURSE_BUCKET_COLORS[stage.bucket];
  const isEmpty = stage.total === 0;
  return (
    <div
      className={
        "group relative flex flex-col rounded-lg border bg-card p-4 transition-all duration-200 hover:shadow-md hover:border-foreground/15 " +
        (isEmpty ? "opacity-60" : "")
      }
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="truncate text-sm font-medium">
          {t(`dashboard.shared.statusBucket.${stage.bucket}`)}
        </div>
        <div className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {pct.toFixed(0)}%
        </div>
      </div>
      <div className="mt-1 text-3xl font-semibold tabular-nums">
        {stage.total}
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full ${color.bg} transition-all duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {stage.states && (
        <div className="mt-3 space-y-1 text-xs">
          {(["pending", "ready", "approved"] as const).map((sub) => {
            const c = stage.states![sub] ?? 0;
            return (
              <div key={sub} className="flex items-center gap-2">
                <span
                  className={`inline-block size-1.5 rounded-full ${statusColor(sub).bg}`}
                />
                <span className="text-muted-foreground">
                  {t(`dashboard.shared.lifecycle.${sub}`)}
                </span>
                <span className="ms-auto font-medium tabular-nums">{c}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
