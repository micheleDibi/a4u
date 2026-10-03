import { Fragment, useLayoutEffect, useMemo, useRef } from "react";

import { splitInlineMath } from "@/lib/inlineMath";
import { preloadKatex, useKatex } from "@/lib/katexLoader";

// KaTeX è caricato su richiesta: il download parte con il chunk che contiene
// questo modulo (vedi `lib/katexLoader.ts`).
preloadKatex();

/**
 * Testo di un campo inline (didascalia di figura o di tabella, label di
 * un'equazione, titolo di un esempio) con il SOLO math reso da KaTeX
 * (`$..$`, `$$..$$`, `\(..\)`, `\[..\]`, grammatica di `lib/inlineMath`):
 * niente enfasi, link o code, in parità con `render_markdown_inline` del
 * PDF. Senza math il risultato è il solo nodo di testo di prima: nessun
 * elemento avvolgente, quindi il layout della figcaption non cambia. Il
 * math è sempre in text style (`displayMode: false`): un blocco dentro
 * una didascalia la spezzerebbe. L'etichetta («Figura N.», «Tabella N.»,
 * …) resta fuori, a carico del chiamante.
 */
export function InlineMath({ text }: { text: string }) {
  const segments = useMemo(() => splitInlineMath(text), [text]);
  return (
    <>
      {segments.map((segment, i) =>
        segment.kind === "math" ? (
          <KatexInline key={i} latex={segment.latex} />
        ) : (
          <Fragment key={i}>{segment.text}</Fragment>
        ),
      )}
    </>
  );
}

/** Formula in linea montata nel DOM tramite ref (`katex.render`, come
 *  `FunctionEditor`): nessun HTML iniettato da stringa. Finché KaTeX non è
 *  caricato lo span (inline, nessun salto di riga) mostra il sorgente LaTeX
 *  come testo; poi React toglie il testo e `katex.render` riempie lo span. */
function KatexInline({ latex }: { latex: string }) {
  const katex = useKatex();
  const ref = useRef<HTMLSpanElement | null>(null);
  useLayoutEffect(() => {
    if (!ref.current || !katex) return;
    katex.render(latex, ref.current, {
      throwOnError: false,
      trust: false,
      displayMode: false,
      strict: "ignore",
    });
  }, [katex, latex]);
  return (
    <span ref={ref} className="math-inline">
      {katex ? null : latex}
    </span>
  );
}

export default InlineMath;
