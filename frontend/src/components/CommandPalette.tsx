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
  UserPlus,
  Users,
  UsersRound,
  type LucideIcon,
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
import { useNavigate, type NavigateFunction } from "react-router-dom";
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
import { P, type PermissionCode } from "@/lib/permissions";

// ---------------------------------------------------------------------------
// Provider + listener globale ⌘K / Ctrl+K. Sta FUORI dal <RouterProvider>
// (non usa hook del router); l'UI vera del dialog è in CommandPaletteDialog
// e va montata dentro AppLayout.
// ---------------------------------------------------------------------------

interface PaletteContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
}

const PaletteContext = createContext<PaletteContextValue | undefined>(undefined);

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

// ---------------------------------------------------------------------------
// Registry dichiarativo dei comandi.
//
// Per aggiungere un comando: append a `COMMANDS` con le sue condizioni di
// visibilità (requirePlatformAdmin / requireOrgId / requirePermission). Il
// rendering è generico.
// ---------------------------------------------------------------------------

type CommandGroupId = "navigation" | "actions" | "preferences";
type ThemeName = "light" | "dark" | "system";

interface CommandContext {
  navigate: NavigateFunction;
  setTheme: (theme: ThemeName) => void;
  orgId: string | null;
}

interface CommandEntry {
  id: string;
  group: CommandGroupId;
  /** i18n key del label visibile. */
  labelKey: string;
  /** i18n key opzionale del hint a destra (es. "Personal", "Platform"). */
  shortcutKey?: string;
  icon: LucideIcon;
  action: (ctx: CommandContext) => void;
  /** Solo platform admin (`me.is_platform_admin`). */
  requirePlatformAdmin?: boolean;
  /** Serve un'org corrente (`orgId` valorizzato). */
  requireOrgId?: boolean;
  /** Permesso org-scoped richiesto. Platform admin lo bypassa. */
  requirePermission?: PermissionCode;
}

const COMMANDS: CommandEntry[] = [
  // -- Platform admin: navigation ----------------------------------------
  {
    id: "admin.dashboard",
    group: "navigation",
    labelKey: "nav.dashboard",
    shortcutKey: "nav.platform",
    icon: LayoutDashboard,
    requirePlatformAdmin: true,
    action: ({ navigate }) => navigate("/admin"),
  },
  {
    id: "admin.organizations",
    group: "navigation",
    labelKey: "nav.organizations",
    icon: Building2,
    requirePlatformAdmin: true,
    action: ({ navigate }) => navigate("/admin/organizations"),
  },
  {
    id: "admin.users",
    group: "navigation",
    labelKey: "nav.users",
    icon: UsersRound,
    requirePlatformAdmin: true,
    action: ({ navigate }) => navigate("/admin/users"),
  },
  {
    id: "admin.permissions",
    group: "navigation",
    labelKey: "nav.permissions",
    icon: LockKeyhole,
    requirePlatformAdmin: true,
    action: ({ navigate }) => navigate("/admin/permissions"),
  },
  {
    id: "admin.i18n",
    group: "navigation",
    labelKey: "nav.i18n",
    icon: Globe,
    requirePlatformAdmin: true,
    action: ({ navigate }) => navigate("/admin/i18n"),
  },
  {
    id: "admin.avatarConfig",
    group: "navigation",
    labelKey: "command.navAdminAvatarConfig",
    icon: Sliders,
    requirePlatformAdmin: true,
    action: ({ navigate }) => navigate("/admin/configurazioni/avatar"),
  },
  {
    id: "admin.taxonomies",
    group: "navigation",
    labelKey: "command.navAdminTaxonomies",
    icon: FolderTree,
    requirePlatformAdmin: true,
    action: ({ navigate }) => navigate("/admin/configurazioni/tassonomie"),
  },

  // -- Org: navigation ---------------------------------------------------
  {
    id: "org.dashboard",
    group: "navigation",
    labelKey: "nav.organization",
    icon: PaintbrushVertical,
    requireOrgId: true,
    action: ({ navigate, orgId }) => navigate(`/orgs/${orgId}`),
  },
  {
    id: "org.members",
    group: "navigation",
    labelKey: "nav.members",
    icon: Users,
    requireOrgId: true,
    requirePermission: P.MEMBER_VIEW,
    action: ({ navigate, orgId }) => navigate(`/orgs/${orgId}/members`),
  },
  {
    id: "org.courses",
    group: "navigation",
    labelKey: "nav.courses",
    icon: BookOpenCheck,
    requireOrgId: true,
    requirePermission: P.COURSE_VIEW,
    action: ({ navigate, orgId }) => navigate(`/orgs/${orgId}/corsi`),
  },
  {
    id: "org.courseSettings",
    group: "navigation",
    labelKey: "nav.courseSettings",
    icon: GraduationCap,
    requireOrgId: true,
    requirePermission: P.COURSE_CONFIG_MANAGE,
    action: ({ navigate, orgId }) =>
      navigate(`/orgs/${orgId}/configurazioni/corsi`),
  },
  {
    id: "org.templatesSlide",
    group: "navigation",
    labelKey: "nav.templatesSlide",
    icon: Presentation,
    requireOrgId: true,
    requirePermission: P.TEMPLATE_SLIDE_MANAGE,
    action: ({ navigate, orgId }) =>
      navigate(`/orgs/${orgId}/templates/slide`),
  },
  {
    id: "org.templatesPdf",
    group: "navigation",
    labelKey: "nav.templatesPdf",
    icon: ScrollText,
    requireOrgId: true,
    requirePermission: P.TEMPLATE_PDF_MANAGE,
    action: ({ navigate, orgId }) => navigate(`/orgs/${orgId}/templates/pdf`),
  },

  // -- Personal ----------------------------------------------------------
  {
    id: "me.avatar",
    group: "navigation",
    labelKey: "user.myAvatar",
    shortcutKey: "nav.personal",
    icon: Smile,
    action: ({ navigate }) => navigate("/me/avatar"),
  },

  // -- Quick actions (org-scoped) ---------------------------------------
  {
    id: "action.newCourse",
    group: "actions",
    labelKey: "command.newCourse",
    icon: BookOpenCheck,
    requireOrgId: true,
    requirePermission: P.COURSE_CREATE,
    action: ({ navigate, orgId }) => navigate(`/orgs/${orgId}/corsi/nuovo`),
  },
  {
    id: "action.inviteMember",
    group: "actions",
    labelKey: "command.inviteMember",
    icon: UserPlus,
    requireOrgId: true,
    requirePermission: P.MEMBER_INVITE,
    action: ({ navigate, orgId }) =>
      navigate(`/orgs/${orgId}/members?invite=1`),
  },
  {
    id: "action.newSlideTemplate",
    group: "actions",
    labelKey: "command.newSlideTemplate",
    icon: Presentation,
    requireOrgId: true,
    requirePermission: P.TEMPLATE_SLIDE_MANAGE,
    action: ({ navigate, orgId }) =>
      navigate(`/orgs/${orgId}/templates/slide/new`),
  },
  {
    id: "action.newPdfTemplate",
    group: "actions",
    labelKey: "command.newPdfTemplate",
    icon: ScrollText,
    requireOrgId: true,
    requirePermission: P.TEMPLATE_PDF_MANAGE,
    action: ({ navigate, orgId }) =>
      navigate(`/orgs/${orgId}/templates/pdf/new`),
  },

  // -- Quick actions (platform admin) -----------------------------------
  {
    id: "action.newOrganization",
    group: "actions",
    labelKey: "command.newOrganization",
    icon: Plus,
    requirePlatformAdmin: true,
    action: ({ navigate }) => navigate("/admin/organizations/new"),
  },

  // -- Preferences: theme -----------------------------------------------
  {
    id: "theme.light",
    group: "preferences",
    labelKey: "theme.light",
    icon: Sun,
    action: ({ setTheme }) => setTheme("light"),
  },
  {
    id: "theme.dark",
    group: "preferences",
    labelKey: "theme.dark",
    icon: Moon,
    action: ({ setTheme }) => setTheme("dark"),
  },
  {
    id: "theme.system",
    group: "preferences",
    labelKey: "theme.system",
    icon: Laptop,
    action: ({ setTheme }) => setTheme("system"),
  },
];

// ---------------------------------------------------------------------------
// UI del dialog. Usa `useNavigate`, va dentro <RouterProvider>.
// ---------------------------------------------------------------------------

export function CommandPaletteDialog() {
  const { open, setOpen } = useCommandPalette();
  const navigate = useNavigate();
  const { me } = useAuth();
  const { setTheme } = useTheme();
  const { t, i18n } = useTranslation();
  const orgId = useEffectiveOrgId();
  const langs = useLanguages();

  const close = useCallback(() => setOpen(false), [setOpen]);

  // Permessi org-scoped dell'utente nell'org corrente. Platform admin li
  // bypassa (vedi `visible()` sotto).
  const isPlatformAdmin = me?.is_platform_admin ?? false;
  const orgPermissions = useMemo(() => {
    if (!orgId || !me) return new Set<string>();
    const org = me.organizations.find((o) => o.organization_id === orgId);
    return new Set(org?.permissions ?? []);
  }, [me, orgId]);

  const visible = useCallback(
    (cmd: CommandEntry): boolean => {
      if (cmd.requirePlatformAdmin && !isPlatformAdmin) return false;
      if (cmd.requireOrgId && !orgId) return false;
      if (cmd.requirePermission) {
        if (isPlatformAdmin) return true;
        return orgPermissions.has(cmd.requirePermission);
      }
      return true;
    },
    [isPlatformAdmin, orgId, orgPermissions],
  );

  const byGroup = useMemo(() => {
    const groups: Record<CommandGroupId, CommandEntry[]> = {
      navigation: [],
      actions: [],
      preferences: [],
    };
    for (const c of COMMANDS) {
      if (visible(c)) groups[c.group].push(c);
    }
    return groups;
  }, [visible]);

  const ctx: CommandContext = useMemo(
    () => ({ navigate, setTheme, orgId: orgId ?? null }),
    [navigate, setTheme, orgId],
  );

  const runCommand = (cmd: CommandEntry) => () => {
    cmd.action(ctx);
    close();
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title={t("command.openCommandPalette")}
    >
      <CommandInput placeholder={t("command.placeholder")} />
      <CommandList>
        <CommandEmpty>{t("command.noResults")}</CommandEmpty>

        {byGroup.navigation.length > 0 && (
          <CommandGroup heading={t("command.navigation")}>
            {byGroup.navigation.map((c) => (
              <CommandItem
                key={c.id}
                value={`${c.id} ${t(c.labelKey)}`}
                onSelect={runCommand(c)}
              >
                <c.icon className="size-4" />
                <span>{t(c.labelKey)}</span>
                {c.shortcutKey && (
                  <CommandShortcut>{t(c.shortcutKey)}</CommandShortcut>
                )}
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        {byGroup.actions.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading={t("command.actions")}>
              {byGroup.actions.map((c) => (
                <CommandItem
                  key={c.id}
                  value={`${c.id} ${t(c.labelKey)}`}
                  onSelect={runCommand(c)}
                >
                  <c.icon className="size-4" />
                  <span>{t(c.labelKey)}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}

        {byGroup.preferences.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading={t("command.preferences")}>
              {byGroup.preferences.map((c) => (
                <CommandItem
                  key={c.id}
                  value={`${c.id} ${t(c.labelKey)}`}
                  onSelect={runCommand(c)}
                >
                  <c.icon className="size-4" />
                  <span>{t(c.labelKey)}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}

        <CommandSeparator />
        <CommandGroup heading={t("command.switchLanguage")}>
          {langs.map((l) => {
            const Flag = flagFor(l.code, l.flag_country_code);
            return (
              <CommandItem
                key={l.code}
                onSelect={() => {
                  void i18n.changeLanguage(l.code);
                  close();
                }}
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
