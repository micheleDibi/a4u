import "@tanstack/react-table";
import type { RowData } from "@tanstack/react-table";

declare module "@tanstack/react-table" {
  // Augment del `meta` di colonna: etichetta leggibile usata dal selettore
  // colonne (`DataTableColumnToggle`). Serve perché alcuni header sono
  // render-function e non testo semplice.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    label?: string;
  }
}
