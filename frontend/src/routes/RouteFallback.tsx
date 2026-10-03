import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

/**
 * Segnaposto mostrato dal `Suspense` del router mentre arriva il chunk di
 * una pagina caricata in modo pigro. Dentro `AppLayout` occupa solo l'area
 * del contenuto con un'altezza minima fissa, così barra laterale e intestazione
 * non si spostano; `fullScreen` è per le pagine fuori dal layout (invito),
 * con lo stesso aspetto dello spinner di `ProtectedRoute`.
 */
export function RouteFallback({ fullScreen = false }: { fullScreen?: boolean }) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("grid place-items-center", fullScreen ? "min-h-screen" : "min-h-[50vh]")}
    >
      <Loader2 aria-hidden="true" className="size-6 animate-spin text-muted-foreground" />
      <span className="sr-only">{t("common.loading")}</span>
    </div>
  );
}

export default RouteFallback;
