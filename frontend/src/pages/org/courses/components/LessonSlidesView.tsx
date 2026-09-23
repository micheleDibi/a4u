import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type {
  LessonContentRaw,
  LessonSlideItem,
  LessonSlidesRaw,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { FigureFrame } from "@/components/shared/FigureFrame";
import { FunctionFigure } from "@/components/shared/FunctionFigure";
import { SourceFigure } from "@/components/shared/SourceFigure";
import { TikzFigure } from "@/components/shared/TikzFigure";
import { InlineMath } from "@/components/shared/InlineMath";
import {
  EquationBlock,
  MarkdownRenderer,
  VisualAssetBody,
} from "@/components/shared/MarkdownRenderer";
import { lessonAssetRefs, type AssetRefs } from "@/lib/lessonAssetRefs";
import { assetRefKey, resolveAsset, uniqueAssetRefs } from "@/lib/slides";

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
 *
 * Figure: `FigureFrame` con `variant="slide"` e senza numero («Figura.»,
 * A2), come nel PDF delle slide e nei frame video; i renderer (Mermaid,
 * Vega-Lite, DOT) sono caricati in modo pigro da `VisualAssetBody`.
 *
 * Titolo, prosa e bullet passano PRIMA dal rimando testuale degli asset
 * (`lessonAssetRefs` su `contentRaw`: un `[FIG:x]` lasciato dal modello
 * diventa «Figura 1», con i NUMERI DELLA DISPENSA e mai un blocco figura
 * — la figura sulla slide arriva da `references_assets`) e poi da
 * `InlineMath` (solo testo e formule, come `citeAssetRefs` +
 * `render_markdown_inline` nel PDF delle slide, WP4/WP8). Un tag che
 * nessun numero risolve — id inesistente, o asset dichiarato solo in
 * Fase 4, che la dispensa non numera — resta com'è. Un asset citato più
 * volte dalla stessa slide è mostrato una volta.
 */
export function LessonSlidesView({ slides, contentRaw }: Props) {
  const { t } = useTranslation();
  const refs = useMemo(() => lessonAssetRefs(contentRaw, t), [contentRaw, t]);
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
          refs={refs}
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
  refs: AssetRefs;
  t: ReturnType<typeof useTranslation>["t"];
}

function SlideCard({
  slide,
  contentRaw,
  newAssets,
  newTables,
  newEquations,
  newExamples,
  refs,
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
            <h4 className="text-base font-semibold">
              <InlineMath text={refs.cite(slide.title)} />
            </h4>
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
            <InlineMath text={refs.cite(slide.body)} />
          </p>
        )}

        {/* Bullets */}
        {slide.bullets.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {slide.bullets.map((b, idx) => (
              <li key={idx}>
                <InlineMath text={refs.cite(b)} />
              </li>
            ))}
          </ul>
        ) : !slide.body ? (
          <p className="text-xs italic text-muted-foreground">
            {t("courses.lessonsSlides.render.noBullets")}
          </p>
        ) : null}

        {/* Assets referenziati: una volta sola anche se citati più volte
            (confronto per `assetRefKey`), come nel PDF delle slide. */}
        {slide.references_assets.length > 0 && (
          <div className="space-y-3">
            {uniqueAssetRefs(slide.references_assets).map((aid) => (
              <SlideAssetRender
                key={assetRefKey(aid)}
                assetId={aid}
                contentRaw={contentRaw}
                newAssets={newAssets}
                newTables={newTables}
                newEquations={newEquations}
                newExamples={newExamples}
                cite={refs.cite}
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
  cite: (text: string) => string;
}

function SlideAssetRender({
  assetId,
  contentRaw,
  newAssets,
  newTables,
  newEquations,
  newExamples,
  cite,
}: SlideAssetRenderProps) {
  const { t } = useTranslation();
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
        {t("courses.lessonsContent.render.missingAsset")}{" "}
        <span className="font-mono">{assetId}</span>
      </div>
    );
  }

  if (resolved.kind === "visual" || resolved.kind === "new_visual") {
    const a = resolved.payload;
    if (a.format === "function") {
      return (
        <FunctionFigure
          assetId={a.asset_id}
          content={a.content}
          caption={a.caption}
          altText={a.alt_text}
          variant="slide"
          cite={cite}
        />
      );
    }
    if (a.format === "source_figure") {
      return (
        <SourceFigure
          assetId={a.asset_id}
          figureId={a.content}
          caption={a.caption}
          altText={a.alt_text}
          variant="slide"
          cite={cite}
        />
      );
    }
    if (a.format === "tikz") {
      return (
        <TikzFigure
          assetId={a.asset_id}
          content={a.content}
          caption={a.caption}
          altText={a.alt_text}
          variant="slide"
          cite={cite}
        />
      );
    }
    return (
      <FigureFrame
        assetId={a.asset_id}
        format={a.format}
        caption={a.caption}
        altText={a.alt_text}
        variant="slide"
        cite={cite}
      >
        <VisualAssetBody
          asset={a}
          imageClassName="max-h-[24rem]"
          variant="slide"
        />
      </FigureFrame>
    );
  }

  // Tabelle, equazioni ed esempi: forme non numerate («Tabella.»,
  // «Equazione.», «Esempio.», A2) come nel PDF delle slide; il markup
  // resta quello delle slide (`MarkdownRenderer` conserva
  // `normalizeMathDelimiters`), solo lo span dell'etichetta è aggiunto;
  // didascalia e titolo passano da `InlineMath` (solo il math è reso).
  if (resolved.kind === "table") {
    return (
      <figure className="space-y-1">
        <MarkdownRenderer source={resolved.payload.markdown} />
        <figcaption className="text-xs italic text-muted-foreground">
          <span className="figure-label font-semibold not-italic">
            {t("courses.figures.table.labelUnnumbered")}
          </span>
          {resolved.payload.caption ? (
            <InlineMath text={` ${cite(resolved.payload.caption)}`} />
          ) : null}
        </figcaption>
      </figure>
    );
  }

  if (resolved.kind === "equation") {
    // Renderer unificato: formula nuda o blocco teorema (enunciato +
    // dimostrazione a passaggi), identico alle Dispense ma senza numero.
    return <EquationBlock equation={resolved.payload} cite={cite} />;
  }

  if (resolved.kind === "example") {
    const ex = resolved.payload;
    return (
      <div className="rounded-md border border-border bg-muted/20 p-3">
        <h5 className="mb-1 text-sm font-semibold">
          <span className="figure-label">
            {t("courses.figures.example.labelUnnumbered")}
          </span>
          {ex.title ? <InlineMath text={` ${cite(ex.title)}`} /> : null}
        </h5>
        <MarkdownRenderer source={ex.content} />
      </div>
    );
  }

  return null;
}

export default LessonSlidesView;
