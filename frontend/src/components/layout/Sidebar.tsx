import { type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  BookOpenCheck,
  Building2,
  Command as CommandIcon,
  FolderTree,
  Globe,
  GraduationCap,
  LayoutDashboard,
  LockKeyhole,
  PaintbrushVertical,
  Presentation,
  ScrollText,
  Sliders,
  Smile,
  UserCog,
  Users,
  UsersRound,
} from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { useHasPermission } from "@/auth/PermissionGate";
import { useEffectiveOrgId } from "@/hooks/useEffectiveOrgId";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useCommandPalette } from "@/components/CommandPalette";
import { OrgSwitcher } from "./OrgSwitcher";
import { UserMenu } from "./UserMenu";
import { cn } from "@/lib/utils";
import { P } from "@/lib/permissions";

interface NavSpec {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  permission?: string;
  exact?: boolean;
}

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { me } = useAuth();
  const orgId = useEffectiveOrgId();
  const { t } = useTranslation();
  const palette = useCommandPalette();

  const platformNav: NavSpec[] = [
    { to: "/admin", label: t("nav.dashboard"), icon: LayoutDashboard, exact: true },
    { to: "/admin/organizations", label: t("nav.organizations"), icon: Building2 },
    { to: "/admin/users", label: t("nav.users"), icon: UsersRound },
    { to: "/admin/permissions", label: t("nav.permissions"), icon: LockKeyhole },
    { to: "/admin/i18n", label: t("nav.i18n"), icon: Globe },
  ];

  const configNav: NavSpec[] = [
    { to: "/admin/configurazioni/avatar", label: t("nav.avatarConfig"), icon: Sliders },
    { to: "/admin/configurazioni/tassonomie", label: t("nav.taxonomies"), icon: FolderTree },
  ];

  const personalNav: NavSpec[] = [
    { to: "/me/profile", label: t("user.profile"), icon: UserCog },
    { to: "/me/avatar", label: t("user.myAvatar"), icon: Smile },
  ];

  const orgNav: NavSpec[] = orgId
    ? [
        { to: `/orgs/${orgId}`, label: t("nav.dashboard"), icon: LayoutDashboard, exact: true },
        { to: `/orgs/${orgId}/members`, label: t("nav.members"), icon: Users, permission: P.MEMBER_VIEW },
        { to: `/orgs/${orgId}/corsi`, label: t("nav.courses"), icon: BookOpenCheck, permission: P.COURSE_VIEW },
        { to: `/orgs/${orgId}/configurazioni/corsi`, label: t("nav.courseSettings"), icon: GraduationCap, permission: P.COURSE_CONFIG_MANAGE },
        { to: `/orgs/${orgId}/templates/slide`, label: t("nav.templatesSlide"), icon: Presentation, permission: P.TEMPLATE_SLIDE_MANAGE },
        { to: `/orgs/${orgId}/templates/pdf`, label: t("nav.templatesPdf"), icon: ScrollText, permission: P.TEMPLATE_PDF_MANAGE },
      ]
    : [];

  return (
    <aside className="flex h-full flex-col border-e border-sidebar-border bg-sidebar text-sidebar-foreground">
      {/* Header fisso: logo + command palette */}
      <div className="flex flex-col gap-2 p-3 pb-2">
        <div className="flex items-center gap-2 px-2 py-2">
          <div className="grid size-8 place-items-center rounded-md bg-brand text-brand-foreground">
            <PaintbrushVertical className="size-4" />
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold">{t("app.name")}</span>
            <span className="text-[11px] text-sidebar-muted-foreground">
              {t("app.tagline")}
            </span>
          </div>
        </div>

        <button
          type="button"
          onClick={() => palette.setOpen(true)}
          className="flex h-9 w-full items-center justify-between rounded-md border border-sidebar-border bg-sidebar-accent/40 px-3 text-sm text-sidebar-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          <span className="inline-flex items-center gap-2">
            <CommandIcon className="size-4" />
            {t("command.openCommandPalette")}
          </span>
          <kbd className="pointer-events-none rounded border border-sidebar-border bg-sidebar-accent px-1.5 py-0.5 font-mono text-[10px] text-sidebar-foreground">
            ⌘K
          </kbd>
        </button>
      </div>

      {/* Area centrale scrollabile: indispensabile su schermi bassi
          (laptop 13"/14" con admin + org + personal nav insieme). */}
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 pb-2">
        <div className="my-1 h-px shrink-0 bg-sidebar-border" />

        {me?.is_platform_admin && (
          <>
            <SectionLabel>{t("nav.platform")}</SectionLabel>
            <Nav items={platformNav} onNavigate={onNavigate} />
            <SectionLabel>{t("nav.configurazioni")}</SectionLabel>
            <Nav items={configNav} onNavigate={onNavigate} />
            {orgId && (
              <div className="my-1 h-px shrink-0 bg-sidebar-border" />
            )}
          </>
        )}

        {orgId && (
          <>
            <SectionLabel>{t("nav.organization")}</SectionLabel>
            <Nav items={orgNav} onNavigate={onNavigate} permissionOrgId={orgId} />
          </>
        )}

        {me && (
          <>
            <SectionLabel>{t("nav.personal")}</SectionLabel>
            <Nav items={personalNav} onNavigate={onNavigate} />
          </>
        )}
      </div>

      {/* Footer fisso: org switcher + user menu + theme/language.
          `sidebar-controls` rimappa i token shadcn (Button ghost/outline)
          ai colori sidebar così tutto resta in tema scuro. */}
      <div className="sidebar-controls flex flex-col gap-2 border-t border-sidebar-border p-3 pt-2">
        <OrgSwitcher />
        <div className="flex items-center gap-1">
          <div className="min-w-0 flex-1">
            <UserMenu />
          </div>
          <div className="flex shrink-0 items-center">
            <LanguageSwitcher />
            <ThemeToggle />
          </div>
        </div>
      </div>
    </aside>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="px-3 pt-1 text-[11px] font-medium uppercase tracking-wider text-sidebar-muted-foreground">
      {children}
    </div>
  );
}

function Nav({
  items,
  onNavigate,
  permissionOrgId,
}: {
  items: NavSpec[];
  onNavigate?: () => void;
  permissionOrgId?: string;
}) {
  return (
    <nav className="flex flex-col gap-0.5">
      {items.map((it) => (
        <NavItem
          key={it.to}
          item={it}
          onNavigate={onNavigate}
          permissionOrgId={permissionOrgId}
        />
      ))}
    </nav>
  );
}

function NavItem({
  item,
  onNavigate,
  permissionOrgId,
}: {
  item: NavSpec;
  onNavigate?: () => void;
  permissionOrgId?: string;
}) {
  const allowed = useHasPermission(item.permission ?? "__always__", permissionOrgId);
  if (item.permission && !allowed) return null;
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.exact}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors",
          isActive
            ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
            : "text-sidebar-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
        )
      }
    >
      <Icon className="size-4 shrink-0" />
      <span className="truncate">{item.label}</span>
    </NavLink>
  );
}
