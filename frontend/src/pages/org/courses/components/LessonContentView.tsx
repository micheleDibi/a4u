import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";

import type { LessonContentRaw } from "@/api/courses";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";

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
 * NB: il contenitore della lezione fa polling (refetchInterval) finché
 * un'altra lezione/PDF è in elaborazione. A ogni tick react-query
 * restituisce un nuovo oggetto `content` con identità diversa ma stesso
 * contenuto: senza memoizzazione l'intero markdown (immagini comprese)
 * verrebbe ri-renderizzato a ripetizione, con flicker visibile. Per
 * questo il componente è `memo`-izzato con confronto sul contenuto reale.
 */
function LessonContentViewImpl({ content }: Props) {
  const { t } = useTranslation();

  // Concatena tutto in un solo documento markdown.
  const fullMarkdown = useMemo(
    () =>
      buildFullMarkdown(content, {
        summaryHeading: t("courses.lessonsContent.render.summary"),
        keyTakeawaysHeading: t("courses.lessonsContent.render.keyTakeaways"),
        referencesHeading: t("courses.lessonsContent.render.references"),
      }),
    [content, t],
  );

  return (
    <article className="rounded-lg border bg-card px-6 py-8 shadow-sm sm:px-10 sm:py-12">
      <MarkdownRenderer
        source={fullMarkdown}
        visualAssets={content.visual_assets}
        tables={content.tables}
        equations={content.equations}
        examples={content.examples}
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

interface BuildOpts {
  summaryHeading: string;
  keyTakeawaysHeading: string;
  referencesHeading: string;
}

function buildFullMarkdown(
  content: LessonContentRaw,
  opts: BuildOpts,
): string {
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
