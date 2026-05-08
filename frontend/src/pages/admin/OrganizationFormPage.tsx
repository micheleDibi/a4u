import { useEffect, useMemo, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { organizationsApi, type OrganizationFormFields } from "@/api/organizations";
import { FormImageUpload } from "@/components/forms/FormImageUpload";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { extractApiError } from "@/lib/errors";

const schema = z.object({
  name: z.string().min(1).max(255),
  email: z.string().email(),
  phone: z.string().max(50).optional().or(z.literal("")),
  website: z.string().max(255).optional().or(z.literal("")),
  vat_number: z.string().max(64).optional().or(z.literal("")),
  fiscal_code: z.string().max(64).optional().or(z.literal("")),
  country: z.string().max(100).optional().or(z.literal("")),
  address: z.string().max(255).optional().or(z.literal("")),
  city: z.string().max(120).optional().or(z.literal("")),
  province: z.string().max(120).optional().or(z.literal("")),
  postal_code: z.string().max(20).optional().or(z.literal("")),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  mode: "create" | "edit";
}

export default function OrganizationFormPage({ mode }: Props) {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [removeLogo, setRemoveLogo] = useState(false);

  const editing = mode === "edit" && id;
  const orgQuery = useQuery({
    queryKey: ["organization", id],
    queryFn: () => organizationsApi.get(id as string),
    enabled: !!editing,
  });

  const defaults: FormValues = useMemo(
    () => ({
      name: orgQuery.data?.name ?? "",
      email: orgQuery.data?.email ?? "",
      phone: orgQuery.data?.phone ?? "",
      website: orgQuery.data?.website ?? "",
      vat_number: orgQuery.data?.vat_number ?? "",
      fiscal_code: orgQuery.data?.fiscal_code ?? "",
      country: orgQuery.data?.country ?? "",
      address: orgQuery.data?.address ?? "",
      city: orgQuery.data?.city ?? "",
      province: orgQuery.data?.province ?? "",
      postal_code: orgQuery.data?.postal_code ?? "",
    }),
    [orgQuery.data]
  );

  const form = useForm<FormValues>({ defaultValues: defaults, resolver: zodResolver(schema), mode: "onBlur" });

  useEffect(() => {
    if (orgQuery.data) form.reset(defaults);
  }, [orgQuery.data, defaults, form]);

  const submit = useMutation({
    mutationFn: async (values: FormValues) => {
      const payload: OrganizationFormFields = {
        name: values.name,
        email: values.email,
        phone: values.phone || undefined,
        website: values.website || undefined,
        vat_number: values.vat_number || undefined,
        fiscal_code: values.fiscal_code || undefined,
        country: values.country || undefined,
        address: values.address || undefined,
        city: values.city || undefined,
        province: values.province || undefined,
        postal_code: values.postal_code || undefined,
      };
      if (editing) {
        return organizationsApi.update(id as string, payload, {
          logo: logoFile,
          remove_logo: removeLogo,
        });
      }
      return organizationsApi.create(payload, logoFile);
    },
    onSuccess: () => {
      toast.success(editing ? t("organizations.updated") : t("organizations.created"));
      qc.invalidateQueries({ queryKey: ["organizations"] });
      qc.invalidateQueries({ queryKey: ["organization", id] });
      navigate("/admin/organizations");
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  const fields: { name: keyof FormValues; label: string; col: number }[] = [
    { name: "name", label: `${t("organizations.fields.name")} *`, col: 6 },
    { name: "email", label: `${t("organizations.fields.email")} *`, col: 6 },
    { name: "phone", label: t("organizations.fields.phone"), col: 6 },
    { name: "website", label: t("organizations.fields.website"), col: 6 },
    { name: "vat_number", label: t("organizations.fields.vatNumber"), col: 6 },
    { name: "fiscal_code", label: t("organizations.fields.fiscalCode"), col: 6 },
    { name: "country", label: t("organizations.fields.country"), col: 4 },
    { name: "address", label: t("organizations.fields.address"), col: 8 },
    { name: "city", label: t("organizations.fields.city"), col: 4 },
    { name: "province", label: t("organizations.fields.province"), col: 4 },
    { name: "postal_code", label: t("organizations.fields.postalCode"), col: 4 },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={editing ? t("organizations.edit") : t("organizations.new")}
      />

      <Card>
        <CardContent className="p-6">
          <form onSubmit={form.handleSubmit((v) => submit.mutate(v))} className="space-y-6">
            <FormImageUpload
              label={t("organizations.fields.logo")}
              helperText={t("organizations.fields.logoHint")}
              value={logoFile}
              existingUrl={!removeLogo ? orgQuery.data?.logo_path ?? null : null}
              onChange={(f) => {
                setLogoFile(f);
                if (f) setRemoveLogo(false);
              }}
              onRemoveExisting={() => setRemoveLogo(true)}
            />

            <div className="grid grid-cols-12 gap-4">
              {fields.map((f) => (
                <div
                  key={f.name}
                  className="col-span-12"
                  style={{ gridColumn: `span ${f.col} / span ${f.col}` }}
                >
                  <Controller
                    name={f.name}
                    control={form.control}
                    render={({ field, fieldState }) => (
                      <div className="space-y-1.5">
                        <Label>{f.label}</Label>
                        <Input
                          {...field}
                          value={field.value ?? ""}
                          aria-invalid={!!fieldState.error}
                        />
                        {fieldState.error?.message && (
                          <p className="text-xs text-destructive">{fieldState.error.message}</p>
                        )}
                      </div>
                    )}
                  />
                </div>
              ))}
            </div>

            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => navigate(-1)}>
                {t("common.cancel")}
              </Button>
              <Button type="submit" disabled={submit.isPending}>
                {submit.isPending
                  ? t("common.saving")
                  : editing
                  ? t("common.save")
                  : t("common.add")}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
