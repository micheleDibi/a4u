/**
 * Rimandi testuali e ancore degli asset (D1, D2, D4) — copia frontend di
 * `backend/app/services/asset_ref_normalize.py`: MANTENERE ALLINEATO
 * (stesse espressioni regolari, stesso algoritmo). La fixture condivisa
 * `backend/tests/fixtures/asset_ref_normalize_cases.json` fissa i casi e
 * `backend/tests/test_asset_ref_normalize.py` la esegue su entrambi i lati
 * (questo modulo non ha import a runtime: Node lo carica con
 * `--experimental-strip-types`).
 *
 * Un tag `[KIND:id]` (`FIG`, `TAB`, `EQ`, `EX`) è «gestito» quando il kind
 * compare in `numbers` e l'id normalizzato (`trim(...).toLowerCase()`) vi ha un
 * numero: dopo `appendUncitedAssetRefs` + `computeAssetNumbers` ogni asset
 * dichiarato ne ha uno, quindi «gestito» coincide con «risolvibile». Un tag
 * non gestito (id irrisolto, `[fig:x]`, kind assente dalla mappa) resta
 * byte-identico in ogni passo: a valle produce il blocco «Asset non trovato»
 * visibile o il blocco di oggi.
 *
 * `normalizeAssetRefs(markdown, { numbers, reference })`:
 * - ogni citazione in linea di un tag gestito diventa il rimando testuale
 *   `reference(kind, idLower, n)` («Figura 2», «Tabella 1», «Lemma 2»:
 *   senza punto, la punteggiatura dell'autore resta);
 * - guardia «parola-etichetta»: se la parola dell'etichetta precede già il
 *   tag sulla stessa riga, a meno di spazi e su parola intera, il rimando
 *   emette il solo numero («La figura [FIG:x]» → «La figura 1», mai «La
 *   figura Figura 1»). La parola non è un elenco scritto a mano: è il
 *   testo che `reference` stesso mette prima del numero, cioè la chiave
 *   i18n del rimando;
 * - una riga fatta del solo tag (`ANCHOR_LINE_RE`, spazi e `\r` tollerati)
 *   è un'ancora e resta byte-identica: è il punto in cui `MarkdownRenderer`
 *   inserisce il blocco; la prima ancora per chiave `KIND:id_lower` è
 *   tenuta, le successive sono rimosse con la riga vuota adiacente;
 * - se una chiave gestita ha citazioni ma nessuna ancora, UNA ancora
 *   `[KIND:id]` (id come scritto nella prima citazione, ripulito) è inserita
 *   su riga propria subito dopo il blocco che contiene la prima citazione:
 *   blocco = righe contigue non vuote; i fence (``` / ~~~) chiusi e i
 *   blocchi `$$…$$` chiusi (anche con righe vuote interne) sono unità
 *   opache; se il blocco entra in una lista (prima riga item o
 *   continuazione indentata, oppure lista che interrompe un paragrafo
 *   attaccato), il blocco si estende sull'intera lista (altrimenti la
 *   lista sciolta viene spezzata in due, con `<ol start="2">`);
 * - dentro fence, code span e math i tag sono citazioni (riscritte), mai
 *   ancore: lasciarli intatti produrrebbe un secondo blocco o markup
 *   escapato nel `<pre>` (`preprocessAssetRefs` è una sostituzione
 *   globale); `- [FIG:a]` è citazione (D2 letterale).
 *
 * `citeAssetRefs(text, { numbers, reference })`: sola sostituzione dei tag
 * gestiti con il rimando, ancore comprese, senza rimozioni né inserimenti:
 * per la coda (punti chiave, riferimenti) che non rende blocchi (C9).
 *
 * I numeri sono dati (calcolati PRIMA, sul corpo non normalizzato), mai
 * ricalcolati; il testo fuori dai tag è byte-identico; la funzione è
 * idempotente. Regex con classi esplicite (`[ \t]`, `[0-9]`, `[^ \t\r\n]`),
 * mai `\s`/`\d`, senza flag `m`/`u`/`i`, e `trim` al posto di
 * `String.trim()`/`str.strip()` (classi diverse: vedi `WS`): stesso esito
 * JavaScript/Python.
 *
 * Limiti dichiarati (gli stessi del backend): blocchi indentati di 4 spazi
 * non riconosciuti come codice; fence con prefisso (`> `, item con 4+
 * spazi) e code span multi-riga non riconosciuti; `\[ … \]` LaTeX display
 * non opaco (convertito in `$$` solo a valle da `normalizeMathDelimiters`);
 * una riga-tag da sola dentro un HTML block è ancora; fence e `$$` non
 * chiusi non sono regioni. La guardia «parola-etichetta» ha quattro
 * limiti, tutti pinnati in fixture: confronta la parola INTERA, quindi il
 * plurale non corrisponde («Le figure [FIG:a]» → «Le figure Figura 1») e
 * nemmeno la parola separata dal tag da un segno di punteggiatura («La
 * figura, [FIG:a]»); «a meno di spazi» è il solo `[ \t]`, quindi uno
 * spazio unificatore (U+00A0) fra parola e tag la disattiva (la
 * ripetizione sopravvive: «La figura Figura 1»), a differenza del resto
 * del modulo che usa la classe larga `WS`; la regola è lessicale e non
 * distingue il sostantivo dal verbo omografo, quindi «il ciclo completo
 * figura [FIG:a]» diventa «… figura 1», unico caso in cui il rimando
 * perde l'etichetta invece di guadagnarne una di troppo; con la parola
 * incollata al tag («La figura[FIG:a]») la cifra resta incollata alla
 * parola («La figura1»), malformata quanto l'ingresso. Gli ultimi tre
 * hanno 0 occorrenze nell'export reale di §20.3 di
 * `docs/courses/17-visual-figures.md`.
 */

export type AssetKind = "FIG" | "TAB" | "EQ" | "EX";

/** `{ KIND: Map<id_lower, N> }`: kind assente o vuoto = identità su quel
 *  kind (mirror di `AssetNumbers` del backend). */
export type AssetNumbers = Partial<
  Record<AssetKind, ReadonlyMap<string, number>>
>;

/** Produce il rimando testuale; l'id serve al ramo teorema (`[EQ:id]` in
 *  famiglia THM → «Lemma 2»). */
export type ReferenceFn = (kind: AssetKind, idLower: string, n: number) => string;

export interface NormalizeOptions {
  numbers: AssetNumbers;
  reference: ReferenceFn;
}

export const ASSET_REF_RE = /\[(FIG|TAB|EQ|EX):([^\]\n]+)\]/g;
export const ANCHOR_LINE_RE = /^[ \t]*\[(FIG|TAB|EQ|EX):([^\]\n]+)\][ \t]*\r?$/;

const BLANK_RE = /^[ \t]*\r?$/;
const FENCE_OPEN_RE = /^ {0,3}(`{3,}|~{3,})/;
const MATH_OPEN_RE = /^ {0,3}\$\$/;
const LIST_ITEM_RE = /^[ \t]*(?:[-+*]|[0-9]{1,9}[.)])(?:[ \t]|\r?$)/;
// Marcatore che può INTERROMPERE un paragrafo attaccato: puntato, oppure
// ordinato che comincia da 1 (CommonMark 5.2); `2. due` dopo una riga di
// prosa è continuazione, non lista.
const LIST_START_RE = /^[ \t]*(?:[-+*]|1[.)])(?:[ \t]|\r?$)/;
const INDENTED_RE = /^(?: {2,}|\t)[^ \t\r\n]/;

const NO_REGION = -1;

// `String.trim()` e `str.strip()` non tolgono gli stessi caratteri:
// `\ufeff` (BOM) solo in JavaScript, `\x1c-\x1f` e `\x85` solo in Python.
// La classe esplicita è l'unione dei due insiemi, così i due lati leggono
// id, righe e tag allo stesso modo.
const WS =
  " \\t\\n\\v\\f\\r\\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a" +
  "\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff";
const TRIM_RE = new RegExp(`^[${WS}]+|[${WS}]+$`, "g");

const TRAILING_SPACES_RE = /[ \t]+$/;
// Confine di parola della guardia «parola-etichetta»: classe esplicita
// perché `\w` è solo ASCII in JavaScript e unicode in Python. Copre le
// lettere latine accentate (senza `\xd7` e `\xf7`, che sono segni), cioè
// le lingue del rimando. Una lettera fuori dalla classe (un alfabeto non
// latino) vale quindi come confine e la guardia SCATTA: oggi non è
// raggiungibile, perché la parola confrontata esce dalla chiave i18n del
// rimando, presente nei soli `it.json` e `en.json`, ed è sempre latina.
const WORD_CHAR_RE = /[0-9A-Za-z_\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f]/;

/** `String.trim()` con la stessa classe dello `strip()` del mirror Python. */
function trim(text: string): string {
  return text.replace(TRIM_RE, "");
}

/** `[idLower, n]` se il tag è gestito, altrimenti `null`. */
function handled(
  numbers: AssetNumbers,
  kind: AssetKind,
  rawId: string,
): [string, number] | null {
  const byId = numbers[kind];
  if (!byId || byId.size === 0) return null;
  const key = trim(rawId).toLowerCase();
  const n = byId.get(key);
  if (n === undefined) return null;
  return [key, n];
}

/** Vero se `prefix` (il testo che precede il tag) finisce con `word` sulla
 *  stessa riga, a meno di spazi e su parola INTERA: è la condizione della
 *  guardia «parola-etichetta». */
function labelRepeated(prefix: string, word: string): boolean {
  const line = prefix.slice(prefix.lastIndexOf("\n") + 1);
  const head = line.replace(TRAILING_SPACES_RE, "");
  const cut = head.length - word.length;
  if (cut < 0 || head.slice(cut).toLowerCase() !== word.toLowerCase()) {
    return false;
  }
  return cut === 0 || !WORD_CHAR_RE.test(head[cut - 1]);
}

/** Il rimando da scrivere davvero: il solo numero quando la parola
 *  dell'etichetta precede già il tag («La figura [FIG:x]» → «La figura
 *  1»), altrimenti il rimando intero. La parola è il testo che `reference`
 *  mette prima del numero, quindi viene dalla chiave i18n del rimando e
 *  non da un elenco a parte. */
function referenceText(prefix: string, rendered: string, n: number): string {
  const at = rendered.lastIndexOf(String(n));
  if (at <= 0) return rendered;
  const word = trim(rendered.slice(0, at));
  if (!word || !labelRepeated(prefix, word)) return rendered;
  return rendered.slice(at);
}

/** Sostituisce in posizione ogni tag gestito con il rimando (guardia
 *  «parola-etichetta» compresa); i tag non gestiti restano byte-identici.
 *  Costruito per slice (mai `String.replace` con pattern: il rimando
 *  potrebbe contenere `$`). */
function rewriteLine(
  line: string,
  numbers: AssetNumbers,
  reference: ReferenceFn,
): string {
  const out: string[] = [];
  let pos = 0;
  for (const m of line.matchAll(ASSET_REF_RE)) {
    const kind = m[1] as AssetKind;
    const h = handled(numbers, kind, m[2]);
    if (h === null) continue;
    const start = m.index ?? 0;
    out.push(line.slice(pos, start));
    out.push(referenceText(line.slice(0, start), reference(kind, h[0], h[1]), h[1]));
    pos = start + m[0].length;
  }
  out.push(line.slice(pos));
  return out.join("");
}

/** Per ogni riga, inizio e fine (inclusiva) della regione opaca che la
 *  contiene, oppure `-1`: fence chiusi (stesso carattere, marcatore di
 *  chiusura lungo almeno quanto l'apertura) e blocchi `$$` chiusi (righe
 *  vuote interne ammesse). Le regioni non si annidano; senza chiusura non
 *  c'è regione. */
function opaqueRegions(lines: string[]): [number[], number[]] {
  const n = lines.length;
  const start: number[] = new Array<number>(n).fill(NO_REGION);
  const end: number[] = new Array<number>(n).fill(NO_REGION);
  let i = 0;
  while (i < n) {
    const line = lines[i];
    let close = NO_REGION;
    const fence = FENCE_OPEN_RE.exec(line);
    if (fence) {
      const marker = fence[1];
      // Backtick e tilde non sono metacaratteri: nessun escape necessario.
      const closeRe = new RegExp(
        "^ {0,3}" + marker[0] + "{" + String(marker.length) + ",}[ \\t]*\\r?$",
      );
      for (let j = i + 1; j < n; j += 1) {
        if (closeRe.test(lines[j])) {
          close = j;
          break;
        }
      }
    } else if (MATH_OPEN_RE.test(line)) {
      const stripped = trim(line);
      if (stripped.length > 3 && stripped.endsWith("$$")) {
        close = i;
      } else {
        for (let j = i + 1; j < n; j += 1) {
          if (trim(lines[j]).endsWith("$$")) {
            close = j;
            break;
          }
        }
      }
    }
    if (close === NO_REGION) {
      i += 1;
      continue;
    }
    for (let k = i; k <= close; k += 1) {
      start[k] = i;
      end[k] = close;
    }
    i = close + 1;
  }
  return [start, end];
}

function isListBlock(line: string): boolean {
  return LIST_ITEM_RE.test(line) || INDENTED_RE.test(line);
}

/** Indice della riga vuota (o `lines.length`) che chiude il blocco della
 *  riga `i`; se il blocco ENTRA in una lista — perché comincia con un item
 *  o una continuazione indentata, oppure perché una lista interrompe un
 *  paragrafo attaccato — il confine salta le righe vuote interne alla
 *  lista. Una lista interrompe un paragrafo solo se comincia con un
 *  marcatore puntato o con `1.`/`1)`. */
function blockBounds(
  i: number,
  lines: string[],
  isBlank: boolean[],
  rstart: number[],
  rend: number[],
): number {
  const n = lines.length;
  let first = rstart[i] !== NO_REGION ? rstart[i] : i;
  while (first > 0 && !isBlank[first - 1]) {
    const prev = first - 1;
    first = rstart[prev] !== NO_REGION ? rstart[prev] : prev;
  }
  let listBlock = isListBlock(lines[first]);
  let j = first;
  for (;;) {
    while (j < n && !isBlank[j]) {
      if (!listBlock && LIST_START_RE.test(lines[j])) listBlock = true;
      j = rend[j] !== NO_REGION ? rend[j] + 1 : j + 1;
    }
    if (j >= n || !listBlock) return j;
    let k = j;
    while (k < n && isBlank[k]) k += 1;
    if (k >= n || !isListBlock(lines[k])) return j;
    j = k;
  }
}

/** Vedi il commento del modulo. `numbers` è `{ KIND: Map<id_lower, N> }`
 *  (kind assente o vuoto = identità su quel kind); `reference(kind,
 *  idLower, n)` produce il rimando testuale. */
export function normalizeAssetRefs(
  markdown: string,
  { numbers, reference }: NormalizeOptions,
): string {
  if (!markdown) return markdown;
  const lines = markdown.split("\n");
  const n = lines.length;
  const [rstart, rend] = opaqueRegions(lines);
  const isBlank = lines.map(
    (line, i) => rstart[i] === NO_REGION && BLANK_RE.test(line),
  );

  const firstAnchor = new Map<string, number>();
  const drop = new Set<number>();
  const firstCite = new Map<string, [number, string]>();
  for (let i = 0; i < n; i += 1) {
    const line = lines[i];
    const anchor = rstart[i] === NO_REGION ? ANCHOR_LINE_RE.exec(line) : null;
    if (anchor !== null) {
      const kind = anchor[1] as AssetKind;
      const h = handled(numbers, kind, anchor[2]);
      if (h !== null) {
        const key = `${kind}:${h[0]}`;
        if (firstAnchor.has(key)) drop.add(i);
        else firstAnchor.set(key, i);
        continue;
      }
    }
    for (const m of line.matchAll(ASSET_REF_RE)) {
      const kind = m[1] as AssetKind;
      const h = handled(numbers, kind, m[2]);
      if (h === null) continue;
      const key = `${kind}:${h[0]}`;
      if (!firstCite.has(key)) firstCite.set(key, [i, `[${kind}:${trim(m[2])}]`]);
    }
  }

  const inserts = new Map<number, string[]>();
  for (const [key, [i, tag]] of firstCite) {
    if (firstAnchor.has(key)) continue;
    const j = blockBounds(i, lines, isBlank, rstart, rend);
    const at = inserts.get(j);
    if (at) at.push(tag);
    else inserts.set(j, [tag]);
  }

  const anchorLines = new Set(firstAnchor.values());
  const out: string[] = [];
  let skipBlank = false;
  for (let i = 0; i < n; i += 1) {
    const line = lines[i];
    const tags = inserts.get(i);
    if (tags) {
      for (const tag of tags) out.push("", tag);
      skipBlank = false;
    }
    if (skipBlank && isBlank[i]) {
      skipBlank = false;
      continue;
    }
    skipBlank = false;
    if (drop.has(i)) {
      if (out.length > 0 && BLANK_RE.test(out[out.length - 1])) skipBlank = true;
      continue;
    }
    if (anchorLines.has(i)) out.push(line);
    else out.push(rewriteLine(line, numbers, reference));
  }
  if (skipBlank && out.length > 0 && BLANK_RE.test(out[out.length - 1])) {
    out.pop();
  }
  for (const tag of inserts.get(n) ?? []) out.push("", tag);
  return out.join("\n");
}

/** Sola sostituzione dei tag gestiti con il rimando, su tutto il testo
 *  (ancore comprese): mai blocchi nella coda (C9). */
export function citeAssetRefs(
  text: string,
  { numbers, reference }: NormalizeOptions,
): string {
  if (!text) return text;
  return rewriteLine(text, numbers, reference);
}
