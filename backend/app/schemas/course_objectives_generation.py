"""Schema Pydantic per l'output della generazione AI di obiettivi e
argomenti chiave a partire da un documento caricato dall'utente.

Vincoli specchio di `CourseUpdateInput`:
- `objectives` max 8000 char
- `argomenti_chiave` max 30 elementi, ognuno max 80 char

L'endpoint `/courses/{id}/objectives/generate-from-file` ritorna questo
schema senza persistere su DB: l'utente conferma esplicitamente in un
dialog FE prima di applicare i valori.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CourseObjectivesGenerationOutput(BaseModel):
    """Output della generazione AI: obiettivi corso + argomenti chiave."""

    objectives: str = Field(
        ...,
        min_length=10,
        max_length=8000,
        description=(
            "Testo discorsivo che esprime cosa lo studente sapra' fare al "
            "termine del corso. 200-1200 caratteri tipici, linguaggio "
            "pedagogico (verbi performativi)."
        ),
    )
    argomenti_chiave: list[str] = Field(
        ...,
        min_length=1,
        max_length=30,
        description=(
            "Lista di 5-15 argomenti chiave del corso, ognuno 2-5 parole. "
            "NO frasi lunghe, NO duplicati."
        ),
    )

    @field_validator("argomenti_chiave")
    @classmethod
    def _clean_argomenti(cls, v: list[str]) -> list[str]:
        # Trim + dedup case-insensitive + drop vuoti + cap a 80 char ciascuno.
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            if not isinstance(raw, str):
                continue
            s = raw.strip()
            if not s:
                continue
            if len(s) > 80:
                s = s[:80].rstrip()
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        if not out:
            raise ValueError("argomenti_chiave: lista vuota dopo cleanup")
        return out
