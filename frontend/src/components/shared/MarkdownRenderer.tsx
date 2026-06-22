import { lazy, Suspense, useMemo, type ReactNode } from "react";
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

function EquationBlock({ equation }: { equation: LessonContentEquation }) {
  // L'AI a volte include già i delimitatori nel campo `latex`
  // (`$$...$$`, `$...$`, `\[...\]`): riavvolgerli in `$$...$$`
  // produrrebbe delimitatori annidati → KaTeX fallisce e mostra il
  // sorgente in rosso. Li rimuoviamo prima di riavvolgere.
  const inner = (equation.latex || "")
    .trim()
    .replace(/^\\\[/, "")
    .replace(/\\\]$/, "")
    .replace(/^\$+/, "")
    .replace(/\$+$/, "")
    .trim();
  const display = `$$${inner}$$`;
  const captionParts = [equation.label, equation.explanation]
    .map((p) => (p || "").trim())
    .filter(Boolean);
  return (
    <figure className="my-6 overflow-hidden rounded-lg border border-border bg-card">
      <div className="lesson-prose flex justify-center bg-muted/20 p-4">
        <ReactMarkdown
          remarkPlugins={[remarkMath]}
          rehypePlugins={[rehypeKatex]}
        >
          {display}
        </ReactMarkdown>
      </div>
      {captionParts.length > 0 && (
        <figcaption className="border-t border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
          {equation.label && (
            <span className="font-semibold not-italic">{equation.label}</span>
          )}
          {equation.label && equation.explanation && (
            <span className="px-1">—</span>
          )}
          {equation.explanation && (
            <span className="italic">{equation.explanation}</span>
          )}
        </figcaption>
      )}
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
