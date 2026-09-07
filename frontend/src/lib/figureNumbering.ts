/**
 * Numerazione editoriale delle figure (D4, Q2) — copia frontend di
 * `backend/app/services/figure_numbering.py`: MANTENERE ALLINEATO (stesse
 * espressioni regolari, stesse regole). La fixture condivisa
 * `backend/tests/fixtures/figure_numbering_cases.json` fissa i casi.
 *
 * Il numero di una figura è legato al suo `asset_id`, non all'occorrenza:
 * la prima citazione `[FIG:id]` nel corpo della dispensa (introduzione →
 * sezioni → sintesi) assegna N crescente; le citazioni ripetute condividono
 * lo stesso N; un id senza asset non consuma numeri; gli asset mai citati
 * sono accodati dopo la sintesi (A12) e ricevono gli ultimi numeri.
 *
 * `FIG_REF_RE` è case-sensitive su `FIG` (come `ASSET_REF_RE` di
 * `MarkdownRenderer` e `RichTextEditor`): un `[fig:x]` non viene sostituito
 * da nessun renderer e numerarlo produrrebbe un numero fantasma. Il tag non
 * attraversa la riga (`[^\]\n]+`). L'id è confrontato con `trim().toLowerCase()`.
 */

export const FIG_REF_RE = /\[FIG:([^\]\n]+)\]/g;

// Prefisso editoriale: parola, numero (con eventuali sottonumeri «1.2» e
// lettera «4a»), poi UN separatore `. : - – — )` non seguito da cifra
// (altrimenti «Figura 1.2 Schema» perderebbe «1.»), oppure fine del testo.
// Il separatore è obbligatorio: senza, «Figure 2 shows …» è una frase.
const FIGURE_PREFIX_RE =
  /^\s*(?:figura|figure|fig\.?|abb\.?)\s*\d+(?:\.\d+)*[a-z]?\s*(?:[.:\-–—)](?!\d)\s*|$)/i;

function norm(assetId: unknown): string {
  return String(assetId ?? "")
    .trim()
    .toLowerCase();
}

/** Id (normalizzati) citati nel markdown, nell'ordine della prima
 *  occorrenza, senza duplicati. */
export function citedFigureIds(markdown: string): string[] {
  const seen = new Set<string>();
  for (const m of (markdown || "").matchAll(FIG_REF_RE)) {
    const key = norm(m[1]);
    if (key && !seen.has(key)) seen.add(key);
  }
  return [...seen];
}

/** Accoda `"\n\n[FIG:{id}]"` per ogni asset mai citato, nell'ordine
 *  dell'array (A12). L'id è scritto come dichiarato (il lookup del renderer
 *  è già case-insensitive). Id vuoti e duplicati sono ignorati. */
export function appendUncitedFigureRefs(
  markdown: string,
  assetIds: Iterable<string>,
): string {
  let out = markdown || "";
  const cited = new Set(citedFigureIds(out));
  for (const assetId of assetIds) {
    const raw = String(assetId ?? "").trim();
    const key = raw.toLowerCase();
    if (!key || cited.has(key)) continue;
    cited.add(key);
    out += `\n\n[FIG:${raw}]`;
  }
  return out;
}

/** `Map<id_lower, N>`: prima occorrenza → N crescente; citazioni ripetute →
 *  stesso N; id senza asset → nessun numero consumato. Da applicare al
 *  markdown DOPO `appendUncitedFigureRefs`, così la coda è numerata dopo le
 *  figure citate. */
export function computeFigureNumbers(
  markdown: string,
  assetIds: Iterable<string>,
): Map<string, number> {
  const known = new Set<string>();
  for (const a of assetIds) {
    const key = norm(a);
    if (key) known.add(key);
  }
  const numbers = new Map<string, number>();
  for (const key of citedFigureIds(markdown)) {
    if (known.has(key) && !numbers.has(key)) numbers.set(key, numbers.size + 1);
  }
  return numbers;
}

/** Rimuove un prefisso «Figura N.» / «Fig. N:» / «Figure N –» / «Abb. N)»
 *  (cifra obbligatoria, eventuale lettera, separatore obbligatorio salvo a
 *  fine testo) dalla didascalia. Solo a render: il testo persistito non
 *  cambia. */
export function stripFigurePrefix(caption: string): string {
  return (caption || "").replace(FIGURE_PREFIX_RE, "");
}
