/**
 * Grammatica del math nei campi inline (didascalie di figura e di tabella,
 * label delle equazioni, titolo degli esempi): specchio dell'istanza `zero`
 * del PDF (`render_markdown_inline` in `course_lesson_pdf_service.py`), che
 * riconosce SOLO il math e lascia tutto il resto letterale (niente enfasi,
 * link, code o escape: `\$5` resta `\$5`, come oggi nel frontend).
 *
 * Regole rispecchiate (dollarmath + `_math_bsdelim` + guardia currency):
 * - `$..$` e `$$..$$` con `allow_space=False` (`$ x $` e `$50 e sale a $70`
 *   sono prosa) e `allow_digits=True` (`2$^{10}$` è math);
 * - `\(..\)` e `\[..\]`, salvo `\[FIG:x\]` (tag di asset) e `\[1\]`,
 *   `\[2, 3\]` (citazioni), che restano letterali;
 * - un `$..$` singolo è un importo, non una formula, se il contenuto è
 *   «importo + separatore» / «separatore + importo» (`$50/$70`, `5$, 10$`)
 *   o se una cifra tocca il delimitatore dall'esterno con contenuto
 *   numerico (`US$50 e US$70`, `50$-70$`): `_is_currency_math`.
 *
 * Nessun import: `backend/tests/test_frontend_inline_math.py` esegue questo
 * modulo con Node (`--experimental-strip-types`) e confronta i segmenti con
 * i token dell'istanza del PDF, caso per caso.
 */

export type InlineMathSegment =
  | { kind: "text"; text: string }
  | { kind: "math"; latex: string; display: boolean };

interface MathMatch {
  latex: string;
  display: boolean;
  /** Delimitatore di apertura: solo `$` è soggetto alla guardia currency. */
  markup: "$" | "$$" | "\\(" | "\\[";
  end: number;
}

// `isWhiteSpace` di markdown-it (spazi, tabulazioni, a capo e gli spazi
// Unicode della categoria Zs).
const WHITESPACE_RE = /[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/;
const ASSET_TAG_RE = /^\s*(FIG|TAB|EQ|EX):/;
const CITATION_LIKE_RE = /^\s*\p{Nd}+(?:\s*[-–,;]\s*\p{Nd}+)*\s*$/u;
const CURRENCY_CONTENT_RE =
  /^(?:\p{Nd}[\p{Nd}.,]*\s*[-\u2013\u2014/,;:]|[/,;:]\s*\p{Nd}[\p{Nd}.,]*)$/u;
const NUMERIC_START_RE = /^[\s+\-\u2013\u2014/,;:]*\p{Nd}/u;
const DIGIT_RE = /^\p{Nd}$/u;

/** Numero dispari di `\` subito prima di `pos`. */
function isEscapedAt(src: string, pos: number): boolean {
  let count = 0;
  for (let i = pos - 1; i >= 0 && src[i] === "\\"; i -= 1) count += 1;
  return count % 2 === 1;
}

function isDigit(ch: string): boolean {
  return DIGIT_RE.test(ch);
}

/** Rule `math_inline` di dollarmath (`allow_space=False`,
 *  `allow_digits=True`, `double_inline=True`) alla posizione `pos`. */
function matchDollar(src: string, pos: number): MathMatch | null {
  const next = src[pos + 1];
  if (next === undefined || WHITESPACE_RE.test(next)) return null;
  if (isEscapedAt(src, pos)) return null;
  const isDouble = next === "$";
  let from = pos + 1 + (isDouble ? 1 : 0);
  let end = -1;
  for (;;) {
    end = src.indexOf("$", from);
    if (end === -1) return null;
    if (isEscapedAt(src, end)) {
      from = end + 1;
      continue;
    }
    if (isDouble) {
      if (end + 1 >= src.length) return null;
      if (src[end + 1] !== "$") {
        from = end + 1;
        continue;
      }
      end += 1;
    }
    break;
  }
  if (WHITESPACE_RE.test(src[end - 1])) return null;
  const latex = isDouble ? src.slice(pos + 2, end - 1) : src.slice(pos + 1, end);
  if (latex === "") return null;
  return { latex, display: isDouble, markup: isDouble ? "$$" : "$", end: end + 1 };
}

/** Rule `_math_bsdelim` del PDF: `\(..\)` in linea, `\[..\]` a blocco;
 *  rifiuta i tag di asset e le citazioni numeriche. */
function matchBackslashDelimiter(src: string, pos: number): MathMatch | null {
  const opener = src[pos + 1];
  if ((opener !== "(" && opener !== "[") || isEscapedAt(src, pos)) return null;
  const closer = opener === "(" ? "\\)" : "\\]";
  let end = src.indexOf(closer, pos + 2);
  while (end !== -1 && isEscapedAt(src, end)) end = src.indexOf(closer, end + 1);
  if (end === -1) return null;
  const latex = src.slice(pos + 2, end);
  if (!latex.trim()) return null;
  if (opener === "[" && (ASSET_TAG_RE.test(latex) || CITATION_LIKE_RE.test(latex))) {
    return null;
  }
  return {
    latex,
    display: opener === "[",
    markup: opener === "(" ? "\\(" : "\\[",
    end: end + 2,
  };
}

interface RawSegment {
  kind: "text" | "math";
  text: string;
  display: boolean;
  markup: MathMatch["markup"] | "";
}

/** `_is_currency_math`: il segmento `i` (un `$..$` singolo) è un importo. */
function isCurrencyMath(segments: RawSegment[], i: number): boolean {
  const seg = segments[i];
  if (seg.kind !== "math" || seg.markup !== "$") return false;
  const content = seg.text;
  if (CURRENCY_CONTENT_RE.test(content.trim())) return true;
  const prev = i > 0 ? segments[i - 1] : null;
  const next = i + 1 < segments.length ? segments[i + 1] : null;
  const nextChar = next && next.kind === "text" ? next.text.slice(0, 1) : "";
  const prevChar = prev && prev.kind === "text" ? prev.text.slice(-1) : "";
  if (nextChar && isDigit(nextChar) && content && isDigit(content[0])) return true;
  return prevChar !== "" && isDigit(prevChar) && NUMERIC_START_RE.test(content);
}

/**
 * Spezza `text` in segmenti di testo letterale e di math. Senza
 * delimitatori riconosciuti ritorna il solo segmento di testo, identico
 * all'ingresso.
 */
export function splitInlineMath(text: string): InlineMathSegment[] {
  const src = text || "";
  const raw: RawSegment[] = [];
  let pending = "";
  const flush = () => {
    if (pending) raw.push({ kind: "text", text: pending, display: false, markup: "" });
    pending = "";
  };
  let pos = 0;
  while (pos < src.length) {
    const ch = src[pos];
    let match: MathMatch | null = null;
    if (ch === "$") match = matchDollar(src, pos);
    else if (ch === "\\") match = matchBackslashDelimiter(src, pos);
    if (match) {
      flush();
      raw.push({ kind: "math", text: match.latex, display: match.display, markup: match.markup });
      pos = match.end;
    } else {
      pending += ch;
      pos += 1;
    }
  }
  flush();

  // Guardia currency (core rule prima di `text_join`): il `$..$` declassato
  // torna testo col delimitatore originale e pesa come testo per i vicini.
  for (let i = 0; i < raw.length; i += 1) {
    if (isCurrencyMath(raw, i)) {
      raw[i] = { kind: "text", text: `$${raw[i].text}$`, display: false, markup: "" };
    }
  }

  // `text_join`: i frammenti di testo adiacenti si rifondono.
  const out: InlineMathSegment[] = [];
  for (const seg of raw) {
    const last = out[out.length - 1];
    if (seg.kind === "text") {
      if (last && last.kind === "text") last.text += seg.text;
      else out.push({ kind: "text", text: seg.text });
    } else {
      out.push({ kind: "math", latex: seg.text, display: seg.display });
    }
  }
  return out;
}
