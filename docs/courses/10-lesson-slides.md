# 10 — Lesson slides (Fase 4)

Generazione AI delle **slide della presentazione** per ogni lezione.
Pipeline parallela (cap=3 di default) con stato per-lezione su
`course_lesson.slides_status` e auto-retry trasparente. Riferimento
spec: §7 (sezione "slides") di `prompt_generazione_corsi.md`.

## Cosa fa

Per ogni lezione con `content_status = 'approved'`, una chiamata
OpenAI produce la sequenza di slide dimensionata sui
`minuti_per_lezione` del corso. Le slide:

- **Riusano** gli asset di Fase 3 (visual_assets, tables, equations,
  examples) tramite `references_assets[]` con asset_id
- Possono creare **nuovi asset** (`new_assets[]`) quando il contenuto
  richiede una visualizzazione che non è già stata prodotta in Fase 3:
  lo schema strict offre al modello `mermaid`, `vegalite` e `dot`
  (`build_lesson_slides_json_schema(visual_formats=available_formats()
  − {"function"})`, A1: `function` non è offerto in Fase 4 ma è accettato
  dall'alias Pydantic se il docente lo aggiunge a mano); `asset_type` e i
  tre formati legacy non sono più nello schema strict. Le regole dei
  formati (D5, D8) sono quelle di Fase 3 (PROMPT 5 rinvia a PROMPT 3),
  vedi [17 — Figure accademiche](17-visual-figures.md)
- Hanno tipo classificato (16 valori: title, agenda, prerequisites,
  concept, definition, diagram, formula, table, example, case_study,
  exercise, discussion, summary, takeaways, references, bibliography)
- Hanno opzionalmente un **`body`** (prosa breve di 1-3 frasi, max 600
  char) per evitare slide tutte-bullet visivamente piatte

## Stato per-lezione

`course_lesson.slides_status` ∈
`empty → pending → processing → ready → approved | failed`.

Auto-retry trasparente prima di `failed`: se l'errore è recuperabile
(rate-limit OpenAI, validazione §7.4 fallita, materializzazione fallita)
e `slides_attempts < COURSE_LESSON_SLIDES_AUTO_RETRY_MAX` (default 5),
il worker riporta lo status a `pending` e ritenta al tick successivo.
La UI vede solo "in elaborazione" finché passa.

`course.status` (`slides_pending` / `slides_ready` / `slides_approved`)
è derivato dagli stati per-lezione (`_recompute_course_slides_status`).

## Pre-condizione

Gate API **per-unità** (nessun allow-set su `course.status`): corso non
terminale (`ensure_course_not_terminal`) + `lesson.content_status = 'approved'`
(dispensa della STESSA lezione approvata — prima bastava `ready`; stesso
code `lesson_content_not_ready_for_slides`).

Il worker accetta ancora `content_status ∈ {ready, approved}` AND
`content_raw` valorizzato (**transitorio**: per non far fallire i task
accodati prima del cambio; stretta a `approved` pianificata a code
svuotate). Se la pre-condizione non è soddisfatta al momento del
dispatch, il worker fa un fail terminale **non recuperabile** con
messaggio "Genera prima il contenuto" — non viene ritentato.

## Flusso di generazione

```
[utente] POST /lessons/{id}/slides/generate (con hint opzionale)
  └─► course_lesson_slides_service.request_lesson_slides_generation
       ├─► ensure_course_not_terminal(course)
       ├─► validate lesson.content_status == 'approved'
       ├─► lesson.slides_status = "pending"
       ├─► lesson.slides_regeneration_hint = hint
       ├─► reset slides_pdf_status='empty' se era ready/failed (PDF obsoleto)
       ├─► _recompute_course_slides_status(course)
       └─► audit course.lesson.slides.generate.requested

[worker] course_lesson_slides_worker._tick (ogni 4s)
  └─► SELECT lessons WHERE slides_status='pending'
      ├─► claim atomico in _inflight (PRIMA del semaforo)
      └─► fire-and-forget _bound_process(lesson_id)

[worker task] _bound_process → semaphore.acquire → _process_one
  ├─► reload lesson + course (eager load completo)
  ├─► pre-check content_status (terminal fail se non ready/approved — transitorio, vedi Pre-condizione)
  ├─► lesson.slides_status = "processing", attempts++
  ├─► build_user_prompt(course, lesson) = §7.2 + §9.4 se rigenerazione
  │    (include content_raw + bibliografia + hint utente)
  ├─► progress ticker (background) ease-out 15→85%
  ├─► openai_lesson_slides_service.generate_lesson_slides(...)
  │    ├─► system prompt §7.1 + REGENERATION_SUFFIX se rigenerazione
  │    ├─► response_format json_schema strict (§7.3)
  │    └─► return (LessonSlidesOutput, usage)
  ├─► cancel-check (re-leggi slides_status — utente potrebbe aver cancellato)
  ├─► materialize_lesson_slides (validazioni §7.4)
  │    1. lesson_id == lesson_code
  │    2. total_slides == len(slides)
  │    3. slide_number sequenziali 1..N
  │    4. slide_id univoci
  │    5. total_slides nel range atteso per minuti_per_lezione (±20%)
  │    6. references_assets risolvibili (Fase 3 ∪ new_assets)
  │    7. source_section_id esiste in Fase 3 (se non vuoto)
  │    8. ogni section è referenziata da almeno una slide (soft warning)
  ├─► lesson.slides_raw = output
  ├─► lesson.slides_tokens = usage
  ├─► lesson.slides_status = "ready", progress = 100
  ├─► _recompute_course_slides_status(course)
  └─► audit course.lesson.slides.generated
```

In caso di errore recuperabile, `_apply_failure(recoverable=True)`
riporta a `pending` finché `attempts < auto_retry_max`. Errori non
recuperabili (`OpenAINotConfiguredError`, pre-check content) sono
terminal subito.

## OpenAI service — `openai_lesson_slides_service.py`

System prompt (§7.1) tradotto fedelmente dalla spec con regole su:
1. **Riuso asset**: referenzia per ID, niente duplicati
2. **Nuovi asset**: solo se necessario, prefisso `*_new_*`
3. **Numero slide**: range indicativo per durata (15min→6-10, 30min→12-15, ...)
4. **Struttura standard**: title + agenda + prerequisites? + sviluppo + summary + takeaways + references
5. **Contenuto per slide**: title ≤8 parole, body 1-3 frasi opzionale, bullets 0-6 max ~14 parole
6. **Tipi slide**: 16 enum
7. **Caso speciale lezione introduttiva**: bibliografia + benvenuto

Regeneration suffix (§9.4):
> ATTENZIONE: stai RIGENERANDO le slide di una lezione già slidificata.
> Considera la versione precedente e il feedback del docente.
> Mantieni gli stessi asset_id già presenti in Fase 3.
> Se possibile, mantieni lo stesso slide_id per slide che corrispondono
> semanticamente alla versione precedente (utile per riusare il discorso
> esistente nella futura Fase 5).

Settings env-driven:

| Env | Default | Significato |
|---|---|---|
| `OPENAI_LESSON_SLIDES_MODEL` | `gpt-5.5` | Modello reasoning per Fase 4 |
| `OPENAI_LESSON_SLIDES_MAX_TOKENS` | `16000` | `max_completion_tokens` (output ~4-8k + reasoning) |
| `OPENAI_LESSON_SLIDES_REASONING_EFFORT` | `medium` | `minimal/low/medium/high` |
| `COURSE_LESSON_SLIDES_POLL_INTERVAL_SECONDS` | `4` | Tick worker |
| `COURSE_LESSON_SLIDES_MAX_CONCURRENCY` | `3` | Lezioni in parallelo |
| `COURSE_LESSON_SLIDES_AUTO_RETRY_MAX` | `5` | Tentativi prima di fail terminale |

## Schema output (§7.3)

```json
{
  "lesson_id": "M1.L4",
  "total_slides": 12,
  "slides": [
    {
      "slide_number": 1,
      "slide_id": "S01",
      "type": "title",
      "title": "Algoritmi non supervisionati",
      "body": "In questa lezione introduciamo le tecniche di clustering...",
      "bullets": [],
      "references_assets": [],
      "source_section_id": ""
    },
    {
      "slide_number": 5,
      "slide_id": "S05",
      "type": "diagram",
      "title": "Pipeline k-means",
      "body": "",
      "bullets": [],
      "references_assets": ["fig_kmeans_flow"],
      "source_section_id": "S2"
    }
  ],
  "new_assets": [
    {
      "asset_id": "fig_new_recap",
      "format": "mermaid",
      "content": "graph LR\nIntro --> Body --> Summary",
      "caption": "Mappa concettuale di sintesi",
      "alt_text": "Flusso lineare a tre step"
    }
  ]
}
```

## Body field (no slide tutte-bullet)

A seguito del feedback utente sulle slide visivamente piatte, ogni slide ha un campo opzionale `body` (prosa breve di 1-3 frasi, max 600 char) che il prompt suggerisce di alternare con i bullet:

| Tipo slide | Body | Bullets |
|---|---|---|
| `title` | 1 frase (sottotitolo) | nessuno |
| `concept`/`definition` | 2-3 frasi | 0-3 di esempio |
| `agenda`/`takeaways` | vuoto | 3-6 |
| `summary` | 1-2 frasi conclusive | opzionali |

Le slide pure-bullet sono ancora supportate (basta lasciare `body` vuoto).

## CRUD manuale — `course_lesson_slides_crud.py`

Edit del `slides_raw` finché la lezione è in `ready`/`approved`. Edit
non degrada lo stato (`approved` resta `approved`). Hard fail solo per:

- `slide_id` duplicati o vuoti
- `slide_number` non sequenziali 1..N
- `new_asset_id` duplicati o vuoti
- `references_assets` verso ID non risolvibili (in `content_raw` ∪ `new_assets`)
- `source_section_id` non vuoto verso sezione Fase 3 inesistente
- `new_assets[]` con `(format, content)` cambiati che non superano il
  validatore del formato (`figure_render_service.validate_visual_assets_or_raise`,
  A15) → `422 lesson_slides_invalid_new_asset` con
  `meta.errors[{loc, asset_id, format, msg, type}]`, mostrato dal dialog
  sulla card dell'asset
- più di un asset visivo o tabella sulla stessa slide (WP4, stessa regola
  del percorso AI, equazioni ed esempi esclusi) →
  `409 lesson_slides_multiple_visual_assets`, **solo sulle slide a cui il
  PATCH aggiunge un visivo**: una slide nuova con due o più visivi/tabelle,
  oppure una slide salvata il cui nuovo insieme di visivi/tabelle ne ha più
  di uno e contiene almeno un riferimento che la versione salvata non aveva
  (confronto `strip().lower()`, un riferimento ripetuto con un'altra grafia
  conta una volta). L'insieme salvato usa i tipi salvati: un'equazione o un
  esempio ridichiarato come visivo con lo stesso id (`new_equations` →
  `new_assets`) è un visivo nuovo per le slide che lo citano, e la regola
  scatta anche quando il PATCH porta solo `new_assets` o `new_tables`.
  Casi espliciti su una slide storica con due o più
  figure:
  - titolo, prosa, bullet, equazioni ed esempi aggiunti, riordino o grafia
    diversa delle stesse figure → 200;
  - una figura tolta (3 → 2, poi 2 → 1): è un sottoinsieme di quanto
    salvato → 200, la slide si riporta alla regola un passo alla volta;
  - una figura sostituita da un'altra (`fig_2` → `fig_3`, stesso numero di
    visivi) o una figura/tabella aggiunta → 409: per cambiare figura la
    slide deve prima scendere a un solo visivo.

  Nessun backfill. Il confronto è per `slide_id`: scambiare gli id fra una
  slide storica con due figure e un'altra che non le contiene dà 409
  (scelta prudente, l'editor non riassegna mai gli id). Il
  percorso AI (`materialize_lesson_slides`, punto 6b) conta allo stesso
  modo gli asset distinti (`["fig_1", "FIG_1"]` è una figura sola); le
  nuove tabelle non vi entrano perché lo schema strict di Fase 4 ammette
  solo `new_assets`

`PATCH /lessons/{id}/slides` setta `slides_modified_at = now()` per
stale-detection downstream (PDF slide e Fase 5 si segnaleranno stale).
Nel PDF slide e nei frame video le figure sono rese con l'etichetta
«Figura.» senza numero (A2).

## Larghezza e box della figura (WP3, 16 settembre 2026)

### Larghezza dalla banda di leggibilità

La larghezza di una figura nelle slide e nei frame video non è più una
percentuale fissa: `figure_scale.fit_figure_width_mm` la calcola perché il
corpo di testo più piccolo dell'SVG cada nella banda della superficie
(`READABILITY_BANDS_PT`, `figure_scale.py:42-45`).

| Superficie | Banda | Variante |
|---|---|---|
| Dispensa e vista web | 8-11 pt | `lesson` |
| Slide e frame video | 10-14 pt | `slide` |

Il corpo di partenza è misurato, non assunto: per Mermaid lo misura
Chromium nella pagina del pre-render, per DOT, Vega-Lite e `function` lo
legge Python dagli attributi e dal foglio di stile dell'SVG; se non è
risolvibile si usa la costante del formato
(`figure_scale.FALLBACK_BASE_FONT_PX`) e la figura entra nel report con
`font_source="constant"` (il suo `in_band` è un'ipotesi). La larghezza va
sul contenitore come ultimo attributo `style="width:Wmm"`: **l'SVG non è
riscritto** e `THEME_VERSION` resta invariato.

Ogni figura è loggata con `figure_fit`; fuori banda aggiunge
`figure_fit_out_of_band` e ogni lezione chiude con un `figure_fit_report`
(totale, in banda, fuori banda con corpo e provenienza del font, figure
sulla costante, difetti di geometria, misure saltate). Quel report è
l'input del gate editoriale D13.

Misura di riferimento su un flowchart Mermaid v11: il testo passa da 13,1
a 11,0 pt in dispensa, da 19,9 a 14,0 pt nelle slide e da 18,3 a 11 pt a
schermo. Sui 57 modelli degli editor restano sotto i 10 pt nelle slide
12/15 Mermaid, 22/24 Vega-Lite e 1/18 DOT: sono accettati e segnalati dal
`figure_fit_report`, perché portarli in banda richiederebbe un corpo per
superficie, cioè un cambio di `THEME_VERSION`.

### Box della figura per pagina

Il cap fisso `max-height: 80mm` del template non esiste più. Il modulo
puro `slide_geometry.py` (specchio delle costanti CSS del template, ogni
campo con il riferimento alla riga e un test a regex che li tiene
allineati) calcola il box su **ogni pagina resa**, dopo la decisione di
split:

- `page_figure_budget` parte dai 120 mm di `.slide-body` (255 × 120 mm,
  `overflow: hidden`) e sottrae tag, titolo (almeno una riga), prosa se
  non vuota, tutti i bullet, i margini degli asset e 3 mm di safety, poi
  divide il resto in parti uguali fra i blocchi asset della pagina. Sotto
  il pavimento del blocco (25 mm di immagine più margine e una riga di
  didascalia) il budget è portato al pavimento e la pagina è dichiarata
  impossibile con `slide_figure_box_exhausted` (a sbordare è il testo, non
  la figura); una pagina con più blocchi, caso solo legacy, è segnalata con
  `slide_figure_box_shared`.
- `image_box` ne ricava il box dell'immagine sottraendo la didascalia
  stimata sul testo reale (etichetta, didascalia dell'autore, coda
  calcolata di `function`), arrotondato per difetto al decimo di mm; se
  scende sotto i 25 mm con un budget non clampato lo segnala
  `slide_figure_caption_squeezed`.
- Il box esce come `--figure-w`/`--figure-h` nello `style` del
  `<figure>`: il wrapper `.figure-body` prende `width: var(--figure-w)` e
  le figure `max-height: var(--figure-h)`; il fit D10 usa quel box al posto
  della costante. Equazioni ed esempi non ricevono box e nella regola
  generica ripiegano sul cap di prima (`var(--figure-h, 80mm)`), così come
  le tabelle degradano ad `auto` sulla larghezza. Nessun cambio
  tipografico.

Con titolo su una riga l'immagine passa da 80 a 86,6 mm. Le pagine che
prima sbordavano (slide dedicata di Fase 4 con titolo e prosa su tre
righe, +9,7 mm; cinque bullet, +25,9 mm; due figure, +15,3 mm) rientrano,
verificate in WeasyPrint e in Chromium da
`tests/test_slide_figure_geometry.py`.

Lo stimatore delle righe (`estimate_lines`) è un **limite superiore**
calibrato `reale ≤ stima ≤ reale + 1` sui `LineBox` di WeasyPrint per sei
famiglie di font: larghezze per classe di carattere, non una media. Limite
dichiarato: una sequenza artificiale di sole `m`/`w`/`W` può restare
sottostimata di una riga.

I frame video ereditano lo stesso HTML e quindi lo stesso box: nessun
codice proprio (vedi [12 — Lesson video](12-lesson-video.md)).

### Fallback della figura non resa

Quando l'SVG manca il blocco mostra il sorgente in un `<pre>`. Deviazione
dichiarata rispetto al progetto B6: il `<pre>` è a `white-space: pre` e
non va a capo, `overflow: hidden` taglia a destra le righe lunghe. Sei
giri di revisione della stima delle righe a capo di `pre-wrap` sono stati
battuti ogni volta da un caso nuovo (spazi, tabulazioni, U+2028, profili
di lingua, regole UAX #14 di Pango); senza a capo una riga sorgente è una
riga resa, esatta in WeasyPrint e in Chromium e indipendente dal font.

Resta l'altezza della riga, che dipende dalla lingua del corso e dagli
script presenti:

| Riga resa | Budget |
|---|---|
| Solo insieme base (`_MONO_BASE_RE`) con lingua neutra | 1,30 em |
| Fuori dall'insieme base o lingua non neutra | 1,70 em |
| Prima run con baseline ideografica | 2,46 em |

Fanno eccezione due lingue del corso, la cui riga base è più alta del
budget generale: `mn-cn` 1,83 em (anche una riga con mongolo tradizionale,
in qualunque corso) e `tcy` 1,76 em.

`truncate_fallback_source` taglia il sorgente alle righe che entrano nel
box (WeasyPrint ignora `max-height` sul `<pre>` frammentato dal fondo
pagina), normalizza CRLF, CR, U+2028 e U+2029 a `\n` perché i due motori
rendano le stesse righe, chiude con «…» visibile e logga
`figure_fallback_truncated` con il numero di righe omesse.

### PDF già materializzati

Nessuna di queste regole tocca i PDF slide già esportati: restano sui
byte di prima finché qualcuno non li rigenera a mano. La rigenerazione è
la stessa di sempre, per lezione — «Rigenera PDF» nel kebab della riga
(`POST /lessons/{lesson_id}/slides-pdf/export`, ammesso con
`slides_pdf_status` ∈ `empty | ready | failed`) o «Esporta PDF tutto»
(`POST /lessons-slides-pdf/export-all`). Nessun backfill e nessuna
invalidazione automatica: `THEME_VERSION` è invariato, quindi neppure la
cache degli SVG si svuota.

## Rendering di titolo, prosa, bullet e riferimenti (WP4)

- **Autoescape.** L'env Jinja del PDF slide
  (`course_lesson_slides_pdf_service._jinja_env`) usa
  `select_autoescape(enabled_extensions=("html", "xml", "j2"))`, come la
  dispensa: prima `["html", "xml"]` non riconosceva l'estensione reale
  `.j2` e titolo, prosa e bullet uscivano crudi (un `<script>` nel titolo
  arrivava intatto anche ai frame video). `asset_html` resta `|safe`: il
  markdown di esempi, equazioni e tabelle ammette HTML come nella
  dispensa, e nei frame video lo neutralizza il JavaScript spento
  (12-lesson-video, «Guardia di rete»).
- **Rimandi agli asset nella prosa (WP8).** `title`, `body` e ogni
  `bullets[]` passano PRIMA da `AssetRefs.cite`: un `[FIG:iter]` lasciato
  dal modello nella prosa diventa «Figura 1» invece di restare letterale
  sulla slide. I numeri sono quelli della DISPENSA
  (`base_pdf.lesson_asset_refs(content_raw, language=…)`, la stessa
  funzione che prepara il corpo del PDF lezione): sulla stessa figura, le
  due superfici dicono lo stesso numero. Il rimando è solo testuale — mai
  un blocco figura: la figura sulla slide arriva da `references_assets`.
  Lo stesso vale per le didascalie in una riga degli asset resi sulla
  slide (didascalia di figura e tabella, label dell'equazione, titolo
  dell'esempio), che ricevono il `cite` dentro i renderer di blocco
  condivisi con la dispensa. Un tag che nessun numero risolve — id
  inesistente, oppure asset dichiarato solo in Fase 4, che la dispensa non
  numera — resta com'è, senza rimando inventato, e la slide emette
  `log.warning("slide_asset_ref_unresolved", lesson_code, slide_id,
  tags=[…])` UNA volta. L'elenco `tags` è quello dei soli campi di PROSA
  (`refs.unresolved(title, body, *bullets)`): un tag irrisolto in una
  didascalia resta letterale allo stesso modo ma NON compare nell'evento,
  perché le didascalie sono citate dentro i renderer di blocco condivisi
  con la dispensa, che non emette alcun evento. Il perimetro
  dell'avviso è quindi quello della prosa, non della slide intera.
- **Math nella prosa.** Dopo il rimando, `title`, `body` e ogni
  `bullets[]` passano da `render_markdown_inline` (testo escapato più
  formule `$..$`, `$$..$$`, `\(..\)`, `\[..\]` come SVG MathJax; nessun
  markdown ricco: un `**grassetto**` resta letterale, come nel preset zero
  dei campi inline). Il collector delle slide
  (`_math_content_for_slides(content_raw, slides_raw, language=…)`)
  raccoglie gli stessi testi come `inline_texts` e riceve la numerazione
  della dispensa in `asset_refs`, quindi vede le chiavi del testo CITATO
  come il renderer (`$a [FIG:iter] b$` → `a Figura 1 b` su entrambi i
  lati); PDF e video pre-renderizzano anche queste formule. La vista
  (`LessonSlidesView`) cita con lo stesso mirror TypeScript
  (`lib/lessonAssetRefs.ts`) e poi rende i tre campi con `InlineMath`.
- **Budget della figura.** Con formule nella prosa `page_figure_budget`
  riceve i pezzi del campo (`_prose_for_budget`: testo e
  `slide_geometry.ProseMath` con l'SVG): una formula in linea conta come
  parola indivisibile larga quanto il suo SVG e fa crescere la riga al più
  della sporgenza oltre la strut; una formula a blocco aggiunge la sua
  altezza. I limiti (ex ≤ 0,58 em, A − D del font fra 0,5 e 0,9) coprono le
  sei famiglie del template; il budget resta un limite superiore
  (calibrazione in `test_slide_figure_geometry.py`, fuori modello i font con
  la x più alta di 0,58 em, per esempio Impact).
- **Riferimenti duplicati.** Un asset citato più volte dalla stessa slide
  (anche con maiuscole o spazi ai bordi diversi) è reso una volta; ogni
  ripetizione produce `log.warning("slide_duplicate_asset_ref",
  lesson_code, slide_id, asset_id)`. Una sola chiave di confronto ovunque,
  minuscolo e senza spazi ai bordi: `_asset_ref_key` nel lookup del PDF
  (`_resolve_asset_for_slide`), `_slide_visual_refs` e il passo 4 nel
  CRUD, il punto 6 del percorso AI, `assetRefKey` in `lib/slides.ts`
  (`resolveAsset`, editor e vista). Un riferimento che il PATCH accetta,
  come `" fig_1 "`, è quindi anche reso. Editor e vista mostrano/salvano la
  lista senza ripetizioni (`uniqueAssetRefs`).
- **Limite dichiarato: il blocco teorema sborda (B6).** Il budget D12
  nasce per il box della FIGURA. Un `figure.equation` in famiglia teorema
  — enunciato più passi di dimostrazione — non passa da
  `slide_geometry.page_figure_budget`: ricade sul cap CSS
  `var(--figure-h, 80mm)` e, quando il contenuto è più alto, esce dai
  120 mm di `.slide-body`, che ha `overflow: hidden`. La prova di consegna
  lo ha misurato su tutte e tre le lezioni rappresentative, **identico
  prima e dopo il branch e senza alcun log**: pagina 3 di L1 **+12,12 mm**,
  pagina 3 di L2 **+19,30 mm**, pagina 3 di L3 **+12,39 mm** oltre i
  120 mm. Con l'overflow nascosto il contenuto in eccesso — tipicamente la
  fine della dimostrazione — sparisce in silenzio. NON è corretto qui: B6
  tiene il cap del teorema invariato, e cambiarlo significa rifare il
  budget per un blocco di testo (non per un'immagine), con effetti su ogni
  slide di teorema già impaginata. Vedi anche
  [17 — § 20.6](17-visual-figures.md#206-limiti-dichiarati-e-rischi-residui-del-branch).
- **Asset `image` con URL assoluto.** Il resolver lo restituisce così com'è
  e il blocco lo scrive in `src` con l'escape HTML dell'attributo: un `"`
  nel `content` (testo libero del PATCH) non apre attributi nuovi.
- **Template.** I `tpl.*` in contesto CSS sono colori hex validati e numeri
  (`|safe`) o stringhe CSS (`font_family`, `url("…")` dello sfondo:
  filtro `css_string`); i loghi in `src` restano all'escape HTML
  dell'attributo, che il parser toglie (l'URL richiesto è identico).

## Frontend — `CourseLessonSlidesView.tsx`

Tab "Slide" (settimo tab del wizard). Abilitata in `mode === "edit"` con
gating **data-based**: ∃ almeno una lezione con `content_status = 'approved'`
(niente liste di `course.status`).

Componenti:
- **Header**: aggregate progress + ETA via `useBatchEta`, CTA batch
  (Genera tutto / Rigenera / Genera mancanti / Approva tutto / Annulla,
  + Esporta PDF tutto). `canStartGeneration` / lezioni mancanti /
  eleggibili / empty-state sono calcolati su `content_status = 'approved'`
  secco (i18n `courses.lessonsSlides.contentNotReady`: "Approva prima le
  dispense…")
- **Module card** per ciascun modulo, con lista lezioni
- **Lesson row** espandibile:
  - status badge + primary CTA (Genera → Approva → Modifica)
  - kebab menu (Rigenera, Rigenera PDF)
  - progress live + phase
  - `<StalenessAlert kind="slides">` quando `isSlidesStale === true`
  - `<ApprovalBadge level="lessonSlides">` quando approved
  - Expanded: `<LessonSlidesView slides={slides_raw} contentRaw={content_raw} />`
- **Dialogs**: `LessonSlidesGenerateDialog` (4 modes con hint),
  `LessonSlidesEditDialog` (editor verticale slide + new_assets),
  `LessonSlidesPdfExportDialog` (selettore template `slide_templates`)

## File rilevanti

```
backend/app/services/openai_lesson_slides_service.py   # OpenAI call + JSON schema + REGENERATION_SUFFIX
backend/app/services/course_lesson_slides_worker.py    # worker async + auto-retry + atomic claim _inflight
backend/app/services/course_lesson_slides_service.py   # orchestrazione + materialize + 8 validazioni §7.4
backend/app/services/course_lesson_slides_crud.py      # PATCH manuale + validazioni allentate
backend/app/services/slide_geometry.py                 # box della figura per pagina + stima righe + troncatura del fallback
backend/app/services/figure_scale.py                   # fit della larghezza dalla banda 10-14 pt (slide) e 8-11 pt (dispensa)
backend/app/schemas/course_lesson_slides.py            # LessonSlidesOutput + LessonSlideItem + LessonSlideNewAsset
backend/app/api/v1/courses.py                          # 7 endpoint Fase 4 (generate / generate-all / generate-missing / cancel-all / approve / approve-all / patch)
frontend/src/api/courses.ts                            # coursesApi.lessonSlides + tipi
frontend/src/pages/org/courses/components/
  ├── CourseLessonSlidesView.tsx                       # vista batch + per-lezione
  ├── LessonSlidesView.tsx                             # render read-only (card per slide)
  ├── LessonSlidesEditDialog.tsx                       # editor manuale
  └── LessonSlidesGenerateDialog.tsx                   # dialog generate/regenerate (4 modes)
```

## Errori comuni

Vedi tabella completa in [05 — API reference](05-api-reference.md). Più
frequenti:

- `lesson_slides_count_out_of_range` — il modello AI ha generato troppe/troppo poche slide. Risolvere con `regeneration_hint` esplicito sul numero.
- `lesson_slides_unknown_asset_ref` — `references_assets[i]` punta a un asset non risolvibile. Quasi sempre causato da edit manuale post-AI che ha rimosso un asset. Aggiungere il `new_assets[]` o rimuovere il riferimento.
- `lesson_content_not_ready_for_slides` — la dispensa della lezione non è `approved`; tornare a Fase 3 e approvarla.
- `lesson_slides_multiple_visual_assets` — il PATCH aggiunge o sostituisce una figura o tabella su una slide che ne avrebbe più di una (o crea una slide con due): una slide per asset visivo. Le slide storiche con più figure restano come sono e si possono solo ridurre (togliere figure passa, sostituirle no).
- `lesson_slides_invalid_new_asset` — un `new_assets[]` modificato a mano non supera il validatore del suo formato (spec Vega-Lite senza `clip`/`scale.domain`, tipo Mermaid escluso, attributo DOT che legge file, spec `function` incoerente): `meta.errors` indica asset e campo.
- `OpenAILessonSlidesError` con finish_reason=length — output troncato, alzare `OPENAI_LESSON_SLIDES_MAX_TOKENS`.


## Figure di fonte e `tikz` nelle slide (feat/literature-figures)

- Il JSON di Fase 3 dato a PROMPT 5 passa da `figure_provenance.prompt_view`:
  - niente UUID delle figure di fonte;
  - per le `tikz`, al posto del sorgente, «(schema TikZ; etichette: …)».
- Le slide referenziano le figure di fonte della dispensa e non possono
  crearne fra i `new_assets`; lo stesso vale per `tikz`
  (`_SLIDES_EXCLUDED_FORMATS`).
- La riparazione deterministica **8c** (`FIGURE_SLIDES_COVERAGE_REPAIR_ENABLED`)
  aggiunge la slide dedicata a ogni figura di Fase 3 che nessuna slide
  cita, poi rinumera.
- **Resa**. Nella vista web la riga «Fonte» sta nella fascia in basso a
  sinistra (U2), come nel PDF delle slide e nei frame del video.

Dettagli: [18 — Figure da letteratura](18-literature-figures.md) §7.
