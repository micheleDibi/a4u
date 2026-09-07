import { lazy } from "react";

import { FigureSourceEditor, type SourceTemplate } from "./FigureSourceEditor";

const DotDiagram = lazy(() => import("./DotDiagram"));

/**
 * Editor di un sorgente Graphviz DOT (`format="dot"`): sorgente +
 * anteprima client (`DotDiagram`, WebAssembly), template accademici senza
 * attributi che leggono file (`image`, `URL`, `href`, `target`: rifiutati
 * dal validatore backend) e senza blocchi globali `graph/node/edge [`,
 * così il tema unico viene iniettato integralmente.
 */
interface DotEditorProps {
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  className?: string;
  rows?: number;
  /** 422 per asset del PATCH, mostrato in testa. */
  error?: string | null;
}

const PREFIX = "courses.lessonsContent.editorUI.dot";

const TEMPLATES: readonly SourceTemplate[] = [
  {
    id: "tree",
    labelKey: `${PREFIX}.templates.tree`,
    code: `digraph albero {
  rankdir=TB;
  radice [label="Radice"];
  a [label="Figlio A"];
  b [label="Figlio B"];
  a1 [label="Foglia A1"];
  a2 [label="Foglia A2"];
  b1 [label="Foglia B1"];
  radice -> a;
  radice -> b;
  a -> a1;
  a -> a2;
  b -> b1;
}`,
  },
  {
    id: "digraph",
    labelKey: `${PREFIX}.templates.digraph`,
    code: `digraph flusso {
  rankdir=LR;
  ingresso [label="Ingresso"];
  elaborazione [label="Elaborazione"];
  verifica [label="Verifica", shape=diamond];
  uscita [label="Uscita"];
  ingresso -> elaborazione;
  elaborazione -> verifica;
  verifica -> uscita [label="valido"];
  verifica -> elaborazione [label="non valido"];
}`,
  },
  {
    id: "automaton",
    labelKey: `${PREFIX}.templates.automaton`,
    code: `digraph automa {
  rankdir=LR;
  inizio [shape=point, label=""];
  q0 [shape=circle, label="q0"];
  q1 [shape=circle, label="q1"];
  q2 [shape=doublecircle, label="q2"];
  inizio -> q0;
  q0 -> q1 [label="a"];
  q1 -> q2 [label="b"];
  q2 -> q0 [label="a"];
  q1 -> q1 [label="a"];
}`,
  },
  {
    id: "cluster",
    labelKey: `${PREFIX}.templates.cluster`,
    code: `digraph architettura {
  rankdir=LR;
  subgraph cluster_client {
    label="Livello client";
    browser [label="Browser"];
  }
  subgraph cluster_server {
    label="Livello server";
    api [label="API"];
    db [label="Base di dati", shape=cylinder];
  }
  browser -> api [label="HTTP"];
  api -> db [label="SQL"];
}`,
  },
];

export function DotEditor({
  value,
  onChange,
  disabled = false,
  className,
  rows = 12,
  error,
}: DotEditorProps) {
  return (
    <FigureSourceEditor
      value={value}
      onChange={onChange}
      disabled={disabled}
      className={className}
      rows={rows}
      serverError={error}
      i18nPrefix={PREFIX}
      templates={TEMPLATES}
      placeholder="digraph G { A -> B; }"
      renderPreview={(source) => <DotDiagram source={source} />}
    />
  );
}

export default DotEditor;
