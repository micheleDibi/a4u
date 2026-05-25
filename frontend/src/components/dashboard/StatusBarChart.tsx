/**
 * Barra orizzontale stacked segmentata per status + legenda.
 * CSS-only (zero dipendenze chart). Tooltip via attributo `title`.
 */

export interface StatusBarItem {
  /** Chiave stabile per React (es. lo status raw). */
  key: string;
  /** Label visibile (già localizzata). */
  label: string;
  count: number;
  /** Classe Tailwind di sfondo (es. `bg-emerald-500`). */
  color: string;
}

interface StatusBarChartProps {
  items: StatusBarItem[];
  /** Mostrato quando il totale è 0 (es. "Nessun dato"). */
  emptyLabel?: string;
  /** Compact mode: legenda inline, barra più sottile. */
  compact?: boolean;
}

export function StatusBarChart({
  items,
  emptyLabel,
  compact = false,
}: StatusBarChartProps) {
  const total = items.reduce((sum, i) => sum + i.count, 0);

  if (total === 0) {
    return (
      <div className="py-4 text-center text-sm text-muted-foreground">
        {emptyLabel ?? "—"}
      </div>
    );
  }

  const visible = items.filter((i) => i.count > 0);

  return (
    <div className="space-y-2">
      <div
        className={
          "flex w-full overflow-hidden rounded-full bg-muted " +
          (compact ? "h-2" : "h-3")
        }
      >
        {visible.map((it) => (
          <div
            key={it.key}
            className={it.color}
            style={{ width: `${(it.count / total) * 100}%` }}
            title={`${it.label}: ${it.count}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {items.map((it) => (
          <div key={it.key} className="flex items-center gap-1.5">
            <span className={`inline-block size-2 rounded-full ${it.color}`} />
            <span className="text-muted-foreground">{it.label}</span>
            <span className="font-medium tabular-nums">{it.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
