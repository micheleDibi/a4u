import { lazy } from "react";

import { FigureSourceEditor, type SourceTemplate } from "./FigureSourceEditor";

const VegaLiteDiagram = lazy(() => import("./VegaLiteDiagram"));

/**
 * Editor di una spec Vega-Lite (`format="vegalite"`): sorgente JSON +
 * anteprima client (`VegaLiteDiagram`), template accademici che
 * rispettano le regole D5 del validatore backend (`data.values` ≤ 200
 * righe, `clip: true` su line/point, `scale.domain` sugli assi quantitativi,
 * una sola `title` alla radice, niente `tooltip`/`selection`/`config`).
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

const TEMPLATES: readonly SourceTemplate[] = [
  {
    id: "histogram",
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
    id: "barsWithError",
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
    id: "scatter",
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
    id: "boxplot",
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
    id: "timeSeries",
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
