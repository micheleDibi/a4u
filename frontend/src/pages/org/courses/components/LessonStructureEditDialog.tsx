import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowDown,
  ArrowUp,
  GraduationCap,
  ListChecks,
  ListOrdered,
  Plus,
  Target,
  Trash2,
} from "lucide-react";
import type {
  LessonStructureMandatoryTopic,
  LessonStructureSectionOutline,
  LessonStructureUpdateInput,
} from "@/api/courses";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

interface Props {
  open: boolean;
  /** Lingua del corso, usata per validazione client-side dei prefissi. */
  languageCode: string;
  /** Etichetta human-readable della lezione, es. "Lezione 3". */
  lessonLabel?: string;
  /** Etichetta human-readable del modulo padre, es. "Modulo 1". */
  moduleLabel?: string;
  initial: {
    learning_objectives: string[];
    mandatory_topics: LessonStructureMandatoryTopic[];
    prerequisites: string[];
    section_outline: LessonStructureSectionOutline[];
  };
  isPending: boolean;
  onClose: () => void;
  onSubmit: (payload: LessonStructureUpdateInput) => void;
}

const OBJECTIVE_PREFIXES: Record<string, string[]> = {
  it: ["lo studente sarà in grado di"],
  en: ["the student will be able to", "the student should be able to"],
};

function nextTopicId(existing: string[]): string {
  const used = new Set(existing);
  for (let i = 1; i < 1000; i++) {
    const candidate = `T${i}`;
    if (!used.has(candidate)) return candidate;
  }
  return `T${Date.now()}`;
}

function nextSectionId(existing: string[]): string {
  const used = new Set(existing);
  for (let i = 1; i < 1000; i++) {
    const candidate = `S${i}`;
    if (!used.has(candidate)) return candidate;
  }
  return `S${Date.now()}`;
}

export function LessonStructureEditDialog({
  open,
  languageCode,
  lessonLabel,
  moduleLabel,
  initial,
  isPending,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const [objectives, setObjectives] = useState<string[]>([]);
  const [topics, setTopics] = useState<LessonStructureMandatoryTopic[]>([]);
  const [prereqs, setPrereqs] = useState<string[]>([]);
  const [sections, setSections] = useState<LessonStructureSectionOutline[]>([]);
  const firstFieldRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open) return;
    setObjectives([...initial.learning_objectives]);
    setTopics(initial.mandatory_topics.map((t) => ({ ...t })));
    setPrereqs([...initial.prerequisites]);
    setSections(
      initial.section_outline.map((s) => ({
        ...s,
        covers_topic_ids: [...s.covers_topic_ids],
      }))
    );
    const id = window.setTimeout(() => firstFieldRef.current?.focus(), 80);
    return () => window.clearTimeout(id);
  }, [open, initial]);

  const topicIds = useMemo(() => topics.map((tt) => tt.topic_id), [topics]);

  const objectivePrefixWarnings = useMemo(() => {
    const prefixes = OBJECTIVE_PREFIXES[languageCode.toLowerCase()] ?? [];
    if (!prefixes.length) return new Set<number>();
    const bad = new Set<number>();
    objectives.forEach((o, idx) => {
      const lower = o.trim().toLowerCase();
      if (!lower) return;
      if (!prefixes.some((p) => lower.startsWith(p))) bad.add(idx);
    });
    return bad;
  }, [objectives, languageCode]);

  const duplicateTopicIds = useMemo(() => {
    const seen = new Set<string>();
    const dup = new Set<string>();
    topics.forEach((tt) => {
      if (seen.has(tt.topic_id)) dup.add(tt.topic_id);
      seen.add(tt.topic_id);
    });
    return dup;
  }, [topics]);

  const duplicateSectionIds = useMemo(() => {
    const seen = new Set<string>();
    const dup = new Set<string>();
    sections.forEach((s) => {
      if (seen.has(s.section_id)) dup.add(s.section_id);
      seen.add(s.section_id);
    });
    return dup;
  }, [sections]);

  const uncoveredTopicIds = useMemo(() => {
    const covered = new Set<string>();
    sections.forEach((s) => s.covers_topic_ids.forEach((c) => covered.add(c)));
    return topicIds.filter((tid) => !covered.has(tid));
  }, [sections, topicIds]);

  const hasErrors =
    objectives.filter((o) => o.trim().length > 0).length === 0 ||
    duplicateTopicIds.size > 0 ||
    duplicateSectionIds.size > 0 ||
    topics.some((tt) => !tt.topic_id.trim() || !tt.topic.trim()) ||
    sections.some((s) => !s.section_id.trim() || !s.title.trim());

  const submit = () => {
    if (hasErrors || isPending) return;
    onSubmit({
      learning_objectives: objectives.map((o) => o.trim()).filter(Boolean),
      mandatory_topics: topics.map((tt) => ({
        topic_id: tt.topic_id.trim(),
        topic: tt.topic.trim(),
        rationale: tt.rationale.trim(),
      })),
      prerequisites: prereqs.map((p) => p.trim()).filter(Boolean),
      section_outline: sections.map((s) => ({
        section_id: s.section_id.trim(),
        title: s.title.trim(),
        purpose: s.purpose.trim(),
        covers_topic_ids: s.covers_topic_ids.filter((c) => c.trim().length > 0),
      })),
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  // Helpers — obiettivi
  const moveObjective = (idx: number, dir: -1 | 1) => {
    const j = idx + dir;
    if (j < 0 || j >= objectives.length) return;
    const next = [...objectives];
    [next[idx], next[j]] = [next[j], next[idx]];
    setObjectives(next);
  };

  // Helpers — sezioni
  const moveSection = (idx: number, dir: -1 | 1) => {
    const j = idx + dir;
    if (j < 0 || j >= sections.length) return;
    const next = [...sections];
    [next[idx], next[j]] = [next[j], next[idx]];
    setSections(next);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (!v && !isPending ? onClose() : undefined)}
    >
      <DialogContent className="max-w-4xl" onKeyDown={handleKeyDown}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {lessonLabel && (
              <Badge variant="outline" className="font-mono text-xs">
                {lessonLabel}
              </Badge>
            )}
            {t("courses.lessonsStructure.lesson.editTitle")}
            {moduleLabel && (
              <span className="text-sm font-normal text-muted-foreground">
                — {moduleLabel}
              </span>
            )}
          </DialogTitle>
          <DialogDescription>
            {t("courses.lessonsStructure.lesson.editDescription")}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[70vh] pr-3">
          <div className="space-y-6 py-2">
            {/* === Obiettivi formativi === */}
            <Section
              icon={<Target className="size-4" />}
              title={t("courses.lessonsStructure.fields.learningObjectives")}
              hint={t("courses.lessonsStructure.lesson.objective.prefixHint", {
                language: languageCode.toUpperCase(),
              })}
            >
              <div className="space-y-2">
                {objectives.length === 0 && (
                  <p className="rounded border border-dashed p-3 text-center text-xs text-muted-foreground">
                    {t("courses.lessonsStructure.lesson.objective.empty")}
                  </p>
                )}
                {objectives.map((o, idx) => {
                  const warn = objectivePrefixWarnings.has(idx);
                  return (
                    <div key={idx} className="flex items-start gap-2">
                      <div className="flex flex-col gap-0.5 pt-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-6"
                          disabled={idx === 0 || isPending}
                          onClick={() => moveObjective(idx, -1)}
                        >
                          <ArrowUp className="size-3" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-6"
                          disabled={idx === objectives.length - 1 || isPending}
                          onClick={() => moveObjective(idx, 1)}
                        >
                          <ArrowDown className="size-3" />
                        </Button>
                      </div>
                      <div className="flex-1 space-y-1">
                        <Textarea
                          ref={idx === 0 ? firstFieldRef : undefined}
                          rows={2}
                          value={o}
                          maxLength={400}
                          onChange={(e) => {
                            const next = [...objectives];
                            next[idx] = e.target.value;
                            setObjectives(next);
                          }}
                          disabled={isPending}
                          placeholder={t(
                            "courses.lessonsStructure.lesson.objective.placeholder"
                          )}
                          className={cn(
                            "resize-y",
                            warn && "border-amber-500/60"
                          )}
                        />
                        {warn && (
                          <p className="text-xs text-amber-600">
                            {t(
                              "courses.lessonsStructure.lesson.objective.prefixWarning"
                            )}
                          </p>
                        )}
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-7 text-destructive"
                        onClick={() =>
                          setObjectives(objectives.filter((_, i) => i !== idx))
                        }
                        disabled={isPending}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  );
                })}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setObjectives([...objectives, ""])}
                  disabled={isPending || objectives.length >= 10}
                >
                  <Plus className="size-3.5" />
                  {t("courses.lessonsStructure.lesson.objective.add")}
                </Button>
              </div>
            </Section>

            {/* === Temi obbligatori === */}
            <Section
              icon={<GraduationCap className="size-4" />}
              title={t("courses.lessonsStructure.fields.mandatoryTopics")}
              hint={t("courses.lessonsStructure.lesson.topic.hint")}
            >
              <div className="space-y-3">
                {topics.length === 0 && (
                  <p className="rounded border border-dashed p-3 text-center text-xs text-muted-foreground">
                    {t("courses.lessonsStructure.lesson.topic.empty")}
                  </p>
                )}
                {topics.map((tt, idx) => {
                  const dup = duplicateTopicIds.has(tt.topic_id);
                  return (
                    <div
                      key={idx}
                      className="space-y-2 rounded-md border bg-muted/10 p-3"
                    >
                      <div className="flex items-start gap-2">
                        <div className="flex-1 grid gap-2 sm:grid-cols-[120px_1fr]">
                          <div className="space-y-1">
                            <Label className="text-xs">
                              {t(
                                "courses.lessonsStructure.lesson.topic.fields.topicId"
                              )}
                            </Label>
                            <Input
                              value={tt.topic_id}
                              maxLength={20}
                              onChange={(e) => {
                                const next = [...topics];
                                next[idx] = { ...tt, topic_id: e.target.value };
                                setTopics(next);
                              }}
                              disabled={isPending}
                              className={cn(
                                "font-mono text-sm",
                                dup && "border-destructive"
                              )}
                            />
                            {dup && (
                              <p className="text-[11px] text-destructive">
                                {t(
                                  "courses.lessonsStructure.lesson.topic.duplicateId"
                                )}
                              </p>
                            )}
                          </div>
                          <div className="space-y-1">
                            <Label className="text-xs">
                              {t(
                                "courses.lessonsStructure.lesson.topic.fields.topic"
                              )}
                            </Label>
                            <Input
                              value={tt.topic}
                              maxLength={400}
                              onChange={(e) => {
                                const next = [...topics];
                                next[idx] = { ...tt, topic: e.target.value };
                                setTopics(next);
                              }}
                              disabled={isPending}
                              placeholder={t(
                                "courses.lessonsStructure.lesson.topic.fields.topicPlaceholder"
                              )}
                            />
                          </div>
                          <div className="space-y-1 sm:col-span-2">
                            <Label className="text-xs">
                              {t(
                                "courses.lessonsStructure.lesson.topic.fields.rationale"
                              )}
                            </Label>
                            <Textarea
                              rows={2}
                              value={tt.rationale}
                              maxLength={1000}
                              onChange={(e) => {
                                const next = [...topics];
                                next[idx] = {
                                  ...tt,
                                  rationale: e.target.value,
                                };
                                setTopics(next);
                              }}
                              disabled={isPending}
                              placeholder={t(
                                "courses.lessonsStructure.lesson.topic.fields.rationalePlaceholder"
                              )}
                              className="resize-y"
                            />
                          </div>
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-7 text-destructive"
                          onClick={() => {
                            const removedId = tt.topic_id;
                            setTopics(topics.filter((_, i) => i !== idx));
                            // Clean up coverage references to removed topic
                            setSections(
                              sections.map((s) => ({
                                ...s,
                                covers_topic_ids: s.covers_topic_ids.filter(
                                  (c) => c !== removedId
                                ),
                              }))
                            );
                          }}
                          disabled={isPending}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    const newId = nextTopicId(topicIds);
                    setTopics([
                      ...topics,
                      { topic_id: newId, topic: "", rationale: "" },
                    ]);
                  }}
                  disabled={isPending || topics.length >= 10}
                >
                  <Plus className="size-3.5" />
                  {t("courses.lessonsStructure.lesson.topic.add")}
                </Button>
              </div>
            </Section>

            {/* === Prerequisiti === */}
            <Section
              icon={<ListChecks className="size-4" />}
              title={t("courses.lessonsStructure.fields.prerequisites")}
              hint={t("courses.lessonsStructure.lesson.prerequisite.hint")}
            >
              <div className="space-y-2">
                {prereqs.length === 0 && (
                  <p className="rounded border border-dashed p-3 text-center text-xs text-muted-foreground">
                    {t("courses.lessonsStructure.lesson.prerequisite.empty")}
                  </p>
                )}
                {prereqs.map((p, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <Input
                      value={p}
                      maxLength={300}
                      onChange={(e) => {
                        const next = [...prereqs];
                        next[idx] = e.target.value;
                        setPrereqs(next);
                      }}
                      disabled={isPending}
                      placeholder={t(
                        "courses.lessonsStructure.lesson.prerequisite.placeholder"
                      )}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-7 text-destructive"
                      onClick={() =>
                        setPrereqs(prereqs.filter((_, i) => i !== idx))
                      }
                      disabled={isPending}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setPrereqs([...prereqs, ""])}
                  disabled={isPending || prereqs.length >= 20}
                >
                  <Plus className="size-3.5" />
                  {t("courses.lessonsStructure.lesson.prerequisite.add")}
                </Button>
              </div>
            </Section>

            {/* === Scaletta sezioni === */}
            <Section
              icon={<ListOrdered className="size-4" />}
              title={t("courses.lessonsStructure.fields.sectionOutline")}
              hint={t("courses.lessonsStructure.lesson.section.hint")}
            >
              {uncoveredTopicIds.length > 0 && (
                <div className="rounded-md border border-amber-500/40 bg-amber-50/40 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
                  {t(
                    "courses.lessonsStructure.lesson.section.uncoveredWarning",
                    { ids: uncoveredTopicIds.join(", ") }
                  )}
                </div>
              )}
              <div className="space-y-3">
                {sections.length === 0 && (
                  <p className="rounded border border-dashed p-3 text-center text-xs text-muted-foreground">
                    {t("courses.lessonsStructure.lesson.section.empty")}
                  </p>
                )}
                {sections.map((s, idx) => {
                  const dup = duplicateSectionIds.has(s.section_id);
                  return (
                    <div
                      key={idx}
                      className="space-y-2 rounded-md border bg-muted/10 p-3"
                    >
                      <div className="flex items-start gap-2">
                        <div className="flex flex-col gap-0.5 pt-1">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="size-6"
                            disabled={idx === 0 || isPending}
                            onClick={() => moveSection(idx, -1)}
                          >
                            <ArrowUp className="size-3" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="size-6"
                            disabled={idx === sections.length - 1 || isPending}
                            onClick={() => moveSection(idx, 1)}
                          >
                            <ArrowDown className="size-3" />
                          </Button>
                        </div>
                        <div className="flex-1 grid gap-2 sm:grid-cols-[120px_1fr]">
                          <div className="space-y-1">
                            <Label className="text-xs">
                              {t(
                                "courses.lessonsStructure.lesson.section.fields.sectionId"
                              )}
                            </Label>
                            <Input
                              value={s.section_id}
                              maxLength={20}
                              onChange={(e) => {
                                const next = [...sections];
                                next[idx] = {
                                  ...s,
                                  section_id: e.target.value,
                                };
                                setSections(next);
                              }}
                              disabled={isPending}
                              className={cn(
                                "font-mono text-sm",
                                dup && "border-destructive"
                              )}
                            />
                            {dup && (
                              <p className="text-[11px] text-destructive">
                                {t(
                                  "courses.lessonsStructure.lesson.section.duplicateId"
                                )}
                              </p>
                            )}
                          </div>
                          <div className="space-y-1">
                            <Label className="text-xs">
                              {t(
                                "courses.lessonsStructure.lesson.section.fields.title"
                              )}
                            </Label>
                            <Input
                              value={s.title}
                              maxLength={200}
                              onChange={(e) => {
                                const next = [...sections];
                                next[idx] = { ...s, title: e.target.value };
                                setSections(next);
                              }}
                              disabled={isPending}
                              placeholder={t(
                                "courses.lessonsStructure.lesson.section.fields.titlePlaceholder"
                              )}
                            />
                          </div>
                          <div className="space-y-1 sm:col-span-2">
                            <Label className="text-xs">
                              {t(
                                "courses.lessonsStructure.lesson.section.fields.purpose"
                              )}
                            </Label>
                            <Textarea
                              rows={2}
                              value={s.purpose}
                              maxLength={1000}
                              onChange={(e) => {
                                const next = [...sections];
                                next[idx] = { ...s, purpose: e.target.value };
                                setSections(next);
                              }}
                              disabled={isPending}
                              className="resize-y"
                            />
                          </div>
                          <div className="space-y-2 sm:col-span-2">
                            <Label className="text-xs">
                              {t(
                                "courses.lessonsStructure.lesson.section.fields.coversTopics"
                              )}
                            </Label>
                            <div className="flex flex-wrap gap-2">
                              {topics.length === 0 ? (
                                <span className="text-xs text-muted-foreground">
                                  {t(
                                    "courses.lessonsStructure.lesson.section.fields.coversNoTopics"
                                  )}
                                </span>
                              ) : (
                                topics.map((tt) => {
                                  const checked =
                                    s.covers_topic_ids.includes(tt.topic_id);
                                  return (
                                    <label
                                      key={tt.topic_id}
                                      className={cn(
                                        "flex cursor-pointer items-center gap-1.5 rounded border px-2 py-1 text-xs",
                                        checked
                                          ? "border-primary bg-primary/10 text-foreground"
                                          : "border-border bg-muted/30 text-muted-foreground hover:bg-muted/50"
                                      )}
                                    >
                                      <Checkbox
                                        checked={checked}
                                        onCheckedChange={(v) => {
                                          const next = [...sections];
                                          if (v === true) {
                                            next[idx] = {
                                              ...s,
                                              covers_topic_ids: [
                                                ...s.covers_topic_ids,
                                                tt.topic_id,
                                              ],
                                            };
                                          } else {
                                            next[idx] = {
                                              ...s,
                                              covers_topic_ids:
                                                s.covers_topic_ids.filter(
                                                  (c) => c !== tt.topic_id
                                                ),
                                            };
                                          }
                                          setSections(next);
                                        }}
                                        disabled={isPending}
                                        className="size-3.5"
                                      />
                                      <span className="font-mono text-[11px]">
                                        {tt.topic_id}
                                      </span>
                                    </label>
                                  );
                                })
                              )}
                            </div>
                          </div>
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-7 text-destructive"
                          onClick={() =>
                            setSections(sections.filter((_, i) => i !== idx))
                          }
                          disabled={isPending}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    const newId = nextSectionId(
                      sections.map((s) => s.section_id)
                    );
                    setSections([
                      ...sections,
                      {
                        section_id: newId,
                        title: "",
                        purpose: "",
                        covers_topic_ids: [],
                      },
                    ]);
                  }}
                  disabled={isPending || sections.length >= 10}
                >
                  <Plus className="size-3.5" />
                  {t("courses.lessonsStructure.lesson.section.add")}
                </Button>
              </div>
            </Section>
          </div>
        </ScrollArea>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="ghost" onClick={onClose} disabled={isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={submit}
            disabled={hasErrors || isPending}
            className="min-w-[120px]"
          >
            {isPending ? t("common.saving") : t("common.save")}
            <kbd className="ms-2 hidden rounded border border-primary-foreground/30 px-1.5 py-0.5 font-mono text-[10px] sm:inline">
              ⌘↵
            </kbd>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface SectionProps {
  icon: React.ReactNode;
  title: string;
  hint?: string;
  children: React.ReactNode;
}

function Section({ icon, title, hint, children }: SectionProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {icon}
        <h4 className="text-sm font-semibold">{title}</h4>
      </div>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {children}
    </div>
  );
}
