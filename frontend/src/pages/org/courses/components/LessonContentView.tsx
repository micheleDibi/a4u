import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { LessonContentRaw } from "@/api/courses";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import {
  appendUncitedFigureRefs,
  computeFigureNumbers,
} from "@/lib/figureNumbering";

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
 * Figure (D4, A12): il corpo (introduzione → sezioni → sintesi) riceve in
 * coda i tag `[FIG:id]` degli asset mai citati e la numerazione «Figura N.»
 * è calcolata sul corpo così esteso (`lib/figureNumbering.ts`, allineato al
 * backend `figure_numbering.py`); punti chiave e riferimenti seguono dopo,
 * come nel PDF. `orgId`/`courseId` per le figure `function` arrivano dal
 * `CourseRefContext` del container (A21), non da qui.
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

  // Corpo (con le figure orfane accodate), coda e numerazione.
  const { fullMarkdown, figureNumbers } = useMemo(() => {
    const assetIds = (content.visual_assets ?? []).map((a) => a.asset_id);
    const body = appendUncitedFigureRefs(buildBodyMarkdown(content, {
      summaryHeading: t("courses.lessonsContent.render.summary"),
    }), assetIds);
    const tail = buildTailMarkdown(content, {
      keyTakeawaysHeading: t("courses.lessonsContent.render.keyTakeaways"),
      referencesHeading: t("courses.lessonsContent.render.references"),
    });
    return {
      fullMarkdown: [body, tail].filter((p) => p.trim()).join("\n\n"),
      figureNumbers: computeFigureNumbers(body, assetIds),
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
        figureNumbers={figureNumbers}
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

interface BodyOpts {
  summaryHeading: string;
}

interface TailOpts {
  keyTakeawaysHeading: string;
  referencesHeading: string;
}

/** Introduzione → sezioni → sintesi: il corpus in cui le figure sono
 *  citate e numerate (stesso perimetro di `_build_lesson_body_markdown`
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

/** Punti chiave e riferimenti: dopo le figure orfane, senza numerazione. */
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
