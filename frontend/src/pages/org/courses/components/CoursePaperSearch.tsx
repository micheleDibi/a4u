import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ChevronsDownUp,
  ChevronsUpDown,
  Download,
  FilterX,
  Loader2,
  Microscope,
  Search,
  SlidersHorizontal,
} from "lucide-react";

import {
  coursesApi,
  type PaperOut,
  type PaperSearchFilters,
  type PaperSearchResultsOut,
  type PaperType,
} from "@/api/courses";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { extractApiError } from "@/lib/errors";

import { PaperResultCard, type SummaryState } from "./PaperResultCard";

interface Props {
  orgId: string;
  courseId: string;
}

const TYPE_VALUES: readonly (PaperType | "any")[] = [
  "any",
  "article",
  "preprint",
  "review",
  "other",
];

/**
 * Sezione "Paper Scientifici" nella tab Documenti.
 * - Pannello filtri (query + 7 filtri opzionali)
 * - Lista risultati con paginazione cursor "Carica altri 20"
 * - Multi-select + bottone "Importa selezionati"
 * - Bottone "Riassunto AI" per ogni paper (apre dialog)
 */
export function CoursePaperSearch({ orgId, courseId }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  // Form di ricerca (state controllato)
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<PaperSearchFilters>({
    is_oa: null,
    work_type: null,
  });
  const [showFilters, setShowFilters] = useState(true);

  // Stato lista risultati (accumulato con "Carica altri")
  const [results, setResults] = useState<PaperOut[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [lastQuery, setLastQuery] = useState<string>("");

  // Multi-select
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Collasso della lista risultati: dopo un import la lista (potenzialmente
  // lunga) viene compressa cosi' non si deve risalire a mano in cima.
  const [resultsCollapsed, setResultsCollapsed] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  const scrollToTop = () => {
    cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // Riassunti AI cached per la sessione (non persistiti). La map
  // sopravvive a "Carica altri" e al toggle expanded, cosi' rigenerare
  // un riassunto gia' visto e' gratis. Si perde solo a unmount o a una
  // nuova ricerca primaria.
  const [summariesById, setSummariesById] = useState<
    Record<string, SummaryState>
  >({});
  const [expandedSummaryIds, setExpandedSummaryIds] = useState<Set<string>>(
    new Set(),
  );

  const searchMut = useMutation({
    mutationFn: (payload: {
      query: string;
      filters: PaperSearchFilters;
      cursor: string | null;
    }) =>
      coursesApi.papers.search(orgId, courseId, {
        query: payload.query,
        filters: payload.filters,
        cursor: payload.cursor,
        per_page: 20,
      }),
    onSuccess: (data: PaperSearchResultsOut, vars) => {
      if (vars.cursor === null) {
        // Prima pagina: reset
        setResults(data.results);
        setSelectedIds(new Set());
        setLastQuery(vars.query);
        setResultsCollapsed(false);
      } else {
        // Carica altri: append, dedup per id
        setResults((prev) => {
          const seen = new Set(prev.map((p) => p.id));
          return [...prev, ...data.results.filter((p) => !seen.has(p.id))];
        });
      }
      setNextCursor(data.next_cursor);
      setTotalCount(data.total_count);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const importMut = useMutation({
    mutationFn: (papers: PaperOut[]) =>
      coursesApi.papers.importMany(orgId, courseId, papers),
    onSuccess: (data) => {
      toast.success(
        t("courses.papers.import.successDetail", {
          total: data.imported.length,
          pdf: data.pdf_count,
          metadata: data.metadata_count,
        }),
      );
      setSelectedIds(new Set());
      // Comprime la lista (spesso lunga) e riporta la vista in cima alla
      // sezione, cosi' non si deve scrollare a mano fino in testa.
      setResultsCollapsed(true);
      scrollToTop();
      // Invalidate course detail per ricaricare la lista documenti.
      qc.invalidateQueries({
        queryKey: ["courses", "detail", orgId, courseId],
      });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const onSearch = () => {
    if (!query.trim() && !hasAnyFilter(filters)) {
      toast.warning(t("courses.papers.results.queryRequired") as string);
      return;
    }
    searchMut.mutate({ query: query.trim(), filters, cursor: null });
  };

  const onLoadMore = () => {
    if (!nextCursor) return;
    searchMut.mutate({ query: lastQuery, filters, cursor: nextCursor });
  };

  const onResetFilters = () => {
    setFilters({ is_oa: null, work_type: null });
  };

  const onToggleSelect = (paperId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(paperId)) next.delete(paperId);
      else next.add(paperId);
      return next;
    });
  };

  const onImportSelected = () => {
    const selectedPapers = results.filter((p) => selectedIds.has(p.id));
    if (selectedPapers.length === 0) return;
    importMut.mutate(selectedPapers);
  };

  const onToggleSummary = async (paper: PaperOut) => {
    const existing = summariesById[paper.id];
    // Gia' generato: toggla solo la visibilita' (niente nuova chiamata).
    if (existing?.status === "success") {
      setExpandedSummaryIds((prev) => {
        const next = new Set(prev);
        if (next.has(paper.id)) next.delete(paper.id);
        else next.add(paper.id);
        return next;
      });
      return;
    }
    // Loading in corso: ignora (bottone gia' disabilitato lato card).
    if (existing?.status === "loading") return;

    // Idle o errore precedente: avvia (o ritenta) generazione.
    setSummariesById((prev) => ({
      ...prev,
      [paper.id]: { status: "loading" },
    }));
    setExpandedSummaryIds((prev) => {
      const next = new Set(prev);
      next.add(paper.id);
      return next;
    });
    try {
      const data = await coursesApi.papers.aiSummary(orgId, courseId, paper);
      setSummariesById((prev) => ({
        ...prev,
        [paper.id]: { status: "success", data },
      }));
    } catch (err) {
      const message = extractApiError(err).message;
      setSummariesById((prev) => ({
        ...prev,
        [paper.id]: { status: "error", error: message },
      }));
      toast.error(message);
    }
  };

  const selectedCount = selectedIds.size;
  const isInitialLoading =
    searchMut.isPending && results.length === 0;
  const isLoadingMore =
    searchMut.isPending && results.length > 0;

  return (
    <Card ref={cardRef} className="scroll-mt-4">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Microscope className="size-5 text-primary" />
            {t("courses.papers.section.title")}
          </CardTitle>
          <CardDescription>
            {t("courses.papers.section.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Form filtri */}
          <div className="space-y-3 rounded-md border border-border bg-muted/30 p-3">
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1.5">
                <Label htmlFor="paper-query">
                  {t("courses.papers.filters.query")}
                </Label>
                <Input
                  id="paper-query"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onSearch();
                  }}
                  placeholder={
                    t("courses.papers.filters.queryPlaceholder") as string
                  }
                />
              </div>
              <Button
                variant="outline"
                size="icon"
                onClick={() => setShowFilters((v) => !v)}
                title={t("courses.papers.filters.toggle") as string}
                aria-label={t("courses.papers.filters.toggle") as string}
              >
                <SlidersHorizontal className="size-4" />
              </Button>
              <Button onClick={onSearch} disabled={searchMut.isPending}>
                {searchMut.isPending && results.length === 0 ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Search className="size-4" />
                )}
                {t("courses.papers.filters.search")}
              </Button>
            </div>

            {showFilters && (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                <div className="space-y-1.5">
                  <Label htmlFor="filter-year-from" className="text-xs">
                    {t("courses.papers.filters.yearFrom")}
                  </Label>
                  <Input
                    id="filter-year-from"
                    type="number"
                    inputMode="numeric"
                    min={1900}
                    max={2100}
                    value={filters.year_from ?? ""}
                    onChange={(e) =>
                      setFilters({
                        ...filters,
                        year_from: e.target.value
                          ? Number(e.target.value)
                          : null,
                      })
                    }
                    placeholder="2020"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="filter-year-to" className="text-xs">
                    {t("courses.papers.filters.yearTo")}
                  </Label>
                  <Input
                    id="filter-year-to"
                    type="number"
                    inputMode="numeric"
                    min={1900}
                    max={2100}
                    value={filters.year_to ?? ""}
                    onChange={(e) =>
                      setFilters({
                        ...filters,
                        year_to: e.target.value
                          ? Number(e.target.value)
                          : null,
                      })
                    }
                    placeholder="2025"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="filter-min-citations" className="text-xs">
                    {t("courses.papers.filters.minCitations")}
                  </Label>
                  <Input
                    id="filter-min-citations"
                    type="number"
                    inputMode="numeric"
                    min={0}
                    value={filters.min_citations ?? ""}
                    onChange={(e) =>
                      setFilters({
                        ...filters,
                        min_citations: e.target.value
                          ? Number(e.target.value)
                          : null,
                      })
                    }
                    placeholder="10"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="filter-author" className="text-xs">
                    {t("courses.papers.filters.author")}
                  </Label>
                  <Input
                    id="filter-author"
                    value={filters.author_name ?? ""}
                    onChange={(e) =>
                      setFilters({
                        ...filters,
                        author_name: e.target.value || null,
                      })
                    }
                    placeholder="Hinton"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="filter-venue" className="text-xs">
                    {t("courses.papers.filters.venue")}
                  </Label>
                  <Input
                    id="filter-venue"
                    value={filters.venue_name ?? ""}
                    onChange={(e) =>
                      setFilters({
                        ...filters,
                        venue_name: e.target.value || null,
                      })
                    }
                    placeholder="Nature"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("courses.papers.filters.type")}
                  </Label>
                  <Select
                    value={filters.work_type ?? "any"}
                    onValueChange={(v) =>
                      setFilters({
                        ...filters,
                        work_type: v === "any" ? null : (v as PaperType),
                      })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TYPE_VALUES.map((tp) => (
                        <SelectItem key={tp} value={tp}>
                          {tp === "any"
                            ? t("courses.papers.filters.typeAny")
                            : t(`courses.papers.types.${tp}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="col-span-2 flex items-center gap-2 md:col-span-3">
                  <Switch
                    id="filter-is-oa"
                    checked={filters.is_oa === true}
                    onCheckedChange={(v) =>
                      setFilters({ ...filters, is_oa: v ? true : null })
                    }
                  />
                  <Label
                    htmlFor="filter-is-oa"
                    className="cursor-pointer text-sm"
                  >
                    {t("courses.papers.filters.isOa")}
                  </Label>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ms-auto"
                    onClick={onResetFilters}
                  >
                    <FilterX className="size-4" />
                    {t("courses.papers.filters.resetFilters")}
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* Stato: vuoto / loading / errore / risultati */}
          {!searchMut.isSuccess && !searchMut.isPending && (
            <div className="rounded-md border border-dashed border-border bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
              {t("courses.papers.results.emptyInitial")}
            </div>
          )}

          {isInitialLoading && <SearchLoadingSkeleton />}

          {searchMut.isSuccess && results.length === 0 && (
            <div className="rounded-md border border-dashed border-border bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
              {t("courses.papers.results.empty")}
            </div>
          )}

          {results.length > 0 && (
            <>
              {/* Header risultati: counter + toggle comprimi + sticky import button */}
              <div className="sticky top-2 z-10 -mx-1 flex items-center justify-between gap-2 rounded-md border border-border bg-card/95 px-3 py-2 shadow-sm backdrop-blur">
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-muted-foreground"
                    onClick={() => {
                      setResultsCollapsed((v) => !v);
                      if (!resultsCollapsed) scrollToTop();
                    }}
                    title={
                      (resultsCollapsed
                        ? t("courses.papers.results.expand")
                        : t("courses.papers.results.collapse")) as string
                    }
                  >
                    {resultsCollapsed ? (
                      <ChevronsUpDown className="size-4" />
                    ) : (
                      <ChevronsDownUp className="size-4" />
                    )}
                    {resultsCollapsed
                      ? t("courses.papers.results.expand")
                      : t("courses.papers.results.collapse")}
                  </Button>
                  <span className="text-sm text-muted-foreground">
                    {t("courses.papers.results.countWithTotal", {
                      shown: results.length,
                      total: totalCount ?? results.length,
                    })}
                  </span>
                </div>
                {selectedCount > 0 && (
                  <Button
                    onClick={onImportSelected}
                    disabled={importMut.isPending}
                    size="sm"
                  >
                    {importMut.isPending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Download className="size-4" />
                    )}
                    {importMut.isPending
                      ? t("courses.papers.import.importing")
                      : t("courses.papers.import.button", {
                          count: selectedCount,
                        })}
                  </Button>
                )}
              </div>

              {resultsCollapsed ? (
                <div className="rounded-md border border-dashed border-border bg-muted/20 px-4 py-6 text-center text-sm text-muted-foreground">
                  {t("courses.papers.results.collapsedHint")}
                </div>
              ) : (
                <>
                  {/* Lista card */}
                  <div className="space-y-3">
                    {results.map((p) => (
                      <PaperResultCard
                        key={p.id}
                        paper={p}
                        selected={selectedIds.has(p.id)}
                        summary={summariesById[p.id]}
                        summaryExpanded={expandedSummaryIds.has(p.id)}
                        onToggleSelect={onToggleSelect}
                        onToggleSummary={onToggleSummary}
                      />
                    ))}
                  </div>

                  {/* Footer paginazione */}
                  {nextCursor && (
                    <div className="flex justify-center pt-2">
                      <Button
                        variant="outline"
                        onClick={onLoadMore}
                        disabled={isLoadingMore}
                      >
                        {isLoadingMore ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : null}
                        {t("courses.papers.results.loadMore")}
                      </Button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
      </CardContent>
    </Card>
  );
}

function hasAnyFilter(f: PaperSearchFilters): boolean {
  return (
    f.year_from !== null && f.year_from !== undefined ||
    f.year_to !== null && f.year_to !== undefined ||
    f.is_oa === true ||
    (f.min_citations !== null && f.min_citations !== undefined &&
      f.min_citations > 0) ||
    !!f.author_name ||
    !!f.venue_name ||
    !!f.work_type
  );
}

function SearchLoadingSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="animate-pulse rounded-lg border border-border bg-card p-4"
        >
          <div className="mb-2 flex gap-2">
            <div className="h-5 w-5 rounded bg-muted" />
            <div className="h-5 w-20 rounded bg-muted" />
            <div className="h-5 w-24 rounded bg-muted" />
            <div className="h-5 w-16 rounded bg-muted" />
          </div>
          <div className="mb-2 h-5 w-3/4 rounded bg-muted" />
          <div className="mb-1 h-3 w-1/2 rounded bg-muted" />
          <div className="mb-2 h-3 w-2/3 rounded bg-muted" />
          <div className="space-y-1">
            <div className="h-3 w-full rounded bg-muted" />
            <div className="h-3 w-full rounded bg-muted" />
            <div className="h-3 w-4/5 rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
