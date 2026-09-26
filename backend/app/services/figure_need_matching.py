"""Abbinamento fabbisogno ↔ figura di fonte (piano delle figure, WP6, doc 18 §23.3).

Deterministico e lessicale, su `depicts` (PROMPT 18/20, inglese canonico) e
sui campi inglesi del fabbisogno (PROMPT 22: `object_en`, `object_terms`,
`variant_en`, `variant_terms`, `is_base`, `representation`). Nessuna
chiamata AI, nessun accesso al DB.

Relazioni (dalla più alla meno specifica):

| relazione | quando | copre? | livello |
|---|---|---|---|
| `exact` | stesso oggetto e stessa variante (o forma base per un fabbisogno base) | sì | 4 |
| `exact_mixed` | variante nel nome («rotational vibrometer») o più specifica | sì | 3 |
| `multi` | la figura mostra più varianti, fra cui quella chiesta | sì | 2 |
| `specialized` | fabbisogno base, la figura mostra una variante dell'oggetto | sì | 2 |
| `legacy` | figura senza `depicts`: oggetto e variante nel suo testo | sì | 1 |
| `base_implicit` | figura senza `depicts`, fabbisogno base, evidenza lessicale forte | sì | 1 |
| `generic` | stesso oggetto senza variante, fabbisogno con variante | **no** | — |
| `conflict` | stesso oggetto con un'altra variante | **no** | — |
| `none` | oggetto diverso | no | — |

Una figura generica non copre MAI un fabbisogno con variante. La variante
si confronta per uguaglianza esatta delle radici (stem leggero: nessun
prefisso, che darebbe «different» ~ «differential»): tutte le radici della
variante chiesta devono stare fra quelle della figura.

Rappresentazione: stessa famiglia (disegni: schematic, block_diagram,
circuit; foto: photo, micrograph; grafici: chart) → livello pieno; disegno
contro foto → un livello in meno; grafico contro disegno o foto → non
copre (un grafico di risposta non è lo schema dello strumento).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.services.openai_figure_describe_service import depicts_current

TIERS = {
    "exact": 4,
    "exact_mixed": 3,
    "multi": 2,
    "specialized": 2,
    "legacy": 1,
    "base_implicit": 1,
}
COVERING = frozenset(TIERS)

# Evidenza lessicale minima per una figura senza `depicts` e un fabbisogno
# base (termini del fabbisogno trovati nel testo della figura).
BASE_IMPLICIT_MIN_SCORE = 3.0

# Parole che non distinguono una variante (restano nell'oggetto).
_GENERIC = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "for",
        "with",
        "and",
        "or",
        "on",
        "to",
        "type",
        "mode",
        "based",
        "configuration",
        "setup",
        "set",
        "system",
        "principle",
        "schematic",
        "scheme",
        "diagram",
        "layout",
        "basic",
        "base",
        "standard",
        # La forma standard («single-point») è la forma base (PROMPT 22 v3):
        # «multi-point» resta «multi».
        "single",
        "point",
        "conventional",
        "general",
        "typical",
        "example",
        "device",
        "instrument",
        "head",
    }
)
# «in» resta: serve alle varianti composte («in-plane» → in, plane).

# Forme dello stesso oggetto (radici già ridotte).
_SYNONYMS = {
    "vibrometry": "vibrometer",
    "ldv": "vibrometer",
    "gage": "gauge",
    # Allestimenti di prova: lo stesso oggetto ha nomi diversi.
    "configuration": "setup",
    "arrangement": "setup",
    "chain": "setup",
    "rig": "setup",
    "bench": "setup",
    "system": "setup",
}

_SUFFIXES = ("ational", "ning", "ing", "ies", "al", "s")

_FAMILY = {
    "schematic": "drawing",
    "block_diagram": "drawing",
    "circuit": "drawing",
    "photo": "photo",
    "micrograph": "photo",
    "chart": "chart",
}


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()


def stem(token: str) -> str:
    """Radice leggera di una parola inglese (uguaglianza esatta, mai prefissi)."""
    word = token
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            if suffix == "ational":
                word = word[: -len(suffix)] + "ation"
            elif suffix == "ies":
                word = word[: -len(suffix)] + "y"
            elif suffix == "s" and word.endswith("ss"):
                continue
            else:
                word = word[: -len(suffix)]
            break
    return _SYNONYMS.get(word, word)


def tokens(text: str) -> frozenset[str]:
    """Radici delle parole («in-plane» → in, plane; «single point» uguale)."""
    return frozenset(stem(t) for t in re.findall(r"\w+", _fold(text)) if t)


def _variant_tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in tokens(text) if t not in _GENERIC)


# Parole generiche che però DISTINGUONO una variante da un'altra: tolte per
# riconoscere la forma base, ma se la variante chiesta le contiene devono
# esserci anche nella figura («on-axis» non è «off-axis», «single-axis» non
# è «three-axis»).
_DISTINGUISHING = frozenset({"on", "with", "single", "point"})


def _required_words(text: str) -> frozenset[str]:
    return frozenset(t for t in tokens(text) if t in _DISTINGUISHING)


@dataclass(frozen=True)
class Match:
    relation: str
    tier: int
    score: float

    @property
    def covers(self) -> bool:
        return self.relation in COVERING


NO_MATCH = Match("none", 0, 0.0)


@dataclass(frozen=True)
class NeedKey:
    """Campi del fabbisogno che servono all'abbinamento, già ridotti."""

    object_heads: frozenset[str]
    object_tokens: frozenset[str]
    object_phrases: tuple[frozenset[str], ...]
    variants: tuple[frozenset[str], ...]
    is_base: bool
    family: str | None
    terms: frozenset[str]
    topic: frozenset[str] = frozenset()
    # Per ogni forma della variante, le parole distintive tolte che la
    # figura deve comunque avere (allineato a `variants`).
    required: tuple[frozenset[str], ...] = ()

    @classmethod
    def from_need(cls, need: Mapping[str, Any]) -> NeedKey:
        phrases = [str(need.get("object_en") or "")]
        phrases += [str(t) for t in need.get("object_terms") or []]
        object_phrases = tuple(p for p in (tokens(x) for x in phrases) if p)
        # Testa dell'oggetto solo dall'inglese (`object_en`): nei termini
        # della lingua del corso la testa non è l'ultima parola.
        head = _last_word(phrases[0])
        heads = frozenset({stem(head)}) if head else frozenset()
        variant_texts = [str(need.get("variant_en") or "")]
        variant_texts += [str(t) for t in need.get("variant_terms") or []]
        forms = [(_variant_tokens(x), _required_words(x)) for x in variant_texts]
        forms = [(v, r) for v, r in forms if v]
        variants = tuple(v for v, _r in forms)
        required = tuple(r for _v, r in forms)
        # Base se la variante, tolte le parole generiche, è vuota: il flag
        # `is_base` da solo non basta (fabbisogno incoerente del modello).
        is_base = not variants
        # L'oggetto del fabbisogno può contenere la variante («scanning laser
        # Doppler vibrometer»): per riconoscere la variante scritta nel nome
        # dell'oggetto della FIGURA conta solo l'oggetto base.
        variant_words = frozenset().union(*variants) if variants and not is_base else frozenset()
        object_phrases = tuple(
            p for p in (phrase - variant_words for phrase in object_phrases) if p
        )
        terms = frozenset().union(
            *(
                tokens(str(t))
                for t in [
                    *(need.get("terms_en") or []),
                    *(need.get("terms_course") or []),
                    need.get("object_en") or "",
                ]
            )
        )
        topic = frozenset(
            t for t in terms - frozenset().union(*object_phrases, heads) - _GENERIC if len(t) > 2
        )
        return cls(
            topic=topic,
            object_heads=heads,
            object_tokens=frozenset().union(*object_phrases) if object_phrases else frozenset(),
            object_phrases=object_phrases,
            variants=() if is_base else variants,
            required=() if is_base else required,
            is_base=is_base,
            family=_FAMILY.get(str(need.get("representation") or "")),
            terms=terms,
        )


def _last_word(text: str) -> str:
    words = re.findall(r"\w+", _fold(text))
    return words[-1] if words else ""


def _same_object(key: NeedKey, obj: frozenset[str], head: str) -> bool:
    if head and head in key.object_heads:
        return True
    return any(phrase <= obj for phrase in key.object_phrases if phrase)


def _has_variant(
    key: NeedKey, figure_variant: frozenset[str], figure_words: frozenset[str] | None = None
) -> bool:
    """Una forma della variante chiesta sta nella variante della figura (con
    le sue parole distintive fra le parole della figura)."""
    words = figure_words if figure_words is not None else figure_variant
    required = key.required or tuple(frozenset() for _ in key.variants)
    return any(
        v <= figure_variant and need <= words
        for v, need in zip(key.variants, required, strict=True)
    )


def _family_penalty(key: NeedKey, kind: str | None) -> int | None:
    """Livelli da togliere per la rappresentazione; None = non copre."""
    figure = _FAMILY.get(kind or "")
    if key.family is None or figure is None or key.family == figure:
        return 0
    if "chart" in (key.family, figure):
        return None
    return 1


def figure_text_tokens(fig: Any) -> frozenset[str]:
    """Testo della figura per l'evidenza senza `depicts` (didascalia,
    descrizione, parole chiave inglesi e della lingua del corso)."""
    keywords = fig.keywords if isinstance(getattr(fig, "keywords", None), dict) else {}
    words = [str(k) for k in (keywords.get("en") or []) + (keywords.get("course") or [])]
    return tokens(
        " ".join(
            [str(getattr(fig, "source_caption", "") or ""), str(fig.description or ""), *words]
        )
    )


def _lexical(key: NeedKey, text: frozenset[str]) -> float:
    return float(len(key.terms & text))


@dataclass(frozen=True)
class DepictedView:
    obj: frozenset[str]
    head: str
    declared: frozenset[str]
    declared_words: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FigureView:
    """Dati della figura già ridotti per l'abbinamento (si calcolano una
    volta sola per figura, poi valgono per tutti i fabbisogni)."""

    figure_id: Any
    kind: str | None
    text: frozenset[str]
    items: tuple[DepictedView, ...] | None  # None = senza `depicts` corrente

    @classmethod
    def of(cls, fig: Any) -> FigureView:
        depicts = getattr(fig, "depicts", None)
        items: tuple[DepictedView, ...] | None = None
        if isinstance(depicts, Mapping) and depicts_current(depicts):
            views = []
            for item in depicts.get("items") or []:
                if not isinstance(item, Mapping):
                    continue
                obj_text = str(item.get("object_en") or "")
                views.append(
                    DepictedView(
                        obj=tokens(obj_text),
                        head=stem(_last_word(obj_text)),
                        declared=_variant_tokens(str(item.get("variant_en") or "")),
                        declared_words=tokens(str(item.get("variant_en") or "")),
                    )
                )
            items = tuple(views)
        return cls(
            figure_id=getattr(fig, "id", None),
            kind=getattr(fig, "kind", None),
            text=figure_text_tokens(fig),
            items=items,
        )

    def index_tokens(self) -> frozenset[str]:
        """Parole con cui un fabbisogno può trovare la figura."""
        if self.items is None:
            return self.text
        out: set[str] = set()
        for item in self.items:
            out |= item.obj
        return frozenset(out)


def match(need: Mapping[str, Any] | NeedKey, fig: Any, *, legacy: bool = True) -> Match:
    """Relazione fra un fabbisogno e una figura (vedi la tabella del modulo).
    Con `legacy=False` una figura senza `depicts` corrente non copre mai
    (`no_depicts`): l'evidenza dal solo testo è poco precisa (M-A3)."""
    key = need if isinstance(need, NeedKey) else NeedKey.from_need(need)
    view = fig if isinstance(fig, FigureView) else FigureView.of(fig)
    penalty = _family_penalty(key, view.kind)
    score = _lexical(key, view.text)
    if view.items is not None:
        relation = _depicts_relation(key, view.items)
        # Un fabbisogno base («grafico di confronto delle prestazioni del
        # vibrometro») non è coperto da QUALUNQUE figura dell'oggetto: serve
        # almeno un termine del tema, oltre all'oggetto, nel testo della figura.
        if key.is_base and key.topic and relation in COVERING and not (key.topic & view.text):
            relation = "off_topic"
    elif legacy:
        relation = _legacy_relation(key, view.text, score)
    else:
        relation = "no_depicts"
    if relation not in COVERING:
        return Match(relation, 0, score)
    if penalty is None:
        return Match("representation", 0, score)
    return Match(relation, max(1, TIERS[relation] - penalty), score)


def _depicts_relation(key: NeedKey, items: tuple[DepictedView, ...]) -> str:
    seen_object = False
    seen_other_variant = False
    exact = mixed = False
    for item in items:
        obj, head, declared = item.obj, item.head, item.declared
        if not _same_object(key, obj, head):
            continue
        # Variante scritta nel nome dell'oggetto («rotational vibrometer»).
        in_name = frozenset(t for t in obj - key.object_tokens - {head} if t not in _GENERIC)
        # Stessa testa ma oggetto diverso («force sensor» / «pressure
        # sensor»): i qualificatori dei due oggetti non si toccano, salvo che
        # il qualificatore della figura sia proprio la variante chiesta
        # («fiber optic differential vibrometer» per il vibrometro differenziale).
        need_quals = key.object_tokens - key.object_heads - _GENERIC
        figure_quals = obj - {head} - _GENERIC
        if (
            need_quals
            and figure_quals
            and not (need_quals & figure_quals)
            and not (not key.is_base and _has_variant(key, figure_quals, obj))
        ):
            continue
        seen_object = True
        if key.is_base:
            if not declared and not in_name:
                exact = True
            else:
                seen_other_variant = True
            continue
        words = item.declared_words | obj
        if _has_variant(key, declared, item.declared_words):
            # Esatta solo se la figura non è più specifica della variante
            # chiesta (scansione continua per «scansione»: un livello sotto).
            if any(v <= declared and not (declared - v - key.object_tokens) for v in key.variants):
                exact = True
            else:
                mixed = True
        elif _has_variant(key, declared | in_name, words):
            mixed = True
        elif declared or in_name:
            seen_other_variant = True
    if key.is_base:
        if exact:
            # Una panoramica che mostra anche altre varianti vale meno della
            # figura della sola forma base.
            return "multi" if seen_other_variant else "exact"
        return "specialized" if seen_other_variant else "none"
    # (il controllo del tema per i fabbisogni base sta in `match`)
    if (exact or mixed) and seen_other_variant:
        return "multi"
    if exact:
        return "exact"
    if mixed:
        return "exact_mixed"
    if seen_other_variant:
        return "conflict"
    return "generic" if seen_object else "none"


def _legacy_relation(key: NeedKey, text: frozenset[str], score: float) -> str:
    """Figura senza `depicts`: evidenza prudente dal testo."""
    has_object = bool(key.object_heads & text) or any(p <= text for p in key.object_phrases)
    if not has_object:
        return "none"
    if key.is_base:
        return "base_implicit" if score >= BASE_IMPLICIT_MIN_SCORE else "generic"
    return "legacy" if _has_variant(key, text) else "generic"


def best_matches(need: Mapping[str, Any], figures: Iterable[Any]) -> list[tuple[Any, Match]]:
    """Figure che coprono il fabbisogno, dalla più specifica."""
    key = NeedKey.from_need(need)
    out = [(fig, m) for fig in figures if (m := match(key, fig)).covers]
    out.sort(key=lambda fm: (-fm[1].tier, -fm[1].score, str(fm[0].id)))
    return out


class FigureIndex:
    """Viste delle figure e indice per parola: un fabbisogno si confronta
    solo con le figure che condividono una parola del suo oggetto (le altre
    sono `none` per costruzione)."""

    def __init__(self, figures: Iterable[Any], *, legacy: bool = True) -> None:
        self.legacy = legacy
        self.views: dict[Any, FigureView] = {}
        self._by_token: dict[str, set[Any]] = {}
        for fig in figures:
            view = FigureView.of(fig)
            self.views[view.figure_id] = view
            for token in view.index_tokens():
                self._by_token.setdefault(token, set()).add(view.figure_id)

    def covering(self, need: Mapping[str, Any] | NeedKey) -> list[tuple[Any, Match]]:
        """(figure_id, relazione) delle figure che coprono il fabbisogno."""
        key = need if isinstance(need, NeedKey) else NeedKey.from_need(need)
        probe = key.object_heads | key.object_tokens
        candidates: set[Any] = set()
        for token in probe:
            candidates |= self._by_token.get(token, set())
        out = [
            (fid, found)
            for fid in candidates
            if (found := match(key, self.views[fid], legacy=self.legacy)).covers
        ]
        out.sort(key=lambda fm: (-fm[1].tier, -fm[1].score, str(fm[0])))
        return out
