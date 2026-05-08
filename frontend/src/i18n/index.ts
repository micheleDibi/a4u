import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import bg from "./locales/bg.json";
import cs from "./locales/cs.json";
import da from "./locales/da.json";
import de from "./locales/de.json";
import el from "./locales/el.json";
import en from "./locales/en.json";
import es from "./locales/es.json";
import et from "./locales/et.json";
import fi from "./locales/fi.json";
import fr from "./locales/fr.json";
import ga from "./locales/ga.json";
import hr from "./locales/hr.json";
import hu from "./locales/hu.json";
import it from "./locales/it.json";
import lt from "./locales/lt.json";
import lv from "./locales/lv.json";
import mt from "./locales/mt.json";
import nl from "./locales/nl.json";
import pl from "./locales/pl.json";
import pt from "./locales/pt.json";
import ro from "./locales/ro.json";
import sk from "./locales/sk.json";
import sl from "./locales/sl.json";
import sv from "./locales/sv.json";

/**
 * Lista bundled (statica al build): usata come fallback offline e per il
 * primo paint. La lista "viva" viene fetched a runtime tramite
 * GET /api/v1/i18n/languages e cacheata da TanStack Query (vedi
 * `useLanguages` in src/hooks).
 */
export const SUPPORTED_LANGS = [
  { code: "bg", name: "Български" },
  { code: "cs", name: "Čeština" },
  { code: "da", name: "Dansk" },
  { code: "de", name: "Deutsch" },
  { code: "el", name: "Ελληνικά" },
  { code: "en", name: "English" },
  { code: "es", name: "Español" },
  { code: "et", name: "Eesti" },
  { code: "fi", name: "Suomi" },
  { code: "fr", name: "Français" },
  { code: "ga", name: "Gaeilge" },
  { code: "hr", name: "Hrvatski" },
  { code: "hu", name: "Magyar" },
  { code: "it", name: "Italiano" },
  { code: "lt", name: "Lietuvių" },
  { code: "lv", name: "Latviešu" },
  { code: "mt", name: "Malti" },
  { code: "nl", name: "Nederlands" },
  { code: "pl", name: "Polski" },
  { code: "pt", name: "Português" },
  { code: "ro", name: "Română" },
  { code: "sk", name: "Slovenčina" },
  { code: "sl", name: "Slovenščina" },
  { code: "sv", name: "Svenska" },
] as const;

export type LangCode = string;

const bundledResources = {
  bg: { translation: bg },
  cs: { translation: cs },
  da: { translation: da },
  de: { translation: de },
  el: { translation: el },
  en: { translation: en },
  es: { translation: es },
  et: { translation: et },
  fi: { translation: fi },
  fr: { translation: fr },
  ga: { translation: ga },
  hr: { translation: hr },
  hu: { translation: hu },
  it: { translation: it },
  lt: { translation: lt },
  lv: { translation: lv },
  mt: { translation: mt },
  nl: { translation: nl },
  pl: { translation: pl },
  pt: { translation: pt },
  ro: { translation: ro },
  sk: { translation: sk },
  sl: { translation: sl },
  sv: { translation: sv },
};

/** Converte un dict di chiavi flat (es. `{"a.b.c": "x"}`) in nested. */
export function flatToNested(flat: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(flat)) {
    const parts = key.split(".");
    let cur = out;
    for (let i = 0; i < parts.length - 1; i++) {
      const k = parts[i];
      if (typeof cur[k] !== "object" || cur[k] === null) cur[k] = {} as Record<string, unknown>;
      cur = cur[k] as Record<string, unknown>;
    }
    cur[parts[parts.length - 1]] = value;
  }
  return out;
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: bundledResources,
    fallbackLng: "it",
    nonExplicitSupportedLngs: true,
    debug: import.meta.env.DEV,
    interpolation: { escapeValue: false },
    // I codici dei permessi contengono `:` (es. `member:view`,
    // `template:slide:manage`). i18next per default tratta `:` come
    // separatore di namespace, quindi `t("permissions.member:view")` viene
    // interpretato come ns=`permissions.member` key=`view`. Disattiviamo il
    // separatore: nessun namespace è esposto via stringa, lavoriamo con
    // l'unico namespace `translation` di default.
    nsSeparator: false,
    detection: {
      order: ["localStorage", "navigator", "htmlTag"],
      caches: ["localStorage"],
      lookupLocalStorage: "i18nextLng",
    },
  });

const apiBase = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";
const fetchedLangs = new Set<string>();

async function fetchAndMerge(lng: string): Promise<void> {
  if (!lng) return;
  if (fetchedLangs.has(lng)) return;
  fetchedLangs.add(lng);
  try {
    const r = await fetch(`${apiBase}/i18n/translations/${lng}`, {
      credentials: "include",
    });
    if (!r.ok) return;
    const data = (await r.json()) as { code: string; translations: Record<string, string> };
    if (!data.translations || Object.keys(data.translations).length === 0) return;
    const nested = flatToNested(data.translations);
    i18n.addResourceBundle(lng, "translation", nested, true, true);
  } catch {
    // offline / errore di rete: usiamo i bundle locali
  }
}

/**
 * Forza la rifetch delle traduzioni dal DB per `lng`, ignorando la cache di
 * `fetchedLangs`. Da chiamare dopo operazioni admin che modificano il DB
 * (auto-translate, clear, edit bulk) per riallineare l'UI senza reload pagina.
 */
export async function reloadDbTranslations(lng: string): Promise<void> {
  if (!lng) return;
  fetchedLangs.delete(lng);
  await fetchAndMerge(lng);
}

const applyHtmlAttributes = (lng: string) => {
  document.documentElement.lang = lng;
  document.documentElement.dir = i18n.dir(lng);
};

i18n.on("languageChanged", (lng: string) => {
  applyHtmlAttributes(lng);
  void fetchAndMerge(lng);
});

const initialLng = i18n.language || i18n.options.fallbackLng?.toString() || "it";
applyHtmlAttributes(initialLng);
void fetchAndMerge(initialLng);

export default i18n;
