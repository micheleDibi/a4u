import { useTranslation } from "react-i18next";
import { LayoutGrid, List } from "lucide-react";

import { cn } from "@/lib/utils";

import type { MediaViewMode } from "./useMediaView";

interface Props {
  value: MediaViewMode;
  onChange: (next: MediaViewMode) => void;
}

/**
 * Segmented control Lista ↔ Griglia per i tab media. Costruito sui
 * `button` nativi (non esiste una primitive ToggleGroup nel design system).
 */
export function MediaViewToggle({ value, onChange }: Props) {
  const { t } = useTranslation();

  const options: { mode: MediaViewMode; label: string; Icon: typeof List }[] = [
    { mode: "list", label: t("courses.media.viewList"), Icon: List },
    { mode: "grid", label: t("courses.media.viewGrid"), Icon: LayoutGrid },
  ];

  return (
    <div className="inline-flex rounded-md border bg-muted/40 p-0.5">
      {options.map(({ mode, label, Icon }) => (
        <button
          key={mode}
          type="button"
          onClick={() => onChange(mode)}
          aria-pressed={value === mode}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors",
            value === mode
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon className="size-3.5" />
          {label}
        </button>
      ))}
    </div>
  );
}
