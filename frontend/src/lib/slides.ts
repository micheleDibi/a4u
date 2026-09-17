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
import type { TFunction } from "i18next";

import type {
  LessonContentRaw,
  LessonContentVisualAsset,
  LessonContentTable,
  LessonContentEquation,
  LessonContentExample,
  LessonSlideNewAsset,
} from "@/api/courses";
import { formatLabel } from "@/lib/figureFormats";

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
  // Confronto per `assetRefKey` (maiuscole e spazi ai bordi non contano),
  // come `_resolve_asset_for_slide` nel PDF: il riferimento della slide e
  // l'id dichiarato dell'asset sono generati dall'AI con case non sempre
  // coerente (es. asset `TAB_x` referenziato come `tab_x`), e il CRUD
  // accetta un riferimento con spazi ai bordi.
  const target = assetRefKey(assetId);
  if (contentRaw) {
    const visual = contentRaw.visual_assets.find(
      (a) => assetRefKey(a.asset_id) === target,
    );
    if (visual) return { kind: "visual", payload: visual };

    const table = contentRaw.tables.find(
      (t) => assetRefKey(t.table_id) === target,
    );
    if (table) return { kind: "table", payload: table };

    const equation = contentRaw.equations.find(
      (e) => assetRefKey(e.equation_id) === target,
    );
    if (equation) return { kind: "equation", payload: equation };

    const example = contentRaw.examples.find(
      (e) => assetRefKey(e.example_id) === target,
    );
    if (example) return { kind: "example", payload: example };
  }

  const newAsset = newAssets.find((a) => assetRefKey(a.asset_id) === target);
  if (newAsset) return { kind: "new_visual", payload: newAsset };

  // Nuovi asset non visivi creati in Fase 4 (parità con le Dispense).
  const newTable = newTables.find((t) => assetRefKey(t.table_id) === target);
  if (newTable) return { kind: "table", payload: newTable };

  const newEquation = newEquations.find(
    (e) => assetRefKey(e.equation_id) === target,
  );
  if (newEquation) return { kind: "equation", payload: newEquation };

  const newExample = newExamples.find(
    (e) => assetRefKey(e.example_id) === target,
  );
  if (newExample) return { kind: "example", payload: newExample };

  return null;
}

/**
 * Chiave di confronto di un riferimento di `references_assets`: senza
 * spazi ai bordi e in minuscolo, come il CRUD delle slide
 * (`course_lesson_slides_crud`), `resolveAsset` e il PDF delle slide
 * (`_asset_ref_key`).
 */
export function assetRefKey(assetId: string): string {
  return (assetId || "").trim().toLowerCase();
}

/**
 * Riferimenti senza ripetizioni (confronto per `assetRefKey`, resta la
 * prima grafia nell'ordine): un asset citato due volte dalla stessa slide
 * è mostrato una volta sola, come nel PDF delle slide e nei frame video.
 */
export function uniqueAssetRefs(refs: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const ref of refs) {
    const key = assetRefKey(ref);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(ref);
  }
  return out;
}

/**
 * Lista tutti gli asset disponibili (Fase 3 + Fase 4) per un editor
 * multi-select. Ordine: visual, table, equation, example, new_assets.
 * L'etichetta è `id (formato)` con il formato localizzato da
 * `formatLabel` («A1 (Grafico Vega-Lite)», «tab_new_1 (Tabella, nuovo)»).
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
  newTables: LessonContentTable[],
  newEquations: LessonContentEquation[],
  newExamples: LessonContentExample[],
  t: TFunction,
): AssetOption[] {
  const out: AssetOption[] = [];
  const label = (id: string, format: string, isNew = false) =>
    `${id} (${formatLabel(format, t, { isNew })})`;

  if (contentRaw) {
    for (const a of contentRaw.visual_assets) {
      out.push({
        id: a.asset_id,
        kind: "visual",
        label: label(a.asset_id, a.format),
        caption: a.caption,
      });
    }
    for (const tb of contentRaw.tables) {
      out.push({
        id: tb.table_id,
        kind: "table",
        label: label(tb.table_id, "table"),
        caption: tb.caption,
      });
    }
    for (const e of contentRaw.equations) {
      out.push({
        id: e.equation_id,
        kind: "equation",
        label: label(e.equation_id, "equation"),
        caption: e.label,
      });
    }
    for (const ex of contentRaw.examples) {
      out.push({
        id: ex.example_id,
        kind: "example",
        label: label(ex.example_id, "example"),
        caption: ex.title,
      });
    }
  }

  for (const na of newAssets) {
    out.push({
      id: na.asset_id,
      kind: "new_visual",
      label: label(na.asset_id, na.format, true),
      caption: na.caption,
    });
  }
  for (const tb of newTables) {
    out.push({
      id: tb.table_id,
      kind: "table",
      label: label(tb.table_id, "table", true),
      caption: tb.caption,
    });
  }
  for (const e of newEquations) {
    out.push({
      id: e.equation_id,
      kind: "equation",
      label: label(e.equation_id, "equation", true),
      caption: e.label,
    });
  }
  for (const ex of newExamples) {
    out.push({
      id: ex.example_id,
      kind: "example",
      label: label(ex.example_id, "example", true),
      caption: ex.title,
    });
  }

  return out;
}
