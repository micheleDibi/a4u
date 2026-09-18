/**
 * Numerazione per kind e rimando testuale degli asset di UNA lezione
 * (D3, D4): il contratto che le tre viste condividono, specchio di
 * `AssetRefs` / `lesson_asset_refs` in `course_lesson_pdf_service.py`.
 *
 * I numeri nascono sempre dal corpo della dispensa (`content_raw`:
 * introduzione → sezioni → sintesi, esteso in coda con i tag degli asset
 * mai citati), mai dalla superficie che li usa: così «Figura 1» è la
 * stessa figura nella dispensa, sulla slide, nel discorso e nei tre PDF.
 * Le slide e il discorso non ricalcolano nulla: prendono questa mappa e
 * chiamano `cite`, che sostituisce le sole citazioni in linea (mai un
 * blocco figura: sulla slide la figura arriva da `references_assets`).
 *
 * Gli asset dichiarati SOLO in Fase 4 (`slides.new_*`) non entrano nella
 * numerazione — la dispensa non li conosce e un numero assegnato qui
 * andrebbe in collisione con quelli del corpo: un tag che li cita resta
 * letterale come un id inesistente, ed è `unresolved` a elencarli.
 *
 * L'etichetta della sintesi non entra nel corpo usato per i numeri: nel
 * backend è il segnaposto `__SUMMARY_HEADING__`, sostituito DOPO
 * `computeAssetNumbers`. Qui il corpo dei numeri usa lo stesso
 * segnaposto, così la lingua dell'interfaccia non può spostare un numero.
 */

import type {
  LessonContentRaw,
  LessonContentSection,
} from "@/api/courses";
import { citeAssetRefs, type ReferenceFn } from "@/lib/assetRefNormalize";
import {
  appendUncitedAssetRefs,
  assetNumbersByKind,
  ASSET_REF_RE,
  computeAssetNumbers,
  equationLabelFamily,
  type AssetIdsByKind,
} from "@/lib/figureNumbering";

/** Segnaposto dell'intestazione «Sintesi» (mirror del backend). */
export const SUMMARY_HEADING_PLACEHOLDER = "__SUMMARY_HEADING__";

/** La sola parte di `t()` che serve qui: chiave + interpolazione. */
export type TranslateFn = (
  key: string,
  options?: Record<string, unknown>,
) => string;

/** Sottoinsieme di `LessonContentRaw` che basta alla numerazione. */
export type AssetRefsContent = Partial<
  Pick<
    LessonContentRaw,
    | "introduction"
    | "sections"
    | "summary"
    | "visual_assets"
    | "tables"
    | "equations"
    | "examples"
  >
>;

export interface AssetRefs {
  /** `{KIND: Map<idLower, N>}` per `normalizeAssetRefs`/`citeAssetRefs`. */
  numbers: ReturnType<typeof assetNumbersByKind>;
  /** `{[KIND, idLower] → N}`, per i blocchi numerati della dispensa. */
  assetNumbers: Map<string, number>;
  reference: ReferenceFn;
  /** Rimandi testuali: mai blocchi, mai ancore. */
  cite: (text: string | null | undefined) => string;
  /** I tag che `cite` NON risolve e che restano quindi letterali. */
  unresolved: (...texts: Array<string | null | undefined>) => string[];
}

/** Id dichiarati per kind, nell'ordine degli array (mirror di
 *  `_asset_ids_by_kind`). */
export function assetIdsByKind(content: AssetRefsContent): AssetIdsByKind {
  return {
    FIG: (content.visual_assets ?? []).map((a) => a.asset_id),
    TAB: (content.tables ?? []).map((tb) => tb.table_id),
    EQ: (content.equations ?? []).map((eq) => eq.equation_id),
    EX: (content.examples ?? []).map((ex) => ex.example_id),
  };
}

/** Introduzione → sezioni → sintesi: il corpus in cui gli asset sono
 *  citati e numerati (stesso perimetro di `_build_lesson_body_markdown`).
 *  `summaryHeading` è l'etichetta della sintesi; il default è il
 *  segnaposto del backend, che non partecipa alla numerazione. */
export function buildBodyMarkdown(
  content: AssetRefsContent,
  summaryHeading: string = SUMMARY_HEADING_PLACEHOLDER,
): string {
  const parts: string[] = [];
  if (content.introduction?.trim()) {
    parts.push(content.introduction.trim());
  }
  for (const section of (content.sections ?? []) as LessonContentSection[]) {
    if (section.title?.trim()) {
      parts.push(`## ${section.title.trim()}`);
    }
    if (section.content?.trim()) {
      parts.push(section.content.trim());
    }
  }
  if (content.summary?.trim()) {
    parts.push(`## ${summaryHeading}`);
    parts.push(content.summary.trim());
  }
  return parts.join("\n\n");
}

/**
 * Rimando testuale per kind, con chiavi `t()` letterali (guardia i18n di
 * `test_frontend_figure_i18n.py`). Per un `[EQ:id]` in famiglia teorema
 * (`equationLabelFamily` → `THM`) il rimando usa la parola del kind
 * («Lemma 2»), come l'intestazione del blocco e il PDF.
 */
export function makeReference(
  content: AssetRefsContent,
  t: TranslateFn,
): ReferenceFn {
  const theoremKinds = new Map<string, string>();
  for (const eq of content.equations ?? []) {
    if (equationLabelFamily(eq) === "THM") {
      theoremKinds.set(
        String(eq.equation_id ?? "").trim().toLowerCase(),
        (eq.kind || "theorem").toLowerCase(),
      );
    }
  }
  return (kind, idLower, n) => {
    switch (kind) {
      case "FIG":
        return t("courses.figures.ref", { n });
      case "TAB":
        return t("courses.figures.table.ref", { n });
      case "EX":
        return t("courses.figures.example.ref", { n });
      case "EQ": {
        const theoremKind = theoremKinds.get(idLower);
        if (theoremKind === undefined) {
          return t("courses.figures.equation.ref", { n });
        }
        const kindWord = t(`courses.theorem.kind.${theoremKind}`, {
          defaultValue: t("courses.theorem.kind.theorem"),
        });
        return t("courses.figures.theorem.ref", { kind: kindWord, n });
      }
    }
  };
}

/**
 * Numeri e rimando testuale della lezione, dal SOLO `content_raw`: è la
 * numerazione della dispensa (stesse funzioni, stessi input), quindi i
 * numeri non possono divergere fra le superfici.
 */
export function lessonAssetRefs(
  content: AssetRefsContent | null | undefined,
  t: TranslateFn,
): AssetRefs {
  const raw = content ?? {};
  const idsByKind = assetIdsByKind(raw);
  const body = appendUncitedAssetRefs(buildBodyMarkdown(raw), idsByKind);
  const assetNumbers = computeAssetNumbers(body, idsByKind);
  const numbers = assetNumbersByKind(assetNumbers);
  const reference = makeReference(raw, t);
  const cite = (text: string | null | undefined) =>
    citeAssetRefs(text ?? "", { numbers, reference });
  return {
    numbers,
    assetNumbers,
    reference,
    cite,
    unresolved: (...texts) => {
      const out: string[] = [];
      for (const text of texts) {
        for (const m of cite(text).matchAll(ASSET_REF_RE)) {
          if (!out.includes(m[0])) out.push(m[0]);
        }
      }
      return out;
    },
  };
}
