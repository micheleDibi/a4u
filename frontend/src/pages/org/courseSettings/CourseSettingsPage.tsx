import { useEffect, useMemo } from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  ClipboardCheck,
  Clock,
  HelpCircle,
  Layers,
  ListChecks,
  Minus,
  PenLine,
  Plus,
  Sparkles,
} from "lucide-react";
import { courseSettingsApi } from "@/api/courseSettings";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { extractApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

const schema = z.object({
  modules_per_cfu: z.number().int().min(1).max(20),
  lessons_per_module: z.number().int().min(1).max(50),
  lesson_duration_minutes: z.number().int().min(1).max(240),
  assessment_lesson_enabled: z.boolean(),
  multiple_choice_questions_count: z.number().int().min(0).max(200),
  open_questions_count: z.number().int().min(0).max(50),
});

type FormValues = z.infer<typeof schema>;

const DEFAULTS: FormValues = {
  modules_per_cfu: 1,
  lessons_per_module: 8,
  lesson_duration_minutes: 15,
  assessment_lesson_enabled: true,
  multiple_choice_questions_count: 30,
  open_questions_count: 6,
};

export default function CourseSettingsPage() {
  const { orgId = "" } = useParams();
  const qc = useQueryClient();
  const { t } = useTranslation();

  const query = useQuery({
    queryKey: ["org", orgId, "course-settings"],
    queryFn: () => courseSettingsApi.get(orgId),
  });

  const defaults: FormValues = useMemo(
    () =>
      query.data
        ? {
            modules_per_cfu: query.data.modules_per_cfu,
            lessons_per_module: query.data.lessons_per_module,
            lesson_duration_minutes: query.data.lesson_duration_minutes,
            assessment_lesson_enabled: query.data.assessment_lesson_enabled,
            multiple_choice_questions_count:
              query.data.multiple_choice_questions_count,
            open_questions_count: query.data.open_questions_count,
          }
        : DEFAULTS,
    [query.data]
  );

  const form = useForm<FormValues>({
    defaultValues: defaults,
    resolver: zodResolver(schema),
    mode: "onChange",
  });

  useEffect(() => {
    if (query.data) form.reset(defaults);
  }, [query.data, defaults, form]);

  const watched = form.watch();
  const isDirty = form.formState.isDirty;

  const save = useMutation({
    mutationFn: (values: FormValues) => courseSettingsApi.update(orgId, values),
    onSuccess: (data) => {
      toast.success(t("courseSettings.saved"));
      qc.setQueryData(["org", orgId, "course-settings"], data);
      form.reset({
        modules_per_cfu: data.modules_per_cfu,
        lessons_per_module: data.lessons_per_module,
        lesson_duration_minutes: data.lesson_duration_minutes,
        assessment_lesson_enabled: data.assessment_lesson_enabled,
        multiple_choice_questions_count: data.multiple_choice_questions_count,
        open_questions_count: data.open_questions_count,
      });
    },
    onError: (err) => toast.error(extractApiError(err).message),
  });

  return (
    <TooltipProvider delayDuration={150}>
      <form
        onSubmit={form.handleSubmit((v) => save.mutate(v))}
        className={cn("space-y-6", isDirty && "pb-24")}
      >
        <PageHeader
          title={t("courseSettings.title")}
          description={t("courseSettings.subtitle")}
        />

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]">
          <div className="space-y-5">
            <SectionCard
              icon={<Layers className="size-4" />}
              title={t("courseSettings.sections.structure.title")}
              subtitle={t("courseSettings.sections.structure.subtitle")}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <Stepper
                  control={form.control}
                  name="modules_per_cfu"
                  label={t("courseSettings.fields.modulesPerCfu")}
                  suffix={t("courseSettings.units.modules")}
                  min={1}
                  max={20}
                />
                <Stepper
                  control={form.control}
                  name="lessons_per_module"
                  label={t("courseSettings.fields.lessonsPerModule")}
                  suffix={t("courseSettings.units.lessons")}
                  min={1}
                  max={50}
                />
              </div>
            </SectionCard>

            <SectionCard
              icon={<Clock className="size-4" />}
              title={t("courseSettings.sections.duration.title")}
              subtitle={t("courseSettings.sections.duration.subtitle")}
            >
              <Stepper
                control={form.control}
                name="lesson_duration_minutes"
                label={t("courseSettings.fields.lessonDuration")}
                suffix={t("courseSettings.units.minutes")}
                min={1}
                max={240}
                step={5}
              />
            </SectionCard>

            <Controller
              name="assessment_lesson_enabled"
              control={form.control}
              render={({ field }) => (
                <SectionCard
                  icon={<ClipboardCheck className="size-4" />}
                  title={t("courseSettings.fields.assessment")}
                  subtitle={t(
                    "courseSettings.sections.assessment.subtitle"
                  )}
                  iconAccent={field.value}
                  headerExtra={
                    <div className="flex items-center gap-2">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            aria-label={t("common.moreInfo")}
                            className="inline-flex shrink-0 text-muted-foreground transition-colors hover:text-foreground"
                          >
                            <HelpCircle className="size-4" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent
                          side="top"
                          className="max-w-xs whitespace-normal text-left leading-relaxed"
                        >
                          {t("courseSettings.fields.assessmentHelp")}
                        </TooltipContent>
                      </Tooltip>
                      <Switch
                        id="assessment_lesson_enabled"
                        checked={field.value}
                        onCheckedChange={field.onChange}
                        aria-label={t("courseSettings.fields.assessment")}
                      />
                    </div>
                  }
                >
                  {field.value ? (
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Stepper
                        control={form.control}
                        name="multiple_choice_questions_count"
                        label={t("courseSettings.fields.mcq")}
                        suffix={t("courseSettings.units.questions")}
                        icon={<ListChecks className="size-3.5" />}
                        min={0}
                        max={200}
                      />
                      <Stepper
                        control={form.control}
                        name="open_questions_count"
                        label={t("courseSettings.fields.openQuestions")}
                        suffix={t("courseSettings.units.questions")}
                        icon={<PenLine className="size-3.5" />}
                        min={0}
                        max={50}
                      />
                    </div>
                  ) : (
                    <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
                      {t("courseSettings.sections.assessment.disabledHint")}
                    </p>
                  )}
                </SectionCard>
              )}
            />
          </div>

          <aside className="lg:sticky lg:top-6 lg:self-start">
            <SummaryCard values={watched} />
          </aside>
        </div>

        {isDirty && (
          <div className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
            <div className="mx-auto flex max-w-screen-2xl items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
              <span className="truncate text-sm text-muted-foreground">
                {t("courseSettings.dirtyBanner")}
              </span>
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => form.reset(defaults)}
                  disabled={save.isPending}
                >
                  {t("common.cancel")}
                </Button>
                <Button
                  type="submit"
                  disabled={save.isPending || !form.formState.isValid}
                >
                  {save.isPending ? t("common.saving") : t("common.save")}
                </Button>
              </div>
            </div>
          </div>
        )}
      </form>
    </TooltipProvider>
  );
}

// --- Section card ---

function SectionCard({
  icon,
  title,
  subtitle,
  iconAccent,
  headerExtra,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  iconAccent?: boolean;
  headerExtra?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      <header className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="flex min-w-0 items-start gap-3">
          <div
            className={cn(
              "grid size-9 shrink-0 place-items-center rounded-md transition-colors",
              iconAccent
                ? "bg-primary/10 text-primary"
                : "bg-muted text-muted-foreground"
            )}
          >
            {icon}
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold leading-tight">{title}</h2>
            {subtitle && (
              <p className="mt-0.5 text-xs leading-snug text-muted-foreground">
                {subtitle}
              </p>
            )}
          </div>
        </div>
        {headerExtra && (
          <div className="flex shrink-0 items-center">{headerExtra}</div>
        )}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

// --- Stepper number input ---

type ControlT = ReturnType<typeof useForm<FormValues>>["control"];
type NumericName =
  | "modules_per_cfu"
  | "lessons_per_module"
  | "lesson_duration_minutes"
  | "multiple_choice_questions_count"
  | "open_questions_count";

function Stepper({
  control,
  name,
  label,
  suffix,
  icon,
  min,
  max,
  step = 1,
}: {
  control: ControlT;
  name: NumericName;
  label: string;
  suffix?: string;
  icon?: React.ReactNode;
  min: number;
  max: number;
  step?: number;
}) {
  return (
    <Controller
      name={name}
      control={control}
      render={({ field, fieldState }) => {
        const value = Number.isFinite(field.value) ? field.value : 0;
        const dec = () =>
          field.onChange(Math.max(min, value - step));
        const inc = () =>
          field.onChange(Math.min(max, value + step));
        return (
          <div className="space-y-2">
            <Label htmlFor={name} className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              {icon}
              {label}
            </Label>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={dec}
                disabled={value <= min}
                aria-label="Decrement"
                className="size-9 shrink-0"
              >
                <Minus className="size-4" />
              </Button>
              <Input
                id={name}
                type="number"
                inputMode="numeric"
                min={min}
                max={max}
                step={1}
                value={value}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (raw === "") {
                    field.onChange(min);
                    return;
                  }
                  const n = Number(raw);
                  if (Number.isFinite(n)) field.onChange(n);
                }}
                onBlur={field.onBlur}
                aria-invalid={!!fieldState.error}
                className="h-9 w-20 text-center font-medium tabular-nums"
              />
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={inc}
                disabled={value >= max}
                aria-label="Increment"
                className="size-9 shrink-0"
              >
                <Plus className="size-4" />
              </Button>
              {suffix && (
                <span className="truncate text-sm text-muted-foreground">
                  {suffix}
                </span>
              )}
            </div>
            {fieldState.error?.message && (
              <p className="text-xs text-destructive">{fieldState.error.message}</p>
            )}
          </div>
        );
      }}
    />
  );
}

// --- Summary side card ---

function SummaryCard({ values }: { values: FormValues }) {
  const { t } = useTranslation();
  const totalPerCfu = values.modules_per_cfu * values.lessons_per_module;
  const totalMinutesPerCfu = totalPerCfu * values.lesson_duration_minutes;
  const hours = Math.floor(totalMinutesPerCfu / 60);
  const minutes = totalMinutesPerCfu % 60;
  const totalQuestions =
    values.multiple_choice_questions_count + values.open_questions_count;

  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 p-5">
      <div className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        <Sparkles className="size-3.5" />
        {t("courseSettings.summary.title")}
      </div>
      <dl className="space-y-3 text-sm">
        <SummaryRow
          label={t("courseSettings.summary.modulesPerCfu")}
          value={String(values.modules_per_cfu)}
        />
        <SummaryRow
          label={t("courseSettings.summary.lessonsPerModule")}
          value={String(values.lessons_per_module)}
        />
        <SummaryRow
          label={t("courseSettings.summary.lessonDuration")}
          value={t("courseSettings.summary.minutesValue", {
            count: values.lesson_duration_minutes,
          })}
        />
        <div className="my-3 h-px bg-border" />
        <SummaryRow
          label={t("courseSettings.summary.totalLessonsPerCfu")}
          value={String(totalPerCfu)}
          accent
        />
        <SummaryRow
          label={t("courseSettings.summary.totalDurationPerCfu")}
          value={
            hours > 0
              ? minutes > 0
                ? t("courseSettings.summary.hoursAndMinutes", {
                    hours,
                    minutes,
                  })
                : t("courseSettings.summary.hours", { count: hours })
              : t("courseSettings.summary.minutesValue", { count: minutes })
          }
          accent
        />
        <div className="my-3 h-px bg-border" />
        {values.assessment_lesson_enabled ? (
          <>
            <SummaryRow
              label={t("courseSettings.summary.mcq")}
              value={String(values.multiple_choice_questions_count)}
            />
            <SummaryRow
              label={t("courseSettings.summary.openQuestions")}
              value={String(values.open_questions_count)}
            />
            <SummaryRow
              label={t("courseSettings.summary.totalQuestions")}
              value={String(totalQuestions)}
              accent
            />
          </>
        ) : (
          <p className="rounded-md bg-background px-3 py-2 text-xs text-muted-foreground">
            {t("courseSettings.summary.noAssessment")}
          </p>
        )}
      </dl>
    </div>
  );
}

function SummaryRow({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="truncate text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "shrink-0 tabular-nums",
          accent
            ? "text-base font-semibold text-foreground"
            : "text-sm font-medium text-foreground"
        )}
      >
        {value}
      </dd>
    </div>
  );
}
