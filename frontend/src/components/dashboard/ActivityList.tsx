import {
  Activity,
  AlertTriangle,
  BookOpenCheck,
  Building2,
  FileText,
  LockKeyhole,
  LogIn,
  Mail,
  Palette,
  Settings2,
  Smile,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { formatTimeAgo } from "@/lib/formatTimeAgo";

export interface ActivityEntry {
  id: string;
  created_at: string;
  action: string;
  actor_user_name: string | null;
  organization_name: string | null;
  target_type?: string | null;
}

interface ActivityListProps {
  items: ActivityEntry[];
  /** Mostrato quando non ci sono eventi. */
  emptyLabel?: string;
  /** Massimo numero di righe renderizzate. */
  maxItems?: number;
}

/**
 * Mappa il prefisso dell'azione audit a un'icona indicativa.
 * Le action codes sono in `docs/database/schema.md` (sezione `audit_logs`).
 */
function iconForAction(action: string): LucideIcon {
  if (action.startsWith("auth.")) return LogIn;
  if (action.startsWith("organization.")) return Building2;
  if (action.startsWith("membership.")) return Users;
  if (action.startsWith("invitation.")) return Mail;
  if (action.startsWith("permission.")) return LockKeyhole;
  if (action.startsWith("template.")) return Palette;
  if (action.startsWith("avatar_config.")) return Settings2;
  if (action.startsWith("avatar.")) return Smile;
  if (action.startsWith("course.lesson")) return BookOpenCheck;
  if (action.startsWith("course.")) return BookOpenCheck;
  if (action.startsWith("i18n.")) return Activity;
  if (action.endsWith(".failed") || action.endsWith(".failure"))
    return AlertTriangle;
  return FileText;
}

/**
 * Color tone per il chip dell'icona, basato sul tipo di azione.
 * "failure"/"failed" → rosso, "create"/"login.success" → verde, altri → muted.
 */
function toneForAction(action: string): { bg: string; fg: string } {
  if (action.endsWith(".failure") || action.endsWith(".failed")) {
    return { bg: "bg-red-100 dark:bg-red-950", fg: "text-red-600 dark:text-red-400" };
  }
  if (action === "auth.refresh.reuse_detected") {
    return { bg: "bg-amber-100 dark:bg-amber-950", fg: "text-amber-700 dark:text-amber-400" };
  }
  if (
    action.endsWith(".create") ||
    action === "auth.login.success" ||
    action === "invitation.accept"
  ) {
    return { bg: "bg-emerald-100 dark:bg-emerald-950", fg: "text-emerald-700 dark:text-emerald-400" };
  }
  return { bg: "bg-muted", fg: "text-foreground" };
}

export function ActivityList({
  items,
  emptyLabel,
  maxItems,
}: ActivityListProps) {
  const { i18n } = useTranslation();
  const lang = i18n.language;
  const list = maxItems !== undefined ? items.slice(0, maxItems) : items;

  if (list.length === 0) {
    return (
      <div className="py-6 text-center text-sm text-muted-foreground">
        {emptyLabel ?? "—"}
      </div>
    );
  }

  return (
    <ul className="divide-y divide-border">
      {list.map((it) => {
        const Icon = iconForAction(it.action);
        const tone = toneForAction(it.action);
        return (
          <li key={it.id} className="flex items-start gap-3 py-2.5">
            <div
              className={`grid size-7 shrink-0 place-items-center rounded-md ${tone.bg} ${tone.fg}`}
            >
              <Icon className="size-3.5" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{it.action}</div>
              <div className="truncate text-xs text-muted-foreground">
                {[it.actor_user_name, it.organization_name]
                  .filter(Boolean)
                  .join(" — ") || "—"}
              </div>
            </div>
            <div
              className="shrink-0 text-xs tabular-nums text-muted-foreground"
              title={new Date(it.created_at).toLocaleString()}
            >
              {formatTimeAgo(it.created_at, lang)}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
