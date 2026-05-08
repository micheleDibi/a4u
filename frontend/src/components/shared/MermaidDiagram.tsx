import { AlertTriangle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

interface MermaidDiagramProps {
  code: string;
  className?: string;
}

let mermaidInitialized = false;
let renderCounter = 0;

async function ensureMermaid() {
  const mod = await import("mermaid");
  const mermaid = mod.default;
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: "default",
      securityLevel: "strict",
      fontFamily: "inherit",
    });
    mermaidInitialized = true;
  }
  return mermaid;
}

export function MermaidDiagram({ code, className }: MermaidDiagramProps) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setSvg(null);

    (async () => {
      try {
        const mermaid = await ensureMermaid();

        // Pre-validazione del codice mermaid: se la parse fallisce,
        // NON chiamiamo `render()` perché mermaid in caso di errore
        // inietta nel DOM una grossa "bomb icon" SVG con scritta
        // "Syntax error in text" che esce dal nostro container e
        // appare visualmente sopra il resto della UI. Con
        // `suppressErrors: true`, parse ritorna `false` invece di
        // throw o renderare il bomb icon.
        const parseOk = await mermaid.parse(code, { suppressErrors: true });
        if (!parseOk) {
          if (!cancelled) {
            setError("Sintassi del diagramma non valida.");
          }
          return;
        }

        renderCounter += 1;
        const id = `mermaid-${renderCounter}-${Date.now()}`;
        const { svg: rendered } = await mermaid.render(id, code);
        if (!cancelled) {
          // Mermaid imposta `style="max-width: <natural_px>"` sull'SVG.
          // Questo impedisce al diagramma di crescere oltre la sua
          // dimensione naturale (~300-400px), anche se il container è
          // molto più largo — risultato: testo illeggibile.
          // Strippiamo quel max-width così l'SVG riempie tutto il
          // container disponibile.
          const cleaned = rendered.replace(
            /max-width\s*:\s*[\d.]+px\s*;?/gi,
            "",
          );
          setSvg(cleaned);
        }
      } catch (exc) {
        if (!cancelled) {
          setError(
            exc instanceof Error
              ? exc.message
              : "Errore durante il rendering del diagramma.",
          );
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code]);

  if (error) {
    return (
      <div
        className={cn(
          "rounded-md border border-amber-300/60 bg-amber-50/60 p-3 text-xs",
          "dark:border-amber-700/40 dark:bg-amber-950/30",
          className,
        )}
      >
        <div className="flex items-center gap-2 text-amber-800 dark:text-amber-200">
          <AlertTriangle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
          <span className="font-medium">Diagramma non disponibile</span>
        </div>
        <p className="mt-1 text-amber-700/90 dark:text-amber-300/80">
          Il codice mermaid contiene un errore di sintassi e non è stato
          possibile generare il diagramma. Apri l'editor della lezione per
          correggere il sorgente.
        </p>
        <details className="mt-2">
          <summary className="cursor-pointer text-amber-700 dark:text-amber-300">
            Mostra codice e dettagli errore
          </summary>
          <div className="mt-2 space-y-2">
            <div className="font-mono text-[0.7rem] text-amber-900/80 dark:text-amber-200/80">
              {error}
            </div>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-amber-100/60 p-2 text-[0.7rem] text-amber-900 dark:bg-amber-950/60 dark:text-amber-100">
              {code}
            </pre>
          </div>
        </details>
      </div>
    );
  }

  if (!svg) {
    return (
      <div className="flex h-32 animate-pulse items-center justify-center rounded bg-muted text-xs text-muted-foreground">
        Rendering del diagramma…
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={cn(
        // Diagramma a tutta larghezza: l'SVG fillsa il container così
        // i nodi e le label restano leggibili anche su flowchart densi.
        // overflow-x-auto come fallback se qualche diagramma ha una
        // larghezza minima > container (mai dovrebbe accadere ora che
        // il max-width inline è strippato, ma resta come safety net).
        "overflow-x-auto rounded bg-background p-2 [&_svg]:!w-full [&_svg]:!max-w-none [&_svg]:h-auto",
        className,
      )}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

export default MermaidDiagram;
