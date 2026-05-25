"""Utility per sanitizzare l'input utente prima di passarlo a un LLM.

Mitiga (non elimina) i tentativi di prompt injection rimuovendo pattern
noti come "ignora le istruzioni precedenti", "sei ora un …", "system
prompt", ecc. È una **difesa di primo livello**: la seconda linea sono
le regole di sicurezza esplicite nel system prompt dell'LLM, e la terza
il filtro lato modello stesso (rifiuto di output fuori contesto).

Riusabile da qualsiasi servizio che apra una conversazione con un LLM
basata su input non-fidato. Attualmente usato da `nova_service`.
"""
from __future__ import annotations

import re

# Pattern noti di prompt injection (case-insensitive). Lista compilata
# una volta sola al modulo-load per efficienza.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignora\s+(le\s+)?istruzioni\s+preced",
        r"ignore\s+(all\s+)?previous\s+instructions?",
        r"disregard\s+(all\s+)?previous\s+instructions?",
        r"you\s+are\s+now\s+",
        r"sei\s+ora\s+(un|una|il|lo|la|i|gli|le)?\s*\w",
        r"fai\s+finta\s+di\s+essere",
        r"pretend\s+(you\s+are|to\s+be)",
        r"act\s+as\s+(a|an|the)\s+",
        r"agisci\s+come\s+",
        r"system\s*prompt",
        r"(new|nuove)\s+istruzioni",
        r"new\s+instructions?",
        r"forget\s+(everything|all|previous)",
        r"dimentica\s+(tutto|tutte|tutti)",
        r"override\s+(your|all)\s+",
        r"\bjailbreak\b",
        r"\bDAN\s+mode\b",
        r"developer\s+mode",
        r"modalit[aà]\s+sviluppatore",
        r"reveal\s+(your|the)\s+(system|prompt|instructions?)",
        r"rivela\s+(il|tuo|tue)\s+(prompt|istruzioni|sistema)",
        r"role[\s_-]*play\s+as",
        r"ruolo\s*[:=]?\s*\w+",
    )
)


def sanitize_user_input(text: str, max_length: int = 2000) -> str:
    """Sanifica `text` rimuovendo pattern di prompt injection.

    - Sostituisce ogni match con `[rimosso]`.
    - Tronca a `max_length` caratteri.
    - Restituisce stringa vuota se l'input è None / non-stringa.

    NON è una difesa completa: il system prompt dell'LLM DEVE comunque
    contenere regole esplicite di rifiuto e il modello DEVE rispondere
    coerentemente. Questa funzione riduce solo la superficie di attacco
    più ovvia.
    """
    if not isinstance(text, str):
        return ""
    cleaned = text
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub("[rimosso]", cleaned)
    return cleaned[:max_length].strip()


def contains_injection_attempt(text: str) -> bool:
    """True se `text` contiene almeno un pattern di prompt injection.

    Usato per audit/logging: dare visibilità di chi tenta di bypassare
    le regole. Non blocca la chiamata (la sanitize_user_input neutralizza
    comunque l'input prima di passarlo al modello).
    """
    if not isinstance(text, str):
        return False
    return any(pat.search(text) for pat in _INJECTION_PATTERNS)
