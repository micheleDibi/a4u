"""Localizzazione AI dei campi testuali degli asset rimasti nella lingua
sbagliata — rete di sicurezza i18n di Fase 3/4.

Quando, dopo la generazione, un campo testuale di un asset (caption, alt_text,
enunciato/spiegazione di equazioni, testo dei passi di dimostrazione, titolo/
contenuto degli esempi, label dei diagrammi Mermaid, celle delle tabelle) non
risulta nella lingua del corso (rilevamento via `app/core/i18n_scripts` nel
chiamante), questo servizio lo traduce nella lingua target PRESERVANDO sintassi
e struttura (LaTeX, Mermaid, markdown, placeholder `[FIG:..]`, ID).

Chiamato da `asset_validation_service` dopo il fix di sintassi. Sincrono,
`response_format=json_object` (chiavi dinamiche), niente persistenza. Pattern
speculare a `openai_asset_fix_service` / `openai_translate_service`.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.i18n_scripts import is_meaningful_translation, primary_script
from app.core.logging import get_logger
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)

log = get_logger("app.openai_asset_localize")


class OpenAIAssetLocalizeError(OpenAIError):
    """Errore specifico della localizzazione asset."""


def _system_prompt(language_code: str) -> str:
    script = primary_script(language_code)
    non_latin_rule = ""
    if script is not None:
        non_latin_rule = (
            f"\nNON-LATIN TARGET: the target language uses the '{script}' script. "
            "EVERY value you return for a field that contains prose MUST contain "
            "characters of that script. NEVER echo the source value unchanged when "
            "it is in another language."
        )
    return (
        "You are a professional translator for academic course material. You "
        "receive a JSON object whose values are text fields extracted from lesson "
        f"assets. Translate each value into the target language (ISO 639 code: "
        f"{language_code}).\n"
        "\n"
        "RULES:\n"
        "1. If a value is ALREADY in the target language, return it unchanged.\n"
        "2. Translate ONLY natural-language text. PRESERVE EXACTLY, untranslated:\n"
        "   - Mathematical LaTeX: anything between $...$ or $$...$$, and raw LaTeX "
        "commands/notation (\\frac, \\alpha, environments, operators, symbols).\n"
        "   - Mermaid diagram SYNTAX: the diagram-type keyword (graph, flowchart, "
        "sequenceDiagram, classDiagram, stateDiagram, erDiagram), directions "
        "(TD, LR, RL, BT), arrows (-->, ---, -.->, ==>, :), node/edge IDs and the "
        "bracket characters. Translate ONLY the human-readable LABEL text inside "
        "the nodes/edges.\n"
        "   - Markdown structure: table pipes `|`, separator rows `---`, heading "
        "markers `#`, list markers. In tables translate the CELL text and headers "
        "but keep the grid intact.\n"
        "   - Placeholders/refs like [FIG:id], [TAB:id], [EQ:id], [EX:id]; asset "
        "IDs; URLs; inline code; numbers; the brand name 'a4u'.\n"
        "3. Keep meaning, tone and formatting. Do NOT add commentary or extra keys.\n"
        f"{non_latin_rule}\n"
        "\n"
        "Output ONLY a valid JSON object with the SAME keys as the input and the "
        "translated string values."
    )


async def localize_texts(
    *,
    items: dict[str, str],
    language_code: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Traduce nella lingua `language_code` i valori di `items` (dict key→testo).

    Ritorna `(localized, usage)`. `localized` contiene SOLO le chiavi con una
    traduzione "meaningful" (per script non-latino: il valore deve contenere
    caratteri dello script atteso, così gli echi del source vengono scartati);
    le chiavi omesse fanno sì che il chiamante mantenga il valore originale.

    Solleva `OpenAIAssetLocalizeError` su errore HTTP/parse;
    `OpenAINotConfiguredError` se manca la API key.
    """
    if not items:
        return {}, {}
    settings = get_settings()
    model = settings.openai_asset_localize_model
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt(language_code)},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": settings.openai_asset_localize_max_tokens,
        # Penalty contro il "degenerate loop" di gpt-4o-mini (token ripetuto
        # fino a troncare il JSON), come in openai_translate_service.
        "frequency_penalty": 0.3,
        "presence_penalty": 0.1,
    }
    log.info(
        "openai_asset_localize_request",
        target=language_code,
        items=len(items),
        model=model,
    )
    try:
        async with get_client(timeout=120.0) as client:
            resp = await client.post("/chat/completions", json=body, timeout=120.0)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_asset_localize_http_error", error=str(exc))
        raise OpenAIAssetLocalizeError(
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
            "openai_asset_localize_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIAssetLocalizeError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_asset_localize_unexpected_response", payload=data)
        raise OpenAIAssetLocalizeError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("openai_asset_localize_json_decode_failed", content=content[:500])
        raise OpenAIAssetLocalizeError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    if not isinstance(parsed, dict):
        raise OpenAIAssetLocalizeError(
            status=resp.status_code,
            message="OpenAI non ha restituito un oggetto JSON.",
        )

    out: dict[str, str] = {}
    for k, v in parsed.items():
        if not isinstance(v, str) or not v.strip():
            continue
        source = items.get(k, "")
        # Per script non-latino, scarta gli echi del source (valore senza
        # caratteri dello script atteso) → il chiamante tiene l'originale.
        if not is_meaningful_translation(source, v, language_code):
            continue
        out[k] = v

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt": int(usage_raw.get("prompt_tokens") or 0),
        "completion": int(usage_raw.get("completion_tokens") or 0),
        "total": int(usage_raw.get("total_tokens") or 0),
        "model": model,
    }
    log.info(
        "openai_asset_localize_response",
        target=language_code,
        translated=len(out),
        requested=len(items),
        tokens=usage["total"],
    )
    return out, usage


__all__ = ["localize_texts", "OpenAIAssetLocalizeError"]
