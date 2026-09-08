import { memo, useEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import {
  fullWidthSvgMaxHeightPx,
  renderMermaidSvg,
  sanitizeMermaidSvg,
  svgIntrinsicSize,
} from "@/lib/figureFormats";
import { mermaidConfig } from "@/lib/figureTheme";
import { cn } from "@/lib/utils";

import { FigureErrorBox, FigureLoading } from "./FigureFrame";

interface MermaidDiagramProps {
  code: string;
  className?: string;
}

let mermaidInitialized = false;
let renderCounter = 0;

// Righe spurie talvolta emesse dall'AI nel codice Mermaid: il fence
// markdown residuo (```/```mermaid) o nodi-segnaposto isolati come
// `mermaid` / `all` / `all:`. Sono sintatticamente "validi" (passano
// mermaid.parse) ma compaiono come box anomali nel diagramma. Le
// rimuoviamo solo quando una riga è ESATTAMENTE uno di questi token
// (non tocchiamo archi/nodi reali tipo `A --> all` o `all[Etichetta]`).
const MERMAID_JUNK_LINE_RE = /^(?:```.*|mermaid|all)\s*:?\s*$/i;

export function sanitizeMermaidCode(raw: string): string {
  if (!raw) return raw;
  return raw
    .split("\n")
    .filter((line) => !MERMAID_JUNK_LINE_RE.test(line.trim()))
    .join("\n")
    .trim();
}

async function ensureMermaid() {
  const mod = await import("mermaid");
  const mermaid = mod.default;
  if (!mermaidInitialized) {
    // Stessa configurazione del validatore e del pre-render backend
    // (`figure_theme.mermaid_initialize_js`, mirror in figureTheme.ts):
    // `htmlLabels: false` al livello top e tema D3, così l'anteprima
    // coincide con PDF, slide e video. `securityLevel: "strict"` (default
    // di figureTheme) nel browser dell'utente. `useMaxWidth: true` come da
    // default Mermaid: il `max-width` naturale è poi rimosso sotto perché
    // l'SVG riempia il contenitore.
    mermaid.initialize(mermaidConfig({ useMaxWidth: true }));
    mermaidInitialized = true;
  }
  return mermaid;
}

type MermaidFailure = { kind: "syntax" } | { kind: "render"; detail: string };

interface RenderedSvg {
  html: string;
  /** Tetto d'altezza dell'SVG a larghezza piena (`fullWidthSvgMaxHeightPx`):
   *  solo per i diagrammi orizzontali, `null` per quelli verticali. */
  maxHeightPx: number | null;
}

function MermaidDiagramImpl({ code, className }: MermaidDiagramProps) {
  const { t } = useTranslation();
  const [svg, setSvg] = useState<RenderedSvg | null>(null);
  const [failure, setFailure] = useState<MermaidFailure | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const cleanCode = sanitizeMermaidCode(code);

  useEffect(() => {
    let cancelled = false;
    setFailure(null);
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
        const parseOk = await mermaid.parse(cleanCode, {
          suppressErrors: true,
        });
        if (!parseOk) {
          if (!cancelled) setFailure({ kind: "syntax" });
          return;
        }

        renderCounter += 1;
        const id = `mermaid-${renderCounter}-${Date.now()}`;
        // `renderMermaidSvg` e non `mermaid.render`: quando il render
        // fallisce Mermaid lascia nel `<body>` il `<div id="d<id>">` con
        // cui ha misurato il diagramma, e in produzione fallisce sempre
        // per una shape `img:` esterna (la politica della pagina blocca
        // l'immagine, `EncodingError`). Senza la rimozione l'editor
        // impilava una copia visibile del diagramma a ogni battuta.
        const rendered = await renderMermaidSvg(mermaid, id, cleanCode);
        // Sanificazione PRIMA di toccare il documento vivo: il diagramma
        // è reso nel browser di chi guarda, non dal backend, quindi un
        // `<image href="http://…">` uscito da una shape che il gate
        // statico non ha riconosciuto farebbe partire la richiesta da qui
        // (SEC-1, giro 5). `securityLevel: "strict"` di Mermaid sanifica
        // le LABEL, non gli attributi che il renderer stesso emette.
        const safe = sanitizeMermaidSvg(rendered);
        // Markup senza `<svg>`: nessun dettaglio tecnico da mostrare, il
        // box usa la frase localizzata di ripiego («render fallito»).
        if (!safe) {
          if (!cancelled) setFailure({ kind: "render", detail: "" });
          return;
        }
        if (!cancelled) {
          // Mermaid imposta `style="max-width: <natural_px>"` sull'SVG.
          // Questo impedisce al diagramma di crescere oltre la sua
          // dimensione naturale (~300-400px), anche se il container è
          // molto più largo — risultato: testo illeggibile.
          // Strippiamo quel max-width così l'SVG riempie tutto il
          // container disponibile.
          const cleaned = safe.replace(/max-width\s*:\s*[\d.]+px\s*;?/gi, "");
          // Il tetto d'altezza dipende dall'orientamento letto dal viewBox
          // (vedi le classi del contenitore sotto).
          setSvg({
            html: cleaned,
            maxHeightPx: fullWidthSvgMaxHeightPx(svgIntrinsicSize(cleaned)),
          });
        }
      } catch (exc) {
        if (!cancelled) {
          setFailure({
            kind: "render",
            detail: exc instanceof Error ? exc.message : String(exc),
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [cleanCode]);

  if (failure) {
    return (
      <FigureErrorBox
        className={className}
        hint={t("courses.lessonsContent.render.figure.mermaidHint")}
        detail={
          failure.kind === "syntax"
            ? t("courses.lessonsContent.render.figure.syntaxError")
            : failure.detail ||
              t("courses.lessonsContent.render.figure.renderFailed")
        }
        source={cleanCode}
      />
    );
  }

  if (!svg) {
    return <FigureLoading />;
  }

  const style =
    svg.maxHeightPx != null
      ? ({ "--mermaid-max-h": `${svg.maxHeightPx}px` } as CSSProperties)
      : undefined;

  return (
    <div
      ref={containerRef}
      style={style}
      className={cn(
        // Diagramma a tutta larghezza: l'SVG riempie il container così
        // i nodi e le label restano leggibili anche su flowchart densi.
        // overflow-x-auto come fallback se qualche diagramma ha una
        // larghezza minima > container (mai dovrebbe accadere ora che
        // il max-width inline è strippato, ma resta come safety net).
        // Fondo chiaro e fisso come le altre figure: gli archi e le
        // frecce del tema (COLOR_AXIS) sul fondo scuro del tema dark
        // resterebbero sotto il rapporto di contrasto minimo.
        "overflow-x-auto rounded bg-white p-2 [&_svg]:!w-full [&_svg]:!max-w-none [&_svg]:h-auto",
        // Tetto d'altezza SOLO per i diagrammi orizzontali (torta, flowchart
        // LR): max(28rem, altezza naturale), così un diagramma compatto non
        // si dilata a tutta colonna e uno grande non è mai rimpicciolito.
        // Un tetto unito a `width: 100%` farebbe scalare in `meet` i
        // diagrammi verticali (sequence, flowchart TD, class) fino a testo
        // illeggibile: per quelli nessun tetto, geometria a larghezza piena.
        svg.maxHeightPx != null && "[&_svg]:max-h-[var(--mermaid-max-h)]",
        className,
      )}
      dangerouslySetInnerHTML={{ __html: svg.html }}
    />
  );
}

// I prop sono solo stringhe (`code`, `className`): il confronto shallow
// di default di `memo` basta a evitare il re-render del diagramma quando
// il genitore si ri-renderizza (es. polling) ma il codice mermaid non
// cambia. Quando `code` cambia (editor, rigenerazione) il diagramma si
// aggiorna normalmente.
export const MermaidDiagram = memo(MermaidDiagramImpl);

export default MermaidDiagram;
