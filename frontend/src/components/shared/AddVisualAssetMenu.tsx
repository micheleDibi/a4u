import {
  ChartColumn,
  Image as ImageIcon,
  Loader2,
  Plus,
  SquareFunction,
  Upload,
  Waypoints,
} from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { coursesApi, type LessonContentVisualAsset } from "@/api/courses";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { extractApiError } from "@/lib/errors";
import { VISUAL_FORMATS, type VisualFormat } from "@/lib/figureFormats";

/**
 * Menu «Aggiungi asset visivo», condiviso dai dialog delle Dispense e
 * delle Slide: carica un'immagine oppure crea un asset vuoto per uno dei
 * formati di figura (Mermaid, Vega-Lite, DOT, `function`). L'id lo decide
 * il chiamante con `makeAssetId` (Dispense: `A{n}`, slide: `asset_new_{n}`,
 * entrambi con il ciclo anti-collisione sugli id esistenti).
 */
export interface AddVisualAssetMenuProps {
  orgId: string;
  courseId: string;
  disabled: boolean;
  makeAssetId: () => string;
  onAdd: (asset: LessonContentVisualAsset) => void;
  /** Formati offerti dal menu (default: tutti). */
  formats?: readonly VisualFormat[];
  /** Etichetta del pulsante (le due superfici usano chiavi diverse). */
  triggerLabel: string;
}

const WRITE_ITEMS: ReadonlyArray<{
  format: Exclude<VisualFormat, "image">;
  labelKey: string;
  Icon: typeof ImageIcon;
}> = [
  {
    format: "mermaid",
    labelKey: "courses.lessonsContent.editor.assetActions.writeMermaid",
    Icon: ImageIcon,
  },
  {
    format: "vegalite",
    labelKey: "courses.lessonsContent.editor.assetActions.writeVegalite",
    Icon: ChartColumn,
  },
  {
    format: "dot",
    labelKey: "courses.lessonsContent.editor.assetActions.writeDot",
    Icon: Waypoints,
  },
  {
    format: "function",
    labelKey: "courses.lessonsContent.editor.assetActions.writeFunction",
    Icon: SquareFunction,
  },
];

export function AddVisualAssetMenu({
  orgId,
  courseId,
  disabled,
  makeAssetId,
  onAdd,
  formats = VISUAL_FORMATS,
  triggerLabel,
}: AddVisualAssetMenuProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);

  const triggerFilePicker = () => {
    if (disabled || uploading) return;
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset il value subito così l'utente può ricaricare lo stesso file in
    // un secondo momento (browser non triggera change su same-file altrimenti).
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    try {
      const { path } = await coursesApi.lessonAssets.upload(orgId, courseId, file);
      onAdd({
        asset_id: makeAssetId(),
        format: "image",
        content: path,
        caption: "",
        alt_text: "",
      });
      toast.success(t("courses.lessonsContent.editor.assetActions.imageUploaded"));
    } catch (err) {
      toast.error(
        extractApiError(err).message ??
          t("courses.lessonsContent.editor.assetActions.imageUploadFailed"),
      );
    } finally {
      setUploading(false);
    }
  };

  const addEmpty = (format: Exclude<VisualFormat, "image">) => {
    onAdd({
      asset_id: makeAssetId(),
      format,
      content: "",
      caption: "",
      alt_text: "",
    });
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={handleFileChange}
      />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || uploading}
          >
            {uploading ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                {t("courses.lessonsContent.editor.assetActions.uploading")}
              </>
            ) : (
              <>
                <Plus className="size-3.5" />
                {triggerLabel}
              </>
            )}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {formats.includes("image") && (
            <DropdownMenuItem onClick={triggerFilePicker} disabled={uploading}>
              <Upload className="size-3.5" />
              {t("courses.lessonsContent.editor.assetActions.uploadImage")}
            </DropdownMenuItem>
          )}
          {WRITE_ITEMS.filter((item) => formats.includes(item.format)).map(
            ({ format, labelKey, Icon }) => (
              <DropdownMenuItem key={format} onClick={() => addEmpty(format)}>
                <Icon className="size-3.5" />
                {t(labelKey)}
              </DropdownMenuItem>
            ),
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

export default AddVisualAssetMenu;
