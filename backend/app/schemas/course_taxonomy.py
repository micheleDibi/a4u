from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

TaxonomyType = Literal[
    "category",
    "teaching_style",
    "content_depth",
    "teacher_role",
    "audience_size",
    "knowledge_level",
    "target_audience",
    "eqf_level",
]

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_lang_map(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    cleaned: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("labels/descriptions devono essere mappe str→str.")
        code = k.strip().lower()
        text = v.strip()
        if not code:
            continue
        cleaned[code] = text
    return cleaned


class TaxonomyTermCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=80)
    parent_id: uuid.UUID | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)
    is_active: bool = True
    labels: dict[str, str] = Field(default_factory=dict)
    descriptions: dict[str, str] | None = None

    @field_validator("slug")
    @classmethod
    def _slug_format(cls, v: str) -> str:
        v = v.strip().lower()
        if not _SLUG_RE.match(v):
            raise ValueError(
                "slug deve iniziare con una lettera e contenere solo "
                "[a-z0-9_]."
            )
        return v

    @field_validator("labels")
    @classmethod
    def _labels_non_empty(cls, v: dict[str, str]) -> dict[str, str]:
        cleaned = _validate_lang_map(v) or {}
        if not cleaned or not any(cleaned.values()):
            raise ValueError("labels deve contenere almeno una lingua valorizzata.")
        return cleaned

    @field_validator("descriptions")
    @classmethod
    def _descriptions_clean(
        cls, v: dict[str, str] | None
    ) -> dict[str, str] | None:
        return _validate_lang_map(v)


class TaxonomyTermUpdate(BaseModel):
    parent_id: uuid.UUID | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)
    is_active: bool | None = None
    labels: dict[str, str] | None = None
    descriptions: dict[str, str] | None = None
    # `unset_parent` distingue "non passare parent_id" (nessuna modifica) da
    # "promuovi a livello 1" (parent_id=null esplicito).
    unset_parent: bool = False

    @field_validator("labels")
    @classmethod
    def _labels_clean(
        cls, v: dict[str, str] | None
    ) -> dict[str, str] | None:
        cleaned = _validate_lang_map(v)
        if cleaned is not None and (not cleaned or not any(cleaned.values())):
            raise ValueError("labels deve contenere almeno una lingua valorizzata.")
        return cleaned

    @field_validator("descriptions")
    @classmethod
    def _descriptions_clean(
        cls, v: dict[str, str] | None
    ) -> dict[str, str] | None:
        return _validate_lang_map(v)


class TaxonomyTermOut(ORMModel):
    id: uuid.UUID
    taxonomy_type: TaxonomyType
    parent_id: uuid.UUID | None = None
    slug: str
    sort_order: int
    is_active: bool
    labels: dict[str, str]
    descriptions: dict[str, str] | None = None
    created_at: datetime
    updated_at: datetime


class TaxonomyTermMove(BaseModel):
    direction: Literal["up", "down"]


class TermAutoTranslateResponse(BaseModel):
    term_id: uuid.UUID
    translated_label_langs: list[str] = Field(default_factory=list)
    translated_description_langs: list[str] = Field(default_factory=list)
    skipped_label_langs: list[str] = Field(default_factory=list)
    skipped_description_langs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class TaxonomyBulkAutoTranslateResponse(BaseModel):
    taxonomy_type: TaxonomyType
    terms_total: int
    languages_processed: list[str] = Field(default_factory=list)
    translated_labels: int
    translated_descriptions: int
    errors: list[str] = Field(default_factory=list)
