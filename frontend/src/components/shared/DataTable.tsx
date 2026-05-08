import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
} from "@tanstack/react-table";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export interface DataTableProps<TData> {
  columns: ColumnDef<TData, unknown>[];
  data: TData[];
  loading?: boolean;
  rowCount?: number;
  pagination?: PaginationState;
  onPaginationChange?: (next: PaginationState) => void;
  emptyMessage?: string;
  rowKey?: (row: TData, idx: number) => string;
}

export function DataTable<TData>({
  columns,
  data,
  loading,
  rowCount,
  pagination,
  onPaginationChange,
  emptyMessage,
  rowKey,
}: DataTableProps<TData>) {
  const { t } = useTranslation();
  const isServerPag = !!pagination && !!onPaginationChange;

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: isServerPag,
    rowCount: isServerPag ? rowCount : undefined,
    state: { pagination: pagination ?? { pageIndex: 0, pageSize: data.length || 10 } },
    onPaginationChange: isServerPag
      ? (updater) => {
          const next =
            typeof updater === "function" ? updater(pagination!) : updater;
          onPaginationChange!(next);
        }
      : undefined,
  });

  const totalPages = isServerPag
    ? Math.max(1, Math.ceil((rowCount ?? 0) / pagination!.pageSize))
    : 1;

  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((h) => (
                <TableHead key={h.id} style={h.column.columnDef.size ? { width: h.column.columnDef.size } : undefined}>
                  {h.isPlaceholder
                    ? null
                    : flexRender(h.column.columnDef.header, h.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {loading && data.length === 0
            ? Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={`s-${i}`}>
                  {columns.map((_c, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : table.getRowModel().rows.length === 0
            ? (
                <TableRow>
                  <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
                    {emptyMessage ?? t("common.noResults")}
                  </TableCell>
                </TableRow>
              )
            : table.getRowModel().rows.map((row, idx) => (
                <TableRow
                  key={rowKey ? rowKey(row.original, idx) : row.id}
                  data-state={row.getIsSelected() ? "selected" : undefined}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
        </TableBody>
      </Table>
      {isServerPag && (
        <div className="flex items-center justify-between gap-2 border-t border-border p-2 text-xs text-muted-foreground">
          <span>
            {(pagination!.pageIndex * pagination!.pageSize + 1).toLocaleString()}–
            {Math.min(
              (pagination!.pageIndex + 1) * pagination!.pageSize,
              rowCount ?? 0
            ).toLocaleString()}{" "}
            {t("common.of")} {(rowCount ?? 0).toLocaleString()}
          </span>
          <div className="flex items-center gap-1">
            <Button
              size="icon"
              variant="ghost"
              disabled={pagination!.pageIndex === 0}
              onClick={() =>
                onPaginationChange!({
                  ...pagination!,
                  pageIndex: pagination!.pageIndex - 1,
                })
              }
            >
              <ChevronLeft className="size-4" />
            </Button>
            <span className="px-1">
              {pagination!.pageIndex + 1} / {totalPages}
            </span>
            <Button
              size="icon"
              variant="ghost"
              disabled={pagination!.pageIndex >= totalPages - 1}
              onClick={() =>
                onPaginationChange!({
                  ...pagination!,
                  pageIndex: pagination!.pageIndex + 1,
                })
              }
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
