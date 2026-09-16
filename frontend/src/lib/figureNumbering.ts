/**
 * Numerazione editoriale degli asset (D3, D4, Q2) — copia frontend di
 * `backend/app/services/figure_numbering.py`: MANTENERE ALLINEATO (stesse
 * espressioni regolari, stesse regole). La fixture condivisa
 * `backend/tests/fixtures/figure_numbering_cases.json` fissa i casi
 * (sezioni `cases` per le sole figure, `asset_cases` per i quattro kind,
 * `equation_label_family`, `strip_prefix`). Nessun import a runtime:
 * `backend/tests/test_figure_numbering.py` carica il modulo con
 * `node --experimental-strip-types`.
 *
 * Il numero di un asset è legato al suo id, non all'occorrenza: la prima
 * citazione `[KIND:id]` nel corpo della dispensa (introduzione → sezioni →
 * sintesi) assegna N crescente; le citazioni ripetute condividono lo stesso
 * N; un id senza asset non consuma numeri; gli asset mai citati sono
 * accodati dopo la sintesi (A12) e ricevono gli ultimi numeri. Il contatore
 * è indipendente per kind (`FIG`, `TAB`, `EQ`, `EX`): «Figura 1» e
 * «Tabella 1» convivono; il ramo teorema di un'equazione
 * (`equationLabelFamily` → `THM`) condivide il contatore `EQ`, perché il
 * tag `[EQ:id]` non trasporta la famiglia.
 *
 * `ASSET_REF_RE` e `FIG_REF_RE` sono case-sensitive sul kind (come
 * `ASSET_REF_RE` di `MarkdownRenderer` e `RichTextEditor`): un `[fig:x]`
 * non viene sostituito da nessun renderer e numerarlo produrrebbe un numero
 * fantasma. Il tag non attraversa la riga (`[^\]\n]+`). L'id è confrontato
 * con `trim().toLowerCase()`.
 */

export type AssetKind = "FIG" | "TAB" | "EQ" | "EX";

export const ASSET_KINDS: readonly AssetKind[] = ["FIG", "TAB", "EQ", "EX"];

export const ASSET_REF_RE = /\[(FIG|TAB|EQ|EX):([^\]\n]+)\]/g;
export const FIG_REF_RE = /\[FIG:([^\]\n]+)\]/g;

/** Id dichiarati per kind (`{ FIG: [...], TAB: [...] }`): un kind assente
 *  non numera e non accoda nulla. */
export type AssetIdsByKind = Partial<Record<AssetKind, Iterable<string>>>;

// Prefisso editoriale: parola, numero (con eventuali sottonumeri «1.2» e
// lettera «4a»), poi UN separatore `. : - – — )` non seguito da cifra
// (altrimenti «Figura 1.2 Schema» perderebbe «1.»), oppure fine del testo.
// Il separatore è obbligatorio: senza, «Figure 2 shows …» è una frase.
// `\p{Nd}` con il flag `u` equivale al `\d` Unicode di Python (cifre
// arabo-indiane, a larghezza piena, …): stesso esito su entrambi i lati.
// La lettera dopo il numero: `[a-z]` con IGNORECASE in Python accetta anche
// «ı» (U+0131) e «İ» (U+0130) per il case-mapping di `i`; il flag `iu` di
// JavaScript usa il simple case folding e non li piega, quindi sono
// elencati esplicitamente (fixture «Figura 2ı:», «Figura 2İ.»).
const FIGURE_PREFIX_RE =
  /^\s*(?:figura|figure|fig\.?|abb\.?)\s*\p{Nd}+(?:\.\p{Nd}+)*[a-zıİ]?\s*(?:[.:\-–—)](?!\p{Nd})\s*|$)/iu;

function norm(assetId: unknown): string {
  return String(assetId ?? "")
    .trim()
    .toLowerCase();
}

/** Coppie `[KIND, id_lower]` citate nel markdown, nell'ordine della prima
 *  occorrenza, senza duplicati. */
export function citedAssetIds(markdown: string): Array<[AssetKind, string]> {
  const seen = new Map<string, [AssetKind, string]>();
  for (const m of (markdown || "").matchAll(ASSET_REF_RE)) {
    const kind = m[1] as AssetKind;
    const id = norm(m[2]);
    const key = `${kind}:${id}`;
    if (id && !seen.has(key)) seen.set(key, [kind, id]);
  }
  return [...seen.values()];
}

/** Id (normalizzati) delle figure citate nel markdown, nell'ordine della
 *  prima occorrenza, senza duplicati (proiezione `FIG` di
 *  `citedAssetIds`). */
export function citedFigureIds(markdown: string): string[] {
  return citedAssetIds(markdown)
    .filter(([kind]) => kind === "FIG")
    .map(([, id]) => id);
}

/** Il token `[KIND:{id}]` rilegge esattamente `id`: falso per gli id vuoti,
 *  con `]` o con un a capo, che nessun percorso automatico produce ma un
 *  PATCH manuale può salvare (mirror di `_round_trips`). Usato anche
 *  dall'editor per non copiare un token che nessun renderer rileggerebbe. */
export function roundTrips(kind: AssetKind, assetId: string): boolean {
  const m = new RegExp(`^${ASSET_REF_RE.source}$`).exec(`[${kind}:${assetId}]`);
  return m !== null && m[2] === assetId;
}

/** Accoda `"\n\n[KIND:{id}]"` per ogni asset mai citato, kind per kind
 *  nell'ordine `FIG → TAB → EQ → EX` e, dentro il kind, nell'ordine
 *  dell'array (A12, D3). L'id è scritto come dichiarato (il lookup del
 *  renderer è già case-insensitive). Id vuoti e duplicati sono ignorati, e
 *  così gli id che il token non sa trasportare (`A]` produrrebbe `[FIG:A]]`,
 *  letto come `A`: un «Asset non trovato» falso e un `]` orfano, COR-4). Un
 *  kind assente dalla mappa non accoda nulla. */
export function appendUncitedAssetRefs(
  markdown: string,
  idsByKind: AssetIdsByKind,
): string {
  let out = markdown || "";
  const cited = new Set(citedAssetIds(out).map(([kind, id]) => `${kind}:${id}`));
  for (const kind of ASSET_KINDS) {
    for (const assetId of idsByKind[kind] ?? []) {
      const raw = String(assetId ?? "").trim();
      const key = `${kind}:${raw.toLowerCase()}`;
      if (!raw || cited.has(key) || !roundTrips(kind, raw)) continue;
      cited.add(key);
      out += `\n\n[${kind}:${raw}]`;
    }
  }
  return out;
}

/** `appendUncitedAssetRefs` per le sole figure. */
export function appendUncitedFigureRefs(
  markdown: string,
  assetIds: Iterable<string>,
): string {
  return appendUncitedAssetRefs(markdown, { FIG: assetIds });
}

/** `Map<"KIND:id_lower", N>`: contatore indipendente per kind; prima
 *  occorrenza → N crescente; citazioni ripetute → stesso N; id senza asset
 *  → nessun numero consumato; kind assente dalla mappa → nessun numero. Da
 *  applicare al markdown DOPO `appendUncitedAssetRefs`, così la coda è
 *  numerata dopo gli asset citati, e PRIMA del normalizzatore dei rimandi
 *  (`lib/assetRefNormalize.ts`), che riscrive le citazioni. */
export function computeAssetNumbers(
  markdown: string,
  idsByKind: AssetIdsByKind,
): Map<string, number> {
  const known = new Set<string>();
  for (const kind of ASSET_KINDS) {
    for (const assetId of idsByKind[kind] ?? []) {
      const id = norm(assetId);
      if (id) known.add(`${kind}:${id}`);
    }
  }
  const numbers = new Map<string, number>();
  const counters = new Map<AssetKind, number>();
  for (const [kind, id] of citedAssetIds(markdown)) {
    const key = `${kind}:${id}`;
    if (!known.has(key) || numbers.has(key)) continue;
    const next = (counters.get(kind) ?? 0) + 1;
    counters.set(kind, next);
    numbers.set(key, next);
  }
  return numbers;
}

/** `Map<id_lower, N>` delle sole figure (proiezione `FIG` di
 *  `computeAssetNumbers`, stessi risultati di sempre). */
export function computeFigureNumbers(
  markdown: string,
  assetIds: Iterable<string>,
): Map<string, number> {
  const numbers = new Map<string, number>();
  for (const [key, n] of computeAssetNumbers(markdown, { FIG: assetIds })) {
    numbers.set(key.slice("FIG:".length), n);
  }
  return numbers;
}

/** Riorganizza la mappa `KIND:id_lower → N` per kind, nella forma attesa da
 *  `normalizeAssetRefs` / `citeAssetRefs` (`{ KIND: Map<id_lower, N> }`). */
export function assetNumbersByKind(
  numbers: ReadonlyMap<string, number>,
): Partial<Record<AssetKind, Map<string, number>>> {
  const out: Partial<Record<AssetKind, Map<string, number>>> = {};
  for (const [key, n] of numbers) {
    const sep = key.indexOf(":");
    const kind = key.slice(0, sep) as AssetKind;
    let byId = out[kind];
    if (!byId) {
      byId = new Map<string, number>();
      out[kind] = byId;
    }
    byId.set(key.slice(sep + 1), n);
  }
  return out;
}

export interface ProofStepLike {
  latex?: string | null;
  text?: string | null;
}

export interface EquationLike {
  statement?: string | null;
  proof?: ReadonlyArray<ProofStepLike | null | undefined> | null;
}

/** Passi della dimostrazione con `latex` o `text` non vuoti (selezione del
 *  renderer del blocco equazione; mirror di `proof_steps`). */
export function nonEmptyProofSteps<T extends ProofStepLike>(
  proof: ReadonlyArray<T | null | undefined> | null | undefined,
): T[] {
  const out: T[] = [];
  for (const step of proof ?? []) {
    if (step === null || step === undefined || typeof step !== "object") continue;
    if (String(step.latex ?? "").trim() || String(step.text ?? "").trim()) {
      out.push(step);
    }
  }
  return out;
}

/** `THM` se `statement` non è vuoto o almeno un passo di `proof` non è
 *  vuoto: il blocco è reso come teorema («Lemma 2.») e il rimando usa la
 *  parola del kind; altrimenti `EQ` («Equazione 2.»). Il campo `kind` da
 *  solo non decide la famiglia. */
export function equationLabelFamily(eq: EquationLike): "EQ" | "THM" {
  if (String(eq.statement ?? "").trim() || nonEmptyProofSteps(eq.proof).length > 0) {
    return "THM";
  }
  return "EQ";
}

/** Rimuove un prefisso «Figura N.» / «Fig. N:» / «Figure N –» / «Abb. N)»
 *  (cifra obbligatoria, eventuale lettera, separatore obbligatorio salvo a
 *  fine testo) dalla didascalia. Solo a render: il testo persistito non
 *  cambia. */
export function stripFigurePrefix(caption: string): string {
  return (caption || "").replace(FIGURE_PREFIX_RE, "");
}
