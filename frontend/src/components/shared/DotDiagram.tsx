import { memo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Viz } from "@viz-js/viz";

import {
  describeFigureParseError,
  figureErrorFromException,
  sanitizeSvgElement,
  stripFenceAndControl,
  type FigureParseError,
} from "@/lib/figureFormats";
import { dotDefaultsPrelude, type DotDefaultsBlock } from "@/lib/figureTheme";
import { cn } from "@/lib/utils";

import { FigureErrorBox, FigureLoading } from "./FigureFrame";

/**
 * Anteprima client di un sorgente Graphviz DOT (`format="dot"`).
 *
 * `@viz-js/viz` (Graphviz compilato in WebAssembly) è caricato con
 * `import()` dinamico e istanziato una sola volta; l'SVG prodotto da
 * `renderSVGElement` è un nodo DOM appeso tramite ref (nessun
 * `dangerouslySetInnerHTML`) dopo `sanitizeSvgElement`: un `URL=` o un
 * `href=` nel sorgente non produce mai un link attivo nell'anteprima
 * (il gate del PATCH li rifiuta comunque per i contenuti persistiti). Il
 * tema (`DOT_DEFAULTS`, mirror di `figure_theme.DOT_DEFAULTS`) è iniettato
 * dopo la `{` di apertura per i blocchi `graph/node/edge [` che il sorgente
 * non definisce, come fa `_dot_with_theme` nel registro backend. Un errore
 * di sintassi diventa il box controllato di `FigureErrorBox` (dettaglio
 * localizzato via `describeFigureParseError`).
 */
interface DotDiagramProps {
  source: string;
  className?: string;
}

type Status = "loading" | "ready" | "error";

const DOT_BLOCK_RES: Record<DotDefaultsBlock, RegExp> = {
  graph: /\bgraph\s*\[/,
  node: /\bnode\s*\[/,
  edge: /\bedge\s*\[/,
};

/** Sorgente con il tema iniettato (specchio di `_dot_with_theme`). */
function dotWithTheme(source: string): string {
  const brace = source.indexOf("{");
  if (brace < 0) return source;
  const skip = new Set<DotDefaultsBlock>(
    (Object.keys(DOT_BLOCK_RES) as DotDefaultsBlock[]).filter((k) =>
      DOT_BLOCK_RES[k].test(source),
    ),
  );
  const prelude = dotDefaultsPrelude(skip);
  if (!prelude) return source;
  return `${source.slice(0, brace + 1)}\n${prelude}\n${source.slice(brace + 1)}`;
}

let vizPromise: Promise<Viz> | null = null;

function ensureViz(): Promise<Viz> {
  if (!vizPromise) {
    vizPromise = import("@viz-js/viz").then((mod) => mod.instance());
    vizPromise.catch(() => {
      vizPromise = null;
    });
  }
  return vizPromise;
}

function DotDiagramImpl({ source, className }: DotDiagramProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<FigureParseError | null>(null);

  const clean = stripFenceAndControl(source);

  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    setStatus("loading");
    setError(null);

    if (!clean) {
      setError({ code: "empty" });
      setStatus("error");
      return undefined;
    }

    (async () => {
      try {
        const viz = await ensureViz();
        if (cancelled || !container) return;
        const svg = viz.renderSVGElement(dotWithTheme(clean));
        svg.removeAttribute("style");
        sanitizeSvgElement(svg);
        container.replaceChildren(svg);
        setStatus("ready");
      } catch (exc) {
        if (!cancelled) {
          setError(figureErrorFromException(exc));
          setStatus("error");
        }
      }
    })();

    return () => {
      cancelled = true;
      container?.replaceChildren();
    };
  }, [clean]);

  return (
    <div className={cn("w-full", className)}>
      {status === "loading" && <FigureLoading />}
      {status === "error" && (
        <FigureErrorBox
          detail={error ? describeFigureParseError(error, t) : null}
          source={clean}
        />
      )}
      <div
        ref={containerRef}
        hidden={status !== "ready"}
        className="flex justify-center overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full"
      />
    </div>
  );
}

export const DotDiagram = memo(DotDiagramImpl);

export default DotDiagram;
