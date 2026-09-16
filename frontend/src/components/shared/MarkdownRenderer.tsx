import { lazy, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

import { mediaUrl } from "@/lib/media";

import type {
  LessonContentEquation,
  LessonContentExample,
  LessonContentTable,
  LessonContentVisualAsset,
} from "@/api/courses";
import { isLegacyFormat } from "@/lib/figureFormats";
import { equationLabelFamily, nonEmptyProofSteps } from "@/lib/figureNumbering";
import { cn } from "@/lib/utils";

import { FigureFrame } from "./FigureFrame";
import { FunctionFigure } from "./FunctionFigure";
import { InlineMath } from "./InlineMath";

const MermaidDiagram = lazy(() => import("./MermaidDiagram"));
const VegaLiteDiagram = lazy(() => import("./VegaLiteDiagram"));
const DotDiagram = lazy(() => import("./DotDiagram"));

interface MarkdownRendererProps {
  source: string;
  visualAssets?: LessonContentVisualAsset[];
  tables?: LessonContentTable[];
  equations?: LessonContentEquation[];
  examples?: LessonContentExample[];
  /**
   * Numeri degli asset (`Map<"KIND:id_lower", N>`, chiave come in
   * `computeAssetNumbers`) calcolati dal chiamante sull'intero corpo della
   * dispensa (`LessonContentView`): il renderer non può numerare da solo
   * perché è montato anche su frammenti (slide, esempi). Senza mappa i
   * blocchi hanno l'etichetta senza numero («Figura.», «Tabella.»,
   * «Equazione.», «Esempio.»; il teorema resta la sola parola del kind).
   */
  assetNumbers?: Map<string, number>;
  className?: string;
}

type AssetKind = "FIG" | "TAB" | "EQ" | "EX";

interface AssetReference {
  kind: AssetKind;
  id: string;
}

// Allineata a `FIG_REF_RE` di `lib/figureNumbering.ts`, a `ASSET_REF_RE` di
// `RichTextEditor` e a `_ASSET_REF_RE` del PDF: il tag non attraversa la riga.
const ASSET_REF_RE = /\[(FIG|TAB|EQ|EX):([^\]\n]+)\]/g;
const ASSET_PLACEHOLDER_RE = /^@@ASSET_(FIG|TAB|EQ|EX)_([^@]+)@@$/;

/**
 * Normalizza i delimitatori LaTeX usati comunemente da modelli AI verso
 * la sintassi `$..$` / `$$..$$` riconosciuta da `remark-math`:
 *   - `\(...\)`  →  `$...$`        (inline math LaTeX-style)
 *   - `\[...\]`  →  `$$...$$`      (display math LaTeX-style)
 *
 * Si esclude esplicitamente `\[FIG|TAB|EQ|EX:..\]` perché in questo
 * codebase è un riferimento ad asset eventualmente "scappato" e va
 * lasciato al pre-processor degli asset ref.
 */
function normalizeMathDelimiters(source: string): string {
  let out = source;
  // Display math: `\[ ... \]` → `$$ ... $$`. Salta gli asset refs.
  out = out.replace(/\\\[([\s\S]*?)\\\]/g, (match, inner: string) => {
    if (/^\s*(FIG|TAB|EQ|EX):/.test(inner)) return match;
    return `$$${inner}$$`;
  });
  // Inline math: `\( ... \)` → `$ ... $`
  out = out.replace(/\\\(([\s\S]*?)\\\)/g, (_m, inner: string) => `$${inner}$`);
  return out;
}

function preprocessAssetRefs(source: string): {
  preprocessed: string;
  refs: AssetReference[];
} {
  const refs: AssetReference[] = [];
  const preprocessed = source.replace(
    ASSET_REF_RE,
    (_match, kind: string, id: string) => {
      const trimmedId = id.trim();
      refs.push({ kind: kind as AssetKind, id: trimmedId });
      // Inseriamo come paragrafo isolato per lasciare che react-markdown
      // ce lo passi come blocco (verrà sostituito dal custom renderer).
      return `\n\n@@ASSET_${kind}_${trimmedId}@@\n\n`;
    },
  );
  return { preprocessed, refs };
}

export function MarkdownRenderer({
  source,
  visualAssets = [],
  tables = [],
  equations = [],
  examples = [],
  assetNumbers,
  className,
}: MarkdownRendererProps) {
  const { preprocessed } = useMemo(
    () => preprocessAssetRefs(normalizeMathDelimiters(source || "")),
    [source],
  );

  // Chiavi normalizzate a minuscolo: i ref `[KIND:id]` nel testo e l'id
  // dichiarato dell'asset sono generati dall'AI con case non sempre
  // coerente (es. asset `TAB_x` referenziato come `[TAB:tab_x]`). Il
  // lookup in `renderAssetBlock` normalizza nello stesso modo.
  const visualMap = useMemo(
    () => new Map(visualAssets.map((a) => [a.asset_id.toLowerCase(), a])),
    [visualAssets],
  );
  const tableMap = useMemo(
    () => new Map(tables.map((t) => [t.table_id.toLowerCase(), t])),
    [tables],
  );
  const equationMap = useMemo(
    () => new Map(equations.map((e) => [e.equation_id.toLowerCase(), e])),
    [equations],
  );
  const exampleMap = useMemo(
    () => new Map(examples.map((ex) => [ex.example_id.toLowerCase(), ex])),
    [examples],
  );

  const components: Components = useMemo(
    () => ({
      p({ children, ...rest }) {
        // Se il paragrafo contiene solo un placeholder asset, renderizzalo
        // come blocco custom invece di <p>.
        const text =
          typeof children === "string"
            ? children.trim()
            : Array.isArray(children) && children.length === 1 && typeof children[0] === "string"
              ? (children[0] as string).trim()
              : null;
        if (text) {
          const m = ASSET_PLACEHOLDER_RE.exec(text);
          if (m) {
            const kind = m[1] as AssetKind;
            const id = m[2];
            return renderAssetBlock(kind, id, {
              visualMap,
              tableMap,
              equationMap,
              exampleMap,
              assetNumbers,
            });
          }
        }
        return <p {...rest}>{children}</p>;
      },
    }),
    [visualMap, tableMap, equationMap, exampleMap, assetNumbers],
  );

  return (
    <div className={cn("lesson-prose max-w-none", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={components}
      >
        {preprocessed}
      </ReactMarkdown>
    </div>
  );
}

interface AssetMaps {
  visualMap: Map<string, LessonContentVisualAsset>;
  tableMap: Map<string, LessonContentTable>;
  equationMap: Map<string, LessonContentEquation>;
  exampleMap: Map<string, LessonContentExample>;
  assetNumbers?: Map<string, number>;
}

function renderAssetBlock(
  kind: AssetKind,
  id: string,
  maps: AssetMaps,
): ReactNode {
  // Match case-insensitive (le mappe hanno chiavi minuscole); l'id
  // originale resta per il messaggio "Asset non trovato". Il numero ha la
  // stessa chiave `KIND:id_lower` di `computeAssetNumbers`.
  const key = id.toLowerCase();
  const number = maps.assetNumbers?.get(`${kind}:${key}`);
  switch (kind) {
    case "FIG": {
      const asset = maps.visualMap.get(key);
      if (!asset) {
        return <MissingAssetBlock kind={kind} id={id} />;
      }
      return <VisualAssetBlock asset={asset} number={number} />;
    }
    case "TAB": {
      const table = maps.tableMap.get(key);
      if (!table) {
        return <MissingAssetBlock kind={kind} id={id} />;
      }
      return <TableBlock table={table} number={number} />;
    }
    case "EQ": {
      const eq = maps.equationMap.get(key);
      if (!eq) {
        return <MissingAssetBlock kind={kind} id={id} />;
      }
      return <EquationBlock equation={eq} number={number} />;
    }
    case "EX": {
      const ex = maps.exampleMap.get(key);
      if (!ex) {
        return <MissingAssetBlock kind={kind} id={id} />;
      }
      return <ExampleBlock example={ex} number={number} />;
    }
  }
}

/**
 * Corpo di una figura per formato (senza cornice): usato da
 * `VisualAssetBlock` (dispensa) e da `LessonSlidesView` (slide). I
 * renderer pesanti sono caricati in modo pigro; il fallback `Suspense`
 * sta nella `FigureFrame` del chiamante. Le figure `function` hanno il
 * proprio componente con cornice (`FunctionFigure`: la didascalia
 * calcolata arriva dal backend insieme all'SVG).
 */
export function VisualAssetBody({
  asset,
  imageClassName,
}: {
  asset: LessonContentVisualAsset;
  imageClassName?: string;
}) {
  const { t } = useTranslation();
  if (asset.format === "mermaid") {
    return <MermaidDiagram code={asset.content} />;
  }
  if (asset.format === "vegalite") {
    return <VegaLiteDiagram spec={asset.content} />;
  }
  if (asset.format === "dot") {
    return <DotDiagram source={asset.content} />;
  }
  if (asset.format === "image") {
    // Immagine caricata dall'utente. `content` è un path relativo (es.
    // `lesson_assets/{cid}/{uuid}.png`); il file è servito da StaticFiles
    // su `/uploads/...`.
    return (
      <img
        src={mediaUrl(asset.content)}
        alt={asset.alt_text || ""}
        className={cn("mx-auto block h-auto w-auto max-w-full", imageClassName)}
      />
    );
  }
  if (isLegacyFormat(asset.format)) {
    // image_prompt / image_search_query / description: il testo del
    // prompt o della descrizione, come nel PDF (`figure-fallback`).
    return (
      <div className="flex min-h-[6rem] items-center justify-center rounded border border-dashed bg-muted/10 px-4 py-3 text-center text-sm italic text-muted-foreground">
        {asset.content}
      </div>
    );
  }
  return (
    <div className="flex min-h-[6rem] items-center justify-center rounded border border-dashed bg-muted/10 px-4 py-3 text-center text-xs italic text-muted-foreground">
      {t("courses.figures.missing")}
    </div>
  );
}

function VisualAssetBlock({
  asset,
  number,
}: {
  asset: LessonContentVisualAsset;
  number?: number;
}) {
  if (asset.format === "function") {
    return (
      <FunctionFigure
        assetId={asset.asset_id}
        content={asset.content}
        caption={asset.caption}
        altText={asset.alt_text}
        number={number}
      />
    );
  }
  return (
    <FigureFrame
      assetId={asset.asset_id}
      format={asset.format}
      caption={asset.caption}
      altText={asset.alt_text}
      number={number}
    >
      <VisualAssetBody asset={asset} imageClassName="max-h-[28rem]" />
    </FigureFrame>
  );
}

/** Tabella con l'etichetta sempre presente («Tabella N.», senza `number`
 *  «Tabella.»: stessa forma del PDF), seguita dalla didascalia se c'è
 *  (solo il math è reso: `InlineMath`, come `render_markdown_inline`). */
function TableBlock({
  table,
  number,
}: {
  table: LessonContentTable;
  number?: number;
}) {
  const { t } = useTranslation();
  const label =
    number != null
      ? t("courses.figures.table.label", { n: number })
      : t("courses.figures.table.labelUnnumbered");
  return (
    <figure className="my-6 overflow-hidden rounded-lg border border-border bg-card">
      <div className="lesson-prose overflow-x-auto p-2">
        {/* remarkMath + rehypeKatex: le celle possono contenere math
            inline ($V$, $S\to aS$, …) che va renderizzato come nelle
            prose e negli esempi, non lasciato come testo grezzo. */}
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
        >
          {table.markdown}
        </ReactMarkdown>
      </div>
      <figcaption className="border-t border-border bg-muted/20 px-4 py-2 text-xs italic text-muted-foreground">
        <span className="figure-label font-semibold not-italic">{label}</span>
        {table.caption ? <InlineMath text={` ${table.caption}`} /> : null}
      </figcaption>
    </figure>
  );
}

/** Ribilancia gli ambienti LaTeX malformati emessi a volte dall'AI:
 *  `\end{env}` senza `\begin{env}` (e viceversa), oppure allineamento
 *  (`&` / `\\`) fuori da un ambiente. Rende renderizzabili formule
 *  altrimenti rotte (es. `aligned` con il `\begin` mancante). */
function balanceMathEnv(s: string): string {
  const beginM = s.match(/\\begin\{([a-zA-Z*]+)\}/);
  const endM = s.match(/\\end\{([a-zA-Z*]+)\}/);
  if (endM && !beginM) return `\\begin{${endM[1]}} ${s}`;
  if (beginM && !endM) return `${s} \\end{${beginM[1]}}`;
  if (!beginM && !endM && (/\\\\/.test(s) || /(?<!\\)&/.test(s))) {
    return `\\begin{aligned} ${s} \\end{aligned}`;
  }
  return s;
}

/** Normalizza il LaTeX: rimuove delimitatori già presenti (per evitare
 *  l'annidamento `$$...$$` che fa fallire KaTeX) e ribilancia gli ambienti. */
function normalizeLatex(latex: string): string {
  const stripped = (latex || "")
    .trim()
    .replace(/^\\\[/, "")
    .replace(/\\\]$/, "")
    .replace(/^\$+/, "")
    .replace(/\$+$/, "")
    .trim();
  return balanceMathEnv(stripped);
}

/** Formula in display mode (KaTeX); `null` se vuota. */
function KatexDisplay({ latex }: { latex: string }) {
  const inner = normalizeLatex(latex);
  if (!inner) return null;
  return (
    <div className="lesson-prose flex justify-center py-1">
      <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
        {`$$${inner}$$`}
      </ReactMarkdown>
    </div>
  );
}

/** Testo markdown (statement / passo dimostrazione) con math inline. */
function ProseMarkdown({ source }: { source: string }) {
  return (
    <div className="lesson-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}

/**
 * Renderer unificato di un asset equazione: formula "nuda" oppure blocco
 * teorema/proposizione con enunciato + dimostrazione a passaggi; la
 * famiglia è decisa da `equationLabelFamily` (mirror del backend). Con
 * `number` l'etichetta è «Equazione N.» / «Lemma N.»; senza (slide) è
 * «Equazione.» / la sola parola del kind. La `label` dell'autore, in
 * entrambi i rami, passa da `InlineMath` (solo il math è reso, come nel
 * PDF). Esportato per riuso nelle slide (`LessonSlidesView`).
 */
export function EquationBlock({
  equation,
  number,
}: {
  equation: LessonContentEquation;
  number?: number;
}) {
  const { t } = useTranslation();
  const statement = (equation.statement || "").trim();
  const steps = nonEmptyProofSteps(equation.proof);
  const hasProof = steps.length > 0;

  // Caso semplice (retro-compatibile): formula nuda → figure + caption,
  // con l'etichetta sempre presente e la label dell'autore accanto.
  if (equationLabelFamily(equation) === "EQ") {
    const label =
      number != null
        ? t("courses.figures.equation.label", { n: number })
        : t("courses.figures.equation.labelUnnumbered");
    return (
      <figure className="my-6 overflow-hidden rounded-lg border border-border bg-card">
        <div className="bg-muted/20 p-4">
          <KatexDisplay latex={equation.latex} />
        </div>
        <figcaption className="border-t border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
          <div className="font-semibold not-italic">
            <span className="figure-label">{label}</span>
            {equation.label ? <InlineMath text={` ${equation.label}`} /> : null}
          </div>
          {(equation.explanation || "").trim() && (
            <div className="italic [&_p:first-child]:mt-0 [&_p:last-child]:mb-0">
              <ProseMarkdown source={equation.explanation} />
            </div>
          )}
        </figcaption>
      </figure>
    );
  }

  const kind = (equation.kind || "theorem").toLowerCase();
  const kindLabel = t(`courses.theorem.kind.${kind}`, {
    defaultValue: t("courses.theorem.kind.theorem"),
  });
  const head =
    number != null
      ? t("courses.figures.theorem.label", { kind: kindLabel, n: number })
      : kindLabel;
  return (
    <figure className="my-6 overflow-hidden rounded-lg border border-border bg-card">
      <div className="border-b border-border bg-muted/30 px-4 py-2 text-sm font-semibold text-primary">
        {head}
        {equation.label ? <InlineMath text={` ${equation.label}`} /> : null}
      </div>
      <div className="space-y-2 p-4">
        {statement && <ProseMarkdown source={statement} />}
        <KatexDisplay latex={equation.latex} />
        {hasProof && (
          <div className="mt-2 border-l-2 border-primary/30 pl-3">
            <div className="text-sm font-semibold italic text-muted-foreground">
              {t("courses.theorem.proof")}.
            </div>
            <div className="space-y-2">
              {steps.map((s, i) => (
                <div key={i} className="space-y-1">
                  {(s.text || "").trim() && <ProseMarkdown source={s.text} />}
                  <KatexDisplay latex={s.latex || ""} />
                </div>
              ))}
            </div>
            <div className="pt-1 text-right text-base leading-none">&#8718;</div>
          </div>
        )}
        {(equation.explanation || "").trim() && (
          <div className="border-t border-border pt-2 text-xs italic text-muted-foreground [&_p:first-child]:mt-0 [&_p:last-child]:mb-0">
            <ProseMarkdown source={equation.explanation} />
          </div>
        )}
      </div>
    </figure>
  );
}

/** Esempio con l'etichetta sempre presente («Esempio N.», senza `number`
 *  «Esempio.»: stessa forma del PDF), seguita dal titolo se c'è (solo il
 *  math è reso: `InlineMath`). */
function ExampleBlock({
  example,
  number,
}: {
  example: LessonContentExample;
  number?: number;
}) {
  const { t } = useTranslation();
  const label =
    number != null
      ? t("courses.figures.example.label", { n: number })
      : t("courses.figures.example.labelUnnumbered");
  return (
    <aside className="my-6 overflow-hidden rounded-lg border-l-4 border-primary bg-primary/5">
      <div className="border-b border-primary/20 bg-primary/10 px-4 py-2 text-sm font-semibold text-primary">
        <span className="figure-label">{label}</span>
        {example.title ? <InlineMath text={` ${example.title}`} /> : null}
      </div>
      <div className="lesson-prose px-4 py-3">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
        >
          {example.content}
        </ReactMarkdown>
      </div>
    </aside>
  );
}

function MissingAssetBlock({ kind, id }: { kind: AssetKind; id: string }) {
  const { t } = useTranslation();
  return (
    <div className="my-3 rounded-md border border-dashed border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
      {t("courses.lessonsContent.render.missingAsset")}{" "}
      <span className="font-mono">[{kind}:{id}]</span>
    </div>
  );
}

export default MarkdownRenderer;
