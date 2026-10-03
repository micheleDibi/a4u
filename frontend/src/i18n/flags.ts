// Import NOMINATIVI, non `import * as`: il pacchetto tiene tutte le 272
// bandiere in un solo modulo (~331 KB) e con l'import a namespace, letto con
// una chiave dinamica, Rollup non poteva scartarne nessuna. Così nel bundle
// entrano solo quelle elencate qui.
import {
  BG,
  CN,
  CZ,
  DE,
  DK,
  EE,
  ES,
  EU,
  FI,
  FR,
  GB,
  GR,
  HR,
  HU,
  IE,
  IL,
  IN,
  IT,
  JP,
  KR,
  LT,
  LV,
  MT,
  NL,
  NO,
  PL,
  PT,
  RO,
  RU,
  SA,
  SE,
  SI,
  SK,
  TR,
  UA,
} from "country-flag-icons/react/3x2";

import type { ComponentType, CSSProperties } from "react";

// Tipo permissivo: country-flag-icons usa il proprio Props (con HTMLSVGElement)
// che non combacia con SVGProps di React. Bastano className/title/style per noi.
export type FlagComp = ComponentType<{
  className?: string;
  title?: string;
  style?: CSSProperties;
}>;

/**
 * Bandiere disponibili: i paesi di `LANG_TO_COUNTRY` qui sotto (che sono anche
 * i `flag_country_code` del seed). Un `flag_country_code` impostato
 * dall'admin fuori da questo elenco ricade sulla bandiera della lingua e poi
 * su quella UE: per mostrarlo va aggiunto qui (import e mappa).
 */
const FLAGS = {
  BG, CN, CZ, DE, DK, EE, ES, FI, FR, GB, GR, HR, HU, IE, IL, IN, IT, JP,
  KR, LT, LV, MT, NL, NO, PL, PT, RO, RU, SA, SE, SI, SK, TR, UA,
} as unknown as Record<string, FlagComp>;
const FALLBACK = EU as unknown as FlagComp;

/**
 * Mappa di fallback codice lingua (ISO 639-1) → codice paese ISO 3166-1.
 * Usata SOLO se la lingua non ha un `flag_country_code` esplicito.
 * Copre i 24 idiomi UE + le lingue extra-UE supportate da XTTS-v2.
 */
const LANG_TO_COUNTRY: Record<string, string> = {
  // 24 idiomi UE storici della piattaforma.
  bg: "BG", cs: "CZ", da: "DK", de: "DE", el: "GR", en: "GB",
  es: "ES", et: "EE", fi: "FI", fr: "FR", ga: "IE", hr: "HR",
  hu: "HU", it: "IT", lt: "LT", lv: "LV", mt: "MT", nl: "NL",
  pl: "PL", pt: "PT", ro: "RO", sk: "SK", sl: "SI", sv: "SE",
  // Lingue extra-UE: le 6 di XTTS-v2 non-UE (tr, ru, ar, zh, ja, ko)
  // piu' altre comuni. `ar` → Arabia Saudita (l'arabo non ha un singolo
  // paese; `SA` e' la scelta convenzionale). `zh` copre anche `zh-cn`.
  // `ua` e' un alias non-standard per l'ucraino (`uk`) usato da alcuni
  // record creati via UI admin.
  ar: "SA", he: "IL", hi: "IN", ja: "JP", ko: "KR", nb: "NO",
  no: "NO", ru: "RU", tr: "TR", ua: "UA", uk: "UA", zh: "CN",
};

/**
 * Mappa codice lingua + (opzionale) codice paese → componente bandiera.
 *
 * Priorità di risoluzione:
 *   1. `countryCode` esplicito, se è fra le bandiere di `FLAGS`.
 *   2. Mappa fallback `LANG_TO_COUNTRY` per i 24 idiomi UE.
 *   3. Bandiera UE per codici sconosciuti.
 */
export function flagFor(lang: string, countryCode?: string | null): FlagComp {
  if (countryCode) {
    const flag = FLAGS[countryCode.toUpperCase()];
    if (flag) return flag;
  }
  const langKey = lang.toLowerCase().split("-")[0];
  const fromMap = LANG_TO_COUNTRY[langKey];
  if (fromMap) {
    const flag = FLAGS[fromMap];
    if (flag) return flag;
  }
  return FALLBACK;
}
