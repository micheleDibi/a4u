/**
 * Mirror frontend di `backend/app/core/i18n_scripts.py`.
 * Usato dall'editor delle traduzioni per identificare le righe non tradotte
 * (es. echi italiani per script non-Latino) coerentemente col backend.
 */

const PRIMARY_SCRIPT: Record<string, string> = {
  // CJK
  zh: "cjk",
  ja: "cjk",
  ko: "hangul",
  // Arabic
  ar: "arabic",
  fa: "arabic",
  ur: "arabic",
  ps: "arabic",
  sd: "arabic",
  // Hebrew
  he: "hebrew",
  yi: "hebrew",
  // Cyrillic
  ru: "cyrillic",
  uk: "cyrillic",
  bg: "cyrillic",
  mk: "cyrillic",
  sr: "cyrillic",
  be: "cyrillic",
  kk: "cyrillic",
  ky: "cyrillic",
  mn: "cyrillic",
  // Greek
  el: "greek",
  // Brahmic
  hi: "devanagari",
  mr: "devanagari",
  ne: "devanagari",
  sa: "devanagari",
  bn: "bengali",
  ta: "tamil",
  te: "telugu",
  kn: "kannada",
  ml: "malayalam",
  gu: "gujarati",
  pa: "gurmukhi",
  si: "sinhala",
  // SE Asia
  th: "thai",
  lo: "lao",
  km: "khmer",
  my: "myanmar",
  // Caucasian
  ka: "georgian",
  hy: "armenian",
  // Ethiopic
  am: "ethiopic",
  ti: "ethiopic",
};

// Regex con escape Unicode espliciti (evita problemi di encoding del sorgente).
// Allineato a SCRIPT_RANGES backend.
const SCRIPT_REGEX: Record<string, RegExp> = {
  cjk: /[㐀-䶿一-鿿豈-﫿぀-ゟ゠-ヿ]/,
  hangul: /[가-힯ᄀ-ᇿ㄰-㆏]/,
  arabic: /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/,
  hebrew: /[֐-׿יִ-ﭏ]/,
  cyrillic: /[Ѐ-ӿԀ-ԯⷠ-ⷿꙀ-ꚟ]/,
  greek: /[Ͱ-Ͽἀ-῿]/,
  devanagari: /[ऀ-ॿ꣠-ꣿ]/,
  bengali: /[ঀ-৿]/,
  tamil: /[஀-௿]/,
  telugu: /[ఀ-౿]/,
  kannada: /[ಀ-೿]/,
  malayalam: /[ഀ-ൿ]/,
  gujarati: /[઀-૿]/,
  gurmukhi: /[਀-੿]/,
  sinhala: /[඀-෿]/,
  thai: /[฀-๿]/,
  lao: /[຀-໿]/,
  khmer: /[ក-៿]/,
  myanmar: /[က-႟]/,
  georgian: /[Ⴀ-ჿⴀ-⴯]/,
  armenian: /[԰-֏ﬓ-ﬗ]/,
  ethiopic: /[ሀ-፿ᎀ-᎟]/,
};

function shortCode(langCode: string): string {
  return langCode.toLowerCase().split("-")[0];
}

/** Ritorna lo script primario (es. 'cjk', 'cyrillic') o `undefined` se Latino/sconosciuto. */
export function primaryScript(langCode: string): string | undefined {
  if (!langCode) return undefined;
  return PRIMARY_SCRIPT[shortCode(langCode)];
}

/** True se `text` contiene almeno un carattere dello script primario di `langCode`. */
export function hasTargetScriptChars(text: string, langCode: string): boolean {
  const script = primaryScript(langCode);
  if (!script) return true;
  const re = SCRIPT_REGEX[script];
  if (!re) return true;
  return re.test(text);
}

/**
 * True se `value` è una traduzione plausibile per `targetCode`.
 * - Vuoto/whitespace: mai meaningful.
 * - Lingua Latina: qualunque non-vuoto OK (echo legittimo).
 * - Lingua non-Latina: deve contenere caratteri dello script atteso.
 */
export function isMeaningfulTranslation(value: string, targetCode: string): boolean {
  if (typeof value !== "string" || !value.trim()) return false;
  if (!primaryScript(targetCode)) return true;
  return hasTargetScriptChars(value, targetCode);
}
