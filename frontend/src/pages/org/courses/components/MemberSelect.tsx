import { Check, ChevronsUpDown } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { useOrgMembers } from "@/hooks/useOrgMembers";
import { cn } from "@/lib/utils";

interface AssigneeFallback {
  id: string;
  full_name: string;
  email: string;
}

interface Props {
  orgId: string;
  value: string | null | undefined;
  onChange: (value: string) => void;
  disabled?: boolean;
  /** Mostrato quando `value` non è nella lista membri (es. l'utente non
   *  ha `member:view`, quindi `/orgs/{id}/members` ritorna 403): permette
   *  comunque di vedere il nome dell'assegnatario invece del placeholder. */
  fallback?: AssigneeFallback | null;
}

export function MemberSelect({
  orgId,
  value,
  onChange,
  disabled,
  fallback,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const query = useOrgMembers(orgId);
  const members = query.data ?? [];

  const selectedFromList = members.find((m) => m.user_id === value);
  const selected = selectedFromList ?? (
    fallback && fallback.id === value
      ? {
          user_id: fallback.id,
          user_full_name: fallback.full_name,
          role_code: "",
          role_name_it: "",
        }
      : undefined
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className="w-full justify-between text-start font-normal"
        >
          {selected ? (
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate font-medium">
                {selected.user_full_name}
              </span>
              {selected.role_code && (
                <span className="truncate text-xs text-muted-foreground">
                  ·{" "}
                  {t(`roles.${selected.role_code}`, {
                    defaultValue: selected.role_name_it,
                  })}
                </span>
              )}
            </span>
          ) : (
            <span className="text-muted-foreground">
              {t("courses.fields.assigneePlaceholder")}
            </span>
          )}
          <ChevronsUpDown className="ms-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[--radix-popover-trigger-width] p-0"
        align="start"
      >
        <Command
          filter={(itemValue, search) => {
            // itemValue è "<full_name> <email>" (lowercase grazie a cmdk).
            // Match case-insensitive su ogni token della search query.
            const tokens = search.toLowerCase().split(/\s+/).filter(Boolean);
            return tokens.every((tok) => itemValue.includes(tok)) ? 1 : 0;
          }}
        >
          <CommandInput placeholder={t("common.search")} />
          <CommandList>
            <CommandEmpty>{t("courses.fields.noMembers")}</CommandEmpty>
            <CommandGroup>
              {members.map((m) => (
                <CommandItem
                  key={m.user_id}
                  value={`${m.user_full_name} ${m.user_email}`}
                  onSelect={() => {
                    onChange(m.user_id);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn(
                      "size-4",
                      value === m.user_id ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate font-medium">
                      {m.user_full_name}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {m.user_email} ·{" "}
                      {t(`roles.${m.role_code}`, {
                        defaultValue: m.role_name_it,
                      })}
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
