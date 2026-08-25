"""Registro accademico condiviso dai prompt di Fase 3 (dispense), Fase 4
(slide) e Fase 5 (discorso).

Perché esiste: il corpus delle lezioni generate mostrava due difetti
trasversali a tutte le discipline — asserzioni valutative non sostenute
("l'idea è elegante") e figure retoriche da divulgazione ("Sono strumenti
potenti. Proprio per questo pericolosi.", "Non è così.", "Non basta.").
Le istruzioni di stile dei tre system prompt erano divergenti e, in Fase
3, chiedevano esplicitamente "frasi brevissime" e "passaggi che
rovesciano la prospettiva". Questo modulo è l'unico punto in cui il
registro viene definito; i tre prompt lo interpolano come valore
(`{register_block}`), così le graffe del blocco non vengono rivalutate
dalle f-string.

Principi del testo:
- definizione POSITIVA del registro (manuale universitario), non un
  divieto generico;
- il criterio sulle valutazioni è l'assenza di giustificazione, non la
  parola: nessuna blacklist ("elegante" in matematica è legittimo se
  argomentato);
- coppie contrastive reali (da evitare → corretta), tratte dal corpus,
  in italiano come tutti i prompt del repo; per slide e discorso entra
  una versione ridotta (nucleo + 3 coppie) per non diluire prompt di
  trasformazione;
- nessun `$`, backslash o graffa doppia nel testo: il blocco entra anche
  nel prompt del parlato (che vieta LaTeX) e dentro f-string.

Funzione pura, nessun accesso a Settings o I/O.
"""

from __future__ import annotations

import textwrap
from typing import Literal

RegisterFlow = Literal["content", "slides", "speech"]

REGISTER_HEADER = "REGISTRO — MANUALE UNIVERSITARIO"
PAIR_LABEL_BAD = "DA EVITARE:"
PAIR_LABEL_GOOD = "CORRETTA:"

_FLOWS: tuple[str, ...] = ("content", "slides", "speech")

_CORE = f"""\
{REGISTER_HEADER} (regola primaria sulla prosa)

Tutto il testo leggibile dal lettore è scritto nel registro di un manuale
universitario: espositivo, denso, tecnico. Questa regola vale per ogni
campo di prosa e prevale su qualsiasi altra indicazione di ritmo o tono,
anche nella lezione introduttiva (cordialità e orientamento, senza frasi
a effetto).

Che cosa significa, in positivo:
- Il periodo tipico è articolato: una proposizione principale che
  afferma e subordinate che fissano condizioni, ipotesi, conseguenze o
  eccezioni. La frase breve è ammessa quando il contenuto è breve (un
  dato, un rimando, una conclusione già argomentata), non come effetto.
- Ogni paragrafo sviluppa un solo nodo concettuale e lo porta a
  compimento nell'ordine che il concetto richiede: che cosa è, sotto
  quali condizioni vale, che cosa ne segue, un caso che lo mostra.
- Il testo spiega, dimostra, distingue; non commenta, non persuade, non
  intrattiene. Il lettore è uno studente che deve capire e poter
  verificare.
- L'autorità del testo sta nell'argomentazione, nei dati e nelle fonti,
  non nel tono. Prendere posizione è lecito quando la posizione è
  argomentata nella stessa frase o in quella successiva.

REGOLA 1 — ASSERZIONI VALUTATIVE
Il criterio non è la parola ma la giustificazione: un giudizio di valore
può comparire SOLO se, nella stessa frase o in quella immediatamente
successiva, è presente ciò che lo sostiene: un criterio esplicito
(rispetto a che cosa, in quale senso), un dato o un caso concreto,
oppure un riferimento a una fonte fornita. Se non puoi sostenerlo,
ometti il giudizio e lascia il fatto.
Nessuna parola è vietata in sé: "elegante" riferito a una dimostrazione
è legittimo se dici in che cosa consiste l'economia di mezzi; "potente"
riferito a un metodo è legittimo se dici che cosa permette di fare che
altri metodi non permettono. Vale, ad esempio, per: elegante, potente,
fondamentale, cruciale, sorprendente, banale, pericoloso, notevole,
decisivo, e per i superlativi (potentissimo, straordinario,
rivoluzionario). Quando i documenti di riferimento coprono il tema,
criterio, dato o fonte vanno presi da lì.

REGOLA 2 — FIGURE RETORICHE DA DIVULGAZIONE
Non usare, in nessun campo:
- la frase-sentenza: una frase di poche parole posta dopo un periodo
  lungo per chiuderlo con enfasi (ad esempio "Punto.", "Anzi.", "Non
  basta.", "Tutto qui.", "Non è così.", e ogni variante dello stesso
  gesto);
- l'antitesi a effetto costruita come coppia di frasi ("X. Proprio per
  questo Y."; "Sono potenti. Per questo pericolosi.");
- il rovesciamento di prospettiva presentato come sorpresa, e la
  negazione che smentisce un'attesa creata dal testo stesso;
- la domanda retorica: una domanda è ammessa solo se il testo la
  risponde subito con un argomento;
- l'iperbole e il superlativo non misurabile;
- il riferimento alla magia, al trucco, al segreto, al miracolo per dire
  che qualcosa ha una spiegazione.
Se il contrasto è reale, esprimilo in un solo periodo articolato, con la
concessiva o l'avversativa dentro la frase e la ragione del contrasto
esplicitata.

REGOLA 3 — IL RITMO VIENE DAL CONTENUTO
La lunghezza di frasi e paragrafi varia perché varia il contenuto (un
dato è breve, una condizione di validità è lunga, una dimostrazione è
più lunga ancora), non per un'alternanza cercata. Non spezzare un
periodo per creare enfasi; non accorciare una spiegazione per dare
ritmo."""

_PAIRS_INTRO = """\
COPPIE CONTRASTIVE
Ogni coppia mostra una frase che viola il registro e la sua versione
corretta: il giudizio viene sostenuto oppure omesso, la frase-sentenza
viene ricomposta in un periodo che porta la ragione, senza aggiungere
fatti che il testo non aveva. Le coppie sono in italiano e prese da
discipline diverse: applica il CRITERIO, nella lingua {language_code},
alla materia della lezione. Non riutilizzarne i contenuti."""

# (da evitare, corretta) — frasi reali del corpus di produzione e loro
# ricomposizione. Dati, non prosa: i test le asseriscono una per una.
CONTRASTIVE_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "Se una funzione non è continua in un punto, non può essere differenziabile lì. Punto.",
        "Se una funzione non è continua in un punto, non può essere "
        "differenziabile in quel punto, perché la differenziabilità implica "
        "la continuità e quindi, per contrapposizione, la discontinuità "
        "esclude la differenziabilità. L'implicazione inversa non vale: il "
        "valore assoluto è continuo in zero ma non vi è derivabile.",
    ),
    (
        "Dal 1944 al 1945 la guerra entrò nella sua fase conclusiva, ma non "
        "per questo divenne meno distruttiva. Anzi.",
        "Che dal 1944 al 1945 la guerra fosse entrata nella fase conclusiva "
        "non ne ridusse la capacità distruttiva: la fase finale concentrò "
        "nel tempo le operazioni decisive su tutti i fronti, e l'intensità "
        "dei combattimenti crebbe invece di diminuire. (Il contrasto è "
        "espresso per struttura; se un documento riporta il dato, la fonte "
        "va in references; se nessuna fonte lo sostiene, limita "
        "l'affermazione.)",
    ),
    (
        'Domandare "avete capito?" alla classe intera produce quasi sempre '
        "un sì generico o un silenzio prudente. Non basta.",
        'Domandare "avete capito?" alla classe intera produce di norma un '
        "assenso generico o un silenzio prudente e non dà quindi al docente "
        "alcuna informazione sullo stato reale della comprensione; per "
        "questo la valutazione formativa ricorre a domande con risposta "
        "verificabile, per esempio un caso da risolvere o un errore da "
        "individuare, rivolte a singoli o a piccoli gruppi.",
    ),
    (
        "Questo errore spesso produce numeri plausibili, ed è proprio per questo pericoloso.",
        "Questo errore è difficile da individuare perché produce numeri "
        "plausibili: un controllo sull'ordine di grandezza non lo segnala, e "
        "lo si scopre solo confrontando il risultato con un caso di cui si "
        "conosce il valore esatto.",
    ),
    (
        "Ecco perché il nostro oggetto è affascinante.",
        "(frase omessa: il giudizio non porta contenuto e non è sostenuto; "
        "il paragrafo prosegue direttamente con la definizione "
        "dell'oggetto.)",
    ),
    (
        "Il controesempio è uno strumento didattico potentissimo.",
        "Il controesempio è uno strumento economico: una sola istanza basta "
        "a mostrare che un'implicazione non vale in generale, mentre per "
        "stabilire che vale servirebbe un argomento su tutti i casi.",
    ),
    (
        "Sono strumenti potenti. Proprio per questo pericolosi.",
        "Sono strumenti che estendono la capacità di intervento sui sistemi "
        "collegati e, nella stessa misura, la portata di un errore di "
        "configurazione, che si propaga a tutti i dispositivi raggiungibili "
        "in rete mentre in un sistema isolato resterebbe confinato.",
    ),
)

# Coppie (indici 0-based) che entrano nella variante ridotta per slide e
# discorso: le tre più trasversali (antitesi, omissione, "potente"
# argomentato).
REDUCED_PAIR_INDEXES: tuple[int, ...] = (3, 4, 6)

FLOW_ADDENDA: dict[str, str] = {
    "content": """\
APPLICAZIONE AL TESTO DELLA LEZIONE
- Il registro vale per introduction, sections[].content, summary,
  key_takeaways, examples[].content, le caption degli asset e
  statement, explanation e text dei passi di proof.
- La sintesi non riassume con giudizi: collega i risultati tra loro e
  alle ipotesi che li reggono.
- I key_takeaways sono enunciati compiuti e verificabili, non slogan.
- Le regole di STILE che seguono precisano il registro; non lo
  sostituiscono.""",
    "slides": """\
APPLICAZIONE ALLE SLIDE
- title: descrittivo, nomina il concetto, il risultato o la relazione
  trattata.
- body: 1-3 frasi articolate nel registro sopra; niente frasi-sentenza,
  niente domande retoriche, niente giudizi senza criterio.
- bullets: ciascuno è un sintagma tecnico o un enunciato compiuto, non
  uno slogan.
- Il testo di Fase 3 è la sola fonte di contenuto: non aggiungere
  definizioni, dati, esempi o attribuzioni che non vi compaiono; se
  contiene formulazioni che violano il registro, non riprodurle:
  riformula mantenendo il contenuto.""",
    "speech": """\
APPLICAZIONE AL PARLATO
Il discorso è orale ma non divulgativo. L'oralità sta in tre cose, tutte
ammesse: ridondanza controllata (ripetere il termine chiave, riformulare
una definizione con altre parole, riprendere quanto detto sulla slide
precedente), transizioni esplicite tra una slide e la successiva, esempi
svolti a voce passo per passo. Non sta nella retorica: niente domande
retoriche, niente frasi-sentenza, niente antitesi a effetto, niente
giudizi senza criterio; una domanda è ammessa solo se il parlato la
risponde subito con un argomento. Vale anche nella lezione introduttiva:
cordialità e orientamento, non entusiasmo di maniera.
DA EVITARE: «Ma questo basta? No. Non basta.»
CORRETTA: «Questo ci dice che la funzione è continua, ma non ancora che
sia derivabile: per la derivabilità serve una condizione in più, che
vediamo nella prossima slide.»
Il testo di Fase 3 e le slide sono le sole fonti di contenuto: se
contengono formulazioni che violano il registro, non leggerle:
ricomponile mantenendo il contenuto.""",
}

# Appesa ai tre REGENERATION_SUFFIX: senza, "mantieni lo stile del resto
# del corso" conserverebbe i tic delle versioni precedenti.
REGENERATION_REGISTER_NOTE = """\
- Il registro di riferimento è quello definito in REGISTRO, non quello
  della versione precedente: se la versione precedente contiene frasi a
  effetto, frasi-sentenza o giudizi non argomentati, ricomponili anche
  se il feedback del docente non li menziona; conserva contenuti, asset
  e id."""


def _render_pairs(indexes: tuple[int, ...]) -> str:
    """Numera e impagina le coppie scelte (numerazione progressiva 1..n)."""
    blocks: list[str] = []
    for n, idx in enumerate(indexes, start=1):
        bad, good = CONTRASTIVE_PAIRS[idx]
        bad_txt = textwrap.fill(f"{n}. {PAIR_LABEL_BAD} «{bad}»", width=72, subsequent_indent="   ")
        good_txt = textwrap.fill(
            f"{PAIR_LABEL_GOOD} «{good}»",
            width=72,
            initial_indent="   ",
            subsequent_indent="   ",
        )
        blocks.append(f"{bad_txt}\n{good_txt}")
    return "\n\n".join(blocks)


def academic_register_block(flow: RegisterFlow, language_code: str) -> str:
    """Blocco di registro da interpolare nel system prompt del `flow`.

    `content` riceve tutte le coppie; `slides` e `speech` la variante
    ridotta (`REDUCED_PAIR_INDEXES`). L'unica sostituzione è
    `{language_code}` nell'introduzione alle coppie (fatta con `replace`,
    non con `str.format`, per non interpretare le graffe del testo).
    """
    if flow not in _FLOWS:
        raise ValueError(f"flow di registro sconosciuto: {flow!r}")
    indexes = tuple(range(len(CONTRASTIVE_PAIRS))) if flow == "content" else REDUCED_PAIR_INDEXES
    intro = _PAIRS_INTRO.replace("{language_code}", language_code)
    return "\n\n".join([_CORE, intro, _render_pairs(indexes), FLOW_ADDENDA[flow]])
