"""Pricing OpenAI + helper di tracking generazione AI.

Per ogni chiamata ai modelli OpenAI nei worker pipeline corso (architettura,
struttura lezioni, contenuti, slide, discorso) salviamo nel JSONB `*_tokens`
un set di metadati uniforme:

- ``model``: nome del modello chiamato
- ``reasoning_effort``: livello di reasoning richiesto (None per modelli
  non-reasoning come gpt-4o*)
- ``prompt`` / ``completion`` / ``total``: token tradizionali
- ``reasoning_tokens``: sub-set di ``completion`` consumato dal "thinking"
  interno (gpt-5*, o1*, o3*, o4*); 0 sui modelli classici
- ``cached_tokens``: sub-set di ``prompt`` servito dal prompt cache OpenAI
  (riduce costo); 0 se nessun hit
- ``duration_ms``: durata della chiamata HTTP catturata dal worker
- ``cost_usd``: stima del costo basata su :data:`MODEL_PRICING`; ``None``
  se il modello non è in tabella (forward-compat)

Manutenzione: aggiornare :data:`MODEL_PRICING` quando OpenAI rilascia nuovi
modelli o ritocca il listino. Centralizzato qui per limitare la superficie
di modifica.
"""
from __future__ import annotations

from typing import Any


# === Lista modelli reasoning =================================================
# I prefissi devono coincidere con quelli di :mod:`openai_client._REASONING_MODEL_PREFIXES`
# per coerenza (la stessa famiglia accetta `reasoning_effort` nel body).
_REASONING_MODEL_PREFIXES: tuple[str, ...] = ("o1", "o3", "o4", "gpt-5")


def supports_reasoning(model: str) -> bool:
    """True se il modello accetta `reasoning_effort` (gpt-5*, o1*, o3*, o4*)."""
    name = (model or "").strip().lower()
    return name.startswith(_REASONING_MODEL_PREFIXES)


# === Pricing tabella =========================================================
# Tutti i prezzi sono in USD per 1 milione di token.
#
# `input`: prezzo prompt token "freschi" (non serviti dal cache).
# `output`: prezzo completion token (sui reasoning model copre anche i
#           reasoning_tokens, che OpenAI fattura come output normale).
# `cached_input`: prezzo prompt token serviti dal cache OpenAI (riduzione
#                 sostanziale, tipicamente ~50%).
#
# Fonti: https://openai.com/api/pricing/ (gennaio 2026).
# TODO: aggiornare quando OpenAI cambia il listino.
MODEL_PRICING: dict[str, dict[str, float]] = {
    # === Modelli classici (non reasoning) ===
    "gpt-4o": {"input": 2.50, "output": 10.00, "cached_input": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached_input": 0.075},
    # === Reasoning serie o (thinking) ===
    "o1": {"input": 15.00, "output": 60.00, "cached_input": 7.50},
    "o1-mini": {"input": 3.00, "output": 12.00, "cached_input": 1.50},
    "o3": {"input": 10.00, "output": 40.00, "cached_input": 5.00},
    "o3-mini": {"input": 1.10, "output": 4.40, "cached_input": 0.55},
    "o4-mini": {"input": 1.10, "output": 4.40, "cached_input": 0.55},
    # === Serie GPT-5 (reasoning integrato) ===
    # TODO: i prezzi gpt-5* sono stime conservative basate sul tier o-series
    # standard. Aggiornare con il listino ufficiale quando disponibile.
    "gpt-5": {"input": 5.00, "output": 20.00, "cached_input": 1.25},
    "gpt-5-mini": {"input": 1.00, "output": 4.00, "cached_input": 0.25},
    "gpt-5.5": {"input": 5.00, "output": 20.00, "cached_input": 1.25},
}


def estimate_cost_usd(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float | None:
    """Stima il costo in USD per una singola chiamata OpenAI.

    Formula::

        cost = (prompt - cached) * input_per_token
             + cached * cached_per_token
             + completion * output_per_token

    I `reasoning_tokens` sono già contati dentro `completion_tokens` e
    OpenAI li fattura al prezzo "output" normale (gpt-5*, o-series).
    Non li passiamo come argomento separato per evitare doppio conteggio.

    Args:
        model: nome del modello come riportato in
            :data:`MODEL_PRICING`. Match case-insensitive.
        prompt_tokens: ``usage.prompt_tokens`` da OpenAI.
        completion_tokens: ``usage.completion_tokens`` da OpenAI (include
            i reasoning_tokens sui modelli reasoning).
        cached_tokens: ``usage.prompt_tokens_details.cached_tokens``.
            Default 0 se la response non lo riporta.

    Returns:
        Costo stimato in USD, oppure ``None`` se il modello non è in
        :data:`MODEL_PRICING` (segnala "best-effort: costo non stimato").
    """
    key = (model or "").strip().lower()
    pricing = MODEL_PRICING.get(key)
    if pricing is None:
        return None

    # Sanitize: cached non può superare prompt (sarebbe un bug della response).
    cached = max(0, min(cached_tokens, prompt_tokens))
    fresh_prompt = prompt_tokens - cached

    cost = (
        fresh_prompt * pricing["input"]
        + cached * pricing["cached_input"]
        + completion_tokens * pricing["output"]
    ) / 1_000_000.0
    # Floor a 0 per robustezza (in teoria già garantito dalla formula).
    return max(0.0, cost)


def build_usage_dict(
    *,
    model: str,
    reasoning_effort_setting: str | None,
    openai_usage: dict[str, Any],
    duration_ms: int,
) -> dict[str, Any]:
    """Costruisce il dict da salvare nel JSONB `*_tokens`.

    Wrapper attorno alla logica di parsing della :attr:`response.usage` di
    OpenAI + calcolo costo + risoluzione del `reasoning_effort` effettivo.

    Args:
        model: nome del modello come da settings (es. ``"gpt-5.5"``).
        reasoning_effort_setting: valore della relativa setting
            (``openai_*_reasoning_effort``). Verrà salvato solo se il
            modello supporta reasoning; altrimenti ``None``.
        openai_usage: dict ``response["usage"]`` così come restituito da
            OpenAI. Può essere vuoto se la response non l'ha incluso.
        duration_ms: tempo della chiamata HTTP, già calcolato dal chiamante.

    Returns:
        Dict idoneo a essere assegnato a ``course.architecture_tokens``,
        ``course_module.lessons_structure_tokens``,
        ``course_lesson.content_tokens`` / ``slides_tokens`` /
        ``speech_tokens``. Chiavi sempre presenti (anche se a 0/None) per
        rendere prevedibile la lettura downstream.
    """
    prompt_tokens = int(openai_usage.get("prompt_tokens") or 0)
    completion_tokens = int(openai_usage.get("completion_tokens") or 0)
    total_tokens = int(openai_usage.get("total_tokens") or 0)

    completion_details = openai_usage.get("completion_tokens_details") or {}
    prompt_details = openai_usage.get("prompt_tokens_details") or {}
    reasoning_tokens = int(completion_details.get("reasoning_tokens") or 0)
    cached_tokens = int(prompt_details.get("cached_tokens") or 0)

    effort = (
        reasoning_effort_setting if supports_reasoning(model) else None
    )

    cost_usd = estimate_cost_usd(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
    )

    return {
        # Campi storici (backward compat).
        "model": model,
        "prompt": prompt_tokens,
        "completion": completion_tokens,
        "total": total_tokens,
        # Nuovi metadati di tracking.
        "reasoning_effort": effort,
        "reasoning_tokens": reasoning_tokens,
        "cached_tokens": cached_tokens,
        "duration_ms": int(duration_ms),
        "cost_usd": cost_usd,
    }
