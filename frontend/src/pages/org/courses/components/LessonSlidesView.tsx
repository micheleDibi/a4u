import { useTranslation } from "react-i18next";

import type {
  LessonContentRaw,
  LessonSlideItem,
  LessonSlidesRaw,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  EquationBlock,
  MarkdownRenderer,
} from "@/components/shared/MarkdownRenderer";
import { MermaidDiagram } from "@/components/shared/MermaidDiagram";
import { mediaUrl } from "@/lib/media";
import { resolveAsset } from "@/lib/slides";

interface Props {
  slides: LessonSlidesRaw;
  contentRaw: LessonContentRaw | null;
}

/**
 * Viewer read-only delle slide di una lezione (Fase 4 §7).
 *
 * Architettura: una `<Card>` per slide, in lista verticale. Header
 * con badge `slide_number`, badge `type`, titolo. Body con bullet,
 * asset embedded (risolti da `references_assets`), speaker hint.
 *
 * Risoluzione asset: cerca prima in `contentRaw` (Fase 3), poi in
 * `slides.new_assets` (asset creati dalla Fase 4). Vedi `lib/slides.ts`.
 */
export function LessonSlidesView({ slides, contentRaw }: Props) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      {slides.slides.map((slide) => (
        <SlideCard
          key={slide.slide_id}
          slide={slide}
          contentRaw={contentRaw}
          newAssets={slides.new_assets}
          newTables={slides.new_tables ?? []}
          newEquations={slides.new_equations ?? []}
          newExamples={slides.new_examples ?? []}
          t={t}
        />
      ))}
    </div>
  );
}

interface SlideCardProps {
  slide: LessonSlideItem;
  contentRaw: LessonContentRaw | null;
  newAssets: LessonSlidesRaw["new_assets"];
  newTables: NonNullable<LessonSlidesRaw["new_tables"]>;
  newEquations: NonNullable<LessonSlidesRaw["new_equations"]>;
  newExamples: NonNullable<LessonSlidesRaw["new_examples"]>;
  t: ReturnType<typeof useTranslation>["t"];
}

function SlideCard({
  slide,
  contentRaw,
  newAssets,
  newTables,
  newEquations,
  newExamples,
  t,
}: SlideCardProps) {
  const typeLabel = t(
    `courses.lessonsSlides.render.types.${slide.type}`,
    { defaultValue: slide.type },
  );
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-[11px]">
              {slide.slide_number}
            </Badge>
            <Badge variant="secondary" className="text-[11px]">
              {typeLabel}
            </Badge>
            <h4 className="text-base font-semibold">{slide.title}</h4>
          </div>
          {slide.source_section_id && (
            <Badge
              variant="muted"
              className="font-mono text-[10px]"
              title={t("courses.lessonsSlides.render.sourceSection")}
            >
              {t("courses.lessonsSlides.render.sourceSection")}:{" "}
              {slide.source_section_id}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Body (prosa breve / sottotitolo) */}
        {slide.body && (
          <p className="text-sm leading-relaxed text-muted-foreground">
            {slide.body}
          </p>
        )}

        {/* Bullets */}
        {slide.bullets.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {slide.bullets.map((b, idx) => (
              <li key={idx}>{b}</li>
            ))}
          </ul>
        ) : !slide.body ? (
          <p className="text-xs italic text-muted-foreground">
            {t("courses.lessonsSlides.render.noBullets")}
          </p>
        ) : null}

        {/* Assets referenziati */}
        {slide.references_assets.length > 0 && (
          <div className="space-y-3">
            {slide.references_assets.map((aid) => (
              <SlideAssetRender
                key={aid}
                assetId={aid}
                contentRaw={contentRaw}
                newAssets={newAssets}
                newTables={newTables}
                newEquations={newEquations}
                newExamples={newExamples}
              />
            ))}
          </div>
        )}

      </CardContent>
    </Card>
  );
}

interface SlideAssetRenderProps {
  assetId: string;
  contentRaw: LessonContentRaw | null;
  newAssets: LessonSlidesRaw["new_assets"];
  newTables: NonNullable<LessonSlidesRaw["new_tables"]>;
  newEquations: NonNullable<LessonSlidesRaw["new_equations"]>;
  newExamples: NonNullable<LessonSlidesRaw["new_examples"]>;
}

function SlideAssetRender({
  assetId,
  contentRaw,
  newAssets,
  newTables,
  newEquations,
  newExamples,
}: SlideAssetRenderProps) {
  const resolved = resolveAsset(
    assetId,
    contentRaw,
    newAssets,
    newTables,
    newEquations,
    newExamples,
  );
  if (!resolved) {
    return (
      <div className="rounded border border-dashed border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
        Asset non trovato: {assetId}
      </div>
    );
  }

  if (resolved.kind === "visual" || resolved.kind === "new_visual") {
    const a = resolved.payload;
    if (a.format === "mermaid") {
      return (
        <figure className="space-y-1">
          <MermaidDiagram code={a.content} />
          {a.caption && (
            <figcaption className="text-xs italic text-muted-foreground">
              {a.caption}
            </figcaption>
          )}
        </figure>
      );
    }
    if (a.format === "image") {
      return (
        <figure className="space-y-1">
          <img
            src={mediaUrl(a.content)}
            alt={a.alt_text || ""}
            className="block max-h-[24rem] w-auto max-w-full rounded"
          />
          {a.caption && (
            <figcaption className="text-xs italic text-muted-foreground">
              {a.caption}
            </figcaption>
          )}
        </figure>
      );
    }
    // Per image_prompt / image_search_query / description (legacy):
    // placeholder testuale.
    return (
      <figure className="space-y-1">
        <div className="rounded-md border bg-muted/30 p-4 text-center text-xs text-muted-foreground">
          {a.alt_text || a.caption || a.content}
        </div>
        {a.caption && (
          <figcaption className="text-xs italic text-muted-foreground">
            {a.caption}
          </figcaption>
        )}
      </figure>
    );
  }

  if (resolved.kind === "table") {
    return (
      <figure className="space-y-1">
        <MarkdownRenderer source={resolved.payload.markdown} />
        {resolved.payload.caption && (
          <figcaption className="text-xs italic text-muted-foreground">
            {resolved.payload.caption}
          </figcaption>
        )}
      </figure>
    );
  }

  if (resolved.kind === "equation") {
    // Renderer unificato: formula nuda o blocco teorema (enunciato +
    // dimostrazione a passaggi), identico alle Dispense.
    return <EquationBlock equation={resolved.payload} />;
  }

  if (resolved.kind === "example") {
    const ex = resolved.payload;
    return (
      <div className="rounded-md border border-border bg-muted/20 p-3">
        {ex.title && (
          <h5 className="mb-1 text-sm font-semibold">{ex.title}</h5>
        )}
        <MarkdownRenderer source={ex.content} />
      </div>
    );
  }

  return null;
}

export default LessonSlidesView;
