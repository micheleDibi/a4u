import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  ImageIcon,
  Loader2,
  Plus,
  Save,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { extractApiError } from "@/lib/errors";
import { mediaUrl } from "@/lib/media";

import {
  coursesApi,
  type LessonContentEquation,
  type LessonContentExample,
  type LessonContentRaw,
  type LessonContentReference,
  type LessonContentSection,
  type LessonContentTable,
  type LessonContentUpdateInput,
  type LessonContentVisualAsset,
} from "@/api/courses";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { LatexEditor } from "@/components/shared/LatexEditor";
import { MermaidEditor } from "@/components/shared/MermaidEditor";
import { RichTextEditor } from "@/components/shared/RichTextEditor";
import { TableEditor } from "@/components/shared/TableEditor";

// Tipi di asset equazione (mirror dello schema BE). Etichette localizzate
// in `courses.theorem.kind.*`.
const EQUATION_KINDS = [
  "definition",
  "formula",
  "identity",
  "theorem",
  "proposition",
  "lemma",
  "corollary",
] as const;

interface Props {
  open: boolean;
  isPending: boolean;
  lessonLabel: string;
  initial: LessonContentRaw;
  /**
   * Org + corso correnti — necessari per gli endpoint
   * `/lesson-assets/{upload,convert-to-mermaid}` quando l'utente
   * carica un'immagine come asset visivo o la digitalizza in Mermaid.
   */
  orgId: string;
  courseId: string;
  onClose: () => void;
  onSubmit: (payload: LessonContentUpdateInput) => void;
}

/**
 * Editor unificato a "foglio bianco": un solo pannello scrollabile in
 * cui l'utente può modificare tutto — testo della lezione + asset
 * visivi + tabelle + equazioni + esempi. Niente tab.
 *
 * Niente sintassi grezza visibile: il testo è in rich-text, le tabelle
 * sono griglie editabili, le formule hanno preview KaTeX e palette
 * simboli, i diagrammi Mermaid hanno preview live e template.
 */
export function LessonContentEditDialog({
  open,
  isPending,
  lessonLabel,
  initial,
  orgId,
  courseId,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const [introduction, setIntroduction] = useState(initial.introduction);
  const [summary, setSummary] = useState(initial.summary);
  const [keyTakeaways, setKeyTakeaways] = useState<string[]>(
    initial.key_takeaways,
  );
  const [sections, setSections] = useState<LessonContentSection[]>(
    initial.sections,
  );
  const [visualAssets, setVisualAssets] = useState<LessonContentVisualAsset[]>(
    initial.visual_assets,
  );
  const [tables, setTables] = useState<LessonContentTable[]>(initial.tables);
  const [equations, setEquations] = useState<LessonContentEquation[]>(
    initial.equations,
  );
  const [examples, setExamples] = useState<LessonContentExample[]>(
    initial.examples,
  );
  const [references, setReferences] = useState<LessonContentReference[]>(
    initial.references,
  );

  // NB: stato inizializzato lazy nei useState sopra (UNA volta al mount).
  // Niente reset su re-render del parent: `initial` arriva come inline-object
  // dal parent e cambia riferimento ad ogni render (es. polling TanStack
  // Query), quindi un useEffect con `[open, initial]` resetterebbe lo state
  // mentre l'utente sta modificando — facendo sparire i campi appena aggiunti
  // (Aggiungi sezione/asset/tabella/equazione/esempio/riferimento).

  // SectionGroup controlled state — necessario per "Evidenzia dove usato",
  // che deve poter espandere programmaticamente il gruppo che contiene la
  // prima occorrenza del token (es. equazione → gruppo "Equazioni").
  type GroupKey =
    | "text"
    | "visualAssets"
    | "tables"
    | "equations"
    | "examples"
    | "references";
  const [openGroups, setOpenGroups] = useState<Record<GroupKey, boolean>>({
    text: true,
    visualAssets: false,
    tables: false,
    equations: false,
    examples: false,
    references: false,
  });
  const toggleGroup = (key: GroupKey) =>
    setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));

  // Refs ai contenitori dei campi scansionabili (intro/summary/sections/
  // examples/equations), keyed da stringa stabile (es. "intro", "section-3",
  // "equation-1-explanation"). Servono per scrollIntoView + flash visivo.
  const fieldRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());
  const setFieldRef = (key: string) => (el: HTMLDivElement | null) => {
    if (el) fieldRefs.current.set(key, el);
    else fieldRefs.current.delete(key);
  };
  const [highlightKey, setHighlightKey] = useState<string | null>(null);
  const highlightTimeoutRef = useRef<number | null>(null);
  // Riferimento all'elemento `<code>` che TipTap usa per renderizzare il
  // token `[KIND:id]` (vedi `protectTokens` in `RichTextEditor.tsx`).
  // Mantenuto per ripulire l'inline-style di flash quando il timer scade
  // o quando si avvia un nuovo flash su un token diverso.
  const flashedTokenElRef = useRef<HTMLElement | null>(null);

  const applyTokenFlash = (el: HTMLElement) => {
    el.style.backgroundColor = "rgb(251 191 36 / 0.45)"; // amber-400 @45%
    el.style.outline = "2px solid rgb(251 191 36)"; // amber-400
    el.style.outlineOffset = "1px";
    el.style.borderRadius = "3px";
    el.style.transition = "background-color 200ms, outline 200ms";
  };

  const clearTokenFlash = (el: HTMLElement) => {
    el.style.backgroundColor = "";
    el.style.outline = "";
    el.style.outlineOffset = "";
    el.style.borderRadius = "";
    el.style.transition = "";
  };

  const findFirstOccurrence = (token: string): string | null => {
    if (introduction.includes(token)) return "intro";
    for (let i = 0; i < sections.length; i++) {
      if (sections[i].content.includes(token)) return `section-${i}`;
    }
    if (summary.includes(token)) return "summary";
    for (let i = 0; i < examples.length; i++) {
      if (examples[i].content.includes(token)) return `example-${i}-content`;
    }
    for (let i = 0; i < equations.length; i++) {
      if (equations[i].explanation.includes(token)) {
        return `equation-${i}-explanation`;
      }
    }
    return null;
  };

  const groupForFieldKey = (key: string): GroupKey | null => {
    if (key === "intro" || key === "summary" || key.startsWith("section-")) {
      return "text";
    }
    if (key.startsWith("example-")) return "examples";
    if (key.startsWith("equation-")) return "equations";
    return null;
  };

  const triggerHighlight = (key: string, token: string | null = null) => {
    const groupKey = groupForFieldKey(key);
    if (groupKey) {
      setOpenGroups((prev) =>
        prev[groupKey] ? prev : { ...prev, [groupKey]: true },
      );
    }
    if (highlightTimeoutRef.current !== null) {
      window.clearTimeout(highlightTimeoutRef.current);
    }
    // Se c'è un flash precedente ancora attivo su un altro token, ripuliscilo.
    if (flashedTokenElRef.current) {
      clearTokenFlash(flashedTokenElRef.current);
      flashedTokenElRef.current = null;
    }
    setHighlightKey(key);
    window.requestAnimationFrame(() => {
      const fieldEl = fieldRefs.current.get(key);
      if (!fieldEl) return;
      // Scroll iniziale al container come fallback / contesto.
      fieldEl.scrollIntoView({ behavior: "smooth", block: "center" });
      if (!token) return;
      // TipTap rende ogni `[KIND:id]` come `<code>{token}</code>` (vedi
      // `protectTokens` in `RichTextEditor.tsx`), quindi basta cercare il
      // primo `<code>` con `textContent === token`.
      const codes = fieldEl.querySelectorAll("code");
      for (const c of Array.from(codes)) {
        if (c.textContent === token) {
          applyTokenFlash(c as HTMLElement);
          flashedTokenElRef.current = c as HTMLElement;
          // Re-scroll per centrare il token specifico, non il contenitore.
          c.scrollIntoView({ behavior: "smooth", block: "center" });
          break;
        }
      }
    });
    highlightTimeoutRef.current = window.setTimeout(() => {
      setHighlightKey(null);
      if (flashedTokenElRef.current) {
        clearTokenFlash(flashedTokenElRef.current);
        flashedTokenElRef.current = null;
      }
      highlightTimeoutRef.current = null;
    }, 2200);
  };

  const highlightUsage = (kind: RefKind, id: string) => {
    const trimmed = id.trim();
    if (!trimmed) {
      toast.info(t("courses.lessonsContent.editor.refIdMissing"));
      return;
    }
    const token = `[${kind}:${trimmed}]`;
    const key = findFirstOccurrence(token);
    if (!key) {
      toast.info(
        t("courses.lessonsContent.editor.refNoOccurrences", { token }),
      );
      return;
    }
    triggerHighlight(key, token);
  };

  // Riferimenti bibliografici: non hanno un id-token tipo [FIG:n], ma una
  // citation di testo libero. Best-effort substring match (case-insensitive)
  // della citation nei campi scansionabili.
  const highlightReferenceUsage = (citation: string) => {
    const needle = citation.trim();
    if (!needle) {
      toast.info(t("courses.lessonsContent.editor.refIdMissing"));
      return;
    }
    const lower = needle.toLowerCase();
    const has = (s: string) => s.toLowerCase().includes(lower);
    let key: string | null = null;
    if (has(introduction)) key = "intro";
    else {
      for (let i = 0; i < sections.length; i++) {
        if (has(sections[i].content)) {
          key = `section-${i}`;
          break;
        }
      }
      if (!key && has(summary)) key = "summary";
      if (!key) {
        for (let i = 0; i < examples.length; i++) {
          if (has(examples[i].content)) {
            key = `example-${i}-content`;
            break;
          }
        }
      }
      if (!key) {
        for (let i = 0; i < equations.length; i++) {
          if (has(equations[i].explanation)) {
            key = `equation-${i}-explanation`;
            break;
          }
        }
      }
    }
    if (!key) {
      toast.info(
        t("courses.lessonsContent.editor.refNoOccurrences", { token: needle }),
      );
      return;
    }
    triggerHighlight(key);
  };

  const fieldHighlightClass = (key: string) =>
    cn(
      "transition-all duration-300",
      highlightKey === key &&
        "ring-2 ring-amber-400 ring-offset-2 ring-offset-background rounded-md",
    );

  const handleSubmit = () => {
    const payload: LessonContentUpdateInput = {
      introduction,
      summary,
      key_takeaways: keyTakeaways.filter((kt) => kt.trim().length > 0),
      sections,
      visual_assets: visualAssets,
      tables,
      equations,
      examples,
      references,
    };
    onSubmit(payload);
  };

  /**
   * Quando l'utente rinomina un id (es. `asset_id`) in una card, propaga
   * il rename a tutti i riferimenti `[KIND:oldId]` presenti nei campi
   * markdown del corpo (introduction, sections, summary, examples,
   * equations.explanation). Salta i casi degeneri (id vuoto o invariato).
   */
  const patchRefs = (kind: RefKind, oldId: string, newId: string) => {
    const o = oldId.trim();
    const n = newId.trim();
    if (!o || !n || o === n) return;
    const escaped = o.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(`\\[${kind}:${escaped}\\]`, "g");
    const newToken = `[${kind}:${n}]`;
    setIntroduction((s) => s.replace(re, newToken));
    setSummary((s) => s.replace(re, newToken));
    setSections((prev) =>
      prev.map((sec) => ({
        ...sec,
        content: sec.content.replace(re, newToken),
      })),
    );
    setExamples((prev) =>
      prev.map((ex) => ({
        ...ex,
        content: ex.content.replace(re, newToken),
      })),
    );
    setEquations((prev) =>
      prev.map((eq) => ({
        ...eq,
        explanation: eq.explanation.replace(re, newToken),
      })),
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent className="max-w-5xl max-h-[92vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {t("courses.lessonsContent.dialog.edit.title", {
              lesson: lessonLabel,
            })}
          </DialogTitle>
          <DialogDescription>
            {t("courses.lessonsContent.dialog.edit.description")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-2">
          {/* === Testo della lezione === */}
          <SectionGroup
            title={t("courses.lessonsContent.editor.text")}
            open={openGroups.text}
            onToggle={() => toggleGroup("text")}
          >
            <div
              ref={setFieldRef("intro")}
              className={cn("space-y-1.5", fieldHighlightClass("intro"))}
            >
              <Label>{t("courses.lessonsContent.render.intro")}</Label>
              <RichTextEditor
                value={introduction}
                onChange={setIntroduction}
                disabled={isPending}
                size="md"
              />
            </div>

            {sections.map((section, idx) => (
              <div
                key={idx}
                ref={setFieldRef(`section-${idx}`)}
                className={cn(
                  "space-y-2 rounded-md border bg-muted/20 p-3",
                  fieldHighlightClass(`section-${idx}`),
                )}
              >
                <Input
                  value={section.title}
                  onChange={(e) =>
                    setSections((prev) =>
                      prev.map((s, i) =>
                        i === idx ? { ...s, title: e.target.value } : s,
                      ),
                    )
                  }
                  placeholder={t("courses.lessonsContent.editor.sectionTitle")}
                  disabled={isPending}
                />
                <RichTextEditor
                  value={section.content}
                  onChange={(md) =>
                    setSections((prev) =>
                      prev.map((s, i) =>
                        i === idx ? { ...s, content: md } : s,
                      ),
                    )
                  }
                  disabled={isPending}
                  size="lg"
                />
              </div>
            ))}

            <div
              ref={setFieldRef("summary")}
              className={cn("space-y-1.5", fieldHighlightClass("summary"))}
            >
              <Label>{t("courses.lessonsContent.render.summary")}</Label>
              <RichTextEditor
                value={summary}
                onChange={setSummary}
                disabled={isPending}
                size="md"
              />
            </div>

            <div className="space-y-1.5">
              <Label>{t("courses.lessonsContent.render.keyTakeaways")}</Label>
              {keyTakeaways.map((kt, idx) => (
                <div key={idx} className="flex gap-2">
                  <Input
                    value={kt}
                    onChange={(e) => {
                      const next = [...keyTakeaways];
                      next[idx] = e.target.value;
                      setKeyTakeaways(next);
                    }}
                    disabled={isPending}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() =>
                      setKeyTakeaways(keyTakeaways.filter((_, i) => i !== idx))
                    }
                    disabled={isPending}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setKeyTakeaways([...keyTakeaways, ""])}
                disabled={isPending}
              >
                <Plus className="size-3.5" />
                {t("common.add")}
              </Button>
            </div>
          </SectionGroup>

          {/* === Asset visivi === */}
          <SectionGroup
            title={t("courses.lessonsContent.editor.visualAssets")}
            open={openGroups.visualAssets}
            onToggle={() => toggleGroup("visualAssets")}
          >
            {visualAssets.map((asset, idx) => (
              <VisualAssetEditor
                key={idx}
                orgId={orgId}
                courseId={courseId}
                asset={asset}
                onChange={(updates) =>
                  setVisualAssets(
                    visualAssets.map((a, i) =>
                      i === idx ? { ...a, ...updates } : a,
                    ),
                  )
                }
                onIdRename={(oldId, newId) => patchRefs("FIG", oldId, newId)}
                onDelete={() =>
                  setVisualAssets(visualAssets.filter((_, i) => i !== idx))
                }
                onHighlightUsage={() => highlightUsage("FIG", asset.asset_id)}
                disabled={isPending}
              />
            ))}
            <AddVisualAssetMenu
              orgId={orgId}
              courseId={courseId}
              nextIndex={visualAssets.length + 1}
              onAdd={(asset) => setVisualAssets([...visualAssets, asset])}
              disabled={isPending}
            />
          </SectionGroup>

          {/* === Tabelle === */}
          <SectionGroup
            title={t("courses.lessonsContent.editor.tables")}
            open={openGroups.tables}
            onToggle={() => toggleGroup("tables")}
          >
            {tables.map((table, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-md border bg-muted/20 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex-1 min-w-0">
                    <RefIdField
                      kind="TAB"
                      id={table.table_id}
                      onChange={(id) => {
                        const oldId = table.table_id;
                        const next = [...tables];
                        next[idx] = { ...table, table_id: id };
                        setTables(next);
                        patchRefs("TAB", oldId, id);
                      }}
                      disabled={isPending}
                    />
                  </div>
                  <HighlightUsageButton
                    onClick={() => highlightUsage("TAB", table.table_id)}
                    disabled={isPending || !table.table_id.trim()}
                  />
                </div>
                <Input
                  value={table.caption}
                  onChange={(e) => {
                    const next = [...tables];
                    next[idx] = { ...table, caption: e.target.value };
                    setTables(next);
                  }}
                  placeholder={t("courses.lessonsContent.editor.caption")}
                  disabled={isPending}
                />
                <TableEditor
                  value={table.markdown}
                  onChange={(md) => {
                    const next = [...tables];
                    next[idx] = { ...table, markdown: md };
                    setTables(next);
                  }}
                  disabled={isPending}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setTables(tables.filter((_, i) => i !== idx))
                  }
                  disabled={isPending}
                >
                  <Trash2 className="size-3.5" />
                  {t("common.delete")}
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setTables([
                  ...tables,
                  {
                    table_id: `T${tables.length + 1}`,
                    markdown: "| Colonna 1 | Colonna 2 |\n| --- | --- |\n|  |  |\n|  |  |",
                    caption: "",
                  },
                ])
              }
              disabled={isPending}
            >
              <Plus className="size-3.5" />
              {t("common.add")}
            </Button>
          </SectionGroup>

          {/* === Equazioni === */}
          <SectionGroup
            title={t("courses.lessonsContent.editor.equations")}
            open={openGroups.equations}
            onToggle={() => toggleGroup("equations")}
          >
            {equations.map((eq, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-md border bg-muted/20 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex-1 min-w-0">
                    <RefIdField
                      kind="EQ"
                      id={eq.equation_id}
                      onChange={(id) => {
                        const oldId = eq.equation_id;
                        const next = [...equations];
                        next[idx] = { ...eq, equation_id: id };
                        setEquations(next);
                        patchRefs("EQ", oldId, id);
                      }}
                      disabled={isPending}
                    />
                  </div>
                  <HighlightUsageButton
                    onClick={() => highlightUsage("EQ", eq.equation_id)}
                    disabled={isPending || !eq.equation_id.trim()}
                  />
                </div>
                <Input
                  value={eq.label}
                  onChange={(e) => {
                    const next = [...equations];
                    next[idx] = { ...eq, label: e.target.value };
                    setEquations(next);
                  }}
                  placeholder={t("courses.lessonsContent.editor.equationLabel")}
                  disabled={isPending}
                />
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("courses.lessonsContent.editor.equationKind")}
                  </Label>
                  <Select
                    value={eq.kind || "formula"}
                    onValueChange={(v) => {
                      const next = [...equations];
                      next[idx] = { ...eq, kind: v };
                      setEquations(next);
                    }}
                    disabled={isPending}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {EQUATION_KINDS.map((k) => (
                        <SelectItem key={k} value={k}>
                          {t(`courses.theorem.kind.${k}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <LatexEditor
                  value={eq.latex}
                  onChange={(latex) => {
                    const next = [...equations];
                    next[idx] = { ...eq, latex };
                    setEquations(next);
                  }}
                  disabled={isPending}
                />
                {/* Enunciato */}
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("courses.lessonsContent.editor.equationStatement")}
                  </Label>
                  <RichTextEditor
                    value={eq.statement || ""}
                    onChange={(md) => {
                      const next = [...equations];
                      next[idx] = { ...eq, statement: md };
                      setEquations(next);
                    }}
                    disabled={isPending}
                    size="sm"
                  />
                </div>
                {/* Dimostrazione: passaggi */}
                <div className="space-y-1.5">
                  <Label className="text-xs">
                    {t("courses.lessonsContent.editor.equationProof")}
                  </Label>
                  {(eq.proof || []).map((step, j) => {
                    const proof = eq.proof || [];
                    const setProof = (p: typeof proof) => {
                      const next = [...equations];
                      next[idx] = { ...eq, proof: p };
                      setEquations(next);
                    };
                    return (
                      <div
                        key={j}
                        className="space-y-1.5 rounded border bg-background p-2"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-[11px] font-medium text-muted-foreground">
                            {t("courses.lessonsContent.editor.proofStep", {
                              n: j + 1,
                            })}
                          </span>
                          <div className="flex items-center gap-1">
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="size-6"
                              disabled={isPending || j === 0}
                              onClick={() => {
                                const p = [...proof];
                                const tmp = p[j - 1];
                                p[j - 1] = p[j];
                                p[j] = tmp;
                                setProof(p);
                              }}
                            >
                              <ArrowUp className="size-3.5" />
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="size-6"
                              disabled={isPending || j === proof.length - 1}
                              onClick={() => {
                                const p = [...proof];
                                const tmp = p[j + 1];
                                p[j + 1] = p[j];
                                p[j] = tmp;
                                setProof(p);
                              }}
                            >
                              <ArrowDown className="size-3.5" />
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="size-6 text-destructive"
                              disabled={isPending}
                              onClick={() =>
                                setProof(proof.filter((_, i) => i !== j))
                              }
                            >
                              <Trash2 className="size-3.5" />
                            </Button>
                          </div>
                        </div>
                        <RichTextEditor
                          value={step.text || ""}
                          onChange={(md) => {
                            const p = [...proof];
                            p[j] = { ...p[j], text: md };
                            setProof(p);
                          }}
                          disabled={isPending}
                          size="sm"
                        />
                        <LatexEditor
                          value={step.latex || ""}
                          onChange={(latex) => {
                            const p = [...proof];
                            p[j] = { ...p[j], latex };
                            setProof(p);
                          }}
                          disabled={isPending}
                        />
                      </div>
                    );
                  })}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={isPending}
                    onClick={() => {
                      const next = [...equations];
                      next[idx] = {
                        ...eq,
                        proof: [...(eq.proof || []), { latex: "", text: "" }],
                      };
                      setEquations(next);
                    }}
                  >
                    <Plus className="size-3.5" />
                    {t("courses.lessonsContent.editor.addProofStep")}
                  </Button>
                </div>
                <div
                  ref={setFieldRef(`equation-${idx}-explanation`)}
                  className={cn(
                    "space-y-1.5",
                    fieldHighlightClass(`equation-${idx}-explanation`),
                  )}
                >
                  <Label className="text-xs">
                    {t("courses.lessonsContent.editor.equationExplanation")}
                  </Label>
                  <RichTextEditor
                    value={eq.explanation}
                    onChange={(md) => {
                      const next = [...equations];
                      next[idx] = { ...eq, explanation: md };
                      setEquations(next);
                    }}
                    disabled={isPending}
                    size="sm"
                  />
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setEquations(equations.filter((_, i) => i !== idx))
                  }
                  disabled={isPending}
                >
                  <Trash2 className="size-3.5" />
                  {t("common.delete")}
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setEquations([
                  ...equations,
                  {
                    equation_id: `E${equations.length + 1}`,
                    latex: "",
                    label: "",
                    explanation: "",
                    kind: "formula",
                    statement: "",
                    proof: [],
                  },
                ])
              }
              disabled={isPending}
            >
              <Plus className="size-3.5" />
              {t("common.add")}
            </Button>
          </SectionGroup>

          {/* === Esempi === */}
          <SectionGroup
            title={t("courses.lessonsContent.editor.examples")}
            open={openGroups.examples}
            onToggle={() => toggleGroup("examples")}
          >
            {examples.map((ex, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-md border bg-muted/20 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex-1 min-w-0">
                    <RefIdField
                      kind="EX"
                      id={ex.example_id}
                      onChange={(id) => {
                        const oldId = ex.example_id;
                        const next = [...examples];
                        next[idx] = { ...ex, example_id: id };
                        setExamples(next);
                        patchRefs("EX", oldId, id);
                      }}
                      disabled={isPending}
                    />
                  </div>
                  <HighlightUsageButton
                    onClick={() => highlightUsage("EX", ex.example_id)}
                    disabled={isPending || !ex.example_id.trim()}
                  />
                </div>
                <Input
                  value={ex.title}
                  onChange={(e) => {
                    const next = [...examples];
                    next[idx] = { ...ex, title: e.target.value };
                    setExamples(next);
                  }}
                  placeholder={t("courses.lessonsContent.editor.exampleTitle")}
                  disabled={isPending}
                />
                <div
                  ref={setFieldRef(`example-${idx}-content`)}
                  className={fieldHighlightClass(`example-${idx}-content`)}
                >
                  <RichTextEditor
                    value={ex.content}
                    onChange={(md) => {
                      const next = [...examples];
                      next[idx] = { ...ex, content: md };
                      setExamples(next);
                    }}
                    disabled={isPending}
                    size="md"
                  />
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setExamples(examples.filter((_, i) => i !== idx))
                  }
                  disabled={isPending}
                >
                  <Trash2 className="size-3.5" />
                  {t("common.delete")}
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setExamples([
                  ...examples,
                  {
                    example_id: `X${examples.length + 1}`,
                    title: "",
                    content: "",
                  },
                ])
              }
              disabled={isPending}
            >
              <Plus className="size-3.5" />
              {t("common.add")}
            </Button>
          </SectionGroup>

          {/* === References === */}
          <SectionGroup
            title={t("courses.lessonsContent.render.references")}
            open={openGroups.references}
            onToggle={() => toggleGroup("references")}
          >
            {references.map((ref, idx) => (
              <div key={idx} className="flex flex-wrap gap-2">
                <Select
                  value={ref.source}
                  onValueChange={(v) => {
                    const next = [...references];
                    next[idx] = {
                      ...ref,
                      source: v as LessonContentReference["source"],
                    };
                    setReferences(next);
                  }}
                >
                  <SelectTrigger className="w-56 shrink-0">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="documento_caricato">
                      {t("courses.lessonsContent.render.sources.documento_caricato")}
                    </SelectItem>
                    <SelectItem value="suggerimento_generale">
                      {t("courses.lessonsContent.render.sources.suggerimento_generale")}
                    </SelectItem>
                  </SelectContent>
                </Select>
                <Input
                  className="flex-1 min-w-[200px]"
                  value={ref.citation}
                  onChange={(e) => {
                    const next = [...references];
                    next[idx] = { ...ref, citation: e.target.value };
                    setReferences(next);
                  }}
                  disabled={isPending}
                />
                <HighlightUsageButton
                  onClick={() => highlightReferenceUsage(ref.citation)}
                  disabled={isPending || !ref.citation.trim()}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() =>
                    setReferences(references.filter((_, i) => i !== idx))
                  }
                  disabled={isPending}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setReferences([
                  ...references,
                  { citation: "", source: "documento_caricato" },
                ])
              }
              disabled={isPending}
            >
              <Plus className="size-3.5" />
              {t("common.add")}
            </Button>
          </SectionGroup>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={isPending}>
            <Save className="size-4" />
            {isPending
              ? t("courses.lessonsContent.dialog.edit.saving")
              : t("courses.lessonsContent.dialog.edit.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface SectionGroupProps {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

function SectionGroup({ title, open, onToggle, children }: SectionGroupProps) {
  return (
    <div className="rounded-lg border">
      <button
        type="button"
        className="flex w-full items-center gap-2 border-b bg-muted/30 px-4 py-2 text-left text-sm font-semibold hover:bg-muted/50"
        onClick={onToggle}
      >
        {open ? (
          <ChevronDown className="size-4" />
        ) : (
          <ChevronRight className="size-4" />
        )}
        {title}
      </button>
      {open && <div className="space-y-3 p-4">{children}</div>}
    </div>
  );
}

type RefKind = "FIG" | "TAB" | "EQ" | "EX";

interface RefIdFieldProps {
  kind: RefKind;
  id: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}

/**
 * Mostra l'identificatore di un asset/tabella/equazione/esempio nello stesso
 * formato `[KIND:id]` con cui è referenziato nel testo, in modo da rendere
 * immediato il match tra token nel testo e card nell'editor. L'`id` è
 * editabile e c'è un pulsante per copiare l'intero token in clipboard.
 */
function RefIdField({ kind, id, onChange, disabled }: RefIdFieldProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const token = `[${kind}:${id || ""}]`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      toast.success(t("courses.lessonsContent.editor.refCopied", { token }));
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(t("courses.lessonsContent.editor.refCopyFailed"));
    }
  };

  return (
    <div className="flex items-center gap-1.5 rounded-md border border-primary/30 bg-primary/5 px-2 py-1">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("courses.lessonsContent.editor.refCode")}
      </span>
      <span className="font-mono text-xs text-primary">[{kind}:</span>
      <input
        value={id}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        spellCheck={false}
        className="flex-1 min-w-0 bg-transparent font-mono text-xs focus:outline-none disabled:opacity-60"
        placeholder="id"
      />
      <span className="font-mono text-xs text-primary">]</span>
      <button
        type="button"
        onClick={handleCopy}
        disabled={disabled || !id}
        title={t("courses.lessonsContent.editor.refCopy")}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-40"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  );
}

interface VisualAssetEditorProps {
  orgId: string;
  courseId: string;
  asset: LessonContentVisualAsset;
  onChange: (updates: Partial<LessonContentVisualAsset>) => void;
  onIdRename: (oldId: string, newId: string) => void;
  onDelete: () => void;
  onHighlightUsage: () => void;
  disabled: boolean;
}

const LEGACY_FORMATS: ReadonlyArray<LessonContentVisualAsset["format"]> = [
  "image_prompt",
  "image_search_query",
  "description",
];

function VisualAssetEditor({
  orgId,
  courseId,
  asset,
  onChange,
  onIdRename,
  onDelete,
  onHighlightUsage,
  disabled,
}: VisualAssetEditorProps) {
  const { t } = useTranslation();
  const [converting, setConverting] = useState(false);

  const isLegacy = LEGACY_FORMATS.includes(asset.format);

  const handleConvertToMermaid = async () => {
    if (asset.format !== "image" || !asset.content) return;
    setConverting(true);
    try {
      const { mermaid_code } =
        await coursesApi.lessonAssets.convertToMermaid(
          orgId,
          courseId,
          asset.content,
        );
      onChange({ format: "mermaid", content: mermaid_code });
      toast.success(
        t("courses.lessonsContent.editor.assetActions.convertedToMermaid"),
      );
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsContent.editor.assetActions.convertToMermaidFailed"),
      );
    } finally {
      setConverting(false);
    }
  };

  return (
    <div className="space-y-2 rounded-md border bg-muted/20 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex-1 min-w-0">
          <RefIdField
            kind="FIG"
            id={asset.asset_id}
            onChange={(id) => {
              const oldId = asset.asset_id;
              onChange({ asset_id: id });
              onIdRename(oldId, id);
            }}
            disabled={disabled || isLegacy}
          />
        </div>
        <HighlightUsageButton
          onClick={onHighlightUsage}
          disabled={disabled || !asset.asset_id.trim()}
        />
      </div>

      {asset.format === "mermaid" && (
        <MermaidEditor
          value={asset.content}
          onChange={(code) => onChange({ content: code })}
          disabled={disabled}
        />
      )}

      {asset.format === "image" && (
        <div className="space-y-2">
          <div className="overflow-hidden rounded-md border bg-background">
            <img
              src={mediaUrl(asset.content)}
              alt={asset.alt_text || ""}
              className="block max-h-80 w-full object-contain"
            />
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleConvertToMermaid}
            disabled={disabled || converting}
          >
            {converting ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                {t(
                  "courses.lessonsContent.editor.assetActions.convertingToMermaid",
                )}
              </>
            ) : (
              <>
                <Sparkles className="size-3.5" />
                {t(
                  "courses.lessonsContent.editor.assetActions.convertToMermaid",
                )}
              </>
            )}
          </Button>
        </div>
      )}

      {isLegacy && (
        <div className="space-y-2">
          <div className="rounded-md border border-amber-400/40 bg-amber-50/40 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            {t("courses.lessonsContent.editor.legacyAssetBanner")}
          </div>
          <Textarea
            rows={4}
            value={asset.content}
            readOnly
            disabled={disabled}
            className="font-mono text-xs"
          />
        </div>
      )}

      <Input
        value={asset.caption}
        onChange={(e) => onChange({ caption: e.target.value })}
        placeholder={t("courses.lessonsContent.editor.caption")}
        disabled={disabled}
      />
      <Input
        value={asset.alt_text}
        onChange={(e) => onChange({ alt_text: e.target.value })}
        placeholder={t("courses.lessonsContent.editor.altText")}
        disabled={disabled}
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={onDelete}
        disabled={disabled}
      >
        <Trash2 className="size-3.5" />
        {t("common.delete")}
      </Button>
    </div>
  );
}


// ===========================================================================
// AddVisualAssetMenu — dropdown "Carica immagine" / "Scrivi Mermaid"
// ===========================================================================

interface AddVisualAssetMenuProps {
  orgId: string;
  courseId: string;
  nextIndex: number;
  onAdd: (asset: LessonContentVisualAsset) => void;
  disabled: boolean;
}

function AddVisualAssetMenu({
  orgId,
  courseId,
  nextIndex,
  onAdd,
  disabled,
}: AddVisualAssetMenuProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);

  const triggerFilePicker = () => {
    if (disabled || uploading) return;
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset il value subito così l'utente può ricaricare lo stesso file in
    // un secondo momento (browser non triggera change su same-file altrimenti).
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    try {
      const { path } = await coursesApi.lessonAssets.upload(
        orgId,
        courseId,
        file,
      );
      onAdd({
        asset_id: `A${nextIndex}`,
        format: "image",
        content: path,
        caption: "",
        alt_text: "",
      });
      toast.success(
        t("courses.lessonsContent.editor.assetActions.imageUploaded"),
      );
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsContent.editor.assetActions.imageUploadFailed"),
      );
    } finally {
      setUploading(false);
    }
  };

  const handleAddMermaid = () => {
    onAdd({
      asset_id: `A${nextIndex}`,
      format: "mermaid",
      content: "",
      caption: "",
      alt_text: "",
    });
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={handleFileChange}
      />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || uploading}
          >
            {uploading ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                {t("courses.lessonsContent.editor.assetActions.uploading")}
              </>
            ) : (
              <>
                <Plus className="size-3.5" />
                {t("courses.lessonsContent.editor.assetActions.addVisualAsset")}
              </>
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onClick={triggerFilePicker} disabled={uploading}>
            <Upload className="size-3.5" />
            {t("courses.lessonsContent.editor.assetActions.uploadImage")}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={handleAddMermaid}>
            <ImageIcon className="size-3.5" />
            {t("courses.lessonsContent.editor.assetActions.writeMermaid")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

interface HighlightUsageButtonProps {
  onClick: () => void;
  disabled?: boolean;
}

function HighlightUsageButton({ onClick, disabled }: HighlightUsageButtonProps) {
  const { t } = useTranslation();
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={onClick}
      disabled={disabled}
      title={t("courses.lessonsContent.editor.highlightUsage")}
    >
      <Eye className="size-3.5" />
      {t("courses.lessonsContent.editor.highlightUsage")}
    </Button>
  );
}
