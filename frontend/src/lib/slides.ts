/**
 * Helper per il rendering delle slide (Fase 4).
 *
 * Le slide referenziano asset tramite ID (`references_assets`). Gli ID
 * possono puntare a:
 * - asset di Fase 3 (`lesson.content_raw.{visual_assets,tables,equations,examples}`)
 * - asset nuovi creati dall'AI in Fase 4 (`slides_raw.new_assets`)
 *
 * Il viewer FE risolve l'ID in un payload tipizzato + ne sceglie il
 * componente di rendering corretto.
 */
import type {
  LessonContentRaw,
  LessonContentVisualAsset,
  LessonContentTable,
  LessonContentEquation,
  LessonContentExample,
  LessonSlideNewAsset,
} from "@/api/courses";

export type ResolvedAsset =
  | { kind: "visual"; payload: LessonContentVisualAsset }
  | { kind: "table"; payload: LessonContentTable }
  | { kind: "equation"; payload: LessonContentEquation }
  | { kind: "example"; payload: LessonContentExample }
  | { kind: "new_visual"; payload: LessonSlideNewAsset };

/**
 * Cerca un asset per ID, prima in `content_raw` (Fase 3) poi in
 * `new_assets` di Fase 4. Ritorna `null` se non trovato.
 */
export function resolveAsset(
  assetId: string,
  contentRaw: LessonContentRaw | null,
  newAssets: LessonSlideNewAsset[],
  newTables: LessonContentTable[] = [],
  newEquations: LessonContentEquation[] = [],
  newExamples: LessonContentExample[] = [],
): ResolvedAsset | null {
  if (contentRaw) {
    const visual = contentRaw.visual_assets.find(
      (a) => a.asset_id === assetId,
    );
    if (visual) return { kind: "visual", payload: visual };

    const table = contentRaw.tables.find((t) => t.table_id === assetId);
    if (table) return { kind: "table", payload: table };

    const equation = contentRaw.equations.find(
      (e) => e.equation_id === assetId,
    );
    if (equation) return { kind: "equation", payload: equation };

    const example = contentRaw.examples.find(
      (e) => e.example_id === assetId,
    );
    if (example) return { kind: "example", payload: example };
  }

  const newAsset = newAssets.find((a) => a.asset_id === assetId);
  if (newAsset) return { kind: "new_visual", payload: newAsset };

  // Nuovi asset non visivi creati in Fase 4 (parità con le Dispense).
  const newTable = newTables.find((t) => t.table_id === assetId);
  if (newTable) return { kind: "table", payload: newTable };

  const newEquation = newEquations.find((e) => e.equation_id === assetId);
  if (newEquation) return { kind: "equation", payload: newEquation };

  const newExample = newExamples.find((e) => e.example_id === assetId);
  if (newExample) return { kind: "example", payload: newExample };

  return null;
}

/**
 * Lista tutti gli asset disponibili (Fase 3 + Fase 4) per un editor
 * multi-select. Ordine: visual, table, equation, example, new_assets.
 */
export interface AssetOption {
  id: string;
  kind: "visual" | "table" | "equation" | "example" | "new_visual";
  label: string;
  caption?: string;
}

export function listAvailableAssets(
  contentRaw: LessonContentRaw | null,
  newAssets: LessonSlideNewAsset[],
  newTables: LessonContentTable[] = [],
  newEquations: LessonContentEquation[] = [],
  newExamples: LessonContentExample[] = [],
): AssetOption[] {
  const out: AssetOption[] = [];

  if (contentRaw) {
    for (const a of contentRaw.visual_assets) {
      out.push({
        id: a.asset_id,
        kind: "visual",
        label: `${a.asset_id} (${a.format})`,
        caption: a.caption,
      });
    }
    for (const t of contentRaw.tables) {
      out.push({
        id: t.table_id,
        kind: "table",
        label: `${t.table_id} (table)`,
        caption: t.caption,
      });
    }
    for (const e of contentRaw.equations) {
      out.push({
        id: e.equation_id,
        kind: "equation",
        label: `${e.equation_id} (equation)`,
        caption: e.label,
      });
    }
    for (const ex of contentRaw.examples) {
      out.push({
        id: ex.example_id,
        kind: "example",
        label: `${ex.example_id} (example)`,
        caption: ex.title,
      });
    }
  }

  for (const na of newAssets) {
    out.push({
      id: na.asset_id,
      kind: "new_visual",
      label: `${na.asset_id} (${na.format}, new)`,
      caption: na.caption,
    });
  }
  for (const t of newTables) {
    out.push({
      id: t.table_id,
      kind: "table",
      label: `${t.table_id} (table, new)`,
      caption: t.caption,
    });
  }
  for (const e of newEquations) {
    out.push({
      id: e.equation_id,
      kind: "equation",
      label: `${e.equation_id} (equation, new)`,
      caption: e.label,
    });
  }
  for (const ex of newExamples) {
    out.push({
      id: ex.example_id,
      kind: "example",
      label: `${ex.example_id} (example, new)`,
      caption: ex.title,
    });
  }

  return out;
}
