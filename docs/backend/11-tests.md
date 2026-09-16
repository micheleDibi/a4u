# Backend 11 — `tests/`

Test pytest (pytest 9, `pytest-asyncio` con loop di sessione). I test HTTP
usano `httpx.AsyncClient` con `ASGITransport(app)` e una sessione SQLAlchemy
isolata per fixture; i test «puri» (moduli leaf delle figure, prompt, script)
non toccano il DB. Inventario rigenerato da `pytest --collect-only -q` sul
branch `feat/academic-figures` (7 settembre 2026): **37 moduli, 877 item**;
il conteggio degli item per modulo è indicato fra parentesi.

---

## Esecuzione

```bash
# Postgres di sviluppo (container a4u-postgres; il DB a4u_test è ricreato dalla fixture _engine)
docker compose up -d postgres

# Suite completa, dalla cartella backend/
cd backend
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python3 -m pytest -q
```

- **`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`** (macOS): WeasyPrint
  carica Pango/GObject con `cffi`; senza la variabile l'import fallisce con
  `cannot load library 'libgobject-2.0-0'` (in locale i test PDF saltano,
  ma anche `app.main` importa WeasyPrint indirettamente). Su Linux/Docker
  non serve.
- **Interprete**: usare esplicitamente il Python 3.12 del backend
  (`python3 -m pytest`, non il `pytest` sul `PATH`, che in locale può
  appartenere a un altro interprete). Nessun venv è richiesto.
- **`JWT_SECRET`**: `conftest.py` lo imposta per la suite. Ogni comando
  manuale che costruisce `Settings` fuori da pytest (script in `scripts/`,
  probe con `create_app()`) richiede `JWT_SECRET` di almeno 32 caratteri
  in ambiente quando manca `.env`, ad esempio
  `JWT_SECRET=$(printf 'x%.0s' $(seq 1 40))`.
- **Dipendenze opzionali**: i test che richiedono binari o rete
  **saltano con motivo esplicito**, mai falliscono — Playwright + Chromium
  e `cdn.jsdelivr.net` (Mermaid 11 reale), `dot` (Graphviz), `vl_convert`,
  `sympy`/`matplotlib`/`numpy`, `weasyprint`/`pypdf`, l'albero
  `../frontend` (test di specchio con i locale), Node ≥ 22 con
  `--experimental-strip-types` (parità con le copie TypeScript). In locale
  con l'ambiente completo la baseline è **tutta verde senza skip
  ambientali** (i test Playwright e D8 girano davvero); in CI (nessun
  Chromium) i test D8 e della palette Mermaid saltano.
- Selezione: `python3 -m pytest -q tests/test_figure_render_service.py -k vegalite`.

---

## `tests/conftest.py`

**Scopo**: definire fixture comuni e impostare le env vars di test.

### Setup env

```python
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("JWT_SECRET", "test-secret-with-at-least-32-bytes-padding-here")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://a4u:a4u_dev_password@localhost:5432/a4u_test",
)
```

In CI (GitHub Actions) `DATABASE_URL` viene sovrascritta dal workflow.

### Fixture

> **Nota (pytest-asyncio ≥ 1.0)**: la vecchia fixture override
> `event_loop` non esiste più. Il loop condiviso di sessione (necessario
> all'engine session-scope) si dichiara in `pyproject.toml`:
> `asyncio_default_fixture_loop_scope = "session"` +
> `asyncio_default_test_loop_scope = "session"`.

#### `_engine` (session-scope, async)

Crea un engine SQLAlchemy async, abilita `citext`, droppa+ricrea tutto lo
schema all'inizio della sessione, droppa al teardown. **Riutilizzato** da
tutti i test. Importa `app.models` come modulo per registrare i metadata
di tutti i modelli.

#### `db` (function-scope, async)

Apre una `AsyncSession` e fa `rollback()` al termine. Usata per test che
toccano direttamente il DB senza HTTP.

#### `seeded_db` (function-scope, async)

Come `db` ma esegue `ensure_seed` prima di yieldare (ruoli, permessi,
traduzioni).

#### `client` (function-scope, async)

`AsyncClient` collegato all'app: monkeypatcha `app.db.session.
async_session_factory` e `engine` con quelli di test, esegue `ensure_seed`,
usa `create_app()` con `app.dependency_overrides[get_db]`, imposta
`headers={"Origin": "http://localhost:5173"}` per il middleware CSRF,
`base_url="http://testserver"`.

#### `random_email`

`f"user-{uuid.uuid4().hex[:8]}@a4u-tests.it"` (il TLD `.local` è rifiutato
da `email-validator` 2.3).

---

## `tests/course_builders.py`

Builder condiviso per i test del dominio corsi: `build_course(db, ...)`
crea org + corso + moduli + lezioni in un colpo solo, con stati
configurabili per fase (`status`, `module_status`, `content_status`,
`slides_status`, `speech_status`, `with_structure`, `with_assessment`,
…) + helper `find_lesson`. Più i builder di payload AI:
`build_document_summary` / `build_course_document` (Appendice A) e
`build_lesson_content_output(*, …, visual_assets=None)` (output §6.3
minimo valido, con sezioni, `coverage_check` e — dal branch delle figure
— `visual_assets` parametrizzabili). Usato anche dagli script di smoke
(`scratchpad/consegna/smoke_seed.py`) e dalla run sintetica dello script
di rivalidazione.

## `tests/helpers/`

`helpers/slow_target.py`: bersagli reali per `figure_compute.isolated.
run_isolated` — `echo`, `big_result` (risultato più grande del buffer
della pipe), `sleep_forever` (il padre deve uccidere il figlio), `boom`
(eccezione nel figlio). Devono stare su disco: il figlio `spawn`
reimporta il modulo e un monkeypatch nel padre non lo raggiunge.

## `tests/fixtures/`

- `figure_numbering_cases.json` — casi condivisi fra `figure_numbering.py`
  e `lib/figureNumbering.ts` (duplicati, id mancante, case diverso,
  `[fig:x]` ignorato, non citati in coda, prefissi «Figura N.»);
- `mermaid_v10_sample.svg` / `mermaid_v10_sample.stripped.svg` — SVG di
  Mermaid 10.9.4 preso da un asset esistente e la sua versione senza
  `max-width` (livello L2 della regressione zero, byte-identico);
- `mermaid11_flowchart.svg` — SVG generato in locale con Mermaid 11.17.2
  (rigenerabile con `A4U_WRITE_FIXTURES=1 pytest tests/test_mermaid_no_foreignobject.py`).

---

## Piattaforma, auth, permessi

### `tests/test_health.py` (2)

`GET /api/v1/system/health` → 200 `status: "ok"`; `GET /system/ready` →
`db: "ok"`.

### `tests/test_auth_flow.py` (2)

Login/logout/`me` end-to-end via HTTP (cookie `access_token`, refresh
revocato al logout → 401) e credenziali non valide → 401.

### `tests/test_permissions.py` (4)

Resolver dei permessi con `seeded_db`: default del ruolo `manager` = seed;
override di org che concede; override di membership che revoca; platform
admin con tutti i codici. Helper `_setup_user_membership(db, *,
role_code)`, riusato dai test HTTP delle figure.

### `tests/test_admin_user_management.py` (7)

Endpoint admin `/users`: set password (revoca dei refresh, hash cambiato,
password debole rifiutata), unicità e cambio email, divieto di
disattivarsi o retrocedersi, conteggio degli altri platform admin attivi.

### `tests/test_profile_self_service.py` (5)

`PATCH /auth/me` (nome), cambio password (flusso completo), cambio email
(password corrente errata, unicità, successo).

---

## Dominio corsi — documenti e pipeline

### `tests/test_document_chunking_merge.py` (11)

Pre-processing dei documenti a chunk: pianificazione (soglia singola,
determinismo con overlap e merge della coda, confini di pagina, scala su
input grandi), `fingerprint`, merge dei riassunti (dedup esatta e
approssimata, ordine di prima occorrenza, voto della lingua, budget
globale), estrazione del testo (equivalenza su `.txt`, hard cap dei
segmenti, PDF con pagine vuote).

### `tests/test_document_citation_policy.py` (11)

Politica di citazione dei documenti riservati: matcher (contenimento,
stem del paper, titoli brevi ignorati), contesto golden senza campi
identificativi, riga «Fonte» solo per i citabili, filtro della
bibliografia riservata (e errore se svuota la bibliografia
introduttiva), cambio di politica che ripulisce e riaccoda, prompt a
chunk con lo stesso hardening.

### `tests/test_document_worker_chunked.py` (7)

Worker dei documenti a chunk: happy path, fallimento e ripresa, kill-switch
che torna al single-shot e cancella i chunk, documento piccolo in
single-shot, guardia di riaccodamento, run superata che non sovrascrive,
documento cancellato a metà run.

### `tests/test_lesson_document_selection.py` (17)

Selezione per lezione degli estratti documentali (grounding del PROMPT 3):
test puri su `lesson_document_selection` e integrazione su
`build_user_prompt` con il kill-switch acceso/spento (ordine «FONTI E
ANCORAGGIO» < «REQUISITI — TESTO», presenza/assenza di `RIFERIMENTI`).

### `tests/test_lesson_coverage_resolver.py` (18)

Risoluzione dei riferimenti di contabilità §6.4 (puro): codici
`O1`/`[O2]`/`o3`, testo verbatim, varianti tipografiche, troncamenti,
anti-falso positivo (parafrasi di un fratello, frammento ambiguo), temi
case-insensitive, dedup e ordine.

### `tests/test_lesson_content_objective_ids.py` (18)

Codici obiettivo end-to-end (`seeded_db`): prompt utente con `- [O1] …`,
`build_lesson_content_json_schema` che inietta l'`enum` senza mutare la
costante (e ritorna la costante per identità con argomenti vuoti), guasto
di produzione riprodotto che ora materializza `ready`, `coverage_check`
derivato, output perfetto persistito byte-identico, reset di
`content_attempts`.

### `tests/test_course_status_model.py` (2)

`COURSE_STATUSES` 1:1 con `COURSE_STATUS_RANK` e CHECK `ck_course_status_valid`
sui 22 valori.

### `tests/test_course_pipeline_gates.py` (19)

Gate per-unità delle Fasi 2-5: rigenerazione oltre la fase, corso
terminale, gate sul solo modulo della lezione, struttura mancante,
generate-all che filtra, `module_has_content`, `regenerate_module_lessons`,
Fase 4/5 su `approved` secco, assessment escluse, monotonia dei recompute.

### `tests/test_course_collateral_gates.py` (15)

Glossario (rank ≥ `architecture_approved`, `archived` bloccato),
`_ensure_editable` del CRUD architettura, approve-all tolleranti,
`update_course` status (publish/archive, valori arbitrari → 409,
riattivazione ricalcolata), `normalize_course_status_from_data`.

### `tests/test_migration_0034_normalization.py` (6)

Equivalenza logica della migrazione 0034 (`importlib` sul modulo della
migrazione): corso regredito → milestone derivata, corso di sole
assessment non promosso, parziali fermi, target di duplicazione saltati,
`published`/`archived` intatti, idempotenza.

### `tests/test_remote_storage.py` (26)

Layer di storage pluggable: mapping path-DB → key namespaced, backend
locale, URL pubblici; backend OVH contro un server FTP in-process
(`pyftpdlib`) quando disponibile.

### `tests/test_measure_register.py` (19)

Test puri di `scripts/measure_register.py` (normalizzazione della prosa,
split delle frasi, indicatori del registro, report). L'import
`from scripts.… import` funziona perché `backend/` è in `sys.path`
(rootdir) e `scripts/` è un namespace package.

---

## Prompt (registro accademico e figure)

### `tests/test_prompt_register.py` (20)

Blocco condiviso `prompt_register` nei prompt di Fase 3/4/5: composizione,
ordine dei sette marcatori (LINGUA ultima), stringhe obbligatorie
(«DELIMITATORI MATH», «DIVIETI ASSOLUTI», `coverage_check`, …), guardie di
lunghezza `MAX_SYSTEM_P3` (22.500) / `P4` (14.500) / `P5` (12.500) anche
sulle varianti con i default, determinismo (`_p3() == _p3()`), nessun tic
del corpus. Il testo dei prompt è statico (A19).

### `tests/test_prompt_composition_bugs.py` (14)

Bug collaterali della composizione dei prompt: etichette tassonomia
vuote, segnaposto lasciati letterali in P4/P5, `{{`/`}}` vietati nel P4
renderizzato, `_format_current_lesson_phase3` che serializza gli asset
come `- {asset_id} [{format}]: {caption}` (parametrizzato su
mermaid/vegalite/dot/function) con la guardia `"(?)" not in text`.

### `tests/test_prompt_figures.py` (13)

Le quattro famiglie di figure nei prompt (WP3): il blocco «FORMATI DELLE
FIGURE» di P3 elenca i tipi Mermaid ammessi ed esclusi (D8), le regole
D5, lo schema compatto di `FunctionFigureSpec` (D9) e tre esempi minimi
(Vega-Lite, DOT, `function`) che devono superare i **validatori reali**
del registro; P4 rinvia a Fase 3 senza graffe; P5 vieta la lettura a voce
delle sorgenti.

---

## Figure accademiche (doc 17)

### `tests/test_figure_theme.py` (57)

Tema D3 e fondamenta (WP2a), puro: chiavi i18n pienamente qualificate
`courses.figures.*`, `figure_labels` con fallback it, `format_number`,
alias `VisualAssetFormat`, parità con `frontend/src/lib/figureTheme.ts`
(costanti, palette, config Mermaid, `THEME_VERSION`), 34 casi
`latex_to_unicode`, `function_caption`.

### `tests/test_figure_i18n_mirrors_frontend.py` (5)

Specchio fra `figure_theme.FIGURE_I18N` e i locale `it.json`/`en.json`
(JSON annidato appiattito): chiavi coincidenti in entrambe le direzioni;
salta se manca `../frontend`.

### `tests/test_mermaid_prerender.py` (17)

Mermaid 11 (WP1), offline: pin unico `settings.mermaid_cdn_version` nella
pagina di pre-render e in quella del validatore, `htmlLabels: false`
top-level in entrambe, re-export dei nomi storici da
`course_lesson_pdf_service`, livello L2 (`_strip_mermaid_max_width`
byte-identico sulla fixture 10.9.4; la fixture 11.17.2 porta ancora
`max-width`), `_sanitize_mermaid_code`, tipi ammessi in
`openai_image_to_mermaid_service`, vincoli 11.x nei prompt di fix e
conversione. La guardia di rete (`block_external_requests` prima di
`set_content`, solo URL del CDN) è verificata sulle tre pagine headless:
pre-render Mermaid, validatore e pre-render MathJax del PDF.

### `tests/test_lesson_pdf_math.py`

Grammatica unica del math del PDF (B3): le quattro rule dollarmath sono
nostre su entrambe le istanze (`_md_renderer`, `_md_inline_renderer`),
flag e ordine delle rule pinnati, corpus currency (prosa byte-identica)
e corpus math numerico, `math_inline_double` mai `<div>` in un `<p>`,
fallback MathML loggato con `reason` e contato, WeasyPrint non rende il
MathML, frase currency e `$$..$$` in frase nel testo del PDF; parità
collector/renderer per uguaglianza (`RecordingMap`) su dispensa e slide,
fixture `fixtures/math_grammar_cases.json` (token e chiavi per caso, in
modalità `block` e `inline`), rule `math_bsdelim` su fence, code span,
citazioni `\[1\]`, tag `\[FIG:x\]` e link, nessun pre-processing testuale
(L11 sul corpo assemblato), `render_markdown_inline` == `markupsafe.escape`
senza math, campi inline con math ed escape (D9), esempio con fence e riga
vuota reiniettato come UN html block, `_math_content_for_slides`, pin
`settings.mathjax_cdn_version` e guardia di rete della pagina MathJax.

### `tests/test_mermaid_no_foreignobject.py` (20)

D8 sull'output reale: i 15 campioni `MERMAID_D8_SAMPLES` resi con la
pagina di produzione producono `<text>` e nessun `<foreignObject>`;
`journey` ne emette e il gate statico lo rifiuta. Richiede Chromium e
`cdn.jsdelivr.net` (skip esplicito in CI).

### `tests/test_mermaid_theme_palette.py` (37)

La palette arriva ai riempimenti e ai bordi dei tipi D8 nell'output reale
di Mermaid 11 (il tema `neutral` non deriva le variabili da
`primaryColor`: `mermaid_config` le fissa una per una). Stessi requisiti
del precedente.

### `tests/test_revalidate_mermaid_assets.py` (26)

Test puri di `scripts/revalidate_mermaid_assets.py`: gate statico D8
(campioni, alias legacy, commenti e frontmatter, tipi esclusi, `%%{init`,
HTML nelle label, frecce e annotazioni ammesse), estrazione degli asset
dai JSONB, asset non citati, valutazione con un renderer finto, righe del
report.

### `tests/test_svg_normalize.py` (37)

`normalize_svg` (Q3): prologo rimosso, rifiuti (`<script>`,
`<foreignObject>`, `<image>`, SMIL, `href` esterni quotati e non, `on*=`,
`url()` non-frammento, anche con prefisso di namespace e dentro
`<style>`), label «vedi url(x)» e attributi `aria-*` accettati, radice
riscritta in px (pt/mm/in), `max_bytes`, `svg_to_data_uri`.

### `tests/test_vegalite_rules.py` (55)

Regole D5 ed euristica del criterio 10 (puro): `data.url` anche in
`lookup.from.data`, `mark image`, chiavi vietate in ogni vista, `clip` e
`scale.domain`, `values`/`sequence`, `axis.format`, `title`; H1-H3 con
ereditarietà dei campi `sequence` alla radice e nei figli
`layer`/`vconcat`/`concat`/`hconcat`/`spec`, alias `calculate`,
`nesting_depth`.

### `tests/test_figure_render_service.py` (100)

Registro dei renderer e `run_isolated` (checklist del brief §7):
Vega-Lite (spec valida; `data.url` annidato, > 4.000 caratteri, `mark
image`, chiavi duplicate, schema → rifiutate; SVG senza `foreignObject`;
iniezione del tema: `font-family` ⊇ «Noto Sans» e un esadecimale della
`PALETTE`); DOT (valido / `image=` e composti rifiutati; tema; `dot`
mancante con `monkeypatch` di `shutil.which` → `(False, "dot_unavailable")`,
mai pass-through); Mermaid (gate statico: tipi, `%%{init`/`initialize`,
frontmatter, HTML nelle label); `run_isolated` (`slow_target`:
timeout reale entro la scadenza senza figli vivi, risultato da 2 MB,
eccezione del figlio → `FigureComputeError`); `render_svg_map` (un batch
per formato, cache LRU, cache negativa, timeout senza eccezioni, tetto
del batch Mermaid); `validate_visual_assets_or_raise` (solo asset
cambiati, payload 422). `skipif` per i casi che eseguono vl-convert o
`dot`.

### `tests/test_asset_validation_dispatch.py` (13)

Dispatch per kind di `asset_validation_service` (D2, Q1): `_validate_slots`
con slot misti e `_validate_js_batch` sostituito in memoria (solo latex e
mermaid nel batch JS, riallineamento `js_pos`), formato non disponibile →
`fixable=False` e `AssetFixUnresolvedError` senza fix AI, raccolta degli
slot per ogni formato renderizzabile, `_sanitize` con ```` ```vega-lite ````,
campi localizzabili dal renderer, **grep che vieta nuovi confronti
letterali `== "mermaid"`** fuori dai siti dichiarati in doc 17 § 9.

### `tests/test_logging_svg_filter.py` (18)

`core.logging._WeasyPrintSvgNoiseFilter` istanziato direttamente: le
«unknown property» SVG di Mermaid/matplotlib/vl-convert/`dot` (`font-*`,
`clip-path`, `vector-effect`, …) sono filtrate; gli altri warning e gli
altri logger passano.

### `tests/test_function_figure_service.py` (117)

Formato `function` (WP7, Q4): parser (passo 1: rifiuti con i messaggi per
il docente, accettazioni, limiti), schema (`parse_function_spec` con
`loc` per campo, hash canonico), numerico (valutatore AST→numpy, rami di
`(x**2-1)/(x-2)`, zeri di `sin`, punti critici, poli e salti, code,
livelli), simbolico (`importorskip("sympy")`: i sei casi del
`global_dict` ristretto su sympy 1.14 e lo studio esatto), render (id
degli elementi, niente `<foreignObject>`/`<image>`, font ⊆ {Noto Sans,
DejaVu Sans}, determinismo byte-identico, forme esatte riconciliate,
formula dentro il viewBox, timeout reale con `slow_target`), registro
(`FunctionRenderer`, hit SVG senza risultato ricalcolato) e gate 422 del
PATCH, endpoint `render-function` (200, 422 semantico, 422 Pydantic, 403,
rate limit). Skip se mancano numpy/matplotlib/sympy.

### `tests/test_figure_numbering.py` (58)

`figure_numbering` (D4, Q2) con la fixture condivisa: prima citazione → N
crescente, citazioni ripetute → stesso N, id senza asset senza numero,
`FIG` case-sensitive, orfane in coda, `strip_figure_prefix` mai «lossy»;
parità con `lib/figureNumbering.ts` eseguita con Node.

### `tests/test_lesson_pdf_figures.py` (50)

Rendering delle figure nei PDF (WP4), puro su oggetti non persistiti:
didascalie «Figura N.» in ordine di citazione, orfano in coda dopo la
sintesi, `en`/`de`, prefisso ripulito, caption escapata,
`<pre class="figure-fallback">` con `figure_render_fallback` nel log
(`structlog.testing.capture_logs`), golden byte-identico del body
Mermaid/image/legacy nel wrapper (A11-L3), slide con «Figura.» e `<img
class="figure-svg">`, coda `function` sopravvissuta all'eviction della
cache; WeasyPrint 69 rende le label degli SVG Mermaid 11 e degli `<img
data:svg>` matplotlib/dot (testo estratto con pypdf, nessun warning oltre
il filtro). `importorskip` su weasyprint/pypdf/matplotlib, `skipif` su
`dot`.

### `tests/test_frontend_figure_i18n.py` (20)

Guardia i18n sui componenti frontend delle figure: nessuna stringa
italiana hard-coded nei file dell'inventario (lessico di parole di
interfaccia, template literal esclusi), ogni chiave `t("…")` risolta in
`it.json` e `en.json`. Salta senza `../frontend`.

### `tests/test_frontend_figure_layout.py` (7)

Geometria delle figure nel frontend misurata in Chromium (Playwright):
`.lesson-prose .figure img { margin: 0 auto }`, tetto d'altezza dei
Mermaid solo per i diagrammi orizzontali (`fullWidthSvgMaxHeightPx`
eseguita con Node). Salta senza Playwright/Chromium.

### `tests/test_frontend_inline_math.py` (8)

Math inline nei campi dei blocchi del frontend (WP2, D9): i sei siti
(`FigureFrame`, `TableBlock`, i due rami di `EquationBlock`,
`ExampleBlock`, `LessonSlidesView`) passano da `InlineMath` (nessun
`dangerouslySetInnerHTML`, `katex.render` con `throwOnError: false`,
`trust: false`, `displayMode: false`); `lib/inlineMath.ts` eseguita con
Node (`--experimental-strip-types`) produce, su un corpus di importi,
decimali, escape, `\[FIG:x\]`/`\[1\]` e sui casi inline di
`fixtures/math_grammar_cases.json`, gli stessi segmenti dei token
dell'istanza zero del PDF; la `FigureFrame` vera (esbuild del frontend,
react-i18next stubbata) montata in Chromium rende la didascalia senza
math con il markup storico byte-identico e quella con math con
`span.katex` in linea, senza `<p>` né `katex-display`. Salta senza
Node/esbuild/Playwright.

### `tests/test_asset_localization_gate.py` (4)

`_needs_localization` + `app/core/i18n_scripts`: per lingue a script non
latino un campo rimasto in italiano è segnalato; testo già nello script
target, testo matematico e lingue latine no. Puro.

---

## Verifiche fuori dalla suite

### `docs/PROMPTS.md` contro i prompt reali

**Nella suite dal 9 settembre 2026**: `tests/test_prompts_md_matches_code.py`
invoca la stessa funzione di confronto dello script e fallisce sul diff,
con la controprova che tolta una riga il confronto la vede. Finché il
controllo era solo un comando da lanciare a mano la deriva passava in
silenzio (il documento aveva perso tre righe del PROMPT 12 per diversi
commit). Lo script resta il modo di LEGGERE il diff e di riallineare.

`docs/PROMPTS.md` riporta i system prompt «verbatim». Lo script
`backend/scripts/check_prompts_md.py` estrae il primo blocco ```text di
ogni sezione «# PROMPT n» pertinente (3 dispense con grounding, 4
verifica, 5 slide, 6 discorso, 11 immagine → Mermaid, 12 fix degli asset
con le varianti Vega-Lite/DOT/`function` dichiarate verbatim) e lo
confronta carattere per carattere con l'output dei `_system_prompt(...)`
resi con i segnaposto documentati (`{language_code}`, `{ruolo_docente}`,
…; il PROMPT 6 normalizza `N * 60 = M` in `{minuti_per_lezione} * 60 =
{secondi}`, il PROMPT 11 sostituisce «italiano»/«inglese» con
`{lang_hint}`):

```bash
cd backend
JWT_SECRET=$(printf 'x%.0s' $(seq 1 40)) python3 -m scripts.check_prompts_md
# [OK] PROMPT 3 — dispense (grounding) (24222 caratteri) … 9/9 blocchi identici al codice
```

Exit 0 se ogni blocco coincide, 1 con un diff unificato per blocco
divergente o mancante. Va eseguito dopo ogni modifica dei prompt (il
diff dice esattamente che cosa incollare nel documento) e prima di
chiudere una PR che li tocca; i messaggi user e gli schemi JSON
restano documentazione descrittiva e non sono confrontati.

### Rivalidazione degli asset Mermaid in DB

`backend/scripts/revalidate_mermaid_assets.py` (dry-run, sola lettura,
Postgres raggiungibile; `--skip-render` per il solo gate statico senza
Chromium): vedi [Courses 09 § Settings comuni](../courses/09-pdf-export.md).
Senza un dump con contenuti reali si esegue una run sintetica su
`a4u_test` (corso di prova costruito con `course_builders`), come
documentato in [Courses 17 § 14.3](../courses/17-visual-figures.md).

### Prove nel container

Le verifiche che dipendono dai font e dal kernel Linux (metriche dei font
di vl-convert, `spawn` sotto uvicorn, fontconfig per WeasyPrint) si
eseguono dentro l'immagine `backend/Dockerfile` con uno script montato in
`/tmp` (`docker run --rm -v probe.py:/tmp/probe.py:ro -e JWT_SECRET=…
<immagine> python /tmp/probe.py`); esiti in Courses 17 § 14.3.

---

## Strategie di estensione

1. **Nuovo renderer di figure**: test puri sul modulo leaf (regole,
   normalizzazione) + un caso in `test_figure_render_service.py` con
   `skipif` sulla dipendenza + un caso di dispatch in
   `test_asset_validation_dispatch.py`; aggiornare la tabella dei siti
   `== "mermaid"` in doc 17 § 9 se il grep cambia.
2. **Organizzazioni / inviti**: fixture `as_admin` che logga il bootstrap
   admin via `/auth/login`; verificare gli audit log.
3. **Template**: upload con `httpx.AsyncClient.post(..., files={"background":
   (filename, payload, "image/png")})`.
