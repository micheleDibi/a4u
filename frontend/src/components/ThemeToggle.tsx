import { Laptop, Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { useTheme, type Theme } from "@/providers/ThemeProvider";

export function ThemeToggle({ size = "icon" }: { size?: "icon" | "sm" | "default" }) {
  const { theme, setTheme } = useTheme();
  const { t } = useTranslation();

  const items: { value: Theme; icon: React.ReactNode; label: string }[] = [
    { value: "light", icon: <Sun className="size-4" />, label: t("theme.light") },
    { value: "dark", icon: <Moon className="size-4" />, label: t("theme.dark") },
    { value: "system", icon: <Laptop className="size-4" />, label: t("theme.system") },
  ];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size={size} aria-label={t("theme.label")}>
          <Sun className="size-4 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
          <Moon className="absolute size-4 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
          <span className="sr-only">{t("theme.label")}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {items.map((it) => (
          <DropdownMenuItem
            key={it.value}
            onSelect={() => setTheme(it.value)}
            className={theme === it.value ? "bg-accent" : ""}
          >
            {it.icon}
            <span>{it.label}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
