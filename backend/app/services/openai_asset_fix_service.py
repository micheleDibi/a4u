"""Auto-fix AI di un singolo asset "fragile" (formula LaTeX o diagramma
Mermaid) generato dall'AI ma non valido.

Usato a generazione (Fase 3 + Fase 4) da `asset_validation_service`: quando
un asset non supera la validazione (latex2mathml + KaTeX per le formule,
mermaid v10.9.4 per i diagrammi), questa funzione chiede al modello di
correggere SOLO la sintassi preservando il significato. Il caller ri-valida
il risultato e, se serve, ritenta.

Output strutturato JSON validato da `AssetFixOut`:
- fixed_content: l'asset corretto (LaTeX senza delimitatori / codice Mermaid grezzo)
- notes: una frase su cosa e' stato corretto (solo per log)

Pattern speculare a `openai_paper_summary_service`: sincrono, JSON schema
strict, niente persistenza.
"""
from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)

log = get_logger("app.openai_asset_fix")

AssetKind = Literal["latex", "mermaid"]


class OpenAIAssetFixError(OpenAIError):
    """Errore specifico dell'auto-fix di un asset."""


class AssetFixOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fixed_content: str = Field(min_length=1)
    notes: str = Field(default="")


_SYSTEM_MERMAID_IT = """\
Sei un esperto di diagrammi Mermaid. Ricevi un diagramma Mermaid che NON e'
valido (non supera il parsing). Correggilo affinche' sia sintatticamente
valido e renderizzabile, PRESERVANDO il significato e i contenuti originali
(stesso tipo di diagramma, stessi nodi, etichette e relazioni).

VINCOLI RIGIDI:
- Compatibilita' con Mermaid v10.9.x. NON usare sintassi "neo look"/v11.
- Restituisci SOLO il codice Mermaid grezzo: NIENTE backtick, NIENTE code
  fence ```, niente testo prima o dopo.
- Mantieni il tipo di diagramma dichiarato (flowchart, sequenceDiagram,
  classDiagram, stateDiagram, erDiagram, ...) se corretto; se la prima riga
  e' errata o assente, scegli il tipo piu' adatto al contenuto.
- Etichette compatibili con `htmlLabels:false`: testo semplice, niente HTML
  ne' markdown dentro le label; se servono caratteri speciali (`(`, `)`, `:`,
  `"`) racchiudi l'etichetta tra virgolette doppie come da sintassi Mermaid.
- NON aggiungere ne' rimuovere contenuti rispetto all'originale: correggi
  solo la sintassi.

Output: SOLO JSON valido conforme allo schema."""

_SYSTEM_MERMAID_EN = """\
You are a Mermaid diagram expert. You receive a Mermaid diagram that is NOT
valid (it fails parsing). Fix it so it is syntactically valid and
renderable, PRESERVING the original meaning and content (same diagram type,
same nodes, labels and relations).

STRICT CONSTRAINTS:
- Compatible with Mermaid v10.9.x. Do NOT use "neo look"/v11 syntax.
- Return ONLY the raw Mermaid code: NO backticks, NO ``` code fences, no
  text before or after.
- Keep the declared diagram type (flowchart, sequenceDiagram, classDiagram,
  stateDiagram, erDiagram, ...) if correct; if the first line is wrong or
  missing, choose the type best suited to the content.
- Labels compatible with `htmlLabels:false`: plain text, no HTML or markdown
  inside labels; if special characters (`(`, `)`, `:`, `"`) are needed,
  wrap the label in double quotes per Mermaid syntax.
- Do NOT add or remove content vs the original: fix syntax only.

Output: ONLY valid JSON conforming to the schema."""

_SYSTEM_LATEX_IT = """\
Sei un esperto di LaTeX matematico. Ricevi una formula LaTeX che NON e'
valida. Correggila affinche' sia valida SIA con KaTeX (strict:"ignore") SIA
con latex2mathml, PRESERVANDO il significato matematico.

VINCOLI RIGIDI:
- Restituisci SOLO il corpo della formula, SENZA delimitatori: niente
  `$...$`, `$$...$$`, `\\(...\\)`, `\\[...\\]`, niente backtick.
- Usa solo comandi supportati sia da KaTeX sia da latex2mathml; evita
  pacchetti/macro esotici o ambienti non supportati.
- Correggi solo la sintassi (parentesi/graffe sbilanciate, comandi errati,
  argomenti mancanti). NON cambiare il significato della formula.
- NON aggiungere testo descrittivo o commenti.

Output: SOLO JSON valido conforme allo schema."""

_SYSTEM_LATEX_EN = """\
You are a mathematical LaTeX expert. You receive a LaTeX formula that is NOT
valid. Fix it so it is valid BOTH with KaTeX (strict:"ignore") AND with
latex2mathml, PRESERVING the mathematical meaning.

STRICT CONSTRAINTS:
- Return ONLY the formula body, WITHOUT delimiters: no `$...$`, `$$...$$`,
  `\\(...\\)`, `\\[...\\]`, no backticks.
- Use only commands supported by both KaTeX and latex2mathml; avoid exotic
  packages/macros or unsupported environments.
- Fix syntax only (unbalanced braces/parens, wrong commands, missing
  arguments). Do NOT change the formula's meaning.
- Do NOT add descriptive text or comments.

Output: ONLY valid JSON conforming to the schema."""


def _is_it(language_code: str) -> bool:
    return (language_code or "it").lower().split("-")[0] == "it"


def _system_prompt(kind: AssetKind, language_code: str) -> str:
    it = _is_it(language_code)
    if kind == "mermaid":
        return _SYSTEM_MERMAID_IT if it else _SYSTEM_MERMAID_EN
    return _SYSTEM_LATEX_IT if it else _SYSTEM_LATEX_EN


ASSET_FIX_JSON_SCHEMA: dict[str, Any] = {
    "name": "asset_fix",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "fixed_content": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["fixed_content", "notes"],
        "additionalProperties": False,
    },
}


async def fix_asset(
    *,
    kind: AssetKind,
    source: str,
    error_message: str,
    context: str = "",
    language_code: str,
) -> tuple[AssetFixOut, dict[str, Any]]:
    """Corregge un singolo asset invalido via AI.

    `kind`: "latex" (formula senza delimitatori) | "mermaid" (codice grezzo).
    `source`: l'asset invalido cosi' com'e'.
    `error_message`: messaggio dal validatore (KaTeX/latex2mathml/mermaid).
    `context`: opzionale — caption/label/explanation per orientare il fix.

    Ritorna `(output, usage)`. Solleva `OpenAIAssetFixError` su errore
    HTTP/parse/schema; `OpenAINotConfiguredError` se manca la API key.
    """
    settings = get_settings()
    lang = "it" if _is_it(language_code) else "en"

    parts: list[str] = [
        f"TIPO ASSET: {kind}",
        f"LINGUA DEL CORSO (per eventuali etichette testuali): {lang}",
    ]
    if context.strip():
        parts.append(f"CONTESTO (caption/label): {context.strip()[:600]}")
    parts.append("")
    parts.append("ERRORE DI VALIDAZIONE:")
    parts.append((error_message or "(nessun dettaglio)").strip()[:800])
    parts.append("")
    parts.append("ASSET DA CORREGGERE:")
    parts.append(source)
    user_message = "\n".join(parts)

    body: dict[str, Any] = {
        "model": settings.openai_asset_fix_model,
        "messages": [
            {"role": "system", "content": _system_prompt(kind, language_code)},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": ASSET_FIX_JSON_SCHEMA,
        },
        "max_tokens": settings.openai_asset_fix_max_tokens,
    }
    apply_reasoning_effort(
        body, settings.openai_asset_fix_model, settings.openai_asset_fix_reasoning_effort
    )

    log.info(
        "openai_asset_fix_request",
        kind=kind,
        model=settings.openai_asset_fix_model,
        source_chars=len(source or ""),
    )
    try:
        async with get_client(timeout=90.0) as client:
            resp = await client.post("/chat/completions", json=body, timeout=90.0)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_asset_fix_http_error", error=str(exc))
        raise OpenAIAssetFixError(
            status=None, message=f"Errore HTTP verso OpenAI: {exc}"
        ) from exc

    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text}
        message = (
            payload.get("error", {}).get("message")
            if isinstance(payload, dict)
            else None
        )
        log.error(
            "openai_asset_fix_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIAssetFixError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_asset_fix_unexpected_response", payload=data)
        raise OpenAIAssetFixError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("openai_asset_fix_json_decode_failed", content=content[:500])
        raise OpenAIAssetFixError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        output = AssetFixOut.model_validate(parsed)
    except Exception as exc:
        log.error("openai_asset_fix_schema_invalid", error=str(exc))
        raise OpenAIAssetFixError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": settings.openai_asset_fix_model,
    }
    log.info("openai_asset_fix_response", kind=kind, tokens=usage["total"])
    return output, usage


__all__ = ["fix_asset", "AssetFixOut", "OpenAIAssetFixError", "AssetKind"]
