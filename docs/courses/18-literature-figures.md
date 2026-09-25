# 18 — Figure da letteratura: fonti del corso, letteratura aperta e schemi TikZ

Branch `feat/literature-figures` (23-24 settembre 2026). Le dispense possono
contenere **figure tratte dalla letteratura**, per esempio lo schema di un
vibrometro laser Doppler preso da un articolo del corso. Le figure sono scelte
in automatico, riprodotte ad alta qualità e numerate «Figura N.» come le
altre. Hanno sempre una riga «Fonte», calcolata a render dai metadati e mai
scritta dal modello.

Le fonti, in ordine di priorità:

1. i **documenti del corso** (PDF, DOCX, PPTX caricati), con l'estrazione in
   un worker dedicato (WP1-WP4);
2. la **letteratura aperta** (Wikimedia Commons, OpenAlex), solo quando una
   lezione ha meno di `FIGURE_SOURCE_MIN_PER_LESSON` figure pertinenti dai
   documenti (WP5);
3. il **ridisegno vettoriale** nel formato `tikz`, compilato da XeLaTeX in
   una sandbox (WP6).

Le figure di fonte **si aggiungono** a quelle generate, con due budget
separati:

- (a) le figure generate (Mermaid, Vega-Lite, DOT, `function` e `tikz`):
  4-8 per lezione ordinaria, 0-2 per l'introduttiva, come prima;
- (b) le figure di fonte: 1-4, e solo se pertinenti.

Piano approvato:
`~/.claude/plans/quirky-snuggling-newell.md`. Configurazione in
[04 — Configurazione](../04-configuration.md). Prompt in
[PROMPTS.md](../PROMPTS.md): PROMPT 3, 5, 6 e 12 modificati, 18-21 nuovi.

## 1. Stato e accensione

| Parte | Codice | Default nel codice | Default in `.env.example` / compose |
|---|---|---|---|
| Catalogo delle figure di fonte in Fase 3 | WP1-WP4 | `FIGURE_SOURCE_ENABLED=true` | `true` |
| Estrazione dai documenti (Docling) | WP2 | `FIGURE_EXTRACTION_ENABLED=true` | **`false`**: si accende dopo la misura M0 sulla VM |
| Revisore delle ridondanze (PROMPT 19) | WP3 | `true` | `true` |
| Letteratura aperta (buchi) | WP5 | `FIGURE_LITERATURE_ENABLED=true` | **`false`** |
| Formato `tikz` (editor, resa) | WP6 | `FIGURE_TIKZ_ENABLED=false` | `false`; serve l'immagine con TeX (`--build-arg INSTALL_TEX=true`) |
| Proposta di `tikz` da parte del modello | WP6.3 | `FIGURE_TIKZ_PROPOSE_ENABLED=false` | `false` |

Con tutto spento il comportamento è quello di `main`. Il catalogo resta
vuoto, e allora PROMPT 3 (system, messaggio user e schema) è
**byte-identico** a prima (test I1). Ogni parte ha un kill-switch.

## 2. Architettura in breve

```
documento caricato ──► course_document_worker (testo + riassunto, invariato)
        │
        └─► course_document_figures_worker ──► processo figlio (Docling o euristico)
                 │  ritagli PNG/JPEG sotto /uploads/courses/{cid}/document_figures/…
                 │  Vision descrittiva (PROMPT 18), costo in vision_usage
                 ▼
        course_document_figure (catalogo del corso, licenza e provenienza sempre registrate)
                 │
   lezione in Fase 3 ──► source_figure_catalog.build_catalog ──► lesson_figure_selection
                 │        (top 8 nel messaggio user, id SRC-<hex8>)
                 │  buco (< MIN pertinenti) ──► course_lesson_figures_gap_worker ──► Wikimedia / OpenAlex
                 ▼                                   (PROMPT 20, prima della Fase 3)
        PROMPT 3 ──► source_figures[] ──► fusione in visual_assets (format="source_figure",
                 content = UUID) ──► validazione/fix ──► revisore PROMPT 19 (solo avvisi)
                 ▼
        resa: resolver lato server (source_figure_service) ──► dispensa, PDF di modulo,
        PDF slide, frame video, vista ed editor ── sempre con la riga «Fonte»
```

- **Riferimento vivo** (J-Q1). L'asset `source_figure` porta l'UUID della
  riga `course_document_figure`. Byte e riga «Fonte» li dà solo il resolver
  del server (`resolve_source_figures`), che filtra per `course_id`.
  L'attribuzione non viene mai dal client né dal modello.
- **Predicato unico** `source_figure_policy.figure_visibility(…, mode)`:
  - `select`: catalogo, PATCH di asset nuovi o cambiati, ricontrollo
    TOCTOU in generazione;
  - `render`: figure già collocate, solo condizioni strutturali (U1).
- **Numerazione**. Le figure di fonte stanno in `content_raw.visual_assets`,
  quindi numerazione, rimandi `[FIG:id]`, slide e localizzazione funzionano
  come per le altre figure.

## 3. Modello dati (migrazioni 0037 e 0038)

- **`course_document`, provenienza**:
  - `origin` (`upload | paper_import | paper_metadata`), `is_own_work`;
  - `license` e `license_source`;
  - `bibliography` (JSONB `DocumentBibliography`) e `bibliography_source`
    (`user | openalex | pdf_metadata | crossref | summary_proposal`).
- **`course_document`, estrazione**: `figures_status`
  (`pending | processing | ready | failed | skipped`; NULL = mai chiesta,
  **nessun backfill**), `figures_error_code` (16 codici), avanzamento
  (`figures_pages_done/_total`), `figures_coverage` (`full | partial`),
  statistiche.
- **`course_document_figure`**:
  - `course_id`, e `document_id` con FK `ON DELETE SET NULL` più
    `detached_at`;
  - `source_kind` (`uploaded | openalex | wikimedia`), `locator`, pagina,
    bbox, didascalia e contesto;
  - file (`storage_path`, anteprima, dpi, `phash`) e dati Vision;
  - `license` **NOT NULL senza default**, `license_url`, `attribution`
    congelata per le staccate e le esterne;
  - `excluded_by_user`, `status` e `reject_reason`, `duplicate_of_id`;
  - con la 0038: `external_id`, `source_url`, `retrieved_at` e un indice
    unico parziale sulle figure esterne.
- **`course_lesson`**:
  - `content_figure_review`, i verdetti del revisore: li crea solo la
    materializzazione, il CRUD li pota soltanto;
  - con la 0038, lo stato dei buchi: `figures_gap_status/attempts/…/usage/stats`.
- **`organization_course_settings`**: `figure_source_license_policy`,
  override per organizzazione.

CHECK e indici sono dichiarati anche nei modelli, perché i test creano lo
schema con `create_all`. La parità modello↔migrazione è verificata da
`test_source_figure_model`.

## 4. Estrazione dai documenti (WP2)

- **Worker gemello** `course_document_figures_worker` (J-Q3):
  - lavora un documento alla volta, con UPDATE condizionali, backoff e
    checkpoint per blocchi di pagine;
  - gli errori recuperabili tornano a `pending`, fino a
    `FIGURE_EXTRACTION_AUTO_RETRY_MAX`;
  - l'estrazione non tocca il riassunto (G5).
- **Processo figlio** (`document_figures/child.py`, `runner.py`):
  - ambiente in allowlist, senza segreti; `start_new_session` e killpg;
  - watchdog di RSS e di tempo; riciclo ogni `…_PAGES_PER_CHILD` pagine;
  - `MemAvailable` controllato prima di ogni blocco;
  - `heavy_job_lock` condiviso con la compilazione TeX;
  - il figlio non tocca mai lo storage: il padre scarica e carica.
- **Motori**:
  - `docling`, il default: solo layout (heron), OCR spento; il ritaglio
    lo fa pypdfium2 sul bbox;
  - `heuristic`: pdfplumber + pypdfium2, senza torch, di qualità minore;
    pensato per CI e per una decisione esplicita;
  - DOCX e PPTX si leggono con zipfile e XML.
- **Ritaglio**:
  - vettoriali a dpi adattivo fra 300 e 600, lato lungo verso 2400 px,
    tetto 12 MP (D21, deviazione dichiarata dal 200-300 dpi del brief);
  - raster al ppi nativo, fra 150 e 300.
- **Filtri**: dimensioni, proporzioni, vuoti, testate e piè di pagina,
  rumore del rilevatore, figure ripetute, duplicati (`phash`).
- **Vision descrittiva** (PROMPT 18, `gpt-4.1-mini` a 768 px, M4):
  produce tipo, descrizione, parole chiave (lingua del corso + inglese),
  qualità, leggibilità e utilità. Il costo cumulativo sta in
  `vision_usage` e compare nella dashboard admin, fase `document_figures`.
- **Provenienza**, da fonti deterministiche, in quest'ordine:
  1. il docente;
  2. OpenAlex;
  3. i metadati del PDF (Info/XMP) o `core.xml` di DOCX/PPTX;
  4. Crossref (DOI trovato nelle prime pagine).

  Il riassunto dell'LLM è solo una **proposta** (`summary_proposal`),
  mostrata in UI da confermare, e non entra mai nella riga.
- **Documenti esclusi**. Da `excluded` non si estrae nulla (`skipped`
  con il motivo). Le fonti riservate (`content_only`) si estraggono come
  materiale del docente (§20).
- **Richiesta dell'estrazione**. Endpoint `…/figures/extract` e script
  `scripts/extract_document_figures.py` (dry-run di default; opzioni
  `--apply`, `--retry-failed` e `--gc-detached`). Non c'è backfill automatico (G10).

## 5. Attribuzione e licenze (WP1)

- **Punto unico**: `figure_attribution.attribution_line(src, language,
  mode="written"|"spoken", adapted=False)`.
  - Testi it/en, con ripiego su it.
  - «et al.» oltre 3 autori; la licenza si omette se `unknown`.
  - La variante parlata garantisce `validate_tts_safety(...) == []`.
- **Esempio** reale della prova §7.3: «Fonte: Docente di Prova, «Dispensa
  di vibrometria laser», fig. 2.1, p. 1».
- **Politica** `FIGURE_SOURCE_LICENSE_POLICY`, con override per
  organizzazione:
  - `cite_all`: qualunque licenza, sempre con attribuzione completa;
  - `open_only`: solo CC0, pubblico dominio, CC BY e CC BY-SA.
- **Nota legale**. `cite_all` presuppone che l'uso rientri nelle eccezioni
  per citazione e insegnamento (artt. 70 e 70-bis L. 633/1941). La
  valutazione legale spetta al committente e non blocca il rilascio:
  `open_only` è l'opzione prudente.
- **Crediti**. Le figure esterne CC hanno in fondo alla dispensa
  l'appendice «Crediti delle figure»: autore, titolo, licenza (con
  versione e URI per Wikimedia; OpenAlex dà solo il codice) e fonte.
- **Figure di terzi** (Fase D). Una didascalia originale con un credito
  («Reprinted from…», «©», «courtesy of», «Fonte: …») rende la figura
  `unknown`: non eredita la licenza del documento ed esce da `open_only`.
  Il credito entra nella riga «Fonte». Le candidate OpenAlex di terzi
  sono scartate.

## 6. Selezione e PROMPT 3 (WP3)

- **Selezione** `lesson_figure_selection` (J-Q2):
  - lessicale, gemella di `lesson_document_selection`, sulle parole chiave
    Vision nella lingua del corso e in inglese;
  - soglie «candidata» e «pertinente» (quest'ultima conta per i buchi);
  - lezioni di verifica escluse.
- **Prompt**. Catalogo di al più 8 voci e 4000 caratteri nel **messaggio
  user**, dentro i delimitatori di dati; nel system entra solo una riga
  statica (M6).
- **Schema**. `source_figures[]`, con l'enum degli id `SRC-…`;
  `visual_assets` non cambia mai.
- **Budget (b)**. È una riga a parte, dopo la misura M7: «Prima decidi le
  figure generate come se il catalogo non ci fosse: stesso numero, stesse
  sezioni e stessi formati…».
- **Fusione** `source_figure_fusion`:
  - le scelte diventano asset `source_figure`; gli asset generati con il
    prefisso `SRC-` vengono rinominati in modo deterministico;
  - il ricontrollo TOCTOU ha un audit;
  - `source_figures` viene svuotato ed escluso dai dump.
- **Revisore** (PROMPT 19, `gpt-4o-mini`):
  - per ogni figura di fonte dà coerenza e coppie
    `distinta | complementare | ridondante`;
  - **segnala soltanto**, con un avviso sulla card nell'editor, e non
    tocca mai `content_raw`;
  - errore o timeout valgono «nessun avviso»; il costo va in
    `content_tokens.assets` (`phase="redundancy"`).
- **Attesa delle estrazioni**. Il `_tick` di Fase 3 aspetta le estrazioni
  in corso con un filtro SQL (`FIGURE_WAIT_MAX_MINUTES`), mai con uno
  sleep.

## 7. Superfici (WP4)

- **Dispensa e PDF di modulo**. La riga «Fonte» sta nel `<figcaption>`
  (partial unico; senza figure di fonte è byte-identico).
- **PDF slide e frame video** (U2). Fascia `.slide-attribution` in basso a
  sinistra (x 18-212 mm), fuori dall'avatar, alta 15 mm su 4 righe:
  - con 1-2 figure, 2 righe per figura;
  - con 3-4 figure, 1 riga per figura;
  - dalla quinta, segnaposto.

  Il testo è adattato alla larghezza (`fitted_written_line`). Una figura
  di fonte senza riga diventa segnaposto: mai l'immagine senza fonte.
- **Fase 4**:
  - la riparazione deterministica 8c aggiunge la slide mancante per ogni
    figura di Fase 3 (`FIGURE_SLIDES_COVERAGE_REPAIR_ENABLED`);
  - il JSON dato al modello passa da `figure_provenance.prompt_view`
    (niente UUID).
- **Fase 5**. La fonte si dice a voce una volta, dal blocco
  `FONTI DELLE FIGURE` (frasi neutralizzate con `safe_spoken_text`).
- **Frontend**:
  - `SourceFigure.tsx` è il punto unico: immagine come Blob
    dall'endpoint autenticato, mai l'URL dello storage;
  - `SourceFigurePicker` sceglie dal catalogo;
  - badge, polling ed estrazione nell'uploader; `DocumentSourceDialog`
    per bibliografia, licenza e «proprio»;
  - politica di organizzazione in `CourseSettingsPage`.
- **Editor**:
  - azioni Rimuovi, Escludi, Sostituisci e ↑/↓;
  - il riordino segue la prima citazione `[FIG:id]`.
- **PATCH della dispensa**:
  - asset nuovi o cambiati → 422 `source_figure_not_in_course` o
    `source_figure_not_available` (con `reason`, anche
    `resolution_unusable`, §22), e `source_figure_format_locked`;
  - la stessa figura due volte nella lezione → 422
    `source_figure_duplicate_in_lesson`, sulla card che la porta di nuovo
    (i doppioni già salvati passano);
  - una figura nuova già in K altre lezioni è ammessa, con audit
    `course.lesson.content.source_figure_over_cap` (§22);
  - gli asset invariati sono ammessi (U1).

## 8. Ciclo di vita e duplicazione

- **Non retroattività** (U1). Cambio di politica, esclusione del docente e
  passaggio a `open_only`:
  - agiscono subito su catalogo, PATCH e ricontrollo in generazione;
  - le figure già collocate restano, con la loro riga, finché la lezione
    non viene rigenerata.

  Dal 24/09/2026 le fonti riservate sono materiale del docente (§20): la
  figura di un documento diventato `content_only` resta, e la sua riga
  scritta diventa subito «materiale del docente», perché si calcola al
  render.
- **Cancellazione del documento**:
  - le figure **usate** vengono staccate: riga e file restano,
    `document_id → NULL`, attribuzione congelata;
  - le non usate si cancellano;
  - la FK è `SET NULL` invece del cascade del brief (deviazione
    dichiarata).
- **Duplicazione**:
  - clona documenti (provenienza compresa), figure (anche staccate ed
    esterne) e file;
  - rimappa `content_raw` e `content_figure_review`;
  - traduce descrizione e parole chiave;
  - controprova G6: stessa riga «Fonte» prima e dopo;
  - il sorgente `tikz` si copia com'è (stesso SVG).

## 9. Letteratura aperta (WP5)

- **Buchi**. Una lezione ordinaria con meno di `FIGURE_SOURCE_MIN_PER_LESSON`
  figure pertinenti dai documenti riceve figure esterne **prima della
  Fase 3**, anche se il corso non ha documenti o ha PDF senza figure (I2).
  - Lo fa `course_lesson_figures_gap_worker`: più lezioni per tick,
    backoff, reset che conta come tentativo.
  - Il worker di Fase 3 marca il buco e attende entro
    `FIGURE_WAIT_MAX_MINUTES`.
  - Non si crea **nessun** `CourseDocument`: riassunti, selezione dei
    documenti e riferimenti della lezione restano invariati.
- **Download**, `safe_http.fetch`:
  - IP risolto e fissato (Host/SNI), redirect ricontrollati;
  - IPv6 NAT64/SIIT/site-local bloccati, IDN in punycode, DNS dentro la
    scadenza totale;
  - `Content-Encoding` rifiutato e byte grezzi letti (niente bombe
    compresse), sniffing del tipo;
  - header del chiamante tolti sui redirect fra host diversi.
- **Limiti delle immagini**, `image_limits`: pixel controllati prima della
  decodifica (16 MP) e PDF oltre il limite di pagine rifiutati.
- **Wikimedia Commons**:
  - solo licenze aperte; `Restrictions` scartate;
  - campo `Attribution` come credito, versione della licenza;
  - PNG reso da Commons: anche per gli SVG si chiede il PNG, in
    deviazione dal «preferire l'SVG» del piano.
- **OpenAlex**:
  - `OPENALEX_API_KEY` obbligatoria dal 13/02/2026: senza, i buchi usano
    solo Wikimedia;
  - solo lavori con PDF nella `best_oa_location` e licenza della
    **stessa** location fra CC BY, CC BY-SA, CC0 e pubblico dominio;
  - PDF elaborato dallo stesso processo figlio, tenendo solo i ritagli;
  - la chiave non compare nei log (httpx a WARNING).
- **Import dei paper**. `/papers/import` rilegge il lavoro lato server e
  scarica solo il PDF del server via `safe_http`: la SSRF preesistente è
  chiusa.
- **PROMPT 20**:
  - termini di ricerca in inglese, una chiamata per lezione;
  - pertinenza e descrizione Vision per candidata (al più 8);
  - costo in `course_lesson.figures_gap_usage`, fase admin `figures_gap`.

## 10. Formato `tikz` (WP6)

- **Motore** (J-Q4): XeLaTeX di TeX Live Debian, poi PDF, poi
  `pdftocairo -svg`, poi `normalize_svg`.
  - Niente dvisvgm né Ghostscript (AGPL): la guardia del Dockerfile
    rifiuta `libgs`.
  - Il preambolo è fisso: font Noto Sans/CJK scelto dal contenuto, colori
    `a4u*`, librerie ammesse, `circuitikz` europeo e `pgfplots` solo se
    usati.
- **Lexer ad allowlist** (`tikz_lexer`). Un solo ambiente esterno. Sono
  ammesse solo le control word dell'elenco; sono vietate le chiavi che
  eseguono codice, `^^`, `@` e `#`. Ci sono tetti su caratteri, graffe,
  nodi, tracciati, `samples` e `\foreach`.
- **Sandbox SBX-2** (`tex_sandbox.py`, `tikz_compile_service`):
  - `python -I` con `setrlimit` ed `execv`;
  - ambiente in allowlist; `openin_any=p`, `openout_any=p`,
    `shell_escape=f`; killpg al timeout;
  - una compilazione alla volta, mai durante un'estrazione Docling;
  - autotest all'avvio, a guasto chiuso.

  SBX-1 (container dedicato) non è stato fatto.
- **Oracolo geometrico** (`tikz_geometry`, pdfplumber sul PDF). Difetti
  rilevati:
  - `labels_overlap` (per glifo);
  - `text_outside_owner`;
  - `edge_crosses_label`;
  - `content_outside_page`;
  - `text_small`, cioè testo sotto 7 pt alla scala della dispensa, con
    pedici e apici riconosciuti dal glifo vicino (commit fa47802).

  In generazione i difetti bloccano; nell'anteprima e nel PATCH sono
  avvisi.
- **Fase 3** (WP6.3). `phase3_visual_formats` offre `tikz` solo se valgono
  insieme tre condizioni:
  - proposta accesa;
  - script della lingua coperto dai font;
  - nessun `tikz_unresolved` al tentativo precedente (marcatore in
    memoria del worker).

  Se non è offerto, system, messaggio e schema sono byte-identici. Quando
  è offerto:
  - nel messaggio user entra il blocco `## Formato aggiuntivo: tikz`;
  - `tikz` conta nel budget (a);
  - ha un solo fix (PROMPT 12, variante `tikz`);
  - sandbox occupata o motore assente non vanno al fix.
- **Revisione Vision della resa** (PROMPT 21, consultiva). Usa il PNG a
  150 dpi. Con difetti, spende l'unico fix solo se la riscrittura passa
  la validazione severa. Il costo va in `content_tokens.assets`.
- **Endpoint e frontend**:
  - `render-tikz`, anteprima con quota per utente;
  - `tikz-view`, che rende solo sorgenti già salvati nel corso;
  - `formats`;
  - `TikzFigure` per vista e slide; `TikzEditor` con 4 modelli (catena di
    misura, vibrometro, partitore, ponte di Wheatstone) provati dal test
    dei template;
  - la voce di menu compare solo nella dispensa e solo se il server rende
    `tikz`.
- **Fase 4 e 5**. `prompt_view` sostituisce il sorgente TeX con
  «(schema TikZ; etichette: …)».

## 11. Misure

| # | Esito |
|---|---|
| **M0** CPU/RAM della VM | **Non eseguita** (a cura dell'utente). Bloccante per `FIGURE_EXTRACTION_ENABLED=true` in produzione |
| **M1** fonti reali in produzione | **Non eseguita** (SQL in sola lettura nell'Appendice B del piano, a cura dell'utente) |
| **M2** Docling su PDF reali | arXiv 2402.10966 e 2304.11054, OCW 20.309, OpenStax. **Tempi** (arm64 nativo, 1 thread): 2,0-2,6 s/pag; RSS del figlio 1,35-1,76 GB. **Motore euristico**: 0,06-0,32 s/pag. **Richiamo** sulle figure con didascalia: Docling 0,93, euristico 0,90. **Precisione**: 0 falsi positivi su 45 ritagli. **Didascalie** esatte dopo la correzione. p90 dei byte 30-126 KB. **Proiezione sulla VM** senza M0 (k = 6): 11-15 s/pag, oltre la soglia di 4 s → decisione al cancello |
| **M3** delta dell'immagine | Stima arm64 ~1,39 GB espansi / ~0,48 GB compressi (build completa non eseguita: disco di Docker pieno). 134 pacchetti, nessun AGPL, CUDA, triton od OpenCV |
| **M4** Vision descrittiva | 49 ritagli: tutti i modelli passano. Scelto **gpt-4.1-mini a 768 px**: kind 0,94-0,98, useful 0,98-1,0, p95 3,6-4,8 s, ~0,0007 $/figura; con il tetto di 80 figure descritte per documento un manuale costa ≈ 0,06 $ (senza tetto, 210 figure in 300 pagine: ≈ 0,15 $). Oltre il tetto le figure non entrano nel catalogo: dalla Fase D si scelgono prima quelle con didascalia, distribuite su tutto il documento |
| **M5** TikZ | TeX Live Debian per XeLaTeX: **+550 MB** (pdflatex +393 MB), oltre soglia → TeX solo con build arg. Nessuna libgs. **Spike di generazione** (gpt-5.5, reasoning high, blocco `tikz` reale; 5 schemi × 3: catena di misura, vibrometro, ponte di Wheatstone, anello di controllo, condizionamento di una termocoppia; 1,77 $): lexer **15/15** (soglia 85%); geometria pulita **8/15** (anello 3/3, Wheatstone 3/3, vibrometro 2/3, catena 0/3, condizionamento 0/3). I rifiuti, controllati sulle immagini, sono difetti veri: etichette sui riquadri, linee sulle etichette, figura più larga della pagina. Regola del piano: 8-11 su 15 → **solo editor**, proposta automatica spenta |
| **M6** system prompt | Righe statiche misurate sulla variante peggiore; guardie invariate (PROMPT 3 ≤ 31.500, PROMPT 5 ≤ 18.200, PROMPT 6 ≤ 12.500) |
| **M7** non sostituzione | Due giri: il 1° **fallito** su S3, il 2° **passato** (dettagli sotto). Costo 15,13 $ |
| **Prova §7.3** | Corso con PDF arXiv CC BY sulla vibrometria + dispensa di prova. **Estrazione**: 4 + 14 figure, Vision 0,016 $. **Dispensa**: 3 figure di fonte oltre alle 4 generate, budget (a) rispettato. **Riga «Fonte»**: presente e identica in dispensa, PDF slide e frame video. **Fascia** fino a 212 mm, avatar da 212 mm. **Slide** 0,081 $. Il PDF arXiv non ha metadati bibliografici: la riga ripiega sul nome del file |

Dettaglio di M7:

- **1° giro**: TVD dei formati 0,31 contro 0,04 fra due estrazioni senza
  catalogo; con il catalogo il modello rinunciava agli schemi.
- **Correzione**: revisione mirata della riga del budget (b).
- **2° giro**: S1 0/8, S2 Δ 0,0, S3 TVD 0,06 contro 0,13, S4 ok.

**M7 dopo WP6.3 non ripetuta**: con la proposta spenta il modello non vede
`tikz`. Va ripetuta prima di accendere `FIGURE_TIKZ_PROPOSE_ENABLED`
(`scripts/measure_source_figures.py --substitution`).

## 12. Cancello intermedio (dopo WP4): rapporto e decisioni

L'utente ha chiesto di proseguire con WP5-WP7 senza attendere. Le decisioni
del cancello sono state prese in autonomia, con l'opzione **più
conservativa e reversibile**.

| Tema | Decisione | Motivo |
|---|---|---|
| Estrazione in produzione | Spenta nel compose (`FIGURE_EXTRACTION_ENABLED=false`) | M0 non eseguita; proiezione di M2 oltre 4 s/pag |
| Motore euristico in produzione | **Non** attivato | Qualità minore sulle figure composte; serve una decisione esplicita |
| Letteratura aperta | Spenta nel compose; OpenAlex solo con `OPENALEX_API_KEY` | Rete esterna e costi: accensione esplicita |
| Sandbox TikZ | SBX-2 nel codice, SBX-1 non fatta | SBX-1 richiede una modifica del compose |
| TeX Live nell'immagine | Solo con `--build-arg INSTALL_TEX=true` | M5: +550 MB |
| Proposta automatica di `tikz` | Spenta | Spike M5 di generazione e M7 ripetuta non eseguiti |
| Font / stile circuiti | Noto Sans (latino, greco, cirillico, CJK), `circuitikz` europeo | Default del piano |

## 13. Decisioni dell'utente e giurie

| # | Decisione |
|---|---|
| U1 | Cambi di politica e cancellazioni **non retroattivi** (§8) |
| U2 | Riga «Fonte» nella fascia in basso a sinistra di slide e frame |
| U3 | PPTX fra i documenti del corso |
| U4 | Chiave OpenAI dall'ambiente (`~/.zshenv`) per M4, M7 e la prova §7.3 |
| U5 | Ritagli sotto `/uploads`, come i documenti; nomi non indovinabili (uuid + sha12). Il frontend non espone mai l'URL |
| U6 | docling-slim (MIT) + torch CPU (BSD) come extra `figures`, `pypdfium2`, Dockerfile trixie |
| U7 | Codice scritto dall'agente principale; 3 verificatori in sola lettura per WP |

Esiti delle giurie:

- **J-Q1**: riferimento vivo, `content` = UUID.
- **J-Q2**: selezione ibrida (lessicale + riordino del modello).
- **J-Q3**: worker gemello con processo figlio.
- **J-Q4**: XeLaTeX → pdftocairo, con SBX-2.
- **J-Q5**: non retroattivo (U1).
- **J-Q6**: nessun ridisegno da `content_only`. La riproduzione delle
  figure riservate come materiale del docente è una decisione successiva
  dell'utente (§20).
- **J-Q7**: Docling solo per rilevare, più ritaglio con pypdfium2.
- **J-Q8**: revisore gemello (PROMPT 19).

Motivazioni e alternative scartate sono nel piano, sezione (c).

## 14. Deviazioni dichiarate

1. **Figure delle fonti riservate** (§20, decisione dell'utente del
   24/09/2026, contro il vincolo del brief «da content_only nessuna figura
   viene riprodotta»): si estraggono e si propongono come materiale del
   docente, con una riga che non nomina mai il documento.
2. **FK `SET NULL`** con lo stacco, invece del cascade.
3. **dpi adattivo 300-600** per i vettoriali (D21).
4. **OpenAlex**: licenza della stessa location del PDF, non sempre
   `best_oa_location.license`.
5. **Wikimedia**: PNG reso da Commons anche per gli SVG.
6. **Esclusione di TikZ ribaltata** (D17). Il prompt «figure accademiche»
   escludeva LaTeX/TikZ; il committente l'ha riaperto con questo brief. Il
   formato esiste, ma è spento.
7. **«Ridisegna da figura» fuori perimetro** (D11): 3A, 3B e 3C non
   esistevano.
8. **`tikz_busy` risponde 409**, non 503 (l'API non usa 503 altrove).
9. **`prompt_view` anche in Fase 4** per i `tikz`: il modello delle slide
   vede solo le etichette, non il sorgente.
10. **Revisione Vision** della resa non rilanciata dopo il fix: contano
    solo i controlli deterministici.
11. **`storage_service._ALLOWED_ROOTS` non esteso a `courses`**: un
    `delete_directory("courses")` cancellerebbe tutti i corsi. WP2 usa un
    helper stretto per `courses/{cid}/document_figures/…`.

### Verifica di WP6 (3 verificatori in sola lettura) e correzioni

Nessun rilievo di gravità alta. Correzioni:

- **Lexer**:
  - vietati anche `.append code`, `.prefix code`, `.add code`, `.get` ed
    `.estore in` (a3d8078);
  - le variabili di `\foreach` valgono solo nella dichiarazione e nel
    corpo del ciclo; i nomi di primitive (`\input`, `\def`…) sono
    rifiutati (bdff904). Prima, `\foreach \input in {1} {}` ammetteva
    `\input{…}` in tutto il sorgente; la sandbox bloccava comunque la
    lettura;
  - un caso negativo per ogni regola.
- **Preambolo 2026.09.2** con `decorations.pathmorphing` (molle e
  smorzatori) e `shapes.arrows`. Il blocco `tikz` di PROMPT 3 elenca
  esattamente le librerie caricate (2353b63).
- **Oracolo geometrico**:
  - pedici su griglia spaziale, con catena limitata alla stessa riga e a
    6 glifi: costo lineare invece di 36 s per 960 glifi, e niente falso
    negativo su una scritta piccola accanto a un nodo grande;
  - accenti matematici esclusi dalle sovrapposizioni (2353b63).
- **Sandbox occupata** durante un'estrazione (c078596):
  - niente cache negativa;
  - PDF della dispensa, PDF slide e frame video fanno ritentare il loro
    worker invece di salvare «Figura non disponibile»;
  - vista e PATCH rispondono 409 `tikz_busy`;
  - la Fase 3 non offre `tikz` mentre gira un'estrazione;
  - registro e renderer condividono la chiave di cache.
- **Default allineati**. Messaggio e schema di PROMPT 3 usano gli stessi
  formati anche quando il chiamante non li passa; lo script di M7 li
  passa a entrambi (71f9c24).
- **Rilievi minori** (1e7b9d2):
  - un solo fix per figura anche dopo la localizzazione, con ritorno alla
    versione valida;
  - le slide non creano `tikz`;
  - nodi commentati fuori dalle etichette;
  - autotest della sandbox all'avvio, in un thread.

## 15. Sicurezza e rischi residui

- **`/uploads` pubblico** (U5; in produzione `ovh_sftp` su una docroot
  pubblica). I ritagli hanno la stessa esposizione per URL dei PDF
  sorgente. I nomi non sono indovinabili, ma un membro del corso che
  conosce il percorso può condividerlo.
- **Sandbox SBX-2**. È nello stesso container del backend: difesa a
  strati (lexer, preambolo fisso, TeX paranoico, limiti, ambiente senza
  segreti), non un isolamento del kernel. Per esporla a utenti non fidati
  l'upgrade è SBX-1.
- **Marcatore `tikz_unresolved` in memoria**. Un riavvio lo perde: al più
  un tentativo `tikz` in più, entro `course_lesson_content_auto_retry_max`.
- **PDF con `tikz` durante un'estrazione lunga**. Il worker del PDF
  ritenta (auto-retry). Un'estrazione più lunga dei suoi tentativi porta
  il PDF a `failed`, visibile e rigenerabile, mai a un PDF con il
  segnaposto.
- **Lock TeX/Docling**. La compilazione TeX attende l'estrazione, ma
  l'estrazione non attende una compilazione già in corso: il blocco è a
  senso unico. I render Chromium esistenti non sono coordinati.
- **Contenuti di terzi**. Didascalie, Vision, extmetadata di Commons,
  metadati OpenAlex ed etichette dei nodi passano da
  `prompt_safety.neutralize_third_party_text`, fra delimitatori di dati
  (test con canarino).
- **Cache degli SVG `tikz` solo in memoria**: il primo export dopo un
  deploy ricompila.

## 16. Limiti noti e lavori futuri

- **Etichette `tikz` escluse dall'estrazione**. Le etichette su più righe
  (`\\`) e le chiavi `l=`/`label=` di circuitikz non sono estratte: né
  per la traduzione né per le etichette attese dei PROMPT 5, 6 e 21.
- **Possibile falso positivo** (non confermato):
  `content_outside_page` scatta su nodi con `inner sep=0pt` al bordo
  della figura (differenza fra il box del font e quello di TeX).
- **Marcatore `tikz_unresolved`**. Resta in memoria anche dopo un
  fallimento terminale; si consuma al tentativo successivo.

- **Test e controlli mancanti**:
  - nessun test dell'SSRF a livello di endpoint `/papers/import` (c'è a
    livello di client);
  - nessun test della downgrade della 0038 (il ciclo è stato eseguito a
    mano su un DB usa-e-getta);
  - harness DOM di `FigureFrame` assente (il frontend non ha un test
    runner).
- **Ottimizzazioni rinviate**:
  - OpenAlex senza cache dei PDF per corso;
  - formato TeX precompilato non fatto.
- **Comportamenti noti**:
  - il conteggio d'uso non si aggiorna al cambio di politica;
  - slide e frame mostrano solo nome e versione della licenza, senza URI
    (l'URI è nei crediti della dispensa);
  - il discorso cita anche le fonti di documenti diventati riservati
    finché la lezione non viene rigenerata (U1); la riga scritta invece
    diventa subito «materiale del docente»;
  - nella duplicazione con traduzione, le etichette dei nodi `tikz`
    restano nella lingua di partenza, come le etichette di Mermaid, DOT e
    Vega-Lite (`visual_assets[].content` non si traduce).
- **Prima di accendere la proposta automatica di `tikz`**: spike M5 di
  generazione (≥ 12/15 schemi puliti) e M7 ripetuta.

## 17. Test (garanzie → file)

| Garanzia | File |
|---|---|
| G1 riga «Fonte» ovunque | `test_figure_attribution`, `test_source_figure_render`, `test_source_figure_band`, `test_source_figure_api`, `test_speech_source_figures`, `test_frontend_source_figures`, `test_source_figure_call_sites` |
| G2 excluded fuori da catalogo e generazione; content_only dentro come materiale del docente, senza nome del documento | `test_source_figure_materialize` (canarini, TOCTOU, nome del riservato mai nel prompt), `test_source_figure_policy`, `test_source_figure_worker` (esclusi non estratti, riservati senza titolo alla Vision), `test_figure_attribution` |
| G3 open_only | `test_license_policy`, `test_source_figure_policy` |
| G4 licenza sempre valorizzata | `test_source_figure_model` |
| G5 isolamento figure/riassunto | `test_source_figure_worker` |
| G6 duplicazione | `test_source_figure_duplication` |
| G7 IDOR | `test_source_figure_api` (altro corso, uuid casuali), `test_tikz_endpoint` (vista di un altro corso) |
| G8 ciclo di vita non retroattivo | `test_source_figure_document_lifecycle` |
| G9 costo | `test_source_figure_vision`, `test_source_figure_redundancy`, `test_source_figure_relevance`, `test_tikz_render_review` |
| G10 nessun backfill | `test_source_figure_worker` |
| G11 nessun AGPL | `test_no_agpl_dependencies` |
| G12 estrazione | `test_source_figure_extraction` (Docling nel container), `test_source_figure_filters`, `test_source_figure_fixtures`, `test_document_metadata`, `test_pptx_documents` |
| I1 non sostituzione | `test_source_figure_non_substitution`, `test_source_figure_prompt_schema` + M7 |
| I2 buchi | `test_source_figure_gaps` |
| I3 revisore | `test_source_figure_redundancy` |
| I4 Fase 4 | `test_slides_source_figures` |
| WP5 rete | `test_safe_fetch`, `test_image_limits`, `test_wikimedia_client`, `test_openalex_figures` |
| WP6 | `test_tikz_validator`, `test_tikz_sandbox`, `test_tikz_render`, `test_tikz_registry`, `test_tikz_phase3`, `test_tikz_render_review`, `test_tikz_endpoint`, `test_frontend_figure_templates` |

**Test con dipendenze pesanti**. Docling, TeX e Chromium si provano nel
container `test` del Dockerfile, con `A4U_REQUIRED_DEPS`: lì uno skip
diventa un fallimento (`tests/dep_guard.py`). In locale i test saltano
con il motivo esplicito (`[dep:tex]`, `[dep:docling]`).

## 18. Passi manuali per la produzione

1. **M0** sulla VM, con il kit del piano: CPU, AVX, RAM, sonda Docker con
   Docling. Poi **M1**, con l'SQL dell'Appendice B in sola lettura.
2. **Deploy**:
   - `docker compose build backend` (per TikZ,
     `--build-arg INSTALL_TEX=true`);
   - migrazioni 0037 e 0038 **prima** dell'avvio (vedi CLAUDE.md);
   - `docker image prune` se il disco non basta.
3. **Accensioni esplicite**, in `.env`:
   - `FIGURE_EXTRACTION_ENABLED=true`, dopo M0;
   - `FIGURE_LITERATURE_ENABLED=true` e `OPENALEX_API_KEY`;
   - `FIGURE_TIKZ_ENABLED=true` (con TeX nell'immagine);
   - `FIGURE_TIKZ_PROPOSE_ENABLED=true`, solo dopo lo spike M5 e M7.
4. Nessun backfill: le estrazioni dei corsi esistenti si chiedono dalla UI
   o con `scripts/extract_document_figures.py --apply`. Lo script rimette
   in coda anche le fonti riservate saltate con la regola precedente e i
   documenti incompleti (§20).
5. Migrazione 0039 e ri-ritaglio delle figure già estratte: §22.6.

## 19. Revisione avversariale (Fase D)

Cinque revisori in sola lettura sulle dimensioni del brief §8 (costo e
prestazioni, correttezza, sicurezza, attribuzione e i18n, tipografia nelle
cinque uscite), più un confutatore sulle correzioni.

### Misura sul manuale di 500 pagine (OpenStax UP1, docling, 1 thread)

Container `--cpus 2 --memory 3g`, codice vero del worker
(`_extract`), storage e DB finti.

| Voce | Esito |
|---|---|
| Tempo | 1,87 s/pag nei blocchi; 17,1 min per 500 pagine con due interruzioni (≈2,0 s/pag); 16 avvii del figlio da 3,7 s |
| Memoria | RSS del figlio 1,64-1,65 GB, stabile con 40 pagine per figlio (watchdog a 2048 MB); padre 91 MB |
| Figure | 401 estratte e 138 scartate; dopo la dedup 210 fino a p. 300 e 390 fino a p. 500; p50 26 KB, p90 66 KB |
| Ripresa | Dopo il kill del container riprende dal checkpoint, senza righe doppie. Dopo il kill del figlio il blocco va rifatto al tentativo dopo (backoff 60 s, un auto-retry) |
| Tetto | `FIGURE_EXTRACTION_MAX_PAGES`=300 rispettato: copertura `partial` |
| VM | Proiezione (k = 6, M0 non fatta): ≈11 s/pag, 300 pagine ≈ 1 h. Oltre `FIGURE_WAIT_MAX_MINUTES`: la Fase 3 di un corso nuovo parte senza le figure del manuale, che arrivano alle rigenerazioni |

### Correzioni (con test)

| Commit | Dimensione | Correzione |
|---|---|---|
| cb45a98 | sicurezza | XML Office con DTD o entità rifiutato prima del parse. Un PPTX di 17 KB portava il processo principale a ~3 GB durante il riassunto. DOI letto dal PDF senza caratteri di query |
| 5699a02 | correttezza | Duplicazione oltre ~700 figure: i rimandi fra figure si assegnano dopo il primo INSERT (prima c'era una FK violata a ogni tentativo). Fusione: `[fig:]` minuscolo non è una citazione; id con spazi. Worker dei buchi: errori inattesi → `pending` |
| e8abd47 | costo | Traduzione delle figure nella duplicazione a blocchi da 25: in una sola chiamata ~80 figure troncavano la risposta, il job falliva e il corso copia veniva cancellato. Tetto delle descrizioni distribuito sul documento, didascalie prima: prima le prime 80 figure in ordine di pagina (su 300 pagine si fermava a p. 138). Niente figlio avviato a pagine finite; memoria del cgroup |
| 83f44cf | attribuzione | Metadati di default esclusi (Microsoft Office User, scanner, Presentation1…). «Cognome, Nome» non perde più il cognome né il primo autore. Figure di terzi («Reprinted from…», «©»): licenza `unknown` e credito originale nella riga; candidate OpenAlex di terzi scartate. Numero di figura oltre 10 caratteri ignorato |
| 86937ae | tipografia | `tikz` a grandezza naturale (prima usciva al 75%). Nella dispensa la riga «Fonte» resta sulla pagina della figura. Ritagli non oltre 1,25× la misura nell'originale. Fascia: cognome conservato, «et al.» solo da tre autori, niente « » aperte |
| 84eb94a | attribuzione | Code di fonte del modello riconosciute in tutte le lingue dell'interfaccia |
| 1410aee | confutatore | Correzioni delle correzioni:
- crediti di terzi: le etichette di pannello «(c)» e «elaborazione propria» non sono crediti; niente «Fonte: Fonte:»;
- titoli con «/» accettati; nessuna persona inventata da «Smith, John»;
- DOI senza segmenti «..»;
- iniziali solo sui nomi con prenome in testa;
- tag `[fig:SRC-…]` minuscoli tolti dal testo;
- larghezza naturale solo nella dispensa;
- traduzione limitata alle chiavi del blocco;
- page cache esclusa dalla memoria;
- `.doc` zip controllato prima di `docx2txt` |

### Rilievi dichiarati, senza correzione

Il confutatore li ha CONFERMATI tutti, con prove dal codice o misure.

**Correttezza**
- **TOCTOU fra cancellazione e collocazione**: un documento cancellato
  mentre la Fase 3 o il PATCH collocano una sua figura può lasciare un
  segnaposto fino alla rigenerazione. Probabilità bassa, sempre visibile.
- **File orfani nello storage**: documento o corso cancellati durante
  un'estrazione; cartella del corso copia dopo una duplicazione fallita;
  righe `superseded` non più usate.
- **Id `SRC-` dopo la duplicazione**: restano quelli del corso sorgente;
  alla rigenerazione il modello non può ripescare quelle figure con l'id
  vecchio (resa e numerazione invariate).
- **Descrizione Vision fallita**: una sola figura con errore
  deterministico porta il documento a `failed(vision_unavailable)`; le
  figure già `ready` restano usabili.

**Costo**
- **OpenAlex nei buchi**:
  - l'estrazione di un PDF (fino a 40 pagine) sotto `HEAVY_JOB_LOCK`
    non rispetta la scadenza della verifica;
  - nessuna cache per corso;
  - ai ritentativi le candidate già scartate si rivalutano (≤ ~0,03 $
    per lezione).
- **Upload su SFTP**: una connessione per file (2-7 min stimati per 300
  pagine su SFTP).
- **Dashboard**: il costo Vision delle righe cancellate o sostituite
  sparisce, perché il totale in admin non è uno storico.

**Attribuzione**
- **Riga nei PDF già generati**: dopo una modifica di bibliografia o
  licenza del documento i PDF non risultano «da rigenerare» (coerente
  con U1: vale dalla prossima esportazione).
- **Script non latini**: in WeasyPrint le righe della fascia con script
  non latini sono alte ~15 px invece di 13,9; con 4 figure la quarta può
  perdere ~3 px. Font macOS diversi da quelli del container: dato
  incerto.
- **Variante parlata**: il cognome è l'ultima parola (nomi
  ungheresi/giapponesi); il credito Wikimedia si perde se c'è il titolo.
- **Fascia con più figure**: le righe non dicono a quale figura si
  riferiscono.
- **Crediti OpenAlex**: senza URI e versione della licenza (OpenAlex dà
  solo il codice). Le figure Wikimedia li hanno.
- **Endpoint `…/image`**: serve anche le figure non proponibili del corso
  (sempre con la riga, a chi ha COURSE_VIEW).
- **«p. N»**: è la pagina del PDF, non quella stampata.
- **Lingue diverse da it/en**: etichette della riga in italiano (A4);
  nessun isolamento bidi.

**Tipografia**
- **Avatar nei frame**: può coprire l'angolo di una figura di fonte larga
  (rapporto ~1,8-3,2) su una slide con il solo titolo. Geometria
  preesistente del body.
- **Slide con 3-4 figure di fonte**: una figura si perde o diventa
  illeggibile nel PDF slide; nella dispensa restano tutte.
- **A capo**: spazio non separabile assente in «fig. N» / «p. N».
- **Separatore**: «e» italiana fra autori in cirillico o cinese.
- **Ritagli senza dpi** (Wikimedia): restano a piena larghezza del
  riquadro.

**Sicurezza**
- **Variabili di `\foreach`**: la lista dei nomi vietati non è
  determinante, perché pgffor rilega la variabile nel corpo. Resta come
  difesa a strati.

## 20. Modifiche del 24/09/2026: fonti riservate, riassunto, copertura totale

Decisioni dell'utente dopo il primo rilascio, prese con il docente.

**Fonti riservate = materiale del docente**
- Le figure si estraggono da tutti i documenti tranne gli esclusi
  (`excluded`, «Escluso dalla generazione»), che restano fuori.
- Una fonte riservata (`content_only`) si assume materiale del docente,
  senza campi nuovi nel DB: vale la politica corrente del documento.
  - Le sue figure si propongono nelle dispense, anche con `open_only`.
  - La riga «Fonte» è «materiale del docente» («instructor's material»).
    Non nomina mai il documento: niente titolo, autori, file, anno,
    pagina, numero di figura o licenza.
  - Se la didascalia originale dichiara un credito di terzi («© Elsevier»,
    «Reprinted from …»), la riga mostra quel credito.
  - Allo stacco (documento cancellato) l'attribuzione congelata porta
    `reserved: true`: la riga resta identica e senza licenza.
- Alla Vision (PROMPT 18) non arriva il titolo del documento riservato;
  il catalogo del PROMPT 3 non contiene nomi di documenti.
- Deduplicazione nel corso: una fonte riservata non nasconde mai la copia
  di un documento citabile, che ha la riga più precisa.
- I documenti saltati con la regola precedente
  (`skipped/policy_content_only`) tornano in coda con «Estrai figure» o
  con lo script.

**Figure nel riassunto strutturato**
- Sezione «Figure» nel dialog del riassunto, presente solo se il
  documento ha figure estratte.
- Per ogni figura: miniatura dall'endpoint autenticato, pagina o slide,
  didascalia originale, descrizione e riga «Fonte» del backend.
- Le figure si caricano a gruppi di 24.
- Chi può modificare il corso esclude una figura dalle proposte o la
  riammette; le lezioni già generate non cambiano.
- Elenco da `GET …/document-figures?document_id=`.

**Copertura totale**
- Default `0` = nessun tetto per `FIGURE_EXTRACTION_MAX_PAGES` (prima
  300), `FIGURE_EXTRACTION_TOTAL_TIMEOUT_SECONDS` (prima 5400) e
  `FIGURE_DESCRIBE_MAX_PER_DOCUMENT` (prima 80). Resta il tempo massimo di
  ogni blocco. I valori sono in `Settings`, `.env.example` e compose.
- Un blocco che manda in crash il motore due volte si riprova pagina per
  pagina: si salta solo la pagina che va in crash due volte (copertura
  `partial`, `crashed_repeatedly`).
- Un giro che avanza azzera il conto dei tentativi automatici. Su un
  manuale lungo contano gli errori di fila, non quelli di tutto il
  documento.
- Un documento `ready` ma incompleto rispetto ai tetti attuali torna in
  coda su richiesta e riprende dal checkpoint (`next_page`), senza rifare
  le pagine già analizzate. Le figure scartate dal tetto delle
  descrizioni (`describe_capped`) tornano da descrivere. In UI il bottone
  è «Completa l'estrazione».
- **Costo e tempi**:
  - la Vision costa ~0,0007 $ a figura, quindi un manuale con 500 figure
    costa ~0,35 $;
  - sulla VM di produzione (16 core, 32 GB, senza AVX; misura del
    24/09/2026) un articolo di 16 pagine ha richiesto 132,5 s, circa
    8 s/pagina con descrizioni e avvio del figlio compresi;
  - un manuale di 700 pagine richiede ore. La Fase 3 lo aspetta al
    massimo `FIGURE_WAIT_MAX_MINUTES`; le sue figure entrano nelle
    dispense generate o rigenerate dopo.

## 21. Qualità delle figure della letteratura (24/09/2026)

Prima prova in produzione: corso «Reti di Calcolatori», senza documenti,
con la letteratura aperta accesa. Sono emersi tre difetti.

| Difetto | Causa | Correzione |
|---|---|---|
| Figure nere o con bande nere | I rendering PNG di Commons hanno lo sfondo trasparente (per esempio in modo `LA`); `convert("RGB")` scartava l'alfa. Il file «IPv4 address structure…» risultava interamente nero, con luminosità media 0 contro 241 su fondo bianco. Lo stesso valeva per le immagini trasparenti dentro DOCX e PPTX | `cropper.on_white`: le zone trasparenti vanno su fondo bianco, sia in `image_limits.load_image` (letteratura) sia in `office._open_image` |
| Figura nera promossa dalla Vision | Il modello si fidava del titolo della fonte | Le immagini vuote o uniformi (`is_blank`) si scartano prima della Vision (`rejected_blank`) |
| Testo in arabo e farsi | Le ricerche in inglese su Commons trovano anche le varianti `-ar`/`-fa` dello stesso schema | PROMPT 20 dichiara `text_language` (ISO 639-1 o `none`). Si tengono solo la lingua del corso, l'inglese o `none` (`rejected_language`); decisione dell'utente: italiano e inglese |
| Le stesse 7 figure in quasi tutte le 48 lezioni | Catalogo di corso senza vincoli di riuso; la verifica dei buchi contava come pertinenti le figure già usate altrove (`reason: enough`) e non cercava più | Una figura di fonte sta in **una sola lezione** (decisione dell'utente; dal 25/09/2026 in al più `FIGURE_SOURCE_MAX_LESSONS_PER_FIGURE` lezioni, §22). Il catalogo non offre quelle collocate in un'altra lezione; il ricontrollo di fine generazione toglie quelle prese intanto da una lezione generata in parallelo, con audit; la verifica dei buchi conta solo le figure libere. Il docente può comunque inserire a mano una figura già usata |

**Limiti**
- Le figure già salvate prima della correzione restano com'erano: il file
  è stato salvato già nero. Per la prova si usa un corso nuovo.
- Due lezioni che salvano quasi nello stesso istante possono ancora
  tenere la stessa figura: il ricontrollo legge lo stato già salvato.

**OpenAlex: editori che bloccano i download** (correzione successiva)
- Molti editori open access (MDPI, Hindawi) rispondono 403 ai download
  automatici. Nei temi di ingegneria, dominati da MDPI, OpenAlex non dava
  quasi figure (`download_errors`).
- Le protezioni degli editori non si aggirano. Se l'editore rifiuta, si
  usa la copia del PDF ospitata da OpenAlex (`has_content.pdf`,
  `content.openalex.org/works/{id}.pdf`, API key in query string).
  - Costa 0,01 $ a PDF, dentro 1 $ gratuito al giorno per chiave (circa
    100 PDF).
  - Si prova prima l'editore, che è gratis. Un editore che risponde 401 o
    403 non si riprova nello stesso giro.
  - A credito finito (429/402) o con la chiave rifiutata, la copia di
    OpenAlex non si chiede più per quella lezione.
- Licenza e attribuzione restano quelle di `best_oa_location`. OpenAlex
  precisa che i PDF mantengono il copyright originale: per questo si
  tengono solo le licenze CC BY, CC BY-SA, CC0 e pubblico dominio.
- Si provano fino a 5 lavori per ricerca, prima 3.
- Statistiche in `figures_gap_stats`: `downloads_publisher`,
  `downloads_openalex`, `publisher_errors`, `download_errors`.
- La chiave non compare nei log: gli errori la oscurano e httpx è a
  WARNING.
- Anche l'import dei paper (Fase 1) usa la stessa copia quando l'editore
  rifiuta: prima importava solo i metadati (doc 16).

## 22. Risoluzione effettiva e riuso limitato (25/09/2026)

Segnalazione del docente del corso «Misure sperimentali per la dinamica
strutturale»: figure sgranate e poche. Diagnosi sui dati di produzione
(dump del corso, misure M-A1/M-A2/M-A2b):

- **sgranate**: 50 collocazioni su 96 uscivano a 120 ppi (ritaglio v1 a
  150 dpi ingrandito ×1,25), 12 raster su 13 in JPEG; alcune avevano un
  nativo di 41-83 ppi, gonfiato dal ritaglio;
- **poche**: estrazione ferma a 3 documenti su 218, letteratura mai
  partita (un documento mai estratto la bloccava), catalogo esclusivo.

Piano: `~/.claude/plans/pasted-content-id-3136-prompt-structured-stardust.md`
(WP1-WP3 rilasciati; il piano delle figure per fabbisogno, WP4-WP10, non
è ancora implementato).

### 22.1 Correzioni rapide (WP1)
- Un documento con estrazione **mai richiesta** non blocca più la
  letteratura: si attendono solo le estrazioni in coda o in corso chieste
  da meno di `FIGURE_WAIT_MAX_MINUTES` (`documents_extracting`).
- **Riuso limitato**: una figura di fonte in al più
  `FIGURE_SOURCE_MAX_LESSONS_PER_FIGURE` lezioni (default 2; 1 = come
  prima per le figure nuove), mai due volte nella stessa. Le figure già
  collocate in una lezione restano sue alla rigenerazione. Fra lezioni
  generate in parallelo il tetto può essere superato di uno (nessun lock
  di corso). Audit `source_figures_dropped` con `reason` (`reuse_cap`,
  `not_selectable_at_materialize`).
- PATCH: doppioni e tetto come in §7.

### 22.2 Metrica e classi (`services/source_figure_resolution.py`)
- ppi effettivi = pixel d'informazione / larghezza stampata; pixel
  d'informazione = `width · min(1, native_ppi / dpi)` (un ritaglio a
  150 dpi di un raster a 76 ppi ha metà dei pixel utili).
- Misura naturale N normalizzata alla pagina (`min(1, 612/page_w)`: una
  slide 16:9 non vale come un foglio da 34 cm); senza dati 90 mm (per
  convenzione per Commons, per stima per le righe vecchie: mai
  `unusable`).
- Classe alla larghezza di riferimento R = min(170 mm, N): **good** ≥ 200,
  **acceptable** ≥ 150, **low** ≥ 100, **unusable** < 100. Si calcola
  sempre in lettura, non si salva.

### 22.3 Regola di stampa
- Dispensa: good `min(C, 1,25·N, px/200)`; acceptable alla misura
  naturale; low `max(min(C, 0,8·N), px/150)`; unusable già collocata a
  100 ppi. **Mai sotto 100 ppi.**
- Slide PDF e frame video: `min(box_w, box_h·w/h, 1,25·px/6,667)`:
  ingrandimento nei frame ≤ 1,25, ≥ 135 ppi nel PDF delle slide.
- Interruttore `FIGURE_RESOLUTION_RULES_ENABLED` (false = regola
  precedente, solo in dispensa, e nessun filtro).

### 22.4 Ritaglio v2 (`crop_version` 2)
- Il raster dominante si rende sulla **sua griglia di pixel** con
  `FPDF_RenderPageBitmapWithMatrix` (correzione δ = 0,01 px,
  `NO_SMOOTHIMAGE`): bit-identico all'oggetto nativo, anche con
  ribaltamenti, rotazioni di 90°, pagine ruotate e Form XObject.
- Figure **miste** (etichette e frecce vettoriali sopra): k volte il
  nativo, mai sotto 300 dpi. Un raster sotto 50 ppi o a striscia sotto
  segni vettoriali è **sfondo** (figura vettoriale). Pannelli a ppi
  diversi: nessuna griglia unica, ppi del pannello peggiore.
- Niente pavimento a 150 dpi né soffitto a 300; tetto 12 MP, anti-bomba
  60 MP. PNG senza perdita per tratto e sorgenti senza perdita; JPEG q95
  4:4:4 solo per foto da sorgente con perdita oltre 1,5 MB.
- DOCX e PPTX: `srcRect`, ribaltamenti, rotazioni a quarti di giro, EMU,
  scala dei gruppi, EXIF.
- Interruttore `FIGURE_EXTRACTION_NATIVE_CROP_ENABLED`.
  `EXTRACTION_VERSION` resta 1: nessun supersede delle figure collocate.

### 22.5 Letteratura, selezione, editor
- OpenAlex salva dpi, bbox e ingressi del ritaglio; Commons usa le
  dimensioni del file originale (SVG = vettoriale) e chiede 1920 px.
  Le candidate `unusable` si scartano prima del download (Commons) o della
  Vision (`rejected_resolution`).
- `unusable` non si propone (catalogo di Fase 3 anche alla lezione che la
  usa, selettore, PATCH di figure nuove: `resolution_unusable`); le low
  vanno in coda al catalogo. Il DTO ha `resolution` (classe, classe nelle
  slide, base, larghezza e ppi di stampa) e `image_rev`.
- Editor: badge «Bassa risoluzione» / «Risoluzione insufficiente» su card,
  selettore e riassunto del documento.

### 22.6 Ri-ritaglio sul posto (`scripts/rerender_document_figures.py`)
- Stesso UUID anche per le figure collocate; niente Vision; verifica
  d'identità (v1 riprodotto ai dpi salvati, phash); UPDATE condizionale;
  `recrop_previous` per `--revert`; file v1 conservati fino a
  `--purge-replaced --older-than 14 --apply`. Una figura che in v2 sarebbe
  `unusable` resta v1 (badge in editor).
- Il worker delle figure lo esegue a lotti di 40 figure con
  `HEAVY_JOB_LOCK` per lotto, dopo le estrazioni in coda. Lo script
  rifiuta `--apply`, `--inline` e `--revert` con estrazioni o ri-ritagli in
  corso, e `--apply` con uno degli interruttori spenti.
- Migrazione 0039: `native_ppi`, `natural_width_mm`, `crop_mode`,
  `crop_version`, `recropped_at`, `recrop_previous` sulle figure;
  `figures_recrop_requested_at` e `figures_recrop_stats` sui documenti.

### 22.7 Prova sul corso del docente (copia locale)
- 5 dispense riesportate: collocazioni a 41/77/83 ppi effettivi → tutte
  sopra 100 tranne una mista (nativo 51 ppi, resta v1 a 52 mm); 12 su 13
  a 150-480 ppi, in PNG; PDF +46,5%.
- Ri-ritaglio: identità 442/442, raster sulla griglia nativa 80%, JPEG
  288 → 0, byte mediana 2,1×.
- Estrazione completa (212 documenti): 1865 figure nuove, 1,65 $ di Vision,
  ~2,5 h di Docling in locale; i documenti contengono gli schemi di quasi
  tutte le tipologie di vibrometro (Tomasini-Castellini, Rembe, Di Maio).
- Trovato e corretto: il byte NUL nel testo di un PDF bloccava
  l'estrazione del documento.

### 22.8 Limiti
- La copertura **per concetto** (una figura per tipologia, nella sua
  sezione e in ordine, e i buchi dichiarati al docente) richiede il piano
  delle figure (WP4-WP8), non ancora implementato: oggi il catalogo resta
  lessicale.
- Una figura mista sotto il minimo già collocata resta col ritaglio v1.

