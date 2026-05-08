import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  open: boolean;
  isRegeneration: boolean;
  isPending: boolean;
  onClose: () => void;
  onConfirm: (hint: string | null) => void;
}

export function GenerateArchitectureDialog({
  open,
  isRegeneration,
  isPending,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const [hint, setHint] = useState("");

  useEffect(() => {
    if (!open) setHint("");
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {isRegeneration
              ? t("courses.architecture.regenerateDialog.title")
              : t("courses.architecture.generateDialog.title")}
          </DialogTitle>
          <DialogDescription>
            {isRegeneration
              ? t("courses.architecture.regenerateDialog.description")
              : t("courses.architecture.generateDialog.description")}
          </DialogDescription>
        </DialogHeader>

        {isRegeneration && (
          <div className="space-y-1.5">
            <Label htmlFor="regeneration-hint">
              {t("courses.architecture.regenerateDialog.hintLabel")}
            </Label>
            <Textarea
              id="regeneration-hint"
              rows={4}
              maxLength={2000}
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder={t(
                "courses.architecture.regenerateDialog.hintPlaceholder"
              )}
              disabled={isPending}
            />
            <p className="text-xs text-muted-foreground">
              {t("courses.architecture.regenerateDialog.hintHelper")}
            </p>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={() => onConfirm(hint.trim() || null)}
            disabled={isPending}
          >
            <Sparkles className="size-4" />
            {isPending
              ? t("courses.architecture.generating")
              : isRegeneration
              ? t("courses.architecture.regenerate")
              : t("courses.architecture.generate")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
