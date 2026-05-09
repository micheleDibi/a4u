import { useEffect, useMemo, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { pdfTemplatesApi, type PdfTemplateFields } from "@/api/pdfTemplates";
import { FormImageUpload } from "@/components/forms/FormImageUpload";
import { PageHeader } from "@/components/layout/PageHeader";
import { PdfTemplatePreview } from "@/components/templates/PdfTemplatePreview";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { extractApiError } from "@/lib/errors";

const HEX = /^#[0-9A-Fa-f]{6}$/;

const schema = z.object({
  name: z.string().min(1).max(120),
  text_color: z.string().regex(HEX),
  primary_color: z.string().regex(HEX),
  secondary_color: z.string().regex(HEX),
  font_family: z.string().min(1).max(120),
  page_size: z.enum(["A4", "Letter"]),
  header_height_mm: z.number().min(0).max(80),
  footer_height_mm: z.number().min(0).max(80),
  margin_mm: z.number().min(0).max(60),
  background_opacity_pct: z.number().min(0).max(100),
});

type FormValues = z.infer<typeof schema>;

const FONT_OPTIONS = ["Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "Source Sans Pro"];

export default function PdfTemplateEditorPage() {
  const { orgId = "", id = "" } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t } = useTranslation();

  const [bgFile, setBgFile] = useState<File | null>(null);
  const [logoLFile, setLogoLFile] = useState<File | null>(null);
  const [logoRFile, setLogoRFile] = useState<File | null>(null);
  const [removeBg, setRemoveBg] = useState(false);
  const [removeLogoL, setRemoveLogoL] = useState(false);
  const [removeLogoR, setRemoveLogoR] = useState(false);

  const tplQuery = useQuery({
    queryKey: ["pdf-template", orgId, id],
    queryFn: () => pdfTemplatesApi.get(orgId, id),
    enabled: !isNew,
  });

  const defaults: FormValues = useMemo(
    () => ({
      name: tplQuery.data?.name ?? "",
      text_color: tplQuery.data?.text_color ?? "#1F1F1F",
      primary_color: tplQuery.data?.primary_color ?? "#4F46E5",
      secondary_color: tplQuery.data?.secondary_color ?? "#9333EA",
      font_family: tplQuery.data?.font_family ?? "Inter",
      page_size: tplQuery.data?.page_size ?? "A4",
      header_height_mm: tplQuery.data?.header_height_mm ?? 20,
      footer_height_mm: tplQuery.data?.footer_height_mm ?? 15,
      margin_mm: tplQuery.data?.margin_mm ?? 20,
      background_opacity_pct: tplQuery.data?.background_opacity_pct ?? 15,
    }),
    [tplQuery.data]
  );

  const form = useForm<FormValues>({ defaultValues: defaults, resolver: zodResolver(schema), mode: "onChange" });

  useEffect(() => {
    if (tplQuery.data) form.reset(defaults);
  }, [tplQuery.data, defaults, form]);

  const watch = form.watch();

  const bgPreview = useMemo(() => (bgFile ? URL.createObjectURL(bgFile) : null), [bgFile]);
  const logoLPreview = useMemo(() => (logoLFile ? URL.createObjectURL(logoLFile) : null), [logoLFile]);
  const logoRPreview = useMemo(() => (logoRFile ? URL.createObjectURL(logoRFile) : null), [logoRFile]);
  useEffect(() => () => { if (bgPreview) URL.revokeObjectURL(bgPreview); }, [bgPreview]);
  useEffect(() => () => { if (logoLPreview) URL.revokeObjectURL(logoLPreview); }, [logoLPreview]);
  useEffect(() => () => { if (logoRPreview) URL.revokeObjectURL(logoRPreview); }, [logoRPreview]);

  const previewBg = bgPreview ?? (removeBg ? null : tplQuery.data?.background_image_path ?? null);
  const previewLogoL = logoLPreview ?? (removeLogoL ? null : tplQuery.data?.logo_left_path ?? null);
  const previewLogoR = logoRPreview ?? (removeLogoR ? null : tplQuery.data?.logo_right_path ?? null);

  const save = useMutation({
    mutationFn: async (values: FormValues) => {
      const fields: PdfTemplateFields = { ...values };
      if (isNew) {
        return pdfTemplatesApi.create(orgId, fields, {
          background: bgFile,
          logo_left: logoLFile,
          logo_right: logoRFile,
        });
      }
      return pdfTemplatesApi.update(orgId, id, fields, {
        background: bgFile,
        logo_left: logoLFile,
        logo_right: logoRFile,
        remove_background: removeBg,
        remove_logo_left: removeLogoL,
        remove_logo_right: removeLogoR,
      });
    },
    onSuccess: () => {
      toast.success(isNew ? t("templates.pdf.created") : t("templates.pdf.updated"));
      qc.invalidateQueries({ queryKey: ["org", orgId, "pdf-templates"] });
      qc.invalidateQueries({ queryKey: ["pdf-template", orgId, id] });
      navigate(`/orgs/${orgId}/templates/pdf`);
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title={isNew ? t("templates.pdf.newPage") : t("templates.pdf.editPage", { name: tplQuery.data?.name ?? "" })}
        description={t("templates.pdf.previewHint")}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardContent className="p-6">
            <form onSubmit={form.handleSubmit((v) => save.mutate(v))} className="space-y-4">
              <Controller
                name="name"
                control={form.control}
                render={({ field, fieldState }) => (
                  <FieldRow label={`${t("templates.fields.name")} *`} error={fieldState.error?.message}>
                    <Input {...field} />
                  </FieldRow>
                )}
              />
              <Controller
                name="page_size"
                control={form.control}
                render={({ field }) => (
                  <FieldRow label={t("templates.fields.pageSize")}>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="A4">A4</SelectItem>
                        <SelectItem value="Letter">Letter</SelectItem>
                      </SelectContent>
                    </Select>
                  </FieldRow>
                )}
              />
              <Controller
                name="font_family"
                control={form.control}
                render={({ field }) => (
                  <FieldRow label={t("templates.fields.font")}>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {FONT_OPTIONS.map((f) => (
                          <SelectItem key={f} value={f}>{f}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FieldRow>
                )}
              />
              <ColorRow control={form.control} name="text_color" label={t("templates.fields.textColor")} />
              <ColorRow control={form.control} name="primary_color" label={t("templates.fields.primaryColor")} />
              <ColorRow control={form.control} name="secondary_color" label={t("templates.fields.secondaryColor")} />

              <SliderRow control={form.control} name="header_height_mm" label={t("templates.fields.headerHeight")} max={80} />
              <SliderRow control={form.control} name="footer_height_mm" label={t("templates.fields.footerHeight")} max={80} />
              <SliderRow control={form.control} name="margin_mm" label={t("templates.fields.margin")} max={60} />

              <FormImageUpload
                label={t("templates.fields.background")}
                value={bgFile}
                existingUrl={!removeBg ? tplQuery.data?.background_image_path ?? null : null}
                onChange={(f) => { setBgFile(f); if (f) setRemoveBg(false); }}
                onRemoveExisting={() => setRemoveBg(true)}
              />
              <SliderRow
                control={form.control}
                name="background_opacity_pct"
                label={t("templates.fields.backgroundOpacity")}
                max={100}
                unit="%"
              />
              <FormImageUpload
                label={t("templates.fields.logoLeft")}
                value={logoLFile}
                existingUrl={!removeLogoL ? tplQuery.data?.logo_left_path ?? null : null}
                onChange={(f) => { setLogoLFile(f); if (f) setRemoveLogoL(false); }}
                onRemoveExisting={() => setRemoveLogoL(true)}
              />
              <FormImageUpload
                label={t("templates.fields.logoRight")}
                value={logoRFile}
                existingUrl={!removeLogoR ? tplQuery.data?.logo_right_path ?? null : null}
                onChange={(f) => { setLogoRFile(f); if (f) setRemoveLogoR(false); }}
                onRemoveExisting={() => setRemoveLogoR(true)}
              />

              <div className="flex justify-end gap-2 pt-2">
                <Button type="button" variant="ghost" onClick={() => navigate(-1)}>
                  {t("common.cancel")}
                </Button>
                <Button type="submit" disabled={save.isPending}>
                  {save.isPending
                    ? t("common.saving")
                    : isNew
                    ? t("common.add")
                    : t("common.save")}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <div className="lg:sticky lg:top-6 lg:self-start">
          <Card>
            <CardContent className="space-y-3 p-6">
              <p className="text-xs uppercase tracking-wider text-muted-foreground">
                {t("templates.pdf.previewLive")}
              </p>
              <div className="mx-auto max-w-[400px]">
                <PdfTemplatePreview
                  background={previewBg}
                  logoLeft={previewLogoL}
                  logoRight={previewLogoR}
                  textColor={watch.text_color || "#1F1F1F"}
                  primaryColor={watch.primary_color || "#4F46E5"}
                  secondaryColor={watch.secondary_color || "#9333EA"}
                  fontFamily={watch.font_family || "Inter"}
                  pageSize={watch.page_size || "A4"}
                  headerHeightMm={watch.header_height_mm ?? 20}
                  footerHeightMm={watch.footer_height_mm ?? 15}
                  marginMm={watch.margin_mm ?? 20}
                  backgroundOpacityPct={watch.background_opacity_pct ?? 15}
                />
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function FieldRow({ label, children, error }: { label: string; children: React.ReactNode; error?: string }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

function ColorRow({
  control,
  name,
  label,
}: {
  control: ReturnType<typeof useForm<FormValues>>["control"];
  name: keyof FormValues;
  label: string;
}) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState }) => (
        <div className="space-y-1.5">
          <Label>{label}</Label>
          <div className="flex items-center gap-2">
            <input
              type="color"
              value={field.value as string}
              onChange={(e) => field.onChange(e.target.value.toUpperCase())}
              className="size-9 cursor-pointer rounded-md border border-input p-0"
            />
            <Input
              value={field.value as string}
              onChange={field.onChange}
              className="flex-1"
              aria-invalid={!!fieldState.error}
            />
          </div>
          {fieldState.error?.message && (
            <p className="text-xs text-destructive">{fieldState.error.message}</p>
          )}
        </div>
      )}
    />
  );
}

function SliderRow({
  control,
  name,
  label,
  max,
  unit = "mm",
}: {
  control: ReturnType<typeof useForm<FormValues>>["control"];
  name: keyof FormValues;
  label: string;
  max: number;
  unit?: string;
}) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field }) => (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label>{label}</Label>
            <span className="text-xs text-muted-foreground">
              {Number(field.value)} {unit}
            </span>
          </div>
          <Slider
            value={[Number(field.value)]}
            onValueChange={(v) => field.onChange(v[0])}
            min={0}
            max={max}
            step={1}
          />
        </div>
      )}
    />
  );
}
