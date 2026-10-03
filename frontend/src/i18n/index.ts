import i18n, { type BackendModule } from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

// Solo italiano (lingua di fallback) ed inglese stanno nel bundle principale.
// Le altre 22 lingue (16-21 KB minificati l'una) sono chunk separati caricati quando
// servono: vedi `lazyLocaleBackend` più sotto.
import en from "./locales/en.json";
import it from "./locales/it.json";

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
  en: { translation: en },
  it: { translation: it },
};

/** Loader dei locale non inclusi nel bundle, per percorso (`./locales/de.json`). */
const lazyLocales = import.meta.glob<Record<string, unknown>>(
  ["./locales/*.json", "!./locales/it.json", "!./locales/en.json"],
  { import: "default" },
);

/**
 * Tentativi di i18next su un chunk di lingua che non arriva, e attesa prima
 * del primo (raddoppia a ogni tentativo). Budget volutamente corto: finché i
 * tentativi non finiscono `changeLanguage` non cambia lingua e, all'avvio, la
 * pagina resta vuota. Chromium tiene in cache l'`import()` fallito (misurato:
 * una sola richiesta anche con 5 tentativi), quindi lì un tentativo in più è
 * solo attesa (~11 s con i default di i18next); serve ai browser che rifanno
 * la richiesta.
 */
const LOCALE_LOAD_RETRIES = 1;
const LOCALE_RETRY_DELAY_MS = 300;

/** Un solo caricamento per lingua, condiviso da i18next e da `fetchAndMerge`. */
const localeLoads = new Map<string, Promise<void>>();

/**
 * Porta nello store il bundle statico di `lng`, se esiste fra i chunk.
 * Il merge è profondo e SENZA sovrascrittura: se gli override dal DB sono già
 * arrivati (per esempio `reloadDbTranslations` su una lingua non ancora
 * aperta) restano loro a vincere, come quando tutti i bundle erano statici.
 * Su errore (chunk irraggiungibile, deploy nel mezzo) la voce si toglie dalla
 * mappa PRIMA che il backend qui sotto risponda a i18next, così ognuno dei
 * suoi tentativi rifà davvero l'`import()` (alcuni browser tengono comunque in
 * cache l'import fallito: lì riprova solo un reload della pagina).
 */
function loadLocaleBundle(lng: string): Promise<void> {
  let pending = localeLoads.get(lng);
  if (!pending) {
    const loader = lazyLocales[`./locales/${lng}.json`];
    pending = loader
      ? loader().then((data) => {
          i18n.addResourceBundle(lng, "translation", data, true, false);
        })
      : Promise.resolve();
    pending.catch(() => localeLoads.delete(lng));
    localeLoads.set(lng, pending);
  }
  return pending;
}

/**
 * Backend di i18next per i locale non inclusi nel bundle. Con
 * `partialBundledLanguages` i18next lo interroga per ogni lingua che non ha
 * già nello store, e `changeLanguage` cambia lingua (ed emette
 * `languageChanged`) solo DOPO il caricamento: chi chiama `changeLanguage`
 * non deve fare nulla di diverso. Il bundle lo aggiunge `loadLocaleBundle`,
 * quindi qui si restituisce `null` (nessun secondo merge da parte di i18next).
 *
 * Su errore si risponde `(err, true)`: per i18next è il segnale «riprova»,
 * e il Connector ripete la lettura fino a `maxRetries` volte con attesa che
 * raddoppia da `retryTimeout` (costanti qui sopra). Finiti i tentativi la
 * lingua resta marcata come «da ricaricare» (stato 0, non -1):
 * `changeLanguage` cambia comunque lingua, l'interfaccia mostra il fallback
 * italiano e una nuova selezione della stessa lingua riprova il caricamento
 * (in Chromium l'import fallito resta tale fino al reload della pagina). Con
 * `(err, null)` la lingua restava marcata come fallita per tutta la sessione.
 */
const lazyLocaleBackend: BackendModule = {
  type: "backend",
  init: () => undefined,
  read: (lng, _ns, callback) => {
    loadLocaleBundle(lng).then(
      () => callback(null, null),
      (err: unknown) => callback(err instanceof Error ? err : String(err), true),
    );
  },
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
  .use(lazyLocaleBackend)
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: bundledResources,
    // Le lingue assenti da `resources` passano dal backend qui sopra.
    partialBundledLanguages: true,
    maxRetries: LOCALE_LOAD_RETRIES,
    retryTimeout: LOCALE_RETRY_DELAY_MS,
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
    // Il bundle statico deve essere nello store PRIMA degli override: se ci
    // arrivassero prima gli override, i18next considererebbe la lingua già
    // caricata e il bundle statico non entrerebbe più. Le due richieste
    // partono insieme; un errore del bundle non ferma gli override.
    const [r] = await Promise.all([
      fetch(`${apiBase}/i18n/translations/${lng}`, { credentials: "include" }),
      loadLocaleBundle(lng).catch(() => undefined),
    ]);
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
