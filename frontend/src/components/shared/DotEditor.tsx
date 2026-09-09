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
  paths: `${PREFIX}.groups.paths`,
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
  {
    id: "parseTree",
    groupKey: GROUP.hierarchy,
    labelKey: `${PREFIX}.templates.parseTree`,
    code: `digraph albero_di_derivazione {
  rankdir=TB;
  ordering=out;
  f [label="F"];
  sn [label="SN"];
  sv [label="SV"];
  art [label="Art"];
  n [label="N"];
  v [label="V"];
  w1 [label="la"];
  w2 [label="lezione"];
  w3 [label="inizia"];
  f -> sn;
  f -> sv;
  sn -> art;
  sn -> n;
  sv -> v;
  art -> w1;
  n -> w2;
  v -> w3;
}
`,
  },
  {
    id: "taxonomy",
    groupKey: GROUP.hierarchy,
    labelKey: `${PREFIX}.templates.taxonomy`,
    code: `digraph tassonomia {
  rankdir=TB;
  radice [label="Apprendimento automatico"];
  sup [label="Supervisionato"];
  nonsup [label="Non supervisionato"];
  rinf [label="Per rinforzo"];
  classificazione [label="Classificazione"];
  regressione [label="Regressione"];
  raggruppamento [label="Raggruppamento"];
  riduzione [label="Riduzione dimensionale"];
  radice -> sup;
  radice -> nonsup;
  radice -> rinf;
  sup -> classificazione;
  sup -> regressione;
  nonsup -> raggruppamento;
  nonsup -> riduzione;
}`,
  },
  // --- Grafi orientati e dipendenze ---------------------------------------
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
  {
    id: "callGraph",
    groupKey: GROUP.flow,
    labelKey: `${PREFIX}.templates.callGraph`,
    code: `digraph grafo_delle_chiamate {
  rankdir=TB;
  main [label="main()"];
  carica [label="carica_dati()"];
  valida [label="valida()"];
  normalizza [label="normalizza()"];
  analizza [label="analizza()"];
  media [label="media()"];
  varianza [label="varianza()"];
  referto [label="stampa_referto()"];
  main -> carica;
  main -> analizza;
  main -> referto;
  carica -> valida;
  carica -> normalizza;
  analizza -> media;
  analizza -> varianza;
  varianza -> media;
}`,
  },
  // --- Grafi non orientati e reti -----------------------------------------
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
  {
    id: "weighted",
    groupKey: GROUP.relations,
    labelKey: `${PREFIX}.templates.weighted`,
    code: `graph distanze_campus {
  rankdir=LR;
  aule [label="Aule"];
  biblioteca [label="Biblioteca"];
  mensa [label="Mensa"];
  laboratori [label="Laboratori"];
  residenza [label="Residenza"];
  aule -- biblioteca [label="4"];
  aule -- mensa [label="7"];
  biblioteca -- laboratori [label="3"];
  mensa -- laboratori [label="2"];
  laboratori -- residenza [label="6"];
  mensa -- residenza [label="9"];
}`,
  },
  {
    id: "bipartite",
    groupKey: GROUP.relations,
    labelKey: `${PREFIX}.templates.bipartite`,
    code: `graph abbinamento_tesi {
  rankdir=LR;
  s1 [label="Studente 1"];
  s2 [label="Studente 2"];
  s3 [label="Studente 3"];
  r1 [label="Relatore Rossi"];
  r2 [label="Relatrice Bianchi"];
  { rank=same; s1; s2; s3; }
  { rank=same; r1; r2; }
  s1 -- r1;
  s2 -- r1;
  s2 -- r2;
  s3 -- r2;
}`,
  },
  {
    id: "network",
    groupKey: GROUP.relations,
    labelKey: `${PREFIX}.templates.network`,
    code: `graph topologia_di_rete {
  rankdir=LR;
  esterna [label="Rete esterna"];
  frontiera [label="Router di frontiera"];
  firewall [label="Firewall"];
  commutatore [label="Commutatore di piano"];
  web [label="Server web"];
  applicativo [label="Server applicativo"];
  archivio [label="Base di dati", shape=cylinder];
  laboratorio [label="Laboratorio didattico"];
  esterna -- frontiera;
  frontiera -- firewall;
  firewall -- commutatore;
  commutatore -- web;
  commutatore -- applicativo;
  commutatore -- laboratorio;
  applicativo -- archivio;
}`,
  },
  // --- Automi e strutture dati --------------------------------------------
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
  {
    id: "hashTable",
    groupKey: GROUP.models,
    labelKey: `${PREFIX}.templates.hashTable`,
    code: `digraph tabella_hash {
  rankdir=LR;
  tabella [shape=Mrecord, label="{<b0> 0|<b1> 1|<b2> 2|<b3> 3}"];
  e0 [shape=Mrecord, label="{aula: B12|<succ> succ}"];
  e1 [shape=Mrecord, label="{esame: 27|nil}"];
  e2 [shape=Mrecord, label="{corso: 9 CFU|nil}"];
  e3 [shape=Mrecord, label="{docente: Rossi|nil}"];
  tabella:b0 -> e0;
  e0:succ -> e1;
  tabella:b2 -> e2;
  tabella:b3 -> e3;
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
  {
    id: "layers",
    groupKey: GROUP.architecture,
    labelKey: `${PREFIX}.templates.layers`,
    code: `digraph architettura_a_livelli {
  rankdir=TB;
  subgraph cluster_presentazione {
    label="Livello di presentazione";
    labeljust="r";
    web [label="Interfaccia web"];
    mobile [label="App mobile"];
    { rank=same; web; mobile; }
  }
  subgraph cluster_applicazione {
    label="Livello applicativo";
    labeljust="r";
    api [label="Servizi REST"];
    dominio [label="Logica di dominio"];
    { rank=same; api; dominio; }
  }
  subgraph cluster_persistenza {
    label="Livello di persistenza";
    labeljust="r";
    mappatura [label="Mappatura oggetti"];
    archivio [label="Base di dati", shape=cylinder];
    { rank=same; mappatura; archivio; }
  }
  web -> api;
  mobile -> api;
  api -> dominio;
  dominio -> mappatura;
  mappatura -> archivio;
}`,
  },
  // --- Cammini e flussi ---------------------------------------------------
  {
    id: "shortestPath",
    groupKey: GROUP.paths,
    labelKey: `${PREFIX}.templates.shortestPath`,
    code: `digraph cammino_minimo {
  rankdir=LR;
  s [label="S", shape=circle];
  a [label="A", shape=circle];
  b [label="B", shape=circle];
  c [label="C", shape=circle];
  t [label="T", shape=circle];
  s -> a [label="2", style=bold, penwidth=2.0];
  a -> b [label="1", style=bold, penwidth=2.0];
  b -> c [label="3", style=bold, penwidth=2.0];
  c -> t [label="2", style=bold, penwidth=2.0];
  s -> b [label="5"];
  a -> c [label="7"];
  b -> t [label="9"];
}`,
  },
  {
    id: "flowNetwork",
    groupKey: GROUP.paths,
    labelKey: `${PREFIX}.templates.flowNetwork`,
    code: `digraph rete_di_flusso {
  rankdir=LR;
  s [label="Sorgente"];
  a [label="Smistamento A"];
  b [label="Smistamento B"];
  c [label="Deposito C"];
  t [label="Pozzo"];
  s -> a [label="8/10"];
  s -> b [label="6/6"];
  a -> b [label="2/4"];
  a -> c [label="6/8"];
  b -> c [label="8/9"];
  c -> t [label="14/16"];
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
