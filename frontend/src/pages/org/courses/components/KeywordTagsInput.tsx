import { useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

interface Props {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  maxTags?: number;
}

export function KeywordTagsInput({
  value,
  onChange,
  placeholder,
  disabled,
  maxTags = 30,
}: Props) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");

  const addTag = (raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return;
    if (value.includes(trimmed)) return;
    if (value.length >= maxTags) return;
    onChange([...value, trimmed]);
  };

  const removeTag = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return;
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag(draft);
      setDraft("");
      return;
    }
    if (e.key === "Backspace" && !draft && value.length > 0) {
      removeTag(value.length - 1);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {value.map((tag, idx) => (
          <Badge key={`${tag}-${idx}`} variant="secondary" className="gap-1 pe-1">
            <span>{tag}</span>
            {!disabled && (
              <button
                type="button"
                onClick={() => removeTag(idx)}
                className="rounded-sm p-0.5 hover:bg-muted-foreground/20"
                aria-label={t("common.remove")}
              >
                <X className="size-3" />
              </button>
            )}
          </Badge>
        ))}
        {value.length === 0 && (
          <span className="text-xs text-muted-foreground">
            {t("courses.fields.argomentiChiaveEmpty")}
          </span>
        )}
      </div>
      <Input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => {
          if (draft.trim()) {
            addTag(draft);
            setDraft("");
          }
        }}
        placeholder={
          placeholder ?? t("courses.fields.argomentiChiavePlaceholder")
        }
        disabled={disabled || value.length >= maxTags}
      />
      <p className="text-xs text-muted-foreground">
        {t("courses.fields.argomentiChiaveHint", { max: maxTags })}
      </p>
    </div>
  );
}
