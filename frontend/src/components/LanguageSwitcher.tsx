import { useTranslation } from "react-i18next";
import { flagFor } from "@/i18n/flags";
import { useLanguages } from "@/hooks/useLanguages";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { ScrollArea } from "./ui/scroll-area";

export function LanguageSwitcher({ size = "icon" }: { size?: "icon" | "sm" | "default" }) {
  const { i18n, t } = useTranslation();
  const langs = useLanguages();
  // Match a 2 livelli per gestire locale browser estese (es. `zh-CN`) contro
  // codici DB corti (`zh`): prima exact, poi short code.
  const currentCode = i18n.resolvedLanguage || i18n.language || "it";
  const shortCode = currentCode.split("-")[0];
  const currentLang =
    langs.find((l) => l.code === currentCode) ??
    langs.find((l) => l.code === shortCode);
  const CurrentFlag = flagFor(currentCode, currentLang?.flag_country_code);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size={size} aria-label={t("language.label")}>
          <CurrentFlag className="size-4 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
          <span className="sr-only">{t("language.label")}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="p-0">
        <ScrollArea className="h-72 w-60">
          <div className="p-1">
            {langs.map((l) => {
              const active = i18n.resolvedLanguage === l.code;
              const Flag = flagFor(l.code, l.flag_country_code);
              return (
                <DropdownMenuItem
                  key={l.code}
                  onSelect={() => i18n.changeLanguage(l.code)}
                  className={active ? "bg-accent font-medium" : ""}
                >
                  <Flag className="size-4 shrink-0 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
                  <span className="text-xs uppercase tracking-wider text-muted-foreground">
                    {l.code}
                  </span>
                  <span className="truncate">{l.name_native}</span>
                </DropdownMenuItem>
              );
            })}
          </div>
        </ScrollArea>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
