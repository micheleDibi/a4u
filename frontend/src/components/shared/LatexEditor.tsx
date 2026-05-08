import { useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import katex from "katex";
import "katex/dist/katex.min.css";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface LatexEditorProps {
  value: string;
  onChange: (latex: string) => void;
  disabled?: boolean;
  className?: string;
  rows?: number;
}

interface SymbolEntry {
  label: string;
  insert: string;
  /** posizione del cursore relativa all'inizio dell'inserimento */
  cursorOffset?: number;
  /** lunghezza della selezione da impostare al cursorOffset */
  selectionLength?: number;
}

interface SymbolGroup {
  titleKey: string;
  items: SymbolEntry[];
}

const SYMBOL_GROUPS: SymbolGroup[] = [
  {
    titleKey: "courses.lessonsContent.editorUI.latex.groups.structures",
    items: [
      { label: "a/b", insert: "\\frac{a}{b}" },
      { label: "√x", insert: "\\sqrt{x}" },
      { label: "ⁿ√", insert: "\\sqrt[n]{x}" },
      { label: "a^b", insert: "a^{b}" },
      { label: "a_b", insert: "a_{b}" },
      { label: "( )", insert: "\\left( \\right)" },
      { label: "[ ]", insert: "\\left[ \\right]" },
      { label: "{ }", insert: "\\left\\{ \\right\\}" },
      { label: "|x|", insert: "\\left| x \\right|" },
    ],
  },
  {
    titleKey: "courses.lessonsContent.editorUI.latex.groups.operators",
    items: [
      { label: "Σ", insert: "\\sum_{i=1}^{n} " },
      { label: "∏", insert: "\\prod_{i=1}^{n} " },
      { label: "∫", insert: "\\int_{a}^{b} " },
      { label: "∮", insert: "\\oint " },
      { label: "lim", insert: "\\lim_{x \\to 0} " },
      { label: "∂/∂x", insert: "\\frac{\\partial}{\\partial x} " },
      { label: "d/dx", insert: "\\frac{d}{dx} " },
      { label: "∇", insert: "\\nabla " },
    ],
  },
  {
    titleKey: "courses.lessonsContent.editorUI.latex.groups.relations",
    items: [
      { label: "≤", insert: "\\le " },
      { label: "≥", insert: "\\ge " },
      { label: "≠", insert: "\\neq " },
      { label: "≈", insert: "\\approx " },
      { label: "≡", insert: "\\equiv " },
      { label: "→", insert: "\\to " },
      { label: "⇒", insert: "\\Rightarrow " },
      { label: "⇔", insert: "\\Leftrightarrow " },
      { label: "∈", insert: "\\in " },
      { label: "∉", insert: "\\notin " },
      { label: "∀", insert: "\\forall " },
      { label: "∃", insert: "\\exists " },
    ],
  },
  {
    titleKey: "courses.lessonsContent.editorUI.latex.groups.basicOps",
    items: [
      { label: "±", insert: "\\pm " },
      { label: "∓", insert: "\\mp " },
      { label: "×", insert: "\\times " },
      { label: "÷", insert: "\\div " },
      { label: "·", insert: "\\cdot " },
      { label: "∞", insert: "\\infty " },
    ],
  },
  {
    titleKey: "courses.lessonsContent.editorUI.latex.groups.greek",
    items: [
      { label: "α", insert: "\\alpha " },
      { label: "β", insert: "\\beta " },
      { label: "γ", insert: "\\gamma " },
      { label: "δ", insert: "\\delta " },
      { label: "ε", insert: "\\varepsilon " },
      { label: "θ", insert: "\\theta " },
      { label: "λ", insert: "\\lambda " },
      { label: "μ", insert: "\\mu " },
      { label: "π", insert: "\\pi " },
      { label: "σ", insert: "\\sigma " },
      { label: "φ", insert: "\\varphi " },
      { label: "ω", insert: "\\omega " },
      { label: "Σ", insert: "\\Sigma " },
      { label: "Π", insert: "\\Pi " },
      { label: "Ω", insert: "\\Omega " },
      { label: "Δ", insert: "\\Delta " },
    ],
  },
  {
    titleKey: "courses.lessonsContent.editorUI.latex.groups.matrices",
    items: [
      {
        label: "matrix",
        insert: "\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}",
      },
      {
        label: "cases",
        insert: "\\begin{cases} a & x \\ge 0 \\\\ b & x < 0 \\end{cases}",
      },
      {
        label: "align",
        insert: "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}",
      },
    ],
  },
];

export function LatexEditor({
  value,
  onChange,
  disabled = false,
  className,
  rows = 4,
}: LatexEditorProps) {
  const { t } = useTranslation();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const insertAtCursor = (snippet: string) => {
    const ta = textareaRef.current;
    if (!ta) {
      onChange((value || "") + snippet);
      return;
    }
    const start = ta.selectionStart ?? value.length;
    const end = ta.selectionEnd ?? value.length;
    const before = value.slice(0, start);
    const after = value.slice(end);
    const next = before + snippet + after;
    onChange(next);
    // Riporta il cursore dopo lo snippet, mantenendo focus
    requestAnimationFrame(() => {
      const t = textareaRef.current;
      if (!t) return;
      t.focus();
      const pos = before.length + snippet.length;
      t.setSelectionRange(pos, pos);
    });
  };

  return (
    <div
      className={cn(
        "rounded-md border bg-background",
        disabled && "pointer-events-none opacity-60",
        className,
      )}
    >
      <SymbolPalette
        onInsert={insertAtCursor}
        disabled={disabled}
        groups={SYMBOL_GROUPS}
        translate={t}
      />
      <div className="grid gap-2 p-2 md:grid-cols-2">
        <div className="space-y-1.5">
          <div className="px-1 text-xs font-medium text-muted-foreground">
            {t("courses.lessonsContent.editorUI.latex.code")}
          </div>
          <Textarea
            ref={textareaRef}
            rows={rows}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            disabled={disabled}
            className="font-mono text-xs"
            placeholder="E = mc^2"
            spellCheck={false}
          />
        </div>
        <div className="space-y-1.5">
          <div className="px-1 text-xs font-medium text-muted-foreground">
            {t("courses.lessonsContent.editorUI.latex.preview")}
          </div>
          <LatexPreview latex={value} />
        </div>
      </div>
    </div>
  );
}

interface SymbolPaletteProps {
  onInsert: (snippet: string) => void;
  disabled: boolean;
  groups: SymbolGroup[];
  translate: (key: string) => string;
}

function SymbolPalette({ onInsert, disabled, groups, translate }: SymbolPaletteProps) {
  return (
    <div className="border-b bg-muted/30 px-2 py-1.5">
      <div className="flex flex-wrap gap-2">
        {groups.map((g) => (
          <div key={g.titleKey} className="flex items-center gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              {translate(g.titleKey)}
            </span>
            <div className="flex flex-wrap gap-0.5">
              {g.items.map((item) => (
                <Button
                  key={item.label}
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-6 min-w-[1.75rem] px-1.5 text-xs"
                  onClick={() => onInsert(item.insert)}
                  disabled={disabled}
                  title={item.insert}
                >
                  {item.label}
                </Button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface LatexPreviewProps {
  latex: string;
}

function LatexPreview({ latex }: LatexPreviewProps) {
  const { t } = useTranslation();
  const { html, error } = useMemo(() => {
    const code = (latex || "").trim();
    if (!code) return { html: "", error: null as string | null };
    try {
      const html = katex.renderToString(code, {
        displayMode: true,
        throwOnError: true,
        strict: "ignore",
        trust: false,
      });
      return { html, error: null as string | null };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { html: "", error: msg };
    }
  }, [latex]);

  if (!latex.trim()) {
    return (
      <div className="flex min-h-[5rem] items-center justify-center rounded-md border border-dashed bg-muted/20 px-3 py-2 text-xs italic text-muted-foreground">
        {t("courses.lessonsContent.editorUI.latex.previewEmpty")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive">
        <div className="font-semibold">
          {t("courses.lessonsContent.editorUI.latex.previewError")}
        </div>
        <div className="mt-0.5 break-words font-mono">{error}</div>
      </div>
    );
  }
  return (
    <div
      className="overflow-x-auto rounded-md border bg-muted/20 px-3 py-2 text-sm"
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export default LatexEditor;
