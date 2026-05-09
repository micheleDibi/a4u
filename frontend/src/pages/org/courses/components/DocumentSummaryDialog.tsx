import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import katex from "katex";
import "katex/dist/katex.min.css";
import {
  coursesApi,
  type CourseDocumentDetailOut,
  type CourseDocumentOut,
} from "@/api/courses";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

interface Props {
  orgId: string;
  courseId: string;
  doc: CourseDocumentOut | null;
  open: boolean;
  onClose: () => void;
}

const SECTIONS = [
  "abstract",
  "structure",
  "concepts",
  "definitions",
  "examples",
  "formulas",
  "authors",
  "tags",
] as const;

type SectionId = (typeof SECTIONS)[number];

function LatexBlock({ source }: { source: string }) {
  // Detection LaTeX: cerchiamo marker espliciti (`\macro`, ^, _, {, },
  // $). Senza di essi KaTeX renderizzerebbe il testo come matematica
  // sequenziale — lettere in italic, spazi rimossi — il che produce
  // l'illeggibile soup tipo "Sec'èunoperandolongdouble". L'AI a volte
  // mette qui descrizioni in linguaggio naturale anziché formule;
  // detectiamole e rendiamole come prosa.
  const looksLikeLatex = useMemo(
    () => /\\[a-zA-Z]+|[\^_{}$]/.test(source),
    [source],
  );

  const html = useMemo(() => {
    if (!looksLikeLatex) return null;
    try {
      return katex.renderToString(source, {
        displayMode: true,
        throwOnError: true,
        strict: "ignore",
      });
    } catch {
      return null;
    }
  }, [source, looksLikeLatex]);

  if (!looksLikeLatex) {
    // Testo in linguaggio naturale: rendi come prosa leggibile.
    return (
      <p className="rounded bg-muted/40 p-3 text-sm leading-relaxed whitespace-pre-wrap">
        {source}
      </p>
    );
  }
  if (!html) {
    // Sembrava LaTeX ma KaTeX non l'ha parsato: mostra il sorgente raw.
    return (
      <pre className="overflow-x-auto rounded bg-muted/40 p-2 font-mono text-xs">
        {source}
      </pre>
    );
  }
  return (
    <div
      className="overflow-x-auto rounded bg-muted/40 p-3 text-sm"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export function DocumentSummaryDialog({
  orgId,
  courseId,
  doc,
  open,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const [section, setSection] = useState<SectionId>("abstract");

  // Reset alla prima sezione ogni volta che si apre per un altro documento.
  useEffect(() => {
    if (open) setSection("abstract");
  }, [open, doc?.id]);

  const enabled = open && !!doc && doc.summary_status === "ready";

  const query = useQuery({
    enabled,
    queryKey: ["courses", "documents", orgId, courseId, doc?.id, "summary"],
    queryFn: (): Promise<CourseDocumentDetailOut> =>
      coursesApi.documents.get(orgId, courseId, doc!.id, {
        includeSummary: true,
      }),
  });

  const detail = query.data;
  const summary = detail?.summary ?? null;

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>
            {t("courses.docs.summary.dialog.title", {
              filename: doc?.filename_original ?? "",
            })}
          </DialogTitle>
          <DialogDescription>
            {summary
              ? t("courses.docs.summary.dialog.detectedLanguage", {
                  lang: summary.detected_language,
                })
              : t("courses.docs.summary.dialog.loading")}
          </DialogDescription>
        </DialogHeader>

        {query.isLoading || !detail ? (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 className="size-6 animate-spin" />
          </div>
        ) : !summary ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {t("courses.docs.summary.dialog.notReady")}
          </p>
        ) : (
          <div className="mt-2 flex gap-4">
            {/* Menu laterale */}
            <nav className="flex w-48 shrink-0 flex-col gap-1">
              {SECTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSection(s)}
                  className={cn(
                    "rounded-md px-3 py-2 text-left text-sm transition-colors",
                    section === s
                      ? "bg-primary font-semibold text-primary-foreground shadow-sm"
                      : "text-foreground hover:bg-muted/60"
                  )}
                >
                  {t(`courses.docs.summary.dialog.sections.${s}`)}
                </button>
              ))}
            </nav>

            {/* Contenuto sezione */}
            <ScrollArea className="h-[60vh] flex-1 pr-3">
              {section === "abstract" && (
                <div>
                  <h4 className="mb-2 text-base font-semibold text-foreground">
                    {summary.source_title}
                  </h4>
                  <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">
                    {summary.abstract}
                  </p>
                </div>
              )}

              {section === "structure" &&
                (summary.structure_outline.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("courses.docs.summary.dialog.empty")}
                  </p>
                ) : (
                  <ol className="ms-5 list-decimal space-y-1.5 text-sm leading-relaxed text-foreground">
                    {summary.structure_outline.map((s, idx) => (
                      <li key={idx}>{s}</li>
                    ))}
                  </ol>
                ))}

              {section === "concepts" &&
                (summary.key_concepts.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("courses.docs.summary.dialog.empty")}
                  </p>
                ) : (
                  <div className="space-y-3">
                    {summary.key_concepts.map((kc, idx) => (
                      <div
                        key={idx}
                        className="rounded-md border border-border p-3"
                      >
                        <h5 className="text-sm font-semibold text-foreground">
                          {kc.name}
                        </h5>
                        <p className="mt-1 text-sm leading-relaxed text-foreground">
                          {kc.explanation}
                        </p>
                      </div>
                    ))}
                  </div>
                ))}

              {section === "definitions" &&
                (summary.definitions.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("courses.docs.summary.dialog.empty")}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {summary.definitions.map((d, idx) => (
                      <div
                        key={idx}
                        className="grid grid-cols-3 gap-3 text-sm leading-relaxed"
                      >
                        <dt className="font-semibold text-foreground">
                          {d.term}
                        </dt>
                        <dd className="col-span-2 text-foreground">
                          {d.definition}
                        </dd>
                      </div>
                    ))}
                  </div>
                ))}

              {section === "examples" &&
                (summary.examples_or_cases.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("courses.docs.summary.dialog.empty")}
                  </p>
                ) : (
                  <div className="space-y-3">
                    {summary.examples_or_cases.map((ex, idx) => (
                      <div
                        key={idx}
                        className="rounded-md border border-border p-3"
                      >
                        <h5 className="text-sm font-semibold text-foreground">
                          {ex.title}
                        </h5>
                        <p className="mt-1 text-sm leading-relaxed text-foreground">
                          {ex.synthesis}
                        </p>
                      </div>
                    ))}
                  </div>
                ))}

              {section === "formulas" &&
                (summary.formulas_or_rules.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("courses.docs.summary.dialog.empty")}
                  </p>
                ) : (
                  <div className="space-y-3">
                    {summary.formulas_or_rules.map((f, idx) => (
                      <div
                        key={idx}
                        className="rounded-md border border-border p-3"
                      >
                        <h5 className="text-sm font-semibold text-foreground">
                          {f.label}
                        </h5>
                        <div className="mt-2">
                          <LatexBlock source={f.latex_or_text} />
                        </div>
                        <p className="mt-2 text-sm leading-relaxed text-foreground">
                          {f.meaning}
                        </p>
                      </div>
                    ))}
                  </div>
                ))}

              {section === "authors" &&
                (summary.authors_and_references.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("courses.docs.summary.dialog.empty")}
                  </p>
                ) : (
                  <ul className="space-y-1.5 text-sm leading-relaxed text-foreground">
                    {summary.authors_and_references.map((a, idx) => (
                      <li key={idx} className="flex items-center gap-2">
                        <Badge
                          variant={
                            a.type === "author" ? "default" : "secondary"
                          }
                        >
                          {t(`courses.docs.summary.dialog.authors.${a.type}`)}
                        </Badge>
                        <span>{a.value}</span>
                      </li>
                    ))}
                  </ul>
                ))}

              {section === "tags" &&
                (summary.didactic_relevance_tags.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    {t("courses.docs.summary.dialog.empty")}
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {summary.didactic_relevance_tags.map((tag, idx) => (
                      <Badge key={idx} variant="secondary">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                ))}
            </ScrollArea>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
