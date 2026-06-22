import { lazy, Suspense, useMemo, type ReactNode } from "react";
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
import { cn } from "@/lib/utils";

const MermaidDiagram = lazy(() => import("./MermaidDiagram"));

interface MarkdownRendererProps {
  source: string;
  visualAssets?: LessonContentVisualAsset[];
  tables?: LessonContentTable[];
  equations?: LessonContentEquation[];
  examples?: LessonContentExample[];
  className?: string;
}

type AssetKind = "FIG" | "TAB" | "EQ" | "EX";

interface AssetReference {
  kind: AssetKind;
  id: string;
}

const ASSET_REF_RE = /\[(FIG|TAB|EQ|EX):([^\]]+)\]/g;
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
            });
          }
        }
        return <p {...rest}>{children}</p>;
      },
    }),
    [visualMap, tableMap, equationMap, exampleMap],
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
}

function renderAssetBlock(
  kind: AssetKind,
  id: string,
  maps: AssetMaps,
): ReactNode {
  // Match case-insensitive (le mappe hanno chiavi minuscole); l'id
  // originale resta per il messaggio "Asset non trovato".
  const key = id.toLowerCase();
  switch (kind) {
    case "FIG": {
      const asset = maps.visualMap.get(key);
      if (!asset) {
        return <MissingAssetBlock kind={kind} id={id} />;
      }
      return <VisualAssetBlock asset={asset} />;
    }
    case "TAB": {
      const table = maps.tableMap.get(key);
      if (!table) {
        return <MissingAssetBlock kind={kind} id={id} />;
      }
      return <TableBlock table={table} />;
    }
    case "EQ": {
      const eq = maps.equationMap.get(key);
      if (!eq) {
        return <MissingAssetBlock kind={kind} id={id} />;
      }
      return <EquationBlock equation={eq} />;
    }
    case "EX": {
      const ex = maps.exampleMap.get(key);
      if (!ex) {
        return <MissingAssetBlock kind={kind} id={id} />;
      }
      return <ExampleBlock example={ex} />;
    }
  }
}

function VisualAssetBlock({ asset }: { asset: LessonContentVisualAsset }) {
  if (asset.format === "mermaid") {
    return (
      <figure className="my-6 overflow-hidden rounded-lg border border-border bg-card">
        <div className="bg-muted/30 p-4">
          <Suspense
            fallback={
              <div className="flex h-32 animate-pulse items-center justify-center rounded bg-muted text-xs text-muted-foreground">
                Caricamento diagramma…
              </div>
            }
          >
            <MermaidDiagram code={asset.content} />
          </Suspense>
        </div>
        {asset.caption && (
          <figcaption className="border-t border-border bg-muted/20 px-4 py-2 text-xs italic text-muted-foreground">
            {asset.caption}
          </figcaption>
        )}
      </figure>
    );
  }
  if (asset.format === "image") {
    // Immagine caricata dall'utente. `content` è un path relativo (es.
    // `lesson_assets/{cid}/{uuid}.png`); il file è servito da StaticFiles
    // su `/uploads/...`.
    return (
      <figure className="my-6 overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex justify-center bg-muted/30 p-4">
          <img
            src={mediaUrl(asset.content)}
            alt={asset.alt_text || ""}
            className="max-h-[28rem] w-auto max-w-full rounded"
          />
        </div>
        {asset.caption && (
          <figcaption className="border-t border-border bg-muted/20 px-4 py-2 text-xs italic text-muted-foreground">
            {asset.caption}
          </figcaption>
        )}
      </figure>
    );
  }
  if (
    asset.format === "image_prompt" ||
    asset.format === "image_search_query" ||
    asset.format === "description"
  ) {
    return (
      <figure className="my-6 overflow-hidden rounded-lg border border-dashed border-border bg-muted/10">
        <div className="flex min-h-[10rem] items-center justify-center p-6 text-center text-sm text-muted-foreground">
          <span className="italic">{asset.content}</span>
        </div>
        {asset.caption && (
          <figcaption className="border-t border-dashed border-border bg-muted/20 px-4 py-2 text-xs italic text-muted-foreground">
            {asset.caption}
          </figcaption>
        )}
      </figure>
    );
  }
  return null;
}

function TableBlock({ table }: { table: LessonContentTable }) {
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
      {table.caption && (
        <figcaption className="border-t border-border bg-muted/20 px-4 py-2 text-xs italic text-muted-foreground">
          {table.caption}
        </figcaption>
      )}
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
 * Renderer unificato di un asset equazione: formula "nuda" (come prima)
 * oppure blocco teorema/proposizione con enunciato + dimostrazione a
 * passaggi. Esportato per riuso nelle slide (`LessonSlidesView`).
 */
export function EquationBlock({ equation }: { equation: LessonContentEquation }) {
  const { t } = useTranslation();
  const statement = (equation.statement || "").trim();
  const steps = (equation.proof || []).filter(
    (s) => (s?.latex || "").trim() || (s?.text || "").trim(),
  );
  const hasProof = steps.length > 0;

  // Caso semplice (retro-compatibile): formula nuda → figure + caption.
  if (!statement && !hasProof) {
    const captionParts = [equation.label, equation.explanation]
      .map((p) => (p || "").trim())
      .filter(Boolean);
    return (
      <figure className="my-6 overflow-hidden rounded-lg border border-border bg-card">
        <div className="bg-muted/20 p-4">
          <KatexDisplay latex={equation.latex} />
        </div>
        {captionParts.length > 0 && (
          <figcaption className="border-t border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
            {equation.label && (
              <div className="font-semibold not-italic">{equation.label}</div>
            )}
            {(equation.explanation || "").trim() && (
              <div className="italic [&_p:first-child]:mt-0 [&_p:last-child]:mb-0">
                <ProseMarkdown source={equation.explanation} />
              </div>
            )}
          </figcaption>
        )}
      </figure>
    );
  }

  const kind = (equation.kind || "theorem").toLowerCase();
  const kindLabel = t(`courses.theorem.kind.${kind}`, {
    defaultValue: t("courses.theorem.kind.theorem"),
  });
  return (
    <figure className="my-6 overflow-hidden rounded-lg border border-border bg-card">
      <div className="border-b border-border bg-muted/30 px-4 py-2 text-sm font-semibold text-primary">
        {kindLabel}
        {equation.label ? ` ${equation.label}` : ""}
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

function ExampleBlock({ example }: { example: LessonContentExample }) {
  return (
    <aside className="my-6 overflow-hidden rounded-lg border-l-4 border-primary bg-primary/5">
      {example.title && (
        <div className="border-b border-primary/20 bg-primary/10 px-4 py-2 text-sm font-semibold text-primary">
          {example.title}
        </div>
      )}
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
  return (
    <div className="my-3 rounded-md border border-dashed border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
      Asset non trovato: <span className="font-mono">[{kind}:{id}]</span>
    </div>
  );
}

export default MarkdownRenderer;
