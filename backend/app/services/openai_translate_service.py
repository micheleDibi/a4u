"""Client OpenAI per traduzione automatica delle stringhe i18n mancanti.

Usa la Chat Completions API con `response_format=json_object` per ottenere
risposte JSON garantite. Le traduzioni vengono fatte in batch per
contenere il numero di chiamate e i token consumati.

Errori → OpenAITranslateError (sottoclasse di OpenAIError). Tutto async.
"""
from __future__ import annotations

import json

import httpx

from app.core.config import get_settings
from app.core.i18n_scripts import is_meaningful_translation, primary_script
from app.core.logging import get_logger
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)

log = get_logger("app.openai_translate")


# Alias storico per call-site esistenti (i18n_service, course_taxonomy_service).
class OpenAITranslateError(OpenAIError):
    """Errore specifico delle chiamate di traduzione."""


def _system_prompt(source_lang: str, target_lang_code: str, target_lang_name: str) -> str:
    script = primary_script(target_lang_code)
    non_latin_rule = ""
    if script is not None:
        non_latin_rule = (
            f"10. CRITICAL — non-Latin script target: {target_lang_name} uses the "
            f"'{script}' script. EVERY translated value MUST contain characters "
            f"of that script. NEVER return the {source_lang} source value "
            "unchanged for this language. Even when a term seems untranslatable "
            "(technical term, common UI noun like 'Languages', 'Settings', "
            "'Dashboard'), you MUST render it in the target script — either "
            "with a faithful translation or a phonetic transliteration. The "
            "ONLY exception is the brand name 'a4u' (lowercase, unchanged) "
            "and pure ASCII tokens that are universally untranslated (PDF, "
            "JSON, URL, MP4, ISO codes, file extensions, percentage symbols).\n"
        )
    return (
        f"You are a professional UI translator for an academic SaaS product called "
        f'"a4u" (university course generation platform). '
        f"Translate the JSON values from {source_lang} to {target_lang_name} "
        f"(language code: {target_lang_code}).\n"
        "\n"
        "CRITICAL RULES:\n"
        "1. Preserve i18next placeholders exactly: {{name}}, {{count}}, {{lang}}, "
        "{{role}}, {{org}}, {{lessons}}, {{minutes}}, {{hours}}, {{ready}}, "
        "{{total}}, {{failed}}, {{hours}}, etc. They MUST appear identical in "
        "the translation.\n"
        "2. Keep the JSON structure: same keys, only translate the string values. "
        "Do not add, remove, or rename keys.\n"
        "3. Use natural, idiomatic, native phrasing for a professional UI. "
        "Match the tone: concise, polite, professional. Avoid literal translations.\n"
        "4. Keep technical terms intact: PDF, JSON, API, MiniMax, Avatar, "
        "MP4, IVA, CFU (use local equivalent if standard, e.g. 'credit' for EN), "
        "URL, ISO codes, file extensions.\n"
        "5. Preserve punctuation: !, ?, :, ;, …, dashes, parentheses.\n"
        "6. Keep numeric/symbol prefixes intact (≥, ±, %, etc.).\n"
        "7. Do NOT add emojis. Do NOT add commentary outside the JSON.\n"
        "8. For pluralization keys (e.g. ending in _one, _other), translate the "
        "value preserving the singular/plural form correctly for the target language.\n"
        "9. Keep brand name 'a4u' lowercase and unchanged.\n"
        f"{non_latin_rule}"
        "\n"
        "Output ONLY a valid JSON object with the same keys as input and "
        "translated string values."
    )


async def translate_batch(
    *,
    items: dict[str, str],
    source_lang_code: str,
    source_lang_name: str,
    target_lang_code: str,
    target_lang_name: str,
) -> dict[str, str]:
    """Traduce un batch di stringhe via OpenAI Chat Completions.

    `items`: dict {key: source_text}. Ritorna dict {key: translated_text}.
    Le chiavi che la API non riesce a tradurre (o restituisce vuote) vengono
    omesse dall'output: il chiamante decide se considerarle skippate.
    """
    if not items:
        return {}
    settings = get_settings()
    body = {
        "model": settings.openai_model,
        "messages": [
            {
                "role": "system",
                "content": _system_prompt(
                    source_lang_name, target_lang_code, target_lang_name
                ),
            },
            {
                "role": "user",
                "content": json.dumps(items, ensure_ascii=False),
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    log.info(
        "openai_translate_request",
        target=target_lang_code,
        items=len(items),
        model=settings.openai_model,
    )
    try:
        async with get_client() as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_http_error", error=str(exc))
        raise OpenAITranslateError(
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
            "openai_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAITranslateError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_unexpected_response", payload=data)
        raise OpenAITranslateError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("openai_json_decode_failed", content=content[:500])
        raise OpenAITranslateError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    if not isinstance(parsed, dict):
        raise OpenAITranslateError(
            status=resp.status_code,
            message="OpenAI non ha restituito un oggetto JSON.",
        )

    out: dict[str, str] = {}
    echoed: list[str] = []
    for k, v in parsed.items():
        if not isinstance(v, str) or not v.strip():
            continue
        source_value = items.get(k, "")
        if not is_meaningful_translation(source_value, v, target_lang_code):
            # Echo del source per lingua non-Latina: rifiutato per consentire
            # al retry pass (in i18n_service) di ritentare con batch più piccolo.
            echoed.append(k)
            continue
        out[k] = v
    log.info(
        "openai_translate_response",
        target=target_lang_code,
        translated=len(out),
        requested=len(items),
        echoed=len(echoed),
    )
    if echoed:
        log.debug(
            "openai_translate_echoes_dropped",
            target=target_lang_code,
            keys=echoed[:10],
            total=len(echoed),
        )
    return out
