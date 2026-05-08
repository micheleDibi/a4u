/**
 * Formatta una durata in millisecondi come stringa leggibile.
 * Esempi:
 *   45_000 → "45s"
 *   90_000 → "1m 30s"
 *   3_660_000 → "1h 1m"
 *   7_200_000 → "2h"
 *   100 → "<1s"
 */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "<1s";
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;

  const totalMinutes = Math.floor(totalSeconds / 60);
  const remSeconds = totalSeconds % 60;
  if (totalMinutes < 60) {
    if (remSeconds === 0) return `${totalMinutes}m`;
    return `${totalMinutes}m ${remSeconds}s`;
  }

  const totalHours = Math.floor(totalMinutes / 60);
  const remMinutes = totalMinutes % 60;
  if (remMinutes === 0) return `${totalHours}h`;
  return `${totalHours}h ${remMinutes}m`;
}
