import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface Props {
  title: string;
  readyCount: number;
  totalCount: number;
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}

/**
 * Sezione modulo collassabile per i tab media. Header cliccabile con
 * etichetta "Modulo N · Titolo", contatore "X/Y pronti" e chevron; il
 * contenuto (righe lista o card griglia) viene nascosto quando chiuso.
 * Niente codice tecnico `M1`.
 */
export function MediaModuleSection({
  title,
  readyCount,
  totalCount,
  collapsed,
  onToggle,
  children,
}: Props) {
  const { t } = useTranslation();
  const allReady = readyCount === totalCount && totalCount > 0;

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!collapsed}
        className="flex w-full items-center gap-2 rounded-md px-1 py-1 text-left transition-colors hover:bg-muted/50"
      >
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            collapsed && "-rotate-90",
          )}
        />
        <h3 className="flex-1 truncate text-sm font-semibold">{title}</h3>
        <Badge
          variant={allReady ? "default" : "secondary"}
          className="shrink-0 font-normal"
        >
          {t("courses.media.readyCount", {
            ready: readyCount,
            total: totalCount,
          })}
        </Badge>
      </button>
      {!collapsed && children}
    </div>
  );
}
