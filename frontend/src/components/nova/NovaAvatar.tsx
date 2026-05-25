// Avatar di Nova: una singola immagine PNG (NO animazione ciclica).
// L'index del frame è gestito dal parent (`NovaWidget`) che lo
// randomizza quando l'utente cambia pagina o ricarica.

const TOTAL_FRAMES = 19;

interface Props {
  /** Pixel del lato (square). */
  size?: number;
  /** Indice del frame (1..19). Default 1. */
  frame?: number;
  className?: string;
}

export function NovaAvatar({ size = 80, frame = 1, className }: Props) {
  const safeFrame = Math.max(1, Math.min(TOTAL_FRAMES, frame));
  return (
    <img
      src={`/nova/nova-${safeFrame}.png`}
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

export const NOVA_FRAMES_COUNT = TOTAL_FRAMES;
