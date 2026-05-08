import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface TableEditorProps {
  value: string;
  onChange: (markdown: string) => void;
  disabled?: boolean;
  className?: string;
}

interface TableState {
  headers: string[];
  rows: string[][];
}

const DEFAULT_STATE: TableState = {
  headers: ["Colonna 1", "Colonna 2"],
  rows: [
    ["", ""],
    ["", ""],
  ],
};

export function TableEditor({
  value,
  onChange,
  disabled = false,
  className,
}: TableEditorProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<TableState>(() =>
    parseMarkdownTable(value) ?? DEFAULT_STATE,
  );
  const lastEmittedRef = useRef<string | null>(null);

  // Se il `value` cambia dall'esterno (es. dialog riaperto), riallinea lo stato
  // ma evita di sovrascrivere edit locali dovuti al nostro stesso emit.
  useEffect(() => {
    if (value === lastEmittedRef.current) return;
    const parsed = parseMarkdownTable(value);
    if (parsed) {
      setState(parsed);
    } else if (!value.trim()) {
      setState(DEFAULT_STATE);
    }
  }, [value]);

  const emitChange = (next: TableState) => {
    setState(next);
    const md = serializeMarkdownTable(next);
    lastEmittedRef.current = md;
    onChange(md);
  };

  const setHeader = (col: number, val: string) => {
    const next: TableState = {
      headers: state.headers.map((h, i) => (i === col ? val : h)),
      rows: state.rows,
    };
    emitChange(next);
  };

  const setCell = (row: number, col: number, val: string) => {
    const next: TableState = {
      headers: state.headers,
      rows: state.rows.map((r, i) =>
        i === row ? r.map((c, j) => (j === col ? val : c)) : r,
      ),
    };
    emitChange(next);
  };

  const addColumn = () => {
    const next: TableState = {
      headers: [...state.headers, `Colonna ${state.headers.length + 1}`],
      rows: state.rows.map((r) => [...r, ""]),
    };
    emitChange(next);
  };

  const removeColumn = (col: number) => {
    if (state.headers.length <= 1) return;
    const next: TableState = {
      headers: state.headers.filter((_, i) => i !== col),
      rows: state.rows.map((r) => r.filter((_, i) => i !== col)),
    };
    emitChange(next);
  };

  const addRow = () => {
    const next: TableState = {
      headers: state.headers,
      rows: [...state.rows, state.headers.map(() => "")],
    };
    emitChange(next);
  };

  const removeRow = (row: number) => {
    if (state.rows.length <= 1) return;
    const next: TableState = {
      headers: state.headers,
      rows: state.rows.filter((_, i) => i !== row),
    };
    emitChange(next);
  };

  return (
    <div
      className={cn(
        "rounded-md border bg-background",
        disabled && "pointer-events-none opacity-60",
        className,
      )}
    >
      <div className="overflow-x-auto p-2">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              {state.headers.map((h, col) => (
                <th
                  key={col}
                  className="border border-border bg-muted/40 p-1 align-top"
                >
                  <div className="flex items-center gap-1">
                    <input
                      className="w-full bg-transparent px-1 py-0.5 font-semibold focus:outline-none"
                      value={h}
                      onChange={(e) => setHeader(col, e.target.value)}
                      disabled={disabled}
                      placeholder={`Col ${col + 1}`}
                    />
                    <button
                      type="button"
                      title={t("courses.lessonsContent.editorUI.table.removeColumn")}
                      onClick={() => removeColumn(col)}
                      disabled={disabled || state.headers.length <= 1}
                      className="inline-flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-30"
                    >
                      <Trash2 className="size-3" />
                    </button>
                  </div>
                </th>
              ))}
              <th className="w-8 border border-transparent" />
            </tr>
          </thead>
          <tbody>
            {state.rows.map((row, rowIdx) => (
              <tr key={rowIdx}>
                {row.map((cell, colIdx) => (
                  <td
                    key={colIdx}
                    className="border border-border p-0 align-top"
                  >
                    <input
                      className="w-full bg-transparent px-2 py-1 focus:bg-accent/30 focus:outline-none"
                      value={cell}
                      onChange={(e) => setCell(rowIdx, colIdx, e.target.value)}
                      disabled={disabled}
                    />
                  </td>
                ))}
                <td className="w-8 border border-transparent p-0 align-middle">
                  <button
                    type="button"
                    title={t("courses.lessonsContent.editorUI.table.removeRow")}
                    onClick={() => removeRow(rowIdx)}
                    disabled={disabled || state.rows.length <= 1}
                    className="inline-flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-30"
                  >
                    <Trash2 className="size-3" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-2 border-t bg-muted/30 px-2 py-1.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={addColumn}
          disabled={disabled}
        >
          <Plus className="size-3.5" />
          {t("courses.lessonsContent.editorUI.table.addColumn")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={addRow}
          disabled={disabled}
        >
          <Plus className="size-3.5" />
          {t("courses.lessonsContent.editorUI.table.addRow")}
        </Button>
      </div>
    </div>
  );
}

function parseMarkdownTable(md: string): TableState | null {
  const text = (md || "").trim();
  if (!text) return null;
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return null;

  const headerLine = lines[0];
  const sepLine = lines[1];
  if (!headerLine.includes("|")) return null;
  // La riga separatrice deve contenere `---` (eventualmente con `:`)
  if (!/^\|?\s*:?-{2,}/.test(sepLine.replace(/\s/g, ""))) return null;

  const headers = splitMarkdownRow(headerLine);
  const rows: string[][] = [];
  for (let i = 2; i < lines.length; i++) {
    const cells = splitMarkdownRow(lines[i]);
    // Pad/trunc per allinearsi al numero di header
    while (cells.length < headers.length) cells.push("");
    if (cells.length > headers.length) cells.length = headers.length;
    rows.push(cells);
  }
  if (rows.length === 0) {
    rows.push(headers.map(() => ""));
  }
  return { headers, rows };
}

function splitMarkdownRow(line: string): string[] {
  // Rimuove i pipe esterni se presenti, poi split
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s
    .split("|")
    .map((c) => c.trim().replace(/\\\|/g, "|"));
}

function serializeMarkdownTable(state: TableState): string {
  if (state.headers.length === 0) return "";
  const header = `| ${state.headers
    .map((h) => escapeCell(h) || " ")
    .join(" | ")} |`;
  const sep = `| ${state.headers.map(() => "---").join(" | ")} |`;
  const body = state.rows
    .map(
      (r) =>
        `| ${r.map((c) => escapeCell(c) || " ").join(" | ")} |`,
    )
    .join("\n");
  return `${header}\n${sep}\n${body}`;
}

function escapeCell(s: string): string {
  return (s || "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

export default TableEditor;
