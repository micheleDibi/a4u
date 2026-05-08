import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const MermaidDiagram = lazy(() => import("./MermaidDiagram"));

interface MermaidEditorProps {
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  className?: string;
  rows?: number;
}

interface TemplateEntry {
  id: string;
  labelKey: string;
  code: string;
}

const TEMPLATES: TemplateEntry[] = [
  {
    id: "flowchart",
    labelKey: "courses.lessonsContent.editorUI.mermaid.templates.flowchart",
    code: `graph TD
  A[Inizio] --> B{Decisione}
  B -- Sì --> C[Azione 1]
  B -- No --> D[Azione 2]
  C --> E[Fine]
  D --> E[Fine]`,
  },
  {
    id: "sequence",
    labelKey: "courses.lessonsContent.editorUI.mermaid.templates.sequence",
    code: `sequenceDiagram
  participant U as Utente
  participant S as Sistema
  U->>S: Richiesta
  S-->>U: Risposta`,
  },
  {
    id: "state",
    labelKey: "courses.lessonsContent.editorUI.mermaid.templates.state",
    code: `stateDiagram-v2
  [*] --> Attesa
  Attesa --> InCorso : start
  InCorso --> Completato : finish
  Completato --> [*]`,
  },
  {
    id: "er",
    labelKey: "courses.lessonsContent.editorUI.mermaid.templates.er",
    code: `erDiagram
  CLIENTE ||--o{ ORDINE : effettua
  ORDINE ||--|{ RIGA_ORDINE : contiene
  PRODOTTO ||--o{ RIGA_ORDINE : presente`,
  },
  {
    id: "mindmap",
    labelKey: "courses.lessonsContent.editorUI.mermaid.templates.mindmap",
    code: `mindmap
  root((Argomento))
    Idea 1
      Sottoidea A
      Sottoidea B
    Idea 2
    Idea 3`,
  },
  {
    id: "class",
    labelKey: "courses.lessonsContent.editorUI.mermaid.templates.class",
    code: `classDiagram
  class Animale {
    +String nome
    +mangia()
  }
  class Cane {
    +abbaia()
  }
  Animale <|-- Cane`,
  },
  {
    id: "gantt",
    labelKey: "courses.lessonsContent.editorUI.mermaid.templates.gantt",
    code: `gantt
  title Timeline progetto
  dateFormat  YYYY-MM-DD
  section Analisi
  Raccolta requisiti :a1, 2025-01-01, 5d
  Studio fattibilità :a2, after a1, 3d
  section Sviluppo
  Implementazione    :b1, after a2, 10d`,
  },
];

export function MermaidEditor({
  value,
  onChange,
  disabled = false,
  className,
  rows = 8,
}: MermaidEditorProps) {
  const { t } = useTranslation();
  const [debouncedCode, setDebouncedCode] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setDebouncedCode(value);
    }, 500);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [value]);

  const applyTemplate = (id: string) => {
    const tpl = TEMPLATES.find((entry) => entry.id === id);
    if (!tpl) return;
    onChange(tpl.code);
  };

  return (
    <div
      className={cn(
        "rounded-md border bg-background",
        disabled && "pointer-events-none opacity-60",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b bg-muted/30 px-2 py-1.5">
        <span className="text-xs font-medium text-muted-foreground">
          {t("courses.lessonsContent.editorUI.mermaid.diagram")}
        </span>
        <Select onValueChange={applyTemplate} disabled={disabled}>
          <SelectTrigger className="h-7 w-56 text-xs">
            <SelectValue
              placeholder={t("courses.lessonsContent.editorUI.mermaid.insertTemplate")}
            />
          </SelectTrigger>
          <SelectContent>
            {TEMPLATES.map((tpl) => (
              <SelectItem key={tpl.id} value={tpl.id}>
                {t(tpl.labelKey)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="grid gap-2 p-2 md:grid-cols-2">
        <div className="space-y-1.5">
          <div className="px-1 text-xs font-medium text-muted-foreground">
            {t("courses.lessonsContent.editorUI.mermaid.code")}
          </div>
          <Textarea
            rows={rows}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            disabled={disabled}
            className="font-mono text-xs"
            placeholder="graph TD; A-->B;"
            spellCheck={false}
          />
        </div>
        <div className="space-y-1.5">
          <div className="px-1 text-xs font-medium text-muted-foreground">
            {t("courses.lessonsContent.editorUI.mermaid.preview")}
          </div>
          <MermaidPreview code={debouncedCode} />
        </div>
      </div>
    </div>
  );
}

interface MermaidPreviewProps {
  code: string;
}

function MermaidPreview({ code }: MermaidPreviewProps) {
  const { t } = useTranslation();
  if (!code.trim()) {
    return (
      <div className="flex min-h-[8rem] items-center justify-center rounded-md border border-dashed bg-muted/20 px-3 py-2 text-xs italic text-muted-foreground">
        {t("courses.lessonsContent.editorUI.mermaid.previewEmpty")}
      </div>
    );
  }
  return (
    <div className="rounded-md border bg-muted/20 p-1">
      <Suspense
        fallback={
          <div className="flex h-32 animate-pulse items-center justify-center rounded bg-muted text-xs text-muted-foreground">
            {t("courses.lessonsContent.editorUI.mermaid.loading")}
          </div>
        }
      >
        <MermaidDiagram code={code} />
      </Suspense>
    </div>
  );
}

export default MermaidEditor;
