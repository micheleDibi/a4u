import { useQuery } from "@tanstack/react-query";
import { i18nApi, type PublicLanguageOut } from "@/api/i18n";
import { SUPPORTED_LANGS } from "@/i18n";

const FALLBACK: PublicLanguageOut[] = SUPPORTED_LANGS.map((l) => ({
  code: l.code,
  name_native: l.name,
  rtl: false,
  flag_country_code: l.code.toUpperCase().slice(0, 2),
  is_default: l.code === "it",
}));

export function useLanguages() {
  const query = useQuery({
    queryKey: ["i18n", "languages", "public"],
    queryFn: i18nApi.publicLanguages,
    staleTime: 5 * 60 * 1000,
    placeholderData: FALLBACK,
  });
  return query.data ?? FALLBACK;
}
