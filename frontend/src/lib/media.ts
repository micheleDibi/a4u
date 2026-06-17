// Helper per costruire l'URL pubblico di un file media servito dallo storage
// (immagini embedded nelle lezioni/slide, ecc.).
//
// In produzione i file vivono sul server OVH e `VITE_UPLOADS_BASE_URL` punta
// a `https://progettiersaf.com/media/uploads`; in sviluppo resta `/uploads`
// (proxato al backend dal dev server / nginx).
//
// Tollerante alle convenzioni di path del DB: accetta sia `/uploads/...` sia
// `uploads/...` sia path "nudi" (`lesson_assets/...`), e lascia invariati gli
// URL già assoluti (`http(s)://...`, `data:`).
const UPLOADS_BASE = (
  import.meta.env.VITE_UPLOADS_BASE_URL ?? "/uploads"
).replace(/\/+$/, "");

export function mediaUrl(path: string | null | undefined): string {
  if (!path) return "";
  if (/^(https?:)?\/\//i.test(path) || path.startsWith("data:")) return path;
  let rel = path.replace(/^\/+/, "");
  if (rel.startsWith("uploads/")) rel = rel.slice("uploads/".length);
  return `${UPLOADS_BASE}/${rel}`;
}
