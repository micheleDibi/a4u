import { lazy } from "react";

import { FigureSourceEditor, type SourceTemplate } from "./FigureSourceEditor";

const DotDiagram = lazy(() => import("./DotDiagram"));

/**
 * Editor di un sorgente Graphviz DOT (`format="dot"`): sorgente +
 * anteprima client (`DotDiagram`, WebAssembly), template accademici senza
 * attributi che leggono file (`image`, `URL`, `href`, `target`: rifiutati
 * dal validatore backend) e senza blocchi globali `graph/node/edge [`,
 * così il tema unico viene iniettato integralmente.
 *
 * I template sono ordinati per FAMIGLIA D'USO e ognuno è provato dal test
 * `test_frontend_figure_templates.py`, che li estrae da questo file, li fa
 * passare dal validatore di produzione e li rende con il binario `dot`.
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

const GROUP = {
  hierarchy: `${PREFIX}.groups.hierarchy`,
  flow: `${PREFIX}.groups.flow`,
  relations: `${PREFIX}.groups.relations`,
  models: `${PREFIX}.groups.models`,
  architecture: `${PREFIX}.groups.architecture`,
} as const;

const TEMPLATES: readonly SourceTemplate[] = [
  // --- Gerarchie e alberi -------------------------------------------------
  {
    id: "tree",
    groupKey: GROUP.hierarchy,
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
    id: "binaryTree",
    groupKey: GROUP.hierarchy,
    labelKey: `${PREFIX}.templates.binaryTree`,
    code: `digraph albero_binario_di_ricerca {
  rankdir=TB;
  ordering=out;
  n8 [label="8", shape=circle];
  n3 [label="3", shape=circle];
  n10 [label="10", shape=circle];
  n1 [label="1", shape=circle];
  n6 [label="6", shape=circle];
  n9 [label="9", shape=circle];
  n14 [label="14", shape=circle];
  n8 -> n3;
  n8 -> n10;
  n3 -> n1;
  n3 -> n6;
  n10 -> n9;
  n10 -> n14;
}`,
  },
  // --- Flussi e dipendenze ------------------------------------------------
  {
    id: "digraph",
    groupKey: GROUP.flow,
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
    id: "pipeline",
    groupKey: GROUP.flow,
    labelKey: `${PREFIX}.templates.pipeline`,
    code: `digraph dipendenze {
  rankdir=LR;
  raccolta [label="Raccolta dei dati"];
  pulizia [label="Pulizia"];
  analisi [label="Analisi statistica"];
  figure [label="Produzione delle figure"];
  relazione [label="Relazione finale"];
  raccolta -> pulizia;
  pulizia -> analisi;
  analisi -> figure;
  figure -> relazione;
  analisi -> relazione [label="tabelle", style=dashed];
}`,
  },
  // --- Relazioni e reti ---------------------------------------------------
  {
    id: "undirected",
    groupKey: GROUP.relations,
    labelKey: `${PREFIX}.templates.undirected`,
    code: `graph collaborazioni {
  rankdir=LR;
  ada [label="Ada"];
  bruno [label="Bruno"];
  carla [label="Carla"];
  dino [label="Dino"];
  elena [label="Elena"];
  ada -- bruno [label="progetto A"];
  ada -- carla;
  bruno -- carla;
  carla -- dino [label="progetto B"];
  dino -- elena;
}`,
  },
  // --- Modelli e strutture ------------------------------------------------
  {
    id: "automaton",
    groupKey: GROUP.models,
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
    id: "record",
    groupKey: GROUP.models,
    labelKey: `${PREFIX}.templates.record`,
    code: `digraph lista_concatenata {
  rankdir=LR;
  n1 [shape=Mrecord, label="{Nodo|{valore: 12|<succ> succ}}"];
  n2 [shape=Mrecord, label="{Nodo|{valore: 7|<succ> succ}}"];
  n3 [shape=Mrecord, label="{Nodo|{valore: 25|nil}}"];
  n1:succ -> n2;
  n2:succ -> n3;
}`,
  },
  // --- Architetture -------------------------------------------------------
  {
    id: "cluster",
    groupKey: GROUP.architecture,
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
