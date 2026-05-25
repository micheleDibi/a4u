/**
 * "Time ago" compatto e localizzato (IT/EN, fallback EN).
 *
 *   ora / 5m fa / 2h fa / 3g fa / 12/03/2026
 *   now / 5m ago / 2h ago / 3d ago / 03/12/2026
 *
 * Non porta dipendenze esterne (niente date-fns) per non gonfiare il bundle
 * frontend solo per il widget `ActivityList`.
 */

export function formatTimeAgo(
  date: Date | string,
  lang: string = "en",
): string {
  const d = typeof date === "string" ? new Date(date) : date;
  const diffMs = Date.now() - d.getTime();

  // Futuro o orologio sballato → trattalo come "ora".
  if (diffMs < 0) {
    return lang.startsWith("it") ? "ora" : "now";
  }

  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return lang.startsWith("it") ? "ora" : "now";

  const min = Math.floor(sec / 60);
  if (min < 60) {
    return lang.startsWith("it") ? `${min}m fa` : `${min}m ago`;
  }

  const hr = Math.floor(min / 60);
  if (hr < 24) {
    return lang.startsWith("it") ? `${hr}h fa` : `${hr}h ago`;
  }

  const day = Math.floor(hr / 24);
  if (day < 7) {
    return lang.startsWith("it") ? `${day}g fa` : `${day}d ago`;
  }

  // Più di una settimana: data localizzata corta.
  return d.toLocaleDateString(
    lang.startsWith("it") ? "it-IT" : undefined,
    { day: "2-digit", month: "2-digit", year: "numeric" },
  );
}
