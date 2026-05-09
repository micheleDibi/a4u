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
): AssetOption[] {
  const out: AssetOption[] = [];

  if (contentRaw) {
    for (const a of contentRaw.visual_assets) {
      out.push({
        id: a.asset_id,
        kind: "visual",
        label: `${a.asset_id} (${a.asset_type})`,
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
      label: `${na.asset_id} (${na.asset_type}, new)`,
      caption: na.caption,
    });
  }

  return out;
}
