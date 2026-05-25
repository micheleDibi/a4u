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

/**
 * Tile metrica grande. Hover lift discreto + icona in tinta primary
 * (oppure muted) per dare una nota di colore consistente con
 * l'identità del prodotto.
 */
export function KpiCard({
  label,
  value,
  sublabel,
  icon: Icon,
  tone = "default",
}: KpiCardProps) {
  return (
    <Card className="transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md hover:border-foreground/15">
      <CardContent className="flex items-start justify-between gap-3 p-5">
        <div className="min-w-0 space-y-1.5">
          <div className="truncate text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            {label}
          </div>
          <div
            className={
              tone === "muted"
                ? "text-3xl font-semibold tabular-nums text-muted-foreground"
                : "text-3xl font-semibold tabular-nums"
            }
          >
            {value}
          </div>
          {sublabel && (
            <div className="text-xs text-muted-foreground">{sublabel}</div>
          )}
        </div>
        {Icon && (
          <div
            className={
              "grid size-10 shrink-0 place-items-center rounded-lg transition-colors " +
              (tone === "muted"
                ? "bg-muted text-muted-foreground"
                : "bg-primary/10 text-primary")
            }
          >
            <Icon className="size-5" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
