import { Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";

interface Props {
  label: string;
  helperText?: string;
  value?: File | null;
  existingUrl?: string | null;
  onChange: (file: File | null) => void;
  onRemoveExisting?: () => void;
  accept?: string;
}

export function FormImageUpload({
  label,
  helperText,
  value,
  existingUrl,
  onChange,
  onRemoveExisting,
  accept = "image/png,image/jpeg,image/webp",
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const { t } = useTranslation();

  useEffect(() => {
    if (value) {
      const url = URL.createObjectURL(value);
      setPreview(url);
      return () => URL.revokeObjectURL(url);
    }
    setPreview(null);
    return () => undefined;
  }, [value]);

  const showUrl = preview ?? existingUrl ?? null;

  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div className="flex items-center gap-3">
        <div className="grid size-24 place-items-center overflow-hidden rounded-md border border-dashed border-border bg-muted/40">
          {showUrl ? (
            <img src={showUrl} alt="" className="size-full object-contain" />
          ) : (
            <span className="px-2 text-center text-xs text-muted-foreground">—</span>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => inputRef.current?.click()}
          >
            <Upload className="size-4" />
            {t("common.add")}
          </Button>
          {(value || existingUrl) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => {
                onChange(null);
                onRemoveExisting?.();
                if (inputRef.current) inputRef.current.value = "";
              }}
            >
              <Trash2 className="size-4" />
              {t("common.remove")}
            </Button>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            onChange(f);
          }}
        />
      </div>
      {helperText && (
        <p className="text-xs text-muted-foreground">{helperText}</p>
      )}
    </div>
  );
}
