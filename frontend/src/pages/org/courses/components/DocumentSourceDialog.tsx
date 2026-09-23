import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  coursesApi,
  type CourseDocumentOut,
  type CourseDocumentUpdate,
  type DocumentBibliographyInput,
  type DocumentLicense,
} from "@/api/courses";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { extractApiError } from "@/lib/errors";

/**
 * Fonte delle figure di un documento: bibliografia (salvata come
 * `bibliography_source="user"`), licenza e «materiale mio». Sono i dati
 * della riga «Fonte» che il backend compone per ogni figura del documento;
 * il frontend non la ricompone mai. La proposta del riassunto AI si può
 * caricare nei campi, ma diventa bibliografia solo se il docente salva.
 * Il salvataggio invia solo i campi cambiati: cambiare la licenza non
 * trasforma in «docente» una bibliografia letta dai metadati, da Crossref
 * o da OpenAlex, e i campi senza controllo nel form (editore, id OpenAlex)
 * restano.
 */
const DOCUMENT_LICENSES: DocumentLicense[] = [
  "cc0",
  "public_domain",
  "cc_by",
  "cc_by_sa",
  "cc_by_nc",
  "cc_by_nd",
  "cc_by_nc_sa",
  "cc_by_nc_nd",
  "all_rights_reserved",
  "other",
];

const UNKNOWN = "__unknown__";

interface Props {
  open: boolean;
  doc: CourseDocumentOut | null;
  orgId: string;
  courseId: string;
  onClose: () => void;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}

interface BibFields {
  title: string;
  authors: string;
  year: string;
  container: string;
  doi: string;
  url: string;
}

function bibFields(doc: CourseDocumentOut): BibFields {
  const bib = doc.bibliography ?? {};
  return {
    title: str(bib.title),
    authors: Array.isArray(bib.authors) ? bib.authors.map(str).join("\n") : "",
    year: str(bib.year),
    container: str(bib.container),
    doi: str(bib.doi),
    url: str(bib.url),
  };
}

export function DocumentSourceDialog({ open, doc, orgId, courseId, onClose }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [authors, setAuthors] = useState("");
  const [year, setYear] = useState("");
  const [container, setContainer] = useState("");
  const [doi, setDoi] = useState("");
  const [url, setUrl] = useState("");
  const [license, setLicense] = useState<string>(UNKNOWN);
  const [ownWork, setOwnWork] = useState(false);
  const [fromProposal, setFromProposal] = useState(false);

  useEffect(() => {
    if (!doc) return;
    const fields = bibFields(doc);
    setTitle(fields.title);
    setAuthors(fields.authors);
    setYear(fields.year);
    setContainer(fields.container);
    setDoi(fields.doi);
    setUrl(fields.url);
    setLicense(doc.license ?? UNKNOWN);
    setOwnWork(doc.is_own_work);
    setFromProposal(false);
  }, [doc]);

  const loadProposal = async () => {
    if (!doc) return;
    try {
      const detail = await coursesApi.documents.get(orgId, courseId, doc.id, {
        includeSummary: true,
      });
      const summary = detail.summary;
      if (!summary) return;
      if (summary.source_title) setTitle(summary.source_title);
      const names = summary.authors_and_references
        .filter((a) => a.type === "author")
        .map((a) => a.value);
      if (names.length) setAuthors(names.join("\n"));
      setFromProposal(true);
    } catch (err) {
      toast.error(extractApiError(err).message ?? t("courses.docs.figures.meta.saveFailed"));
    }
  };

  const saveMut = useMutation({
    mutationFn: async () => {
      const current = doc!;
      const initial = bibFields(current);
      const edited: BibFields = { title, authors, year, container, doi, url };
      const bibChanged =
        fromProposal ||
        (Object.keys(initial) as (keyof BibFields)[]).some(
          (key) => initial[key].trim() !== edited[key].trim(),
        );
      const update: CourseDocumentUpdate = {};
      if (bibChanged) {
        const authorList = authors
          .split("\n")
          .map((a) => a.trim())
          .filter(Boolean);
        const yearNumber = Number.parseInt(year, 10);
        const previous = current.bibliography ?? {};
        const bibliography: DocumentBibliographyInput = {
          title: title.trim() || null,
          authors: authorList,
          year: Number.isFinite(yearNumber) ? yearNumber : null,
          container: container.trim() || null,
          doi: doi.trim() || null,
          url: url.trim() || null,
          // Campi senza controllo nel form: restano come sono.
          publisher: str(previous.publisher) || null,
          openalex_id: str(previous.openalex_id) || null,
        };
        const empty =
          !bibliography.title &&
          authorList.length === 0 &&
          !bibliography.year &&
          !bibliography.container &&
          !bibliography.doi &&
          !bibliography.url &&
          !bibliography.publisher;
        update.bibliography = empty ? null : bibliography;
      }
      const nextLicense = license === UNKNOWN ? null : (license as DocumentLicense);
      if (nextLicense !== (current.license ?? null)) update.license = nextLicense;
      if (ownWork !== current.is_own_work) update.is_own_work = ownWork;
      if (Object.keys(update).length === 0) return current;
      return coursesApi.documents.update(orgId, courseId, current.id, update);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["courses", "detail", orgId, courseId] });
      qc.invalidateQueries({ queryKey: ["document-figures", orgId, courseId] });
      toast.success(t("courses.docs.figures.meta.saved"));
      onClose();
    },
    onError: (err) =>
      toast.error(extractApiError(err).message ?? t("courses.docs.figures.meta.saveFailed")),
  });

  const sourceKey = doc?.bibliography_source;
  return (
    <Dialog open={open && !!doc} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t("courses.docs.figures.meta.title")}</DialogTitle>
          <DialogDescription>
            {t("courses.docs.figures.meta.description")}
            {sourceKey && sourceKey !== "summary_proposal"
              ? ` (${t(`courses.docs.figures.meta.sourceLabel.${sourceKey}`)})`
              : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {doc?.summary_status === "ready" && (
            <Button type="button" variant="outline" size="sm" onClick={loadProposal}>
              {t("courses.docs.figures.meta.useProposal")}
            </Button>
          )}
          {fromProposal && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              {t("courses.docs.figures.meta.proposalHint")}
            </p>
          )}
          <div className="space-y-1.5">
            <Label>{t("courses.docs.figures.meta.bibTitle")}</Label>
            <Input value={title} maxLength={500} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label>{t("courses.docs.figures.meta.authors")}</Label>
            <Textarea rows={3} value={authors} onChange={(e) => setAuthors(e.target.value)} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>{t("courses.docs.figures.meta.year")}</Label>
              <Input
                value={year}
                inputMode="numeric"
                maxLength={4}
                onChange={(e) => setYear(e.target.value.replace(/[^0-9]/g, ""))}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t("courses.docs.figures.meta.container")}</Label>
              <Input value={container} onChange={(e) => setContainer(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>{t("courses.docs.figures.meta.doi")}</Label>
              <Input value={doi} onChange={(e) => setDoi(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>{t("courses.docs.figures.meta.url")}</Label>
              <Input value={url} onChange={(e) => setUrl(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>{t("courses.docs.figures.meta.license")}</Label>
            <Select value={license} onValueChange={setLicense}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={UNKNOWN}>
                  {t("courses.docs.figures.meta.licenseUnknown")}
                </SelectItem>
                {DOCUMENT_LICENSES.map((code) => (
                  <SelectItem key={code} value={code}>
                    {t(`courses.docs.figures.licenses.${code}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={ownWork} onCheckedChange={(v) => setOwnWork(v === true)} />
            {t("courses.docs.figures.meta.ownWork")}
          </label>
        </div>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button type="button" onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
            {t("courses.docs.figures.meta.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default DocumentSourceDialog;
