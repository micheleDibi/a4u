import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FileText,
  Loader2,
  Lock,
  Microscope,
  Quote,
  Sparkles,
  Tags,
  Unlock,
} from "lucide-react";

import type { PaperAISummaryOut, PaperOut } from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

export type SummaryState =
  | { status: "loading" }
  | { status: "success"; data: PaperAISummaryOut }
  | { status: "error"; error: string };

interface Props {
  paper: PaperOut;
  selected: boolean;
  summary: SummaryState | undefined;
  summaryExpanded: boolean;
  onToggleSelect: (paperId: string) => void;
  onToggleSummary: (paper: PaperOut) => void;
}

const ABSTRACT_PREVIEW_CHARS = 320;

export function PaperResultCard({
  paper,
  selected,
  summary,
  summaryExpanded,
  onToggleSelect,
  onToggleSummary,
}: Props) {
  const { t } = useTranslation();
  const [abstractExpanded, setAbstractExpanded] = useState(false);

  const abstractTooLong =
    (paper.abstract?.length ?? 0) > ABSTRACT_PREVIEW_CHARS;
  const visibleAbstract = abstractExpanded
    ? paper.abstract
    : paper.abstract?.slice(0, ABSTRACT_PREVIEW_CHARS) +
      (abstractTooLong ? "…" : "");

  const visibleAuthors = paper.authors.slice(0, 5);
  const remainingAuthors = paper.authors.length - visibleAuthors.length;

  const relevancePct =
    paper.relevance_score !== null
      ? Math.round(paper.relevance_score * 100)
      : null;
  const relevanceColor =
    relevancePct === null
      ? "bg-muted"
      : relevancePct >= 70
        ? "bg-emerald-500"
        : relevancePct >= 40
          ? "bg-amber-500"
          : "bg-muted-foreground/50";

  const allKeywords = [...paper.keywords, ...paper.subjects].slice(0, 10);

  // Stato pulsante riassunto: 4 modalita' (idle / loading / collapsed-success / expanded-success).
  const summaryButtonContent = (() => {
    if (summary?.status === "loading") {
      return (
        <>
          <Loader2 className="size-4 animate-spin" />
          {t("courses.papers.summary.generating")}
        </>
      );
    }
    if (summary?.status === "success") {
      return summaryExpanded ? (
        <>
          <ChevronUp className="size-4" />
          {t("courses.papers.summary.hide")}
        </>
      ) : (
        <>
          <ChevronDown className="size-4" />
          {t("courses.papers.summary.show")}
        </>
      );
    }
    return (
      <>
        <Sparkles className="size-4" />
        {t("courses.papers.card.aiSummary")}
      </>
    );
  })();

  return (
    <div
      className={cn(
        "rounded-lg border p-4 shadow-sm transition-colors",
        selected
          ? "border-primary bg-primary/5"
          : "border-border bg-card hover:bg-muted/30",
      )}
    >
      {/* Riga 1: checkbox + badges */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Checkbox
          checked={selected}
          onCheckedChange={() => onToggleSelect(paper.id)}
          aria-label={t("courses.papers.card.selectPaper")}
          className="size-5"
        />
        {paper.is_oa ? (
          <Badge variant="success" className="gap-1">
            <Unlock className="size-3" />
            {t("courses.papers.card.oaBadge")}
          </Badge>
        ) : (
          <Badge variant="warning" className="gap-1">
            <Lock className="size-3" />
            {t("courses.papers.card.nonOaBadge")}
          </Badge>
        )}
        {relevancePct !== null && (
          <div
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-0.5 text-xs"
            title={t("courses.papers.card.relevanceTooltip", {
              pct: relevancePct,
            })}
          >
            <span className="text-muted-foreground">
              {t("courses.papers.card.relevance")}:
            </span>
            <div className="h-2 w-16 overflow-hidden rounded-full bg-muted">
              <div
                className={cn("h-full transition-all", relevanceColor)}
                style={{ width: `${relevancePct}%` }}
              />
            </div>
            <span className="font-mono text-[10px] tabular-nums">
              {relevancePct}%
            </span>
          </div>
        )}
        <Badge variant="secondary" className="gap-1">
          <Quote className="size-3" />
          {t("courses.papers.card.citations", { count: paper.citations })}
        </Badge>
        {paper.work_type && (
          <Badge variant="outline">
            {t(`courses.papers.types.${paper.work_type}`)}
          </Badge>
        )}
      </div>

      {/* Riga 2: titolo (link DOI) */}
      <h3 className="mb-1 text-base font-semibold leading-tight">
        {paper.doi_url ? (
          <a
            href={paper.doi_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-foreground hover:text-primary hover:underline"
          >
            {paper.title}
          </a>
        ) : (
          <span>{paper.title}</span>
        )}
      </h3>

      {/* Riga 3: autori + anno + journal */}
      <div className="mb-2 text-sm text-muted-foreground">
        {visibleAuthors.length > 0 && (
          <span>{visibleAuthors.join(", ")}</span>
        )}
        {remainingAuthors > 0 && (
          <span className="ms-1 italic">
            {t("courses.papers.card.plusMoreAuthors", {
              count: remainingAuthors,
            })}
          </span>
        )}
        {paper.year && (
          <>
            {visibleAuthors.length > 0 ? " · " : ""}
            <span>{paper.year}</span>
          </>
        )}
        {paper.journal && (
          <>
            {" · "}
            <span className="italic">{paper.journal}</span>
          </>
        )}
      </div>

      {/* Riga 4: abstract collapsible */}
      {paper.abstract && (
        <div className="mb-2 text-sm text-foreground/85">
          <p className="whitespace-pre-wrap">{visibleAbstract}</p>
          {abstractTooLong && (
            <button
              type="button"
              onClick={() => setAbstractExpanded((v) => !v)}
              className="mt-1 text-xs font-medium text-primary hover:underline"
            >
              {abstractExpanded
                ? t("courses.papers.card.abstractShowLess")
                : t("courses.papers.card.abstractShowMore")}
            </button>
          )}
        </div>
      )}

      {/* Riga 5: keywords + subjects (uniti, max 10) */}
      {allKeywords.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1">
          {allKeywords.map((k, i) => (
            <span
              key={`${i}-${k}`}
              className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              {k}
            </span>
          ))}
        </div>
      )}

      {/* Riga 6: TL;DR se disponibile (popolato on-demand) */}
      {paper.tldr && (
        <div className="mb-3 rounded-md border border-border bg-muted/40 p-2 text-xs">
          <span className="font-semibold text-primary">TL;DR: </span>
          <span className="text-foreground/85">{paper.tldr}</span>
        </div>
      )}

      {/* Footer: bottoni */}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {paper.doi_url && (
          <Button asChild variant="ghost" size="sm">
            <a
              href={paper.doi_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ExternalLink className="size-4" />
              {t("courses.papers.card.openDoi")}
            </a>
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onToggleSummary(paper)}
          disabled={summary?.status === "loading"}
        >
          {summaryButtonContent}
        </Button>
      </div>

      {/* Sezione riassunto AI inline (sotto i bottoni, espandibile) */}
      {summary?.status === "error" && (
        <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {t("courses.papers.summary.error")}: {summary.error}
        </div>
      )}
      {summary?.status === "success" && summaryExpanded && (
        <PaperSummaryBlock data={summary.data} />
      )}
    </div>
  );
}

function PaperSummaryBlock({ data }: { data: PaperAISummaryOut }) {
  const { t } = useTranslation();
  return (
    <div className="mt-3 space-y-4 rounded-md border border-primary/30 bg-primary/5 p-4">
      <SummarySection
        icon={<FileText className="size-4 text-primary" />}
        title={t("courses.papers.summary.shortSummary")}
        content={data.short_summary}
      />
      <SummarySection
        icon={<Microscope className="size-4 text-primary" />}
        title={t("courses.papers.summary.technicalSummary")}
        content={data.technical_summary}
      />
      <div>
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <Tags className="size-4 text-primary" />
          {t("courses.papers.summary.keywords")}
        </h4>
        <div className="flex flex-wrap gap-1.5">
          {data.keywords.map((k, i) => (
            <span
              key={`${i}-${k}`}
              className="rounded-md bg-secondary px-2 py-1 text-xs"
            >
              {k}
            </span>
          ))}
        </div>
      </div>
      <SummarySection
        icon={<AlertTriangle className="size-4 text-amber-600" />}
        title={t("courses.papers.summary.limitations")}
        content={data.study_limitations}
      />
    </div>
  );
}

function SummarySection({
  icon,
  title,
  content,
}: {
  icon: React.ReactNode;
  title: string;
  content: string;
}) {
  return (
    <div>
      <h4 className="mb-1.5 flex items-center gap-2 text-sm font-semibold">
        {icon}
        {title}
      </h4>
      <p className="whitespace-pre-wrap text-sm text-foreground/90">
        {content}
      </p>
    </div>
  );
}
