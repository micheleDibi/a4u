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


# --- Testo di terzi (figure di fonte) ----------------------------------------
#
# Didascalie e contesti estratti dai documenti, bibliografie, metadati di
# Wikimedia/OpenAlex e l'output della Vision finiscono nei prompt come DATI.
# `sanitize_user_input` qui corromperebbe testo legittimo (es. «ruolo del
# sensore», «act as a filter»): per i testi di terzi si neutralizzano solo i
# tentativi espliciti di istruzione, i falsi delimitatori e i prefissi di
# ruolo, e il testo va sempre dentro `data_block`.

_THIRD_PARTY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignora\s+(tutte\s+)?(le\s+)?istruzioni\s+preced\w*",
        r"ignore\s+(all\s+)?(the\s+)?previous\s+instructions?",
        r"disregard\s+(all\s+)?(the\s+)?previous\s+instructions?",
        r"forget\s+(everything|all\s+previous|previous\s+instructions?)",
        r"dimentica\s+(tutto|tutte\s+le\s+istruzioni)",
        r"(new|nuove)\s+(system\s+)?istruzioni\s*:",
        r"new\s+instructions?\s*:",
        r"you\s+are\s+now\s+(a|an|the)\b",
        r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions?)",
        r"rivela\s+(il\s+)?(tuo\s+)?(prompt|istruzioni)",
        r"system\s*prompt",
        r"\bjailbreak\b",
        r"developer\s+mode",
    )
)
_FAKE_DELIMITER_RE = re.compile(r"<{3,}|>{3,}|`{3,}")
_ROLE_PREFIX_RE = re.compile(
    r"(?im)^(\s*)(system|assistant|developer|user|sistema|assistente|utente)\s*:"
)
_INVISIBLE_RE = re.compile("[​-‏‪-‮⁠-⁤﻿]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANKS_RE = re.compile(r"[ \t]+")
_THIRD_PARTY_MARK = "[testo rimosso]"


def neutralize_third_party_text(text: str | None, max_length: int = 2000) -> str:
    """Rende inerte un testo di terzi prima di metterlo in un prompt.

    Toglie caratteri invisibili e di controllo, falsi delimitatori di dati
    (`<<<`, `>>>`, fence), prefissi di ruolo a inizio riga («system:»),
    sostituisce i tentativi espliciti di istruzione con «[testo rimosso]» e
    tronca a `max_length` su un confine di parola. Il resto del testo resta
    intatto (niente falsi positivi sulle didascalie tecniche).
    """
    if not isinstance(text, str):
        return ""
    cleaned = _INVISIBLE_RE.sub("", text)
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = _FAKE_DELIMITER_RE.sub(" ", cleaned)
    cleaned = _ROLE_PREFIX_RE.sub(lambda m: f"{m.group(1)}{m.group(2)} -", cleaned)
    for pattern in _THIRD_PARTY_PATTERNS:
        cleaned = pattern.sub(_THIRD_PARTY_MARK, cleaned)
    cleaned = "\n".join(_BLANKS_RE.sub(" ", line).strip() for line in cleaned.splitlines())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) > max_length:
        cut = cleaned[:max_length]
        cleaned = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip() + " …"
    return cleaned


def data_block(label: str, text: str) -> str:
    """Testo di terzi (già neutralizzato) fra delimitatori di dati: il
    prompt di sistema dice che quanto sta fra `<<<` e `>>>` è materiale da
    descrivere, mai istruzioni da eseguire."""
    return f"<<<{label}\n{text}\n>>>"
