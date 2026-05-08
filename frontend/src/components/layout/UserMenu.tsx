import { LogOut } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function UserMenu({ collapsed = false }: { collapsed?: boolean }) {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const { t } = useTranslation();
  if (!me) return null;

  const initials = me.user.full_name
    .split(" ")
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="h-auto w-full justify-start gap-2 px-2 py-1.5"
          aria-label={t("user.menu")}
        >
          <Avatar className="size-7">
            <AvatarFallback className="bg-brand text-brand-foreground text-xs">
              {initials}
            </AvatarFallback>
          </Avatar>
          {!collapsed && (
            <div className="flex min-w-0 flex-1 flex-col items-start text-left">
              <span className="block w-full truncate text-sm font-medium">
                {me.user.full_name}
              </span>
              <span className="block w-full truncate text-xs text-muted-foreground">
                {me.user.email}
              </span>
            </div>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="top" className="w-64 max-w-[calc(100vw-1rem)]">
        <DropdownMenuLabel>
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="truncate text-sm">{me.user.full_name}</span>
            <span className="truncate text-xs font-normal text-muted-foreground">
              {me.user.email}
            </span>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={async () => {
            await logout();
            navigate("/login", { replace: true });
          }}
        >
          <LogOut className="size-4" />
          <span>{t("auth.logout")}</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
