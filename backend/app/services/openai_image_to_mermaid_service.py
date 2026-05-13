"""Service per convertire un'immagine in codice Mermaid via OpenAI Vision.

Chiamata on-demand dall'editor lezione: quando l'utente carica un'immagine
e clicca "Digitalizza in Mermaid", il backend serializza l'immagine in
base64 e la passa a un modello multimodale (gpt-4o di default) chiedendogli
di produrre codice Mermaid che rappresenti lo schema/diagramma raffigurato.

Niente persistenza dei token: l'usage viene tornato nel response JSON
per debug/log futuri ma non scritto su DB (non è parte di una pipeline
batch).
"""
from __future__ import annotations

import base64
import re
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)
from app.services.openai_pricing import build_usage_dict

log = get_logger("app.openai.image_to_mermaid")


_MERMAID_KEYWORDS = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "gantt",
    "pie",
    "journey",
    "mindmap",
    "timeline",
    "gitGraph",
    "quadrantChart",
    "requirementDiagram",
    "C4Context",
)

_UNRECOGNIZED_TOKEN = "UNRECOGNIZED"


class OpenAIImageToMermaidError(OpenAIError):
    """Errore di conversione immagine → Mermaid."""


def _system_prompt(language_code: str) -> str:
    lang_hint = (
        "italiano" if language_code.lower().startswith("it") else "inglese"
    )
    return (
        "Sei un assistente che converte immagini di schemi, diagrammi e "
        "grafici in codice Mermaid valido.\n\n"
        "REGOLE:\n"
        "1. Analizza l'immagine: identifica nodi, relazioni, gerarchie, "
        "frecce, gruppi.\n"
        "2. Scegli il tipo di diagramma Mermaid più adatto "
        "(flowchart/sequenceDiagram/classDiagram/stateDiagram/erDiagram/"
        "mindmap/timeline/ecc.).\n"
        "3. Produci codice Mermaid SINTATTICAMENTE VALIDO.\n"
        "4. Usa label leggibili in " + lang_hint + ".\n"
        "5. Output: SOLO il codice Mermaid grezzo. Niente backtick, "
        "niente prefissi tipo `mermaid`, niente prosa esplicativa.\n"
        "6. Se l'immagine NON contiene uno schema/diagramma riconoscibile "
        "(es. è una fotografia generica, un paesaggio, un volto, un "
        "documento di testo), rispondi con esattamente: "
        + _UNRECOGNIZED_TOKEN
    )


def _extract_mermaid_code(raw: str) -> str:
    """Pulisce l'output del modello: rimuove eventuali fence ```mermaid e
    spazi superflui. Mantiene il contenuto verbatim altrimenti."""
    text = raw.strip()
    # Caso 1: il modello ha incluso una fence ```mermaid ... ```
    fence_match = re.match(
        r"^```(?:mermaid)?\s*\n(.*?)\n```\s*$",
        text,
        flags=re.DOTALL,
    )
    if fence_match:
        return fence_match.group(1).strip()
    # Caso 2: fence singola su una riga (raro)
    fence_match = re.match(r"^```(?:mermaid)?\s*(.*?)\s*```\s*$", text)
    if fence_match:
        return fence_match.group(1).strip()
    return text


def _is_valid_mermaid_keyword(code: str) -> bool:
    """Validazione superficiale: il codice deve iniziare con uno dei
    keyword Mermaid noti. La validazione semantica vera (parser) avviene
    sul frontend con la live preview di MermaidEditor."""
    first_line = code.lstrip().split("\n", 1)[0].strip()
    # Alcuni diagrammi hanno direttive prima del tipo (es. "%%{init: ...}%%")
    # → salta tutte le righe che iniziano con `%%` per trovare la prima
    # riga "vera".
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        first_line = stripped
        break
    return any(first_line.startswith(kw) for kw in _MERMAID_KEYWORDS)


async def convert_image_to_mermaid(
    *,
    image_bytes: bytes,
    mime_type: str,
    language_code: str,
) -> tuple[str, dict[str, Any]]:
    """Converte l'immagine in codice Mermaid.

    Ritorna `(mermaid_code, usage)`. Solleva `OpenAIImageToMermaidError`
    se il modello risponde con `UNRECOGNIZED` o produce output non-Mermaid.
    """
    settings = get_settings()
    model = settings.openai_image_to_mermaid_model
    effort = settings.openai_image_to_mermaid_reasoning_effort

    encoded = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt(language_code)},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Converti questa immagine in codice Mermaid. "
                            "Ricorda: solo codice, niente backtick, niente "
                            "spiegazioni."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "max_completion_tokens": settings.openai_image_to_mermaid_max_tokens,
    }
    apply_reasoning_effort(body, model, effort)

    log.info(
        "openai_image_to_mermaid_request",
        model=model,
        bytes=len(image_bytes),
        mime=mime_type,
    )

    t0 = time.monotonic()
    try:
        async with get_client(timeout=120.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_image_to_mermaid_http_error", error=str(exc))
        raise OpenAIImageToMermaidError(
            status=None, message=f"Errore HTTP verso OpenAI: {exc}"
        ) from exc
    duration_ms = int((time.monotonic() - t0) * 1000)

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
            "openai_image_to_mermaid_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAIImageToMermaidError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_image_to_mermaid_unexpected_response", payload=data)
        raise OpenAIImageToMermaidError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    if not content or not content.strip():
        log.error("openai_image_to_mermaid_empty_content")
        raise OpenAIImageToMermaidError(
            status=resp.status_code,
            message="Il modello non ha prodotto output.",
        )

    raw_text = content.strip()
    if raw_text == _UNRECOGNIZED_TOKEN:
        log.info("openai_image_to_mermaid_unrecognized")
        raise OpenAIImageToMermaidError(
            status=resp.status_code,
            message=(
                "L'immagine non sembra contenere uno schema o diagramma "
                "riconoscibile. Carica un'immagine di uno schema/grafico."
            ),
        )

    mermaid_code = _extract_mermaid_code(raw_text)
    if not mermaid_code or not _is_valid_mermaid_keyword(mermaid_code):
        log.warning(
            "openai_image_to_mermaid_invalid_output",
            preview=mermaid_code[:120] if mermaid_code else "(empty)",
        )
        raise OpenAIImageToMermaidError(
            status=resp.status_code,
            message=(
                "Il codice generato non è Mermaid valido. Riprova oppure "
                "scrivilo a mano."
            ),
        )

    usage = build_usage_dict(
        model=model,
        reasoning_effort_setting=effort,
        openai_usage=data.get("usage") or {},
        duration_ms=duration_ms,
    )
    log.info(
        "openai_image_to_mermaid_success",
        chars=len(mermaid_code),
        duration_ms=duration_ms,
    )
    return mermaid_code, usage
