import { memo, useEffect, useRef, useState } from "react";
import type { EmbedOptions, VisualizationSpec } from "vega-embed";

import { stripFenceAndControl } from "@/lib/figureFormats";
import { VEGALITE_THEME_CONFIG } from "@/lib/figureTheme";
import { cn } from "@/lib/utils";

import { FigureErrorBox, FigureLoading } from "./FigureFrame";

/**
 * Anteprima client di una spec Vega-Lite (`format="vegalite"`).
 *
 * `vega`, `vega-lite` e `vega-embed` sono caricati con `import()` dinamico
 * (chunk separato, come Mermaid); la spec riceve lo stesso `$schema` e lo
 * stesso `config` del renderer server-side (`figure_theme.VEGALITE_THEME_CONFIG`,
 * mirror in `figureTheme.ts`) e viene montata nel DOM da vega-embed
 * tramite ref: nessun `dangerouslySetInnerHTML`. `actions: false` toglie
 * il menu di esportazione; `renderer: "svg"` produce lo stesso vettoriale
 * di vl-convert. Un errore del parser o del renderer diventa il box
 * controllato di `FigureErrorBox`, mai un'eccezione nella pagina.
 */
const VEGALITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json";

interface VegaLiteDiagramProps {
  spec: string;
  className?: string;
}

type Status = "loading" | "ready" | "error";

function parseSpec(raw: string): {
  spec: Record<string, unknown> | null;
  error: string | null;
} {
  const text = stripFenceAndControl(raw);
  if (!text) return { spec: null, error: "spec vuota" };
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return { spec: null, error: "la spec deve essere un oggetto JSON" };
    }
    return { spec: value as Record<string, unknown>, error: null };
  } catch (exc) {
    return {
      spec: null,
      error: exc instanceof Error ? exc.message : String(exc),
    };
  }
}

function VegaLiteDiagramImpl({ spec, className }: VegaLiteDiagramProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let finalize: (() => void) | null = null;
    const container = containerRef.current;
    setStatus("loading");
    setError(null);

    const parsed = parseSpec(spec);
    if (!parsed.spec) {
      setError(parsed.error);
      setStatus("error");
      return undefined;
    }

    (async () => {
      try {
        const { default: embed } = await import("vega-embed");
        if (cancelled || !container) return;
        const vlSpec = {
          ...parsed.spec,
          $schema: VEGALITE_SCHEMA,
        } as VisualizationSpec;
        const options: EmbedOptions = {
          mode: "vega-lite",
          actions: false,
          renderer: "svg",
          hover: false,
          defaultStyle: false,
          config: VEGALITE_THEME_CONFIG as EmbedOptions["config"],
        };
        const result = await embed(container, vlSpec, options);
        if (cancelled) {
          result.finalize();
          return;
        }
        finalize = () => result.finalize();
        setStatus("ready");
      } catch (exc) {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : String(exc));
          setStatus("error");
        }
      }
    })();

    return () => {
      cancelled = true;
      finalize?.();
      container?.replaceChildren();
    };
  }, [spec]);

  return (
    <div className={cn("w-full", className)}>
      {status === "loading" && <FigureLoading />}
      {status === "error" && (
        <FigureErrorBox detail={error} source={stripFenceAndControl(spec)} />
      )}
      <div
        ref={containerRef}
        hidden={status !== "ready"}
        className="flex justify-center overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full"
      />
    </div>
  );
}

export const VegaLiteDiagram = memo(VegaLiteDiagramImpl);

export default VegaLiteDiagram;
