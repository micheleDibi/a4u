import { useTranslation } from "react-i18next";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useOrgMembers } from "@/hooks/useOrgMembers";

interface Props {
  orgId: string;
  value: string | null | undefined;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export function MemberSelect({ orgId, value, onChange, disabled }: Props) {
  const { t } = useTranslation();
  const query = useOrgMembers(orgId);
  const members = query.data ?? [];

  return (
    <Select value={value ?? undefined} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger>
        <SelectValue placeholder={t("courses.fields.assigneePlaceholder")} />
      </SelectTrigger>
      <SelectContent>
        {members.map((m) => (
          <SelectItem key={m.user_id} value={m.user_id}>
            <span className="font-medium">{m.user_full_name}</span>
            <span className="ms-2 text-muted-foreground">
              · {t(`roles.${m.role_code}`, { defaultValue: m.role_name_it })}
            </span>
          </SelectItem>
        ))}
        {members.length === 0 && (
          <div className="p-2 text-xs text-muted-foreground">
            {t("courses.fields.noMembers")}
          </div>
        )}
      </SelectContent>
    </Select>
  );
}
