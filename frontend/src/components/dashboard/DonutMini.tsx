/**
 * Donut compatto via `conic-gradient` (CSS pure, niente SVG/canvas) + legenda.
 * Mostrato come sintesi laterale di breakdown a poche voci (es. avatar
 * clips ready/pending/failed).
 */

export interface DonutItem {
  /** Chiave stabile per React (es. lo status raw). */
  key: string;
  /** Label visibile (già localizzata). */
  label: string;
  count: number;
  /** Classe Tailwind di sfondo per il pallino della legenda. */
  color: string;
  /** Hex equivalente per il `conic-gradient`. */
  hex: string;
}

interface DonutMiniProps {
  items: DonutItem[];
  /** Etichetta sotto il numero al centro (uppercase small). */
  centerLabel?: string;
  /** Lato esterno del donut in px (default 140). */
  size?: number;
}

export function DonutMini({ items, centerLabel, size = 140 }: DonutMiniProps) {
  const total = items.reduce((sum, i) => sum + i.count, 0);
  const visible = items.filter((i) => i.count > 0);

  let gradient: string;
  if (total === 0) {
    gradient = "conic-gradient(#e5e7eb 0deg 360deg)";
  } else {
    let acc = 0;
    const stops: string[] = [];
    for (const it of visible) {
      const fromDeg = (acc / total) * 360;
      acc += it.count;
      const toDeg = (acc / total) * 360;
      stops.push(`${it.hex} ${fromDeg}deg ${toDeg}deg`);
    }
    gradient = `conic-gradient(${stops.join(", ")})`;
  }

  return (
    <div className="flex items-center gap-4">
      <div
        className="relative shrink-0 rounded-full"
        style={{ width: size, height: size, background: gradient }}
      >
        <div className="absolute inset-[18%] grid place-items-center rounded-full bg-card">
          <div className="text-center">
            <div className="text-2xl font-semibold leading-none tabular-nums">
              {total}
            </div>
            {centerLabel && (
              <div className="mt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                {centerLabel}
              </div>
            )}
          </div>
        </div>
      </div>
      <div className="min-w-0 flex-1 space-y-1 text-sm">
        {items.map((it) => (
          <div key={it.key} className="flex items-center gap-2">
            <span className={`inline-block size-2 rounded-full ${it.color}`} />
            <span className="truncate text-muted-foreground">{it.label}</span>
            <span className="ms-auto font-medium tabular-nums">{it.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
