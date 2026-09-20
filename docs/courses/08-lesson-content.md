# 08 — Lesson content (Fase 3) + Glossario corso

Implementazione di **Fase 3 — Testo della lezione** (§6 di
`prompt_generazione_corsi.md`) e del **Glossario corso** (§10.1) come
prerequisito condiviso.

## Obiettivo

Per ogni lezione approvata in Fase 2 (con `learning_objectives`,
`mandatory_topics`, `prerequisites`, `section_outline`), l'AI genera:

- **Testo Markdown completo** in stile capitolo di manuale: introduzione
  → sezioni (in ordine della `section_outline`) → sintesi →
  key_takeaways.
- **Asset visivi**: figure in quattro formati — diagrammi **Mermaid 11**,
  grafici **Vega-Lite**, grafi **Graphviz DOT** e figure matematiche
  calcolate (`function`, sympy + matplotlib) — oppure immagini caricate
  dall'utente (`format=image`; vedi
  [§ Asset visivi: figure + immagini caricate](#asset-visivi-figure-mermaid-vega-lite-dot-function--immagini-caricate)
  per il workflow e [17 — Figure accademiche](17-visual-figures.md) per il
  registro dei renderer, il tema unico e la numerazione «Figura N.»),
  formule LaTeX, tabelle markdown. Record legacy
  (`image_prompt|image_search_query|description`) ancora supportati in
  lettura come placeholder.
- **Esempi**, **references**, **coverage_check**.

> **Nota**: il campo `exercises_for_self_study` previsto nella spec
> originale è stato rimosso (vedi prompt §6 — "NON GENERARE ESERCIZI").
> Lo schema `content_raw` espone `examples` ma non `exercises`.

L'output passa prima dallo schema `LessonContentOutput`, che normalizza
`key_takeaways` e `references` (vedi
[§ Punti chiave e riferimenti](#punti-chiave-e-riferimenti-normalizzazione-in-scrittura-b5--d18)),
poi è validato (10 validazioni di §6.4, di cui 5 tolleranti o
derivate — vedi sotto) e materializzato come JSONB `content_raw` su
`course_lesson`. La UI rende live le figure (Mermaid, Vega-Lite, DOT,
`function`) con la cornice «Figura N.», KaTeX e tabelle.

## Glossario corso (§10.1)

Single-shot, riusato in tutte le fasi successive (`{{glossario}}` nel
user prompt di Fase 3, e in futuro Fasi 5 e 6). Generato **automaticamente
dal worker della Fase 3** al primo passaggio se `glossary_status='empty'`,
oppure manualmente via `POST /glossary/regenerate`.

State machine: `empty → processing → ready (+failed)`. Il valore
`approved` del CHECK di `glossary_status` è **morto**: nessun codice lo
setta (il gate del worker lo tollera solo per legacy).

Gate di `regenerate_glossary`: rank di `course.status` ≥
`architecture_approved` AND status ≠ `archived` (`published` ammesso) —
sostituisce la vecchia allow-set enumerata, che ometteva stati validi.

## Schema dati (migration 0015)

### `course_lesson` (10 colonne nuove)

| Colonna | Tipo | Note |
|---|---|---|
| `content_status` | VARCHAR(40) | CHECK ∈ (empty, pending, processing, ready, approved, failed) |
| `content_raw` | JSONB | output AI completo (verbatim §6.3) |
| `content_tokens` | JSONB | `{model, prompt, completion, total, …, cost_usd, assets, assets_cost_usd}` (sotto, «Costo degli asset») |
| `content_attempts` | SMALLINT | counter retry, azzerato a ogni richiesta dell'utente su lezione `failed`/`empty` |
| `content_error` | TEXT | messaggio errore |
| `content_generated_at` | TIMESTAMPTZ | |
| `content_approved_at` | TIMESTAMPTZ | |
| `content_regeneration_hint` | TEXT | hint utente §9.3 |
| `content_progress` | SMALLINT | 0..100 (CHECK) |
| `content_progress_phase` | VARCHAR(50) | preparing_prompt, calling_openai, validating_assets, materializing |

### `course` (5 colonne nuove + 1 stato)

| Colonna | Tipo | Note |
|---|---|---|
| `glossary_status` | VARCHAR(40) | CHECK come sopra |
| `glossary_raw` | JSONB | `{course_id, terms:[{term, translation, usage_note}]}` |
| `glossary_tokens` | JSONB | usage |
| `glossary_generated_at` | TIMESTAMPTZ | |
| `glossary_error` | TEXT | |

`course.status` CHECK aggiornato per includere `content_approved`
(`content_pending` e `content_ready` esistevano già).

## State machine (Fase 3)

```
empty → pending → processing ────────► ready ────────► approved
                          │              ▲
                          ▼              │
                       failed (riprova) ─┘
```

`course.status` per Fase 3 è derivato:
- almeno 1 lezione in `pending|processing|failed` → `content_pending`
- TUTTE in `approved` → `content_approved`
- TUTTE in `ready|approved` (almeno 1 ready) → `content_ready`

## Equazioni: enunciato + dimostrazione (`equations[]`)

Ogni asset in `equations[]` (schema `LessonContentEquation` in
`course_lesson_content.py`) non è più solo una formula nuda: porta una
classificazione e, dove sensato, l'enunciato e la dimostrazione a
passaggi.

| Campo | Tipo | Note |
|---|---|---|
| `equation_id` | str | ID stabile, referenziato nel testo via `[EQ:id]` |
| `latex` | str | formula/relazione principale, LaTeX **senza** delimitatori `$..$` |
| `label` | str | etichetta breve (≤200) |
| `explanation` | str | descrizione discorsiva (≤1200) |
| `kind` | str | tipo dell'asset (vedi sotto); default `formula` |
| `statement` | str | enunciato formale (markdown + math inline `$..$`); ≤3000 |
| `proof` | `list[ProofStep]` | dimostrazione a passaggi; `[]` quando non applicabile |

`kind` ∈ `definition`, `formula`, `identity`, `theorem`,
`proposition`, `lemma`, `corollary` (enum `strict` nel JSON schema). Il
modello lo usa per decidere se generare la dimostrazione:

- `theorem`/`proposition`/`lemma`/`corollary` → `statement` obbligatorio
  (ipotesi + tesi) e `proof` con i passaggi della dimostrazione;
- `definition` → `statement` con la definizione precisa, `proof: []`;
- `formula`/`identity` "nude" → `statement` può restare `""`, `proof: []`.

Un `ProofStep` ha due campi: `latex` (il contenuto matematico del
passo, LaTeX **senza** delimitatori — può usare ambienti completi e
bilanciati `aligned`/`cases` per il multilinea, oppure `""` se il passo
è puramente discorsivo) e `text` (la spiegazione del passo, markdown
con math inline `$..$`). Il prompt vieta esplicitamente di **inventare
dimostrazioni**: per definizioni, formule empiriche/postulate o identità
elementari `proof` resta vuota.

Sul frontend `EquationBlock` (in `MarkdownRenderer.tsx`) rende
l'enunciato (`ProseMarkdown` su `statement`) e, se presente, la
dimostrazione passo-passo; l'etichetta del tipo è i18n
`courses.theorem.kind.{kind}` (fallback `theorem`) e il blocco "prova"
usa `courses.theorem.proof`.

## Risoluzione degli asset `[KIND:id]` (case-insensitive)

I tag inline `[FIG:id]` / `[TAB:id]` / `[EQ:id]` / `[EX:id]` nel testo
vengono risolti contro gli asset dichiarati con **match
case-insensitive su entrambi i lati** (kind e id normalizzati a
minuscolo). L'AI genera spesso gli id con case non coerente (es. asset
`TAB_x` referenziato come `[TAB:tab_x]`); senza normalizzazione i lookup
fallirebbero e i warning di asset orfani/non referenziati sarebbero
falsi positivi.

- **Backend** (`course_lesson_content_service`): `_ASSET_REF_RE =
  r"\[(FIG|TAB|EQ|EX):([^\]\n]+)\]"` (stessa forma del PDF e di
  `figure_numbering.ASSET_REF_RE`: un tag a cavallo di riga non è un tag
  per nessun renderer), `_count_asset_refs` conta le occorrenze per
  `(kind.upper(), id.strip().lower())` (un `Counter`, non più un
  insieme); le validazioni soft sugli asset (9-11 di §6.4) confrontano
  ref e id entrambi in minuscolo. Il corpus dei ref comprende
  introduzione, sezioni, sintesi, `examples[].content` e
  `tables[].markdown`: un tag scritto in un esempio conta come uso e un id
  inesistente in una tabella è segnalato. Il PDF però non sostituisce i
  tag in quei due campi (li rende come markdown del blocco): un asset
  citato solo lì è accodato al corpo come mai citato.
- **Rimandi e ancore a render** (`asset_ref_normalize`, mirror
  `lib/assetRefNormalize.ts`): nella dispensa e nel PDF una citazione in
  linea `[KIND:id]` diventa il rimando testuale «Figura N» / «Tabella N» /
  «Equazione N» / «Esempio N» (teorema: «Lemma N», chiavi
  `courses.figures.*.ref`, senza punto) e il blocco è inserito UNA volta
  su riga propria dopo il blocco della prima citazione; una riga fatta del
  solo tag è l'ancora del blocco. Se la parola dell'etichetta precede già
  il tag sulla stessa riga, a meno di spazi e su parola intera, il rimando
  emette il solo numero («La figura [FIG:x]» → «La figura 1», mai «La
  figura Figura 1»); la parola è quella della chiave i18n del rimando,
  quindi la guardia vale in italiano e in inglese, ma il plurale («Le
  figure [FIG:x]») non corrisponde. I numeri sono per kind
  (`compute_asset_numbers`, prima citazione nell'ordine del documento,
  calcolati PRIMA della normalizzazione); gli asset mai citati di ogni
  kind sono accodati dopo la sintesi (FIG → TAB → EQ → EX). Punti chiave e
  riferimenti ricevono solo rimandi, mai blocchi. Nulla è persistito.
- **Frontend** (`MarkdownRenderer.tsx`): le `Map` degli asset hanno
  chiavi `asset_id.toLowerCase()` e `renderAssetBlock` cerca con
  `id.toLowerCase()`. Solo se il lookup fallisce mostra
  "Asset non trovato: `[KIND:id]`" (con l'id originale).

## Punti chiave e riferimenti: normalizzazione in scrittura (B5 / D18)

Questione B5 del piano della dispensa, decisione D18. `key_takeaways` e
`references` sono normalizzati **dallo schema**, al solo confine di
scrittura (`app/schemas/course_lesson_content.py`): due helper privati,
`_clean_key_takeaways` e `_clean_references`, e quattro `field_validator`
in mode "after" su `LessonContentOutput` (output AI, unico ingresso
`openai_lesson_content_service.generate_lesson_content`) e su
`LessonContentUpdateInput` (PATCH del docente).

- Trim, voci vuote scartate (vuoto = `str.strip()`: U+00A0 cade, U+200B
  no), dedup case-insensitive (`str.lower()`, come `_clean_argomenti` e
  `_clean_keywords`) con ordine e grafia della prima occorrenza. Nessun
  collasso degli spazi interni né della punteggiatura finale.
- References: chiave `(source, citation.lower())` sulla citation
  trimmata. La stessa citazione come `documento_caricato` e come
  `suggerimento_generale` resta doppia. Una `citation=""` è respinta
  dall'item (`string_too_short`, come prima); una di soli spazi supera
  l'item ed è scartata dalla lista.
- `min_length=KEY_TAKEAWAYS_MIN` (3) e `max_length=KEY_TAKEAWAYS_MAX`
  (12) contano l'elenco **grezzo**. La lista persistita può quindi
  scendere a 1-2 punti chiave senza rigenerare la lezione (in mode
  "before" `['A', 'a', ' A ']` darebbe `too_short` e una generazione
  intera per un difetto cosmetico). Il worker lo segnala con il warning
  `lesson_content_key_takeaways_below_min` (`key_takeaways`, `minimum`).
  `ValueError` solo se l'output AI resta vuoto dopo il cleanup (sole voci
  bianche), con retry recuperabile.
- PATCH: `None` = campo non toccato, `[]` = azzeramento ammesso (nessun
  errore). Il tetto è 12 come per l'output AI (domanda aperta 14: con 10
  una lezione da 11-12 punti chiave non era salvabile dall'editor).
- **Nessuna dedup in lettura, nessun backfill.** PDF
  (`render_lesson_html`, coda pre-resa con `_tail` sulla lista grezza),
  vista web (`LessonContentView`) ed editor mostrano `content_raw` com'è.
  Una lezione storica si normalizza alla rigenerazione o al primo
  salvataggio dall'editor, che invia SEMPRE le due liste (righe vuote
  scartate prima dell'invio). Un PATCH che non porta le liste le lascia
  com'erano. La divergenza fra editor e PDF è temporale, non spaziale: in
  ogni istante le superfici leggono lo stesso `content_raw`.
- Invariante: `content_raw` non viene mai ri-validato con
  `LessonContentOutput.model_validate` (fallirebbe con `too_short` su una
  lezione degradata). La duplicazione e la traduzione del corso
  (`course_duplication_service`) lavorano sul dict senza schema: i
  duplicati storici passano nelle copie così come sono.

## Riferimenti agli obiettivi: codici nel prompt + risoluzione tollerante

`sections[].objectives_addressed` e `coverage_check.objectives_covered[]`
erano l'unico punto della pipeline in cui l'AI doveva riferirsi a un dato
di Fase 2 tramite il suo TESTO invece che tramite un ID (i temi hanno
`topic_id`, le slide `slide_id`, le sezioni `section_id`). Il confronto
era esatto a meno di minuscole e spazi: un apostrofo tipografico o un
accento composto diversamente bastavano a far scartare l'intera dispensa
con `lesson_content_unknown_objective`, e la regola di LINGUA — che
ordina di scrivere ogni campo nella lingua del corso — spingeva
attivamente il modello a tradurre proprio la stringa che doveva copiare.

Doppia rete:

1. **Codici nel prompt + `enum` nello schema.** Gli obiettivi sono
   numerati `[O1] … [On]` nel blocco `## Lezione da generare`
   (`_format_learning_objectives`, simmetrico a `[T1]` dei temi) e
   `build_lesson_content_json_schema` inietta quei codici come `enum` sui
   due campi nello schema strict della singola chiamata. Il modello non
   PUÒ più emettere un valore inesistente. Solo gli obiettivi entrano
   nell'enum: i `topic_id` sono stringhe libere di Fase 2 e renderebbero
   lo schema diverso quasi a ogni lezione (la latenza di preprocessing
   dello schema si paga una volta per variante). Lista vuota ⇒ nessun
   `enum`: `"enum": []` non è uno schema valido.
2. **Risoluzione tollerante in materializzazione**
   (`lesson_coverage_resolver`), cascata deterministica, primo esito
   vince: codice (`O3`, `[O3]`, `o3`; indice fuori range ⇒ irrisolto) →
   uguaglianza storica (minuscole + spazi) → uguaglianza normalizzata
   (NFKD, accenti, apostrofi, punteggiatura) → contenimento (≥ 30
   caratteri e candidato **unico**). Niente `difflib`: fra due obiettivi
   fratelli «segnale periodico» / «segnale non periodico», la parafrasi
   «segnale aperiodico» ha ratio 0.947 sul primo e 0.783 sul secondo,
   quindi qualunque soglia sceglierebbe l'opposto semantico. Un falso
   negativo costa un warning; un falso positivo corrompe la contabilità
   in silenzio.

**In `content_raw` si persiste sempre il testo canonico di Fase 2**, mai
il codice: `O1` è una maniglia posizionale valida per una sola chiamata
(il docente può riordinare gli obiettivi dalla PATCH di Fase 2) e
`coverage_check.objectives_covered[].objective` viene tradotto sulla
duplicazione, dove un `"O1"` verrebbe translitterato o scartato. Forma di
`content_raw` invariata: nessuna migrazione, nessuna rigenerazione.

## Validazione asset (LaTeX/Mermaid) + auto-fix AI a generazione

Prima di materializzare la lezione, il worker valida e ripara gli asset
"fragili" (`asset_validation_service.validate_and_fix_content_assets`,
fase di progress `validating_assets` a 88%), poi revisiona le figure
contro il testo e localizza (sotto, «Pipeline di validazione di Fase 3»). Un asset è "fragile" se può
essere sintatticamente invalido e finire rotto nell'output (PDF / frame
video / preview FE):

- **Formule LaTeX** — `equations[].latex`, ogni `proof[].latex`, e il
  math inline `$..$` / `$$..$$` nei campi testo (introduction, summary,
  sezioni, esempi, `statement` e `proof[].text` delle equazioni).
  Validate con **`latex2mathml`** (motore dell'export PDF/video, sync e
  offline — gate duro) **E** con **KaTeX** (motore del preview FE): una
  formula è valida solo se passa entrambi.
- **Figure** — `visual_assets[].format` in `figure_render_service.
  RENDERABLE_FORMATS` (`mermaid`, `vegalite`, `dot`, `function`), ciascuna
  validata dal renderer del registro (vedi
  [17 — Figure accademiche](17-visual-figures.md)):
  - `mermaid`: gate statico D8, soglie editoriali sul sorgente
    (`figure_compute.graph_rules`, sotto) + parse con **Mermaid 11.x**, pin unico
    `settings.mermaid_cdn_version` (default `11.17.2`), lo stesso del
    pre-render PDF/video (`mermaid_prerender`) e del lock npm del frontend,
    con la stessa inizializzazione `figure_theme.mermaid_initialize_js`
    (`htmlLabels: false` al livello top, tema D3): un diagramma "verde"
    nell'editor lo è anche nell'output. I tipi ammessi sono i 15 di D8
    (`figure_theme.MERMAID_ALLOWED_TYPES`); `journey`, `gitGraph`,
    `kanban`, `packet-beta` e `architecture-beta` sono esclusi (`journey`
    emette `<foreignObject>`, non renderizzabile da WeasyPrint);
  - `vegalite`: schema JSON di Vega-Lite v6, regole D5 (dati inline,
    niente `data.url`/interattività/`config`, `clip` e `scale.domain`) ed
    euristica del criterio 10 (funzioni matematiche → `function`), poi
    render offline con vl-convert;
  - `dot`: gate statico (header, attributi che leggono file, tetto di
    risorsa di 600 archi), soglie editoriali sul sorgente e render con il
    binario `dot`; gli incroci fra archi misurati sull'SVG reso oltre
    `MAX_EDGE_CROSSINGS` sono un warning e una voce del report, mai un
    rifiuto;
  - `function`: `FunctionFigureSpec` (struttura Pydantic + controlli
    semantici) e render con sympy/matplotlib.

  **Soglie editoriali dei grafi (D13, D14).** Una figura Mermaid o DOT
  valida ma illeggibile è rifiutata con un messaggio che comincia per
  `graph_too_dense:` e dice che cosa ridurre (`nodi 42 > 30`, `archi 60 >
  45`, `caratteri dell'etichetta 80 > 64`, `caratteri del titolo 130 >
  110`, `righe 130 > 120`, `caratteri del sorgente 3500 > 3000` solo
  Mermaid), seguito una volta dalla clausola «qui semplificare è la correzione
  richiesta: mantieni tipo e significato», perché il system prompt del fix
  vieta di togliere contenuti. Il messaggio arriva al fix AI come ogni altro
  errore; nel PATCH manuale è il `type` `graph_too_dense` del 422 per
  asset. Le soglie sono provvisorie (calibrate sui 57 modelli degli
  editor) e tarate perché nessuna figura normale le superi: un errore qui
  costa, a fix esauriti, la rigenerazione della lezione. Per questo, dove
  Mermaid manda a capo da sé (forme e collegamenti del flowchart, state,
  mindmap, timeline, relazioni e note di class ed ER, blocchi della
  sequence), la soglia dell'etichetta vale per la parola più lunga e non
  per la riga: un evento di timeline descrittivo è una figura normale. Gli incroci fra
  archi non rifiutano mai una figura: un percettrone multistrato 3-4-2 ne
  ha 16 per costruzione e il fix non potrebbe toglierli; oltre
  `MAX_EDGE_CROSSINGS` diventano la voce `graph_too_dense: incroci fra
  archi …` fra i difetti della figura e il warning
  `figure_geometry_defects` (DOT già alla validazione profonda, Mermaid
  all'export). Il tetto di risorsa sul sorgente
  (`VISUAL_ASSET_CONTENT_MAX_CHARS`, 12.000 caratteri) vale sugli asset
  generati e, nel PATCH, sui soli asset cambiati: una lezione storica con
  un Mermaid più lungo resta modificabile nel testo. Dettagli e regole di
  conteggio per tipo in
  [17 — Figure accademiche](17-visual-figures.md), sezione 12.

  Il prompt di Fase 3 (PROMPT 3 in `docs/PROMPTS.md`, blocco «FORMATI
  DELLE FIGURE») descrive sempre i quattro formati; lo schema strict
  offre al modello solo quelli abilitati e disponibili sul server
  (`available_formats()`). Un formato non disponibile produce un check
  non riparabile: nessun fix AI, rigenerazione immediata.

La validazione JS (KaTeX + Mermaid) gira in una pagina Playwright
headless con un loop dedicato. Flusso (`_validate_and_fix`):

1. valida gli originali; se è tutto valido **non tocca nulla** (gli
   asset validi restano byte-identici);
2. step deterministico (no AI) sui soli invalidi: rimozione di
   caratteri di controllo C0/C1 e combining marks dalle sorgenti LaTeX
   (spesso il garbage del modello si risolve qui senza spendere token);
3. **fix AI iterativo** sui soli asset ancora invalidi, fino a
   `settings.asset_fix_max_attempts` (default `3`):
   `openai_asset_fix_service.fix_asset` (`openai_asset_fix_model`,
   default `gpt-4o-mini`, 10 varianti di system prompt LaTeX/Mermaid/
   Vega-Lite/DOT/function × IT/EN) chiede al modello di correggere
   **solo la sintassi** preservando il significato (LaTeX senza
   delimitatori / Mermaid grezzo per la 11.x: solo i tipi ammessi di D8,
   label in testo semplice, niente `%%{init}%%` / spec JSON o sorgente
   DOT conformi alle regole del renderer). L'output
   viene sanitizzato (niente code-fence/delimitatori reintrodotti) e
   scartato se reintroduce un placeholder asset (`[EQ:..]` ecc.).

Se un asset resta invalido dopo i tentativi → `AssetFixUnresolvedError`
(**recuperabile**): il worker la mappa su **auto-retry** e rigenera
l'intera lezione, così nessun asset rotto raggiunge `ready`. Se le
librerie CDN non sono raggiungibili, il LaTeX resta gated da
`latex2mathml` (offline) mentre KaTeX/Mermaid degradano a pass-through
(warning), per non bloccare la generazione quando la rete è giù. Lo
stesso servizio valida anche gli asset di Fase 4
(`validate_and_fix_slides_assets`: `new_assets` Mermaid + math inline
nelle slide).

### Pipeline di validazione di Fase 3: fix → revisione → localizzazione

`validate_and_fix_content_assets(output, *, language_code)` ritorna
`(output, assets_usage)` ed esegue tre passi nell'ordine:

1. **fix** degli asset invalidi (sopra);
2. **revisione figura ↔ testo** (D15,
   `openai_figure_review_service.review_figure`, PROMPT 17 in
   `docs/PROMPTS.md`; kill-switch `FIGURE_REVIEW_ENABLED`, al più
   `FIGURE_REVIEW_MAX_ATTEMPTS` chiamate per figura, default 2; saltata
   prima di ogni resa se manca `OPENAI_API_KEY`). Ogni figura valida
   (`visual_assets[]` con formato renderizzabile) è inviata al modello con
   didascalia e testo alternativo, il **testo integrale della prima
   sezione che la cita** (`FIG_REF_RE` su introduzione → sezioni →
   sintesi, id confrontato con `.strip().lower()`; tetto di sicurezza
   24.000 caratteri) oppure, se non è citata, il corpo della lezione
   troncato a 12.000 caratteri, e la **misura di WP5** dell'originale:
   nodi e archi dal sorgente (`graph_rules`), incroci e difetti di lettura
   dalla figura resa, corpo minimo del testo nella dispensa A4 di default
   (170 × 242 mm, banda 8-11 pt). Gli originali sono resi una volta con
   `render_figure_map` (DOT, Vega-Lite e `function` sono hit della cache
   della validazione profonda; i Mermaid costano un Chromium per lezione,
   0,8-1,8 s misurati il 17 settembre 2026; rese **speculative**,
   `cache_failures=False`: un loro guasto non mette in cache negativa le
   figure dell'export). Le chiamate di un giro partono in parallelo, ma in
   volo non ne sta mai più di `FIGURE_REVIEW_MAX_PARALLEL` (default 4,
   semaforo per processo: il tetto vale anche fra lezioni concorrenti). Il
   verdetto predefinito è **`coerente`**: nessuna riscrittura. Con **`correggi`** il sorgente proposto passa da
   `_sanitize` e da controlli deterministici (`missing_source`, sorgente
   identico = nessuna riscrittura, `placeholder`, `type_changed` per un
   tipo Mermaid diverso, `density_increased` se nodi o archi aumentano,
   `nodes_removed` se manca un nodo dell'originale, per id o, nei tipi
   senza id, per numero, `nodes_isolated` se un nodo che aveva archi
   resta senza; togliere archi è ammesso),
   poi dalla stessa `_validate_slots` del fix e, per Mermaid e DOT, dalla
   misura: originale e riscrittura sono letti dalla stessa
   `render_figure_map` (l'originale è un hit della cache, la riscrittura
   DOT è in cache dalla validazione profonda, quella Mermaid è resa e
   misurata nella pagina del pre-render: circa 2 s in più fra parse e
   misura). Regola di accettazione (`review_acceptance`): la riscrittura
   deve essere resa (`measure_unavailable`, per esempio Chromium assente)
   e misurata (`measure_skipped`, anche quando è saltata la misura
   dell'originale: mai un'accettazione senza misura); con l'originale
   misurato gli incroci non aumentano (`crossings: n > m`) e nessun codice
   di difetto compare più volte che nell'originale (`new_defects: …`);
   con l'originale non misurato la riscrittura deve essere senza difetti.
   Vega-Lite e `function` non hanno archi né misura geometrica: al loro
   posto vale la **conservazione dei dati** (`_DATA_GUARDS`), che entra fra
   i controlli deterministici: per Vega-Lite le righe inline
   (`data.values`, `datasets`, a ogni livello della composizione) non
   diminuiscono (`rows_removed`), i blocchi `data.sequence` restano
   (`sequence_removed`) e i campi citati dall'encoding ci sono ancora
   (`fields_removed`); per `function` restano tutte le espressioni
   (`expressions_changed`, confronto senza spazi) e il dominio non si
   restringe (`domain_reduced`). Il confronto è sui conteggi e sui campi,
   non sull'identità delle righe: correggere un valore sbagliato resta una
   riscrittura legittima, cancellare o sostituire i dati no. Una
   riscrittura respinta lascia l'originale
   **byte-identico** (nessuna scrittura in `content`, voce di cache
   dell'originale intatta), logga `figure_review_rejected` (`reason`,
   `crossings_before`, `crossings_after`) e il motivo torna al modello nel
   tentativo successivo; le riscritture accettate si applicano tutte
   insieme a fine revisione. Ogni chiamata logga `figure_review_verdict`
   (`asset_id`, `verdict`, `accepted`, `outcome`, `reason`, `cost_usd`).
   La revisione **non fa mai fallire la lezione**: ogni errore di una
   chiamata (HTTP, corpo 200 non JSON, schema, eccezione imprevista del
   client) è un tentativo perso di quella figura
   (`figure_review_call_failed`) e non tocca le chiamate sorelle del giro,
   che finiscono e restano contate; un guasto imprevisto fuori dalle
   chiamate diventa `figure_review_failed` con gli originali intatti. Una
   chiamata comunque pagata (200 con JSON troncato da
   `OPENAI_FIGURE_REVIEW_MAX_TOKENS` o schema fuori contratto) porta il suo
   usage nell'eccezione (`OpenAIError.usage`) e resta contabilizzata, con
   `cost_usd` anche nel log del tentativo perso;
3. **localizzazione** dei campi rimasti in un'altra lingua, con
   rivalidazione non fatale dei kind strutturali.

Dopo i tre passi il worker rilegge `content_status` (secondo
cancel-check, `lesson_content_cancelled_post_assets`): un annullamento
arrivato mentre gli asset erano in validazione scarta il risultato, come
dopo la chiamata di Fase 3, e non viene sovrascritto da `ready`. Una
lezione annullata non ha una riga in `content_tokens`, quindi il costo
già speso non è contabilizzato: resta nel log dell'annullamento
(`cost_usd`, `assets_calls`, `assets_cost_usd`). Lo stesso vale per un
fix che non si risolve: `AssetFixUnresolvedError` porta con sé le
chiamate già pagate e il worker le logga
(`lesson_content_assets_cost_discarded`) prima di far rigenerare la
lezione.

**Costo degli asset in `content_tokens` (D16).** Fix, revisione e
localizzazione producono l'usage di `openai_pricing.build_usage_dict`
(`model`, `prompt`, `completion`, `total`, `reasoning_effort`,
`reasoning_tokens`, `cached_tokens`, `duration_ms`, `cost_usd`). Ogni
chiamata è una voce di `assets_usage` con `phase` (`fix`, `review`,
`localize`) e `asset_id` (l'id dello slot: `asset:<id>`, `eq:<id>`,
`sec0.content#1`…; `None` per la localizzazione, che copre più campi e
porta `fields`). Il worker lo fonde con
`asset_validation_service.merge_assets_usage` prima di
`materialize_lesson_content`. Esempio reso da `merge_assets_usage` con i
valori del test del worker (`tests/test_figure_review.py`: Fase 3 con 1.000
+ 2.000 token su `gpt-5.5`, un fix e una revisione con 3.000 + 300 token su
`gpt-4o-mini`; campi `reasoning_*`, `cached_tokens` e `duration_ms` delle
voci omessi qui):

```json
{
  "model": "gpt-5.5", "prompt": 1000, "completion": 2000, "total": 3000,
  "reasoning_effort": "high", "cost_usd": 0.045,
  "assets": [
    {"phase": "fix", "asset_id": "asset:g9", "model": "gpt-4o-mini",
     "prompt": 3000, "completion": 300, "total": 3300, "cost_usd": 0.00063},
    {"phase": "review", "asset_id": "asset:g9", "model": "gpt-4o-mini",
     "prompt": 3000, "completion": 300, "total": 3300, "cost_usd": 0.00063}
  ],
  "assets_cost_usd": 0.00126
}
```

`cost_usd` resta quello della chiamata principale. La dashboard admin
(`admin_metrics_service`) somma nella fase `content` `cost_usd` e
`assets_cost_usd` di ogni riga (una chiave assente vale 0), anche nelle
finestre a 7 e 30 giorni. Nessuna colonna
nuova e nessuna migrazione (`content_tokens` è JSONB). Limiti: le
chiamate di una generazione poi fallita (lezione rimessa in coda) non
sono registrate, come la chiamata principale; una risposta pagata ma
illeggibile (JSON o schema non validi) non ha usage; in Fase 4 lo stesso
usage è solo loggato (`slides_assets_usage`), `slides_tokens` non lo
raccoglie.

## Architettura backend

### Servizi OpenAI

- `openai_glossary_service.py` — wrapper § 10.1 con system prompt
  minimal (10-30 termini), JSON schema strict, gestione errori
  `OpenAIGlossaryError` + diagnostica empty-content per gpt-5.5
  reasoning tokens.
- `openai_lesson_content_service.py` — wrapper §6 con system prompt
  verbatim (**"v4"**, vedi sotto), addendum §9.3 per rigenerazione,
  JSON schema completo §6.4, `OpenAILessonContentError`. Timeout 600s
  (lezione completa 60-120s di reasoning). Il prompt interpola
  `ruolo_docente`, `stile_insegnamento` e `livello_eqf` nel tono e
  fornisce due campioni di prosa umana (RITMO + REGISTRO DIDATTICO) da
  imitare. Espone anche `_assessment_system_prompt()` +
  `generate_lesson_assessment()` per le lezioni `is_assessment`
  (verifica delle competenze, schema MC/aperte distinto). Vedi
  [PROMPTS.md — PROMPT 3](../PROMPTS.md).
- `openai_asset_fix_service.py`, `openai_figure_review_service.py`,
  `openai_asset_localize_service.py` — i tre servizi ausiliari degli asset
  chiamati da `asset_validation_service` (fix → revisione →
  localizzazione, sezione «Pipeline di validazione di Fase 3»): PROMPT 12,
  PROMPT 17 e il prompt di localizzazione; usage di `build_usage_dict`
  raccolto in `content_tokens.assets`.

#### Prompt "v4" — due fasi interne in un solo call

Il system prompt impone al modello un processo di scrittura in **due
fasi interne**, eseguite in un **unico call** OpenAI:

- **Fase 1 (interna)**: prima stesura completa, focalizzata solo su
  correttezza dei contenuti e copertura di obiettivi/temi, senza
  preoccuparsi dello stile.
- **Fase 2 (interna)**: riscrittura integrale della stesura applicando
  con rigore le regole di STILE (ritmo variabile delle frasi, niente
  connettivi standard né formule stereotipate, paragrafi di lunghezza
  irregolare) e avvicinandosi ai campioni di prosa di riferimento.

Nell'output JSON il modello inserisce **solo il risultato della Fase
2**; la prima stesura non compare mai. Contenuti, formule, tabelle e
tag asset (`[FIG:]`, `[EQ:]`, `[TAB:]`, `[EX:]`) restano invariati tra
le due fasi — non è un doppio call, è un'istruzione di processo dentro
lo stesso prompt.

#### Posizione dei tag (D17)

Il blocco `POSIZIONE DEI TAG — REGOLA RIGIDA` (al posto del vecchio
paragrafo «Per ogni asset») nomina i quattro tag con il campo id del
proprio array (`[FIG:asset_id]`, `[TAB:table_id]`, `[EQ:equation_id]`,
`[EX:example_id]`), quindi vale per figure, tabelle, equazioni ed
esempi, e chiede:

- UN tag per asset, da solo su una riga propria fra due righe vuote,
  dopo il paragrafo che introduce l'asset (il renderer lo sostituisce con
  l'asset numerato);
- nel testo il richiamo a parole («come mostra la figura», «nella tabella
  seguente»), senza ripetere il tag e senza «Figura»/«Tabella»/
  «Equazione»/«Esempio» davanti al tag;
- mai un tag in codice, formule, `caption`, `key_takeaways`,
  `references`, `examples[].content` o `tables[].markdown` (negli ultimi
  due il PDF non sostituisce i tag).

È la forma che il renderer tratta come ancora del blocco senza toccare la
frase (test `test_the_layout_taught_by_the_phase3_prompt_leaves_the_prose_alone`).
La vecchia formula «referenziato almeno una volta» ammetteva le
ripetizioni, che `_count_asset_refs` ora segnala
(`lesson_content_duplicate_asset_refs`); le citazioni in linea dei
contenuti storici restano gestite come rimandi «Figura N». Il blocco
DIVIETI vieta la numerazione a mano e rimanda alla regola (la regola
sulle didascalie sta solo lì); `REGENERATION_SUFFIX` chiede di riscrivere
secondo la regola i tag ripetuti o dentro le frasi. Budget misurato:
27.923 caratteri nella variante più lunga (+373) e 28.819 con il
suffisso di rigenerazione (+445), entrambi sotto `MAX_SYSTEM_P3 = 28_900`
invariato (`tests/test_prompt_register.py`, che controlla anche la
variante di rigenerazione); la regola non entra in Fase 4, il cui
margine è di 300 caratteri. Testo verbatim in
[PROMPTS.md — PROMPT 3](../PROMPTS.md), verificato da
`scripts/check_prompts_md.py`.

Entrambi inseriscono `reasoning_effort` nel body via
`apply_reasoning_effort()` (`openai_client.py`) — solo per modelli
reasoning, omesso su `gpt-4o`/`gpt-4o-mini`. Default
`OPENAI_LESSON_CONTENT_REASONING_EFFORT=high` (task più complesso del
pipeline). Lever per accelerare: abbassare a `medium` riduce il tempo
per lezione del ~40%, qualità leggermente inferiore. Vedi
[04 — Configuration](../04-configuration.md#reasoning-effort-gpt-5x--o1--o3--o4).

### Servizi orchestrazione

- `course_glossary_service.py` — sync, single-shot:
  - `regenerate_glossary` (endpoint pubblico)
  - `ensure_glossary_ready` (helper chiamato dal worker Fase 3)
  - `format_glossary_for_prompt` (serializza in formato bullet per i
    prompt downstream)
- `course_lesson_content_service.py` — orchestrazione Fase 3:
  - `request_lesson_generation` — gate **per-unità** (la vecchia allow-set
    `VALID_COURSE_GENERATE_FROM_STATUSES` su `course.status` è stata
    eliminata): corso non terminale (`ensure_course_not_terminal` →
    `409 course_terminal_status`) + struttura del SOLO modulo della lezione
    `approved` + struttura della lezione presente (`section_outline`;
    le lezioni `is_assessment` sono esenti dal check di esistenza) —
    `ensure_lesson_structure_ready` → `409 lessons_structure_not_approved` |
    `409 lesson_structure_missing`.
  - `request_all_lessons_generation` / `request_missing_lessons_generation` —
    filtrano **in silenzio** le lezioni non eleggibili
    (`lesson_structure_is_ready`); regressione esplicita
    `course.status='content_pending'`.
  - `materialize_lesson_content` — applica le **10 validazioni §6.4**:
    0. (schema, prima della materializzazione) `key_takeaways` e
       `references` già normalizzati da `LessonContentOutput`: trim,
       vuoti scartati, dedup case-insensitive con ordine conservato,
       references a parità di `source`; `min_length=3` conta il grezzo,
       la lista persistita può degradare a 1-2 voci
    1. `lesson_id` ↔ `lesson_code` match (hard)
    2. `section_id` univoci (hard)
    3. `asset_id` univoci per tipo (visual_assets, tables, equations, examples) (hard)
    4. Cross-field: ogni `objectives_addressed` è **riconciliato** sul testo
       canonico di Fase 2 (`lesson_coverage_resolver`); ciò che resta
       irrisolto viene scartato → warning + audit
       `course.lesson.content.coverage_refs_dropped`
    5. Cross-field: idem per `topics_addressed` sul `topic_id` canonico
    6. Coverage completa: unione su sections copre TUTTI obiettivi/topic —
       **unica validazione di contabilità rimasta bloccante**, con gli
       elementi scoperti nel messaggio
    7. `coverage_check.objectives_covered` **derivato** dalle sections
    8. `coverage_check.topics_covered` **derivato** dalle sections
    9. Asset orfani (referenziati ma non definiti) → warning soft
       `lesson_content_dangling_asset_refs`
    10. Asset non referenziati nel testo → warning soft
       `lesson_content_unused_assets`
    11. Tag ripetuti (stesso `(kind, id)` più di una volta nel corpus) →
       warning soft `lesson_content_duplicate_asset_refs` con
       `duplicated={"FIG:a": 3}`; la materializzazione prosegue (il PDF
       tiene una sola ancora e trasforma le altre citazioni in rimandi)
  - `approve_lesson_content` / `approve_all_lessons_content` —
    l'approve-all è **tollerante** (come quelli di slide/discorso): ignora
    le lezioni `empty` (non ancora generate — normali nel flusso
    per-unità), `409 not_all_lessons_ready` solo con lezioni
    `pending/processing/failed`, `409 no_content_to_approve` se nessuna
    lezione ha una dispensa generata, no-op idempotente se già tutte
    `approved`.
  - `_recompute_course_content_status`
- `course_lesson_content_crud.py` — edit manuale di `content_raw`
  (richiede status `ready`/`approved`). Validazioni allentate (solo
  unicità ID, no coverage hard). Il PATCH normalizza `key_takeaways` e
  `references` che porta (schema `LessonContentUpdateInput`); i campi
  assenti non vengono riscritti; il conteggio di audit `fields.*` è
  post-normalizzazione.

### Worker parallelo

`course_lesson_content_worker.py` — speculare al worker Fase 2 ma
scoped a livello LEZIONE:
- `_inflight: set[UUID]` su `lesson_id` (claim atomico in `_tick`,
  vedi [02 — Architecture](../02-architecture.md#pattern-batch-parallelo-lesson_structure-lesson_content-lesson_slides-lesson_speech-lesson_pdf-lesson_slides_pdf-lesson_speech_pdf-lesson_video-lesson_avatar_video))
- `_semaphore = asyncio.Semaphore(course_lesson_content_max_concurrency)`
  (default `3`, output 5x più grande di Fase 2)
- Polling: `course_lesson_content_poll_interval_seconds` (default `4`)
- **Pre-check struttura** (difesa in profondità del gate API): prima di
  passare a `processing`, se `lesson_structure_is_ready` è falso (race con
  una rigenerazione della struttura: task accodato fuori contesto) →
  failure immediata **non recuperabile** con
  `phase="precheck_structure"` + audit `course.lesson.content.failed`.
- Glossary auto-trigger: al primo task del corso, se
  `glossary_status not in ('ready','approved')`, chiama sync
  `course_glossary_service.ensure_glossary_ready` (~10-20s).
- Ticker progress: ease-out 15→85% in ~90s (lezione più lunga di
  Fase 2 → ticker più lento).
- **Costo degli asset** (D16): dopo `validate_and_fix_content_assets`
  l'usage della chiamata di Fase 3 riceve `assets` e `assets_cost_usd`
  (`merge_assets_usage`), prima del filtro delle fonti riservate e di
  `materialize_lesson_content`; `cost_usd` resta quello della chiamata
  principale.
- **Punti chiave degradati** (D18): dopo la materializzazione,
  `_warn_on_degraded_key_takeaways` emette
  `lesson_content_key_takeaways_below_min` se la dedup dello schema ha
  lasciato meno di `KEY_TAKEAWAYS_MIN` voci. La lezione resta `ready`:
  nessun retry.
- **Auto-retry trasparente** — `_apply_failure(lesson, *,
  recoverable, auto_retry_max)` è invocato in tutti i 4 percorsi di
  errore (glossary_gate, openai_call, materialize). Se `recoverable`
  e `content_attempts < course_lesson_content_auto_retry_max` (default
  5), riporta `content_status='pending'` (il prossimo tick ritenta) e
  azzera `content_error` — l'utente non vede mai il messaggio. Solo
  dopo `auto_retry_max` esauriti `→ failed`. Errori non recuperabili
  (`OpenAINotConfiguredError` — config issue, non si risolverà
  ritentando) vanno a `failed` subito.
  Il budget è **per-richiesta**: `request_lesson_generation`,
  `request_all_lessons_generation` e `request_missing_lessons_generation`
  azzerano `content_attempts` sulle lezioni `failed`/`empty` (valore
  precedente nei metadata dell'audit). Il vincolo sullo stato serve
  perché generate-all riporta a `pending` anche lezioni `processing`:
  senza, due clic di fila azzererebbero il contatore all'infinito e il
  tetto dei retry non morderebbe mai. `cancel-all` non azzera nulla.

### API endpoints (6 nuovi)

| Metodo | Path | Permesso | Effetto |
|---|---|---|---|
| `POST` | `/lessons/{lid}/content/generate` | `course:generate` | Set lezione `pending`. 202. |
| `POST` | `/lessons-content/generate-all` | `course:generate` | Set le lezioni eleggibili `pending` (le altre saltate in silenzio). 202. |
| `POST` | `/lessons/{lid}/content/approve` | `course:generate` | Approve lezione singola (richiede `ready`). |
| `POST` | `/lessons-content/approve-all` | `course:generate` | Approve batch tollerante (approva le `ready`, ignora le `empty`). |
| `PATCH` | `/lessons/{lid}/content` | `course:edit` | CRUD manuale. |
| `POST` | `/glossary/regenerate` | `course:generate` | Rigenera glossario sync. |

Tutti restituiscono `CourseOut` aggiornato. Worker registrato in
lifespan `app/main.py`.

## Frontend

### Componenti shared (rendering)

- `MarkdownRenderer.tsx` — wrapper `react-markdown` + `remark-gfm` +
  `remark-math` + `rehype-katex`. Pre-processa `[FIG:..]`, `[TAB:..]`,
  `[EQ:..]`, `[EX:..]` sostituendoli con custom blocks (Mermaid,
  KaTeX block, table, example card). **Normalizza i delimitatori
  math** AI-style: `\(..\)` → `$..$`, `\[..\]` → `$$..$$` (escludendo i
  pattern asset-ref `\[FIG:..\]` ecc.) — necessario perché alcuni
  output gpt-5.5 emettono LaTeX "puro" che `remark-math` non
  riconosce. Le classi tipografiche sono `lesson-prose` (custom CSS in
  `index.css`, niente `@tailwindcss/typography`). Riceve `assetNumbers`
  (`KIND:id_lower` → N) da `LessonContentView`, che numera il corpo NON
  normalizzato (`computeAssetNumbers`) e poi lo passa da
  `normalizeAssetRefs` / `citeAssetRefs` (`lib/assetRefNormalize.ts`):
  tabelle, equazioni ed esempi portano l'etichetta «Tabella N.» /
  «Equazione N.» / «Lemma N.» / «Esempio N.» (chiavi `courses.figures.*`,
  sempre presente; senza mappa la forma non numerata, come nelle slide).
- `FigureFrame.tsx` — cornice unica delle figure (D4): stesso markup del
  partial backend `templates/partials/figure.html.j2` (`<figure
  class="figure figure--{variant} figure--{format}">` + `<figcaption>` con
  «Figura N.» in grassetto, `stripFigurePrefix` sulla didascalia, coda
  calcolata di `function` come `extraCaption`), fallback `Suspense`
  interno, nessuna card. Il numero arriva da `lib/figureNumbering.ts`
  (copia di `figure_numbering.py`: prima citazione `[FIG:id]` nel corpo
  intro → sezioni → sintesi, orfane accodate dopo la sintesi, A12).
- `MermaidDiagram.tsx` — lazy-load di `mermaid` (dynamic import) +
  `initialize` da `lib/figureTheme.ts` (tema D3, `htmlLabels: false` al
  livello top, `securityLevel: "strict"` nel browser) + render SVG.
  **Pre-validazione con `mermaid.parse(code, { suppressErrors: true })`
  PRIMA del render**: se la sintassi è invalida, mostra il box di errore
  controllato `FigureErrorBox` (`courses.figures.renderError` + dettagli
  collassabili). Senza la pre-validazione, `mermaid.render()` su syntax
  invalida inietta nel DOM una grossa SVG bomb-icon che rompe il layout
  della pagina. Strip programmaticamente l'attributo `max-width` inline
  dell'SVG generato; la larghezza viene dalla banda di leggibilità del
  web (D10/D11, 8-11 pt): `measureSvgFontPx` misura nel DOM il corpo del
  testo più piccolo (stesso JS del pre-render backend), `fitFigureWidthMm`
  (`lib/figureFormats.ts`, mirror di `figure_scale.py`) dà la larghezza
  `W` e un wrapper interno senza padding porta `width: min(100%, Wpx)`
  con l'SVG a `width: 100%`. Nessun tetto d'altezza (il vecchio tetto
  «solo per i diagrammi orizzontali» è superato: un tetto unito a
  `width: 100%` scalava i verticali fino a testo di 7 px, mentre la
  larghezza piena portava un flowchart LR a 18 pt in una colonna di
  900 px).
- `VegaLiteDiagram.tsx` — import dinamico di `vega` / `vega-lite` /
  `vega-embed` (`actions: false`, `config` = `VEGALITE_THEME_CONFIG`,
  `loader` inerte che rifiuta `load/http/file`: un `data.url` non viene
  mai scaricato dal browser del docente); l'SVG è montato nel DOM tramite
  ref dopo `sanitizeSvgElement` (nessun `dangerouslySetInnerHTML`).
- `DotDiagram.tsx` — `@viz-js/viz` (Graphviz in WebAssembly, import
  dinamico) con `dotDefaultsPrelude` del tema iniettato dopo la `{` di
  apertura; stesso montaggio via ref e stesso box di errore.
- `FunctionFigure.tsx` — la spec JSON `function` è resa dal backend
  (`POST /lesson-assets/render-function`, `useQuery` con `staleTime:
  Infinity` per `(orgId, courseId, assetId, content)`): `<img>` con
  l'SVG in data URI e didascalia calcolata (`computed_caption`) come
  coda; `orgId`/`courseId` arrivano da `CourseRefContext` (A21), senza
  provider mostra `courses.figures.missing`.

### Componenti shared (editing) — editor user-friendly

L'edit manuale del contenuto **non espone più la sintassi grezza**.
Gli editor specializzati nascondono markdown, LaTeX e — dove possibile —
la sorgente delle figure:

- **`RichTextEditor.tsx`** — wrapper TipTap (`@tiptap/react` 3.22 +
  `@tiptap/starter-kit` + `@tiptap/extension-link` + `tiptap-markdown`
  0.9). Bridge bidirezionale markdown ↔ ProseMirror doc. Toolbar con
  Bold/Italic/Strike/H2/H3/UL/OL/Quote/Link/InlineCode. Prop `size`
  controlla la min-height (`sm`/`md`/`lg`).
  - `protectTokens(md)` / `unprotectTokens(md)`: avvolgono i pattern
    inline `[KIND:..]`, `$..$`, `$$..$$` in inline-code (`` `...` ``)
    prima di passare il testo a TipTap, così ProseMirror non lo
    escapa con backslash. Invertito al salvataggio.
  - Accesso allo storage markdown:
    `(editor.storage as unknown as Record<string, unknown>).markdown
    as MarkdownStorage | undefined` (workaround di typing
    tiptap-markdown).
- **`TableEditor.tsx`** — griglia visuale per tabelle markdown. Stato
  `{ headers: string[], rows: string[][] }`. Toolbar +/- riga e
  +/- colonna. Parser tollerante (fallback 2x2 vuota su markdown
  malformato). Serializza in markdown table su ogni edit.
- **`LatexEditor.tsx`** — split textarea (LaTeX raw) + preview KaTeX
  live. **Palette di simboli** in 6 gruppi (structures, basicOps,
  relations, operators, greek, matrices) — click inserisce token al
  cursore. Errori LaTeX visibili nel preview (rosso KaTeX).
- **`MermaidEditor.tsx`** — split textarea + preview live `<MermaidDiagram>`
  con debounce 500ms. Dropdown **template** con un modello per ciascuno dei
  **quindici** tipi ammessi da D8, raggruppati per famiglia d'uso (processi
  e flussi: `flowchart`, `sequence`, `state`; struttura e modelli: `class`,
  `er`, `block`; organizzazione dei concetti: `mindmap`, `timeline`,
  `treemap`; quantità e ripartizioni: `pie`, `xychart`, `radar`, `sankey`;
  pianificazione e decisione: `gantt`, `quadrant`). La scelta sostituisce
  il contenuto con uno scheletro funzionante; i template restano distinti
  dai campioni D8 dei test (A22).
- **`VegaLiteEditor.tsx`** / **`DotEditor.tsx`** — costruiti su
  `FigureSourceEditor` (textarea + anteprima client con
  `useDebouncedValue`, select dei template raggruppato per famiglia
  d'uso): template accademici che rispettano le regole D5 del validatore
  (Vega-Lite, 24 modelli — confronto fra categorie, parte sul tutto
  (torta e ciambella), distribuzione, andamento nel tempo, correlazione,
  matrice, incertezza, graduatoria — con `data.values` ≤ 200 righe,
  `clip: true`, `scale.domain`, una sola `title`; DOT, 18 modelli —
  gerarchie e alberi, grafi orientati e dipendenze, grafi non orientati e
  reti, automi e strutture dati, architetture, cammini e flussi — senza
  attributo `image`/`URL`/`href` e senza
  blocco `graph/node/edge [` così il tema è iniettato per intero).
  L'errore del parser client va sotto
  l'anteprima; il 422 per-asset del PATCH (`meta.errors`) in testa alla
  card (pattern `LatexEditor`).
- **`FunctionEditor.tsx`** — modulo a campi per la spec `FunctionFigureSpec`
  (D9): il docente non vede JSON. Select `kind`, espressioni (max 4) con
  label, variabile (+ `variables` per `level_curves`), dominio/range,
  `show`, annotazioni (tangente / area / punto), parametro, `sampling` in
  «Avanzate». Anteprima via `POST /lesson-assets/render-function`
  (`useDebouncedValue(spec, 700)` + `useQuery` con `keepPreviousData`,
  timeout client 30 s), errori `meta.errors` mappati sul campo con
  `aria-invalid`, KaTeX per il LaTeX di ogni espressione, didascalia
  calcolata mostrata come coda.
- **`VisualAssetEditor.tsx`** e **`AddVisualAssetMenu.tsx`** — componenti
  condivisi dai dialog di Fase 3 e Fase 4 (prima duplicati in ~220 righe):
  badge di formato, campi caption / alt text, editor per formato, errore
  422 per-asset, `makeAssetId` con loop anti-collisione.

I dati salvati restano **markdown / LaTeX / sorgenti delle figure come
stringhe** (`content`: codice Mermaid, spec JSON Vega-Lite, sorgente DOT,
spec JSON `function`) — schema e renderer di vista condivisi con il PDF.

### Vista principale

`CourseLessonContentView.tsx` (Tab 6 dell'editor):
- Header con aggregate progress (0..100%) + pulsanti Generate/Approve all
  ("Approva tutti" tollerante, mirror BE: visibile con ≥1 lezione `ready`
  e nessuna `pending/processing/failed`)
- Gating per-unità: empty-state **per-modulo** e CTA per-riga abilitate
  solo se il modulo della lezione ha la struttura `approved`
- **ETA + tempo medio per lezione** durante un batch attivo: `useBatchEta`
  (vedi [Frontend 08 — Hooks](../frontend/08-hooks.md)) deriva la velocità
  dai timestamp `content_generated_at` delle lezioni completate nella
  recent window (90 min) e stima il rimanente come `avgPerLesson × remaining`
- **Sub-pannello Glossario** (collapsible): chip dei termini con
  tooltip su `usage_note` + pulsante Rigenera
- Lista per modulo con sub-card per lezione (status badge + Progress
  live + bottoni contestuali Generate/Regenerate/Retry/Approve/Edit)
- Quando lezione è `ready`/`approved` ed espansa: render completo via
  `LessonContentView.tsx` (figure live con «Figura N.» — Mermaid,
  Vega-Lite, DOT, `function` —, KaTeX, tabelle, esempi card). Il
  container fornisce `CourseRefContext` (`orgId`/`courseId`) ai renderer
  che chiamano il backend e inoltra al dialog gli errori 422 per-asset
  (`meta.errors`) della mutation di salvataggio.

### Dialogs

- `LessonContentGenerateDialog.tsx` — 4 modi: generate/regenerate per
  singola lezione o batch corso. Textarea hint per regenerate.
- `LessonContentEditDialog.tsx` — `max-w-6xl` con pannello unico
  scrollabile organizzato in `SectionGroup` collassabili:
  - Testo della lezione (intro / sections / summary) → `RichTextEditor`
  - Asset visivi → vedi sezione [Asset visivi: figure + immagini caricate](#asset-visivi-figure-mermaid-vega-lite-dot-function--immagini-caricate)
    qui sotto.
  - Tabelle → `TableEditor`
  - Formule → `LatexEditor` (latex) + `RichTextEditor` (explanation)
  - Esempi → `RichTextEditor`
  - Key takeaways / References → `<Input>`. `handleSubmit` invia sempre
    entrambe le liste, scartando le righe vuote (per le references: le
    citation vuote, che darebbero 422); il backend le restituisce
    normalizzate (trim + dedup): la vista si aggiorna dalla risposta
    (`setCache`), il dialog si chiude e alla riapertura mostra le liste
    normalizzate.
  - **`RefIdField`**: per ogni asset (FIG/TAB/EQ/EX) mostra l'ID
    canonico (es. `[FIG:fig_pipeline]`) con pulsante **copy** e
    **input rinominabile**. Etichetta + chip readable in i18n
    `courses.lessonsContent.editor.refCode`.
  - **Auto-sync rinomine**: `patchRefs(kind, oldId, newId)` viene
    invocato `onIdRename`/`onChange` di ogni `RefIdField` e fa il
    replace di `[KIND:oldId]` → `[KIND:newId]` su tutti i campi
    testuali (introduction, sections.content, summary, examples.content,
    equations.explanation). Niente "promemoria di sincronizzare a
    mano" — l'editor fa cascade.
  - **"Evidenzia dove usato"** (`HighlightUsageButton`): accanto ad
    ogni `RefIdField` cerca la prima occorrenza di `[KIND:id]` nei
    campi scansionabili (intro → sezioni → summary → esempi →
    explanation equazioni), apre il `SectionGroup` che la contiene
    (i `SectionGroup` sono controllati `open`/`onToggle` dallo stato
    padre proprio per supportare l'auto-espansione), scrolla in vista
    e applica un flash visivo `ring-2 ring-amber-400` per ~2.2s.
    **Inoltre evidenzia anche il `<code>{token}</code>`** specifico
    dentro il paragrafo (background amber-400 al 45% + outline) — il
    `RichTextEditor` rende ogni token come `<code>` grazie a
    `protectTokens`, quindi basta un `querySelectorAll("code")` con
    match esatto sul textContent (commit `f53906c`).
    Per i `references[]` (citazioni senza ID-token) il pulsante fa
    invece un substring match case-insensitive della `citation` —
    best-effort. Se nessuna occorrenza viene trovata → toast informativo.

#### Asset visivi: figure (Mermaid, Vega-Lite, DOT, function) + immagini caricate

Refactor del commit `92d5f37` (asset `image` + Mermaid) esteso dal branch
`feat/academic-figures` alle quattro famiglie di figure (documento
[17 — Figure accademiche](17-visual-figures.md)). Pre-refactor lo schema
aveva `asset_type` (diagramma/schema/...) + `format`
(mermaid/image_prompt/...). Oggi:

- L'editor produce asset con `format ∈ { "mermaid", "vegalite", "dot",
  "function", "image" }` (alias `VisualAssetFormat` in
  `schemas/course_lesson_content.py`).
- L'AI Fase 3 genera i quattro formati di figura: lo schema strict offre
  al modello quelli in `figure_render_service.available_formats()`
  (kill-switch `FIGURE_*_ENABLED` e dipendenza presente sul server); il
  testo del prompt li descrive sempre tutti (A19).
- Ogni figura è validata dal renderer del suo formato: a generazione
  (`validate(deep=True)`, con fix AI per kind) e al salvataggio manuale
  (`PATCH …/content`, solo gli asset con `(format, content)` cambiati,
  A15) — un asset invalido produce `422 lesson_content_invalid_visual_asset`
  con `meta.errors[{loc, asset_id, format, msg, type}]`, che l'editor
  mostra sulla card dell'asset.
- Asset legacy (`image_prompt|image_search_query|description`) restano
  in DB e vengono renderizzati come placeholder testuale dentro la
  cornice «Figura.». L'editor li mostra come banner readonly: l'utente
  deve eliminarli e ricrearli.
- Numerazione: il numero della figura non è persistito; è calcolato a
  render (vista e PDF) dall'ordine di prima citazione `[FIG:id]` nel corpo
  intro → sezioni → sintesi; le figure non citate sono accodate dopo la
  sintesi (A12). I prompt vietano al modello di iniziare la caption con
  «Figura N»; un prefisso già presente è ripulito a render, mai nel DB.

**Workflow nuovo asset** (componente condiviso `AddVisualAssetMenu`,
usato anche dal dialog delle slide):

```
[+ Aggiungi asset visivo]
    │
    ▼
┌──────────────────────────────────┐
│ Carica immagine                  │ → file picker (jpg/png/webp ≤5 MB)
│ Scrivi Mermaid a mano            │ → MermaidEditor
│ Grafico Vega-Lite                │ → VegaLiteEditor (template accademici)
│ Grafo DOT                        │ → DotEditor
│ Figura calcolata (function)      │ → FunctionEditor (modulo a campi)
└──────────────────────────────────┘
```

- **Carica immagine** → POST `/lesson-assets/upload` →
  `path = lesson_assets/{cid}/{uuid}.png` → push asset con
  `format="image"`, `content=path`. L'editor mostra preview `<img>` +
  bottone `[✨ Digitalizza in Mermaid]`.
- **Scrivi Mermaid a mano** → push asset con `format="mermaid"`,
  `content=""`. L'editor apre subito `MermaidEditor` (live preview).
- **Grafico Vega-Lite** / **Grafo DOT** / **Figura calcolata** → push
  asset con il formato scelto e il primo template dell'editor
  corrispondente; l'id è generato con `makeAssetId` (loop
  anti-collisione sugli id esistenti).

**Digitalizza in Mermaid** (Vision API) — sull'asset `format="image"`:

- POST `/lesson-assets/convert-to-mermaid` con `path = asset.content`.
- Backend (`openai_image_to_mermaid_service`): encode base64 → call
  OpenAI Vision (`settings.openai_image_to_mermaid_model`, default
  `gpt-4o`) con un system prompt che chiede solo codice Mermaid, niente
  prosa, niente fence. Se l'immagine non contiene uno schema
  riconoscibile, il modello risponde `UNRECOGNIZED` → il service
  solleva `OpenAIImageToMermaidError` → endpoint 409
  `image_to_mermaid_failed`.
- Validazione superficiale del codice: deve iniziare con un tipo di
  `figure_theme.MERMAID_ALLOWED_TYPES` (i 15 tipi D8 più gli alias
  `graph`/`stateDiagram`; `journey`, `gitGraph`, `kanban`, `packet-beta`,
  `architecture-beta` sono rifiutati). La validazione semantica vera
  avviene sul frontend tramite live preview di `MermaidEditor` e, al
  salvataggio, con il gate statico del registro.
- Successo → editor sostituisce localmente `format="mermaid"` +
  `content=<codice>`. Il file PNG resta sul disco fino al successivo
  salvataggio del `content_raw`, dove il cleanup orfani lo elimina.

**Cleanup file orfani** (`course_lesson_content_crud._cleanup_removed_image_assets`):
al PATCH `content_raw`, il service confronta `old_visual_assets` con
`new_visual_assets` e per ogni asset rimosso con `format="image"`
esegue `os.unlink` best-effort dopo `db.commit()`. Safety check: il
path deve essere sotto `lesson_assets/{course_id}/`, niente `..` o
slash strani — niente cancellazioni cross-tenant o path traversal.

### Polling

`CourseEditorPage.tsx`: la query `courseQuery.refetchInterval`
restituisce `5000ms` se almeno una lezione è in
`content_status ∈ {pending, processing}` o se
`glossary_status ∈ {pending, processing}`. Estensione §7: poll anche se
`pdf_status ∈ {pending, processing}` (4000ms — vedi
[09 — PDF export](09-pdf-export.md)).

### Dipendenze npm

```json
{
  "mermaid": "^11.17.2",
  "vega": "^6.4.0",
  "vega-lite": "^6.4.3",
  "vega-embed": "^7.2.0",
  "@viz-js/viz": "^3.30.0",
  "react-markdown": "^9",
  "remark-gfm": "^4",
  "remark-math": "^6",
  "rehype-katex": "^7",

  "@tiptap/react": "^3.22.5",
  "@tiptap/pm": "^3.22.5",
  "@tiptap/starter-kit": "^3.22.5",
  "@tiptap/extension-link": "^3.22.5",
  "tiptap-markdown": "^0.9.0"
}
```

(KaTeX era già installato per il summary documenti.) `vega`, `vega-lite`,
`vega-embed` e `@viz-js/viz` sono caricati con import dinamico solo
quando una figura del formato è visibile: il bundle iniziale cresce di
circa 38 kB (vedi le misure in [17 — Figure accademiche](17-visual-figures.md)).

### i18n

Locali aggiornati: solo IT/EN canonici (le altre 22 lingue saranno
completate via "Completa con AI" in app). Namespace
`courses.lessonsContent.*` e `courses.glossary.*`.

Vale anche per le 12 chiavi di etichetta dei quattro kind aggiunte da
D5 sotto `courses.figures.*` (rimando, tabella, equazione, esempio,
teorema): sono in `it.json` e `en.json` e in nessuno degli altri 22
locali. Non è un buco visibile perché **entrambi i lati ricadono
sull'italiano**: il frontend con `fallbackLng: "it"` (`src/i18n/index.ts`)
e il backend con `figure_theme.figure_labels`, che normalizza il
sottotag (`en-GB` → `en`) e per ogni altra lingua ritorna il dizionario
`FIGURE_I18N_FALLBACK = "it"` (36 chiavi per `it` e per `en`). Un corso
in una terza lingua vede quindi «Figura 1.» e «Tabella 1.» in italiano
nella vista e nel PDF, non una chiave grezza.

## Configurazione

```env
# Glossario (§10.1)
OPENAI_GLOSSARY_MODEL=gpt-5.5
OPENAI_GLOSSARY_MAX_TOKENS=4000
COURSE_GLOSSARY_DOCUMENTS_CONTEXT_MAX_CHARS=20000

# Fase 3 — Contenuti
OPENAI_LESSON_CONTENT_MODEL=gpt-5.5
OPENAI_LESSON_CONTENT_MAX_TOKENS=32000
OPENAI_LESSON_CONTENT_REASONING_EFFORT=high   # [minimal, low, medium, high]
COURSE_LESSON_CONTENT_POLL_INTERVAL_SECONDS=4
COURSE_LESSON_CONTENT_MAX_CONCURRENCY=3
# Selezione per lezione degli estratti documentali (vedi «--grounding»
# in fondo): budget totale, budget per documento, kill-switch.
COURSE_LESSON_CONTENT_DOCUMENTS_CONTEXT_MAX_CHARS=40000
COURSE_LESSON_CONTENT_DOCUMENTS_PER_DOC_MAX_CHARS=12000
COURSE_LESSON_CONTENT_DOCUMENTS_SELECTION_ENABLED=true
# Numero massimo di retry automatici prima di transitare a `failed`.
# La UI vede la lezione come "in elaborazione" durante i retry.
COURSE_LESSON_CONTENT_AUTO_RETRY_MAX=5
COURSE_LESSON_STRUCTURE_AUTO_RETRY_MAX=5

# Revisore figura ↔ testo (D15): verdetto predefinito `coerente`,
# riscrittura accettata solo se valida e non peggiora la misura.
OPENAI_FIGURE_REVIEW_MODEL=gpt-4o-mini
OPENAI_FIGURE_REVIEW_REASONING_EFFORT=
OPENAI_FIGURE_REVIEW_MAX_TOKENS=4000
FIGURE_REVIEW_MAX_ATTEMPTS=2
FIGURE_REVIEW_MAX_PARALLEL=4
FIGURE_REVIEW_ENABLED=true
```

`OPENAI_LESSON_CONTENT_MAX_TOKENS=32000` è calibrato per output
8-15k token + reasoning gpt-5.5. Aumentare se viene osservato
`finish_reason="length"` con `reasoning_tokens` alti.

`OPENAI_LESSON_CONTENT_REASONING_EFFORT=high` di default — è il task più
complesso del pipeline (markdown lungo + asset + bibliografia + JSON
schema strict). Per accelerare drasticamente un corso grande, abbassare a
`medium` taglia ~40% dei tempi con qualità leggermente inferiore.

## Cosa NON fa questa iterazione (out of scope)

1. **Generazione AI ex-novo di immagini** (DALL·E / Stable Diffusion):
   il backend non chiama nessun text-to-image. L'AI Fase 3 produce solo
   figure descritte da una sorgente testuale (codice Mermaid, spec
   Vega-Lite, sorgente DOT, spec `function`); le immagini "vere" arrivano
   da upload utente (eventualmente convertibili in Mermaid via Vision
   API, vedi `openai_image_to_mermaid_service`).
2. **Cascade invalidation di Fase 4-5** su edit di Fase 3.
3. **Versioning storico** delle rigenerazioni (`content_raw` snapshotta
   solo l'ultima versione).
4. **Auto-trigger di Fase 3 su approve di Fase 2** (l'utente preferisce
   trigger manuale).
5. **Multi-lingua del glossario** (generato 1 volta nella
   `course.language_code`).
6. **Streaming** dell'output AI (json_schema strict richiede non-streaming).
7. **Esercizi auto-studio**: l'iterazione precedente includeva
   `exercises_for_self_study`; ora rimosso dal prompt e dallo schema.

> **Già fatto** (era out-of-scope nelle versioni precedenti del
> documento):
> - editor WYSIWYG del markdown — vedi sezione "Componenti shared
>   (editing) — editor user-friendly" sopra;
> - upload immagini come asset visivo + conversione vision → Mermaid
>   (commit `92d5f37`).

## Quante figure e di quale formato (18 settembre 2026)

Il prompt di Fase 3 chiedeva «1-3 figure per lezione» e apriva l'elenco
dei formati su `mermaid` con il criterio più largo possibile. Sull'export
reale di quattro lezioni di quattro corsi diversi il risultato era: **14
figure generate dal modello, 13 `mermaid` flowchart e una `function`**,
zero `vegalite`, zero `dot`, e tre lezioni su quattro con esattamente
quattro figure — cioè **sopra** il tetto dichiarato: il «1-3» non era il
vincolo che teneva basso il numero, mancava il criterio che lega la
figura al contenuto. Due regole nuove, entrambe in
`openai_lesson_content_service._system_prompt`:

- **scelta guidata dal contenuto** — per ogni figura si dichiara prima
  che cosa deve far vedere, il formato viene dopo; `function`,
  `vegalite` e `dot` sono nominati prima del flowchart, che è «l'ULTIMA
  scelta, non la prima». Con i vincoli di realtà (mai numeri inventati,
  mai figure decorative) e una regola editoriale di varietà (non più di
  due flowchart in una lezione con almeno tre figure, se il contenuto lo
  consente);
- **numerosità** — la figura segue il contenuto sezione per sezione,
  indicativamente 4-8 per lezione ordinaria e 0-2 per l'introduttiva,
  senza inventare contenuto per arrivare al numero.

La Fase 4 riceve la stessa scelta in forma breve (senza `function`, A1) e
un budget di slide ritarato: il range di `materialize_lesson_slides`
cresce già di una slide per ogni asset visivo, tabella e `new_asset`,
quindi otto figure non fanno uscire la lezione dal range a nessuna
durata. Nessun altro tetto è stato toccato: lo schema strict non limita
`visual_assets`, il cap di token (32.000) ha margine (~700 token in più
passando da tre a otto figure) e la validazione di otto figure in un
batch costa 1,3-2,4 s contro i 90 s del timeout. Storia completa,
misure e oracoli in [17 — Figure accademiche § 22](17-visual-figures.md).

**Diagnostica.** `app/services/figure_mix.py` (`compute_figure_mix`)
conta i formati e, dentro Mermaid, i tipi di diagramma. Alla
materializzazione `materialize_lesson_content` emette
`lesson_content_figure_mix` (`figures`, `formats`, `mermaid_types`) e,
quando una lezione con almeno tre figure usa un solo formato e un solo
tipo, il warning `lesson_content_figure_monoculture`. Nessuno dei due
blocca: la lezione resta valida e va in `ready`, è una misura per sapere
se il prompt ha funzionato. La stessa funzione alimenta la sezione (c) di
`scripts/measure_asset_refs.py`, così la misura sull'export e quella nei
log non possono divergere.

Dal lato Fase 4 la misura gemella è `lesson_slides_unreferenced_assets`
(warning di `materialize_lesson_slides`): elenca le figure e le tabelle
di Fase 3 che nessuna slide referenzia. La validazione controlla che
ogni riferimento esista, non che ogni figura sia referenziata, e una
figura senza slide sparisce anche dal video — la Fase 5 parla le slide
che esistono — restando solo in coda alla dispensa. Sull'export reale
capitava già con quattro figure; con 4-8 la perdita cresce. La stessa
misura è la colonna «senza slide» della sezione (c) di
`scripts/measure_asset_refs.py`.

## Misura del registro (strumento diagnostico)

`backend/scripts/measure_register.py` misura, nei testi già generati
(dispense, slide, bullet delle slide, discorso), la presenza di "frasi ad
effetto" e di "asserzioni valutative non sostenute". Serve a confrontare
il registro prima/dopo una modifica dei prompt. **Non è un gate**: non
esistono soglie, non blocca nulla, i numeri sono euristiche lessicali da
leggere in aggregato. Sola lettura sul DB.

```bash
# dalla cartella backend/
python -m scripts.measure_register                          # markdown, una riga per corso x tipo
python -m scripts.measure_register --course "Analisi" --by-lesson --top 10
python -m scripts.measure_register --format csv --export-jsonl before.jsonl > before.csv
python -m scripts.measure_register --from-jsonl before.jsonl --top 5
python -m scripts.measure_register --compare before.jsonl after.jsonl

# sul server (dc = alias docker compose di produzione)
dc exec -T backend python -m scripts.measure_register --format csv \
    --export-jsonl before.jsonl > before.csv
```

Filtri: `--course` (titolo ILIKE o UUID), `--lesson M1.L1`, `--since
YYYY-MM-DD` (sul `*_generated_at` del tipo), `--include-assessment`,
`--language it|en|auto`; `--group-introductory` separa le lezioni
introduttive; `--formulas-file`, `--punch-max-words`, `--long-min-words`
regolano le euristiche.

Indicatori per lezione x tipo (grezzi e normalizzati per 1.000 parole
o per 100 frasi):

- `punchline_after_long`: frase di <= 5 parole dopo una frase di >= 25;
- `formula_hits`: "Non è così.", "Punto.", "Anzi.", "proprio per questo",
  "Ecco perché", ... (lista per lingua);
- `evaluative_unjustified_a/b`: aggettivi valutativi (tier A: elegante,
  potente, brillante, ...; tier B: fondamentale, cruciale, ...) senza
  marcatore di giustificazione nella stessa frase o nella successiva
  (`:`, perché, infatti, ad esempio, una cifra, una formula, un
  riferimento bibliografico, ...);
- `short_ratio`, `rhetorical_questions`, `antithesis_openers` ("Ma ",
  "Eppure", "Anzi", ...), `sent_len_mean`/`sent_len_std`.

Ogni output riporta `SCRIPT_VERSION`: si confrontano solo run con la
stessa versione (le euristiche possono cambiare tra versioni). Test puri
in `backend/tests/test_measure_register.py`.

### `--grounding` e selezione degli estratti documentali

Dalla PR di grounding il prompt di Fase 3 non riceve più lo stesso blocco
documenti per ogni lezione: `app/services/lesson_document_selection.py`
seleziona per lezione le voci dei riassunti (definizioni, formule e regole,
concetti chiave, esempi e casi, struttura) per sovrapposizione lessicale con
titolo, temi obbligatori, scaletta, obiettivi e sinossi della lezione, con
smorzamento di frequenza (gli stem ubiqui del corso non pesano) e budget
`COURSE_LESSON_CONTENT_DOCUMENTS_CONTEXT_MAX_CHARS` (default 40.000) /
`COURSE_LESSON_CONTENT_DOCUMENTS_PER_DOC_MAX_CHARS` (12.000). Il kill-switch
`COURSE_LESSON_CONTENT_DOCUMENTS_SELECTION_ENABLED=false` ripristina il
comportamento storico (utile per la campagna before/after del registro). Ogni
selezione lascia un log `lesson_documents_context_selected` con
`docs_ready`, `docs_relevant`, `entries_selected`, `chars`, `fallback_overview`.

`python -m scripts.measure_register --db --grounding --by-lesson` riesegue la
stessa selezione per ogni dispensa e riporta `ground_cov` (quota delle voci
selezionate che compaiono nel testo generato) e `refs_doc`/`refs_gen`
(references per `source`).
