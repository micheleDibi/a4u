import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useTaxonomyTerms } from "@/hooks/useTaxonomyTerms";
import type { TaxonomyTermOut, TaxonomyType } from "@/api/courseTaxonomy";

const NULL_VALUE = "__none__";

interface Props {
  taxonomyType: TaxonomyType;
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  placeholder?: string;
  hierarchical?: boolean; // se true, indenta i children dei parent
}

export function TaxonomyTermSelect({
  taxonomyType,
  value,
  onChange,
  disabled,
  placeholder,
  hierarchical,
}: Props) {
  const { t, i18n } = useTranslation();
  const query = useTaxonomyTerms(taxonomyType);
  const terms = query.data ?? [];

  // Lingua corrente per la label visualizzata, con fallback al codice corto e a 'it'.
  const currentLng = i18n.resolvedLanguage || i18n.language || "it";
  const shortLng = currentLng.split("-")[0];
  const labelOf = (term: TaxonomyTermOut) =>
    term.labels[currentLng] ||
    term.labels[shortLng] ||
    term.labels["it"] ||
    term.slug;

  const orderedItems = useMemo(() => {
    if (!hierarchical) {
      return terms.map((t) => ({ term: t, depth: 0 }));
    }
    // Raggruppa parent + children e li mette in una lista flat con indentazione.
    const parents = terms.filter((t) => t.parent_id === null);
    const childrenByParent = new Map<string, TaxonomyTermOut[]>();
    for (const t of terms) {
      if (t.parent_id) {
        const list = childrenByParent.get(t.parent_id) ?? [];
        list.push(t);
        childrenByParent.set(t.parent_id, list);
      }
    }
    const out: { term: TaxonomyTermOut; depth: number }[] = [];
    for (const p of parents) {
      out.push({ term: p, depth: 0 });
      for (const c of childrenByParent.get(p.id) ?? []) {
        out.push({ term: c, depth: 1 });
      }
    }
    return out;
  }, [terms, hierarchical]);

  return (
    <Select
      value={value ?? NULL_VALUE}
      onValueChange={(v) => onChange(v === NULL_VALUE ? null : v)}
      disabled={disabled}
    >
      <SelectTrigger>
        <SelectValue placeholder={placeholder ?? t("common.select")} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NULL_VALUE}>
          <span className="text-muted-foreground">{t("common.none")}</span>
        </SelectItem>
        {orderedItems.map(({ term, depth }) => (
          <SelectItem
            key={term.id}
            value={term.id}
            className={depth > 0 ? "ps-7" : "font-medium"}
          >
            {depth > 0 && (
              <span className="text-muted-foreground">└ </span>
            )}
            {labelOf(term)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
