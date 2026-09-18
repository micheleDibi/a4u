import { memo, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { sanitizeTrim, verticalChainVariant } from "@/lib/chainLayout";
import {
  type FigureVariant,
  fitFigureWidthMm,
  measureSvgFontPx,
  MERMAID_FALLBACK_FONT_PX,
  MM_PER_PX,
  REFERENCE_BOX_MM,
  renderMermaidSvg,
  sanitizeMermaidSvg,
  svgIntrinsicBox,
} from "@/lib/figureFormats";
import { mermaidConfig } from "@/lib/figureTheme";
import { cn } from "@/lib/utils";

import { FigureErrorBox, FigureLoading } from "./FigureFrame";

interface MermaidDiagramProps {
  code: string;
  className?: string;
  /** Superficie su cui la figura finirà: decide il box di riferimento e la
   *  banda con cui si sceglie la direzione di una catena (D15). Le slide
   *  hanno un box largo e basso, dove il verticale di norma perde. */
  variant?: FigureVariant;
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

/**
 * Caratteri di controllo tolti prima di tutto il resto: C0 senza `\t`,
 * `\n` e `\r`, DEL e C1. È la classe di
 * `figure_render_service._CONTROL_CHARS_RE`, che il backend toglie in
 * `MermaidRenderer.sanitize` prima di rendere: senza questo passaggio la
 * vista partiva da byte diversi da quelli della pagina e poteva scegliere
 * una direzione diversa per la stessa catena. Scritta come intervallo di
 * codici e non come classe di regex perché `no-control-regex` vieta il
 * letterale.
 */
function isControlChar(code: number): boolean {
  return (
    code <= 0x08 ||
    code === 0x0b ||
    code === 0x0c ||
    (code >= 0x0e && code <= 0x1f) ||
    (code >= 0x7f && code <= 0x9f)
  );
}

export function sanitizeMermaidCode(raw: string): string {
  if (!raw) return raw;
  const joined = [...raw]
    .filter((ch) => !isControlChar(ch.codePointAt(0) ?? 0))
    .join("")
    .split("\n")
    .filter((line) => !MERMAID_JUNK_LINE_RE.test(sanitizeTrim(line)))
    .join("\n");
  return sanitizeTrim(joined);
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
  /** Larghezza del wrapper (px CSS) a cui il testo più piccolo del
   *  diagramma cade nella banda di leggibilità del web (8-11 pt, D11);
   *  `null` se il viewBox non è determinabile (nessun vincolo). */
  widthPx: number | null;
}

/**
 * Larghezza in px a cui rendere l'SVG (mirror di `_figure_width_style` del
 * PDF, senza box: la colonna la applica il CSS `min(100%, …)`). Il corpo
 * del testo è misurato nel DOM con lo stesso JS del pre-render backend;
 * misura fallita → fallback del tema (14 px); nessun testo → scala
 * naturale. Per difetto al centesimo di px, come i mm del PDF.
 */
function fittedWidthPx(svg: string): number | null {
  const box = svgIntrinsicBox(svg);
  if (!box) return null;
  const m = measureSvgFontPx(svg);
  const baseFontPx =
    m === null ? MERMAID_FALLBACK_FONT_PX : m.count === 0 ? null : m.min;
  const fit = fitFigureWidthMm({
    vbW: box.vbW,
    vbH: box.vbH,
    baseFontPx,
    boxWMm: null,
    boxHMm: null,
    variant: "lesson",
    intrinsicWPx: box.widthPx,
  });
  return fit ? Math.floor((fit.widthMm / MM_PER_PX) * 100) / 100 : null;
}

/**
 * Fit dell'SVG nel box di RIFERIMENTO della superficie su cui la figura
 * finirà (`null` se il viewBox non è determinabile). La vista non conosce
 * il template del docente, quindi per DECIDERE la direzione di una catena
 * (D15) usa lo stesso box che la resa userà sul template di default: la
 * dispensa 168 × 242 mm (`LESSON_REFERENCE_BOX_MM`), la slide 255 × 86,6
 * mm (`SLIDE_REFERENCE_BOX_MM`), ciascuna con la propria banda. Decidere
 * sempre con il box della dispensa mostrava in anteprima una disposizione
 * che la slide esportata non avrebbe avuto: misurata la catena di 12 nodi,
 * nella dispensa il verticale vince (2,64 → 8,59 pt) e nella slide perde
 * (4,01 contro 3,07 pt). La larghezza RESA resta quella di
 * `fittedWidthPx`, senza box: sul web il limite è la colonna.
 */
function referenceFit(
  svg: string,
  variant: FigureVariant,
): { textPt: number; inBand: boolean } | null {
  const box = svgIntrinsicBox(svg);
  if (!box) return null;
  const m = measureSvgFontPx(svg);
  const baseFontPx =
    m === null ? MERMAID_FALLBACK_FONT_PX : m.count === 0 ? null : m.min;
  const referenceBox = REFERENCE_BOX_MM[variant];
  const fit = fitFigureWidthMm({
    vbW: box.vbW,
    vbH: box.vbH,
    baseFontPx,
    boxWMm: referenceBox[0],
    boxHMm: referenceBox[1],
    variant,
    intrinsicWPx: box.widthPx,
  });
  return fit ? { textPt: fit.textPt, inBand: fit.inBand } : null;
}

// Mermaid imposta `style="max-width: <natural_px>"` sull'SVG: lo togliamo,
// perché la larghezza la decide la banda di leggibilità, non la dimensione
// naturale del diagramma.
const MERMAID_MAX_WIDTH_RE = /max-width\s*:\s*[\d.]+px\s*;?/gi;

/**
 * SVG reso, sanificato e ripulito dal `max-width`, oppure `null` se il
 * render fallisce o il markup non contiene un `<svg>`.
 *
 * Sanificazione PRIMA di toccare il documento vivo: il diagramma è reso
 * nel browser di chi guarda, non dal backend, quindi un `<image
 * href="http://…">` uscito da una shape che il gate statico non ha
 * riconosciuto farebbe partire la richiesta da qui (SEC-1, giro 5).
 * `securityLevel: "strict"` di Mermaid sanifica le LABEL, non gli
 * attributi che il renderer stesso emette.
 */
async function renderCleanSvg(
  mermaid: Awaited<ReturnType<typeof ensureMermaid>>,
  id: string,
  code: string,
): Promise<string | null> {
  // `renderMermaidSvg` e non `mermaid.render`: quando il render fallisce
  // Mermaid lascia nel `<body>` il `<div id="d<id>">` con cui ha misurato
  // il diagramma, e in produzione fallisce sempre per una shape `img:`
  // esterna (la politica della pagina blocca l'immagine, `EncodingError`).
  // Senza la rimozione l'editor impilava una copia visibile del diagramma
  // a ogni battuta.
  const safe = sanitizeMermaidSvg(await renderMermaidSvg(mermaid, id, code));
  return safe ? safe.replace(MERMAID_MAX_WIDTH_RE, "") : null;
}

function MermaidDiagramImpl({
  code,
  className,
  variant = "lesson",
}: MermaidDiagramProps) {
  const { t } = useTranslation();
  const [svg, setSvg] = useState<RenderedSvg | null>(null);
  const [failure, setFailure] = useState<MermaidFailure | null>(null);

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
        const cleaned = await renderCleanSvg(mermaid, id, cleanCode);
        // Markup senza `<svg>`: nessun dettaglio tecnico da mostrare, il
        // box usa la frase localizzata di ripiego («render fallito»).
        if (!cleaned) {
          if (!cancelled) setFailure({ kind: "render", detail: "" });
          return;
        }
        // Direzione della catena (D15): se il diagramma esce SOTTO la
        // banda nel box di riferimento della SUA superficie (dispensa o
        // slide) e il sorgente è una catena dichiarata in orizzontale, si
        // rende anche la variante verticale e si tiene quella che dà il
        // corpo più grande. Il sorgente salvato non cambia: la scelta vive
        // nella resa, qui come nel PDF. La misura avviene sull'SVG già
        // sanificato: `<style>` (tema e corpi dei testi) sopravvive alla
        // sanificazione, quindi il `font-size` calcolato è quello che il
        // lettore vedrà.
        let chosen = cleaned;
        const before = referenceFit(cleaned, variant);
        const flippedCode =
          before && !before.inBand ? verticalChainVariant(cleanCode) : null;
        if (before && flippedCode) {
          // La variante è un di più: se la sua resa fallisce resta
          // l'originale, che è già valido. Un `throw` qui manderebbe il
          // diagramma buono nel box d'errore.
          const flipped = await renderCleanSvg(
            mermaid,
            `${id}-v`,
            flippedCode,
          ).catch(() => null);
          const after = flipped ? referenceFit(flipped, variant) : null;
          if (flipped && after && after.textPt > before.textPt) {
            chosen = flipped;
          }
        }
        if (!cancelled) {
          // Il wrapper interno porta `min(100%, Wpx)` e l'SVG lo riempie
          // (`width: 100%`).
          setSvg({ html: chosen, widthPx: fittedWidthPx(chosen) });
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
  }, [cleanCode, variant]);

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

  return (
    <div
      className={cn(
        // Contenitore esterno: scorrimento orizzontale di sicurezza (il
        // wrapper interno non supera mai il 100%), fondo chiaro e fisso
        // come le altre figure: gli archi e le frecce del tema
        // (COLOR_AXIS) sul fondo scuro del tema dark resterebbero sotto
        // il rapporto di contrasto minimo.
        "overflow-x-auto rounded bg-white p-2",
        className,
      )}
    >
      {/*
        Wrapper interno senza padding: la larghezza viene dalla banda di
        leggibilità (D10/D11), `min(100%, Wpx)`, così il testo più piccolo
        del diagramma cade fra 8 e 11 pt (il flowchart D8 passa dalla
        larghezza piena a 532 px in una colonna di 900) e in una colonna
        stretta l'SVG riempie il 100% senza scorrere: nessun tetto
        d'altezza, nessun `clientWidth` né `ResizeObserver` (vale 0 nei
        pannelli chiusi dell'editor), il vincolo di colonna lo applica il
        browser a ogni resize. L'SVG riempie il wrapper (`width: 100%`)
        e conserva le proporzioni (`height: auto`). Stessa politica del
        PDF (`_figure_width_style`), senza box: sul web la colonna è il
        solo limite.
      */}
      <div
        className="mx-auto [&_svg]:!w-full [&_svg]:!max-w-none [&_svg]:h-auto"
        style={{
          width: svg.widthPx != null ? `min(100%, ${svg.widthPx}px)` : undefined,
        }}
        dangerouslySetInnerHTML={{ __html: svg.html }}
      />
    </div>
  );
}

// I prop sono solo stringhe (`code`, `className`): il confronto shallow
// di default di `memo` basta a evitare il re-render del diagramma quando
// il genitore si ri-renderizza (es. polling) ma il codice mermaid non
// cambia. Quando `code` cambia (editor, rigenerazione) il diagramma si
// aggiorna normalmente.
export const MermaidDiagram = memo(MermaidDiagramImpl);

export default MermaidDiagram;
