"""Regressione del task G3 (Livello 1 prestazioni): bandiere importate per nome.

`frontend/src/i18n/flags.ts` non importa più tutte le 272 bandiere ma solo un
elenco esplicito: un paese fuori elenco ricade in silenzio sulla bandiera della
lingua o su quella UE. Questi test legano l'elenco alle due fonti dei codici
paese: la mappa `LANG_TO_COUNTRY` dello stesso file e il seed delle lingue del
backend (`LANGUAGE_META`), così una lingua nuova senza bandiera fa fallire la CI
invece di mostrare la bandiera sbagliata.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.db.seed import LANGUAGE_META

FLAGS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "flags.ts"


def _section(pattern: str) -> str:
    match = re.search(pattern, FLAGS_TS.read_text(encoding="utf-8"), re.S)
    assert match, pattern
    return match.group(1)


def _flags() -> set[str]:
    """Chiavi dell'oggetto `FLAGS` (le bandiere risolvibili per codice paese)."""
    return set(re.findall(r"\b([A-Z]{2})\b", _section(r"const FLAGS = \{(.*?)\}")))


def test_flags_map_matches_named_imports() -> None:
    imported = set(re.findall(r"\b([A-Z]{2})\b", _section(r"import \{(.*?)\} from")))
    assert imported - {"EU"} == _flags()


def test_every_language_fallback_has_its_flag() -> None:
    mapped = set(re.findall(r'"([A-Z]{2})"', _section(r"LANG_TO_COUNTRY[^{]*\{(.*?)\};")))
    assert mapped and mapped <= _flags(), sorted(mapped - _flags())


def test_every_seeded_language_country_has_its_flag() -> None:
    seeded = {str(m["country"]).upper() for m in LANGUAGE_META.values() if m.get("country")}
    assert seeded and seeded <= _flags(), sorted(seeded - _flags())
