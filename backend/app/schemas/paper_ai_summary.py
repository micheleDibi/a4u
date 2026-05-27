"""Schema Pydantic dell'output del riassunto AI di un paper (POST
`/papers/ai-summary`).

4 campi richiesti:
- `short_summary`: riassunto breve (200-400 char)
- `technical_summary`: riassunto tecnico (600-1200 char)
- `keywords`: 5-10 parole chiave
- `study_limitations`: limiti dello studio (200-500 char)

Tutti i campi sono generati nella lingua del corso (vedi
`openai_paper_summary_service`). NON viene persistito in DB:
l'output e' usato solo per il dialog FE.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PaperAISummaryOut(BaseModel):
    short_summary: str = Field(min_length=20, max_length=2000)
    technical_summary: str = Field(min_length=50, max_length=4000)
    keywords: list[str] = Field(min_length=1, max_length=20)
    study_limitations: str = Field(min_length=20, max_length=2000)

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, v: list[str]) -> list[str]:
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
            raise ValueError("keywords: lista vuota dopo cleanup")
        return out
