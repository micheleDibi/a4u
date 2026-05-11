import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Save, Trash2, Plus, ChevronDown, ChevronRight, Copy, Check } from "lucide-react";
import { toast } from "sonner";

import type {
  LessonContentEquation,
  LessonContentExample,
  LessonContentRaw,
  LessonContentReference,
  LessonContentSection,
  LessonContentTable,
  LessonContentUpdateInput,
  LessonContentVisualAsset,
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

interface Props {
  open: boolean;
  isPending: boolean;
  lessonLabel: string;
  initial: LessonContentRaw;
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
          <SectionGroup title={t("courses.lessonsContent.editor.text")} defaultOpen>
            <div className="space-y-1.5">
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
                className="space-y-2 rounded-md border bg-muted/20 p-3"
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

            <div className="space-y-1.5">
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
          <SectionGroup title={t("courses.lessonsContent.editor.visualAssets")}>
            {visualAssets.map((asset, idx) => (
              <VisualAssetEditor
                key={idx}
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
                disabled={isPending}
              />
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setVisualAssets([
                  ...visualAssets,
                  {
                    asset_id: `A${visualAssets.length + 1}`,
                    asset_type: "diagram",
                    format: "mermaid",
                    content: "",
                    caption: "",
                    alt_text: "",
                  },
                ])
              }
              disabled={isPending}
            >
              <Plus className="size-3.5" />
              {t("common.add")}
            </Button>
          </SectionGroup>

          {/* === Tabelle === */}
          <SectionGroup title={t("courses.lessonsContent.editor.tables")}>
            {tables.map((table, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-md border bg-muted/20 p-3"
              >
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
          <SectionGroup title={t("courses.lessonsContent.editor.equations")}>
            {equations.map((eq, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-md border bg-muted/20 p-3"
              >
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
                <LatexEditor
                  value={eq.latex}
                  onChange={(latex) => {
                    const next = [...equations];
                    next[idx] = { ...eq, latex };
                    setEquations(next);
                  }}
                  disabled={isPending}
                />
                <div className="space-y-1.5">
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
          <SectionGroup title={t("courses.lessonsContent.editor.examples")}>
            {examples.map((ex, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-md border bg-muted/20 p-3"
              >
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
          <SectionGroup title={t("courses.lessonsContent.render.references")}>
            {references.map((ref, idx) => (
              <div key={idx} className="flex gap-2">
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
                  value={ref.citation}
                  onChange={(e) => {
                    const next = [...references];
                    next[idx] = { ...ref, citation: e.target.value };
                    setReferences(next);
                  }}
                  disabled={isPending}
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
  defaultOpen?: boolean;
  children: React.ReactNode;
}

function SectionGroup({ title, defaultOpen = false, children }: SectionGroupProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border">
      <button
        type="button"
        className="flex w-full items-center gap-2 border-b bg-muted/30 px-4 py-2 text-left text-sm font-semibold hover:bg-muted/50"
        onClick={() => setOpen((v) => !v)}
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
  asset: LessonContentVisualAsset;
  onChange: (updates: Partial<LessonContentVisualAsset>) => void;
  onIdRename: (oldId: string, newId: string) => void;
  onDelete: () => void;
  disabled: boolean;
}

function VisualAssetEditor({
  asset,
  onChange,
  onIdRename,
  onDelete,
  disabled,
}: VisualAssetEditorProps) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 rounded-md border bg-muted/20 p-3">
      <RefIdField
        kind="FIG"
        id={asset.asset_id}
        onChange={(id) => {
          const oldId = asset.asset_id;
          onChange({ asset_id: id });
          onIdRename(oldId, id);
        }}
        disabled={disabled}
      />
      <div className="grid grid-cols-2 gap-2">
        <Select
          value={asset.asset_type}
          onValueChange={(v) =>
            onChange({
              asset_type: v as LessonContentVisualAsset["asset_type"],
            })
          }
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="diagram">
              {t("courses.lessonsContent.editor.assetTypes.diagram")}
            </SelectItem>
            <SelectItem value="schema">
              {t("courses.lessonsContent.editor.assetTypes.schema")}
            </SelectItem>
            <SelectItem value="image">
              {t("courses.lessonsContent.editor.assetTypes.image")}
            </SelectItem>
            <SelectItem value="illustration">
              {t("courses.lessonsContent.editor.assetTypes.illustration")}
            </SelectItem>
            <SelectItem value="chart">
              {t("courses.lessonsContent.editor.assetTypes.chart")}
            </SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={asset.format}
          onValueChange={(v) =>
            onChange({ format: v as LessonContentVisualAsset["format"] })
          }
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="mermaid">
              {t("courses.lessonsContent.editor.assetFormats.mermaid")}
            </SelectItem>
            <SelectItem value="image_prompt">
              {t("courses.lessonsContent.editor.assetFormats.image_prompt")}
            </SelectItem>
            <SelectItem value="image_search_query">
              {t("courses.lessonsContent.editor.assetFormats.image_search_query")}
            </SelectItem>
            <SelectItem value="description">
              {t("courses.lessonsContent.editor.assetFormats.description")}
            </SelectItem>
          </SelectContent>
        </Select>
      </div>
      {asset.format === "mermaid" ? (
        <MermaidEditor
          value={asset.content}
          onChange={(code) => onChange({ content: code })}
          disabled={disabled}
        />
      ) : (
        <Textarea
          rows={4}
          value={asset.content}
          onChange={(e) => onChange({ content: e.target.value })}
          placeholder={t("courses.lessonsContent.editor.assetContent")}
          disabled={disabled}
        />
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
