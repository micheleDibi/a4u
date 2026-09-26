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
| §22 risoluzione effettiva, ritaglio v2, ri-ritaglio | `test_source_figure_resolution`, `test_source_figure_crop_native`, `test_source_figure_recrop`, `test_source_figure_render`, `test_source_figure_band`, `test_source_figure_filters`, `test_frontend_source_figures` (badge, `image_rev`) |
| §22.1 riuso limitato (K) e doppioni | `test_source_figure_materialize` (tetto parametrizzato, lezioni in parallelo sotto il lock), `test_source_figure_gaps` (documenti mai estratti) |
| §23.1 fabbisogni (PROMPT 22) | `test_figure_needs_service`, `test_figure_plan` (richiesta, worker, attesa della Fase 3, ripiego inline, 0040) |
| §23.2 `depicts` | `test_figure_depicts`, `test_source_figure_vision`, `test_source_figure_relevance` |
| §23.3 abbinamento | `test_figure_need_matching` + `fixtures/figure_need_matching_cases.json` |
| §23.4 assegnazione globale | `test_source_figure_assignment` (oracolo, proprietà, figure proprie, riserva della letteratura), `test_source_figure_assignment_db` (offerta, lock, partecipanti, alternative e residuo al tetto, `settle`, 0042) |
| §23.5 buchi per fabbisogno | `test_figure_gaps_needs` (tetti, impronta vecchia senza ciclo, esiti fusi dopo un errore, spesa fra i tentativi, piano spento) |
| §23.6 blocco del piano, collocazione | `test_source_figure_plan`, `test_source_figure_materialize` (catalogo del piano, errore del catalogo, ripiego inline, annullamento durante il lock), `test_source_figure_prompt_schema` (I1 senza piano) |
| §23.7 editor, PROMPT 19 v2, sequenze, upload | `test_figure_needs_view` (0043, PUT), `test_source_figure_redundancy`, `test_slides_source_figures`, `test_lesson_asset_upload_format`, `test_frontend_source_figures` |
| §24 figure inserite in automatico, PROMPT 23, «Inserisci» | `test_source_figure_fill`, `test_source_figure_plan`, `test_source_figure_materialize`, `test_frontend_source_figures` |

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
6. **Piano delle figure** (§23): migrazioni 0040-0043 prima dell'avvio.
   Sui corsi esistenti, **prima di rigenerare**, completare `depicts`:
   `scripts/redescribe_figure_depicts.py --course <id>` (conteggio), poi
   `--apply --max-usd N`, oppure `--all`. Una Vision per figura (~0,001 $).
   Senza, le figure dei documenti non coprono nessun fabbisogno: la Fase 3
   usa il catalogo lessicale e la verifica dei buchi non cerca nella
   letteratura (`depicts_missing`, §23.9). Commons richiede
   `PAPERS_POLITE_EMAIL` nel `.env`.

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
| Le stesse 7 figure in quasi tutte le 48 lezioni | Catalogo di corso senza vincoli di riuso; la verifica dei buchi contava come pertinenti le figure già usate altrove (`reason: enough`) e non cercava più | Una figura di fonte sta in **una sola lezione** (decisione dell'utente; dal 25/09/2026 in al più `FIGURE_SOURCE_MAX_LESSONS_PER_FIGURE` lezioni, default 2, §22.1). Il catalogo non offre quelle già al tetto in altre lezioni; il ricontrollo di fine generazione toglie quelle arrivate intanto al tetto per una lezione generata in parallelo, con audit; la verifica dei buchi conta solo le figure sotto il tetto. Il docente può comunque inserire a mano una figura già usata |

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
(WP1-WP3 rilasciati il 25/09; il piano delle figure per fabbisogno,
WP4-WP10, è descritto in §23).

### 22.1 Correzioni rapide (WP1)
- Un documento con estrazione **mai richiesta** non blocca più la
  letteratura: si attendono solo le estrazioni in coda o in corso chieste
  da meno di `FIGURE_WAIT_MAX_MINUTES` (`documents_extracting`).
- **Riuso limitato**: una figura di fonte in al più
  `FIGURE_SOURCE_MAX_LESSONS_PER_FIGURE` lezioni (default 2; 1 = come
  prima per le figure nuove), mai due volte nella stessa. Le figure già
  collocate in una lezione restano sue alla rigenerazione. Con WP6 (§23.4)
  il ricontrollo alla materializzazione avviene sotto il lock di corso: le
  lezioni generate in parallelo non superano più il tetto (resta possibile
  solo se il lock non si prende entro 20 s). Audit `source_figures_dropped` con `reason` (`reuse_cap`,
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
  100 ppi. **Mai sotto 100 ppi** sui pixel d'informazione noti: le righe
  v1 (estratte prima della 0039, `native_ppi` NULL) contano i pixel del
  render (150-300 dpi) e possono uscire sotto 100 ppi effettivi finché non
  si ri-ritagliano (§22.6).
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
  nativo (k ≤ 8), mai sotto 300 dpi entro il tetto di 12 MP. Un raster sotto 50 ppi o a striscia sotto
  segni vettoriali è **sfondo** (figura vettoriale). Pannelli a ppi
  diversi: nessuna griglia unica, ppi del pannello peggiore.
- Niente pavimento a 150 dpi né soffitto a 300; tetto 12 MP, anti-bomba
  60 MP. PNG senza perdita per tratto e sorgenti senza perdita; JPEG q95
  4:4:4 solo per foto da sorgente con perdita oltre 1,5 MB.
- DOCX e PPTX: `srcRect`, ribaltamenti, rotazioni a quarti di giro, EMU,
  scala dei gruppi (PPTX), EXIF. In un disegno DOCX con più immagini vale
  l'`a:ext` di ciascuna, senza scala del gruppo.
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
  `--purge-replaced --older-than 14 --apply`.
- Una figura che in v2 sarebbe `unusable` resta v1 (`kept_unusable`, gli
  id in `figures_recrop_stats.kept_unusable_ids`): senza `native_ppi` la sua
  classe si calcola sui pixel del render v1, quindi **nessun badge** in
  editor e resta proponibile (anche in una seconda lezione): va segnalata a
  mano al docente.
- Si lancia **un corso alla volta** (`--course`): le figure dei corsi su
  cui non lo si lancia restano v1. Elenco dei corsi con figure v1 (sola
  lettura): `select distinct course_id from course_document_figure where
  crop_version = 1 and source_kind = 'uploaded' and storage_path is not
  null`.
- Il worker delle figure lo esegue a lotti di 40 figure con
  `HEAVY_JOB_LOCK` per lotto, dopo le estrazioni in coda (le estrazioni
  aspettano comunque la fine del documento: stesso giro del worker).
  `--inline` gira senza `HEAVY_JOB_LOCK`: usarlo con il worker fermo o in
  orari senza estrazioni. Lo script rifiuta `--apply`, `--inline` e
  `--revert` con estrazioni o ri-ritagli in corso, e `--apply` e
  `--inline` con uno degli interruttori spenti.
- Dopo il ri-ritaglio vanno **riesportate** dispense e slide (ed
  eventualmente i video) delle lezioni in `lessons_to_reexport` (lo dà
  `--measure`): i PDF già generati non cambiano da soli.
- Migrazione 0039: `native_ppi`, `natural_width_mm`, `crop_mode`,
  `crop_version`, `recropped_at`, `recrop_previous` sulle figure;
  `figures_recrop_requested_at` e `figures_recrop_stats` sui documenti.

### 22.7 Prova sul corso del docente (copia locale)
- 5 dispense riesportate: collocazioni a 41/77/83 ppi effettivi → tutte
  sopra 100 tranne una mista (nativo 51 ppi, resta v1 a 52 mm); 12 su 13
  a 150-480 ppi, in PNG; PDF +46,5%.
- Ri-ritaglio: identità 442/442; modo `raster_native` 80% dei raster,
  allineate alla griglia nativa 98%; JPEG 288 → 0; byte mediana 2,1×.
- Estrazione completa (212 documenti): 1865 figure nuove, 1,65 $ di Vision,
  ~2,5 h di Docling in locale; i documenti contengono gli schemi di quasi
  tutte le tipologie di vibrometro (Tomasini-Castellini, Rembe, Di Maio).
- Trovato e corretto: il byte NUL nel testo di un PDF bloccava
  l'estrazione del documento.

### 22.8 Limiti
- La copertura **per concetto** (una figura per tipologia, nella sua
  sezione e in ordine, e i buchi dichiarati al docente) la dà il piano
  delle figure (§23); senza piano (`FIGURE_PLAN_ENABLED=false` o
  `FIGURE_PLAN_IN_PROMPT_ENABLED=false`) il catalogo resta lessicale.
- Una figura mista sotto il minimo già collocata resta col ritaglio v1.

## 23. Piano delle figure: copertura per concetto (WP4-WP10)

Secondo difetto segnalato dal docente: figure corrette ma poche. In M4.L6
«Tipologie di vibrometri laser Doppler» il testo tratta sei tipologie e le
figure di fonte erano due. Il catalogo lessicale sceglie le figure «più
simili alla lezione», non «una per ciascun concetto che la lezione
enumera». Il piano delle figure dichiara prima i **fabbisogni** della
lezione (quali figure servirebbero, in quale sezione, in quale ordine) e
poi cerca, assegna, colloca o dichiara scoperto ciascun fabbisogno.

### 23.1 Fabbisogni per lezione (PROMPT 22, WP4)
Moduli: `openai_figure_needs_service.py` (prompt, schema strict,
validazione), `figure_plan_service.py` (input, impronta, richiesta, attesa,
ripiego inline), `course_lesson_figure_needs_worker.py` (worker).

- **Input**: solo la struttura di Fase 2 della lezione (titolo, obiettivi,
  temi, scaletta con `section_id`) più i titoli delle lezioni sorelle del
  modulo. **Niente catalogo**: altrimenti il modello chiederebbe solo ciò
  che esiste già e i buchi sparirebbero. Ogni stringa passa da
  `neutralize_third_party_text` dentro `data_block`.
- **Uscita** per fabbisogno: `section_id` (solo fra quelli della
  scaletta), `subject`, `representation` (schematic, block_diagram,
  circuit, chart, photo, micrograph, other), `focus`, `priority` (`must`/`should`),
  `object_en` + `object_terms`, `variant_en` + `variant_terms` (vuoti per
  la forma base, `is_base`), `sequence_group`/`sequence_index` per le
  enumerazioni ordinate, termini di ricerca nella lingua del corso e in
  inglese, `reason`.
- **Validazione** (`validate_needs`): sezioni inventate scartate; must
  prima al troncamento, al più 8 must e `FIGURE_NEEDS_MAX_PER_LESSON` (10)
  in totale, `FIGURE_NEEDS_MAX_PER_INTRO_LESSON` (3) nelle introduttive;
  gruppi di un solo elemento sciolti; sequenze rinumerate; i conteggi
  degli scarti vanno in `dropped`.
- **`need_id` stabile**: `n` + sha1(`section_id` | soggetto normalizzato)
  [:8], con suffisso sulle collisioni. Le etichette N1…Nn si calcolano
  alla lettura. Collegamenti, «Non serve» e riserve restano validi finché
  il fabbisogno è lo stesso.
- **Pigrizia e impronta**: i fabbisogni si calcolano solo sulle richieste
  esplicite di Fase 3 (le tre `request_*`), mai all'avvio o in una
  migrazione. L'impronta è lo sha256 dell'input esatto, di
  `PROMPT_VERSION` e dei tetti: con la stessa impronta una richiesta non
  ricalcola nulla. Verifiche e lezioni senza scaletta → `skipped`.
- **Worker**: concorrenza `FIGURE_NEEDS_CONCURRENCY` (4), claim
  condizionale, backoff 30 s × tentativi, errore recuperabile → di nuovo
  `pending` fino a `FIGURE_NEEDS_AUTO_RETRY_MAX` (3), poi `failed`;
  chiave OpenAI assente o 4xx diverso da 429 → `failed` subito;
  `processing` interrotti → `pending` all'avvio. Una richiesta arrivata
  durante il calcolo si soddisfa col risultato solo se l'impronta
  dell'input attuale coincide, altrimenti si ricalcola. Il worker parte
  solo con il piano attivo e un errore del DB non lo ferma.
- **Attesa della Fase 3** (`waiting_clause` in `_pending_lessons_query`):
  una lezione in coda parte quando nessuna lezione in coda dello stesso
  corso ha i fabbisogni in coda o in calcolo, oppure quando sono passati
  `FIGURE_WAIT_MAX_MINUTES` dalla **propria** richiesta. Così
  l'assegnazione globale (WP6) vede la domanda di tutte le lezioni chieste
  insieme. Il ripiego inline `ensure_lesson_needs` (una chiamata, per
  fabbisogni mancanti o vecchi al momento della generazione; se fallisce
  la Fase 3 procede senza piano) lo chiama il worker della Fase 3 prima di
  `reserve`, solo con il piano nel prompt e solo per fabbisogni già chiesti
  (in coda, falliti o vecchi): una lezione mai pianificata procede senza.
- **Costo**: `figure_needs_usage` cumulativo, fase admin `figure_needs`.
  Duplicazione: i fabbisogni non si copiano (la copia è sempre in un'altra
  lingua; si ricalcolano alla prima richiesta di Fase 3, §23.9).
- **Migrazione 0040**: `figure_needs`, `figure_needs_status` (CHECK),
  `figure_needs_attempts`, `figure_needs_requested_at`,
  `figure_needs_checked_at`, `figure_needs_usage`, indice parziale sulle
  lezioni `pending`.
- **Interruttore**: `FIGURE_PLAN_ENABLED` (con `FIGURE_SOURCE_ENABLED`).
  Spento: nessuna richiesta, nessuna attesa, nessuna chiamata.
- **Misure** (copia locale del corso del docente, 25/09/2026):
  - M-N1, qualità: 4 lezioni di riferimento più M3.L4 di controllo, 3 giri
    per modello, contro l'elenco dei fabbisogni attesi scritto prima di
    ogni chiamata. `gpt-4.1-mini`: recall dei must 1,0 (0,96 contando solo
    quelli classificati `must`), ma 4-5 must spuri sulla lezione
    matematica di controllo e sequenza delle tipologie di M4.L6 spezzata
    (multi-point declassato a should). `gpt-5.5` (reasoning `none`): recall
    1,0 (0,92 in senso stretto: un giro su tre di M5.L1 classifica lo
    schema del setup come should), must spuri ≤1, 0 sezioni inventate,
    sequenza di M4.L6 corretta 3 volte su 3, 0 fabbisogni su M3.L4. Scelto
    `gpt-5.5`. Stabilità fra giri (Jaccard dei fabbisogni attesi coperti)
    1,0 tranne M5.L1 (0,33: lezione con un solo must atteso).
  - M-N2, latenza: 42 lezioni con concorrenza 4 in 107 s (p50 11 s a
    chiamata, massimo 22 s); 180 fabbisogni, 99 must, al più 6 must a
    lezione; 1,23 $ (circa 0,03 $ a lezione). Con 2500 token di output una
    lezione usciva troncata: tetto portato a 4500.
  - PROMPT 22 v2 (dopo M-A3, §23.3): con la v1 `gpt-5.5` scriveva la
    variante anche nell'oggetto («scanning laser Doppler vibrometer») e
    varianti descrittive («discrete scanning», «force-controlled shaker
    excitation»), che nessuna figura dichiara: l'abbinamento non trovava gli
    schemi a scansione di M4.L6. La v2 chiede l'oggetto senza variante e il
    nome breve e comune della variante, primo fra i `variant_terms`.
    M-N1 ripetuta (gpt-5.5, 3 giri): recall dei must 1,0 anche in senso
    stretto, must spuri ≤1, sequenza di M4.L6 corretta 3 su 3, 0
    fabbisogni su M3.L4; M-N2: 42 lezioni in 111 s, 1,22 $, 96 must.
    Cambiando `PROMPT_VERSION` cambia l'impronta: i fabbisogni della v1 si
    ricalcolano alla richiesta successiva.
  - PROMPT 22 v3: con la v2 il vibrometro a punto singolo arrivava come
    variante («out-of-plane»), che nessuna figura dichiara; la v3 chiede la
    forma standard (quella chiamata col solo nome dell'oggetto) come forma
    base. M-N1 (gpt-5.5, 3 giri): recall dei must 1,0 in senso stretto,
    spuri ≤1, M4.L6 con S1 base e le sei tipologie in ordine 3 su 3, 0
    fabbisogni su M3.L4; M-N2: 42 lezioni in 101 s, 1,16 $, 89 must.

### 23.2 Che cosa raffigura una figura: `depicts` (WP5)
Per abbinare una figura a un fabbisogno serve sapere quale oggetto e quale
**variante** mostra: le parole chiave dicono «vibrometro», non «vibrometro
differenziale». La Vision descrittiva (PROMPT 18) e quella della
letteratura (PROMPT 20) restituiscono anche `depicts`:

- `items`: da 0 a 4 oggetti, il primo è lo strumento o l'allestimento di
  cui la figura tratta nel suo insieme (non i suoi componenti);
  `object_en` generico e senza variante, `variant_en` solo se la variante
  si vede o la didascalia la dichiara, altrimenti vuota;
- `focus`: il primo piano in 2-5 parole inglesi (optical layout,
  measurement setup, instrument photo, application example, measured
  response).

Tutto in **inglese canonico** (deviazione 4 del piano): i termini nella
lingua del corso stanno nei fabbisogni. L'output si neutralizza, si taglia
a 80 caratteri, si deduplica; si salva in `course_document_figure.depicts`
come `{"v": DEPICTS_VERSION, "items", "focus"}` (migrazione 0041).

- **Riuso delle descrizioni**: una figura quasi identica copia la
  descrizione (e `depicts`) solo da una fonte con `depicts` alla versione
  corrente; altrimenti si paga una chiamata Vision.
- **Figure già descritte**: `scripts/redescribe_figure_depicts.py`
  (`--course` o `--all`; senza `--apply` conta e stima; `--apply` richiede
  `--max-usd`). Una chiamata PROMPT 18 per figura, di cui si tiene SOLO
  `depicts` (descrizione, parole chiave, tipo e qualità restano: il
  catalogo non cambia); il costo va in `vision_usage`; le copie della
  descrizione (`describe_source_id`) ricevono lo stesso `depicts` senza
  chiamate. Il tetto `--max-usd` si applica stimando ogni chiamata come la
  più cara vista (almeno 0,0012 $; un modello senza prezzo conta la stima)
  e, finché non ha visto una chiamata pagata, lo script ne fa una alla
  volta; una figura col file illeggibile o una chiamata fallita contano
  come fallite e lo script prosegue (le copie di una fonte fallita si
  descrivono da sé). Sulla copia del corso del docente: 2306 figure,
  2,59 $, nessun fallimento.
- **Misura M-D1** (50 figure etichettate a mano guardandole, 30 di
  vibrometri che coprono tutte le varianti e 20 di accelerometri, shaker,
  martelli, celle di carico): primo giro variante corretta 0,73 a 768 px e
  0,60 a 1024 px; il modello elencava i componenti (laser, fotodiodo) al
  posto dello strumento e metteva la variante nel nome («rotational
  vibrometer»). Dopo una correzione del prompt: 0,77 a 768 px in senso
  stretto, circa 0,90 contando i sinonimi («laser velocimeter») e le figure
  in cui lo strumento non si vede; oggetto corretto 1,0 sulle 20 figure non
  di vibrometri, nessun vibrometro inventato; `kind` coerente con la
  rappresentazione 0,94. Resta 768 px (1024 non migliora); circa 0,0012 $
  a figura. L'abbinamento (WP6) cerca i termini della variante anche nel
  nome dell'oggetto e accetta sinonimi dell'oggetto.

### 23.3 Abbinamento fabbisogno ↔ figura (`figure_need_matching.py`, WP6)
Deterministico e lessicale su `depicts` (inglese canonico) e sui campi
inglesi del fabbisogno; nessuna chiamata AI.

| relazione | quando | copre | livello |
|---|---|---|---|
| `exact` | stesso oggetto e stessa variante (o forma base) | sì | 4 |
| `exact_mixed` | variante scritta nel nome dell'oggetto o figura più specifica | sì | 3 |
| `multi` | panoramica di più varianti, fra cui quella chiesta | sì | 2 |
| `specialized` | fabbisogno base, figura di una variante | sì | 2 |
| `legacy` / `base_implicit` | figura senza `depicts`, evidenza dal testo | sì, solo con l'interruttore | 1 |
| `generic` | oggetto senza variante per un fabbisogno con variante | **no** | — |
| `conflict` | altra variante | **no** | — |
| `off_topic` | fabbisogno base senza nessun termine del tema nella figura | no | — |

- La variante si confronta per uguaglianza esatta delle radici (stem
  leggero, mai prefissi: «different» ≠ «differential»); tutte le radici di
  una forma della variante chiesta devono stare fra quelle della figura.
  «single» e «point» sono parole della forma base (PROMPT 22 v3).
- Rappresentazione: disegno contro foto un livello in meno; grafico contro
  disegno o foto non copre.
- Nomi degli allestimenti equivalenti (setup, configuration, chain,
  system, rig, bench). Nessun sinonimo «velocimeter → vibrometer»: è anche
  l'anemometro laser Doppler dei flussi (errore trovato in M-A3).
- `FIGURE_PLAN_LEGACY_MATCH_ENABLED=false`: una figura senza `depicts` non
  copre mai un fabbisogno (precisione 0,53 in M-A3). Le figure vecchie si
  completano con `scripts/redescribe_figure_depicts.py`.
- Un indice per parola (`FigureIndex`) confronta ogni fabbisogno solo con
  le figure che condividono una parola del suo oggetto; test di
  equivalenza con la scansione completa.

**Misura M-A3** (4 lezioni di riferimento, 20 fabbisogni, figure etichettate
a vista; braccio A = solo testo, B = `depicts`):

| | A | B, primo giro | B dopo le correzioni | B dopo la verifica WP6 |
|---|---|---|---|---|
| precisione della figura scelta | 9/17 = 0,53 | 13/17 = 0,76 | 15/17 = 0,88 | 15/16 = 0,94 |
| recall sui fabbisogni con una figura corretta fra le candidate | 0,53 | — | 0,88 | 15/17 = 0,88 |
| M4.L6: sei tipologie coperte dalla figura giusta | 3/6 | 5/6 | 6/6 | 6/6 |

Le correzioni fra il primo giro e il finale: PROMPT 22 v2 e v3 (§23.1),
oggetto base senza le parole della variante, panoramiche a livello 2,
forma standard come base, sinonimo «velocimeter» tolto. Restano due
errori: una figura d'allineamento CSLDV che la Vision descrive come
«scanning» e un grafico ODS proposto per un confronto forme modali/ODS.
Dopo la verifica di WP6 il matcher esige anche le parole distintive della
variante chiesta («on-axis» non è «off-axis», «single-axis» non è
«three-axis») e, fra oggetti con la stessa testa, qualificatori comuni o
la variante chiesta nel nome («force sensor» non è «pressure sensor»); un
fabbisogno con variante non vuota non è mai base. Rigiocata M-A3: un should
perde la figura (l'oggetto della figura era diverso), un altro passa a una
figura corretta. Soglia del piano (0,95) sfiorata: limite dichiarato; il
revisore delle ridondanze e il docente vedono comunque la figura.

### 23.4 Assegnazione globale (`source_figure_assignment.py`, `…_service.py`, WP6)
- **Vincoli**: una figura al più una volta per lezione e per fabbisogno;
  tetto di riuso K contando gli usi fissi (lezioni che non si rigenerano)
  e le assegnazioni del giro; una figura già nella lezione resta sua anche
  oltre K ma conta per le altre (e le si tiene il posto finché non la
  prende); budget (b) del piano = min(`FIGURE_PLAN_MAX_PER_LESSON` 8,
  max(morbido, must pronti)), morbido = minuti // `FIGURE_SOURCE_MINUTES_PER_FIGURE`
  (4) fra il budget senza piano e il tetto; uno should non prende il posto
  di un must ancora coperibile.
- **Greedy** sugli archi ordinati per (bassa risoluzione, −livello,
  letteratura, −must, −in sequenza, −classe, −già nella lezione, numero di
  candidate, −punteggio, id): una `low` solo senza alternativa, prima la
  specificità (regola del committente, anche uno should più specifico
  batte un must generico di un'altra lezione), poi il documento del corso
  prima della letteratura. Prima la riserva della letteratura
  (`found_for_*`: fra le figure trovate per quel fabbisogno la migliore, se
  lo copre e nessuna figura di documento lo copre meglio), dopo una
  riparazione a scambio singolo (mai a livello o classe inferiori, mai su
  una riserva) e una seconda passata: prima le figure già nella lezione,
  poi le altre senza il posto tenuto per i must; da lì l'eccezione al
  tetto vale solo per le figure che la lezione ha tenuto (test di
  proprietà su 3000 istanze con figure già nelle lezioni: budget, unicità e
  K mai superato da chi non aveva già la figura; il codice di prima
  falliva 4 casi). Le figure già nel contenuto di una lezione partecipante
  che nessun suo fabbisogno usa contano come usi fissi, come nel
  ricontrollo sotto il lock.
- **Partecipanti**: la lezione che parte e quelle del corso in coda per la
  Fase 3 con i fabbisogni pronti; una lezione in generazione tiene le
  figure della sua offerta per `OFFER_TTL` (2 h).
- **Worker di Fase 3**: all'avvio `reserve` scrive l'offerta in
  `course_lesson.figure_assignment` sotto il lock di corso
  (`pg_try_advisory_xact_lock` con tentativi fino a 20 s, mai
  un'eccezione); alla materializzazione il lock si riprende fino al commit:
  il tetto di riuso si ricontrolla lì (prima due lezioni generate insieme
  potevano superarlo entrambe, ora la seconda toglie la figura con audit
  `reuse_cap`; test con una barriera nella materializzazione che senza lock
  fallisce sempre); dopo l'attesa del lock si ricontrolla l'annullamento;
  `settle` scrive la fotografia finale (figure collocate, fabbisogni legati
  all'offerta, mancati, collocazione) solo dopo una materializzazione
  riuscita e solo per l'offerta di quel giro (`run_token`). Il catalogo del PROMPT 3 resta quello lessicale
  fino al blocco del piano (WP8).
- **Duplicazione**: `found_for_*` rimappati sulle lezioni del corso nuovo.
  La copia è sempre in un'altra lingua, quindi fabbisogni, collegamenti e
  `figure_assignment` non si copiano (soggetti e need_id sono nella lingua
  del sorgente): si ricalcolano alla prima richiesta di Fase 3 (Fase D).
- **Misure Q3** (copia del corso, 35 lezioni con fabbisogni tutte in coda,
  2225 figure, 4120 archi): greedy + riparazione = ottimo dei must (flusso
  massimo) con K = 1, 2, 3 (63 must su 63 con candidate; 36 must senza
  candidate); fotografia completa ≤1,4 s, assegnazione pura p95 15 ms. Su
  istanze casuali piccole (2000 per K) il greedy resta sotto l'ottimo di
  un must nel 3,5-5,4% dei casi (tutti must), 6-9% contando la precedenza
  alla specificità: accettato, perché sui dati reali coincide.

### 23.5 Buchi per fabbisogno (letteratura aperta, WP7)
Con il piano attivo e i fabbisogni pronti, `check_lesson` non usa più il
criterio «almeno `FIGURE_SOURCE_MIN_PER_LESSON` figure pertinenti» (che non
vede le varianti scoperte, D1) ma `check_lesson_needs`:

- **Solo i fabbisogni scoperti** per l'assegnazione (nessuna figura li copre,
  o tutte sono al tetto di riuso); i must prima degli should. Nessuno
  scoperto → `done` con `reason: covered`, nessuna chiamata.
- **Ricerche dai fabbisogni** (niente chiamata dei termini del PROMPT 20):
  Commons con una ricerca per gruppo di varianti (il solo oggetto, risultati
  in cache nel giro) e un filtro lessicale sulla variante prima del
  download; OpenAlex per fabbisogno, prima su titolo e abstract
  (`title_and_abstract.search`), poi sul testo completo.
- **Verifica della variante**: la Vision (PROMPT 20) riceve la FIGURA
  CERCATA; la figura tenuta copre il fabbisogno solo se il suo `depicts`
  supera l'abbinamento di §23.3. Se lo copre le si scrivono
  `found_for_lesson_id`/`found_for_need_id` (riserva per l'assegnazione) e la
  ricerca del fabbisogno si ferma; una figura pertinente che non lo copre
  resta nel catalogo del corso (`kept_not_covering`).
- **Tetti**:
  - candidate: `FIGURE_LITERATURE_MAX_CANDIDATES_PER_NEED` (3, 1 per gli
    should) e `FIGURE_LITERATURE_MAX_CANDIDATES_PER_LESSON` (15);
  - dollari: `FIGURE_LITERATURE_MAX_COST_USD_PER_CHECK` (0,08 $, Vision e
    copie OpenAlex);
  - PDF a pagamento: `FIGURE_LITERATURE_MAX_PAID_PDF_PER_LESSON` (3);
  - corso: max(`FIGURE_LITERATURE_MAX_PER_COURSE`, fabbisogni pronti del
    corso), contando solo le figure esterne pronte e non escluse.

  Il tetto in dollari vale **per verifica**, retry compresi: la spesa dei
  tentativi precedenti si salva con l'errore (`spent_usd`) e riparte da lì.
  Ci si ferma **prima** di una Vision che lo supererebbe, stimandone il
  costo con la più cara vista nel giro (almeno 0,0012 $). La copia OpenAlex
  (0,01 $) entra nel costo della verifica (`openalex_copies`), non fra le
  chiamate AI. Una copia già pagata nel giro non si ripaga, e un lavoro
  ritrovato dalla seconda ricerca per lo stesso fabbisogno non si
  riestrae. Gli stessi tetti in dollari e di PDF valgono anche per la
  verifica senza piano, dove il tetto di candidate è salito da 8 a 15.
- **Esiti** in `figures_gap_stats.needs[need_id]` (`found` con la figura,
  `not_found`, `not_searched`), fusi fra i giri anche dopo un tentativo
  fallito (un trovato resta trovato); `needs_fp` è l'impronta dei
  fabbisogni verificati. Con fabbisogni pronti ma vecchi (scaletta cambiata
  dopo il calcolo, nuova versione del prompt) la verifica usa il criterio
  di prima e registra comunque la loro impronta: il tick non la riapre a
  ogni giro (prima ripartiva all'infinito e la Fase 3 non partiva, rilievo
  della verifica di WP7). Si riapre quando i fabbisogni vengono
  ricalcolati.
- **Richiesta e riapertura** (`_request_figure_gaps` nel tick della Fase 3):
  la verifica si chiede quando i fabbisogni non sono più in calcolo; una
  verifica `done` con un `needs_fp` diverso dall'impronta attuale (o fatta
  prima del piano) si riapre. Con i fabbisogni pronti la verifica continua
  anche dopo l'avvio della Fase 3 (niente `phase3_started`): le figure
  trovate servono alla rigenerazione successiva.
- **Limite dei corsi grandi (M-E, stima)**: i fabbisogni di 42 lezioni si
  calcolano in circa 100 s (M-N2), ma il worker dei buchi è sequenziale e
  una verifica con candidate da valutare dura da decine di secondi a
  qualche minuto: con tutte le lezioni chieste insieme molte partono prima
  della propria verifica (tetto `FIGURE_WAIT_MAX_MINUTES`). Le figure
  trovate dopo servono alla rigenerazione; al docente si consiglia di
  rigenerare per modulo.
- **Misura M-L2 non eseguita in locale**: Commons risponde 403 alle
  richieste di un client senza contatto nello User-Agent (robot policy di
  Wikimedia; in produzione `PAPERS_POLITE_EMAIL` lo fornisce) e in locale
  non c'è `OPENALEX_API_KEY` (D5). Il ramo è coperto dai test
  (`tests/test_figure_gaps_needs.py`). Sulla copia del corso del docente, con
  i documenti estratti, M4.L6 e M5.L1 risultano coperte (`covered`, nessuna
  chiamata); restano scoperti tre should (M4.L7 microstrutture e validazione
  modale, M5.L7 certificato di taratura).

### 23.6 Blocco del piano nella Fase 3 (`source_figure_plan.py`, WP8)
Con il piano attivo, un'offerta per la lezione e
`FIGURE_PLAN_IN_PROMPT_ENABLED=true`, il catalogo delle figure di fonte del
messaggio user del PROMPT 3 è il **catalogo del piano**:

- per sezione, per ogni fabbisogno coperto la figura assegnata e al più 2
  alternative (le figure assegnate si riservano prima: un'alternativa non
  toglie la figura assegnata a un altro fabbisogno), in coda al più 2
  figure facoltative del catalogo lessicale; i fabbisogni scoperti non
  compaiono; tetto 8000 caratteri con taglio che non tocca mai la figura
  assegnata a un must; soggetti neutralizzati;
- riga del budget (b) con preambolo e coda identici alla riga senza piano
  (M7) e, in mezzo, le regole del piano (figura assegnata nella sua
  sezione, sequenze in ordine, mai due figure per la stessa voce);
- la versione attuale della lezione (rigenerazione) dice a quale voce del
  piano corrisponde ogni figura di fonte già collocata;
- schema `source_figures` invariato (cambia solo l'elenco degli id).

Dopo la fusione, `apply_placement` confronta le scelte col piano senza
spostare nulla nel testo: `placed` (nella sua sezione), `misplaced` (in
un'altra), `auto_placed` (must non scelto, inserito in fondo alla sua
sezione se la sezione c'è e il budget lo consente, con il soggetto come
didascalia), `missing` (con il motivo: `no_anchor`, `budget`),
`order_warning` sui gruppi di sequenza citati fuori ordine; di due figure
per lo stesso fabbisogno resta la prima citata. L'esito va nella fotografia
(`figure_assignment.placement`) e nei conteggi di
`content_tokens.source_figures.plan`.

Interruttore `FIGURE_PLAN_IN_PROMPT_ENABLED=false`: nessuna offerta, catalogo
lessicale come prima (test).

**Correzioni dalla verifica avversariale di WP7-WP8.** Ogni correzione ha un
test.
- **Alternative e residuo.** Non si offre una figura assegnata a
  un'altra lezione del giro se la porterebbe oltre il tetto K (le figure
  già nel contenuto della lezione passano). Il modello l'avrebbe scelta, il
  ricontrollo l'avrebbe tolta e il must sarebbe rimasto scoperto.
  L'offerta le elenca in `held_elsewhere`.
- **Taglio al budget.** Con il piano tiene prima le figure dei must, poi
  degli should, poi il residuo; la seconda figura di uno stesso fabbisogno
  viene dopo tutte le altre. Senza piano resta l'ordine di citazione.
- **Collocazione dopo i ricontrolli.** Una figura tolta per politica o
  tetto di riuso riporta il suo fabbisogno a `missing`
  (`dropped_<motivo>`). La fotografia lega ogni fabbisogno alla figura
  davvero collocata, anche un'alternativa.
- **Worker.**
  - `reserve` e il catalogo del piano stanno in savepoint separati; un
    errore del catalogo lascia quello lessicale;
  - dopo un rollback la lezione si rilegge;
  - l'annullamento durante l'attesa del lock si registra prima del
    rollback (prima: MissingGreenlet).
- **Titoli di sezione.** Id e titolo della scaletta nel blocco del piano
  sono neutralizzati e su una riga: non possono chiudere il blocco dati.

**M7b (26/09/2026, copia locale del corso, 3,86 $).** Rigenerazione reale
della Fase 3 (gpt-5.5) di M4.L6, M4.L7, M5.L7, M5.L1 e M3.L4 (controllo) in
due bracci: A con il piano fuori dal prompt (catalogo lessicale), B con il
piano nel prompt.

| Lezione | Generate A → B | Di fonte A → B | Collocazione (B) |
|---|---|---|---|
| M4.L6 | 5 → 5 (stessi formati) | 3 → 6 | 6 su 6 `placed`, nessun fabbisogno scoperto |
| M4.L7 | 5 → 5 (stessi formati) | 3 → 3 | 3 su 3 `placed` |
| M5.L7 | 4 → 4 (un dot diventa mermaid) | 3 → 4 | 4 su 4 `placed` |
| M5.L1 | 4 → 4 (stessi formati) | 2 → 4 | 3 su 3 `placed`, più una facoltativa |
| M3.L4 | 4 → 5 | 3 → 3 | nessun piano (nessuna offerta) |

- **Budget (a).** Il numero delle figure generate non cambia nelle 4 lezioni
  col piano; M3.L4, che non ha il piano, varia di ±1 per la varianza del
  modello.
- **Collocazione.** C1 = 16/16 nella propria sezione, con 0 `misplaced`,
  0 `auto_placed` e 0 `missing`.
- **Costo.** Il costo del PROMPT 3 per lezione non cambia: 0,37-0,45 $ in
  entrambi i bracci.

### 23.7 Editor, revisore, slide e discorso (WP9)
**Vista dei fabbisogni** (`services/figure_needs_view.py`, funzione pura
calcolata alla lettura e mai salvata; esposta nel DTO della lezione come
`figure_needs_view` e `figure_needs_summary`). Per ogni fabbisogno pronto,
nell'ordine delle sezioni, con etichetta N1…Nn, combina:
- la fotografia dell'assegnazione: prima il legame (`bound`, anche con
  un'alternativa scelta dal modello), poi l'asset della collocazione, poi
  la figura offerta e le alternative;
- il contenuto attuale, che il docente può aver modificato;
- l'esito della letteratura (`figures_gap_stats.needs`);
- i collegamenti del docente.

Gli stati possibili sono:
- `placed`: nel contenuto, citata nella sua sezione (anche dentro un
  esempio o una tabella citati nella sezione);
- `misplaced`: citata in un'altra sezione, o nell'introduzione o nella
  sintesi;
- `missing`: il piano aveva una figura che nel contenuto non c'è, oppure la
  figura c'è ma il testo non la cita (`not_cited`, non conta come
  collocata);
- `uncovered`: nessuna figura, con il motivo (`no_candidate`, `reuse_cap`,
  `budget`, `duplicate_in_lesson`, `not_planned`) e l'esito della
  letteratura (`found`, `not_found`, `not_searched`);
- `dismissed`: il docente ha detto «Non serve».

La vista non contiene nomi di documenti.

**Collegamenti del docente** (migrazione 0043, `course_lesson.figure_need_links`
JSONB). Li scrive solo il CRUD, con
`PUT /courses/{id}/lessons/{lesson_id}/figure-needs/{need_id}` e corpo
`{state: "dismissed" | "linked" | null, asset_id}`:
- `null` torna allo stato calcolato;
- `linked` richiede un asset della lezione. Gli errori sono 422 con
  `meta.errors[].loc`: `figure_need_unknown`, `figure_need_state_invalid`,
  `figure_need_asset_unknown`;
- `content_raw` e `content_modified_at` restano invariati. L'azione va
  nell'audit come `course.lesson.figure_need.updated`;
- la duplicazione non copia fabbisogni né collegamenti (copia in un'altra
  lingua, §23.4).

**Editor** (`LessonFigureNeedsPanel.tsx`):
- **Etichetta nella riga della lezione:** «Figure x/y», cioè i must nel
  contenuto su quelli attivi. Nel tooltip ci sono le figure da trovare e
  quelle fuori sezione.
- **Pannello «Figure consigliate»:** dal 26/09 sta nella finestra di
  modifica, non più nella vista della lezione (§24). Elenca i fabbisogni per
  sezione con stato e motivo come arrivano dal backend; le azioni sono
  «Inserisci», «Non serve» e «Ripristina». «Collega» è stato tolto (l'API
  resta per i collegamenti già salvati).
- **Upload di lezione** (`upload_lesson_asset`): conserva il PNG
  (`save_upload_image(preserve_format=True)`) invece di ricodificarlo in
  JPEG. L'EXIF si toglie e il tetto dei pixel resta.

**PROMPT 19 v2** (`FIGURE_REDUNDANCY_SUBJECT_CHECK_ENABLED`, default true).
Vale per una figura di fonte che la collocazione lega a un fabbisogno:
- il revisore riceve il soggetto fra delimitatori di dati;
- risponde anche `subject_match`, salvato nel verdetto come avviso.

Senza legame il messaggio e lo schema sono quelli di prima, byte per byte.

**Fase 4 e 5.** `figure_sequences(lesson)` restituisce i gruppi di sequenza
con almeno due figure **distinte** citate nel contenuto, solo col piano
attivo:
- **PROMPT 5:** riceve «una slide per ciascuna, in quest'ordine, con il
  nome della variante nel titolo»;
- **PROMPT 6:** riceve i tempi: 25-45 s per la slide di una figura di
  fonte, 15-25 s per le successive di una sequenza (M-S, §23.8);
- **8c:** aggiunge le slide mancanti nell'ordine di citazione nel testo.

Senza sequenze, o col piano spento, entrambi i messaggi restano identici.

**Ripiego inline e worker dei fabbisogni.** Il ripiego vale solo per
fabbisogni già chiesti (stato non nullo). Se il worker sta calcolando i
fabbisogni della lezione (`processing`), la Fase 3 non li ricalcola e
procede col catalogo lessicale. Se sono in coda (`pending`), la Fase 3 li
prende con un UPDATE condizionale; se il calcolo non riesce tornano in
coda. Così non si paga due volte e l'offerta usa gli stessi fabbisogni
salvati.

**Correzioni dalla verifica di WP9** (tutte con test): legame della
fotografia nella vista, figure non citate, citazioni in esempi e tabelle,
sequenze senza doppioni e con l'interruttore del piano, motivo
`duplicate_in_lesson` tradotto, coordinamento
del ripiego inline; test del collegamento nel worker (taglio per priorità,
PROMPT 19 v2, fotografia con un'alternativa, collocazione dopo il lock).

**Non fatto (dichiarato).** Mancano tre parti del progetto:
- un endpoint dei candidati per fabbisogno;
- l'upload diretto dal pannello;
- il badge «già usata in».

Oggi il percorso è: editor della lezione (upload o selettore delle figure
di fonte), poi «Collega». M-S (misura di slide e durata) non è stata
eseguita: i tempi del PROMPT 6 sono quelli del piano.

### 23.8 Prova finale sulla copia del corso (WP10, 26/09/2026)
Copia locale del corso del docente, codice finale, configurazione di
produzione (gpt-5.5, reasoning `none`). Fase 3 di 5 lezioni dalla versione
del docente, Fasi 4 e 5 di M4.L6, M4.L7 e M5.L7; dispense, PDF delle slide e
frame esportati e misurati. Spesa della prova: 3,04 $.

| Criterio | Soglia | Esito |
|---|---|---|
| M4.L6, le 6 tipologie | 6 su 6 nella propria sezione e in ordine, o dichiarate | **6 su 6** `placed`, nessun avviso d'ordine (prima: 2 figure di fonte) |
| Generica sopra un fabbisogno di variante (H2) | 0 | 0 (relazioni `exact`, `exact_mixed`, `specialized`) |
| Lezioni di riferimento: must coperti o dichiarati | tutti | M4.L7 3/3 (3 should scoperti, `no_candidate`), M5.L7 3/3 (1 scoperto), M5.L1 1/1 |
| M3.L4 (controllo) | ≤1 must di fonte; budget (a) ±1 | nessun fabbisogno; figure generate 5 contro 4 del docente (±1) |
| Figure di fonte in stampa | 0 sotto 100 ppi | 0 (15 ≥200, 5 fra 150 e 199) |
| Frame | ingrandimento ≤1,25 | massimo 1,239 |
| PDF delle slide | ≥135 ppi | minimo 137 |
| Costo della Fase 3 per lezione | entro M-A4 +50% | 0,27-0,34 $ con il piano (0,35-0,45 $ senza, M7b braccio A) |

**M-S (tempi di slide e discorso, 15 minuti).**

| Lezione | Slide (prima → dopo) | Slide di figura | Secondi medi per slide di concetto |
|---|---|---|---|
| M4.L6 | 20 → 25 | 9 → 13 | 46,1 → 38,1 (−17%) |
| M4.L7 | 24 → 21 | 9 → 9 | 40,4 → 47,9 (+19%) |
| M5.L7 | 21 → 26 | 8 → 12 | 57,6 → 33,4 (**−42%**) |

- **Regola del piano:** oltre il 25% i tempi delle slide di sequenza
  successive scendono a 15-25 s nel PROMPT 6. Il cambio è applicato, ma
  l'effetto è minimo: la normalizzazione a 900 s riscala tutte le durate,
  e le slide di sequenza restano a 28-30 s.
- **Secondo passo non applicato:** il tetto morbido per should e residuo a
  3 non è stato introdotto, per due motivi:
  - toglierebbe una sola figura a M5.L7;
  - il confronto con la versione del docente non è omogeneo, perché nel
    discorso di produzione le slide delle figure di fonte avevano 0 s.

  Decisione lasciata al docente, con il limite dichiarato: con 6-8 figure
  a 15 minuti le slide di concetto hanno meno tempo.

### 23.9 Revisione finale (Fase D, WP10)
Cinque revisori in sola lettura (correttezza, costo e prestazioni,
attribuzione, tipografia, lingue), al più tre in parallelo, e un
confutatore per ogni rilievo medio o alto: 9 rilievi confermati (nessuno
alto) e 11 bassi. Corretti, con test:
- **Lezioni partite insieme.** I worker della Fase 3 prendono più lezioni
  nello stesso giro. Una lezione `processing` senza offerta valida
  partecipa ora all'assegnazione: prima la prima offerta ignorava la
  domanda delle altre.
- **Figure nel contenuto di altre lezioni.** Nella seconda passata
  dell'assegnazione torna libero solo il posto della lezione corrente
  (`release`). Le figure nel contenuto delle altre restano occupate finché
  quelle non si rigenerano, come al ricontrollo sotto il lock.
- **Offerte in corso.** Le offerte valide delle lezioni in generazione
  escludono le figure al tetto K dal residuo del catalogo del piano
  (`held_elsewhere`) e dal catalogo lessicale (`offered_saturated`).
- **Decisioni del docente.** «Non serve» e le figure collegate dal docente
  escono da assegnazione, budget, catalogo del piano, collocazione e
  verifica dei buchi (`figure_plan_service.active_needs`).
- **Corsi esistenti senza `depicts`.** Se più del 20% delle figure dei
  documenti non ha `depicts` corrente, la verifica per fabbisogno non
  cerca nella letteratura (`reason: depicts_missing`, nessuna spesa) e
  registra l'impronta dei fabbisogni. Passo manuale: §18.6.
- **Coda dei buchi.** Prima le verifiche che bloccano una Fase 3 in coda,
  poi quelle che servono solo alla rigenerazione successiva.
- **Figure inserite dalla collocazione.** La didascalia è la prima frase
  della descrizione della figura, non il testo della richiesta («Schema
  pubblicato o foto…»). Un membro di una sequenza entra dopo il paragrafo
  del membro precedente, non in fondo alla sezione.
- **Duplicazione.** Fabbisogni, collegamenti e fotografia non si copiano
  (copia in un'altra lingua).
- **Offerta aperta.** Una rigenerazione in corso, fallita o annullata non
  fa risultare «mancanti» le figure ancora nel contenuto: l'offerta porta
  la fotografia chiusa precedente (`previous`).
- **Indice** su `found_for_lesson_id` (FK con ON DELETE SET NULL, nella
  0042 non ancora rilasciata).
- **PROMPT 20.** Il blocco del fabbisogno si chiama «WANTED FIGURE» nei
  corsi non in italiano, come nel system prompt inglese.

Rilievi bassi dichiarati, senza correzione:
- **Worker dei fabbisogni.** Lavora a lotti con barriera, a circa metà
  della portata misurata in M-N2. Una richiesta nuova durante il calcolo
  può far pagare il PROMPT 22 due volte.
- **Attese.** L'attesa dei fabbisogni e quella dei buchi si sommano: la
  Fase 3 può aspettare fino a circa 2× `FIGURE_WAIT_MAX_MINUTES`.
- **OpenAlex.** Lo stesso lavoro si ri-estrae per fabbisogni diversi. La
  copia pagata invece non si ripaga.
- **Slide aggiunte da 8c.** Con copertura parziale di una sequenza le
  slide mancanti finiscono dopo l'ultima slide della sezione.
- **`subject_match`.** Il verdetto del PROMPT 19 v2 si salva ma l'editor
  non lo mostra.
- **Etichette N1…Nn.** Quelle del catalogo nel PROMPT 3 (solo i fabbisogni
  coperti) non coincidono con quelle del pannello.

## 24. Figure inserite in automatico e «Inserisci» (26/09/2026)

Richiesta del docente dopo il rilascio del piano: le figure devono finire
nella lezione da sole, e il pannello deve stare nella modifica. Scelte
dell'utente: pannello nella modifica con «Inserisci»; estrazione dei
documenti manuale come prima; una figura trovata dopo la generazione entra
da sola solo nelle lezioni non approvate; le figure consigliate entrano da
sole entro il budget; ogni figura inserita dal sistema ha la frase che la
introduce.

**Fine della Fase 3** (`source_figure_plan.apply_placement`). Le figure del
piano che il PROMPT 3 non ha citato entrano nella loro sezione: prima le
obbligatorie, poi le consigliate, finché c'è posto nel budget del piano. Un
membro di una sequenza entra subito dopo la figura del membro precedente
citato nella sezione, o prima del paragrafo che introduce il successivo;
altrimenti in fondo alla sezione. La didascalia è la prima frase della
descrizione della figura. Le frasi introduttive si scrivono subito dopo la
collocazione ma entrano nel testo solo prima della materializzazione e solo
per le figure sopravvissute ai ricontrolli (politica, tetto di riuso sotto
il lock): mai una frase senza la sua figura.

**Frase che introduce la figura** (PROMPT 23,
`openai_figure_intro_service.py`). Il testo delle dispense colloca ogni
figura con `[FIG:id]` su una riga propria, dopo il paragrafo che la
introduce, e la richiama a parole. Per ogni figura inserita dal sistema una
chiamata breve (`gpt-4.1-mini`) scrive una o due frasi nella lingua del
corso, dal testo che precede e dalla descrizione della figura; la frase
entra come paragrafo subito prima del tag. Output ripulito (niente tag,
niente «Fonte») e neutralizzato. Ripiego senza chiamata, su errore o con
`FIGURE_INTRO_SENTENCE_ENABLED=false`: «La figura seguente mostra …» /
«The following figure shows …»; nelle altre lingue la figura entra senza
frase. Costo in `figure_needs_usage` (fase `figure_needs` della dashboard).

**Completamento dopo la generazione** (`source_figure_fill.fill_lesson`,
`FIGURE_AUTO_FILL_ENABLED`). Parte quando una figura adatta arriva dopo:
- la verifica dei buchi trova una figura per un fabbisogno (worker dei
  buchi, solo quella lezione);
- finisce l'estrazione di un documento (worker delle figure, tutte le
  lezioni del corso, fuori da `HEAVY_JOB_LOCK`).

Condizioni: piano attivo, lezione `ready` (mai `approved`, in coda o in
generazione), contenuto non superato da modifiche a struttura o
architettura, fabbisogni pronti. Le frasi (PROMPT 23) si scrivono PRIMA di
prendere il lock di corso; poi, sotto il lock e con la riga bloccata
(`SELECT … FOR UPDATE`), si scrive solo se stato, date, contenuto,
fabbisogni e collegamenti sono quelli letti e se le figure stanno ancora
nel tetto di riuso. Un'approvazione, un salvataggio del docente o una
rigenerazione arrivati nel frattempo vincono; lock occupato o riga cambiata:
si salta e il prossimo evento ci riprova. Il giro sul corso ricarica il
corso per ogni lezione: un errore su una lezione non ferma le altre. Per ogni
fabbisogno scoperto e non «Non serve» la figura migliore del corso (stesso
abbinamento, tetto di riuso K e budget del piano; prima i must, a parità di
livello prima le figure non a bassa risoluzione) entra nella sezione con la
sua frase. Il sistema aggiorna `content_generated_at` (mai
`content_modified_at`, riservato alle modifiche manuali): PDF e slide
risultano da riesportare. La fotografia dell'assegnazione registra il legame
(`bound`, collocazione `auto_placed` con `filled: true`); audit
`course.lesson.content.figures_filled` con il motivo (`literature`,
`extraction`).

**Editor** (`LessonContentEditDialog.tsx` → `LessonFigureNeedsPanel`).
Nuovo gruppo «Figure consigliate» nella finestra di modifica:
- per ogni figura scoperta per cui il corso ha una candidata
  (`GET …/figure-needs/candidates`) compare «Figura proposta: …» e
  «Inserisci»;
- «Inserisci» (`POST …/figure-needs/{need_id}/insert`) riceve il testo
  della sezione nella bozza e restituisce il testo con frase e figura, più
  l'asset da aggiungere: la bozza si aggiorna, il contenuto si salva con
  «Salva» (PATCH del contenuto, come ogni modifica manuale). L'endpoint
  registra anche il legame fabbisogno → figura (`figure_need_links`, stato
  `linked`): vale quando la figura entra nel contenuto salvato (la voce
  risulta collocata e il completamento non ne aggiunge una seconda), resta
  inerte se la bozza si scarta. Se la sezione cambia mentre la richiesta è
  in corso, il testo restituito non si applica (avviso: premere di nuovo);
- «Non serve» e «Ripristina» restano; nella vista della lezione resta solo
  l'etichetta «Figure x/y».

**Test**: `test_source_figure_fill.py` (PROMPT 23, inserimento, lezioni
escluse, endpoint, agganci), `test_source_figure_plan.py` (consigliate
entro il budget), `test_source_figure_materialize.py` (frase della figura
inserita a fine Fase 3), `test_frontend_source_figures.py` (pannello nella
modifica). Nei test il PROMPT 23 non esce mai in rete (fixture in
`conftest.py`).

**Limiti.**
- Una figura trovata dopo entra con la sola frase introduttiva: il resto
  del testo della sezione non la cita. Per un discorso integrato serve la
  rigenerazione.
- Con il completamento il sistema modifica il contenuto di lezioni non
  approvate senza un'azione del docente; l'audit e la fotografia dicono
  che cosa è entrato. Una lezione approvata non cambia mai.
- Se il docente salva una bozza aperta prima del completamento, il suo
  salvataggio sostituisce il contenuto (vince la versione del docente); se
  salva durante il completamento, il completamento si annulla.
- Dashboard: il completamento aggiorna `content_generated_at`, che data
  anche il costo della Fase 3 (si sposta nelle finestre 7/30 giorni, non si
  somma due volte); il costo del PROMPT 23 sta in `figure_needs_usage`,
  datato all'ultimo calcolo dei fabbisogni.

**Verifica (3 verificatori in sola lettura).** Corretti con test: legame
fabbisogno → figura all'«Inserisci» (prima la voce restava scoperta e il
completamento ne aggiungeva una seconda); approvazione, salvataggio o
rigenerazione durante il completamento (prima venivano sovrascritti: ora
frasi fuori dal lock e ricontrollo a riga bloccata); giro sul corso che si
fermava dopo un errore; frase introduttiva orfana a fine Fase 3; «Inserisci»
che sovrascriveva il testo scritto durante la richiesta; `clean_sentence`
(«fonte:» minuscolo, parentesi aperte) e ripiego che abbassava le sigle.
Dichiarati: le slide mancanti di una sequenza aggiunte da 8c dopo l'ultima
della sezione e la datazione dei costi in dashboard (sopra).
