import { Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  PERMISSION_CATEGORIES,
  ROLE_DEFAULT_PERMISSIONS,
  type PermissionCode,
  type RoleCode,
} from "@/lib/permissions";
import { cn } from "@/lib/utils";

interface Props {
  roleCode: RoleCode;
  /** Se vero usa una variante più compatta (densità maggiore, font ridotto). */
  compact?: boolean;
  className?: string;
}

/**
 * Riassunto delle capacità predefinite di un ruolo, raggruppato per area.
 * Permessi concessi → check verde. Permessi non concessi → X grigia
 * barrata.
 *
 * Note:
 * - Mostra la **configurazione di default** del ruolo (definita in
 *   `lib/permissions.ts`, mirror del BE). Non riflette eventuali override
 *   per organizzazione né per membership — di cui si può occupare
 *   l'amministratore nella sezione "Permessi" dell'organizzazione.
 * - Usato nei dialog di invito + cambio ruolo per dare all'utente un
 *   colpo d'occhio su cosa concede / non concede il ruolo selezionato.
 */
export function RolePermissionsBox({ roleCode, compact, className }: Props) {
  const { t } = useTranslation();
  const granted: ReadonlySet<PermissionCode> = new Set(
    ROLE_DEFAULT_PERMISSIONS[roleCode],
  );

  return (
    <div
      className={cn(
        "rounded-md border border-border bg-muted/30 p-3 text-sm",
        className,
      )}
    >
      <p className={cn("font-medium", compact ? "text-xs" : "text-sm")}>
        {t("roles.capabilitiesTitle", {
          role: t(`roles.${roleCode}`),
        })}
      </p>
      <p
        className={cn(
          "mt-0.5 text-muted-foreground",
          compact ? "text-[11px]" : "text-xs",
        )}
      >
        {t("roles.capabilitiesHelp")}
      </p>
      <div className={cn("mt-3 space-y-3", compact && "mt-2 space-y-2")}>
        {PERMISSION_CATEGORIES.map((cat) => (
          <div key={cat.key}>
            <p
              className={cn(
                "text-xs font-semibold uppercase tracking-wide text-muted-foreground",
                compact && "text-[10px]",
              )}
            >
              {t(`permissionCategories.${cat.key}`)}
            </p>
            <ul className="mt-1 space-y-0.5">
              {cat.permissions.map((perm) => {
                const has = granted.has(perm);
                return (
                  <li
                    key={perm}
                    className={cn(
                      "flex items-start gap-2",
                      compact ? "text-[11px]" : "text-xs",
                      has
                        ? "text-foreground"
                        : "text-muted-foreground/70 line-through",
                    )}
                  >
                    {has ? (
                      <Check className="mt-0.5 size-3 shrink-0 text-green-600" />
                    ) : (
                      <X className="mt-0.5 size-3 shrink-0 text-muted-foreground/60" />
                    )}
                    <span>{t(`permissions.${perm}`)}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
