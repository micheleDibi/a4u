"""Schemi Pydantic per Nova — assistente AI contestuale floating widget.

Endpoint stateless: nessuna persistenza DB delle conversazioni. La memoria
della chat vive solo in memoria locale del widget FE. Il payload
`NovaChatRequest` include una `history` opzionale (cap ~10 messaggi) per
mantenere il filo del discorso durante la sessione aperta.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NovaPageContext(BaseModel):
    """Contesto della schermata corrente, annunciato dalle pagine FE
    via `useSetNovaContext({...})`. I `fields` possono contenere
    qualsiasi oggetto serializzabile JSON (es. filtri attivi, titolo
    bozza, status corso). Il BE serializza e tronca a 500 char prima
    di concatenare al system prompt — niente loop di context bloat.
    """

    model_config = ConfigDict(extra="forbid")
    page: str = Field(default="", max_length=80)
    fields: dict[str, Any] = Field(default_factory=dict)
    org_id: uuid.UUID | None = None


class NovaMessage(BaseModel):
    """Singolo messaggio della conversazione in-session. Il `role` è
    limitato a `user`/`assistant` (no `system` — quello lo costruisce
    sempre il BE da `nova_system_prompt`)."""

    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class NovaChatRequest(BaseModel):
    """Body di `POST /nova/chat`."""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    context: NovaPageContext = Field(default_factory=NovaPageContext)
    # Cap soft di 20 messaggi (10 turn) — il BE prende comunque
    # gli ultimi N secondo `settings.nova_history_cap`.
    history: list[NovaMessage] = Field(default_factory=list, max_length=20)
    # Codice ISO della lingua di risposta. Default `it`.
    language_code: str = Field(default="it", min_length=2, max_length=10)


class NovaChatResponse(BaseModel):
    """Response di `POST /nova/chat`."""

    message: str


class NovaWelcomeRequest(BaseModel):
    """Body di `POST /nova/welcome` — invocato dal widget al primo open
    per ottenere un saluto contestuale alla pagina corrente.
    """

    model_config = ConfigDict(extra="forbid")
    context: NovaPageContext = Field(default_factory=NovaPageContext)
    language_code: str = Field(default="it", min_length=2, max_length=10)


class NovaWelcomeResponse(BaseModel):
    """Response di `POST /nova/welcome`."""

    message: str
