import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";

interface KpiCardProps {
  label: string;
  value: string | number;
  /** Sotto-etichetta opzionale (es. "+5 ultimi 30g", "su 12 totali"). */
  sublabel?: string;
  icon?: LucideIcon;
  /** Tonalità del valore. `muted` = grigio (per KPI secondari / vuoti). */
  tone?: "default" | "muted";
}

export function KpiCard({
  label,
  value,
  sublabel,
  icon: Icon,
  tone = "default",
}: KpiCardProps) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-3 p-4">
        <div className="min-w-0 space-y-1">
          <div className="truncate text-xs uppercase tracking-wide text-muted-foreground">
            {label}
          </div>
          <div
            className={
              tone === "muted"
                ? "text-2xl font-semibold text-muted-foreground"
                : "text-2xl font-semibold"
            }
          >
            {value}
          </div>
          {sublabel && (
            <div className="text-xs text-muted-foreground">{sublabel}</div>
          )}
        </div>
        {Icon && (
          <div className="grid size-9 shrink-0 place-items-center rounded-md bg-muted text-foreground">
            <Icon className="size-4" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
