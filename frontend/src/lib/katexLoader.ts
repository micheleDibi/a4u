import { useEffect, useState } from "react";
import type { Options as MarkdownOptions } from "react-markdown";

/**
 * Caricamento su richiesta di KaTeX (~270 KB minificati più il suo CSS e i
 * font) e del plugin `rehype-katex`: prima erano importati staticamente e
 * finivano nel chunk delle pagine corso anche quando sullo schermo non c'era
 * nessuna formula. Ogni consumer chiama `preloadKatex()` /
 * `preloadRehypeKatex()` al livello di modulo, così il download parte appena
 * si carica il chunk della pagina, in parallelo alla fetch dei dati: il
 * fallback dei componenti si vede solo se la formula arriva prima di KaTeX.
 */

type Katex = typeof import("katex").default;
type RehypeKatex = typeof import("rehype-katex").default;
type RehypePlugins = NonNullable<MarkdownOptions["rehypePlugins"]>;

let katexModule: Katex | null = null;
let katexPromise: Promise<Katex> | null = null;
let rehypeKatexModule: RehypeKatex | null = null;
let rehypeKatexPromise: Promise<RehypeKatex> | null = null;

/**
 * KaTeX insieme al suo CSS. La promessa si risolve solo quando anche il
 * foglio di stile è caricato (il preload di Vite attende l'evento `load` del
 * `<link>`), così nessuna formula compare senza stile. Su errore (chunk
 * irraggiungibile) la promessa memorizzata si azzera e il prossimo
 * componente riprova.
 */
export function loadKatex(): Promise<Katex> {
  if (!katexPromise) {
    const pending = Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(
      ([mod]) => {
        katexModule = mod.default;
        return mod.default;
      },
    );
    pending.catch(() => {
      if (katexPromise === pending) katexPromise = null;
    });
    katexPromise = pending;
  }
  return katexPromise;
}

/** Il plugin `rehype-katex` (che usa lo stesso modulo `katex`) più il CSS. */
export function loadRehypeKatex(): Promise<RehypeKatex> {
  if (!rehypeKatexPromise) {
    const pending = Promise.all([import("rehype-katex"), loadKatex()]).then(([mod]) => {
      rehypeKatexModule = mod.default;
      return mod.default;
    });
    pending.catch(() => {
      if (rehypeKatexPromise === pending) rehypeKatexPromise = null;
    });
    rehypeKatexPromise = pending;
  }
  return rehypeKatexPromise;
}

/** Avvia il download senza attenderlo (un errore lo gestisce chi lo userà). */
export function preloadKatex(): void {
  loadKatex().catch(() => undefined);
}

/** Come `preloadKatex`, per il plugin dei renderer markdown. */
export function preloadRehypeKatex(): void {
  loadRehypeKatex().catch(() => undefined);
}

/**
 * Stato di caricamento condiviso dagli hook: il modulo se c'è già (subito,
 * senza un render a vuoto), altrimenti `null` e un nuovo render quando arriva.
 */
function useLoaded<T>(current: T | null, load: () => Promise<T>): T | null {
  const [value, setValue] = useState<T | null>(current);
  useEffect(() => {
    if (value) return;
    let alive = true;
    load().then(
      // forma funzionale: `rehype-katex` è esso stesso una funzione e
      // `setValue(fn)` la tratterebbe come un updater
      (loaded) => {
        if (alive) setValue(() => loaded);
      },
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [value, load]);
  return value;
}

/** KaTeX se già caricato, altrimenti `null` (e un nuovo render all'arrivo). */
export function useKatex(): Katex | null {
  return useLoaded(katexModule, loadKatex);
}

const NO_PLUGINS: RehypePlugins = [];
let katexPlugins: RehypePlugins | null = null;

/**
 * `rehypePlugins` per `ReactMarkdown`: `[rehypeKatex]` quando il plugin è
 * caricato, altrimenti nessun plugin. Nel frattempo `remark-math` lascia la
 * formula come `<code class="language-math">` col sorgente LaTeX, nello
 * stesso punto del testo. Gli array sono stabili fra i render.
 */
export function useRehypeKatexPlugins(): RehypePlugins {
  const rehypeKatex = useLoaded(rehypeKatexModule, loadRehypeKatex);
  if (!rehypeKatex) return NO_PLUGINS;
  katexPlugins ??= [rehypeKatex];
  return katexPlugins;
}
