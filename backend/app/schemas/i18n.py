from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class LanguageOut(ORMModel):
    code: str
    name_native: str
    is_active: bool
    is_default: bool
    rtl: bool
    flag_country_code: str | None = None
    created_at: datetime
    updated_at: datetime
    untranslated_count: int = 0


class PublicLanguageOut(ORMModel):
    """Versione pubblica (per i18n switcher), senza timestamp né flag is_active."""
    code: str
    name_native: str
    rtl: bool
    flag_country_code: str | None = None
    is_default: bool


class LanguageCreate(BaseModel):
    code: str = Field(min_length=2, max_length=10, pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")
    name_native: str = Field(min_length=1, max_length=120)
    flag_country_code: str | None = Field(default=None, max_length=2)
    rtl: bool = False
    is_active: bool = True
    copy_translations_from: str | None = Field(default=None, min_length=2, max_length=10)


class LanguageUpdate(BaseModel):
    name_native: str | None = Field(default=None, min_length=1, max_length=120)
    flag_country_code: str | None = Field(default=None, max_length=2)
    rtl: bool | None = None
    is_active: bool | None = None
    is_default: bool | None = None


class TranslationsBulkUpdate(BaseModel):
    translations: dict[str, str] = Field(default_factory=dict)


class TranslationsResponse(BaseModel):
    language: LanguageOut
    translations: dict[str, str]


class PublicTranslationsResponse(BaseModel):
    code: str
    translations: dict[str, str]


class AutoTranslateResponse(BaseModel):
    """Risultato della traduzione automatica via OpenAI per una singola lingua."""
    code: str
    requested: int = 0
    translated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
