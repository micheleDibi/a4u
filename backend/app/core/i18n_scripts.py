"""Mappa codice lingua → script primario e validazione delle traduzioni.

Usato dal servizio di auto-traduzione per:
  - Rifiutare echi del source italiano per lingue con script non-Latino
    (es. zh, ja, ru, ar): se OpenAI ritorna "Lingue" come traduzione cinese,
    il valore non contiene caratteri CJK e va scartato.
  - Identificare valori in DB che sono stati erroneamente salvati come echi
    in passato, per poterli cancellare e rigenerare.

Per script Latini (la maggior parte delle lingue UE) accettiamo qualsiasi
stringa non-vuota: l'echo "Manager" → "Manager" può essere una traduzione
legittima (cèco, polacco, ecc.).
"""
from __future__ import annotations

# Lingue con script primario non-Latino. Le lingue non in questa mappa sono
# trattate come Latine (no echo check).
PRIMARY_SCRIPT: dict[str, str] = {
    # CJK
    "zh": "cjk",
    "ja": "cjk",
    "ko": "hangul",
    # Arabic-script
    "ar": "arabic",
    "fa": "arabic",
    "ur": "arabic",
    "ps": "arabic",
    "sd": "arabic",
    # Hebrew-script
    "he": "hebrew",
    "yi": "hebrew",
    # Cyrillic
    "ru": "cyrillic",
    "uk": "cyrillic",
    "bg": "cyrillic",
    "mk": "cyrillic",
    "sr": "cyrillic",
    "be": "cyrillic",
    "kk": "cyrillic",
    "ky": "cyrillic",
    "mn": "cyrillic",
    # Greek
    "el": "greek",
    # Brahmic family
    "hi": "devanagari",
    "mr": "devanagari",
    "ne": "devanagari",
    "sa": "devanagari",
    "bn": "bengali",
    "ta": "tamil",
    "te": "telugu",
    "kn": "kannada",
    "ml": "malayalam",
    "gu": "gujarati",
    "pa": "gurmukhi",
    "si": "sinhala",
    # SE Asia
    "th": "thai",
    "lo": "lao",
    "km": "khmer",
    "my": "myanmar",
    # Caucasian
    "ka": "georgian",
    "hy": "armenian",
    # Ge'ez (Amharic, Tigrinya)
    "am": "ethiopic",
    "ti": "ethiopic",
}

# Range Unicode per ogni script. Sufficienti per detection di "almeno un char
# nello script atteso"; non puntiamo a coverage 100% delle estensioni.
SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "cjk": [
        (0x3400, 0x4DBF),   # CJK Unified Ext A
        (0x4E00, 0x9FFF),   # CJK Unified
        (0xF900, 0xFAFF),   # CJK Compatibility
        (0x3040, 0x309F),   # Hiragana
        (0x30A0, 0x30FF),   # Katakana
    ],
    "hangul": [
        (0xAC00, 0xD7AF),   # Hangul Syllables
        (0x1100, 0x11FF),   # Hangul Jamo
        (0x3130, 0x318F),   # Hangul Compatibility Jamo
    ],
    "arabic": [
        (0x0600, 0x06FF),
        (0x0750, 0x077F),
        (0x08A0, 0x08FF),
        (0xFB50, 0xFDFF),
        (0xFE70, 0xFEFF),
    ],
    "hebrew": [(0x0590, 0x05FF), (0xFB1D, 0xFB4F)],
    "cyrillic": [
        (0x0400, 0x04FF),
        (0x0500, 0x052F),
        (0x2DE0, 0x2DFF),
        (0xA640, 0xA69F),
    ],
    "greek": [(0x0370, 0x03FF), (0x1F00, 0x1FFF)],
    "devanagari": [(0x0900, 0x097F), (0xA8E0, 0xA8FF)],
    "bengali": [(0x0980, 0x09FF)],
    "tamil": [(0x0B80, 0x0BFF)],
    "telugu": [(0x0C00, 0x0C7F)],
    "kannada": [(0x0C80, 0x0CFF)],
    "malayalam": [(0x0D00, 0x0D7F)],
    "gujarati": [(0x0A80, 0x0AFF)],
    "gurmukhi": [(0x0A00, 0x0A7F)],
    "sinhala": [(0x0D80, 0x0DFF)],
    "thai": [(0x0E00, 0x0E7F)],
    "lao": [(0x0E80, 0x0EFF)],
    "khmer": [(0x1780, 0x17FF)],
    "myanmar": [(0x1000, 0x109F)],
    "georgian": [(0x10A0, 0x10FF), (0x2D00, 0x2D2F)],
    "armenian": [(0x0530, 0x058F), (0xFB13, 0xFB17)],
    "ethiopic": [(0x1200, 0x137F), (0x1380, 0x139F)],
}


def _short_code(lang_code: str) -> str:
    return lang_code.lower().split("-")[0]


def primary_script(lang_code: str) -> str | None:
    """Ritorna lo script primario (es. 'cjk', 'cyrillic') o None se Latino/sconosciuto."""
    if not lang_code:
        return None
    return PRIMARY_SCRIPT.get(_short_code(lang_code))


def has_target_script_chars(text: str, lang_code: str) -> bool:
    """True se `text` contiene almeno un carattere dello script primario di `lang_code`.

    Per lingue Latine (script primario None) ritorna sempre True (nessun vincolo).
    """
    script = primary_script(lang_code)
    if script is None:
        return True
    ranges = SCRIPT_RANGES.get(script)
    if not ranges:
        return True
    for ch in text:
        cp = ord(ch)
        for lo, hi in ranges:
            if lo <= cp <= hi:
                return True
    return False


def is_meaningful_translation(
    source: str, target: str, target_code: str
) -> bool:
    """True se `target` è una traduzione plausibile di `source` per `target_code`.

    Regole:
      - Stringa vuota/whitespace → mai meaningful.
      - Lingua target con script Latino → qualunque non-vuoto è OK
        (echo legittimo possibile per termini tecnici, brand, prestiti).
      - Lingua target con script non-Latino → target deve contenere almeno
        un carattere dello script atteso. Filtra echi del source italiano
        ("Lingue" passato come traduzione cinese viene rifiutato).
    """
    if not isinstance(target, str) or not target.strip():
        return False
    if primary_script(target_code) is None:
        return True
    return has_target_script_chars(target, target_code)
