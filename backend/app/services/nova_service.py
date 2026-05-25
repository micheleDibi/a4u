"""Servizio Nova — assistente AI conversazionale contestuale.

Espone due funzioni async stateless:
- `nova_chat`: risposta a un messaggio utente, con history opzionale +
  contesto pagina/fields. Sanifica input, costruisce system prompt
  dinamico, chiama OpenAI Chat Completions, audita aggregato (NO content).
- `nova_welcome`: messaggio di benvenuto page-aware al primo open.

Niente persistenza DB delle conversazioni — la memoria della chat vive
solo in memoria locale del widget FE. L'audit log salva solo metadata
(tokens, page, cost) per visibility/auditing, mai il content.

Pattern OpenAI mirror di `openai_translate_service` (httpx async client,
gestione errori uniforme via `OpenAIError`).
"""
from __future__ import annotations

import json
import uuid

import httpx

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.prompt_safety import contains_injection_attempt, sanitize_user_input
from app.schemas.nova import NovaMessage, NovaPageContext
from app.services.nova_system_prompt import (
    build_system_prompt,
    build_welcome_prompt,
    get_language_name,
)
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    get_client,
)
from app.services.openai_pricing import estimate_cost_usd

log = get_logger("app.nova.service")


# Risposta standard quando l'utente prova prompt injection: stesso testo
# in tutte le lingue UI principali. Per lingue non in mappa fallback IT.
_MANIPULATION_RESPONSES: dict[str, str] = {
    "it": "Posso aiutarti solo con le funzionalità della piattaforma a4u! Chiedimi pure come usare una feature.",
    "en": "I can only help you with a4u platform features! Feel free to ask me how to use one.",
    "es": "¡Solo puedo ayudarte con las funcionalidades de la plataforma a4u! Pregúntame cómo usar una.",
    "fr": "Je peux uniquement vous aider avec les fonctionnalités de la plateforme a4u ! N'hésitez pas à me demander comment utiliser une fonction.",
    "de": "Ich kann dir nur mit den Funktionen der a4u-Plattform helfen! Frag mich gerne, wie du eine Funktion nutzt.",
    "pt": "Só posso ajudar com as funcionalidades da plataforma a4u! Pergunte-me como usar uma funcionalidade.",
}


def _manipulation_response(language_code: str) -> str:
    return _MANIPULATION_RESPONSES.get(
        language_code.lower(), _MANIPULATION_RESPONSES["it"]
    )


async def _openai_chat(
    *,
    system_prompt: str,
    user_messages: list[dict[str, str]],
    timeout: float = 30.0,
) -> tuple[str, dict[str, int]]:
    """Chiama OpenAI Chat Completions e ritorna (content, usage).

    `usage` è il dict `{prompt_tokens, completion_tokens, total_tokens}`
    estratto dalla response. Solleva `OpenAIError` su errori HTTP o
    payload malformati.
    """
    settings = get_settings()
    body = {
        "model": settings.openai_nova_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            *user_messages,
        ],
        "temperature": settings.openai_nova_temperature,
        "max_tokens": settings.openai_nova_max_tokens,
    }
    try:
        async with get_client(timeout=timeout) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("nova_openai_http_error", error=str(exc))
        raise OpenAIError(status=None, message=f"Errore HTTP: {exc}") from exc

    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text}
        msg = (
            payload.get("error", {}).get("message")
            if isinstance(payload, dict)
            else None
        ) or f"OpenAI HTTP {resp.status_code}"
        log.error("nova_openai_api_error", status=resp.status_code, message=msg)
        raise OpenAIError(status=resp.status_code, message=msg, payload=payload)

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("nova_openai_unexpected_response", payload=data)
        raise OpenAIError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    usage = data.get("usage") or {}
    return (content or "").strip(), {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }


async def nova_chat(
    db,  # AsyncSession
    *,
    user_message: str,
    context: NovaPageContext,
    history: list[NovaMessage],
    language_code: str,
    actor_user_id: uuid.UUID,
) -> str:
    """Risponde a un messaggio dell'utente nella chat Nova.

    1. Sanifica input (anti prompt-injection).
    2. Se l'input è "sospetto" → risposta standard, no OpenAI call.
    3. Costruisce system prompt dinamico (lingua + page + fields).
    4. Costruisce payload con history (cap settings.nova_history_cap).
    5. Chiama OpenAI Chat Completions.
    6. Audit log aggregato (tokens + page + cost, NO content).
    """
    settings = get_settings()
    safe_message = sanitize_user_input(user_message)

    # Anti-injection: se l'input contiene pattern noti → risposta standard.
    if not safe_message or contains_injection_attempt(user_message):
        log.warning(
            "nova_injection_attempt",
            user_id=str(actor_user_id),
            page=context.page,
            len=len(user_message or ""),
        )
        return _manipulation_response(language_code)

    system_prompt = build_system_prompt(
        language_code=language_code,
        page=context.page,
        fields=context.fields,
    )

    # History: cap a nova_history_cap (escluso il messaggio corrente).
    cap = max(0, int(settings.nova_history_cap))
    capped_history = history[-cap:] if cap > 0 else []
    user_messages: list[dict[str, str]] = [
        {"role": m.role, "content": m.content} for m in capped_history
    ]
    user_messages.append({"role": "user", "content": safe_message})

    try:
        content, usage = await _openai_chat(
            system_prompt=system_prompt,
            user_messages=user_messages,
        )
    except OpenAINotConfiguredError:
        log.warning("nova_openai_not_configured")
        return _manipulation_response(language_code)
    except OpenAIError as exc:
        log.error("nova_openai_error", error=str(exc))
        return _manipulation_response(language_code)

    # Audit log aggregato: tokens + page + cost. NO content.
    cost_usd = estimate_cost_usd(
        model=settings.openai_nova_model,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
    )
    try:
        await write_audit(
            db,
            action="nova.chat",
            actor_user_id=actor_user_id,
            organization_id=context.org_id,
            target_type="nova",
            target_id=None,
            metadata={
                "page": context.page[:80],
                "language_code": language_code[:10],
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
                "cost_usd": cost_usd,
                "model": settings.openai_nova_model,
                "history_msgs": len(capped_history),
            },
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — audit failure non blocca la risposta
        log.warning("nova_audit_failed", error=str(exc))

    return content or _manipulation_response(language_code)


async def nova_welcome(
    db,  # AsyncSession
    *,
    context: NovaPageContext,
    language_code: str,
    actor_user_id: uuid.UUID,
) -> str:
    """Genera un messaggio di benvenuto contestuale al primo open del
    widget. Solo un turno (no history). System prompt minimal con
    knowledge piattaforma + page corrente.
    """
    system_prompt = build_welcome_prompt(
        language_code=language_code, page=context.page
    )
    # User message vuoto: il system prompt contiene già tutto.
    user_messages = [
        {
            "role": "user",
            "content": f"[Genera saluto per pagina {context.page!r}]",
        },
    ]

    try:
        content, usage = await _openai_chat(
            system_prompt=system_prompt,
            user_messages=user_messages,
            timeout=15.0,
        )
    except OpenAINotConfiguredError:
        return _default_welcome(language_code, context.page)
    except OpenAIError as exc:
        log.warning("nova_welcome_openai_error", error=str(exc))
        return _default_welcome(language_code, context.page)

    settings = get_settings()
    cost_usd = estimate_cost_usd(
        model=settings.openai_nova_model,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
    )
    try:
        await write_audit(
            db,
            action="nova.welcome",
            actor_user_id=actor_user_id,
            organization_id=context.org_id,
            target_type="nova",
            target_id=None,
            metadata={
                "page": context.page[:80],
                "language_code": language_code[:10],
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
                "cost_usd": cost_usd,
                "model": settings.openai_nova_model,
            },
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("nova_audit_failed", error=str(exc))

    return content or _default_welcome(language_code, context.page)


def _default_welcome(language_code: str, page: str) -> str:
    """Fallback se OpenAI non configurato o errore: saluto generico
    nella lingua dell'utente.
    """
    lang = language_code.lower()
    if lang.startswith("en"):
        return "Hi! I'm Nova, your a4u assistant. What can I help you with?"
    if lang.startswith("es"):
        return "¡Hola! Soy Nova, tu asistente de a4u. ¿En qué puedo ayudarte?"
    if lang.startswith("fr"):
        return "Salut ! Je suis Nova, ton assistant a4u. Que puis-je faire pour toi ?"
    if lang.startswith("de"):
        return "Hallo! Ich bin Nova, dein a4u-Assistent. Wie kann ich dir helfen?"
    if lang.startswith("pt"):
        return "Olá! Sou Nova, sua assistente a4u. Como posso ajudar?"
    return "Ciao! Sono Nova, il tuo assistente a4u. Come posso aiutarti?"
