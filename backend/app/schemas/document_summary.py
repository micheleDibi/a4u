"""Schema Pydantic per il riassunto strutturato di un documento corso.

Mirror dello schema JSON dell'Appendice A: ogni campo è validato prima di
essere serializzato in JSONB nel campo `course_document.summary`.

Validazione "lasca": rispettiamo la struttura del JSON Schema
dell'Appendice A ma accettiamo anche array vuoti (il modello LLM a volte
non identifica formule/definizioni, e va bene). I vincoli minOccurs/maxOccurs
del prompt sono indicativi, non bloccanti.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KeyConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    explanation: str


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str
    definition: str


class ExampleOrCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    synthesis: str


class FormulaOrRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    latex_or_text: str
    meaning: str


class AuthorOrReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["author", "cited_reference"]
    value: str


class DocumentSummaryOut(BaseModel):
    """Output validato dell'Appendice A."""

    model_config = ConfigDict(extra="forbid")

    source_title: str
    detected_language: str
    abstract: str
    structure_outline: list[str] = Field(default_factory=list)
    key_concepts: list[KeyConcept] = Field(default_factory=list)
    definitions: list[Definition] = Field(default_factory=list)
    examples_or_cases: list[ExampleOrCase] = Field(default_factory=list)
    formulas_or_rules: list[FormulaOrRule] = Field(default_factory=list)
    authors_and_references: list[AuthorOrReference] = Field(default_factory=list)
    didactic_relevance_tags: list[str] = Field(default_factory=list)
