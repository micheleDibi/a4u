"""Generazione del testo completo di una lezione (Fase 3, §6).

Una chiamata API per lezione: prende in input la struttura formativa
approvata (Fase 2 — `learning_objectives`, `mandatory_topics`,
`prerequisites`, `section_outline`), il glossario corso e i documenti
di riferimento; produce il testo Markdown della lezione + asset visivi
(figure Mermaid, Vega-Lite, DOT e `function`, formule LaTeX, tabelle,
esempi), references e coverage_check.

Il testo del system prompt è statico e descrive sempre le quattro
famiglie di figure (A19); solo l'`enum` di `visual_assets[].format`
nello schema strict è ristretto a `figure_render_service.available_formats()`
(kill-switch e dipendenze del server).

Errori → `OpenAILessonContentError` (sottoclasse di `OpenAIError`).
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.course_lesson_content import (
    LessonAssessmentOutput,
    LessonContentOutput,
)
from app.services.figure_compute.function_parse import FUNCTIONS
from app.services.figure_render_service import available_formats
from app.services.figure_theme import MERMAID_D8_TYPES, MERMAID_EXCLUDED_TYPES
from app.services.openai_client import (
    OpenAIError,
    OpenAINotConfiguredError,
    apply_reasoning_effort,
    get_client,
)
from app.services.openai_pricing import build_usage_dict
from app.services.prompt_register import (
    REGENERATION_REGISTER_NOTE,
    academic_register_block,
)

log = get_logger("app.openai_lesson_content")


class OpenAILessonContentError(OpenAIError):
    """Errore specifico delle chiamate di generazione contenuti lezione (§6)."""


# System prompt — versione rivista per output naturale, senza codici
# tecnici nel testo e senza struttura accademica rigida visibile.
# Blocco storico sulle fonti (solo etichettatura delle citazioni). Resta
# in uso con il kill-switch del grounding disattivato.
_RIFERIMENTI_BLOCK = """\
RIFERIMENTI

- Cita i documenti di riferimento DOVE LI USI
- `source = "documento_caricato"` SOLO per i documenti NOMINATI nella
  sezione "Documenti di riferimento". L'eventuale "Materiale di
  contesto aggiuntivo (non citabile)" non va MAI citato né menzionato
  come fonte: usane i contenuti senza riferirne l'origine.
- NON inventare bibliografia. Eventuali letture aggiuntive devono
  essere etichettate `source = "suggerimento_generale"`.
"""

# Grounding sui documenti dell'utente (regola forte del docente): i
# documenti caricati pesano più della conoscenza generale del modello.
# Rimanda alla REGOLA 1 del REGISTRO senza riformularla.
_FONTI_BLOCK = """\
FONTI E ANCORAGGIO — REGOLA FORTE

Nel messaggio utente trovi "Documenti di riferimento": estratti
(definizioni, formule, concetti, esempi) selezionati dai materiali
caricati dal docente per QUESTA lezione. Hanno priorità su qualunque
altra fonte, inclusa la tua conoscenza generale.

- Quando i documenti coprono un tema, ogni affermazione sostanziale
  (definizioni, enunciati, formule, dati, casi, attribuzioni) deve essere
  riconducibile a un estratto: riprendine impostazione, terminologia,
  notazione ed esempi. Se il documento e la tua conoscenza divergono,
  segui il documento.
- Quando i documenti NON coprono un tema obbligatorio, trattalo comunque
  con la conoscenza consolidata della disciplina (manuali standard,
  risultati classici), mai con giudizi non argomentati o esempi
  inventati ad hoc. La copertura di obiettivi e temi obbligatori prevale
  sempre: un tema assente dai documenti non va saltato.
- I giudizi di valore seguono la REGOLA 1 del REGISTRO.
- Non indicare nel testo da dove proviene un'affermazione (niente "come
  dice il documento", niente marcatori): la provenienza va SOLO in
  `references`.

REFERENCES (schema esistente: `citation` + `source`)
- Una voce `source = "documento_caricato"` per OGNI documento nominato
  nella sezione "Documenti di riferimento" di cui hai usato gli estratti:
  `citation` = il nome o la Fonte come compaiono nell'header, seguito da
  " — " e dai titoli delle sezioni della lezione in cui lo hai usato.
- Una voce `source = "suggerimento_generale"` per OGNI tema obbligatorio
  trattato senza copertura documentale: `citation` = opera di riferimento
  standard della disciplina seguita da " — " e dal tema coperto.
- Il "Materiale di contesto aggiuntivo (non citabile)" non va MAI citato
  né menzionato come fonte: usane i contenuti senza riferirne l'origine.
- NON inventare bibliografia.
"""

# Blocco «FORMATI DELLE FIGURE» (D5, D8, D9): gli elenchi dei tipi Mermaid
# e delle funzioni ammesse sono derivati dalle stesse costanti del
# validatore, così prompt e gate non possono divergere. Gli esempi sono
# costanti separate (non f-string: le graffe sono JSON letterale) e
# vengono verificati dai validatori del registro in
# `tests/test_prompt_figures.py`.
_MERMAID_TYPES_TEXT = ", ".join(MERMAID_D8_TYPES)
_MERMAID_EXCLUDED_TEXT = ", ".join(MERMAID_EXCLUDED_TYPES)
_FUNCTION_FUNCTIONS_TEXT = ", ".join(sorted(FUNCTIONS))

# Esempio Vega-Lite minimo (≤ 300 caratteri): barre con dati inline,
# `clip`, `scale.domain` e unità di misura sull'asse quantitativo; senza
# `title` (opzionale) per restare nel budget di A5.
_VEGALITE_EXAMPLE = (
    '{"data":{"values":[{"mese":"gen","mm":80},{"mese":"feb","mm":65}]},"mark":{"type":"bar",'
    '"clip":true},"encoding":{"x":{"field":"mese","type":"nominal","axis":{"title":"Mese"}},'
    '"y":{"field":"mm","type":"quantitative","scale":{"domain":[0,100]},"axis":{"title":'
    '"Precipitazioni (mm)"}}}}'
)

# Esempio DOT minimo (≤ 150 caratteri): label brevi, nessuno stile.
_DOT_EXAMPLE = (
    'digraph G { rankdir=LR; A [label="Ingresso"]; B [label="Elaborazione"]; '
    'C [label="Uscita"]; A -> B -> C; }'
)

# Schema compatto di `FunctionFigureSpec` (schemas/figure_function.py) a
# una riga: campi, enum e limiti; il test di WP3 verifica che ogni campo
# e ogni valore degli enum compaiano nel prompt.
_FUNCTION_SPEC_COMPACT = (
    '{"kind":"function_study|tangent|area|family|level_curves",'
    '"expressions":[{"expr":str,"label":str}] (1-4),'
    '"variable":"x","variables":["x","y"] (solo level_curves),'
    '"domain":[min,max],"range":[min,max]|null,'
    '"show":["zeros"|"critical_points"|"inflection_points"|"asymptotes"|'
    '"discontinuities"|"formula"],'
    '"annotations":[{"kind":"tangent"|"point","at":n,"expr_index":0,"label":str}|'
    '{"kind":"area","between":[a,b],"expr_index":0,"against":int|null,"label":str}] (≤ 6),'
    '"parameter":{"name":"k","values":[n,...]} (solo family),'
    '"sampling":{"points":800},"levels":int|[n,...] (solo level_curves)}'
)

# Esempio D9 completo (studio di funzione): nessun numero calcolato.
_FUNCTION_EXAMPLE = (
    '{"kind":"function_study","expressions":[{"expr":"(x**2-1)/(x-2)","label":"f"}],'
    '"variable":"x","domain":[-4,6],"range":[-12,12],"show":["zeros","critical_points",'
    '"asymptotes","formula"],"annotations":[{"kind":"point","at":0,"expr_index":0,'
    '"label":"intercetta"}]}'
)


def _system_prompt(
    language_code: str,
    *,
    ruolo_docente: str = "",
    stile_insegnamento: str = "",
    livello_eqf: str = "",
    grounding_enabled: bool = True,
) -> str:
    register_block = academic_register_block("content", language_code)
    fonti_block = f"\n{_FONTI_BLOCK}" if grounding_enabled else ""
    riferimenti_block = "" if grounding_enabled else f"{_RIFERIMENTI_BLOCK}\n"
    return f"""\
Sei un autore di materiale didattico universitario di alto livello.
Il tuo compito è scrivere il TESTO COMPLETO di una singola lezione,
nel registro di manuale universitario definito nel blocco REGISTRO qui
sotto, partendo dalla sua struttura formativa già approvata.
{fonti_block}
REQUISITI — TESTO

- Markdown, in lingua {language_code}.
- Tono coerente con ruolo "{ruolo_docente}", stile
  "{stile_insegnamento}" e livello EQF {livello_eqf}, entro il registro
  definito sotto: ruolo e stile modulano lessico e profondità, non il
  registro.
- NON usare h1 nel content (riservato al titolo della lezione).
- Anticipa fraintendimenti tipici degli studenti, indicando quale
  ipotesi o passaggio li genera.

{register_block}

- STILE — il testo deve leggersi come prosa didattica scritta da un
  docente, non come scheda tecnica. Spiega in modo discorsivo,
  intercalando definizioni, intuizioni ed esempi quando servono, senza
  mai usare etichette esplicite tipo "Definizione formale",
  "Spiegazione intuitiva", "Esempio:". Segui questi principi:

  - La lunghezza di frasi e paragrafi segue il contenuto: enunciati e
    rimandi brevi, condizioni e dimostrazioni lunghe; evita un ritmo
    uniforme senza cercare l'effetto.
  - Non mantenere una struttura sintattica uniforme; evita schemi
    retorici ripetitivi.
  - Non aprire i paragrafi con connettivi standard (Inoltre, Tuttavia,
    È importante notare). Entra nel merito.
  - Evita formule stereotipate ("è importante notare", "si osserva
    che", "in conclusione", "in questo contesto"), salvo quando
    strettamente necessarie.
  - Non rendere simmetrica la lunghezza dei paragrafi: alcuni concetti
    richiedono poche righe, altri una trattazione molto più ampia.
  - Evita le triadi automatiche. Se un concetto ha due aspetti, dinne
    due; non gonfiarli a tre.
  - Dove pertinente — non in ogni sezione, ma quando il concetto lo
    giustifica — non limitarti a definire: spiega perché un'idea si è
    sviluppata e quali problemi cercava di risolvere, sulla base dei
    documenti di riferimento o della storia consolidata della
    disciplina.
  - Inserisci, in modo irregolare, osservazioni tipiche di una lezione
    reale: errori frequenti, dubbi comuni, ciascuno ricondotto al punto
    tecnico che lo genera (quale ipotesi viene dimenticata, quale
    passaggio viene confuso).
  - Una domanda può aprire un ragionamento solo se il testo la risponde
    subito con un argomento; non usarla come enfasi né come apertura di
    paragrafo.
  - La sintesi non ripete: collega i risultati della lezione tra loro,
    alle ipotesi che li reggono e alla lezione successiva, indicando
    che cosa resta aperto.
  - Usa esempi concreti e specifici (numeri, nomi, casi reali della
    disciplina), tratti in via prioritaria dai documenti di riferimento;
    altrimenti casi classici della disciplina.

ESEMPIO DI REGISTRO DIDATTICO

Il campione seguente (scritto da un autore umano) mostra come si spiega
un concetto tecnico in prosa: si definisce, si scioglie la definizione,
si chiarisce a cosa serve. Non copiarne i contenuti: imitane la
costruzione.

Campione — registro didattico:
<<<
Un file system è quella parte di un sistema operativo responsabile di
gestione e organizzazione dei file. Per gestire un elevato numero di
file, un FS è strutturato in directory, cioè in un insieme di nodi
contenenti informazioni su tutti i file. Una directory è un file
speciale creato con l'obiettivo di risolvere la corrispondenza tra il
nome del file in formato testuale e il suo identificativo interno.
>>>

Replica QUESTO modo di scrivere — non questi argomenti — applicandolo
alla materia della lezione.

PROCESSO DI SCRITTURA — DUE FASI

Lavora internamente in due fasi prima di produrre l'output finale:

Fase 1 (interna): scrivi una prima stesura completa del testo,
concentrandoti solo su correttezza dei contenuti, copertura dei temi
e degli obiettivi. Non preoccuparti dello stile in questa fase.

Fase 2 (interna): RIVEDI frase per frase la stesura della Fase 1
applicando REGISTRO e STILE: (a) ogni asserzione valutativa viene
sostenuta da criterio, dato o fonte, oppure eliminata; (b) ogni
frase-sentenza, antitesi a effetto o domanda retorica viene ricomposta
in un periodo articolato che porta la ragione; (c) connettivi standard
e formule stereotipate vengono eliminati; (d) i passaggi meccanici
diventano formulazioni che un docente userebbe davvero a lezione. Non
spezzare periodi, non accorciare spiegazioni.

Nell'output JSON inserisci SOLO il risultato della Fase 2. La prima
stesura non deve mai comparire. Contenuti, formule, tabelle e tag
asset ([FIG:], [EQ:], [TAB:]) devono restare invariati tra le due fasi.

DELIMITATORI MATH — REGOLA RIGIDA
- Per math INLINE nel testo Markdown usa SEMPRE `$...$` (es. `$\\varphi$`,
  `$P \\lor \\neg P$`). NON usare `\\(...\\)`, NON usare parentesi tonde
  attorno al comando LaTeX (es. `(\\varphi)` è sbagliato — non viene
  renderizzato).
- Per math DISPLAY (formule centrate su linea propria) nel testo Markdown
  usa SEMPRE `$$...$$`. NON usare `\\[...\\]`. Tuttavia, le formule
  importanti vanno in `equations[]` come asset dedicato e referenziate
  nel testo via `[EQ:equation_id]` invece che inline.

DIVIETI ASSOLUTI NEL TESTO VISIBILE
- NON citare mai nel testo codici tecnici interni come `M1.L1`,
  `M2.L5`, `T1`, `O1`, `S2`, `asset_id`, `VIS-...`, `FIG-...`. Questi sono
  identificatori di sistema e non devono apparire al lettore.
- Quando vuoi richiamare un'altra lezione del corso, usa il suo
  TITOLO (es. "Nella lezione sulla Trasformata di Fourier abbiamo
  visto..."), MAI il codice.
- Le caption di figure, tabelle, formule devono essere brevi
  descrizioni semantiche; NON includere codici come "[A1]" o
  "Figura M1.L2.01", né iniziare con "Figura 1"/"Fig. 1": il numero
  lo mette il renderer.

CASO SPECIALE — LEZIONE INTRODUTTIVA (is_introductory=true):
- Nessun caso studio o dimostrazione tecnica complessa
- Tono accessibile e orientativo: presenta il percorso, le aspettative
  e i materiali nel registro del manuale; niente promesse, niente
  entusiasmo di maniera
- Tratta la bibliografia consigliata (riprendi e amplia la
  `recommended_bibliography` data in input, aggiungendo per ogni testo
  un breve commento sul suo ruolo nel corso)
- Spiega "come lavoreremo": lo stile d'aula, le aspettative
- Anteprima dei moduli successivi (richiamati per titolo, non per
  codice)

DIMENSIONAMENTO

Linea guida (non vincolante):
- profondita = introduttivo: ~250-400 parole per tema obbligatorio
- profondita = intermedio: ~400-700 parole per tema
- profondita = avanzato: ~700-1200 parole per tema
- profondita = specialistico: ~1000-1800 parole per tema
+ introduzione (~150-300) + sintesi (~150-300).

REQUISITI — ASSET VISIVI

- 1-3 figure per lezione (NON per la lezione introduttiva, dove sono
  opzionali e tipicamente 0-1)
- formule LaTeX TUTTE le volte che la disciplina lo richiede
- tabelle quando devi confrontare alternative o riassumere
  classificazioni

Per ogni asset: `asset_id` stabile (uso interno), referenziato almeno
una volta nel testo tramite `[FIG:asset_id]`, `[TAB:asset_id]`,
`[EQ:asset_id]` (questi tag verranno sostituiti dal renderer con
l'asset rendering — non devono apparire al lettore finale, ma servono
al parser). La `caption` è una breve descrizione semantica leggibile.

FORMATI DELLE FIGURE (`visual_assets[].format`; `content` è sempre una
stringa: codice, sorgente o spec JSON serializzata). Dal contenuto al
formato e al tipo di diagramma:
- processo, flusso, gerarchia, relazioni fra entità, scambio di
  messaggi, stati, linea del tempo, ripartizione → `mermaid` (tipo di
  diagramma corrispondente: flowchart, sequenceDiagram, classDiagram,
  stateDiagram-v2, erDiagram, mindmap, timeline, pie);
- dati, misure, distribuzioni, confronti quantitativi, serie
  temporali → `vegalite` (barre, linee, punti, aree);
- grafi con archi etichettati, alberi, automi, reti → `dot`;
- funzione matematica da studiare (grafico, tangente, area, famiglia
  con parametro, curve di livello) → `function`.
Niente prompt per immagini né descrizioni testuali: le immagini reali
le carica il docente dall'editor.

MERMAID 11. Tipi ammessi: {_MERMAID_TYPES_TEXT}.
Esclusi: {_MERMAID_EXCLUDED_TEXT}.
Label in testo semplice (niente HTML né markdown), tra virgolette
doppie se contengono caratteri speciali; nessuna direttiva
`%%{{init}}%%` né frontmatter: il tema lo impone il renderer.

VEGA-LITE (spec JSON v6, ≤ 4000 caratteri) SOLO per: (a) rette o
polinomi ausiliari sui dati con `data.sequence` + `transform.calculate`;
(b) dati dei documenti del corso, con la fonte nella caption; (c) dati
illustrativi, con la caption che termina con «Dati illustrativi, non
sperimentali». Dati inline in `data.values` (≤ 200 righe); vietati
`data.url`, `data.name`, `mark: "image"`, `config`, `$schema`, `params`,
`selection`, `tooltip`, `usermeta`, `encoding.href`: il tema lo inietta
il renderer e il grafico è statico. Obbligatori `"clip": true` sui mark
`line`/`area`/`point`/`trail` e `scale.domain` [min, max] sui canali
`x`/`y` quantitativi; al massimo una `title` (radice, ≤ 120 caratteri);
`axis.title` con l'unità di misura sugli assi quantitativi; legenda solo
con più serie. Le FUNZIONI MATEMATICHE (seno, esponenziale, potenze,
razionali su una `sequence`) NON si tracciano in Vega-Lite: usa
`function`. Esempio:
{_VEGALITE_EXAMPLE}

DOT (Graphviz): inizia con `graph`, `digraph` o `strict`; label brevi
tra virgolette doppie; nessun colore, font o stile (li impone il
renderer); mai `image`, `URL`, `href` o attributi che leggono file.
Esempio: {_DOT_EXAMPLE}

FUNCTION (figura calcolata da sympy e matplotlib): `content` è la
stringa JSON di questo oggetto:
{_FUNCTION_SPEC_COMPACT}
Espressioni in sintassi Python: `**` (mai `^`), `2*x` (mai `2x`), solo
la variabile dichiarata e l'eventuale `parameter.name`, costanti `pi`
ed `E`, funzioni ammesse: {_FUNCTION_FUNCTIONS_TEXT}.
NON scrivere numeri calcolati (zeri, massimi, integrali, asintoti) né
nella spec né nella caption: li calcola il renderer e li aggiunge alla
didascalia. Esempio:
{_FUNCTION_EXAMPLE}

EQUAZIONI — ENUNCIATO E DIMOSTRAZIONE (`equations[]`)
Per OGNI asset in `equations[]`:
- `latex`: la formula/relazione principale, LaTeX puro SENZA delimitatori.
- `kind`: classifica il tipo →
  `definition` | `formula` | `identity` | `theorem` | `proposition` |
  `lemma` | `corollary`.
- `statement`: l'ENUNCIATO formale (markdown; math inline SOLO con `$..$`).
  Obbligatorio per `theorem`/`proposition`/`lemma`/`corollary` (ipotesi +
  tesi) e per `definition` (la definizione precisa). Per `formula`/
  `identity` "nude" può restare vuoto ("").
- `proof`: la DIMOSTRAZIONE come lista ORDINATA di passaggi, SOLO quando il
  risultato è realmente dimostrabile (`theorem`/`proposition`/`lemma`/
  `corollary`). Ogni passaggio:
    · `latex`: il contenuto matematico del passo, LaTeX puro SENZA
      delimitatori (puoi usare ambienti `aligned`, `cases`, `align*` per il
      multilinea). Vuoto ("") se il passo è puramente discorsivo.
    · `text`: la spiegazione del passo (markdown; math inline con `$..$`).
  I passaggi devono essere CORRETTI, COMPLETI e in ordine logico,
  giustificando ogni deduzione fino alla tesi.
- NON inventare dimostrazioni: per `definition`, `formula` empiriche/
  postulate o `identity` elementari lascia `proof: []` (e, se non c'è un
  enunciato sensato, `statement: ""`).
- REGOLA RIGIDA sui campi formula (`latex` dell'equazione e di ogni
  passaggio): MAI delimitatori `$`/`$$`/`\\(`/`\\[`. Per il multilinea usa
  SEMPRE un ambiente COMPLETO e BILANCIATO `\\begin{{aligned}} ... \\end{{aligned}}`
  (oppure `cases`): MAI un `&` o un `\\\\` fuori da un ambiente, MAI un
  `\\end{{...}}` senza il corrispondente `\\begin{{...}}`. In dubbio, preferisci
  più passaggi `proof` brevi (una riga ciascuno) invece di un unico blocco
  `aligned` lungo.

ALLINEAMENTO

- Ogni obiettivo formativo in almeno una sezione
- Ogni tema obbligatorio in almeno una sezione
- Compila `coverage_check` mappando obiettivi e temi alle sezioni
- In `objectives_addressed` e in `coverage_check.objectives_covered[].objective`
  scrivi SOLO il codice fra parentesi quadre dell'obiettivo (`O1`, `O2`, ...),
  mai il suo testo. In `topics_addressed` e `topics_covered[].topic_id` SOLO il
  `topic_id`.

{riferimenti_block}NON GENERARE ESERCIZI: il campo `exercises_for_self_study` non è più
richiesto.

VERIFICA FINALE DEL REGISTRO
Prima di emettere il JSON: nessun giudizio senza criterio, nessuna
frase-sentenza, nessuna domanda retorica, nessuna iperbole. Se ne
trovi, ricomponi il periodo.

LINGUA — REGOLA TASSATIVA
TUTTO il testo leggibile dall'utente DEVE essere scritto in {language_code}: non solo
la prosa, ma anche OGNI campo testuale degli asset. In particolare:
- `caption` e `alt_text` degli asset visivi;
- le ETICHETTE / il testo dei nodi DENTRO il codice Mermaid (le label, NON la sintassi);
- `title`, `axis.title`, `legend.title` e `header.title` delle spec Vega-Lite; le
  `label` dei sorgenti DOT; `expressions[].label` e `annotations[].label` delle spec
  `function`;
- `caption`, intestazioni e celle delle tabelle (`markdown`);
- `label`, `statement`, `explanation` delle equazioni e il `text` di OGNI passo di `proof`;
- `title` e `content` degli esempi.
Restano invariati SOLO: la notazione matematica LaTeX (campi `latex`), la struttura
sintattica di Mermaid (tipo di diagramma, frecce, ID dei nodi), di Vega-Lite (chiavi
JSON, `field`, `type`, espressioni `datum.*`), di DOT (ID dei nodi, `->`/`--`, attributi
diversi da `label`) e di `function` (chiavi JSON, `expr`, `kind`, `show`), gli ID degli
asset, i tag `[FIG:..]`/`[TAB:..]`/`[EQ:..]`/`[EX:..]` e i codici di obiettivi (`O1`) e temi
(`T1`). NON lasciare in nessun campo testo in un'altra lingua (es. italiano): traduci
tutto in {language_code}.
Output: SOLO JSON valido conforme allo schema."""


# Addendum §9.3 — appeso al system prompt quando si rigenera una lezione
# già scritta (con o senza `regeneration_hint`).
REGENERATION_SUFFIX = (
    """\

ATTENZIONE: stai RIGENERANDO una lezione già scritta. Tieni in
considerazione la versione precedente e il feedback del docente.
- Mantieni invariati gli obiettivi formativi e i temi obbligatori
  forniti, a meno che il feedback non li tocchi esplicitamente.
- Riusa gli asset visivi della versione precedente quando ancora
  pertinenti, mantenendo gli stessi asset_id.
- Se il feedback chiede di rimuovere/sostituire un asset, fallo e
  documenta il cambiamento.
- Mantieni lessico e terminologia coerenti con il resto del corso.
"""
    + REGENERATION_REGISTER_NOTE
)


# JSON Schema verbatim §6.4 — passato a OpenAI come response_format.json_schema.
LESSON_CONTENT_JSON_SCHEMA: dict[str, Any] = {
    "name": "lesson_content",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "lesson_title": {"type": "string"},
            "is_introductory": {"type": "boolean"},
            "estimated_word_count": {"type": "integer"},
            "introduction": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "objectives_addressed": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "topics_addressed": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "section_id",
                        "title",
                        "content",
                        "objectives_addressed",
                        "topics_addressed",
                    ],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
            "key_takeaways": {
                "type": "array",
                "items": {"type": "string"},
            },
            "visual_assets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        # Le quattro famiglie renderizzabili (D1); a runtime
                        # `build_lesson_content_json_schema` restringe l'enum
                        # a `figure_render_service.available_formats()`.
                        "format": {
                            "type": "string",
                            "enum": ["mermaid", "vegalite", "dot", "function"],
                        },
                        "content": {"type": "string"},
                        "caption": {"type": "string"},
                        "alt_text": {"type": "string"},
                    },
                    "required": [
                        "asset_id",
                        "format",
                        "content",
                        "caption",
                        "alt_text",
                    ],
                    "additionalProperties": False,
                },
            },
            "tables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "table_id": {"type": "string"},
                        "markdown": {"type": "string"},
                        "caption": {"type": "string"},
                    },
                    "required": ["table_id", "markdown", "caption"],
                    "additionalProperties": False,
                },
            },
            "equations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "equation_id": {"type": "string"},
                        "latex": {"type": "string"},
                        "label": {"type": "string"},
                        "explanation": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": [
                                "definition",
                                "formula",
                                "identity",
                                "theorem",
                                "proposition",
                                "lemma",
                                "corollary",
                            ],
                        },
                        "statement": {"type": "string"},
                        "proof": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "latex": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["latex", "text"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": [
                        "equation_id",
                        "latex",
                        "label",
                        "explanation",
                        "kind",
                        "statement",
                        "proof",
                    ],
                    "additionalProperties": False,
                },
            },
            "examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "example_id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["example_id", "title", "content"],
                    "additionalProperties": False,
                },
            },
            "references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "citation": {"type": "string"},
                        "source": {
                            "type": "string",
                            "enum": [
                                "documento_caricato",
                                "suggerimento_generale",
                            ],
                        },
                    },
                    "required": ["citation", "source"],
                    "additionalProperties": False,
                },
            },
            "coverage_check": {
                "type": "object",
                "properties": {
                    "objectives_covered": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "objective": {"type": "string"},
                                "covered_in_section_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "objective",
                                "covered_in_section_ids",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "topics_covered": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "topic_id": {"type": "string"},
                                "covered_in_section_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": [
                                "topic_id",
                                "covered_in_section_ids",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["objectives_covered", "topics_covered"],
                "additionalProperties": False,
            },
        },
        "required": [
            "lesson_id",
            "lesson_title",
            "is_introductory",
            "estimated_word_count",
            "introduction",
            "sections",
            "summary",
            "key_takeaways",
            "visual_assets",
            "tables",
            "equations",
            "examples",
            "references",
            "coverage_check",
        ],
        "additionalProperties": False,
    },
}


def build_lesson_content_json_schema(
    *, objective_ids: Sequence[str] = (), visual_formats: Sequence[str] = ()
) -> dict[str, Any]:
    """Schema della singola chiamata: la costante base + l'`enum` dei
    codici obiettivo sui due campi di contabilità + l'`enum` dei formati
    di figura offerti al modello.

    Con l'`enum` il modello non PUÒ emettere un obiettivo che non esiste
    (era la causa di `lesson_content_unknown_objective`) né un formato di
    figura non renderizzabile su questo server (`visual_formats` =
    `figure_render_service.available_formats()`: kill-switch e dipendenze;
    il testo del prompt resta statico, A19).

    `deepcopy` obbligatorio: fino a `COURSE_LESSON_CONTENT_MAX_CONCURRENCY`
    lezioni sono in volo insieme e una mutazione in place farebbe colare
    l'enum di una lezione nella richiesta di un'altra.

    Con ENTRAMBI gli argomenti vuoti ritorna la costante per identità.
    Lista di obiettivi vuota (lezione senza obiettivi di Fase 2) → nessun
    `enum` sugli obiettivi: `"enum": []` non è uno schema strict valido e
    OpenAI risponderebbe 400 a ogni tentativo; lo stesso vale per i
    formati (vuoto → enum della costante, che elenca le quattro famiglie).
    """
    if not objective_ids and not visual_formats:
        return LESSON_CONTENT_JSON_SCHEMA
    schema = copy.deepcopy(LESSON_CONTENT_JSON_SCHEMA)
    props = schema["schema"]["properties"]
    if objective_ids:
        ids = list(objective_ids)
        props["sections"]["items"]["properties"]["objectives_addressed"]["items"]["enum"] = ids
        props["coverage_check"]["properties"]["objectives_covered"]["items"]["properties"][
            "objective"
        ]["enum"] = ids
    if visual_formats:
        props["visual_assets"]["items"]["properties"]["format"]["enum"] = list(visual_formats)
    return schema


async def generate_lesson_content(
    *,
    user_prompt: str,
    language_code: str,
    is_regeneration: bool,
    ruolo_docente: str = "",
    stile_insegnamento: str = "",
    livello_eqf: str = "",
    objective_ids: Sequence[str] = (),
) -> tuple[LessonContentOutput, dict[str, Any]]:
    """Chiama OpenAI per generare il testo completo di una lezione.

    Ritorna `(content, usage)` dove `usage` è un dict con i conteggi
    token. Solleva `OpenAILessonContentError` su errore HTTP/parsing/
    schema. Solleva `OpenAINotConfiguredError` se la API key è assente.

    NOTA: il modello `gpt-5.5` richiede `max_completion_tokens` (NON
    `max_tokens`) e non accetta `temperature` custom (solo default 1.0).
    L'output di una lezione completa è 8-15k token + reasoning, quindi
    `OPENAI_LESSON_CONTENT_MAX_TOKENS` deve partire alto (32000).
    """
    settings = get_settings()
    system_prompt = _system_prompt(
        language_code,
        ruolo_docente=ruolo_docente,
        stile_insegnamento=stile_insegnamento,
        livello_eqf=livello_eqf,
        grounding_enabled=settings.course_lesson_content_documents_selection_enabled,
    )
    if is_regeneration:
        system_prompt = system_prompt + REGENERATION_SUFFIX

    body = {
        "model": settings.openai_lesson_content_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": build_lesson_content_json_schema(
                objective_ids=objective_ids,
                visual_formats=available_formats(),
            ),
        },
        "max_completion_tokens": settings.openai_lesson_content_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_lesson_content_model,
        settings.openai_lesson_content_reasoning_effort,
    )
    log.info(
        "openai_lesson_content_request",
        chars=len(user_prompt),
        regeneration=is_regeneration,
        objective_ids=len(objective_ids),
        model=settings.openai_lesson_content_model,
        reasoning_effort=body.get("reasoning_effort"),
    )
    t0 = time.monotonic()
    try:
        # Timeout esteso: lezione completa può richiedere 60-120s di reasoning.
        async with get_client(timeout=600.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_lesson_content_http_error", error=str(exc))
        raise OpenAILessonContentError(
            status=None, message=f"Errore HTTP verso OpenAI: {exc}"
        ) from exc
    duration_ms = int((time.monotonic() - t0) * 1000)

    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text}
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
        log.error(
            "openai_lesson_content_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_lesson_content_unexpected_response", payload=data)
        raise OpenAILessonContentError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    # gpt-5.5 può esaurire `max_completion_tokens` con i reasoning tokens
    # prima di emettere il content → content vuoto. Diagnostica esplicita.
    if not content or not content.strip():
        usage_raw = data.get("usage") or {}
        completion_tokens = usage_raw.get("completion_tokens") or 0
        reasoning_tokens = (usage_raw.get("completion_tokens_details") or {}).get(
            "reasoning_tokens"
        ) or 0
        log.error(
            "openai_lesson_content_empty_content",
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            max_tokens=settings.openai_lesson_content_max_tokens,
        )
        if finish_reason == "length":
            raise OpenAILessonContentError(
                status=resp.status_code,
                message=(
                    f"OpenAI ha esaurito i token disponibili "
                    f"(reasoning={reasoning_tokens}, completion={completion_tokens}, "
                    f"cap={settings.openai_lesson_content_max_tokens}). "
                    f"Aumenta OPENAI_LESSON_CONTENT_MAX_TOKENS."
                ),
                payload=data,
            )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=(
                f"OpenAI ha restituito un contenuto vuoto "
                f"(finish_reason={finish_reason}, reasoning={reasoning_tokens}). "
                f"Riprova; se persiste aumenta OPENAI_LESSON_CONTENT_MAX_TOKENS."
            ),
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error("openai_lesson_content_json_decode_failed", content=content[:500])
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        lesson_content = LessonContentOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_lesson_content_schema_invalid", error=str(exc))
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage = build_usage_dict(
        model=settings.openai_lesson_content_model,
        reasoning_effort_setting=settings.openai_lesson_content_reasoning_effort,
        openai_usage=data.get("usage") or {},
        duration_ms=duration_ms,
    )
    log.info(
        "openai_lesson_content_response",
        lesson_id=lesson_content.lesson_id,
        word_count=lesson_content.estimated_word_count,
        sections=len(lesson_content.sections),
        assets=len(lesson_content.visual_assets),
        tokens=usage["total"],
        duration_ms=usage["duration_ms"],
        cost_usd=usage["cost_usd"],
    )
    return lesson_content, usage


# ---------------------------------------------------------------------------
# Verifica delle competenze (lezione `is_assessment`) — Fase 3
# ---------------------------------------------------------------------------


def _assessment_system_prompt(language_code: str) -> str:
    return f"""\
Sei un docente universitario esperto di valutazione dell'apprendimento.
Il tuo compito è redigere una VERIFICA DELLE COMPETENZE per un modulo di
un corso: un elenco di domande a scelta multipla e di domande aperte che
misurano le competenze e le conoscenze trattate nel modulo.

REQUISITI GENERALI
- Lingua: {language_code}.
- Le domande verificano la PADRONANZA degli argomenti del modulo nel suo
  insieme, non la memoria di una singola lezione.
- DIVIETO ASSOLUTO: non fare MAI riferimento a lezioni specifiche. Non
  scrivere "nella lezione X", "come visto nella lezione...", non citare
  titoli né codici di lezione. Ogni domanda deve essere autoconsistente,
  comprensibile da sola, formulata come verifica di competenza.
- Non citare codici interni (es. M1.L2, T1, S3).
- Copri in modo equilibrato TUTTI gli argomenti forniti in input.
- Varia il livello cognitivo (ricordare, comprendere, applicare, analizzare).

DOMANDE A SCELTA MULTIPLA
- Ogni domanda ha ESATTAMENTE 4 opzioni, con `option_id` "A", "B", "C", "D".
- ESATTAMENTE una opzione è corretta: indicala in `correct_option_id`.
- I distrattori (opzioni errate) devono essere plausibili e pertinenti,
  non palesemente assurdi.
- Evita "tutte le precedenti" / "nessuna delle precedenti".

DOMANDE APERTE
- `text`: la consegna della domanda.
- `expected_answer`: una traccia sintetica della risposta attesa — i
  punti chiave / i criteri che il docente userà per la correzione (non
  un tema svolto per esteso).

QUANTITÀ
- Produci ESATTAMENTE il numero di domande a scelta multipla e di domande
  aperte indicato nell'input.
- `question_id` univoci e brevi (es. "MC1", "MC2", ..., "OP1", "OP2").

Output: SOLO JSON valido conforme allo schema."""


LESSON_ASSESSMENT_JSON_SCHEMA: dict[str, Any] = {
    "name": "lesson_assessment",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "lesson_id": {"type": "string"},
            "lesson_title": {"type": "string"},
            "multiple_choice_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "string"},
                        "text": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "option_id": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["option_id", "text"],
                                "additionalProperties": False,
                            },
                        },
                        "correct_option_id": {"type": "string"},
                    },
                    "required": [
                        "question_id",
                        "text",
                        "options",
                        "correct_option_id",
                    ],
                    "additionalProperties": False,
                },
            },
            "open_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "string"},
                        "text": {"type": "string"},
                        "expected_answer": {"type": "string"},
                    },
                    "required": ["question_id", "text", "expected_answer"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "lesson_id",
            "lesson_title",
            "multiple_choice_questions",
            "open_questions",
        ],
        "additionalProperties": False,
    },
}


async def generate_lesson_assessment(
    *,
    user_prompt: str,
    language_code: str,
    is_regeneration: bool,
) -> tuple[LessonAssessmentOutput, dict[str, Any]]:
    """Chiama OpenAI per generare la verifica delle competenze di un modulo.

    Stessa configurazione modello/token della generazione contenuti
    (Fase 3 — usa `openai_lesson_content_*`). Ritorna `(assessment, usage)`.
    Solleva `OpenAILessonContentError` su errore HTTP/parsing/schema,
    `OpenAINotConfiguredError` se la API key è assente.
    """
    settings = get_settings()
    system_prompt = _assessment_system_prompt(language_code)
    if is_regeneration:
        system_prompt = system_prompt + REGENERATION_SUFFIX

    body = {
        "model": settings.openai_lesson_content_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": LESSON_ASSESSMENT_JSON_SCHEMA,
        },
        "max_completion_tokens": settings.openai_lesson_content_max_tokens,
    }
    apply_reasoning_effort(
        body,
        settings.openai_lesson_content_model,
        settings.openai_lesson_content_reasoning_effort,
    )
    log.info(
        "openai_lesson_assessment_request",
        chars=len(user_prompt),
        regeneration=is_regeneration,
        model=settings.openai_lesson_content_model,
    )
    t0 = time.monotonic()
    try:
        async with get_client(timeout=600.0) as client:
            resp = await client.post("/chat/completions", json=body)
    except OpenAINotConfiguredError:
        raise
    except httpx.HTTPError as exc:
        log.error("openai_lesson_assessment_http_error", error=str(exc))
        raise OpenAILessonContentError(
            status=None, message=f"Errore HTTP verso OpenAI: {exc}"
        ) from exc
    duration_ms = int((time.monotonic() - t0) * 1000)

    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except Exception:
            payload = {"text": resp.text}
        message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
        log.error(
            "openai_lesson_assessment_api_error",
            status=resp.status_code,
            message=message or "unknown",
        )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=message or f"OpenAI ha risposto con HTTP {resp.status_code}.",
            payload=payload,
        )

    data = resp.json()
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        log.error("openai_lesson_assessment_unexpected_response", payload=data)
        raise OpenAILessonContentError(
            status=resp.status_code,
            message="Risposta OpenAI in formato inatteso.",
            payload=data,
        ) from exc

    if not content or not content.strip():
        log.error(
            "openai_lesson_assessment_empty_content",
            finish_reason=finish_reason,
            max_tokens=settings.openai_lesson_content_max_tokens,
        )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=(
                f"OpenAI ha restituito un contenuto vuoto "
                f"(finish_reason={finish_reason}). Riprova; se persiste "
                f"aumenta OPENAI_LESSON_CONTENT_MAX_TOKENS."
            ),
            payload=data,
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.error(
            "openai_lesson_assessment_json_decode_failed",
            content=content[:500],
        )
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=f"OpenAI non ha restituito JSON valido: {exc}",
        ) from exc

    try:
        assessment = LessonAssessmentOutput.model_validate(parsed)
    except Exception as exc:
        log.error("openai_lesson_assessment_schema_invalid", error=str(exc))
        raise OpenAILessonContentError(
            status=resp.status_code,
            message=f"Output OpenAI non conforme allo schema: {exc}",
            payload=parsed,
        ) from exc

    usage = build_usage_dict(
        model=settings.openai_lesson_content_model,
        reasoning_effort_setting=settings.openai_lesson_content_reasoning_effort,
        openai_usage=data.get("usage") or {},
        duration_ms=duration_ms,
    )
    log.info(
        "openai_lesson_assessment_response",
        lesson_id=assessment.lesson_id,
        mc_questions=len(assessment.multiple_choice_questions),
        open_questions=len(assessment.open_questions),
        tokens=usage["total"],
        cost_usd=usage["cost_usd"],
    )
    return assessment, usage
