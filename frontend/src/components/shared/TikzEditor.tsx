import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { coursesApi } from "@/api/courses";
import { describeApiError } from "@/lib/errors";
import { svgDataUri } from "@/lib/figureFormats";
import { cn } from "@/lib/utils";

import { FIGURE_SURFACE, FigureErrorBox, FigureLoading } from "./FigureFrame";
import { FigureSourceEditor, type SourceTemplate } from "./FigureSourceEditor";

/**
 * Editor di uno schema TikZ (`format="tikz"`, WP6): sorgente + anteprima
 * compilata dal SERVER (`POST /lesson-assets/render-tikz`, XeLaTeX nella
 * sandbox), con debounce più lungo degli editor client (la compilazione
 * costa e l'endpoint ha una quota per utente; gli hit della cache non la
 * consumano). I difetti geometrici della resa (etichette sovrapposte,
 * testo fuori dal riquadro, …) sono avvisi: il salvataggio li accetta, la
 * generazione di Fase 3 no.
 *
 * Il corpo è UN solo ambiente `tikzpicture` o `circuitikz`: preambolo,
 * librerie e colori `a4u*` li mette il server. I modelli sono provati dal
 * test `test_frontend_figure_templates.py` (lexer ovunque, compilazione e
 * geometria con TeX): nei template literal ogni barra è raddoppiata.
 */
interface TikzEditorProps {
  value: string;
  onChange: (code: string) => void;
  orgId: string;
  courseId: string;
  disabled?: boolean;
  className?: string;
  rows?: number;
  /** 422 per asset del PATCH, mostrato in testa. */
  error?: string | null;
}

const PREFIX = "courses.lessonsContent.editorUI.tikz";

const GROUP = {
  blocks: `${PREFIX}.groups.blocks`,
  circuits: `${PREFIX}.groups.circuits`,
} as const;

const TEMPLATES: readonly SourceTemplate[] = [
  // --- Schemi a blocchi ---------------------------------------------------
  {
    id: "chain",
    groupKey: GROUP.blocks,
    labelKey: `${PREFIX}.templates.chain`,
    code: `\\begin{tikzpicture}[node distance=10mm,
  box/.style={draw, rounded corners, minimum height=9mm, fill=a4uC0!12}]
  \\node[box] (s) {Sensore};
  \\node[box, right=of s] (c) {Condizionamento};
  \\node[box, right=of c] (a) {ADC};
  \\node[box, right=of a] (p) {Elaborazione};
  \\draw[->] (s) -- node[above] {$v(t)$} (c);
  \\draw[->] (c) -- (a);
  \\draw[->] (a) -- (p);
\\end{tikzpicture}`,
  },
  {
    id: "vibrometer",
    groupKey: GROUP.blocks,
    labelKey: `${PREFIX}.templates.vibrometer`,
    code: `\\begin{tikzpicture}[node distance=8mm and 12mm,
  box/.style={draw, rounded corners, minimum height=9mm, text width=24mm,
    align=center, fill=a4uC0!10}]
  \\node[box] (laser) {Laser He-Ne};
  \\node[box, right=of laser] (bs) {Divisore di fascio};
  \\node[box, right=of bs] (target) {Superficie vibrante};
  \\node[box, below=of bs] (bragg) {Cella di Bragg};
  \\node[box, below=of target] (det) {Fotorivelatore};
  \\node[box, right=of det] (demod) {Demodulatore};
  \\draw[->] (laser) -- (bs);
  \\draw[->] (bs) -- (target);
  \\draw[->] (bs) -- (bragg);
  \\draw[->] (bragg) -- (det);
  \\draw[->] (target) -- (det);
  \\draw[->] (det) -- (demod);
\\end{tikzpicture}`,
  },
  // --- Circuiti -----------------------------------------------------------
  {
    id: "divider",
    groupKey: GROUP.circuits,
    labelKey: `${PREFIX}.templates.divider`,
    code: `\\begin{circuitikz}
  \\draw (0,0) to[V, l=$V_{in}$] (0,4) -- (2.5,4)
    to[R, l=$R_1$] (2.5,2)
    to[R, l=$R_2$] (2.5,0) -- (0,0);
  \\draw (2.5,2) to[short, -o] (4.5,2) node[right] {$V_{out}$};
  \\draw (2.5,0) to[short, -o] (4.5,0);
\\end{circuitikz}`,
  },
  {
    id: "wheatstone",
    groupKey: GROUP.circuits,
    labelKey: `${PREFIX}.templates.wheatstone`,
    code: `\\begin{circuitikz}
  \\draw (0,4) to[R, l_=$R_1$] (0,2) to[R, l_=$R_3$] (0,0) -- (4,0)
    to[R, l_=$R_4$] (4,2) to[R, l_=$R_2$] (4,4) -- (0,4);
  \\draw (0,2) to[rmeter, t=G] (4,2);
  \\draw (4,4) -- (6,4) to[V, l=$V_s$] (6,0) -- (4,0);
\\end{circuitikz}`,
  },
];

function TikzPreview({
  orgId,
  courseId,
  source,
}: {
  orgId: string;
  courseId: string;
  source: string;
}) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ["tikz-preview", orgId, courseId, source],
    queryFn: () => coursesApi.lessonAssets.renderTikz(orgId, courseId, source),
    staleTime: Infinity,
    gcTime: 10 * 60_000,
    retry: false,
  });
  if (query.isError) {
    return <FigureErrorBox detail={describeApiError(query.error)} />;
  }
  if (!query.data) return <FigureLoading />;
  const { svg, warnings } = query.data;
  return (
    <div className="space-y-2">
      <div className={cn(FIGURE_SURFACE, "flex justify-center")}>
        <img src={svgDataUri(svg)} alt="" className="mx-auto block h-auto max-w-full" />
      </div>
      {warnings.length > 0 && (
        <div className="rounded-md border border-amber-300/60 bg-amber-50/60 px-3 py-2 text-xs text-amber-900 dark:border-amber-700/40 dark:bg-amber-950/30 dark:text-amber-200">
          <div className="font-semibold">{t(`${PREFIX}.warnings`)}</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {warnings.map((warning, i) => (
              <li key={i}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function TikzEditor({
  value,
  onChange,
  orgId,
  courseId,
  disabled = false,
  className,
  rows = 14,
  error,
}: TikzEditorProps) {
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
      placeholder={"\\begin{tikzpicture}\n  \\node[draw] (a) {A};\n\\end{tikzpicture}"}
      renderPreview={(source) => (
        <TikzPreview orgId={orgId} courseId={courseId} source={source} />
      )}
      debounceMs={1200}
    />
  );
}

export default TikzEditor;
