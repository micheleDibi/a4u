import { memo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Loader } from "vega";
import type { EmbedOptions, VisualizationSpec } from "vega-embed";

import {
  describeFigureParseError,
  figureErrorFromException,
  parseJsonObject,
  sanitizeSvgElement,
  stripFenceAndControl,
  type FigureParseError,
} from "@/lib/figureFormats";
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
 * tramite ref: nessun `dangerouslySetInnerHTML`; l'SVG prodotto passa da
 * `sanitizeSvgElement` (difesa in profondità: il gate del PATCH resta
 * autoritativo). `actions: false` toglie il menu di esportazione;
 * `renderer: "svg"` produce lo stesso vettoriale di vl-convert. Un errore
 * del parser o del renderer diventa il box controllato di
 * `FigureErrorBox` (dettaglio localizzato via `describeFigureParseError`),
 * mai un'eccezione nella pagina. Il `loader` è inerte: `data.url`, `mark
 * image` e `href` sono rifiutati dal gate del PATCH, ma l'anteprima
 * dell'editor rende la spec prima del salvataggio e il loader di default
 * di vega-embed eseguirebbe davvero il fetch dal browser del docente.
 */
const VEGALITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json";

function refuseUri(): Promise<never> {
  return Promise.reject(new Error("vega_loader_disabled"));
}

/** Loader che non carica nulla (D5: solo `data.values` e `datasets`);
 *  vega gestisce il rifiuto di `sanitize` per href e immagini senza errori
 *  in pagina. */
const INERT_LOADER: Loader = {
  load: refuseUri,
  sanitize: refuseUri,
  http: refuseUri,
  file: refuseUri,
};

interface VegaLiteDiagramProps {
  spec: string;
  className?: string;
}

type Status = "loading" | "ready" | "error";

function VegaLiteDiagramImpl({ spec, className }: VegaLiteDiagramProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<FigureParseError | null>(null);

  useEffect(() => {
    let cancelled = false;
    let finalize: (() => void) | null = null;
    const container = containerRef.current;
    setStatus("loading");
    setError(null);

    const parsed = parseJsonObject(spec);
    if (!parsed.value) {
      setError(parsed.error);
      setStatus("error");
      return undefined;
    }

    (async () => {
      try {
        const { default: embed } = await import("vega-embed");
        if (cancelled || !container) return;
        const vlSpec = {
          ...parsed.value,
          $schema: VEGALITE_SCHEMA,
        } as VisualizationSpec;
        const options: EmbedOptions = {
          mode: "vega-lite",
          actions: false,
          renderer: "svg",
          hover: false,
          defaultStyle: false,
          loader: INERT_LOADER,
          config: VEGALITE_THEME_CONFIG as EmbedOptions["config"],
        };
        const result = await embed(container, vlSpec, options);
        if (cancelled) {
          result.finalize();
          return;
        }
        finalize = () => result.finalize();
        for (const svg of Array.from(container.querySelectorAll("svg"))) {
          sanitizeSvgElement(svg);
        }
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
      finalize?.();
      container?.replaceChildren();
    };
  }, [spec]);

  return (
    <div className={cn("w-full", className)}>
      {status === "loading" && <FigureLoading />}
      {status === "error" && (
        <FigureErrorBox
          detail={error ? describeFigureParseError(error, t) : null}
          source={stripFenceAndControl(spec)}
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

export const VegaLiteDiagram = memo(VegaLiteDiagramImpl);

export default VegaLiteDiagram;
