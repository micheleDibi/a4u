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
        renderCounter += 1;
        const id = `mermaid-${renderCounter}-${Date.now()}`;
        const { svg: rendered } = await mermaid.render(id, code);
        if (!cancelled) {
          setSvg(rendered);
        }
      } catch (exc) {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : String(exc));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code]);

  if (error) {
    return (
      <div className="rounded border border-destructive/50 bg-destructive/10 p-3 text-xs text-destructive">
        <div className="mb-1 font-semibold">Errore rendering Mermaid</div>
        <div className="font-mono">{error}</div>
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-[0.7rem]">
          {code}
        </pre>
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
        "flex justify-center overflow-x-auto rounded bg-background p-2 [&_svg]:max-w-full [&_svg]:h-auto",
        className,
      )}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

export default MermaidDiagram;
