import {
  BookOpenCheck,
  Building2,
  FolderTree,
  Globe,
  GraduationCap,
  Laptop,
  LayoutDashboard,
  LockKeyhole,
  Moon,
  PaintbrushVertical,
  Plus,
  Presentation,
  ScrollText,
  Sliders,
  Smile,
  Sun,
  Users,
  UsersRound,
} from "lucide-react";
import { flagFor } from "@/i18n/flags";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { useTheme } from "@/providers/ThemeProvider";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { useEffectiveOrgId } from "@/hooks/useEffectiveOrgId";
import { useLanguages } from "@/hooks/useLanguages";

interface PaletteContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
}

const PaletteContext = createContext<PaletteContextValue | undefined>(undefined);

/**
 * Provider con stato + listener globale ⌘K. NON usa hook del router,
 * quindi può stare ANCHE fuori dal <RouterProvider>.
 * L'UI vera del dialog è in <CommandPaletteDialog />, montata dentro
 * AppLayout (= dentro il router).
 */
export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const value = useMemo(() => ({ open, setOpen }), [open]);

  return <PaletteContext.Provider value={value}>{children}</PaletteContext.Provider>;
}

export function useCommandPalette() {
  const ctx = useContext(PaletteContext);
  if (!ctx) {
    return { open: false, setOpen: () => undefined };
  }
  return ctx;
}

/**
 * UI del dialog. Usa hook del router (useNavigate, useParams), quindi
 * va montato DENTRO il <RouterProvider> (vedi AppLayout).
 */
export function CommandPaletteDialog() {
  const { open, setOpen } = useCommandPalette();
  const navigate = useNavigate();
  const { me } = useAuth();
  const { setTheme } = useTheme();
  const { t, i18n } = useTranslation();
  const orgId = useEffectiveOrgId();
  const langs = useLanguages();

  const close = useCallback(() => setOpen(false), [setOpen]);
  const run = (fn: () => void) => () => {
    fn();
    close();
  };

  return (
    <CommandDialog open={open} onOpenChange={setOpen} title={t("command.openCommandPalette")}>
      <CommandInput placeholder={t("command.placeholder")} />
      <CommandList>
        <CommandEmpty>{t("command.noResults")}</CommandEmpty>
        <CommandGroup heading={t("command.navigation")}>
          {me?.is_platform_admin && (
            <>
              <CommandItem onSelect={run(() => navigate("/admin"))}>
                <LayoutDashboard className="size-4" />
                <span>{t("nav.dashboard")}</span>
                <CommandShortcut>{t("nav.platform")}</CommandShortcut>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate("/admin/organizations"))}>
                <Building2 className="size-4" />
                <span>{t("nav.organizations")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate("/admin/users"))}>
                <UsersRound className="size-4" />
                <span>{t("nav.users")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate("/admin/permissions"))}>
                <LockKeyhole className="size-4" />
                <span>{t("nav.permissions")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate("/admin/i18n"))}>
                <Globe className="size-4" />
                <span>{t("nav.i18n")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate("/admin/configurazioni/avatar"))}>
                <Sliders className="size-4" />
                <span>{t("nav.configurazioni")} — {t("nav.avatarConfig")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate("/admin/configurazioni/tassonomie"))}>
                <FolderTree className="size-4" />
                <span>{t("nav.configurazioni")} — {t("nav.taxonomies")}</span>
              </CommandItem>
            </>
          )}
          {orgId && (
            <>
              <CommandItem onSelect={run(() => navigate(`/orgs/${orgId}`))}>
                <PaintbrushVertical className="size-4" />
                <span>{t("nav.organization")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate(`/orgs/${orgId}/members`))}>
                <Users className="size-4" />
                <span>{t("nav.members")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate(`/orgs/${orgId}/corsi`))}>
                <BookOpenCheck className="size-4" />
                <span>{t("nav.courses")}</span>
              </CommandItem>
              <CommandItem
                onSelect={run(() =>
                  navigate(`/orgs/${orgId}/configurazioni/corsi`)
                )}
              >
                <GraduationCap className="size-4" />
                <span>{t("nav.courseSettings")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate(`/orgs/${orgId}/templates/slide`))}>
                <Presentation className="size-4" />
                <span>{t("nav.templatesSlide")}</span>
              </CommandItem>
              <CommandItem onSelect={run(() => navigate(`/orgs/${orgId}/templates/pdf`))}>
                <ScrollText className="size-4" />
                <span>{t("nav.templatesPdf")}</span>
              </CommandItem>
            </>
          )}
          {me && (
            <CommandItem onSelect={run(() => navigate("/me/avatar"))}>
              <Smile className="size-4" />
              <span>{t("user.myAvatar")}</span>
              <CommandShortcut>{t("nav.personal")}</CommandShortcut>
            </CommandItem>
          )}
        </CommandGroup>
        {me?.is_platform_admin && (
          <>
            <CommandSeparator />
            <CommandGroup heading={t("command.actions")}>
              <CommandItem onSelect={run(() => navigate("/admin/organizations/new"))}>
                <Plus className="size-4" />
                <span>{t("command.newOrganization")}</span>
              </CommandItem>
            </CommandGroup>
          </>
        )}
        <CommandSeparator />
        <CommandGroup heading={t("command.preferences")}>
          <CommandItem onSelect={run(() => setTheme("light"))}>
            <Sun className="size-4" />
            <span>{t("theme.light")}</span>
          </CommandItem>
          <CommandItem onSelect={run(() => setTheme("dark"))}>
            <Moon className="size-4" />
            <span>{t("theme.dark")}</span>
          </CommandItem>
          <CommandItem onSelect={run(() => setTheme("system"))}>
            <Laptop className="size-4" />
            <span>{t("theme.system")}</span>
          </CommandItem>
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading={t("command.switchLanguage")}>
          {langs.map((l) => {
            const Flag = flagFor(l.code, l.flag_country_code);
            return (
              <CommandItem
                key={l.code}
                onSelect={run(() => i18n.changeLanguage(l.code))}
              >
                <Flag className="size-4 shrink-0 rounded-sm shadow-[0_0_0_1px_rgba(0,0,0,0.08)]" />
                <span className="text-xs uppercase tracking-wider text-muted-foreground me-1">
                  {l.code}
                </span>
                <span>{l.name_native}</span>
              </CommandItem>
            );
          })}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
