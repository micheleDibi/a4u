import type { ColumnDef, VisibilityState } from "@tanstack/react-table";
import { SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface Props<TData> {
  columns: ColumnDef<TData, unknown>[];
  value: VisibilityState;
  onChange: (next: VisibilityState) => void;
  /** Etichetta del pulsante; default `courses.list.columnsButton`. */
  label?: string;
}

function columnId<TData>(col: ColumnDef<TData, unknown>): string | undefined {
  if (col.id) return col.id;
  if ("accessorKey" in col && typeof col.accessorKey === "string") {
    return col.accessorKey;
  }
  return undefined;
}

/**
 * Selettore "Colonne": dropdown di checkbox per mostrare/nascondere le
 * colonne di una `DataTable`. Riutilizzabile; itera le colonne con
 * `enableHiding !== false` e usa `column.meta.label` come etichetta.
 */
export function DataTableColumnToggle<TData>({
  columns,
  value,
  onChange,
  label,
}: Props<TData>) {
  const { t } = useTranslation();
  const buttonLabel = label ?? t("courses.list.columnsButton");

  const hideable = columns.filter(
    (c) => c.enableHiding !== false && !!columnId(c),
  );

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="outline" size="sm" className="h-9">
          <SlidersHorizontal className="size-4" />
          {buttonLabel}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuLabel>{buttonLabel}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {hideable.map((col) => {
          const id = columnId(col)!;
          const colLabel = col.meta?.label ?? id;
          return (
            <DropdownMenuCheckboxItem
              key={id}
              checked={value[id] !== false}
              // Evita la chiusura del menu ad ogni toggle (multi-selezione).
              onSelect={(e) => e.preventDefault()}
              onCheckedChange={(next) => onChange({ ...value, [id]: next })}
            >
              {colLabel}
            </DropdownMenuCheckboxItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
