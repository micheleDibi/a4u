import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { TemplateSelect, type SourceTemplate } from "./FigureSourceEditor";

const MermaidDiagram = lazy(() => import("./MermaidDiagram"));

/**
 * Editor di un sorgente Mermaid 11 (`format="mermaid"`): sorgente +
 * anteprima client, un template per ciascuno dei QUINDICI tipi ammessi dal
 * gate statico D8 (`figure_theme.MERMAID_D8_TYPES`), ordinati per famiglia
 * d'uso e non alfabeticamente.
 *
 * Regole rispettate da tutti i template (le impone il gate al salvataggio):
 * niente direttiva `%%{init}%%` (il tema lo impone il renderer), niente HTML
 * nelle label e nessuno statement che porti un URL o un'icona nella figura.
 * Le parentesi angolari nelle label vanno scritte come ENTITÀ (`&lt;`,
 * `&gt;`): scritte nude, Mermaid le legge come tag e cancella il testo — nel
 * diagramma delle classi i generici si scrivono invece `List~String~`, la
 * forma nativa del linguaggio.
 *
 * Vincoli di GEOMETRIA, verificati misurando in Chromium il bbox reale dei
 * `<text>` contro il viewBox (`test_frontend_figure_templates.py`): i tipi
 * a tela fissa non ridimensionano il riquadro sul testo, e ciò che esce
 * dalla tela lo taglia WeasyPrint nel PDF. Nel radar il margine del tema
 * (100 px a sinistra, 200 a destra per la legenda) tiene un'etichetta di
 * una ventina di caratteri: oltre, va accorciata. Nel treemap il corpo del
 * testo segue l'ALTEZZA della piastrella e Mermaid lo riduce fino a farlo
 * stare in larghezza con ~10 px di margine: valori troppo diversi fra loro
 * producono piastrelle strette con l'etichetta al limite del ritaglio,
 * quindi il modello tiene le quantità confrontabili.
 *
 * Ogni template è provato dal test `test_frontend_figure_templates.py`, che
 * li estrae da questo file, li fa passare dal gate di produzione e li rende
 * con Chromium verificando ZERO `<foreignObject>`.
 */
interface MermaidEditorProps {
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  className?: string;
  rows?: number;
}

const PREFIX = "courses.lessonsContent.editorUI.mermaid";

const GROUP = {
  process: `${PREFIX}.groups.process`,
  structure: `${PREFIX}.groups.structure`,
  knowledge: `${PREFIX}.groups.knowledge`,
  quantities: `${PREFIX}.groups.quantities`,
  planning: `${PREFIX}.groups.planning`,
} as const;

const TEMPLATES: readonly SourceTemplate[] = [
  // --- Processi e flussi --------------------------------------------------
  {
    id: "flowchart",
    groupKey: GROUP.process,
    labelKey: `${PREFIX}.templates.flowchart`,
    code: `flowchart TD
  A[Inizio] --> B{"Valore &lt; 0?"}
  B -- sì --> C[Correggi il segno]
  B -- no --> D[Prosegui]
  C --> E[Fine]
  D --> E`,
  },
  {
    id: "sequence",
    groupKey: GROUP.process,
    labelKey: `${PREFIX}.templates.sequence`,
    code: `sequenceDiagram
  participant U as Utente
  participant S as Sistema
  U->>S: Richiesta
  S-->>U: Risposta`,
  },
  {
    id: "state",
    groupKey: GROUP.process,
    labelKey: `${PREFIX}.templates.state`,
    code: `stateDiagram-v2
  [*] --> Attesa
  Attesa --> InCorso : start
  InCorso --> Completato : finish
  Completato --> [*]`,
  },
  // --- Struttura e modelli ------------------------------------------------
  {
    id: "class",
    groupKey: GROUP.structure,
    labelKey: `${PREFIX}.templates.class`,
    code: `classDiagram
  class Animale {
    +String nome
    +List~String~ tratti
    +mangia()
  }
  class Cane {
    +abbaia()
  }
  Animale <|-- Cane`,
  },
  {
    id: "er",
    groupKey: GROUP.structure,
    labelKey: `${PREFIX}.templates.er`,
    code: `erDiagram
  CLIENTE ||--o{ ORDINE : effettua
  ORDINE ||--|{ RIGA_ORDINE : contiene
  PRODOTTO ||--o{ RIGA_ORDINE : presente`,
  },
  {
    id: "block",
    groupKey: GROUP.structure,
    labelKey: `${PREFIX}.templates.block`,
    code: `block-beta
  columns 3
  acquisizione["Acquisizione"] elaborazione["Elaborazione"] presentazione["Presentazione"]
  acquisizione --> elaborazione
  elaborazione --> presentazione
  space:3
  archivio["Archivio dei dati"]:3
  elaborazione --> archivio`,
  },
  // --- Organizzazione dei concetti ---------------------------------------
  {
    id: "mindmap",
    groupKey: GROUP.knowledge,
    labelKey: `${PREFIX}.templates.mindmap`,
    code: `mindmap
  root)Argomento(
    Definizione
      Ipotesi
      Notazione
    Teorema
      Dimostrazione
    Esempi`,
  },
  {
    id: "timeline",
    groupKey: GROUP.knowledge,
    labelKey: `${PREFIX}.templates.timeline`,
    code: `timeline
  title Tappe della meccanica classica
  section XVI-XVII secolo
    1543 : De revolutionibus
    1687 : Principia
  section XVIII-XIX secolo
    1788 : Meccanica analitica di Lagrange
    1834 : Equazioni di Hamilton`,
  },
  {
    id: "treemap",
    groupKey: GROUP.knowledge,
    labelKey: `${PREFIX}.templates.treemap`,
    code: `treemap-beta
"Monte ore del corso"
    "Teoria"
        "Lezioni": 40
        "Seminari": 20
    "Pratica"
        "Esercitazioni": 20
        "Laboratorio": 20`,
  },
  // --- Quantità e ripartizioni -------------------------------------------
  {
    id: "pie",
    groupKey: GROUP.quantities,
    labelKey: `${PREFIX}.templates.pie`,
    code: `pie title Ripartizione del monte ore del corso
  "Lezioni" : 48
  "Esercitazioni" : 24
  "Laboratorio" : 16
  "Verifiche" : 12`,
  },
  {
    id: "xychart",
    groupKey: GROUP.quantities,
    labelKey: `${PREFIX}.templates.xychart`,
    code: `xychart-beta
  title "Iscritti (barre) e laureati (linea) per anno accademico"
  x-axis ["2020/21", "2021/22", "2022/23", "2023/24", "2024/25"]
  y-axis "Studenti" 0 --> 200
  bar [120, 138, 151, 166, 180]
  line [62, 71, 78, 84, 96]`,
  },
  {
    id: "radar",
    groupKey: GROUP.quantities,
    labelKey: `${PREFIX}.templates.radar`,
    code: `radar-beta
  title Profilo delle competenze
  axis analisi["Analisi"], sintesi["Sintesi"], calcolo["Calcolo"], scrittura["Scrittura"]
  curve iniziale["Rilevazione iniziale"]{2, 3, 3, 2}
  curve finale["Rilevazione finale"]{4, 4, 5, 4}
  max 5
  min 0`,
  },
  {
    id: "sankey",
    groupKey: GROUP.quantities,
    labelKey: `${PREFIX}.templates.sankey`,
    code: `sankey-beta

Monte ore,Lezioni,48
Monte ore,Esercitazioni,24
Monte ore,Laboratorio,16
Lezioni,Teoria,30
Lezioni,Esempi svolti,18`,
  },
  // --- Pianificazione e decisione ----------------------------------------
  {
    id: "gantt",
    groupKey: GROUP.planning,
    labelKey: `${PREFIX}.templates.gantt`,
    code: `gantt
  title Piano di lavoro
  dateFormat  YYYY-MM-DD
  section Analisi
  Raccolta requisiti :a1, 2025-01-01, 5d
  Studio di fattibilità :a2, after a1, 3d
  section Sviluppo
  Implementazione    :b1, after a2, 10d`,
  },
  {
    id: "quadrant",
    groupKey: GROUP.planning,
    labelKey: `${PREFIX}.templates.quadrant`,
    code: `quadrantChart
  title Priorità degli interventi didattici
  x-axis Basso impegno --> Alto impegno
  y-axis Basso impatto --> Alto impatto
  quadrant-1 Pianificare
  quadrant-2 Eseguire subito
  quadrant-3 Rinviare
  quadrant-4 Delegare
  Esercitazioni guidate: [0.35, 0.75]
  Nuovo laboratorio: [0.80, 0.85]
  Revisione delle dispense: [0.30, 0.40]
  Registrazione dei video: [0.70, 0.30]`,
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
        <TemplateSelect
          templates={TEMPLATES}
          placeholder={t("courses.lessonsContent.editorUI.mermaid.insertTemplate")}
          onPick={applyTemplate}
          disabled={disabled}
        />
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
