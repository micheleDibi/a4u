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
 */
export function LessonContentView({ content }: Props) {
  const { t } = useTranslation();

  // Concatena tutto in un solo documento markdown.
  const fullMarkdown = buildFullMarkdown(content, {
    summaryHeading: t("courses.lessonsContent.render.summary"),
    keyTakeawaysHeading: t("courses.lessonsContent.render.keyTakeaways"),
    referencesHeading: t("courses.lessonsContent.render.references"),
  });

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
