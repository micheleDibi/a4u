import { lazy } from "react";

import { FigureSourceEditor, type SourceTemplate } from "./FigureSourceEditor";

const VegaLiteDiagram = lazy(() => import("./VegaLiteDiagram"));

/**
 * Editor di una spec Vega-Lite (`format="vegalite"`): sorgente JSON +
 * anteprima client (`VegaLiteDiagram`), template accademici che
 * rispettano le regole D5 del validatore backend (`data.values` ≤ 200
 * righe, `clip: true` su line/area/point/trail, `scale.domain` sugli assi
 * quantitativi, una sola `title` alla radice, niente
 * `tooltip`/`selection`/`params`/`config`).
 *
 * I template sono ordinati per FAMIGLIA D'USO (confronto, parte sul tutto,
 * distribuzione, andamento, correlazione, matrice, incertezza,
 * graduatoria), non alfabeticamente. Ognuno è provato dal test
 * `test_frontend_figure_templates.py`, che li estrae da questo file e li
 * fa passare dal validatore di produzione e da `vl_convert`: un template
 * che non si salverebbe fa fallire la suite.
 */
interface VegaLiteEditorProps {
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
  className?: string;
  rows?: number;
  /** 422 per asset del PATCH, mostrato in testa. */
  error?: string | null;
}

const PREFIX = "courses.lessonsContent.editorUI.vegalite";

const GROUP = {
  comparison: `${PREFIX}.groups.comparison`,
  partToWhole: `${PREFIX}.groups.partToWhole`,
  distribution: `${PREFIX}.groups.distribution`,
  trend: `${PREFIX}.groups.trend`,
  correlation: `${PREFIX}.groups.correlation`,
  matrix: `${PREFIX}.groups.matrix`,
  uncertainty: `${PREFIX}.groups.uncertainty`,
  ranking: `${PREFIX}.groups.ranking`,
} as const;

const TEMPLATES: readonly SourceTemplate[] = [
  // --- Confronto fra categorie -------------------------------------------
  {
    id: "barsVertical",
    groupKey: GROUP.comparison,
    labelKey: `${PREFIX}.templates.barsVertical`,
    code: `{
  "title": "Iscritti per corso di laurea (dati illustrativi)",
  "data": {
    "values": [
      {"corso": "Matematica", "iscritti": 118},
      {"corso": "Fisica", "iscritti": 94},
      {"corso": "Informatica", "iscritti": 176},
      {"corso": "Chimica", "iscritti": 72}
    ]
  },
  "mark": "bar",
  "encoding": {
    "x": {"field": "corso", "type": "nominal", "axis": {"title": "Corso di laurea"}},
    "y": {
      "field": "iscritti", "type": "quantitative",
      "scale": {"domain": [0, 200]}, "axis": {"title": "Iscritti"}
    }
  }
}`,
  },
  {
    id: "barsHorizontal",
    groupKey: GROUP.comparison,
    labelKey: `${PREFIX}.templates.barsHorizontal`,
    code: `{
  "title": "Ore per attività didattica (dati illustrativi)",
  "data": {
    "values": [
      {"attivita": "Lezioni frontali in aula", "ore": 48},
      {"attivita": "Esercitazioni guidate", "ore": 24},
      {"attivita": "Laboratorio sperimentale", "ore": 16},
      {"attivita": "Studio individuale assistito", "ore": 32}
    ]
  },
  "mark": "bar",
  "encoding": {
    "y": {"field": "attivita", "type": "nominal", "axis": {"title": "Attività"}},
    "x": {
      "field": "ore", "type": "quantitative",
      "scale": {"domain": [0, 60]}, "axis": {"title": "Ore"}
    }
  }
}`,
  },
  {
    id: "barsGrouped",
    groupKey: GROUP.comparison,
    labelKey: `${PREFIX}.templates.barsGrouped`,
    code: `{
  "title": "Punteggio medio per prova e per gruppo (dati illustrativi)",
  "data": {
    "values": [
      {"prova": "Prova 1", "gruppo": "Sperimentale", "punteggio": 6.8},
      {"prova": "Prova 1", "gruppo": "Controllo", "punteggio": 6.1},
      {"prova": "Prova 2", "gruppo": "Sperimentale", "punteggio": 7.4},
      {"prova": "Prova 2", "gruppo": "Controllo", "punteggio": 6.3},
      {"prova": "Prova 3", "gruppo": "Sperimentale", "punteggio": 8.1},
      {"prova": "Prova 3", "gruppo": "Controllo", "punteggio": 6.9}
    ]
  },
  "mark": "bar",
  "encoding": {
    "x": {"field": "prova", "type": "nominal", "axis": {"title": "Prova"}},
    "xOffset": {"field": "gruppo"},
    "y": {
      "field": "punteggio", "type": "quantitative",
      "scale": {"domain": [0, 10]}, "axis": {"title": "Punteggio medio"}
    },
    "color": {"field": "gruppo", "type": "nominal", "legend": {"title": "Gruppo"}}
  }
}`,
  },
  {
    id: "barsStacked",
    groupKey: GROUP.comparison,
    labelKey: `${PREFIX}.templates.barsStacked`,
    code: `{
  "title": "Esiti d'esame per sessione (dati illustrativi)",
  "data": {
    "values": [
      {"sessione": "Invernale", "esito": "Superato", "studenti": 62},
      {"sessione": "Invernale", "esito": "Respinto", "studenti": 18},
      {"sessione": "Invernale", "esito": "Ritirato", "studenti": 9},
      {"sessione": "Estiva", "esito": "Superato", "studenti": 71},
      {"sessione": "Estiva", "esito": "Respinto", "studenti": 14},
      {"sessione": "Estiva", "esito": "Ritirato", "studenti": 7},
      {"sessione": "Autunnale", "esito": "Superato", "studenti": 44},
      {"sessione": "Autunnale", "esito": "Respinto", "studenti": 11},
      {"sessione": "Autunnale", "esito": "Ritirato", "studenti": 5}
    ]
  },
  "mark": "bar",
  "encoding": {
    "x": {"field": "sessione", "type": "nominal", "axis": {"title": "Sessione"}},
    "y": {
      "field": "studenti", "type": "quantitative", "stack": "zero",
      "scale": {"domain": [0, 100]}, "axis": {"title": "Studenti"}
    },
    "color": {"field": "esito", "type": "nominal", "legend": {"title": "Esito"}}
  }
}`,
  },
  {
    id: "barsNormalized",
    groupKey: GROUP.comparison,
    labelKey: `${PREFIX}.templates.barsNormalized`,
    code: `{
  "title": "Quote degli esiti d'esame per sessione (dati illustrativi)",
  "data": {
    "values": [
      {"sessione": "Invernale", "esito": "Superato", "studenti": 62},
      {"sessione": "Invernale", "esito": "Respinto", "studenti": 18},
      {"sessione": "Invernale", "esito": "Ritirato", "studenti": 9},
      {"sessione": "Estiva", "esito": "Superato", "studenti": 71},
      {"sessione": "Estiva", "esito": "Respinto", "studenti": 14},
      {"sessione": "Estiva", "esito": "Ritirato", "studenti": 7},
      {"sessione": "Autunnale", "esito": "Superato", "studenti": 44},
      {"sessione": "Autunnale", "esito": "Respinto", "studenti": 11},
      {"sessione": "Autunnale", "esito": "Ritirato", "studenti": 5}
    ]
  },
  "mark": "bar",
  "encoding": {
    "x": {"field": "sessione", "type": "nominal", "axis": {"title": "Sessione"}},
    "y": {
      "field": "studenti", "type": "quantitative", "stack": "normalize",
      "scale": {"domain": [0, 1]},
      "axis": {"title": "Quota sul totale", "format": ".0%"}
    },
    "color": {"field": "esito", "type": "nominal", "legend": {"title": "Esito"}}
  }
}`,
  },
  // --- Parte sul tutto ----------------------------------------------------
  {
    id: "pie",
    groupKey: GROUP.partToWhole,
    labelKey: `${PREFIX}.templates.pie`,
    code: `{
  "title": "Ripartizione del monte ore del corso (dati illustrativi)",
  "data": {
    "values": [
      {"componente": "Lezioni", "ore": 48},
      {"componente": "Esercitazioni", "ore": 24},
      {"componente": "Laboratorio", "ore": 16},
      {"componente": "Verifiche", "ore": 12}
    ]
  },
  "mark": {"type": "arc"},
  "encoding": {
    "theta": {"field": "ore", "type": "quantitative", "stack": true},
    "color": {"field": "componente", "type": "nominal", "legend": {"title": "Componente"}}
  }
}`,
  },
  {
    id: "donut",
    groupKey: GROUP.partToWhole,
    labelKey: `${PREFIX}.templates.donut`,
    code: `{
  "title": "Composizione del voto finale (dati illustrativi)",
  "data": {
    "values": [
      {"prova": "Prova scritta", "peso": 50},
      {"prova": "Prova orale", "peso": 30},
      {"prova": "Relazione di laboratorio", "peso": 20}
    ]
  },
  "mark": {"type": "arc", "innerRadius": 60},
  "encoding": {
    "theta": {"field": "peso", "type": "quantitative", "stack": true},
    "color": {"field": "prova", "type": "nominal", "legend": {"title": "Prova"}}
  }
}`,
  },
  // --- Distribuzione ------------------------------------------------------
  {
    id: "histogram",
    groupKey: GROUP.distribution,
    labelKey: `${PREFIX}.templates.histogram`,
    code: `{
  "title": "Distribuzione dei valori osservati",
  "data": {
    "values": [
      {"v": 1.2}, {"v": 2.4}, {"v": 2.9}, {"v": 3.1}, {"v": 3.8}, {"v": 4.0},
      {"v": 4.3}, {"v": 4.7}, {"v": 5.1}, {"v": 5.6}, {"v": 6.2}, {"v": 7.5}
    ]
  },
  "mark": "bar",
  "encoding": {
    "x": {
      "field": "v", "type": "quantitative", "bin": {"maxbins": 8},
      "scale": {"domain": [0, 8]}, "axis": {"title": "Valore (unità)"}
    },
    "y": {
      "aggregate": "count", "type": "quantitative",
      "scale": {"domain": [0, 5]}, "axis": {"title": "Frequenza"}
    }
  }
}`,
  },
  {
    id: "boxplot",
    groupKey: GROUP.distribution,
    labelKey: `${PREFIX}.templates.boxplot`,
    code: `{
  "title": "Distribuzione per gruppo",
  "data": {
    "values": [
      {"gruppo": "A", "valore": 3.1}, {"gruppo": "A", "valore": 3.8},
      {"gruppo": "A", "valore": 4.2}, {"gruppo": "A", "valore": 4.9},
      {"gruppo": "A", "valore": 5.5}, {"gruppo": "B", "valore": 2.4},
      {"gruppo": "B", "valore": 3.0}, {"gruppo": "B", "valore": 3.6},
      {"gruppo": "B", "valore": 4.1}, {"gruppo": "B", "valore": 6.2}
    ]
  },
  "mark": {"type": "boxplot", "extent": "min-max"},
  "encoding": {
    "x": {"field": "gruppo", "type": "nominal", "axis": {"title": "Gruppo"}},
    "y": {
      "field": "valore", "type": "quantitative",
      "scale": {"domain": [0, 7]}, "axis": {"title": "Valore (unità)"}
    }
  }
}`,
  },
  {
    id: "dotPlot",
    groupKey: GROUP.distribution,
    labelKey: `${PREFIX}.templates.dotPlot`,
    code: `{
  "title": "Voti registrati all'appello, un punto per studente (dati illustrativi)",
  "data": {
    "values": [
      {"voto": 21}, {"voto": 22}, {"voto": 22}, {"voto": 24}, {"voto": 24},
      {"voto": 24}, {"voto": 25}, {"voto": 26}, {"voto": 26}, {"voto": 27},
      {"voto": 27}, {"voto": 27}, {"voto": 28}, {"voto": 28}, {"voto": 30}
    ]
  },
  "transform": [{"window": [{"op": "rank", "as": "posizione"}], "groupby": ["voto"]}],
  "mark": {"type": "point", "clip": true, "filled": true, "size": 90},
  "encoding": {
    "x": {
      "field": "voto", "type": "quantitative",
      "scale": {"domain": [18, 31]}, "axis": {"title": "Voto", "format": "d"}
    },
    "y": {
      "field": "posizione", "type": "quantitative",
      "scale": {"domain": [0, 4]}, "axis": {"title": "Conteggio", "format": "d"}
    }
  }
}`,
  },
  {
    id: "violin",
    groupKey: GROUP.distribution,
    labelKey: `${PREFIX}.templates.violin`,
    code: `{
  "title": "Densità dei valori per gruppo (dati illustrativi)",
  "width": 140,
  "data": {
    "values": [
      {"gruppo": "A", "valore": 3.1}, {"gruppo": "A", "valore": 3.8},
      {"gruppo": "A", "valore": 4.2}, {"gruppo": "A", "valore": 4.4},
      {"gruppo": "A", "valore": 4.9}, {"gruppo": "A", "valore": 5.5},
      {"gruppo": "B", "valore": 2.4}, {"gruppo": "B", "valore": 3.0},
      {"gruppo": "B", "valore": 3.3}, {"gruppo": "B", "valore": 3.6},
      {"gruppo": "B", "valore": 4.1}, {"gruppo": "B", "valore": 6.2}
    ]
  },
  "transform": [
    {"density": "valore", "groupby": ["gruppo"], "extent": [0, 8], "steps": 40}
  ],
  "mark": {
    "type": "area", "clip": true, "orient": "horizontal",
    "line": false, "opacity": 0.8
  },
  "encoding": {
    "y": {
      "field": "value", "type": "quantitative",
      "scale": {"domain": [0, 8]}, "axis": {"title": "Valore (unità)"}
    },
    "x": {
      "field": "density", "type": "quantitative", "stack": "center",
      "scale": {"domain": [-0.5, 0.5]}, "axis": {"title": "Densità"}
    },
    "column": {"field": "gruppo", "type": "nominal", "header": {"title": "Gruppo"}}
  }
}`,
  },
  // --- Andamento nel tempo ------------------------------------------------
  {
    id: "line",
    groupKey: GROUP.trend,
    labelKey: `${PREFIX}.templates.line`,
    code: `{
  "title": "Frequenza alle lezioni per settimana (dati illustrativi)",
  "data": {
    "values": [
      {"settimana": 1, "presenze": 92}, {"settimana": 2, "presenze": 88},
      {"settimana": 3, "presenze": 85}, {"settimana": 4, "presenze": 79},
      {"settimana": 5, "presenze": 74}, {"settimana": 6, "presenze": 76},
      {"settimana": 7, "presenze": 71}, {"settimana": 8, "presenze": 68},
      {"settimana": 9, "presenze": 72}, {"settimana": 10, "presenze": 81}
    ]
  },
  "mark": {"type": "line", "clip": true, "point": true},
  "encoding": {
    "x": {
      "field": "settimana", "type": "quantitative",
      "scale": {"domain": [1, 10]}, "axis": {"title": "Settimana", "format": "d"}
    },
    "y": {
      "field": "presenze", "type": "quantitative",
      "scale": {"domain": [0, 100]}, "axis": {"title": "Presenze (%)"}
    }
  }
}`,
  },
  {
    id: "multiLine",
    groupKey: GROUP.trend,
    labelKey: `${PREFIX}.templates.multiLine`,
    code: `{
  "title": "Punteggio medio per settimana e per gruppo (dati illustrativi)",
  "data": {
    "values": [
      {"settimana": 1, "gruppo": "Sperimentale", "punteggio": 5.8},
      {"settimana": 2, "gruppo": "Sperimentale", "punteggio": 6.4},
      {"settimana": 3, "gruppo": "Sperimentale", "punteggio": 7.1},
      {"settimana": 4, "gruppo": "Sperimentale", "punteggio": 7.6},
      {"settimana": 5, "gruppo": "Sperimentale", "punteggio": 8.2},
      {"settimana": 1, "gruppo": "Controllo", "punteggio": 5.6},
      {"settimana": 2, "gruppo": "Controllo", "punteggio": 5.9},
      {"settimana": 3, "gruppo": "Controllo", "punteggio": 6.1},
      {"settimana": 4, "gruppo": "Controllo", "punteggio": 6.4},
      {"settimana": 5, "gruppo": "Controllo", "punteggio": 6.6}
    ]
  },
  "mark": {"type": "line", "clip": true, "point": true},
  "encoding": {
    "x": {
      "field": "settimana", "type": "quantitative",
      "scale": {"domain": [1, 5]}, "axis": {"title": "Settimana", "format": "d"}
    },
    "y": {
      "field": "punteggio", "type": "quantitative",
      "scale": {"domain": [0, 10]}, "axis": {"title": "Punteggio medio"}
    },
    "color": {"field": "gruppo", "type": "nominal", "legend": {"title": "Gruppo"}}
  }
}`,
  },
  {
    id: "area",
    groupKey: GROUP.trend,
    labelKey: `${PREFIX}.templates.area`,
    code: `{
  "title": "Ore di studio cumulate per settimana (dati illustrativi)",
  "data": {
    "values": [
      {"settimana": 1, "ore": 6}, {"settimana": 2, "ore": 13},
      {"settimana": 3, "ore": 21}, {"settimana": 4, "ore": 30},
      {"settimana": 5, "ore": 36}, {"settimana": 6, "ore": 45},
      {"settimana": 7, "ore": 56}, {"settimana": 8, "ore": 64}
    ]
  },
  "mark": {"type": "area", "clip": true},
  "encoding": {
    "x": {
      "field": "settimana", "type": "quantitative",
      "scale": {"domain": [1, 8]}, "axis": {"title": "Settimana", "format": "d"}
    },
    "y": {
      "field": "ore", "type": "quantitative",
      "scale": {"domain": [0, 80]}, "axis": {"title": "Ore cumulate"}
    }
  }
}`,
  },
  {
    id: "stackedArea",
    groupKey: GROUP.trend,
    labelKey: `${PREFIX}.templates.stackedArea`,
    code: `{
  "title": "Ore settimanali per tipo di attività (dati illustrativi)",
  "data": {
    "values": [
      {"settimana": 1, "attivita": "Lezione", "ore": 4},
      {"settimana": 1, "attivita": "Esercitazione", "ore": 2},
      {"settimana": 1, "attivita": "Studio individuale", "ore": 5},
      {"settimana": 2, "attivita": "Lezione", "ore": 4},
      {"settimana": 2, "attivita": "Esercitazione", "ore": 3},
      {"settimana": 2, "attivita": "Studio individuale", "ore": 6},
      {"settimana": 3, "attivita": "Lezione", "ore": 4},
      {"settimana": 3, "attivita": "Esercitazione", "ore": 3},
      {"settimana": 3, "attivita": "Studio individuale", "ore": 8},
      {"settimana": 4, "attivita": "Lezione", "ore": 4},
      {"settimana": 4, "attivita": "Esercitazione", "ore": 4},
      {"settimana": 4, "attivita": "Studio individuale", "ore": 9}
    ]
  },
  "mark": {"type": "area", "clip": true},
  "encoding": {
    "x": {
      "field": "settimana", "type": "quantitative",
      "scale": {"domain": [1, 4]}, "axis": {"title": "Settimana", "format": "d"}
    },
    "y": {
      "field": "ore", "type": "quantitative", "stack": "zero",
      "scale": {"domain": [0, 20]}, "axis": {"title": "Ore"}
    },
    "color": {"field": "attivita", "type": "nominal", "legend": {"title": "Attività"}}
  }
}`,
  },
  {
    id: "stepLine",
    groupKey: GROUP.trend,
    labelKey: `${PREFIX}.templates.stepLine`,
    code: `{
  "title": "Crediti acquisiti per semestre (dati illustrativi)",
  "data": {
    "values": [
      {"semestre": 1, "crediti": 24}, {"semestre": 2, "crediti": 51},
      {"semestre": 3, "crediti": 78}, {"semestre": 4, "crediti": 102},
      {"semestre": 5, "crediti": 129}, {"semestre": 6, "crediti": 156}
    ]
  },
  "mark": {"type": "line", "clip": true, "interpolate": "step-after", "point": true},
  "encoding": {
    "x": {
      "field": "semestre", "type": "quantitative",
      "scale": {"domain": [1, 6]}, "axis": {"title": "Semestre", "format": "d"}
    },
    "y": {
      "field": "crediti", "type": "quantitative",
      "scale": {"domain": [0, 180]}, "axis": {"title": "Crediti cumulati"}
    }
  }
}`,
  },
  {
    id: "timeSeries",
    groupKey: GROUP.trend,
    labelKey: `${PREFIX}.templates.timeSeries`,
    code: `{
  "title": "Andamento nel tempo",
  "data": {
    "values": [
      {"anno": 2018, "valore": 3.1}, {"anno": 2019, "valore": 3.6},
      {"anno": 2020, "valore": 2.9}, {"anno": 2021, "valore": 4.2},
      {"anno": 2022, "valore": 4.8}, {"anno": 2023, "valore": 5.3},
      {"anno": 2024, "valore": 5.9}
    ]
  },
  "mark": {"type": "line", "clip": true, "point": true},
  "encoding": {
    "x": {
      "field": "anno", "type": "quantitative",
      "scale": {"domain": [2018, 2024]}, "axis": {"title": "Anno", "format": "d"}
    },
    "y": {
      "field": "valore", "type": "quantitative",
      "scale": {"domain": [0, 7]}, "axis": {"title": "Valore (unità)"}
    }
  }
}`,
  },
  // --- Correlazione -------------------------------------------------------
  {
    id: "scatter",
    groupKey: GROUP.correlation,
    labelKey: `${PREFIX}.templates.scatter`,
    code: `{
  "title": "Relazione fra due grandezze",
  "data": {
    "values": [
      {"x": 1.0, "y": 2.1}, {"x": 1.8, "y": 2.9}, {"x": 2.5, "y": 3.2},
      {"x": 3.1, "y": 4.4}, {"x": 3.9, "y": 4.1}, {"x": 4.6, "y": 5.6},
      {"x": 5.2, "y": 5.9}, {"x": 6.0, "y": 7.1}, {"x": 6.8, "y": 6.9}
    ]
  },
  "mark": {"type": "point", "clip": true},
  "encoding": {
    "x": {
      "field": "x", "type": "quantitative",
      "scale": {"domain": [0, 8]}, "axis": {"title": "x (unità)"}
    },
    "y": {
      "field": "y", "type": "quantitative",
      "scale": {"domain": [0, 8]}, "axis": {"title": "y (unità)"}
    }
  }
}`,
  },
  {
    id: "bubble",
    groupKey: GROUP.correlation,
    labelKey: `${PREFIX}.templates.bubble`,
    code: `{
  "title": "Ore di studio, voto medio e numerosità del gruppo (dati illustrativi)",
  "data": {
    "values": [
      {"ore": 3, "voto": 22.1, "studenti": 12},
      {"ore": 5, "voto": 24.0, "studenti": 18},
      {"ore": 6, "voto": 24.8, "studenti": 25},
      {"ore": 8, "voto": 26.4, "studenti": 20},
      {"ore": 9, "voto": 27.1, "studenti": 14},
      {"ore": 11, "voto": 28.6, "studenti": 8}
    ]
  },
  "mark": {"type": "point", "clip": true, "filled": true, "opacity": 0.75},
  "encoding": {
    "x": {
      "field": "ore", "type": "quantitative",
      "scale": {"domain": [0, 12]}, "axis": {"title": "Ore di studio settimanali"}
    },
    "y": {
      "field": "voto", "type": "quantitative",
      "scale": {"domain": [18, 31]}, "axis": {"title": "Voto medio"}
    },
    "size": {
      "field": "studenti", "type": "quantitative",
      "scale": {"range": [60, 600]}, "legend": {"title": "Studenti"}
    }
  }
}`,
  },
  // --- Matrice ------------------------------------------------------------
  {
    id: "heatmap",
    groupKey: GROUP.matrix,
    labelKey: `${PREFIX}.templates.heatmap`,
    code: `{
  "title": "Ore di lezione per giorno e fascia oraria (dati illustrativi)",
  "data": {
    "values": [
      {"giorno": "Lunedì", "fascia": "9-11", "ore": 2},
      {"giorno": "Lunedì", "fascia": "11-13", "ore": 2},
      {"giorno": "Lunedì", "fascia": "14-16", "ore": 0},
      {"giorno": "Martedì", "fascia": "9-11", "ore": 2},
      {"giorno": "Martedì", "fascia": "11-13", "ore": 0},
      {"giorno": "Martedì", "fascia": "14-16", "ore": 3},
      {"giorno": "Mercoledì", "fascia": "9-11", "ore": 0},
      {"giorno": "Mercoledì", "fascia": "11-13", "ore": 2},
      {"giorno": "Mercoledì", "fascia": "14-16", "ore": 2},
      {"giorno": "Giovedì", "fascia": "9-11", "ore": 3},
      {"giorno": "Giovedì", "fascia": "11-13", "ore": 1},
      {"giorno": "Giovedì", "fascia": "14-16", "ore": 0},
      {"giorno": "Venerdì", "fascia": "9-11", "ore": 2},
      {"giorno": "Venerdì", "fascia": "11-13", "ore": 3},
      {"giorno": "Venerdì", "fascia": "14-16", "ore": 1}
    ]
  },
  "mark": {"type": "rect"},
  "encoding": {
    "x": {"field": "giorno", "type": "nominal", "axis": {"title": "Giorno"}},
    "y": {"field": "fascia", "type": "ordinal", "axis": {"title": "Fascia oraria"}},
    "color": {
      "field": "ore", "type": "quantitative",
      "scale": {"scheme": "blues"}, "legend": {"title": "Ore"}
    }
  }
}`,
  },
  // --- Incertezza ---------------------------------------------------------
  {
    id: "barsWithError",
    groupKey: GROUP.uncertainty,
    labelKey: `${PREFIX}.templates.barsWithError`,
    code: `{
  "title": "Media per gruppo con deviazione standard",
  "data": {
    "values": [
      {"gruppo": "A", "media": 4.2, "lo": 3.7, "hi": 4.7},
      {"gruppo": "B", "media": 5.1, "lo": 4.4, "hi": 5.8},
      {"gruppo": "C", "media": 3.6, "lo": 3.2, "hi": 4.0}
    ]
  },
  "layer": [
    {
      "mark": "bar",
      "encoding": {
        "x": {"field": "gruppo", "type": "nominal", "axis": {"title": "Gruppo"}},
        "y": {
          "field": "media", "type": "quantitative",
          "scale": {"domain": [0, 7]}, "axis": {"title": "Media (unità)"}
        }
      }
    },
    {
      "mark": {"type": "errorbar", "ticks": true},
      "encoding": {
        "x": {"field": "gruppo", "type": "nominal"},
        "y": {"field": "lo", "type": "quantitative", "scale": {"domain": [0, 7]}},
        "y2": {"field": "hi"}
      }
    }
  ]
}`,
  },
  {
    id: "confidenceBand",
    groupKey: GROUP.uncertainty,
    labelKey: `${PREFIX}.templates.confidenceBand`,
    code: `{
  "title": "Stima media con banda di confidenza al 95% (dati illustrativi)",
  "data": {
    "values": [
      {"t": 0, "media": 2.1, "lo": 1.7, "hi": 2.5},
      {"t": 1, "media": 2.6, "lo": 2.1, "hi": 3.1},
      {"t": 2, "media": 3.0, "lo": 2.4, "hi": 3.6},
      {"t": 3, "media": 3.7, "lo": 3.0, "hi": 4.4},
      {"t": 4, "media": 4.1, "lo": 3.3, "hi": 4.9},
      {"t": 5, "media": 4.4, "lo": 3.5, "hi": 5.3},
      {"t": 6, "media": 4.6, "lo": 3.6, "hi": 5.6}
    ]
  },
  "layer": [
    {
      "mark": {"type": "area", "clip": true, "opacity": 0.25, "line": false},
      "encoding": {
        "x": {
          "field": "t", "type": "quantitative",
          "scale": {"domain": [0, 6]}, "axis": {"title": "Settimana", "format": "d"}
        },
        "y": {
          "field": "lo", "type": "quantitative",
          "scale": {"domain": [0, 7]}, "axis": {"title": "Valore stimato"}
        },
        "y2": {"field": "hi"}
      }
    },
    {
      "mark": {"type": "line", "clip": true, "point": true},
      "encoding": {
        "x": {"field": "t", "type": "quantitative", "scale": {"domain": [0, 6]}},
        "y": {"field": "media", "type": "quantitative", "scale": {"domain": [0, 7]}}
      }
    }
  ]
}`,
  },
  // --- Graduatoria --------------------------------------------------------
  {
    id: "barsRanked",
    groupKey: GROUP.ranking,
    labelKey: `${PREFIX}.templates.barsRanked`,
    code: `{
  "title": "Pubblicazioni per dipartimento, in ordine decrescente (dati illustrativi)",
  "data": {
    "values": [
      {"dipartimento": "Matematica", "pubblicazioni": 42},
      {"dipartimento": "Fisica", "pubblicazioni": 51},
      {"dipartimento": "Informatica", "pubblicazioni": 47},
      {"dipartimento": "Chimica", "pubblicazioni": 33},
      {"dipartimento": "Biologia", "pubblicazioni": 28}
    ]
  },
  "mark": "bar",
  "encoding": {
    "y": {
      "field": "dipartimento", "type": "nominal", "sort": "-x",
      "axis": {"title": "Dipartimento"}
    },
    "x": {
      "field": "pubblicazioni", "type": "quantitative",
      "scale": {"domain": [0, 60]}, "axis": {"title": "Pubblicazioni"}
    }
  }
}`,
  },
  {
    id: "lollipop",
    groupKey: GROUP.ranking,
    labelKey: `${PREFIX}.templates.lollipop`,
    code: `{
  "title": "Indice di gradimento per insegnamento (dati illustrativi)",
  "data": {
    "values": [
      {"insegnamento": "Analisi matematica I", "indice": 8.4},
      {"insegnamento": "Geometria", "indice": 7.6},
      {"insegnamento": "Fisica generale", "indice": 7.1},
      {"insegnamento": "Programmazione", "indice": 8.9},
      {"insegnamento": "Statistica", "indice": 6.8}
    ]
  },
  "layer": [
    {
      "mark": {"type": "rule"},
      "encoding": {
        "y": {
          "field": "insegnamento", "type": "nominal", "sort": "-x",
          "axis": {"title": "Insegnamento"}
        },
        "x": {
          "field": "indice", "type": "quantitative",
          "scale": {"domain": [0, 10]}, "axis": {"title": "Indice (0-10)"}
        },
        "x2": {"datum": 0}
      }
    },
    {
      "mark": {"type": "point", "clip": true, "filled": true, "size": 110},
      "encoding": {
        "y": {"field": "insegnamento", "type": "nominal", "sort": "-x"},
        "x": {
          "field": "indice", "type": "quantitative",
          "scale": {"domain": [0, 10]}
        }
      }
    }
  ]
}`,
  },
];

export function VegaLiteEditor({
  value,
  onChange,
  disabled = false,
  className,
  rows = 12,
  error,
}: VegaLiteEditorProps) {
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
      placeholder='{"data": {"values": []}, "mark": "bar", "encoding": {}}'
      renderPreview={(source) => <VegaLiteDiagram spec={source} />}
    />
  );
}

export default VegaLiteEditor;
