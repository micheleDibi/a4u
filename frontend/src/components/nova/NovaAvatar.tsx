import { useEffect, useState } from "react";

// 19 frame PNG dell'avatar di Nova in `public/nova/nova-1.png` …
// `nova-19.png`. L'animazione idle cambia frame ogni `intervalMs`.
// Durante `isPending` la frequenza accelera per dare la sensazione
// che Nova stia "pensando".

const TOTAL_FRAMES = 19;

interface Props {
  /** Pixel del lato (square). Default 56 (button) / 32 (header). */
  size?: number;
  /** Velocità in millisecondi tra un frame e l'altro. */
  intervalMs?: number;
  /** Se true, accelera l'animazione (Nova sta "pensando"). */
  isPending?: boolean;
  /** Disabilita l'animazione (mostra solo frame 1). Default false. */
  paused?: boolean;
  className?: string;
}

export function NovaAvatar({
  size = 56,
  intervalMs = 150,
  isPending = false,
  paused = false,
  className,
}: Props) {
  const [frame, setFrame] = useState(1);

  useEffect(() => {
    if (paused) return;
    const speed = isPending ? 80 : intervalMs;
    const id = window.setInterval(() => {
      setFrame((f) => (f % TOTAL_FRAMES) + 1);
    }, speed);
    return () => window.clearInterval(id);
  }, [intervalMs, isPending, paused]);

  return (
    <img
      src={`/nova/nova-${frame}.png`}
      alt="Nova"
      width={size}
      height={size}
      className={
        "shrink-0 select-none object-contain " + (className ?? "")
      }
      style={{ width: size, height: size }}
      draggable={false}
    />
  );
}
