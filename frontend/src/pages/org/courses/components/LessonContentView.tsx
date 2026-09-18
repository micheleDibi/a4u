import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { LessonContentRaw } from "@/api/courses";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { citeAssetRefs, normalizeAssetRefs } from "@/lib/assetRefNormalize";
import { appendUncitedAssetRefs } from "@/lib/figureNumbering";
import {
  buildBodyMarkdown,
  lessonAssetRefs,
  assetIdsByKind,
} from "@/lib/lessonAssetRefs";

interface Props {
  content: LessonContentRaw;
}

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
  const { fullMarkdown, assetNumbers, cite } = useMemo(() => {
    // Numeri e rimando vengono da `lessonAssetRefs` (la stessa mappa che
    // usano le viste slide e discorso): il corpo che li produce porta il
    // segnaposto della sintesi, mai l'etichetta tradotta.
    const refs = lessonAssetRefs(content, t);
    const body = appendUncitedAssetRefs(
      buildBodyMarkdown(content, t("courses.lessonsContent.render.summary")),
      assetIdsByKind(content),
    );
    const tail = buildTailMarkdown(content, {
      keyTakeawaysHeading: t("courses.lessonsContent.render.keyTakeaways"),
      referencesHeading: t("courses.lessonsContent.render.references"),
    });
    const opts = { numbers: refs.numbers, reference: refs.reference };
    return {
      fullMarkdown: [normalizeAssetRefs(body, opts), citeAssetRefs(tail, opts)]
        .filter((p) => p.trim())
        .join("\n\n"),
      assetNumbers: refs.assetNumbers,
      cite: refs.cite,
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
        cite={cite}
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

interface TailOpts {
  keyTakeawaysHeading: string;
  referencesHeading: string;
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
