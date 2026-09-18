import { AlertTriangle } from "lucide-react";
import { Suspense, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { stripFigurePrefix } from "@/lib/figureNumbering";
import { cn } from "@/lib/utils";

import { InlineMath } from "./InlineMath";

/**
 * Cornice unica delle figure (D4): stesso markup e stessa didascalia del
 * partial backend `templates/partials/figure.html.j2` (`<figure
 * class="figure figure--{variant} figure--{format}">` + `<figcaption>` con
 * l'etichetta «Figura N.» in grassetto), così vista lezione, editor, slide,
 * PDF e frame video coincidono. Niente card: la figura è un elemento
 * tipografico del testo, non un riquadro.
 *
 * - `number` assente → etichetta senza numero («Figura.», A2: slide e
 *   frame video);
 * - la didascalia è ripulita da un eventuale prefisso «Figura 3.» già
 *   scritto dal modello (`stripFigurePrefix`, solo a render);
 * - `extraCaption` è la coda calcolata delle figure `function`; se la
 *   didascalia termina già con la stessa frase non viene ripetuta
 *   (stessa guardia del backend);
 * - la didascalia del docente passa da `InlineMath` (solo il math
 *   `$..$`/`\(..\)` è reso, con KaTeX; il resto è letterale, come nel
 *   PDF); l'etichetta e la coda calcolata restano testo;
 * - il fallback `Suspense` dei renderer caricati in modo pigro sta dentro
 *   la cornice: la didascalia è visibile anche durante il caricamento.
 */
export interface FigureFrameProps {
  assetId: string;
  format: string;
  caption: string;
  altText?: string;
  number?: number | null;
  variant?: "lesson" | "slide";
  extraCaption?: string;
  /** Rimando testuale degli asset (`AssetRefs.cite`) applicato alla
   *  didascalia DOPO `stripFigurePrefix` e prima di `InlineMath`, come il
   *  `caption_renderer` del partial nel backend: un `[FIG:x]` nella
   *  didascalia diventa «Figura 1». Default: identità. */
  cite?: (text: string) => string;
  className?: string;
  children: ReactNode;
}

/** Superficie della figura: chiara e FISSA, non legata al tema. Il tema
 * D3 disegna inchiostro scuro su fondo trasparente (identico a PDF, slide
 * e frame video); sul fondo scuro del frontend le figure Vega-Lite, DOT e
 * `function` sarebbero nere su nero. Una palette scura alternativa
 * romperebbe l'identità con gli artefatti consegnati e la parità con la
 * copia backend del tema: la superficie chiara la conserva. */
/** Punteggiatura che chiude una didascalia: se manca e c'è una coda
 *  calcolata, il partial (e questa cornice) aggiungono un punto. */
const CAPTION_END_RE = /[.!?…:;]$/;

export const FIGURE_SURFACE = "rounded bg-white p-2";

export function FigureFrame({
  assetId,
  format,
  caption,
  altText,
  number,
  variant = "lesson",
  extraCaption,
  cite,
  className,
  children,
}: FigureFrameProps) {
  const { t } = useTranslation();
  const label =
    number != null
      ? t("courses.figures.label", { n: number })
      : t("courses.figures.labelUnnumbered");
  const stripped = stripFigurePrefix(caption || "").trim();
  const text = cite ? cite(stripped) : stripped;
  let extra = (extraCaption || "").trim();
  if (extra && text.endsWith(extra)) extra = "";
  // La coda calcolata è un periodo a sé («Zeri in x = -1, 1.»): senza il
  // punto la didascalia del docente le si fonderebbe contro («…razionale
  // Zeri in x…»). Stessa regola del partial del PDF (`figure_markup`).
  const stop =
    extra !== "" && text !== "" && !CAPTION_END_RE.test(text) ? "." : "";
  const tail = text ? ` ${text}${stop}` : "";

  return (
    <figure
      role="figure"
      aria-label={altText || text || label}
      data-asset-id={assetId}
      className={cn(
        "figure flex flex-col items-center gap-2",
        `figure--${variant}`,
        `figure--${format}`,
        variant === "lesson" ? "my-6" : "my-2",
        className,
      )}
    >
      <div className="figure-body w-full">
        <Suspense fallback={<FigureLoading />}>{children}</Suspense>
      </div>
      <figcaption
        className={cn(
          "figure-caption max-w-prose text-center leading-snug text-foreground",
          variant === "lesson" ? "text-sm" : "text-xs",
        )}
      >
        <span className="figure-label font-semibold">{label}</span>
        {tail ? <InlineMath text={tail} /> : null}
        {extra ? ` ${extra}` : ""}
      </figcaption>
    </figure>
  );
}

/** Segnaposto di caricamento comune ai renderer (`courses.figures.loading`). */
export function FigureLoading({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        "flex h-32 animate-pulse items-center justify-center rounded bg-muted text-xs text-muted-foreground",
        className,
      )}
    >
      {t("courses.figures.loading")}
    </div>
  );
}

export interface FigureErrorBoxProps {
  /** Titolo del box; default `courses.figures.renderError`. */
  title?: string;
  /** Frase di aiuto sotto il titolo (es. «apri l'editor per correggere»). */
  hint?: string;
  /** Messaggio tecnico del renderer, mostrato nei dettagli. */
  detail?: string | null;
  /** Sorgente della figura, mostrato nei dettagli. */
  source?: string | null;
  className?: string;
}

/**
 * Box di errore controllato (stesso stile per Mermaid, Vega-Lite, DOT e
 * `function`): la figura non renderizzabile non rompe mai la pagina e non
 * mostra mai l'SVG di errore del renderer.
 */
export function FigureErrorBox({
  title,
  hint,
  detail,
  source,
  className,
}: FigureErrorBoxProps) {
  const { t } = useTranslation();
  const hasDetails = Boolean(detail || source);
  return (
    <div
      className={cn(
        "rounded-md border border-amber-300/60 bg-amber-50/60 p-3 text-left text-xs",
        "dark:border-amber-700/40 dark:bg-amber-950/30",
        className,
      )}
    >
      <div className="flex items-center gap-2 text-amber-800 dark:text-amber-200">
        <AlertTriangle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        <span className="font-medium">
          {title ?? t("courses.figures.renderError")}
        </span>
      </div>
      {hint && (
        <p className="mt-1 text-amber-700/90 dark:text-amber-300/80">{hint}</p>
      )}
      {hasDetails && (
        <details className="mt-2">
          <summary className="cursor-pointer text-amber-700 dark:text-amber-300">
            {t("courses.lessonsContent.render.figure.showDetails")}
          </summary>
          <div className="mt-2 space-y-2">
            {detail && (
              <div className="font-mono text-[0.7rem] text-amber-900/80 dark:text-amber-200/80">
                {detail}
              </div>
            )}
            {source && (
              <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-amber-100/60 p-2 text-[0.7rem] text-amber-900 dark:bg-amber-950/60 dark:text-amber-100">
                {source}
              </pre>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

export default FigureFrame;
