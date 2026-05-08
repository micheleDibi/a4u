import "react-image-crop/dist/ReactCrop.css";
import { Crop, Trash2, Upload, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactCrop, {
  centerCrop,
  makeAspectCrop,
  type Crop as CropType,
  type PixelCrop,
} from "react-image-crop";
import { useTranslation } from "react-i18next";
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

interface Props {
  label: string;
  helperText?: string;
  value?: File | null;
  existingUrl?: string | null;
  onChange: (file: File | null) => void;
  onRemoveExisting?: () => void;
  /** Lato target (in pixel) dell'immagine ritagliata. 1024 = 1024x1024. */
  outputSize?: number;
}

const ACCEPT = "image/png,image/jpeg,image/webp";

function readAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

function buildCenteredSquare(imgW: number, imgH: number): CropType {
  return centerCrop(
    makeAspectCrop({ unit: "%", width: 80 }, 1, imgW, imgH),
    imgW,
    imgH
  );
}

async function cropToFile(
  image: HTMLImageElement,
  crop: PixelCrop,
  outputSize: number,
  filenameStem: string
): Promise<File> {
  // Le coordinate di react-image-crop sono in pixel "renderizzati" (DOM),
  // dobbiamo riscalare alle dimensioni naturali dell'immagine.
  const scaleX = image.naturalWidth / image.width;
  const scaleY = image.naturalHeight / image.height;

  const sx = crop.x * scaleX;
  const sy = crop.y * scaleY;
  const sw = crop.width * scaleX;
  const sh = crop.height * scaleY;

  const canvas = document.createElement("canvas");
  canvas.width = outputSize;
  canvas.height = outputSize;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas context unavailable");
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(image, sx, sy, sw, sh, 0, 0, outputSize, outputSize);

  const blob: Blob = await new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("Canvas toBlob failed"))),
      "image/jpeg",
      0.92
    );
  });
  return new File([blob], `${filenameStem}.jpg`, { type: "image/jpeg" });
}

export function FormAvatarImageInput({
  label,
  helperText,
  value,
  existingUrl,
  onChange,
  onRemoveExisting,
  outputSize = 1024,
}: Props) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [dialogSrc, setDialogSrc] = useState<string | null>(null);
  const [crop, setCrop] = useState<CropType | undefined>(undefined);
  const [completedCrop, setCompletedCrop] = useState<PixelCrop | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (value) {
      const url = URL.createObjectURL(value);
      setPreviewUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    setPreviewUrl(null);
    return () => undefined;
  }, [value]);

  const showUrl = previewUrl ?? existingUrl ?? null;

  const handleFile = async (file: File) => {
    try {
      const dataUrl = await readAsDataURL(file);
      setDialogSrc(dataUrl);
    } catch {
      // ignora; file probabilmente non leggibile
    }
  };

  const handleCropConfirm = async () => {
    if (!imgRef.current || !completedCrop || completedCrop.width < 8) return;
    setWorking(true);
    try {
      const file = await cropToFile(
        imgRef.current,
        completedCrop,
        outputSize,
        "avatar-image"
      );
      onChange(file);
      setDialogSrc(null);
      setCrop(undefined);
      setCompletedCrop(null);
    } finally {
      setWorking(false);
    }
  };

  const reopenCrop = async () => {
    if (!value) return;
    await handleFile(value);
  };

  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <div className="grid aspect-square w-full max-w-[280px] place-items-center overflow-hidden rounded-lg border border-dashed border-border bg-muted/40 sm:w-64">
          {showUrl ? (
            <img src={showUrl} alt="" className="size-full object-cover" />
          ) : (
            <div className="flex flex-col items-center gap-2 px-4 text-center text-xs text-muted-foreground">
              <User className="size-10 opacity-40" />
              <span>{t("myAvatar.imageEmpty")}</span>
            </div>
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
          {value && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={reopenCrop}
            >
              <Crop className="size-4" />
              {t("myAvatar.recrop")}
            </Button>
          )}
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
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0] ?? null;
          if (f) void handleFile(f);
        }}
      />
      {helperText && <p className="text-xs text-muted-foreground">{helperText}</p>}

      <Dialog
        open={!!dialogSrc}
        onOpenChange={(o) => {
          if (!o) {
            setDialogSrc(null);
            setCrop(undefined);
            setCompletedCrop(null);
          }
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("myAvatar.cropTitle")}</DialogTitle>
            <DialogDescription>{t("myAvatar.cropHint")}</DialogDescription>
          </DialogHeader>
          {dialogSrc && (
            <div className="flex max-h-[60vh] justify-center overflow-auto bg-muted/40 p-2">
              <ReactCrop
                crop={crop}
                onChange={(_pc, pCrop) => setCrop(pCrop)}
                onComplete={(pc) => setCompletedCrop(pc)}
                aspect={1}
                keepSelection
                circularCrop={false}
                minWidth={50}
                minHeight={50}
              >
                <img
                  ref={imgRef}
                  src={dialogSrc}
                  alt=""
                  onLoad={(e) => {
                    const img = e.currentTarget;
                    const initial = buildCenteredSquare(img.width, img.height);
                    setCrop(initial);
                  }}
                  className="max-h-[55vh] w-auto"
                />
              </ReactCrop>
            </div>
          )}
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => {
                setDialogSrc(null);
                setCrop(undefined);
                setCompletedCrop(null);
                if (inputRef.current) inputRef.current.value = "";
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void handleCropConfirm()}
              disabled={
                !completedCrop ||
                completedCrop.width < 8 ||
                completedCrop.height < 8 ||
                working
              }
            >
              {working ? t("common.saving") : t("myAvatar.cropConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
