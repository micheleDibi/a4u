import { Mic, Square, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

interface Props {
  label: string;
  helperText?: string;
  value: File | null;
  existingUrl: string | null;
  onChange: (file: File | null) => void;
  onRemoveExisting?: () => void;
}

const supportsRecording = (): boolean => {
  if (typeof navigator === "undefined") return false;
  if (!navigator.mediaDevices?.getUserMedia) return false;
  if (typeof window === "undefined") return false;
  return typeof window.MediaRecorder !== "undefined";
};

function pickRecorderMime(): string | undefined {
  if (typeof window === "undefined" || !window.MediaRecorder) return undefined;
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4;codecs=mp4a",
    "audio/mp4",
    "audio/ogg;codecs=opus",
    "audio/ogg",
  ];
  for (const c of candidates) {
    if (window.MediaRecorder.isTypeSupported?.(c)) return c;
  }
  return undefined;
}

function extensionForMime(mime: string): string {
  if (mime.includes("webm")) return "webm";
  if (mime.includes("ogg")) return "ogg";
  if (mime.includes("mp4") || mime.includes("aac")) return "m4a";
  if (mime.includes("wav")) return "wav";
  if (mime.includes("mpeg") || mime.includes("mp3")) return "mp3";
  return "bin";
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

export function FormAudioInput({
  label,
  helperText,
  value,
  existingUrl,
  onChange,
  onRemoveExisting,
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [tab, setTab] = useState<"upload" | "record">("upload");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const { t } = useTranslation();

  const canRecord = useMemo(() => supportsRecording(), []);

  // Recording state
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startTsRef = useRef<number>(0);
  const intervalRef = useRef<number | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (value) {
      const url = URL.createObjectURL(value);
      setPreviewUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    setPreviewUrl(null);
    return () => undefined;
  }, [value]);

  useEffect(() => () => {
    // Cleanup on unmount
    if (intervalRef.current) window.clearInterval(intervalRef.current);
    streamRef.current?.getTracks().forEach((tr) => tr.stop());
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      try {
        recorderRef.current.stop();
      } catch {
        /* noop */
      }
    }
  }, []);

  const audioSrc = previewUrl ?? existingUrl ?? null;

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const mime = pickRecorderMime();
      const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      recorderRef.current = recorder;
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const usedMime = recorder.mimeType || mime || "audio/webm";
        const blob = new Blob(chunksRef.current, { type: usedMime });
        const ext = extensionForMime(usedMime);
        const file = new File([blob], `recording.${ext}`, { type: usedMime });
        onChange(file);
        streamRef.current?.getTracks().forEach((tr) => tr.stop());
        streamRef.current = null;
        if (intervalRef.current) {
          window.clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      };
      recorder.start(100);
      startTsRef.current = Date.now();
      setElapsed(0);
      intervalRef.current = window.setInterval(() => {
        setElapsed(Math.floor((Date.now() - startTsRef.current) / 1000));
      }, 250);
      setIsRecording(true);
    } catch (err) {
      console.warn("recording_failed", err);
      setIsRecording(false);
    }
  };

  const stopRecording = () => {
    setIsRecording(false);
    const r = recorderRef.current;
    if (r && r.state !== "inactive") r.stop();
  };

  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="w-full">
        <TabsList className={cn(canRecord ? "grid grid-cols-2" : "grid grid-cols-1")}>
          <TabsTrigger value="upload">
            <Upload className="size-4" />
            {t("myAvatar.audioUpload")}
          </TabsTrigger>
          {canRecord && (
            <TabsTrigger value="record">
              <Mic className="size-4" />
              {t("myAvatar.audioRecord")}
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="upload" className="space-y-2 pt-3">
          <div className="flex items-center gap-3">
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
            <input
              ref={inputRef}
              type="file"
              accept="audio/*"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                onChange(f);
              }}
            />
          </div>
        </TabsContent>

        {canRecord && (
          <TabsContent value="record" className="space-y-2 pt-3">
            <div className="flex items-center gap-3">
              {!isRecording ? (
                <Button type="button" variant="outline" size="sm" onClick={startRecording}>
                  <Mic className="size-4" />
                  {t("myAvatar.recordStart")}
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  onClick={stopRecording}
                >
                  <Square className="size-4" />
                  {t("myAvatar.recordStop")}
                </Button>
              )}
              {isRecording && (
                <span className="font-mono text-sm tabular-nums text-destructive">
                  ● {formatDuration(elapsed)}
                </span>
              )}
            </div>
          </TabsContent>
        )}
      </Tabs>

      {audioSrc && !isRecording && (
        <audio src={audioSrc} controls className="w-full" />
      )}
      {helperText && <p className="text-xs text-muted-foreground">{helperText}</p>}
    </div>
  );
}
