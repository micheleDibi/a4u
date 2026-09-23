"""Etichette e didascalie delle figure («Figura 2.1.», «Fig. 3», «FIGURE 4:»)."""

from __future__ import annotations

import re

# Etichetta a inizio didascalia. Il gruppo `kind` distingue figure e tabelle.
_LABEL_RE = re.compile(
    r"^\s*(?P<word>(?P<fig>fig(?:ura|ure|\.)?|abb(?:ildung|\.)?|illustrazione|schema)"
    r"|(?P<tab>tab(?:ella|le|\.)?))\s*(?P<num>\d+(?:[.\-]\d+)*[a-z]?)\s*[.:\-–—)]?",
    re.IGNORECASE,
)
_SPACES_RE = re.compile(r"\s+")

MAX_CAPTION_CHARS = 600


def normalize(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _SPACES_RE.sub(" ", text).strip()
    return cleaned or None


def caption_label(caption: str | None) -> str | None:
    """«Figura 2.1» da «Figura 2.1. Schema …»; None se non c'è un'etichetta
    di figura (le tabelle non contano)."""
    if not caption:
        return None
    match = _LABEL_RE.match(caption)
    if match is None or match.group("fig") is None:
        return None
    word = match.group("word").rstrip(".")
    if len(word) <= 4:
        word = f"{word}."
    return f"{word} {match.group('num')}"


def is_figure_caption(line: str) -> bool:
    match = _LABEL_RE.match(line)
    return match is not None and match.group("fig") is not None


def is_table_caption(line: str) -> bool:
    match = _LABEL_RE.match(line)
    return match is not None and match.group("tab") is not None


def clip_caption(text: str | None) -> str | None:
    cleaned = normalize(text)
    if cleaned is None:
        return None
    if len(cleaned) <= MAX_CAPTION_CHARS:
        return cleaned
    return cleaned[:MAX_CAPTION_CHARS].rsplit(" ", 1)[0] + " …"
