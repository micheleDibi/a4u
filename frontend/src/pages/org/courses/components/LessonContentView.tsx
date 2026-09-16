import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { LessonContentRaw } from "@/api/courses";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import {
  citeAssetRefs,
  normalizeAssetRefs,
  type ReferenceFn,
} from "@/lib/assetRefNormalize";
import {
  appendUncitedAssetRefs,
  assetNumbersByKind,
  computeAssetNumbers,
  equationLabelFamily,
} from "@/lib/figureNumbering";

interface Props {
  content: LessonContentRaw;
}

type Translate = ReturnType<typeof useTranslation>["t"];

/**
 * Render "foglio bianco": tutto il contenuto della lezione fluisce come
 * un unico documento markdown, senza badge tecnici né label di sezione
 * visibili. Le sezioni interne (introduzione, scaletta, sintesi,
 * key takeaways, references) sono concatenate con `## Titolo` come unico
 * marcatore strutturale.
 *
 * Asset (D3, D4, A12, C2/C9): il corpo (introduzione → sezioni → sintesi)
 * riceve in coda i tag `[KIND:id]` degli asset mai citati (figure, tabelle,
 * equazioni, esempi, in quest'ordine) e la numerazione per kind («Figura
 * N.», «Tabella N.», …) è calcolata sul corpo così esteso e NON ancora
 * normalizzato (`lib/figureNumbering.ts`, allineato al backend
 * `figure_numbering.py`). Poi `normalizeAssetRefs` riscrive ogni citazione
 * in linea nel rimando testuale («Figura 2», «Lemma 1», chiavi
 * `courses.figures.*.ref` nella lingua dell'interfaccia) e lascia UNA
 * ancora su riga propria dopo il blocco della prima citazione; punti
 * chiave e riferimenti ricevono solo rimandi (`citeAssetRefs`), mai
 * blocchi, come nel PDF. `orgId`/`courseId` per le figure `function`
 * arrivano dal `CourseRefContext` del container (A21), non da qui.
 *
 * NB: il contenitore della lezione fa polling (refetchInterval) finché
 * un'altra lezione/PDF è in elaborazione. A ogni tick react-query
 * restituisce un nuovo oggetto `content` con identità diversa ma stesso
 * contenuto: senza memoizzazione l'intero markdown (immagini comprese)
 * verrebbe ri-renderizzato a ripetizione, con flicker visibile. Per
 * questo il componente è `memo`-izzato con confronto sul contenuto reale.
 */
function LessonContentViewImpl({ content }: Props) {
  const { t } = useTranslation();

  // Corpo (con gli asset orfani accodati), numerazione, rimandi e coda.
  const { fullMarkdown, assetNumbers } = useMemo(() => {
    const idsByKind = {
      FIG: (content.visual_assets ?? []).map((a) => a.asset_id),
      TAB: (content.tables ?? []).map((tb) => tb.table_id),
      EQ: (content.equations ?? []).map((eq) => eq.equation_id),
      EX: (content.examples ?? []).map((ex) => ex.example_id),
    };
    const body = appendUncitedAssetRefs(
      buildBodyMarkdown(content, {
        summaryHeading: t("courses.lessonsContent.render.summary"),
      }),
      idsByKind,
    );
    const tail = buildTailMarkdown(content, {
      keyTakeawaysHeading: t("courses.lessonsContent.render.keyTakeaways"),
      referencesHeading: t("courses.lessonsContent.render.references"),
    });
    // I numeri sono calcolati sul corpo NON normalizzato: il normalizzatore
    // riscrive le citazioni e sposta le ancore, i numeri sono un dato.
    const numbers = computeAssetNumbers(body, idsByKind);
    const opts = {
      numbers: assetNumbersByKind(numbers),
      reference: makeReference(content, t),
    };
    return {
      fullMarkdown: [normalizeAssetRefs(body, opts), citeAssetRefs(tail, opts)]
        .filter((p) => p.trim())
        .join("\n\n"),
      assetNumbers: numbers,
    };
  }, [content, t]);

  return (
    <article className="rounded-lg border bg-card px-6 py-8 shadow-sm sm:px-10 sm:py-12">
      <MarkdownRenderer
        source={fullMarkdown}
        visualAssets={content.visual_assets}
        tables={content.tables}
        equations={content.equations}
        examples={content.examples}
        assetNumbers={assetNumbers}
      />
    </article>
  );
}

export const LessonContentView = memo(
  LessonContentViewImpl,
  // Salta il re-render quando il contenuto è strutturalmente invariato
  // (stessa identità logica, nuovo oggetto dal polling). content_raw è
  // JSON serializzabile, quindi la firma è deterministica ed economica.
  (prev, next) =>
    JSON.stringify(prev.content) === JSON.stringify(next.content),
);

/**
 * Rimando testuale per kind, con chiavi `t()` letterali (guardia i18n di
 * `test_frontend_figure_i18n.py`). Per un `[EQ:id]` in famiglia teorema
 * (`equationLabelFamily` → `THM`) il rimando usa la parola del kind
 * («Lemma 2»), come l'intestazione del blocco e il PDF.
 */
function makeReference(content: LessonContentRaw, t: Translate): ReferenceFn {
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

interface BodyOpts {
  summaryHeading: string;
}

interface TailOpts {
  keyTakeawaysHeading: string;
  referencesHeading: string;
}

/** Introduzione → sezioni → sintesi: il corpus in cui gli asset sono
 *  citati e numerati (stesso perimetro di `_build_lesson_body_markdown`
 *  nel backend). */
function buildBodyMarkdown(content: LessonContentRaw, opts: BodyOpts): string {
  const parts: string[] = [];

  // Introduction (no heading — è l'incipit)
  if (content.introduction?.trim()) {
    parts.push(content.introduction.trim());
  }

  // Sections — solo titolo come h2, no badge/section_id
  for (const section of content.sections) {
    if (section.title?.trim()) {
      parts.push(`## ${section.title.trim()}`);
    }
    if (section.content?.trim()) {
      parts.push(section.content.trim());
    }
  }

  // Summary
  if (content.summary?.trim()) {
    parts.push(`## ${opts.summaryHeading}`);
    parts.push(content.summary.trim());
  }

  return parts.join("\n\n");
}

/** Punti chiave e riferimenti: dopo gli asset orfani, senza numerazione
 *  (ricevono solo rimandi testuali, mai blocchi). */
function buildTailMarkdown(content: LessonContentRaw, opts: TailOpts): string {
  const parts: string[] = [];

  // Key takeaways come bullet list
  if (content.key_takeaways && content.key_takeaways.length > 0) {
    parts.push(`## ${opts.keyTakeawaysHeading}`);
    parts.push(
      content.key_takeaways
        .map((kt) => `- ${kt.trim()}`)
        .join("\n"),
    );
  }

  // References
  if (content.references && content.references.length > 0) {
    parts.push(`## ${opts.referencesHeading}`);
    parts.push(
      content.references
        .map((ref) => `- ${ref.citation.trim()}`)
        .join("\n"),
    );
  }

  return parts.join("\n\n");
}

export default LessonContentView;
