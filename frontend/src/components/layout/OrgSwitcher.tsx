import { Check, ChevronsUpDown, Building2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { useEffectiveOrgId } from "@/hooks/useEffectiveOrgId";
import { cn } from "@/lib/utils";

export function OrgSwitcher() {
  const { me } = useAuth();
  const effectiveId = useEffectiveOrgId();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const { t } = useTranslation();

  if (!me || me.organizations.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/40 px-2 py-2 text-xs text-muted-foreground">
        <Building2 className="size-4" />
        {t("orgSwitcher.noOrgs")}
      </div>
    );
  }

  const current = me.organizations.find((o) => o.organization_id === effectiveId);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between text-start"
          size="sm"
        >
          <div className="flex min-w-0 items-center gap-2">
            <Building2 className="size-4 shrink-0" />
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-sm font-medium">
                {current?.organization_name ?? t("orgSwitcher.select")}
              </span>
              {current && (
                <span className="truncate text-xs text-muted-foreground">
                  {current.role_name_it}
                </span>
              )}
            </div>
          </div>
          <ChevronsUpDown className="ms-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start">
        <Command>
          <CommandInput placeholder={t("orgSwitcher.search")} />
          <CommandList>
            <CommandEmpty>{t("common.noResults")}</CommandEmpty>
            <CommandGroup>
              {me.organizations.map((o) => (
                <CommandItem
                  key={o.organization_id}
                  value={o.organization_name}
                  onSelect={() => {
                    setOpen(false);
                    navigate(`/orgs/${o.organization_id}`);
                  }}
                >
                  <Check
                    className={cn(
                      "size-4",
                      effectiveId === o.organization_id ? "opacity-100" : "opacity-0"
                    )}
                  />
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate">{o.organization_name}</span>
                    <span className="truncate text-xs text-muted-foreground">
                      {o.role_name_it}
                    </span>
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
