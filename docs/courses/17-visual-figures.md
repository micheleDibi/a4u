# 17 — Figure accademiche: Mermaid 11, Vega-Lite, DOT e figure calcolate

Sintesi di progettazione (Fase B) delle **quattro famiglie di asset
visivi** generati o modificati in Fase 3 (dispense) e Fase 4 (slide):
diagrammi **Mermaid 11**, grafici **Vega-Lite** (vl-convert), grafi
**Graphviz DOT** e figure matematiche **calcolate** (`function`: sympy +
matplotlib). Le quattro famiglie passano da un **registro di renderer
unico**, condividono un **tema accademico unico** e ricevono didascalie
«Figura N.» identiche in editor, vista lezione, slide, PDF dispensa, PDF
slide e frame video.

Questo documento è nato come **v1** prima del codice del registro, come
richiede §5 del brief (fissa le firme, gli algoritmi e le decisioni che i
work package implementano), ed è stato completato in **v2** alla chiusura
del branch (WP6): stato dei WP, correzioni emerse nelle verifiche
indipendenti, esiti reali delle misure (Docker, bundle, rivalidazione,
prove nel container), checklist di smoke del frontend, decisioni prese e
alternative scartate, limiti e lavori futuri. Gli esiti della revisione
avversariale di Fase D del branch `feat/academic-figures` sono nelle
sezioni 14.4-14.11. I capitoli **16-19** sono le richieste successive del
docente sui cataloghi dei modelli; il capitolo **20** è il branch
`fix/asset-refs-math-figure-scale` (16-17 settembre 2026), che affronta i
quattro difetti visti sul PDF consegnato — asset duplicato con la frase
spezzata, LaTeX crudo, testo delle figure fuori scala, diagrammi densi non
misurati — e ha come solo segnaposto rimasto gli esiti della sua Fase D
(sezione 20.8).

Documenti correlati: [08 — Lesson content (Fase 3)](08-lesson-content.md)
(generazione, validazione asset, editor), [09 — PDF export](09-pdf-export.md)
(pre-render Mermaid e pipeline WeasyPrint), [10 — Lesson slides (Fase
4)](10-lesson-slides.md), [12 — Lesson video (Fase 6)](12-lesson-video.md)
(frame Playwright dello stesso HTML delle slide), [15 — Duplicazione
corso](15-course-duplication.md) (localizzazione degli asset), [05 — API
reference](05-api-reference.md), [06 — Frontend](06-frontend.md), [01 — Data
model](01-data-model.md), [04 — Configuration](../04-configuration.md),
[07 — Deployment](../07-deployment.md), [PROMPTS.md](../PROMPTS.md),
[backend/06 — Schemas](../backend/06-schemas.md), [backend/07 —
Services](../backend/07-services.md), [backend/11 — Tests](../backend/11-tests.md),
[frontend/05 — Components](../frontend/05-components.md).

I riferimenti a file e righe delle sezioni 2-9 sono al commit `8ce8160`
del branch `feat/academic-figures` (HEAD al momento della v1) e non sono
stati riallineati riga per riga: i moduli citati esistono tutti e i
nomi dei simboli sono quelli del codice; per la posizione esatta fa fede
il codice (`git grep`). Le sezioni 1, 12, 13, 14 e 15 sono aggiornate
alla chiusura del branch `feat/academic-figures` (7 settembre 2026) e, per
le parti toccate dal branch `fix/asset-refs-math-figure-scale`, al
17 settembre 2026; i riferimenti a file e righe del capitolo 20 e dei
riallineamenti di §6.4, §12, §13 e §15 sono verificati su quel branch.

## 1. Perimetro e stato di avanzamento

| Famiglia | `format` | Renderer server-side | Renderer browser | Validazione |
|---|---|---|---|---|
| Mermaid 11 | `mermaid` | Playwright + Chromium, pin `mermaid_cdn_version` (11.17.2) | `mermaid` 11.17.2 (lock npm) | gate statico D8 + parse JS in batch |
| Vega-Lite | `vegalite` | `vl_convert.vegalite_to_svg` in processo figlio | `vega-embed` (import dinamico) | schema JSON v6 + regole D5 + criterio 10 |
| Graphviz DOT | `dot` | binario `dot` in `subprocess` | `@viz-js/viz` (WASM, import dinamico) | limiti + scansione dei nomi di attributo + prova di render |
| Figura calcolata | `function` | numpy + matplotlib in thread, sympy in processo figlio | `<img>` dell'SVG prodotto dall'endpoint `render-function` | Pydantic + AST |

Gli altri formati dell'alias `VisualAssetFormat` (`image`, `image_prompt`,
`image_search_query`, `description`) restano invariati: `image` è
l'immagine caricata dal docente, gli altri tre sono legacy della Fase 4 e
non entrano nel registro.

Stato dei work package alla chiusura del branch (ogni WP ha avuto una
verifica indipendente e, dove indicato, un commit di correzione; il
report di consegna è il corpo della PR):

| WP | Contenuto | Stato |
|---|---|---|
| WP2a | alias `VisualAssetFormat`, `figure_theme.py` + `figureTheme.ts`, setting `figure_*`, `.env.example`, compose, pyproject, Dockerfile, CI | `bb17b49` + correzione `b97ff74` (palette nei riempimenti Mermaid, `latex_to_unicode` annidato): due commit, nessuno squash |
| WP1 | Mermaid 11: `mermaid_prerender.py` estratto, pin unico, `htmlLabels:false` top-level BE+FE, prompt fix/digitalizzazione, script `revalidate_mermaid_assets.py`, test D8 | `8ce8160` |
| WP2b-0 | questo documento (v1) | `ae3b28f` |
| WP2b | `figure_render_service.py`, `svg_normalize.py`, `figure_compute/`, dispatch del validatore, fix AI, localizzazione, PATCH 422, builder degli schemi OpenAI, filtro log | `61689b6` + correzioni `5cac685` (data.url annidato, alias del criterio 10, gate Mermaid generico, attributi DOT composti, scansione SVG con virgolette) e `6838a24` (gate Mermaid `initialize`/frontmatter, nomi DOT quotati, JSON annidato) |
| WP7 | formato `function` (schema, parsing, calcolo, disegno, endpoint) | `d38496f` + correzioni `9e13a3f` (tetto ai punti notevoli, plateau, tolleranze locali, lock del disegno, timeout del worker) e `6c1bf00` (formula e numeri bounded, poli al bordo, variabile nelle didascalie, avvio del figlio); livello 1 (`function_study`, `tangent`, `area`, `family`, `level_curves`), livello 2 lavoro successivo (A9) |
| WP4 | numerazione, partial `figure.html.j2`, PDF dispensa/slide, frame video | `3f55fb5` + correzione `ebf3b30` (coda `function` dopo l'eviction, guardia anti-doppia coda, prefisso non lossy, id normalizzati) |
| WP3 | prompt P3/P4/P5, guardie di lunghezza, `PROMPTS.md` | `98d4912` |
| WP5 | frontend: `FigureFrame`, renderer ed editor per formato, dialog, i18n | `e753c1e` + correzioni `87a30e0` (errori del parser localizzati, cifre Unicode nel prefisso, SVG dell'anteprima inerte) e `2f1c58c` (tetto Mermaid solo per i diagrammi orizzontali, figure centrate, loader vega inerte) |
| WP6 | documentazione, misure, consegna (questo documento v2, `scripts/check_prompts_md.py`, aggiornamento di 25 documenti, corpo della PR) | commit `docs(figures): WP6 — documentazione, misure e consegna` |
| Fase D | revisione avversariale (correttezza, regressione, sicurezza, i18n, tipografia) | da eseguire dopo WP6; esiti da registrare in 14.3 |

## 2. Architettura del registro (Q1)

Il registro vive in `backend/app/services/figure_render_service.py`
(WP2b). I renderer sono oggetti **sincroni e puri**: il chiamante decide
se eseguirli in thread o in processo. Tutto ciò che è CPU-bound o non
interrompibile (vl-convert, sympy) gira in un processo figlio `spawn`
tramite `figure_compute.isolated.run_isolated` (A13).

### 2.1 Protocollo `FigureRenderer`

```python
class FigureRenderer(Protocol):
    fmt: str
    def available(self) -> bool: ...
    def sanitize(self, content: str) -> str: ...
    def validate(self, content: str, *, deep: bool = False) -> tuple[bool, str]: ...
    def render_svg(self, content: str, *, asset_id: str = "") -> str | None: ...
    def render_svg_batch(self, contents: list[str], *, asset_ids: list[str]) -> list[str | None]: ...
    def extract_translatable(self, content: str) -> dict[str, str]: ...
    def apply_translations(self, content: str, tr: Mapping[str, str]) -> str: ...

REGISTRY: dict[str, FigureRenderer]
RENDERABLE_FORMATS = ("mermaid", "vegalite", "dot", "function")

@lru_cache(maxsize=1)
def available_formats() -> tuple[str, ...]: ...

@dataclass(frozen=True)
class RenderedFigure:  # D10: metriche del testo accanto all'SVG
    svg: str
    metrics: SvgMetrics | None

async def render_figure_map(assets: list[dict], *, language: str) -> dict[str, RenderedFigure]: ...
async def render_svg_map(assets: list[dict], *, language: str) -> dict[str, str]: ...  # proiezione .svg
async def validate_visual_assets_or_raise(assets, *, previous, loc_root, code) -> None: ...
```

`MermaidRenderer` espone in più `render_figure_batch(contents, *,
asset_ids) -> list[RenderedFigure | None]` (SVG e corpo del testo misurato
nella stessa pagina); non è nel `Protocol`, quindi i renderer registrati
dall'esterno e i fake dei test restano validi, e `render_svg_batch` ne è
la proiezione `.svg`.

- `available()`: la dipendenza è presente (binario `dot`, moduli
  `vl_convert`/`jsonschema`, `sympy`+`matplotlib`); Mermaid è sempre
  disponibile.
- `sanitize()`: strip di fence e caratteri di controllo, mai un round-trip
  che alteri i byte del contenuto (Vega-Lite resta la stringa del docente).
- `validate(deep=False)`: gate offline (PATCH manuale, endpoint);
  `validate(deep=True)`: anche la prova di render, il cui SVG entra nella
  cache così che la validazione del worker renda gratis l'export.
- `render_svg()` / `render_svg_batch()`: ritornano `None` per la figura
  che fallisce, mai un'eccezione; il batch esiste perché Mermaid apre un
  solo Chromium per lezione. Il metodo per-asset di Mermaid non è nel
  percorso di produzione.
- `extract_translatable()` / `apply_translations()`: campi testuali per la
  localizzazione D7 (sezione 8.3).

`available_formats()` = kill-switch booleano del setting **e** dipendenza
presente: `("mermaid",) + {vegalite se figure_vegalite_enabled ∧
available} + {dot …} + {function …}`. Con cache di modulo (`lru_cache`),
loggata una volta all'avvio dei worker; `dot` assente produce
`log.error("graphviz_dot_missing")` una sola volta. Lo schema strict
offerto al modello e il dispatch del validatore leggono da qui (A19: il
testo dei prompt resta statico, solo l'`enum` dello schema si restringe).

### 2.2 `render_figure_map`: l'unico punto asincrono

`render_figure_map(assets, *, language)` è chiamata dai tre
materializzatori (PDF dispensa, PDF slide, frame video) attraverso
`_prerender_visual_assets_for_lesson`; `render_svg_map(assets, *,
language)` ne è la proiezione `{asset_id: svg}` (nome del piano, tenuto
per gli script e i test che leggono solo l'SVG) e non ha logica propria.
In origine `render_svg_map` ha preso il posto del filtro Mermaid di
`_prerender_mermaid_for_lesson` (`course_lesson_pdf_service.py:633-656`)
e `_prerender_mermaid_for_slides` (`course_lesson_slides_pdf_service.py:271-290`);
con WP3 (D10) la logica è passata a `render_figure_map`.
`render_figure_map` è l'unico punto **dell'export** in cui compaiono `asyncio.to_thread`,
`asyncio.wait_for(settings.figure_render_timeout_seconds)` e
`asyncio.Semaphore(settings.figure_render_max_workers)`; il semaforo è
tenuto dal chiamante async e rilasciato anche su timeout, mai acquisito
dentro il thread. Fuori dall'export il `to_thread` compare in altri due
siti, **senza** semaforo e per scelta (Fase D, COR-5): la validazione
profonda del worker (`asset_validation_service._validate_slots`, con il
proprio `wait_for` per slot) e il gate `deep=False` del PATCH. Il tetto
effettivo dei render concorrenti è quindi
`figure_render_max_workers` per l'export più
`course_lesson_{content,slides}_max_concurrency` per la generazione (una
validazione profonda alla volta per lezione).

Algoritmo:

1. filtra gli asset con `format in RENDERABLE_FORMATS` e contenuto non
   vuoto; per ognuno calcola la chiave di cache
   `(fmt, sha256(sanitized), THEME_VERSION)`. La lingua **non** entra nella
   chiave (scelta di WP2b rispetto al piano, che la elencava): `render_svg`
   del protocollo non la riceve, quindi l'SVG non può dipenderne — la
   didascalia calcolata di `function` è composta fuori dall'SVG da
   `figure_theme.function_caption` e l'endpoint `render-function` ha una
   cache propria con la lingua (sezione 4.6). Con la lingua nella chiave
   l'SVG prodotto da `validate(deep=True)` nel worker (che non conosce la
   lingua) non sarebbe mai l'hit dell'export;
2. serve dalla cache LRU (`OrderedDict` + `threading.Lock`, dimensione
   `figure_svg_cache_size`) le chiavi presenti; per `function` l'hit vale
   solo se anche il risultato del motore è nella cache dei risultati di
   `figure_function_service` (`_svg_cache_hit_complete` →
   `FunctionRenderer.result_cached`), altrimenti la figura torna nel batch
   e `render_svg` ricalcola ripopolando entrambe le cache (correzione
   WP4, sezione 6.3); le chiavi nella **cache negativa** (render fallito
   negli ultimi 60 s) vengono saltate senza ritentare — evita di ripetere
   un render fallito a ogni giro del fix loop;
3. raggruppa i restanti per formato e chiama **un** batch per formato in
   `to_thread` sotto semaforo e `wait_for` (`_render_batch`:
   `render_figure_batch` se il renderer lo espone, altrimenti
   `render_svg_batch` del protocollo, il cui SVG diventa
   `RenderedFigure.from_svg`): per Mermaid è
   `MermaidRenderer.render_figure_batch` →
   `mermaid_prerender._prerender_mermaid_batch_sync` (SVG e metriche
   misurate nella stessa pagina; `_prerender_mermaid_to_svg_batch_sync`
   resta come proiezione storica `.svg`), con `_sanitize_mermaid_code`
   prima e `_strip_mermaid_max_width` dopo (un Chromium per lezione, come
   oggi); per gli altri formati la normalizzazione SVG (sezione 7);
4. ritorna `{asset_id: RenderedFigure(svg, metrics)}` (`render_svg_map`:
   `{asset_id: svg}`); le chiavi assenti attivano il fallback del partial
   (`<pre class="figure-fallback">`).

`render_figure_map` **non solleva mai**: timeout, eccezioni del renderer e
SVG rifiutati producono `log.warning("figure_render_failed", …)` con
formato, `asset_id` e motivo, e la chiave resta assente. I worker
(`materialize_*`) non vedono eccezioni nuove: un'eccezione lì farebbe
ripartire l'intero export via auto-retry.

**Tetto del batch Mermaid** (correzione WP2b, giro 2). Il `wait_for` è
unico per formato come prescrive Q1, ma il batch Mermaid paga un costo
fisso prima del primo render (lancio di Chromium, caricamento della CDN
con `wait_for_function` fino a 15 s in `mermaid_prerender.py`) che con il
timeout unico di 20 s farebbe perdere in blocco tutte le figure Mermaid
della lezione appena la CDN rallenta — e oggi `_prerender_mermaid_for_lesson`
non ha alcun tetto complessivo. Perciò `_BATCH_TIMEOUT_FLOOR_S = {"mermaid":
60.0}` alza il tetto del solo batch Mermaid a `max(figure_render_timeout_seconds,
60)`, e un timeout dell'**intero** batch Mermaid non entra in cache
negativa (`_NO_NEGATIVE_CACHE_ON_BATCH_TIMEOUT`): il tempo è dominato dal
costo fisso, non attribuibile alle singole figure, mentre un render
fallito per figura (`None` nella lista) resta in cache negativa per 60 s.
Le due tabelle sono per formato: nessun nuovo confronto letterale con
`"mermaid"` (sezione 9). WP4 instrada il pre-render PDF su questo punto
senza altre decisioni di dimensionamento.

**Accumulo del tetto per i formati a costo per figura** (Fase D, COR-6).
Per `function` e `dot` `render_svg_batch` è sequenziale e ogni figura può
costare fino a `figure_function_timeout_seconds` (10 s) o al timeout di
`dot`: N figure competono per il tetto unico di 20 s pensato per una sola.
Nel percorso nominale non si vede (la validazione del worker ha già reso e
messo in cache ogni figura), ma con la cache fredda — riavvio, sfratto
della LRU, figura modificata a mano, che il gate del PATCH non
pre-renderizza — il batch può scadere e la lezione perde in blocco tutte
le figure di quel formato, con il fallback di A23. È la forma prescritta
da Q1 e non un difetto: resta dichiarata qui e in sezione 15.

**Lista parallela** (Fase D, COR-1). `render_figure_map` non solleva mai,
nemmeno se un renderer registrato con `register_renderer` viola il
contratto restituendo una lista di lunghezza diversa: la lista viene
normalizzata a `len(items)` con `None` e la figura degrada a fallback,
con `figure_render_batch_length_mismatch` nei log.

**Sorgenti che si svuotano con la sanificazione** (Fase D, REG-4). Il
filtro del passo 1 guarda il contenuto grezzo; un fence vuoto
(```` ```mermaid\n``` ````) sopravvive al filtro e si svuota poi. Dopo la
sanificazione il sorgente vuoto viene saltato, così una lezione i cui
Mermaid si svuotano tutti non avvia Chromium né carica la CDN a vuoto.

### 2.3 `validate_visual_assets_or_raise` e il payload 422

Chiamata nei due CRUD manuali (`course_lesson_content_crud.update_lesson_content`
dopo `_validate_consistency`, r.221, con `loc_root="visual_assets"` e
`code="lesson_content_invalid_visual_asset"`;
`course_lesson_slides_crud.update_lesson_slides` con `new_assets` e
`code="lesson_slides_invalid_new_asset"`), **prima** di `if not changed:
return course`, e solo quando `payload.visual_assets is not None`. Valida
**solo** gli asset del payload con `(format, content)` diversi da
`previous` (confronto per `asset_id`, A15): un edit del testo non
rivalida diagrammi legacy già in DB. Usa `validate(deep=False)` in
`to_thread`; un formato assente da `available_formats()` è un errore
`figure_format_unavailable`, non un pass-through.

Il payload segue la forma `loc/msg/type` del handler Pydantic
(`core/errors.py:97`), estesa con `asset_id` e `format`:

```json
{
  "code": "lesson_content_invalid_visual_asset",
  "message": "Uno o più asset visivi non sono validi.",
  "meta": {
    "errors": [
      {
        "loc": ["visual_assets", 2, "content"],
        "asset_id": "A3",
        "format": "vegalite",
        "msg": "encoding.x quantitativo richiede scale.domain [min, max]",
        "type": "figure_invalid"
      }
    ]
  }
}
```

`type` ∈ `figure_invalid | figure_format_unavailable |
mermaid_type_not_allowed | vegalite_use_function_format |
function_spec_invalid`; `msg` è troncato a 600 caratteri. La `loc` si
ferma sempre a `content`: per `function` il percorso dentro la spec sta
nel `msg` (`function_spec_invalid: expressions.0.expr: …`), mentre la
`loc` per campo (`["expressions", 0, "expr"]`) è quella dell'endpoint
`render-function` (sezione 4.6), che valida un asset solo (Fase D,
COR-7).

Il confronto con `previous` è per `asset_id`: **rinominare** l'id di un
asset lo rende nuovo per il gate, quindi un diagramma legacy escluso
(`journey`, direttiva `%%{init`) che passa indenne a un edit del testo
viene rifiutato con 422 se il docente ne cambia l'id. È coerente con A15
(l'edit del testo non rivalida nulla) e con l'idea che cambiare l'id
cambia l'identità dell'asset — anche i token `[FIG:id]` del corpo —, ma va
saputo: l'errore parla del `content`, che il docente non ha toccato (Fase
D, REG-2). I messaggi del payload sono in italiano su qualunque lingua
del corso e dell'interfaccia, come tutti i `ValidationAppError` del
prodotto, e si mescolano a quelli inglesi di jsonschema e vl-convert; il
`type` è stabile e basta a tradurli lato client quando si vorrà (Fase D,
I18N-7). Il frontend legge `meta.errors` da
`extractApiError` e li mostra per asset nel dialog di modifica (WP5).

Questo chiude un buco reale: oggi Pydantic accetta otto formati mentre lo
schema strict di Fase 3 ne offre uno, quindi un PATCH manuale può già
persistere `format="vegalite"` senza validazione né renderer.

### 2.4 Moduli

```
backend/app/services/
├── figure_theme.py                 # tema D3, i18n it/en, function_caption (WP2a, committato)
├── mermaid_prerender.py            # pre-render Mermaid estratto dal PDF service (WP1, committato)
├── svg_normalize.py                # Q3: normalize_svg, svg_to_data_uri, SvgRejectedError (WP2b)
├── figure_compute/                 # «leaf»: importabili dal figlio spawn senza config/SQLAlchemy
│   ├── isolated.py                 # run_isolated, FigureTimeoutError, FigureComputeError
│   ├── vegalite_rules.py           # regole D5 + euristica del criterio 10
│   ├── vegalite_render.py          # bersaglio del figlio per vl_convert
│   ├── function_parse.py           # WP7: AST a due passi
│   ├── function_numeric.py         # WP7: campionamento e punti notevoli (numpy)
│   ├── function_symbolic.py        # WP7: sympy nel figlio
│   └── function_plot.py            # WP7: matplotlib, TextPath
├── figure_render_service.py        # registro D2 + orchestratore async (WP2b)
├── figure_numbering.py             # Q2: numerazione «Figura N.» (WP4)
└── figure_markup.py                # Environment Jinja dedicato + render_figure_html (WP4)
backend/app/templates/partials/figure.html.j2   # partial D4 (WP4)
backend/app/schemas/figure_function.py          # FunctionFigureSpec (WP7)
frontend/src/lib/{figureTheme,figureNumbering,figureFormats}.ts
frontend/src/components/shared/{FigureFrame,VegaLiteDiagram,DotDiagram,FunctionFigure,
  VegaLiteEditor,DotEditor,FunctionEditor,VisualAssetEditor,AddVisualAssetMenu}.tsx   # WP5
```

`figure_theme.py`, `svg_normalize.py` e ogni modulo di `figure_compute/`
non importano `app.core.config` né SQLAlchemy: sono importabili dal
processo figlio `spawn` e dai test puri.

## 3. I renderer

### 3.1 `MermaidRenderer`: gate statico D8

`validate(deep=False)` è un gate **statico e duro**, senza Chromium,
realizzato da `mermaid_static_gate(code) -> (esito, dettaglio)` (unico
punto: lo consuma anche `scripts/revalidate_mermaid_assets.py`, che nella
prima stesura ne aveva una copia divergente). Salta le righe di commento
`%%` e il frontmatter YAML `---…---`; il **primo token** della prima riga
utile (`graph TD;` → `graph`) deve essere **esattamente** un tipo di
`MERMAID_ALLOWED_TYPES` (`figure_theme.py:97-115`: i 15 tipi D8 più gli
alias `graph` e `stateDiagram` v1, accettati in lettura per i contenuti già
in DB) oppure `classDiagram-v2` (alias di Mermaid 11 per `classDiagram`);
il confronto esatto rifiuta con `mermaid_type_not_allowed` i tipi di
`MERMAID_EXCLUDED_TYPES` (`journey`, `gitGraph`, `kanban`, `packet-beta`,
`architecture-beta`), i token sconosciuti (`flowchartXYZ`) e
`flowchart-elk` (layout esterno, assente nel pre-render da CDN). Rifiuta
poi la **direttiva `%%{init`** — anche nella forma `%%{initialize`, che
Mermaid 11 tratta come alias (`detectInit` usa
`/(?:init\b)|(?:initialize\b)/`; la regex del gate, `%%\s*\{\s*init(?:ialize)?\b`,
è un soprainsieme della `%%{` contigua di Mermaid) — e il **frontmatter
YAML** con qualunque chiave diversa da `title` e `displayMode`. Mermaid
carica il frontmatter con js-yaml (`JSON_SCHEMA`) e legge SOLO
`parsed.title`, `parsed.displayMode` e `parsed.config` (quest'ultima
equivale alla direttiva: sovrascriverebbe tema e `htmlLabels:false`
imposti dal renderer, D3, riportando i `<foreignObject>` nel PDF). Una
chiave `config` si scrive in molte forme YAML equivalenti (`"config":`,
`'config':`, `{config: …}` in forma flow, `"con\x66ig":` con escape,
chiave complessa `? config`, alias `*a` di un'ancora): senza un parser
YAML — PyYAML è installato in locale solo come dipendenza transitiva di
`python-frontmatter`, non del backend — l'unico gate deterministico e
senza dipendenze nuove è la **lista chiusa**: ogni riga del frontmatter
deve essere vuota, un commento `#` o una voce `title:` / `displayMode:`
in forma blocco (`_FRONTMATTER_LINE_RE`), altrimenti `mermaid_init_directive`
con la riga incriminata nel messaggio. Un frontmatter con una chiave che
Mermaid ignora (`theme: forest` al primo livello) è rifiutato con lo
stesso esito e il motivo esplicito: costo accettato. Il `---` di
chiusura deve avere lo **stesso rientro** di quello di apertura, come la
`frontMatterRegex` di Mermaid (`^([^\S\n\r]*)-{3}…\n\1-{3}`): un `---`
con rientro diverso è parte del frontmatter, non la sua fine, così il
blocco che il gate analizza coincide con quello che Mermaid carica.
Infine rifiuta i **tag HTML** nelle label (con `htmlLabels:false`
finirebbero in chiaro nel `<text>`): regola generica «`<` seguito da un
nome di elemento e chiuso da `>` sulla stessa riga», non un elenco di tag
(`<script>`, `<table>`, `<svg>`, `<h1>`, `<b>` sono rifiutati),
applicata alle sole righe del corpo che non sono commenti `%%`
(un tag in un commento o nel frontmatter non viene renderizzato: giro 2).
Non sono tag e passano: le frecce (`-->`, `<|--`, `->>`, `<<->>`), le
annotazioni `<<interface>>`, un `<` isolato (`A[a < b]`) e un `<b` non
chiuso (`A[x <b] --> B`: la sezione degli attributi non attraversa `]`,
`)`, `}`). **`<br>` è ammesso** (correzione di Fase D, REG-1): non è HTML
reso in chiaro ma un a capo di Mermaid stesso, che 10.9.4 e 11.17.2
rendono identicamente come due `tspan.row` senza alcun `<foreignObject>`;
rifiutarlo bocciava al PATCH contenuti già in DB che rendono
correttamente, li segnalava come «da correggere» nel report L5 e mandava
al fix AI diagrammi validi. Il criterio del lookahead **non** è
`lineBreakRegex = /<br\s*\/?>/gi`: la label passa dal parser HTML del
browser PRIMA di quella regex, che vede quindi una forma già
normalizzata. L'insieme dei veri a capo è perciò un soprainsieme di
`lineBreakRegex`, misurato sul pre-render di produzione (giro 3): sono a
capo, e passano, `<br>`, `<br/>`, `<br />`, le varianti con spazi dopo la
barra (`<br/ >`, `<br / >`) e la forma di chiusura (`</br>`), tutte con un
SVG identico a quello di `<br>` a meno dell'id `mmd-N`. Resta un tag —
rifiutato — un `br` con un attributo (`<br class="x">`), che il parser
serializza in chiaro nella label.
Falsi negativi misurati e non chiusi: `<br/x>` (reso in chiaro come `<br
x="">`), `< br>` (reso in chiaro) e `</ br>` (tolto in silenzio) passano
il gate, perché la regola generica dei tag non li riconosce come tale —
stessa classe del `<b` non chiuso, costo estetico in una label, nessuna
risorsa esterna.
Falso positivo dichiarato: i tipi generici di `classDiagram`
scritti con `<…>` (`List<int>`) sono rifiutati; Mermaid vuole `List~int~`
e il messaggio lo suggerisce.

Il gate rifiuta poi (`mermaid_external_resource`, correzione di Fase D,
SEC-1) le **direttive di shape `@{ … }`** che non stiano dentro una lista
CHIUSA di chiavi: la shape immagine diventa un `<image href="…">` che il
Chromium del pre-render, WeasyPrint (dispensa e slide) e il browser di
chi apre la lezione dereferenziano — SSRF dal server e risorsa esterna
dentro il PDF consegnato, con il solo `course:edit`. Il blocco è
delimitato da `_mermaid_shape_blocks`, uno scanner a carattere che chiude
sulla `}` FUORI dalle virgolette come lo stato `shapeDataStr` del lexer
di Mermaid 11.17.2 (regole `["]` push, `["]` pop, `[^"]+`, nessun escape
con la barra rovesciata): la regex `@\{[^}]*\}` del primo giro si fermava
alla prima `}` e una label `"}"` le nascondeva l'`img:` che seguiva
(giro 2 della revisione).

**Postura dichiarata (giro 5, confermata dal giro 7): il gate statico è
una euristica di difesa in profondità, non una prova di impossibilità.**
Riconoscere leggendo il sorgente come testo se un diagramma caricherà una
risorsa è una gara contro un parser vero (lexer con stati, js-yaml, una
decina di grammatiche), e i primi quattro giri l'hanno persa a turno; il
quinto, il sesto e il settimo hanno trovato altri sorgenti che il gate
accettava e che rendevano davvero un riferimento esterno. **Il gate non
può vincere questa gara e non è il controllo su cui il prodotto si
regge.** I controlli che reggono sono quattro, indipendenti fra loro:

1. **isolamento di rete del pre-render** (`mermaid_prerender.block_external_requests`):
   il Chromium del server instrada ogni richiesta e annulla quelle fuori
   da `PRERENDER_ALLOWED_PREFIX` (i WebSocket, che `page.route` non vede,
   sono instradati a parte e chiusi prima dell'handshake). È questa — non
   il gate — che impedisce
   l'SSRF dal server; il verificatore del giro 2 ha misurato 0 GET su 18
   vettori con la guardia attiva, quello del giro 6 le stesse 0 GET sui
   vettori nuovi e il giro 7 le stesse 0 GET sui suoi 19;
2. **scansione dell'SVG reso** (`_svg_external_ref`): la figura con un
   riferimento esterno non entra nel documento consegnato;
3. **politica del browser** (`frontend/nginx.conf`, giro 7): la pagina
   dell'applicazione è servita con `Content-Security-Policy: img-src
   'self' data: blob: <origine degli upload>`, e il browser rifiuta la
   richiesta verso l'host scelto dall'autore prima ancora che parta. È
   questa — non la sanificazione — che chiude il residuo del render
   client-side, perché agisce sul documento e non sul markup. **Governa
   le sole immagini**: senza `default-src`, `@import`, `@font-face`,
   `<iframe>`, `<object>`, `<video><source>`, `prefetch` e `fetch`
   restano liberi, e oggi non è un buco solo perché Mermaid 11.17.2 con
   `htmlLabels: false` non emette nessuno di quei nodi (misurato in
   sezione 14.11). **In sviluppo non c'è**: il server di Vite non passa
   da nginx (sezione 15);
4. **sanificazione lato client** (`sanitizeMermaidSvg` in
   `lib/figureFormats.ts`): l'SVG che Mermaid rende nel browser del
   lettore è reso inerte prima di entrare nella pagina, così il nodo
   che resta nel documento non ha né `<image>` né `<a>` esterni. **Non
   può arrivare prima della prima richiesta**: `mermaid.render` misura il
   diagramma su un nodo che attacca al documento, quindi per una shape
   `img:` la GET parte durante il render (misurato in Chromium senza
   intercettazioni). Nessuna opzione di Mermaid la evita:
   `securityLevel: 'sandbox'` sposta il render in un `<iframe sandbox>`,
   e l'attributo `sandbox` non impedisce il caricamento delle
   sottorisorse — per questo la rete che conta è la 3.

Il gate resta la prima rete, ed è quella che dà al docente un errore
leggibile invece di una figura che sparisce; ma il suo criterio di
correzione dal giro 5 è **l'unione, mai lo scambio**: ogni giro può
aggiungere controlli, nessuno può sostituirne uno. Il giro 4 aveva
sostituito la scansione larga `\bimg\s*:` con la sola lista chiusa e
aveva così **riaperto sette vettori** che il giro 3 rifiutava; oggi i tre
controlli si sommano e basta che uno segnali.

L'unione del giro 7 vale sulla **lettura del sorgente**, non solo sui
controlli: `_mermaid_gate_views` produce due letture e il gate ripete
tutto su entrambe.

* La prima è quella dei giri 1-6: righe da `str.split("\n")`, commento =
  riga che comincia per `%%`.
* La seconda ricalca `preprocessDiagram` (`mermaid.core.mjs`):
  `cleanupText` normalizza `\r\n?` in `\n` PRIMA che Mermaid tolga
  frontmatter, direttive e commenti, `removeDirectives` cancella le
  direttive con la `directiveRegex` vera, e commento è ciò che
  `cleanupComments` toglie davvero (`^\s*%%(?!{)[^\n]+`).

Le due differenze erano entrambe sfruttabili, e misurate al giro 6 con la
GET arrivata al listener: `%%nota\rclick A href "http://…"` era una sola
riga di commento per il gate e due righe per Mermaid, che eseguiva la
seconda (vale per `flowchart`, `graph`, `classDiagram`, `stateDiagram`,
`sequenceDiagram` e per le shape `@{ img: … }`, anche con l'escape
`"\x69mg"`, e anche con `%%\r` nudo perché `cleanupComments` pretende
almeno un carattere dopo `%%`); `%%{x}%% click A href "http://…"` era un
commento per il gate e una direttiva per Mermaid, che ne toglie solo
`%%{x}%%` e lascia lo statement (`_INIT_DIRECTIVE_RE` non scatta perché
la direttiva non è `init`). Perché due letture e non una sola, quella
fedele: portare il `\r` nella divisione in righe sarebbe uno scambio, non
un'unione — `---\rtitle: x\r---\rflowchart LR` oggi è rifiutato perché il
tipo è `---`, e con il solo `\r` normalizzato tornerebbe ammesso.

Dentro il blocco vale lo stesso ragionamento del frontmatter, e per la
stessa ragione: **`@{ … }` non è testo, è YAML**. Mermaid lo dà a js-yaml
(`addVertex` → `load(yamlData, {schema: JSON_SCHEMA})`, avvolgendolo in
`{ … }` quando sta su una riga sola), quindi la chiave `img` si scrive in
molte forme equivalenti — `"\x69mg"`, `'img'`, `? img`, `!!str img`, una
voce in forma blocco — che nessun pattern testuale `\bimg\s*:` vede. Fino
al giro 3 il gate era esattamente quell'elenco di pattern ed era
evadibile: `A@{ "\x69mg": "\x68ttp://…" }` passava gate, `mermaid.parse` e
PATCH, e il payload restava in DB. Dal giro 4 ogni voce del blocco deve
avere una chiave scritta in forma piana (o quotata senza escape) e
appartenere a `MERMAID_SHAPE_KEYS` — le undici che Mermaid 11.17.2 legge
davvero: `shape`, `label`, `labelType`, `form`, `pos`, `w`, `h`,
`constraint` per il nodo, `animate`, `animation`, `curve` per l'arco.
`img` e `icon` sono fuori, e con loro qualunque chiave in forma non
riconosciuta: il gate non decodifica gli escape, li rifiuta. Le shape
senza risorse (`A@{ shape: rect, label: "x" }`) restano ammesse.

Perché la lista chiusa funzioni bisogna segmentare le voci **come le
segmenta Mermaid**, e il giro 4 non lo faceva. Il testo che arriva ad
`addVertex` non è il sorgente grezzo: nello stato `shapeDataStr` la
regola 10 del lexer (`chunk-SHT3W25Y.mjs`) applica
`yytext.replace(/\n\s*/g, "<br/>")`, quindi un a capo scritto DENTRO le
virgolette sparisce, e con lui la scelta della forma — `addVertex` guarda
`metadata.includes("\n")` per decidere fra mappa flow (voci separate da
virgola) e mappa blocco (una voce per riga). Leggendo il sorgente grezzo
il gate vedeva la forma blocco e una voce sola su
`A@{ label: "a⏎b", img: "http://…" }` (chiave `label`, ammessa) mentre
js-yaml leggeva la forma flow e la chiave `img`: gate verde,
`mermaid.parse` verde, PATCH 200, `<image href>` nell'SVG e GET arrivata
al listener. `_mermaid_shape_metadata` ricostruisce quel testo e
`_mermaid_shape_entries` segmenta lui, non il sorgente. Secondo
disallineamento chiuso nello stesso punto: le **parentesi tonde** non sono
un indicatore di collezione YAML (per js-yaml `label: (` è lo scalare
`(`), quindi non proteggono la virgola che segue; lo splitter le conta
solo negli statement, dove `(` apre davvero la sezione di una label di
Mermaid (`A(fai clic; qui)`). La terza rete è la scansione
`\b(?:img|icon)\s*:` sul blocco GREZZO, quella del giro 3, che tiene le
forme che nessuna segmentazione spezza (due chiavi sulla stessa riga di
una mappa in forma blocco: per js-yaml è un errore di indentazione, ma il
gate non deve dipendere da quel dettaglio).

Il giro 6 ha aggiunto l'unione anche alla **segmentazione**, non solo ai
controlli, perché nessuna delle segmentazioni possibili coincide con il
parser vero: il lexer di Mermaid, js-yaml e il gate hanno tre idee diverse
di che cosa sia una stringa. `_split_top_level` ha quindi tre modalità
(`_QUOTING_ANY`, `_QUOTING_YAML`, `_QUOTING_DOUBLE`) e il gate le prova a
coppie — `any`+`yaml` per le voci di shape, `any`+`double` per gli
statement — rifiutando se una qualsiasi segnala. La ragione è l'**apice
singolo**: per il lexer dei flowchart è uno dei caratteri ammessi in un
`NODE_STRING`, insieme alla virgoletta doppia (classe
`[A-Za-z0-9!"#$%&'*+./?\\_]` più l'apice inverso, in
`chunk-SHT3W25Y.mjs`), per
js-yaml in mezzo a uno scalare piano è un carattere qualsiasi
(`label: x'y` è lo scalare `x'y`), ma per lo splitter era un delimitatore.
Un apice DISPARI «quotava» quindi tutto quello che seguiva: nel blocco
`@{ … }` la virgola che separava la voce successiva
(`A@{ label: x'y, "\x69mg": "\x68ttp://…" }` era UNA voce con chiave
`label`, e rendeva un `<image href>` con la GET arrivata al listener), in
una riga il `;` che separava lo statement successivo
(`A[it's]; click A href "http://…"` → `<a xlink:href>`). A inizio nodo
l'apice resta un delimitatore anche per il gate, perché lì lo è davvero
per js-yaml: `A@{ label: 'x, img: y' }` passa e si rende senza alcun
riferimento esterno.

Stesso giro, stessa classe di errore sul **whitespace**. `U+FEFF` (BOM) è
l'unico carattere in cui `\s` di JavaScript — il whitespace che il lexer
salta — è più largo dell'insieme di `str.strip()` di Python: un BOM
davanti a `click`, `link`, `links` o `properties` nascondeva la parola
chiave al gate mentre il renderer la eseguiva, in tutte e quattro le
famiglie. Il `\r` è il caso simmetrico: è un a capo per il lexer ma non
per `str.split("\n")`, quindi `A --> B\rclick A href "http://…"`
registrava il click. Il BOM si toglie insieme agli spazi
(`_MERMAID_TRIM_RE`) e il `\r` entra fra i separatori di statement
(`_MERMAID_STATEMENT_SEPARATORS`); nella divisione in righe non si sposta,
ci si aggiunge una lettura in più (giro 7, sopra), perché spostarlo
cambierebbe anche il riconoscimento del tipo e del frontmatter e un
sorgente oggi rifiutato (`---\rtitle: x\r---\rflowchart LR`, tipo `---`)
tornerebbe ammesso — sarebbe uno scambio, non un'unione.

Il `<image>` però non nasce solo dalle shape. Misurando ogni parola
chiave in tutte e 15 le famiglie D8 e cercando l'URL negli ATTRIBUTI
dell'SVG reso, escono gli **statement** che portano nella figura un
riferimento scelto dall'autore senza passare da alcun `@{ … }`:
`sequenceDiagram` con `properties A: {"icon": "http://…"}` produce un
`<image xlink:href>` — una GET vera nel browser di chi apre la lezione —
e `links`, `link` (in `sequenceDiagram` e `classDiagram`) e `click …
href` (in `flowchart`/`graph`, `classDiagram` e **`stateDiagram` /
`stateDiagram-v2`**) producono un `<a xlink:href>` verso l'host scelto.
`MERMAID_URL_STATEMENTS` è la mappa misurata (famiglia → parole chiave,
con la distinzione di maiuscole del lexer: `sequenceDiagram` e
`stateDiagram` sono case-insensitive — `CLICK A HREF "…"` rende —
`flowchart` e `classDiagram` no) e il gate rifiuta quelle coppie, con `;`
e `\r` come separatori di statement e il BOM tolto insieme agli spazi
davanti alla parola chiave. `stateDiagram` è entrato nella mappa al
giro 5: fino al giro 4 `click A href "http://…"` passava gate e PATCH e
arrivava nel PDF come `<a xlink:href="http://…" target="_blank">`; la
coppia non ha falsi positivi renderizzabili, perché uno stato che si
chiama davvero `click` non parsa. Fuori da quelle coppie le stesse parole
restano contenuto legittimo e passano: in `mindmap` e `timeline` sono il
testo di un nodo, in `sankey-beta` ed `erDiagram` il nome di un nodo o di
un'entità, in `classDiagram` `Link` con la maiuscola è una classe, in
`stateDiagram` `A --> B: click qui` è il testo di una transizione.
Il parse JS resta nel batch di
`_validate_slots` (sezione 8.1): al salvataggio manuale il gate statico
basta (A15), il parse vive già nell'editor con la stessa major 11.

`MermaidRenderer.render_figure_batch` delega a
`mermaid_prerender._prerender_mermaid_batch_sync` (Playwright, pin
`settings.mermaid_cdn_version`, `mermaid_initialize_js(use_max_width=True)`;
`render_svg_batch` e `_prerender_mermaid_to_svg_batch_sync` restano come
proiezioni `.svg`);
l'SVG Mermaid **non** passa dalla normalizzazione di sezione 7: la catena
resta byte-identica rispetto a oggi (A11, livello L3). Proprio perché
manca quella rete, l'SVG reso è scansionato per i costrutti che caricano
una risorsa esterna (`<image`, `<script`, `<iframe`, `url(http|file|//)`,
`@import`) **e per gli `<a>` con un `href`/`xlink:href` non interno**: la
figura che ne contiene uno degrada a fallback invece di finire nel
documento (Fase D, SEC-1). Non è un doppione del gate ma una difesa
indipendente, ed è quello che serve: sorgenti che il gate accetta e che
producono davvero un `<image href>` o un `<a xlink:href>` sono stati
trovati in ognuno dei sette giri della revisione — l'ultimo, quello del
giro 7, con un `\r` dentro una riga di commento e con una riga `%%{…}%%`
(sezione 14.10). Nessuno dei quindici campioni D8 resi fa scattare la
scansione, quindi non costa figure sane. L'ancora è la
correzione del giro 5. Fino al giro 4 la scansione non la cercava, con la
motivazione «un collegamento non è una richiesta, ed è il gate degli
statement a impedirne la nascita»: era una delega a un controllo che
aveva un buco (`stateDiagram`), non una difesa indipendente, e infatti
`click A href "http://…"` in uno `stateDiagram` arrivava fino al PDF.
Un `<a xlink:href="http://…" target="_blank">` è un collegamento verso
l'host scelto dall'autore dentro il documento consegnato e dentro la vista
lezione, dove un lettore lo segue; un ancoraggio interno (`href="#nodo"`)
resta ammesso. La
scansione guarda solo
il contenuto dei tag e i blocchi `<style>` — le stesse regioni di
`svg_normalize`, ottenute dalle sue `iter_tag_contents` /
`iter_style_bodies` — e mai il testo dei nodi: guardando tutto l'SVG,
nel primo giro faceva sparire dall'export una figura la cui label parla
di `@import` o di `url(https://…)`, caso normale in una lezione sul web
(giro 2). Terza difesa, indipendente dalle prime due: la pagina del
pre-render (e quella del validatore) instrada tutte le richieste e
annulla quelle che non stanno sotto `mermaid_prerender.PRERENDER_ALLOWED_PREFIX`
(il CDN da cui importa i moduli), quindi anche un costrutto che sfuggisse
al gate non farebbe eseguire al server una GET verso l'host scelto
dall'autore (`prerender_request_blocked` nei log).

Le due difese che non riguardano il PDF ma la vista e l'editor, dove il
diagramma è reso con Mermaid NEL BROWSER DI CHI GUARDA e senza passare dal
backend. La **politica del browser** (`frontend/nginx.conf`, giro 7):
`Content-Security-Policy: img-src 'self' data: blob: <origine degli
upload>` sulla pagina dell'applicazione, che rifiuta la richiesta verso
l'host scelto dall'autore. È l'unica che arriva prima della richiesta,
perché `mermaid.render` attacca l'SVG al documento per misurarne la
geometria e la GET parte lì; e vale anche per un sorgente che il gate
rifiuta, perché l'editor rende mentre il docente scrive, molto prima di
qualunque salvataggio. La **sanificazione lato client**
(`sanitizeMermaidSvg`, sezione 7) resta e serve a ciò che la politica non
fa: togliere dal markup che entra nella pagina l'`<image>` e l'`<a>`
esterni, che senza di lei resterebbero nel documento (fino al giro 4 il
markup entrava così com'era, con `dangerouslySetInnerHTML` in
`MermaidDiagram.tsx`).

`journey` è escluso perché emette due `<foreignObject>` anche in 10.9.4
(WeasyPrint non li rende); gli altri esclusi non hanno uso didattico o
dipendono da risorse esterne (icone).

### 3.2 `VegaLiteRenderer`

`sanitize`: strip di fence e caratteri di controllo, **nessun round-trip
JSON** (i byte del contenuto restano quelli del docente).

`validate(deep=False)`, nell'ordine:

1. lunghezza ≤ 4.000 caratteri;
2. `json.loads(object_pairs_hook=…)` che **rifiuta le chiavi duplicate**
   (un `"mark"` ripetuto vincerebbe silenziosamente l'ultimo); il decoder
   C solleva `RecursionError` — non `ValueError` — oltre ~1.000 livelli,
   raggiungibili in 3.000 caratteri (`[[[…]]]`): `_parse_vegalite` la
   intercetta come «JSON non valido» (giro 2: prima attraversava il gate
   del PATCH come HTTP 500 e `_validate_slots` come eccezione non gestita);
3. **annidamento** complessivo ≤ `MAX_NESTING` = 32 livelli, misurato senza
   ricorsione da `vegalite_rules.nesting_depth` (`spec annidata oltre 32
   livelli (N)`): jsonschema ricorre in Python e, misurato il 7 settembre,
   regge 60 livelli di `{"and": [{"and": …}]}` ma cade a 100 con
   `RecursionError`; un oggetto annidato dentro `data.values` passa lo
   schema (tipo `object`) e farebbe ricorrere le regole D5, la visita dei
   campi testuali, `json.dumps` e il pickle verso il figlio. Il cap è
   deterministico, indipendente dal limite dell'interprete, e vive in
   `_parse_vegalite`, punto comune di validazione, render e localizzazione
   (una spec legittima non supera la ventina: composizione ≤ 4 più
   `encoding.x.axis…`); `validate` avvolge comunque schema e regole in un
   `except RecursionError` come difesa in profondità;
4. validazione contro lo **schema JSON di Vega-Lite v6** con
   `jsonschema.Draft7Validator`, costruito una volta (`lru_cache`) dal file
   `Path(find_spec("altair").submodule_search_locations[0]) /
   "vegalite/v6/schema/vega-lite-schema.json"` (1,9 MB; altair 6.2.2 porta
   la v6.4.1) **senza `import altair`**; l'errore è
   `best_match(iter_errors)` reso come `"path: msg"[:1600]`. Costo
   misurato il 7 settembre: init 0 ms, 1 ms per spec — nessun caching
   aggressivo necessario;
5. `figure_compute.vegalite_rules.check_vegalite_rules(spec) -> list[str]`
   (sezione 3.2.1); la prima violazione con prefisso
   `vegalite_use_function_format:` classifica l'errore come criterio 10.

`validate(deep=True)` aggiunge `render_svg`. `render_svg`: copia della
spec con **`$schema` imposto** (`https://vega.github.io/schema/vega-lite/v6.json`),
poi `vl_convert.vegalite_to_svg(json.dumps(spec), config=VEGALITE_THEME_CONFIG,
allowed_base_urls=[])` eseguita in `run_isolated` sul bersaglio
`figure_compute.vegalite_render:render_svg` (validazione: solleva con il
messaggio di vl-convert) o `:render_svg_batch` (export: `None` per la spec
che fallisce, senza motivo — accettato per il batch), quindi
`normalize_svg`. Il tema e lo `$schema` li impone il **registro**, non il
bersaglio del figlio: `vegalite_render.py` riceve `config` nel payload.
Il `config` di render è `VEGALITE_THEME_CONFIG` più `aria: false` (WP2b):
l'SVG va in `<img alt>` e gli attributi `aria-label` di Vega ripeterebbero i
valori dei dati dentro i tag (il titolo conserva comunque il suo
`aria-label`: la scansione di sezione 7 ignora gli attributi `aria-*`).
vl-convert paga 361 ms alla prima chiamata di ogni processo; spawn + import
misurati in 0,29-0,47 s.

#### 3.2.1 Regole D5 (`vegalite_rules.py`)

Applicate dopo lo schema (che garantisce la forma) con ricorsione ≤ 4
livelli su `layer | hconcat | vconcat | concat | spec` (`facet` e `repeat`
sono raggiunti attraverso `spec`, che è dove Vega-Lite mette la vista
figlia):

- vietati `data.url`, `data.name` senza `datasets` alla radice,
  `mark: "image"`, `selection | params | interactive | config | usermeta |
  tooltip` (in ogni vista, anche dentro `mark` ed `encoding`),
  `encoding.href`: niente interattività, niente rete, niente tema scritto
  dal modello (il `config` lo inietta il renderer). Le regole su `data`
  valgono per **ogni** oggetto `data` della vista, non solo per quello di
  primo livello: `transform[i].lookup.from.data` (`transform[0].from.data.url
  non ammesso`) è l'unico altro punto in cui Vega-Lite accetta una sorgente
  dati, e `allowed_base_urls=[]` di vl-convert non basta come gate
  (verificato il 7 settembre: rifiuta con «External data url not allowed»
  solo gli URL http(s), anche relativi; un `file://…` non viene letto ma
  non solleva e produce un join vuoto in silenzio; nel browser di WP5
  vega-embed farebbe la fetch);
- `values` ≤ 200 righe (liste o CSV/TSV inline, anche in `datasets`);
  `sequence` con `start/stop/step` numerici, `step > 0`, ≤ 5.000 passi;
- obbligatorio `clip: true` sui mark `line | area | point | trail` (anche
  quando il mark è una stringa: `"line"` è rifiutato con il suggerimento
  `{"type": "line", "clip": true}`) e `scale.domain` `[min, max]` numerico
  con `min < max` sui canali `x`/`y` quantitativi: la figura non sborda mai
  dal riquadro e il modello dichiara l'intervallo che disegna;
- `axis.format` ⊆ `^[ ,.0-9a-z%$~+-]{0,12}$`; una sola `title`, stringa
  ≤ 120 caratteri, solo alla radice.

Il messaggio riporta la profondità della vista (`(profondità 2) mark line
richiede clip:true`) così il fix AI sa dove intervenire.

#### 3.2.2 Euristica del criterio 10 (H1-H3, con ereditarietà)

Le figure matematiche sono **vietate** in Vega-Lite: esiste `function`.
Non si può riconoscere «una funzione» in generale, ma il modo in cui
Vega-Lite la traccia è uno solo: `data.sequence` più un
`transform[].calculate` sul campo della sequenza. Con almeno un campo
`sequence` in vista, ogni espressione `calculate` che referenzia
`datum.<campo>` (o `datum["campo"]`) e contiene

- **H1** una funzione trascendente o non lineare (`sin cos tan asin acos
  atan sinh cosh tanh exp log sqrt pow abs`), oppure
- **H2** una divisione con `datum` a denominatore (`/ datum.x`, `/ (2*datum.x
  + 1)`, `/(-datum.x)`, `/(2*(datum.x+1))`, `/(PI*datum.x)`: fra la barra e
  `datum` sono ammessi, in qualunque ordine, segni, parentesi aperte e
  costanti seguite da un operatore — numeriche o simboliche di Vega (`PI`,
  `E`, `LN2`, `SQRT2`, …: un identificatore, giro 2); `datum.x / 2` non è
  una funzione razionale; una chiamata di funzione non in H1 davanti a
  `datum` (`1/(round(2)*datum.x)`) resta un falso negativo), oppure
- **H3** una potenza (`**` o `^`)

viene rifiutata con `vegalite_use_function_format: transform[i].calculate
traccia una <motivo>: usa format="function"`. Il rifiuto è messo in testa
alla lista così il chiamante lo classifica.

**Ereditarietà dei campi `sequence`** (difetto trovato nella revisione del
7 settembre, correzione obbligatoria in WP2b): la prima stesura applicava
H1-H3 solo ai `transform` della stessa vista che dichiara `data.sequence`,
quindi `data.sequence` alla radice e `calculate: "sin(datum.x)"` dentro
`layer`, `vconcat`, `concat` o `spec` **passava**. In Vega-Lite i dati
della vista padre sono ereditati dalle viste figlie, perciò `_walk`
riceve l'insieme dei campi `sequence` ereditati, vi aggiunge quelli della
vista corrente e lo propaga a tutti i figli, applicando H1-H3 a ogni
`transform.calculate` della sottovista. Test dedicati per `layer`,
`vconcat` e `spec` (`test_vegalite_rules.py`).

**Gli alias contano come campi `sequence`** (revisione del WP2b): un
`calculate` che referenzia un campo `sequence` — anche solo `datum.x` —
rende il proprio `as` un campo `sequence` per le trasformazioni successive
e per le viste figlie, così `{"calculate": "datum.x", "as": "t"}` seguito
da `sin(datum.t)` è rifiutato come `sin(datum.x)`; un `calculate` che non
referenzia la sequenza (`"2"`) non crea alias.

**Falso negativo accettato**: rette e polinomi scritti con `*`
(`2*datum.x + 1`, `datum.x*datum.x`) restano ammessi. Una retta di
regressione illustrativa su dati inline è legittima in Vega-Lite e non è
distinguibile sintatticamente da `y = 2x + 1`; il prompt P3 chiede
esplicitamente `function` per ogni funzione da studiare (WP3). Nessun
riconoscimento «fuzzy» (nomi di campo, titoli): produrrebbe falsi
positivi su grafici legittimi.

### 3.3 `DotRenderer`

- `available()` = `shutil.which(settings.graphviz_dot_path or "dot")`.
- `sanitize` = strip di fence e caratteri di controllo; **mai**
  `_sanitize_mermaid_code`, che cancella le righe `all` (nodo DOT
  legittimo).
- `validate(deep=False)`: lunghezza ≤ `figure_dot_max_chars` (12.000);
  prima parola `strict | graph | digraph`; rifiuto degli attributi che
  fanno leggere file locali o risorse esterne a `dot`, **con i composti**
  degli archi e delle label: `{image, shapefile, imagepath, fontpath,
  stylesheet, URL, href, target}` con i prefissi `label | head | tail |
  edge` (`_DOT_FORBIDDEN_NAMES`), più `SRC` dell'`<IMG>` delle label
  HTML-like (`labelURL`, `headhref`, `tailtarget`, `edgeURL`, `<IMG
  SRC="…">` rifiutati; `headlabel`, `imagescale`, le `<TABLE>` HTML-like
  ammessi). Il piano prescriveva una regex `\b(…)\s*=` sul sorgente
  grezzo: **sostituita nel giro 2 da un tokenizzatore**
  (`_dot_tokens` / `_dot_forbidden_attribute`) perché lo scanner di
  Graphviz risolve lo stesso nome in molte forme che la regex non vede —
  quotato `"image"=`, concatenato `"ima"+"ge"=`, spezzato da una
  continuazione di riga `"ima\⏎ge"=`, separato dall'`=` da un commento
  `image/**/=`, stringa HTML `<image>=` — e tutte, verificate con dot
  15.1.1, aprono il file indicato (`'image'` con apici singoli è invece
  un errore di sintassi per `dot`: nessun file letto, non trattato).
  Il tokenizzatore riproduce le regole dello scanner (`scan.l`: `\"` →
  `"`, `\\` conservato **come coppia** e consumato in due caratteri —
  leggerne uno solo faceva passare la virgoletta di chiusura per un apice
  escapato e nascondeva l'attributo seguente (Fase D, SEC-2) —,
  `\`+newline ignorato, `"a" + "b"` uniti **come `<a>+"b"` e `"a"+<b>`**,
  perché per Graphviz la concatenazione unisce anche le stringhe HTML in
  un solo ID (`<ima>+"ge"=` è `image=`, SEC-2),
  commenti `/* */`, `//` e `#` saltati fuori dalle stringhe — il `#` è
  documentato come riga di preprocessore ma lo scanner reale lo tratta
  come commento fino a fine riga anche a metà riga (verificato: `digraph
  { a # -> b⏎; c }` non produce archi), e limitarlo alla colonna 0
  lasciava che una virgoletta dentro un commento desincronizzasse il
  tokenizzatore (SEC-2, terzo vettore chiuso nel giro 2) —,
  stringhe HTML `<…>` con annidamento) e confronta, senza distinzione di
  maiuscole per prudenza (`dot` è case-sensitive: `IMAGE=` non è letto),
  il **nome che precede un `=`**; nelle stringhe HTML cerca
  `<IMG … SRC=`. Effetto collaterale voluto: un nome vietato come TESTO
  (`label="vedi image=1"`, un commento, una riga `#`, `<TD>src=1</TD>`)
  non è più un falso positivo. Motivo del `SRC`: `dot` apre il file
  indicato e il suo stderr («was not found as a file» contro «No or
  improper image file») distinguerebbe un path esistente del server da
  uno assente nel messaggio inoltrato al fix AI e nei log. `tooltip` e i
  suoi composti restano ammessi: non leggono nulla e in `<img>`/PDF sono
  inerti; ≤ 600 archi.
  Al PATCH manuale il DOT è validato **solo staticamente** (`deep=False`,
  come prescrive Q1): un errore di sintassi accettato al salvataggio emerge
  nell'editor (viz-js, WP5) e all'export; `dot` costa ~50 ms e una
  validazione profonda nel gate resta un'opzione da valutare in Fase D.
- Tema: `dot_defaults_prelude(skip=…)` inserisce i blocchi `graph [...]`,
  `node [...]`, `edge [...]` di `DOT_DEFAULTS` subito dopo la `{` di
  apertura, saltando quelli che il sorgente definisce già (il modello
  resta libero di sovrascrivere il tema in modo esplicito).
- Render: **un solo** `subprocess.run([dot, "-Tsvg", "-Gcharset=utf8"],
  input=source, capture_output=True, timeout=T, check=False,
  cwd=<tmpdir vuoto>, env=…)`, senza shell: `returncode ≠ 0` → `(False,
  stderr[:1600])`, `TimeoutExpired` → `(False, "dot_timeout")`; l'SVG
  normalizzato entra in cache (il render successivo è un hit). La
  validazione profonda e il render sono la stessa chiamata. L'ambiente del
  figlio (`_dot_env`) è **più ampio** del `{"PATH", "LANG"}` del piano,
  deviazione dichiarata: oltre a `PATH` e `LANG`/`LC_ALL=C.UTF-8` passa
  `HOME`, `XDG_CACHE_HOME`, `FONTCONFIG_PATH`, `FONTCONFIG_FILE` se
  presenti, cioè le sole chiavi con cui fontconfig trova configurazione e
  cache dei font (senza `HOME` nel container rescandisce le famiglie a ogni
  run e scrive «No writable cache directories» su stderr; in locale la
  differenza è nulla: 42-50 ms in entrambi i casi). Nessuna variabile che
  influenzi l'esecuzione (`LD_*`, `DYLD_*`, `GV*`).
- `dot` assente: `(False, "dot_unavailable")` con `AssetCheck.fixable=False`
  (il fix AI non può installare un binario), mai pass-through.
- Modelli dell'editor (`DotEditor.tsx`): **diciotto**, ordinati per
  famiglia d'uso e provati uno per uno dal validatore, dal binario `dot`
  e dalla misura della geometria dell'SVG reso — sezione 19, che porta
  anche i dieci tipi di grafo aggiunti il 9 settembre e il paragrafo DOT
  del prompt di Fase 3 che li nomina.

Nessun pacchetto pip `graphviz` (A3): il wrapper non espone un timeout.

### 3.4 `FunctionRenderer`

`validate` = Pydantic (`FunctionFigureSpec`) + AST, sincrono e senza sympy;
`render` = calcolo numerico e disegno matplotlib in thread, calcolo
simbolico in `run_isolated`. Dettaglio nella sezione 4.

### 3.5 `run_isolated` (`figure_compute/isolated.py`)

`run_isolated(fn_path, payload, *, timeout)` avvia un `Process` con
contesto `spawn` (nessuna copia dello stato del worker uvicorn: loop,
connessioni, thread), `Pipe`, `parent_conn.poll(timeout)` **prima** di
`recv` (evita lo stallo con risultati più grandi del buffer della pipe:
il figlio resterebbe bloccato in `send`), `kill()` allo scadere. Il
figlio importa il bersaglio dal disco (`"pacchetto.modulo:funzione"`):
un monkeypatch nel padre non lo raggiunge, perciò il test del timeout usa
un bersaglio reale (`tests/helpers/slow_target.py`). Eccezioni:
`FigureTimeoutError` (scadenza, figlio ucciso e raccolto) e
`FigureComputeError` (il figlio ha sollevato o è morto senza risposta, o
non è nemmeno partito: un errore di `Process.start()` — descrittori
esauriti, bootstrap del processo principale non concluso — è convertito
qui, così i renderer e il motore `function` lo trattano come ogni altro
fallimento del figlio invece di lasciarlo risalire come 500), con i nomi
di A18. `daemon=True` impedisce processi nipoti: accettabile, i bersagli
non ne creano.

Correzione dovuta in WP2b: dopo un `poll()` andato a buon fine, `recv()`
non ha scadenza e i tre `join(5)` possono sommare fino a 5 s oltre
`timeout`; si introduce una deadline monotona (`poll(remaining)` +
`join(min(1, remaining))`).

## 4. Il formato `function` (Q4)

### 4.1 `FunctionFigureSpec` (`schemas/figure_function.py`)

Tutti i modelli hanno `extra="forbid"`. `Var = Annotated[str,
StringConstraints(pattern=r"^[a-zA-Z]$")]`.

- `ExpressionSpec(expr: str (1..200), label: str = "")` (label ≤ 24; vuota
  → `f, g, h, k` assegnate al render).
- Annotazioni con discriminatore `kind`: `TangentAnnotation(kind="tangent",
  at: float, expr_index: int = 0 (0..3), label: str = "")`,
  `AreaAnnotation(kind="area", between: tuple[float, float], expr_index,
  against: int | None, label)`, `PointAnnotation(kind="point", at,
  expr_index, label ≤ 40)`.
- `ParameterSpec(name: Var, values: list[float] (1..6))`;
  `SamplingSpec(points: int = 800 (100..2000))`.
- `ShowItem = Literal["zeros", "critical_points", "inflection_points",
  "asymptotes", "discontinuities", "formula"]`.
- `FunctionFigureSpec(kind: Literal["function_study", "tangent", "area",
  "family", "level_curves"], expressions (1..4), variable: Var = "x",
  variables: tuple[Var, Var] | None (level_curves), domain, range | None,
  show (≤ 6), annotations (≤ 6), parameter | None, sampling, levels: int |
  list[float] | None (2..12))`.

Controlli semantici in `check_function_spec(spec) -> list[SpecIssue]`
(scelta di WP7: non un `model_validator`, perché un `ValueError` del
modello collasserebbe ogni errore in una sola voce con `loc` radice,
mentre il 422 dell'endpoint e il frontend richiedono la `loc` del campo;
`parse_function_spec(content)` esegue struttura e semantica insieme ed è
l'ingresso del renderer e del gate del PATCH): dominio e range finiti con
`lo < hi` e ampiezza in `[1e-3, 1e4]`; `parameter.name != variable`;
`expr_index < len(expressions)`; `tangent` richiede almeno una
`TangentAnnotation` con `at` nel dominio; `area` almeno una
`AreaAnnotation` con `between ⊂ domain`; `family` richiede `parameter` e
una sola espressione; `level_curves` richiede `variables` distinte,
`levels`, una espressione e nessuna annotazione; label non vuote univoche;
ogni `expr` passa `check_expression` con simboli liberi ⊆ `{variable(s),
parameter.name}`. Le analisi (zeri, punti critici, flessi, asintoti,
discontinuità) riguardano la prima espressione; `show` omesso vale
`zeros, critical_points, asymptotes, formula`. Costo misurato del passo
simbolico (`run_isolated` + import di sympy): 0,31-0,32 s per chiamata
(spawn da solo 0,04 s), render completo 0,2-0,7 s.
`content` dell'asset è la stringa JSON di questo oggetto; nel prompt P3
va lo schema compatto a una riga (~330 caratteri) più l'esempio di D9
(~480), con un test che verifica la presenza dei nomi dei campi e degli
enum nel prompt (WP3).

### 4.2 Parsing a due passi (`function_parse.py`)

Passo 1, senza sympy, nel processo padre: `check_expression(src, *,
free_symbols) -> ParsedExpr` che solleva `ExprError(loc_suffix, msg,
type)`. `ast.parse(src, mode="eval")`; `SyntaxError` «invalid decimal
literal» → «moltiplicazione implicita non ammessa: scrivi 2*x»; `BitXor`
→ «usa ** per la potenza»; nodi ammessi `{Expression, BinOp, UnaryOp,
Call, Name, Constant, Load, Add, Sub, Mult, Div, Pow, USub, UAdd}`;
`Constant` solo `int`/`float`; `Call.func` è un `Name` in `{sin, cos, tan,
exp, log, sqrt, abs, asin, acos, atan, sinh, cosh, tanh, floor}` con un
argomento (`log` anche due), nessuna keyword; `Name.id` ∈ funzioni ∪
`{pi, E}` ∪ `free_symbols`, altrimenti «simbolo non dichiarato: y»; `Pow`
con esponente costante `|v| ≤ 12` (`2**1000000` passa l'AST ma non questo
limite) e rifiuto dell'esponente fatto di sole costanti ma non
calcolabile (`x**(1/0)`: senza questo controllo numpy calcolava `x**inf` e
la figura usciva con una didascalia matematicamente falsa — Fase D,
COR-2); ≤ 80 nodi, profondità ≤ 12.

**Nodo** e **profondità** sono definiti come li conta la visita (Fase D,
COR-3): un nodo è un operando (`Constant`, `Name`, `Call`, `BinOp`,
`UnaryOp`, `Expression`), non l'oggetto operatore (`Add`, `Div`, `Pow`),
che l'AST di Python rappresenta a parte; la profondità è l'annidamento
**reale**, quindi una catena associativa (`x**11 + … + 1`,
`(x-1)*…*(x-12)`) è piatta e consuma un livello solo, come la legge chi la
scrive. Prima le due misure erano disallineate — il conteggio preliminare
includeva gli operatori e la profondità contava i termini — e il tetto
effettivo era di ~40 nodi: un polinomio di grado 11 o il Taylor di eˣ a 11
termini venivano rifiutati come «troppo annidati» o «oltre 80 nodi»,
messaggi falsi che arrivavano al docente e al fix AI.

Passo 2, **solo nel figlio sympy** (`function_symbolic.parse_sympy`):
`parse_expr(src, transformations=standard_transformations,
global_dict={"__builtins__": {}, "Integer", "Float", "Rational", "Symbol",
"pi", "E", <funzioni sympy>, "abs": Abs}, local_dict={v: Symbol(v,
real=True)}, evaluate=True)` sulla **stessa** stringa già filtrata;
controllo finale `expr.free_symbols ⊆ dichiarati`.
`parse_expr` usa `eval(code, global_dict, local_dict)`: il `global_dict`
ristretto e il passo 1 sono le due difese, una nel padre e una nel
figlio. **Verificato il 7 settembre su sympy 1.14.0** (voce 6 del
«Delta», prova manuale con il `global_dict` sopra e
`local_dict={"x": Symbol("x", real=True)}`): `__import__('os').system('id')`
e `open('/etc/hosts').read()` → `NameError: name 'Function' is not
defined` (la trasformazione `auto_symbol` converte i nomi ignoti in
chiamate `Function`, assente dal `global_dict`: nessun accesso a builtin);
`(lambda: 1)()` → valutata a `1`, `x.__class__.__mro__` e
`[].__class__.__base__.__subclasses__()` → eseguiti: il `global_dict` NON
li ferma, li ferma **solo il passo 1** (nodi `Lambda`, `Attribute`, `List`
non ammessi), da cui l'obbligo dei due passi; `y + x` → simbolo libero
`y` (colto dal controllo finale `free_symbols ⊆ dichiarati`);
`2**1000000` → `ValueError: Exceeds the limit (4300 digits)` (e il passo 1
lo rifiuta prima con `|esponente| ≤ 12`); `sin(x)**2 + 2*x` → ok in ~1 ms.
Il test `importorskip("sympy")` in `test_function_figure_service.py`
(WP7) fissa questi sei casi.

### 4.3 Numerico prima, simbolico nel figlio

`function_numeric.py` (numpy, in thread): `compile_numpy(parsed)` è un
valutatore ricorsivo dell'AST (`sin → np.sin`, …, `Pow → np.power` in
`errstate(all="ignore")`), niente `eval` né `lambdify`; `sample`,
`split_branches` (taglia dove `y` non è finito o `|Δy| > 8·mediana` con
`|y|` crescente verso il taglio da entrambi i lati; ritorna i rami e le
regioni di taglio), `classify_cuts(f, regions, scale, width, domain)`
(per ogni regione interna: polo se `|f| > POLE_MAGNITUDE·scale` nel punto
di minimo di `|1/f|`, salto se la discontinuità persiste bisecando, nulla
altrimenti; i due estremi del dominio sono esaminati con `edge_pole`:
`|f|` che almeno raddoppia per decade avvicinandosi al bordo e supera la
stessa soglia a `1e-9·width` è un polo — `tan(x)` su [−π/2, π/2], `1/x`
su [0, 1] — mentre un valore non finito lungo le sonde, cioè l'overflow di
`exp(x)`, o una crescita lenta come `log(x)` non lo è), `find_zeros`
(cambi di segno + bisezione, `ZeroSearch` con zeri, plateau e flag di
troncamento), `find_critical` (`CriticalSearch`) / `find_inflection`
(derivate centrali), `oblique_or_horizontal` (regressione lineare sulle
due code `[1e3, 1e4]` e `[1e4, 1e5]`, accettata solo se le due stime
coincidono; `|q| < 1e-3·(1 + |m|)` è rumore della regressione e diventa
0), `tail_confirmed(f, m, q)` (verifica diretta di una retta asintotica
proposta dal figlio). **La figura non dipende da sympy.** Limiti
introdotti dalle correzioni di WP7 (A13, «bounded dai limiti della
spec»): ogni categoria di punti notevoli è limitata a
`MAX_NOTABLE_POINTS = 12` voci (le prime da sinistra; la ricerca si ferma
al primo punto oltre il tetto, `computed["truncated"]` elenca le categorie
e la didascalia aggiunge `courses.figures.function.truncated`); le
sequenze di campioni con `y == 0` sono un plateau (`zero_intervals`, frase
`courses.figures.function.zero_intervals`; con campioni vicini in
sottoflusso, uno zero di tangenza nel punto medio) e quelle con `f' == 0`
un tratto stazionario (avvertenza `stationary_interval`, nessun punto); le
tolleranze degli zeri di tangenza, dei punti stazionari e degli estremi
sono locali (relative ai campioni vicini, mai alla mediana globale di
`|y|`). Il disegno è serializzato da un `threading.Lock` (`rcParams` è
globale al processo) e il motore riceve dall'endpoint una scadenza
monotona controllata fra un passo e l'altro; nel worker
`validate(deep=True)` è avvolta in
`asyncio.wait_for(figure_render_timeout_seconds)`.

`function_symbolic.py` (importa sympy solo nel figlio;
`analyze_symbolic(payload)` è il bersaglio di `run_isolated`): zeri,
punti critici e flessi con `solveset(expr, x, Interval)` e ripiego su
`solve` filtrato ai reali nell'intervallo; singolarità con
`singularities` e ripiego sul denominatore di `together(f)`; `limit(f, x,
s, ±)` per distinguere asintoto verticale e discontinuità eliminabile;
code con `limit(f/x)` e `limit(f − m·x)` per `x → ±∞`; `integrate` sugli
intervalli delle aree; `diff(f).subs` per le tangenti. Forme esatte
(`exact_form`): `nsimplify(val, [pi, E], rational=True, tolerance=1e-9)`
accettato solo se `|val − float(exact)| < 1e-9`, denominatore ≤ 10.000 e
`count_ops ≤ 12`, poi `latex(..., ln_notation=True, fold_short_frac=False,
min=-4, max=6)` (Float fuori da quegli esponenti in notazione
scientifica) purché non superi `MAX_EXACT_LATEX = 80` caratteri. Ogni
testo del figlio è bounded: il LaTeX dell'espressione oltre
`MAX_FORMULA_LATEX = 160` caratteri è omesso con l'avvertenza
`symbolic_latex_too_long` (sympy valuta le potenze intere:
`((10**12)**12)**12*x` produceva 1.731 cifre e un SVG di 1,4 MB) e le
rette asintotiche senza scrittura esatta breve arrivano al padre senza
`expr`/`latex`. Eseguito con
`run_isolated("app.services.figure_compute.function_symbolic:analyze_symbolic",
payload, timeout=settings.figure_function_timeout_seconds)` dentro
`asyncio.to_thread`. Riconciliazione (`figure_function_service._Reconciler`):
ogni punto numerico è sostituito dall'esatto entro `1e-6·ampiezza`; un
esatto senza riscontro numerico entra solo se una verifica diretta con
tolleranza locale lo conferma (`is_zero_at`, `is_stationary_at`,
`inflection_confirmed`, `f` non finita nel punto, `tail_confirmed`),
altrimenti è ignorato (mai numeri «plausibili»); le rette senza `expr`
del figlio sono scritte dai coefficienti numerici con `format_number`
(«y = 1×10¹⁴⁴ x») e i nomi degli assi della spec. Su timeout, eccezione o
avvio del figlio fallito: `approximate=True`,
`warnings=["symbolic_timeout" | "symbolic_failed"]`, coda della
didascalia con `courses.figures.approxValues`; l'SVG viene comunque
prodotto.

### 4.4 Render matplotlib con `TextPath` (A14)

API a oggetti, deterministica: `with rc_context(MATPLOTLIB_RC |
{"svg.hashsalt": f"a4u:{content_hash}"}): fig = Figure(figsize=(5.2, 3.6),
dpi=200); FigureCanvasAgg(fig); ax = fig.add_subplot(111)`; spine
`left`/`bottom` in `set_position("zero")` con frecce, `top`/`right`
nascoste; rami `ax.plot(xs, ys, color=PALETTE[i], gid=f"branch-{i}-{j}")`;
asintoti `axvline`/`axline(ls="--", color="#888")`; punti notevoli `"o"`
`ms=4` con coordinate esatte; formula in alto a destra (`gid="formula"`);
nessun titolo; griglia solo in `level_curves` (`contour` + `clabel`);
`family` con `legend(frameon=False)` solo con più serie; `area` con
`fill_between(alpha=.25)`; `tangent` come retta `y = f(a) + f'(a)(x − a)`;
`fig.savefig(buf, format="svg", metadata={"Date": None, "Creator": None})`
→ `normalize_svg`. I `gid` diventano `id=` nell'SVG: sono i ganci dei test.

Mathtext: con `svg.fonttype: none` matplotlib emette `\sqrt` con
`font-family: STIXSizeOneSym`, `\left(\right)` con `STIXSizeTwoSym`,
`\int`/`\sum` con `DejaVu Sans Display`, famiglie **non installate** nel
container né nel browser. Le stringhe mathtext (formula, coordinate esatte,
tick esatti) sono quindi disegnate come geometria: `TextPath` +
`PathPatch`; il resto del testo resta `<text>` con Noto Sans / DejaVu Sans.
`function_plot.to_mathtext` rimuove `\left`/`\right`, `\tfrac → \frac`,
`\lvert`/`\rvert → |`, `\displaystyle`; formule con `\begin{…}` o `\over`
sono omesse; prova preventiva `MathTextParser("path").parse("$" + s + "$")`
in `try/except`. Senza il LaTeX del figlio (timeout, errore, sympy
assente, LaTeX troppo lungo) la formula è scritta dall'AST
dell'espressione con `expr_to_mathtext` (`x**2 - 2 → x^{2} - 2`,
`(x**2-1)/(x-2) → \frac{x^{2} - 1}{x - 2}`, costanti fuori da [1e-3, 1e6)
come `1.5 \cdot 10^{7}`), mai in sintassi Python; l'espressione in chiaro
è l'ultimo ripiego, con l'avvertenza `formula_not_mathtext`. La formula
è misurata (`text_width_pt`, `TextPath` sul font bundled) contro la
larghezza degli assi: se eccede, il corpo scende in proporzione fino a
`MIN_MATH_SIZE_PT = 6,5` (`fit_size`); se nessun candidato entra
nemmeno al minimo resta il solo nome `f(x)` con l'avvertenza
`formula_too_wide` (un polinomio di grado 11 con coefficienti decimali
usciva dal viewBox a sinistra). I tick usano `format_number(...,
scientific_small=True)`: notazione scientifica da 1e6 in modulo e, solo
sui tick, anche sotto 1e-3 (domini di ampiezza minima 1e-3); un tick
sotto `1e-9·ampiezza` è «0». Guardia: test `set(font-family) ⊆ {Noto
Sans, DejaVu Sans}` sull'SVG e test che la formula resti dentro il
viewBox.

**Leggibilità del testo** (correzioni di Fase D, TIP-3/4/5). I `PathPatch`
di matplotlib stanno a zorder 1 e le linee a 2: formula e coordinate
esatte finivano **sotto** le curve. Ora il testo disegnato come geometria
sta a `TEXT_ZORDER = 5` con un alone bianco (`withStroke` sul contorno del
`TextPath`, 2,2 pt: resta geometria, nessun raster) e le etichette
`<text>` hanno un riquadro bianco (`bbox`), restando testo estraibile.
Con le spine a zero anche i **tick** stanno dentro l'area dati: l'asse è
disegnato sopra i dati (`set_axisbelow(False)`) e la griglia delle curve
di livello è tracciata a mano sotto le curve, così un ramo che passa per
un tick non lo cancella più. Il **nome dell'asse x** sta sopra la freccia
(prima, a `va="top"` e 2 pt dal tick centrato sulla fine dell'asse, i due
si leggevano come un unico token: «8x»). L'**etichetta esatta di uno
zero** è omessa quando in quel punto c'è già un tick con lo stesso testo
(«0₀») e scende di una riga quando il tick dice altro («−π» sopra «−3»):
la scelta usa le posizioni del locator, che dipendono solo dai limiti già
fissati, quindi il render resta byte-deterministico. `function_plot` non produce mai raster (`<image>`): `contour` e
`fill_between` sono path, `imshow` non è usato; un test lo asserisce
perché `normalize_svg` rifiuta `<image>` mentre `MATPLOTLIB_RC` tiene
`svg.image_inline: True`.

### 4.5 `computed` e didascalia calcolata (mai persistita)

`computed = {approximate, variable, latex: [str], zeros: [{x, exact}],
zero_intervals: [[a, b]], critical_points: [{x, y, exact_x, exact_y,
type}], inflection_points, asymptotes: [{kind, x | m, q, expr, latex}],
discontinuities, integral: {between, value, exact} | None, tangents:
[{at, slope, exact_slope}], levels, truncated, warnings}`.
`figure_theme.function_caption(computed, language)` compone la coda dalle
frasi `courses.figures.function.*` (testo Unicode con √ e π via
`latex_to_unicode`, 3 decimali se approssimato, `format_number` in
notazione scientifica da 1e6 in modulo; il segnaposto `{{var}}` delle
frasi con «x =» — zeri, punti critici, flessi, tangente — prende
`computed["variable"]`, così una spec con `variable: "t"` non contraddice
formula e asse). La coda **non è mai persistita**: viene rigenerata a
render e passata come `extra_caption` al partial; il frontend la riceve
come `computed_caption` e la passa a `FigureFrame.extraCaption`. Guardia
anti-doppia coda: `if caption.rstrip().endswith(tail): tail = ""`. Se la
didascalia del docente non chiude con punteggiatura, il partial aggiunge
un punto prima della coda: la coda è un periodo autonomo e giustapposta
si leggeva «…razionale Zeri in x = −1, 1.» (Fase D, TIP-9).

### 4.6 Endpoint `render-function`

`POST /orgs/{org_id}/courses/{course_id}/lesson-assets/render-function`
(in `courses.py` accanto a `convert_lesson_asset_to_mermaid`),
`response_model=_FunctionRenderOut(svg, computed: dict, latex: list[str],
warnings: list[str], computed_caption: str, content_hash: str)`,
`@limiter.limit("30/minute")`, permesso `course:edit` (`_ensure_org` →
`resolve_permissions` → `get_course` con 404 silenzioso), poi
`figure_render_service.render_function(payload, language=course.language_code)`
con cache LRU di SVG e `computed` per `sha256(model_dump_json canonico) +
THEME_VERSION` (la didascalia, l'unica parte che dipende dalla lingua, è
composta a ogni chiamata) e scadenza monotona passata al motore. Errori:
body Pydantic → 422 standard (`loc` con `body` in
testa); semantici → `ValidationAppError("Specifica della funzione non
valida.", code="function_spec_invalid", meta={"errors": [...]})`; timeout
simbolico → 200 con `warnings` e `approximate`; render numerico o
matplotlib fallito, o timeout complessivo → 422 `function_render_failed`
/ `function_render_timeout` (il 409 nel repo esprime conflitti di stato,
non errori di input); 403 da `require`. Un'espressione **indefinita su
tutto il dominio** (`sqrt(x)` su [−2, −1], `log(x)` su un dominio
negativo, `x/0`) non è fra questi: l'endpoint risponde 200 con la figura
senza rami e l'avvertenza `expression_0_undefined`, che l'editor mostra
tradotta — l'anteprima diagnostica serve al docente più di un errore
secco. È invece un errore per `validate(deep=True)`, cioè nel percorso del
worker: il check fallisce, l'asset va al fix AI e la lezione non arriva a
`ready` con una figura vuota (Fase D, COR-2). Il client chiama con `timeout:
30_000` (il default di `apiClient` è 20 s).

Frontend (WP5): `FunctionEditor` con stato `FunctionFigureSpec` tipizzato
(il docente non vede JSON), anteprima con `useDebouncedValue(spec, 700)` +
`useQuery` (`retry: false`, `staleTime` 5 min, `placeholderData:
keepPreviousData`), errori `meta.errors` mappati sui campi; `FunctionFigure`
con `useQuery` per `(orgId, courseId, assetId, content)` e `staleTime:
Infinity` (react-query hasha la chiave: è la cache per asset e hash).

## 5. Tema accademico unico (D3)

`backend/app/services/figure_theme.py` è la sorgente di verità;
`frontend/src/lib/figureTheme.ts` è la copia da **mantenere allineata**
(stesse costanti, stesso ordine della palette, stessa configurazione
Mermaid, stesso `THEME_VERSION`), pinnata dal test di parità
`test_figure_theme.py` (`:304-327`). Ogni nuova chiave di `themeVariables`
va replicata nel `.ts` nello stesso commit.

- `THEME_VERSION = "2026.09.4"`: entra nella chiave di cache degli SVG; ogni
  modifica visibile del tema lo incrementa (Fase D lo ha portato da
  `2026.09.2` a `2026.09.3` per le tre correzioni del tema qui sotto, la
  revisione del catalogo a `2026.09.4` per le due di geometria, §18).
- Font: `FONT_FAMILY_PRIMARY = "Noto Sans"`, `FONT_STACK = '"Noto Sans",
  "DejaVu Sans", sans-serif'`, `FONT_ALLOWED = {Noto Sans, DejaVu Sans}`
  (guardia dei test), `MERMAID_FONT_FAMILY` con `Noto Sans CJK JP` per le
  label ideografiche.
- Palette di **Okabe e Ito** (`PALETTE`, 8 colori: blu `#0072B2`, vermiglio
  `#D55E00`, verde bluastro `#009E73`, arancio `#E69F00`, porpora `#CC79A7`,
  celeste `#56B4E9`, giallo `#F0E442`, nero `#000000`), distinguibile in
  scala di grigi e con deficit cromatici, ordinata per contrasto sul bianco;
  `PALETTE_LABEL` dà il colore del testo sopra ogni colore pieno (bianco
  solo su blu e nero). Neutri: `COLOR_INK #1F1F1F`, `COLOR_AXIS #4D4D4D`,
  `COLOR_GRID #D9D9D9`, `COLOR_MUTED #888888`, `COLOR_SURFACE #F4F6F8`.
  Niente gradienti, niente ombre.
- Mermaid: `mermaid_config(*, use_max_width, security_level="loose")` e
  `mermaid_initialize_js(...)` con `htmlLabels: false` **top-level**
  (condizione per 0 `<foreignObject>` sui tipi D8) e `theme: "neutral"`. Il
  tema neutral non deriva riempimenti e bordi da `primaryColor` (`nodeBkg =
  mainBkg`, `nodeBorder = border1`, `actorBkg`, `signalColor`, `cScale0..11`,
  gantt, stato): ogni variabile derivata letta dai 15 tipi D8 è **fissata
  esplicitamente** nei `themeVariables` (verificato sull'output reale di
  11.17.2 da `test_mermaid_theme_palette`), inclusa `pieOpacity: "1"`:
  senza, Mermaid 11 disegna le fette a 0,7 e i riempimenti percepiti
  (`#4798C6` invece di `#0072B2`) escono dalla palette e non coincidono
  con i riquadri della legenda, che stanno a opacità piena (Fase D,
  TIP-7). Limite noto: `sankey-beta`
  colora i nodi con `schemeTableau10` di d3, hard-coded nel renderer.
  `security_level`: `loose` nei Chromium headless del backend, `strict` nel
  browser dell'utente (default di `mermaidConfig` nel `.ts`). Il blocco per
  tipo porta anche l'unica correzione di GEOMETRIA: `radar: {marginLeft:
  100, marginRight: 240}`, perché con i 50 px di default la tela 700×700
  lascia 31 px all'etichetta dell'asse di sinistra e 71 alla legenda, e
  WeasyPrint taglia quello che esce dal `viewBox` (§18).
- Vega-Lite: `VEGALITE_THEME_CONFIG` (`figure_theme.py:458`): `font`,
  `background: "transparent"` (lo schema non ammette `null`), `view`
  360×220 senza bordo — `continuousWidth/Height` **e**
  `discreteWidth/Height`, perché le scale band e point non leggono le
  prime e userebbero il passo di default di 20 px per banda: un grafico a
  barre, la forma più probabile delle figure generate, usciva 147×318 px,
  più alto che largo e alto un terzo di colonna accanto a uno scatter
  (Fase D, TIP-6) —, `axisX: {labelAngle: 0, labelOverlap: "greedy"}`
  perché Vega-Lite ruota di −90° le etichette ordinali e nominali (resa da
  cruscotto, TIP-8), `axisY: {labelLimit: 220}` perché sull'asse y scorrono
  le categorie per esteso e i 120 px comuni le tagliavano oltre una
  ventina di caratteri — proprio nelle barre orizzontali, il tipo che
  esiste per le etichette lunghe (§18) —,
  `axis`/`legend`/`header`/`title`/`text` con font,
  dimensioni e colori del tema, `range.category = PALETTE`, `mark.color =
  PALETTE[0]`, `line.strokeWidth 2`, `point` pieno, `area` 0,35 con linea.
  È iniettato dal renderer: il modello non scrive mai `config` (D5).
- DOT: `DOT_DEFAULTS` (`graph`, `node`, `edge` con `fontname="Noto Sans"`,
  `bgcolor="transparent"`, nodi `box` arrotondati, colori dei neutri) e
  `dot_defaults_prelude(skip=…)`.
- **Una sola famiglia, non uno stack**, per Vega-Lite e DOT: vl-convert
  misura il testo con la prima famiglia della lista e una lista cambierebbe
  la geometria prodotta dal server. Nel container le due famiglie sono
  installate (`fonts-noto-core`), ma nel browser del docente «Noto Sans»
  di norma non c'è e Chromium ripiegava sul suo default, **Times**: le
  figure Vega-Lite e DOT uscivano in serif accanto a Mermaid e `function`
  in sans, e diverse dal PDF. Il ripiego è aggiunto **solo a schermo**, da
  `index.css` (`.figure--vegalite svg text, .figure--dot svg text`), e il
  webfont Noto Sans è caricato con Inter perché a schermo le metriche
  coincidano con il PDF (Fase D, TIP-1).
- matplotlib: `MATPLOTLIB_RC` (31 chiavi, solo valori serializzabili: il
  modulo non importa matplotlib): `svg.fonttype: none`, famiglie Noto Sans /
  DejaVu Sans, `mathtext.fontset: dejavusans`, sfondi trasparenti, ciclo
  colori = `PALETTE`, spessori e colori degli assi; `svg.hashsalt` è
  aggiunto per asset dal renderer.
- i18n: `FIGURE_I18N` (`:590-639`, sezione 6.4).

## 6. Convenzione editoriale e numerazione (D4, Q2)

### 6.1 `figure_numbering.py` (puro)

- `FIG_REF_RE = re.compile(r"\[FIG:([^\]\n]+)\]")`: **case-sensitive su
  `FIG`** come `_ASSET_REF_RE` (`course_lesson_pdf_service.py:399`) e
  `ASSET_REF_RE` del frontend (un `[fig:x]` non è sostituito da nessun
  renderer: numerarlo produrrebbe un numero fantasma); id confrontati con
  `.strip().lower()`.
- `append_uncited_figure_refs(markdown, asset_ids) -> str`: aggiunge
  `"\n\n[FIG:{id}]"` per gli asset mai citati, in ordine di array (A12).
  Salta gli id che il token non sa trasportare, cioè quelli che
  `FIG_REF_RE` non rilegge identici: `A]` produrrebbe `[FIG:A]]`, letto
  come `A`, e la coda darebbe un «Asset non trovato» falso più un `]`
  orfano invece della figura. Nessun percorso automatico genera id simili
  (il prompt e l'editor usano `A1`, `asset_new_2`), ma gli schemi
  accettano qualunque stringa 1..50 e un PATCH manuale può salvarli
  (Fase D, COR-4). Stessa regola nella copia `figureNumbering.ts`, pinnata
  dalla fixture condivisa.
- `compute_figure_numbers(markdown, asset_ids) -> dict[str, int]`
  (`{id_lower: N}`): prima occorrenza → N crescente; citazioni ripetute →
  stesso N (il numero è legato all'id, non all'occorrenza); id senza asset
  → nessun numero consumato (il blocco `missing-asset` non «ruba» numeri).
  Applicata al markdown **dopo** l'append, così la coda è numerata dopo le
  citate.
- `strip_figure_prefix(caption)`: `^\s*(?:figura|figure|fig\.?|abb\.?)\s*
  \d+(?:\.\d+)*[a-z]?\s*(?:[.:\-–—)](?!\d)\s*|$)` IGNORECASE, **cifra
  obbligatoria** («Figurativo» e «Fig. X» intatti) e **separatore
  obbligatorio** dopo il numero (o fine del testo): il piano lo aveva
  opzionale, ma così «Figure 2 shows the flow» diventava «shows the flow»,
  «Figura 3 e 4 a confronto» → «e 4 a confronto» e «Figura 1.2 Schema» →
  «2 Schema» (rilievo della verifica di WP4). Con il separatore
  obbligatorio e non seguito da cifra il prefisso non è mai «lossy»: una
  didascalia «Figura 3 Schema» resta intatta e viene resa come «Figura 1.
  Figura 3 Schema» (brutta ma completa; i prompt P3/P4 vietano al modello
  di iniziare la caption con «Figura N», WP3). Applicato **solo a
  render**, mai persistito; nel frontend `stripFigurePrefix` con la stessa
  fixture. Decisione presa in WP4 (correzione), da confermare con il
  docente: è un solo regex e una coppia della fixture.

Il testo di numerazione è il corpo della dispensa (`introduction →
sections → summary`, il corpus «referenziato» di
`course_lesson_content_service.py:1082-1088`): **non** `key_takeaways` e
`references`, che il template rende senza sostituzione dei tag. Includerli
farebbe divergere frontend e PDF.

### 6.2 Dove si calcola

Backend, in `render_lesson_html` (`course_lesson_pdf_service.py:1148`):
`body_md = _build_lesson_body_markdown(raw)` → `append_uncited_figure_refs`
→ `numbers = compute_figure_numbers(body_md, ids)` →
`_replace_summary_heading` e `_substitute_asset_refs` invariati →
`_build_asset_html_map(raw, …, figure_numbers=numbers,
labels=figure_labels(language))` → `_render_visual_asset_block(asset, …,
number=numbers.get(id.lower()), labels=…)`. WP4 ha riordinato
`render_lesson_html` in questo senso (il corpo markdown precede la mappa
degli asset, che riceve i numeri). Slide e video: `number=None`,
`variant="slide"`; `render_slides_html` costruisce i blocchi con la stessa
mappa `visual_svg_map` che il video le passa da
`_prerender_mermaid_for_slides` (nome storico, oggi tutti i formati).

Frontend (`lib/figureNumbering.ts`, «mantenere allineato con
`figure_numbering.py`»): `LessonContentView.buildFullMarkdown` si spezza in
corpo (intro/sezioni/sintesi + tag orfani) e coda (`key_takeaways`,
`references`); `figureNumbers` è calcolato lì e passato a `MarkdownRenderer`
come prop opzionale (i montaggi su frammenti in `LessonSlidesView` non la
passano). Coincidenza BE/FE: fixture
`backend/tests/fixtures/figure_numbering_cases.json` (duplicati, id
mancante, case diverso, `[fig:x]` ignorato, non citati in coda, nessuna
figura) usata da `test_figure_numbering.py`; lato FE smoke locale.

### 6.3 Markup unico: `figure_markup.py` e `partials/figure.html.j2`

Quarto `Environment` Jinja, dedicato: `Environment(loader=FileSystemLoader(
TEMPLATES_DIR / "partials"), autoescape=True, trim_blocks=True,
lstrip_blocks=True)` (gli ambienti esistenti hanno autoescape solo sul PDF
dispensa; slide e discorso no). `render_figure_html(*, body_html: Markup |
None, caption, alt_text, asset_id, fmt, number: int | None, labels,
variant: Literal["lesson", "slide"], fallback_source: str | None = None,
extra_caption: str = "") -> str`.

```html
<figure class="visual figure figure--{{ variant }} figure--{{ fmt }}"
        data-asset-id="…" role="figure" aria-label="{{ alt_text or caption }}">
  <div class="figure-body">{{ body_html }} | <pre class="figure-fallback">{{ fallback_source }}</pre></div>
  <figcaption class="figure-caption">
    <span class="figure-label">{{ label }}</span> {{ caption }} {{ extra_caption }}
  </figcaption>
</figure>
```

`label` = `labels["courses.figures.label"]` interpolato con `n`
(«Figura 3.») oppure `labelUnnumbered` («Figura.») nelle slide (A2);
`body_html` entra come `Markup` (prodotto da noi), tutto il resto è
escapato. Usato per **tutti** i formati: per Mermaid il body `<div
class="mermaid-svg">{svg}</div>` è byte-identico a oggi e cambia solo il
wrapper (A11-L3); nelle slide (`variant="slide"`) Mermaid resta `<img
class="mermaid-svg">` con data URI come prima; `image` e legacy passano
dallo stesso partial. Il wrapper interno `.mermaid-svg` va conservato
perché la regola `figure.visual:has(.mermaid-svg) .figure-body { padding:
1mm }` (`lesson_pdf.html.j2`) continui ad applicarsi.

Dettagli di `render_figure_html` (WP4): applica `strip_figure_prefix` alla
didascalia e collassa gli spazi bianchi di didascalia, `alt` ed
`extra_caption`; **guardia anti-doppia coda** (Q4): se la didascalia
dell'autore termina già con la coda calcolata (il docente ha copiato nel
campo caption il testo mostrato dall'anteprima, o una didascalia
localizzata la include), `extra_caption` è omessa — confronto esatto sul
suffisso dopo il collasso degli spazi, `if caption.rstrip().endswith(tail):
tail = ""`; vale per dispensa, slide e frame video perché tutto passa dal
partial, e il frontend (WP5) applica la stessa guardia in
`FigureFrame.extraCaption`. **Punto di separazione** (Fase D, TIP-9): se
la didascalia non termina con `.!?…:;` e c'è una coda, il partial aggiunge
un punto — la coda è un periodo autonomo («Zeri in x = −1, 1.») e il
prompt non obbliga a chiudere la didascalia, quindi si leggeva «Studio
della funzione razionale Zeri in x = −1, 1.»; `FigureFrame` applica la
stessa regola (`CAPTION_END_RE`). L'output non contiene righe vuote perché nella
dispensa il blocco entra nel markdown come HTML block di markdown-it, che
si chiude alla prima riga vuota: le righe vuote del sorgente di fallback
sono rese con U+00A0 (non è spazio per markdown-it, invisibile nel
`<pre>`) e le righe vuote di `body_html` (spazio bianco fra tag di un SVG
inline) sono rimosse — Mermaid non ne emette (le fixture 10 e 11 ne hanno
zero, quindi il body Mermaid resta byte-identico, A11-L3), ma la garanzia
vale per l'intero blocco e non solo per le parti prodotte dal partial. Un
formato sconosciuto va nel fallback (mai il contenuto in chiaro nel corpo)
con `log.warning("figure_format_unknown")`.

Per `function` la coda della didascalia arriva da
`figure_render_service.function_computed_caption(content, language=…,
asset_id=…)` → `FunctionRenderer.computed_caption`, che legge la cache dei
risultati del motore (`figure_function_service.cached_result`) e non
calcola mai nel thread di composizione dell'HTML. Le due cache hanno la
stessa dimensione (`figure_svg_cache_size`) ma **traffico diverso**:
l'endpoint `render-function` (anteprime dell'editor) riempie solo la
cache dei risultati, mai quella degli SVG; bastano quindi 256 anteprime
distinte fra due export perché il risultato di una figura sia espulso
mentre il suo SVG resta in cache (sequenza ordinaria: export della
dispensa → anteprime → export delle slide o ri-export). Correzione WP4:
`render_figure_map` serve un hit della cache SVG di `function` solo se anche
il risultato è in cache (`FunctionRenderer.result_cached`), altrimenti
rimanda la figura al renderer, e `FunctionRenderer.render_svg` sull'hit
incompleto ricalcola con `render_function_sync` (nel thread di render con
semaforo e timeout, mai in quello dell'HTML), che ripopola la cache dei
risultati; l'SVG è byte-identico (`hashsalt` fisso). Se nonostante ciò il
risultato manca al momento della composizione (eviction fra
`render_figure_map` e `render_lesson_html`, in pratica impossibile: i due
passi sono consecutivi), `computed_caption` emette
`log.warning("figure_caption_missing", asset_id=…, format="function",
reason="result_not_cached")` e la coda è vuota: mai una perdita silenziosa.
Test: `test_function_svg_cache_hit_without_engine_result_is_rerendered`
(motore finto) e `test_function_caption_survives_result_cache_eviction_with_the_real_engine`.

CSS: in `lesson_pdf.html.j2` le regole card generiche `figure {}`
(217-230) si restringono a `figure.table, figure.equation`;
`figure.visual` senza bordo né raggio, `margin: 5mm 0`, centrata,
`page-break-inside: avoid`; `.figure-caption` 9pt tondo centrato senza
bordo; `.figure-label` in grassetto; `.figure-svg { max-width: 100%;
height: auto; max-height: {{ max_figure_height_cm }}cm; display: block;
margin: 0 auto }`; `.mermaid-fallback, .figure-fallback` condividono la
regola. In `lesson_slides_pdf.html.j2` si modifica la regola esistente
`.slide-asset .caption, .slide-asset figcaption` (202-209) a 8pt tondo; si
aggiunge `.slide-asset .figure-svg, .slide-asset .mermaid-svg, .slide-asset
.uploaded-image { max-height: 80mm; width: auto; height: auto; object-fit:
contain }` che riconcilia il cap di 80 mm (180-189) con `.uploaded-image {
max-height: 100% }` (221-227); `.figure-fallback` e `.missing-asset`
ricevono CSS in entrambi i template (dal 16 settembre 2026 il cap fisso è
sostituito da `max-height: var(--figure-h)`, il box della pagina resa:
D12 in §12). `TableBlock`/`EquationBlock` nel
frontend e `figure.table`/`figure.equation` nel PDF **restano a card**:
sono elementi tipografici diversi da una figura.

Frontend `FigureFrame.tsx`: props `{ assetId, format, caption, altText,
number?, variant?: "lesson" | "slide", extraCaption?, className?,
children }` → `<figure role="figure" aria-label=…>` senza card, con
`<figcaption>` che usa `t("courses.figures.label", { n })` e
`stripFigurePrefix(caption)`; sostituisce le tre `<figure>` di
`VisualAssetBlock` e le tre di `SlideAssetRender`; il fallback `Suspense`
sta dentro `FigureFrame`. `.lesson-prose .figure img { border-radius: 0;
margin: 0 }` in `index.css`.

### 6.4 Localizzazione it/en con fallback it (A4)

`figure_theme.FIGURE_I18N = {"it": {...}, "en": {...}}` con le **stesse
chiavi pienamente qualificate** di `it.json`/`en.json`:
`courses.figures.label` («Figura {{n}}.»), `courses.figures.labelUnnumbered`
(«Figura.»), `illustrativeData`, `approxValues`, `renderError`, `loading`,
`missing`, `formats.{mermaid,vegalite,dot,function,image}`,
`function.{zeros,zero_intervals,critical_points,inflection_points,
asymptote_vertical,asymptote_horizontal,asymptote_oblique,integral,tangent,
levels,none,truncated}` (24 chiavi per lingua nella v1; `zero_intervals` è
la frase dei plateau su cui la funzione si annulla, `truncated` quella
delle categorie troncate al tetto di `MAX_NOTABLE_POINTS`). Con le
etichette dei quattro kind (capitolo 20, decisione B2) se ne aggiungono
**12 per lingua** sotto lo stesso prefisso — `ref`, `table.{label,
labelUnnumbered,ref}`, `equation.{…}`, `example.{…}`, `theorem.{label,ref}`
— per un totale di **36 chiavi `courses.figures.*` per lingua**, il numero
che `it.json` ed `en.json` portano oggi (contate con `jq '.courses.figures
| [paths(scalars)] | length'`) e che il backend ripete in `FIGURE_I18N`.
`figure_labels(language)` (`:722-728`) ritorna una
copia con fallback `it` (`de`, `None`, `ja` → it; `en-GB` → en), coerente
con `_labels_for` del PDF (`course_lesson_pdf_service.py:1946-1983`) e
con `fallbackLng: "it"` del frontend. `_interpolate` gestisce `{{n}}` e
`{{ n }}`. Le altre 22 lingue ricevono le etichette in italiano nel PDF
(come «Sintesi» oggi) e nel frontend finché l'amministratore non lancia
l'auto-translate. Il meccanismo nel frontend è il fallback di i18next:
`i18n.init({ resources: bundledResources, fallbackLng: "it",
nonExplicitSupportedLngs: true, … })`
(`frontend/src/i18n/index.ts:113`); i 22 locale non it/en non hanno
nessuna chiave sotto `courses.figures` (verificato: 36 in `it.json` e
`en.json`, 0 negli altri 22), quindi ogni `t("courses.figures.…")` cade
sull'italiano. Niente lookup nel DB delle traduzioni: il seed ha 226
chiavi e nessuna `courses.*` (in produzione sarebbe sempre vuoto). Test di
specchio `test_figure_i18n_mirrors_frontend` (flatten del JSON annidato):
un sottoalbero `courses.figures` vuoto, parziale o di cardinalità diversa
da `FIGURE_I18N` **fallisce**, non salta; lo skip resta solo per l'albero
`frontend/` assente, come nei test fratelli.

Due precisazioni emerse in Fase D.

- `courses.figures.illustrativeData` è un **valore canonico condiviso**
  BE/FE, non una chiave letta dal codice: la dicitura «Dati illustrativi,
  non sperimentali» vive come letterale nel prompt P3, perché la caption è
  contenuto generato dal modello e la regola LINGUA vuole che sia scritta
  nella lingua del corso — leggerla da `figure_labels`, che copre solo
  it/en, produrrebbe una coda italiana dentro una caption spagnola
  (Fase D, I18N-5).
- Nel frontend l'etichetta «Figura N.» segue la lingua dell'**interfaccia**
  (chiave i18next), mentre la coda calcolata di `function`, il PDF, le
  slide e i frame seguono la lingua del **corso**: con UI inglese su un
  corso italiano la vista mostra «Figure 6. … Zeri in x = −1, 1.» e il PDF
  «Figura 6.». È la convenzione già in uso nella vista per le intestazioni
  del corpo («Summary», «Key takeaways», in lingua UI sopra testo
  italiano) e non tocca nessun artefatto consegnato; cambiarla ha senso
  per l'intera vista, non per la sola figura (Fase D, I18N-2).

## 7. Normalizzazione degli SVG (Q3)

`svg_normalize.normalize_svg(svg, *, max_bytes) -> NormalizedSvg(svg,
width_px, height_px)`, modulo puro (regex sul solo tag radice + scansione),
per gli SVG di vl-convert, `dot` e matplotlib. **Mermaid non passa da qui**
(A11): al suo posto, dopo il pre-render, una scansione mirata dei soli
costrutti che caricano una risorsa esterna e degli `<a>` che puntano fuori
(sezione 3.1), che riusa da
qui le regioni «codice» del documento (`iter_tag_contents`,
`iter_style_bodies`: attributi e blocchi `<style>`, mai il testo dei nodi).

**Lato client la normalizzazione ha due funzioni, non una.** Gli SVG che
il browser produce da sé — viz-js per DOT, vega-embed per Vega-Lite —
passano da `sanitizeSvgElement` (`lib/figureFormats.ts`): rimuove gli
elementi attivi (`<script>`, `<iframe>`, `<image>`, `<foreignObject>`, gli
elementi SMIL) e i `<style>` interni, sostituisce gli `<a>` con i propri
figli, cancella i gestori `on*`, gli `href` non interni e gli attributi con
`url(…)` esterni. Per Mermaid quella funzione non va bene com'è: il tema
del diagramma vive proprio in un `<style>` interno e rimuoverlo smonta la
figura. `sanitizeMermaidSvgElement` è la variante che serve —
stessa logica sugli attributi e sugli `<a>`, ma la lista degli elementi
rimossi contiene solo ciò che carica o esegue (`<script>`, `<iframe>`,
`<image>`, SMIL), il `<style>` viene RIPULITO (`@import` e `url(…)`
esterni tolti, il resto intatto) e `<foreignObject>` resta, perché con
`htmlLabels: false` non ne esistono e cancellarne uno significherebbe
cancellare una label. `sanitizeMermaidSvg(svg)` è il suo involucro sulle
stringhe: analizza il markup di `mermaid.render` in un documento INERTE
(`DOMParser`, nessun contesto di navigazione, nessuna risorsa scaricata) e
lo riserializza, così l'`<image href="http://…">` sparisce PRIMA di
toccare il documento vivo. `MermaidDiagram.tsx` lo chiama sul risultato di
`mermaid.render` e mette nello stato solo il markup ripulito (giro 5,
SEC-1). Prova reale in `tests/test_frontend_mermaid_sanitize.py`: il
modulo è compilato con l'esbuild del frontend, Mermaid 11 rende davvero i
tre vettori (shape `img:`, `click href` di `flowchart` e di
`stateDiagram`) in Chromium, e si contano le richieste che il browser
tenta con e senza sanificazione.

**La sanificazione però arriva dopo la prima richiesta, e ciò che la
precede è la politica del documento** (giro 7): `mermaid.render` attacca
l'SVG al documento per misurarlo, quindi la GET di una shape `img:` parte
lì. La pagina servita da `frontend/nginx.conf` porta
`Content-Security-Policy: img-src 'self' data: blob: <origine degli
upload>` e il browser la rifiuta. Misurato con la pagina servita da un
server vero, Mermaid 11.17.2 (il pacchetto di `node_modules`), Chromium e
un listener HTTP: senza l'header arrivano le GET dei quattro vettori
(`img:`, `img:` dietro un `\r`, `img:` dietro `%%{x}%%`,
`sequenceDiagram properties icon`), con l'header non ne arriva nessuna e
Chromium registra la violazione in console. Le immagini
dell'applicazione non ne risentono — stessa origine, origine dello
storage, `data:` delle anteprime di Vega-Lite/DOT/`function`, `blob:`
delle anteprime di un file appena scelto: tutte caricate nella stessa
prova. La sola sorgente di immagine dell'applicazione che la politica
blocca è l'**immagine markdown esterna scritta nel corpo della dispensa**
(`![](http://…)`, che `MarkdownRenderer` rende come `<img src>` senza
riscrivere l'URL): è la stessa classe di richiesta dell'`<image>` di
Mermaid e il blocco è voluto, ma è un cambiamento visibile sui contenuti
già in DB (misurato in sezione 14.11, voce di sezione 13).
`sanitizeMermaidSvg` resta perché la politica blocca la richiesta ma non
toglie il nodo dal markup (sezione 3.1, punti 3 e 4). **La politica
governa le sole immagini**: senza `default-src`, `@import`, `@font-face`,
`<iframe>`, `<object>`, `<video><source>`, `prefetch` e `fetch` restano
liberi — misurato, sette classi di direttiva passano (sezione 14.11).

1. oltre `max_bytes` (`figure_svg_max_bytes`, 1,5 MB) → `SvgRejectedError`;
2. strip del prologo: BOM, `<?xml …?>`, `<!DOCTYPE …>`, commenti iniziali
   («Created with matplotlib», «Generated by graphviz»: determinismo fra
   versioni e date);
3. scansione che **rifiuta** (i renderer sono nostri: un'anomalia è un
   fallback, non una sanificazione parziale) — globale sul documento per
   `<script`, `<foreignObject`, `<iframe`, `<image` e per gli elementi
   SMIL `<set`, `<animate*`, `<handler` (possono assegnare `href` o `on*` a
   tempo di esecuzione; nessun renderer nostro li emette), anche con
   prefisso di namespace (`<svg:script>`, `<x:foreignObject>`: stesso
   elemento per un parser XML, giro 2); limitata al
   contenuto dei tag `<…>` per `<use` con `href` non-frammento, gestori
   `on[a-z]+=`, `href` esterni (quotati **o non quotati**: `href=http://x`),
   `javascript:`, `data:text/html`, `@import`, `url()` non-frammento. Il
   riconoscimento del tag **rispetta le virgolette**
   (`<(?:[^>"']|"[^"]*"|'[^']*')*>`): un `>` dentro un valore quotato
   (`aria-label="a > b"`) non chiude il tag e gli attributi successivi
   (`onclick=`) restano nella scansione — vl-convert e `dot` emettono
   `&gt;`/`&quot;` negli attributi, ma il modulo non dipende da questo. I
   `<use xlink:href="#m…">` e i `<clipPath>` interni di matplotlib passano.
   La limitazione al contenuto dei tag è una correzione dovuta in WP2b: la
   prima stesura scandiva l'intero documento e una label di nodo o un tick
   contenente `href=`, `url(` o `javascript:` faceva rifiutare la figura
   (fallback silenzioso), con test «vedi url(x)». Gli attributi `aria-*`
   (Vega: `aria-label="Title text 'vedi url(x)'"`) sono testo inerte che
   ripete titoli e dati dentro il tag: esclusi dalla scansione come il
   testo dei nodi (restano nell'SVG);
4. tag radice: `viewBox` letto o costruito, `width`/`height` convertiti in
   **px** (`pt × 96/72`, `mm × 96/25.4`, `in × 96`) e riscritti come
   dimensione intrinseca dell'`<img>` (`max-width: 100%` riduce ma non
   ingrandisce: un DOT a tre nodi resta piccolo; un SVG matplotlib a 374,4
   pt esce a 499,2 px, da verificare contro `max_figure_height_cm` in WP4),
   `preserveAspectRatio="xMidYMid meet"`, `max-width` rimosso dallo
   `style`, `xmlns` garantito. Nessun namespacing degli id: ogni `<img>` è
   un documento isolato.

Inline contro `<img>`: nella dispensa Mermaid resta **inline** (invariato);
Vega-Lite, DOT e `function` vanno in `<img class="figure-svg"
src="data:image/svg+xml;base64,…">` (elemento sostituito: `max-height`
rispettato, motivazione già in `course_lesson_slides_pdf_service.py:166-173`;
font risolti per nome di famiglia da Pango). Slide e video usano `<img>`
per tutti (A8). Limite dichiarato: il testo delle figure `<img>` non è
selezionabile nel PDF. `svg_to_data_uri` è unica: WP4 sostituisce
`_svg_to_data_uri` di `course_lesson_slides_pdf_service.py:146-153` con un
re-export, non ne aggiunge una terza.

Filtro dei log `_WEASYPRINT_SVG_NOISE_RE` (`core/logging.py:21-23`): la
alternanza attuale **non** copre `font-*` (fatto del piano risultato
falso): WP2b aggiunge `font-` e l'estensione prudenziale `clip-rule |
vector-effect | clip-path | image-rendering`, con un test che istanzia
`_WeasyPrintSvgNoiseFilter` direttamente (il filtro è agganciato solo in
`configure_logging`).

## 8. Validazione, fix AI e localizzazione (D6, D7)

### 8.1 Dispatch in `_validate_slots`

Oggi (`asset_validation_service.py:553-583`) tutti gli slot vanno nel
batch JS e i risultati sono letti per posizione; ogni kind diverso da
`latex` è trattato come Mermaid e il ramo `else` scrive il letterale
`"mermaid"`. La rimappatura esplicita:

```python
js_pos: dict[int, int] = {}
js_items: list[tuple[str, str]] = []
for i, s in enumerate(slots):
    if s.kind in ("latex", "mermaid"):
        js_pos[i] = len(js_items)
        js_items.append((s.kind, s.current))
js_results = await _validate_js_batch(js_items)
for i, slot in enumerate(slots):
    if slot.kind == "latex":
        ...  # latex2mathml + js_results[js_pos[i]] come oggi
    elif slot.kind == "mermaid":
        ok_s, err_s = REGISTRY["mermaid"].validate(slot.current)   # gate statico duro
        if not ok_s:
            checks.append(AssetCheck(slot.id, "mermaid", False, err_s)); continue
        ...  # pass-through se js_results is None, altrimenti js_results[js_pos[i]]
    else:                                                          # vegalite | dot | function
        r = REGISTRY.get(slot.kind)
        if r is None or not r.available():
            checks.append(AssetCheck(slot.id, slot.kind, False,
                                     f"{slot.kind}_unavailable", fixable=False)); continue
        ok, err = await asyncio.to_thread(r.validate, slot.current, deep=True)   # mai pass-through
        checks.append(AssetCheck(slot.id, slot.kind, ok, err))
```

`AssetCheck` (frozen, `:56-61`) acquisisce `fixable: bool = True` in coda
(compatibile con le costruzioni posizionali); `_validate_and_fix` non
manda al fix AI i non-fixable e alza subito `AssetFixUnresolvedError`.
`_collect_content_slots` / `_collect_slides_slots` filtrano
`asset.format in RENDERABLE_FORMATS` con `kind=asset.format`; `_sanitize`
accetta fence con tag `[a-zA-Z0-9_-]*` (oggi `[a-zA-Z]*` corrompe
```` ```vega-lite ````); i contatori di log a 860/894 diventano un
breakdown per kind. Il validatore Mermaid resta a pass-through con CDN
assente (come oggi); i tre formati nuovi sono offline e non degradano
mai.

### 8.2 Fix AI per kind

`openai_asset_fix_service`: `AssetKind = Literal["latex", "mermaid",
"vegalite", "dot", "function"]`; `_system_prompt` diventa un dict `{kind:
(IT, EN)}` con tre coppie nuove — Vega-Lite: solo la spec JSON, niente
`config`/`$schema`/`data.url`/`selection`/`tooltip`, aggiungere `clip:true`
e `scale.domain`, conservare i dati; DOT: solo il sorgente, label nella
lingua, niente `image=`; `function`: solo JSON conforme a
`FunctionFigureSpec`, `**` non `^`, `2*x` non `2x`, whitelist delle
funzioni, nessun numero calcolato. Kind ignoto → `ValueError` (errore di
programmazione, A20), non più prompt LaTeX. `_ERROR_CAP = 1600` (era 800:
gli errori dello schema JSON sono più lunghi), contesto della lezione
resta `[:600]`, `openai_asset_fix_max_tokens` resta 4.000 (A16: una spec
≤ 4.000 caratteri ≈ 1.500 token). Le varianti Mermaid dicono già «11.x,
tipi D8, label testo semplice, niente `%%{init}%%`» (WP1).

### 8.3 Localizzazione (D7)

`_LocField.kind` accoglie `vegalite | dot | function` con
`extract_translatable` / `apply_translations` del renderer: Vega-Lite
`title`, `axis.title`, `legend.title`, `header.title` e i `text` letterali;
DOT i valori di `label | xlabel | headlabel | taillabel`; `function`
`expressions[i].label` e `annotations[i].label`. Tutti e tre applicano la
traduzione **nel sorgente**, senza riserializzarlo: DOT sostituisce il
corpo della label, Vega-Lite e `function` passano da `json_spans`, che
individua la posizione esatta di ogni stringa valore e cambia le sole
tradotte. Conseguenza verificata: la formattazione scritta dal docente
sopravvive al ciclo e una traduzione identica lascia il sorgente
byte-identico (I18N-3, giro 2). La tupla «structural»
(`:828`, oggi `("mermaid", "table")`) diventa «tutti i kind non-text»: un
asset localizzato viene rivalidato offline. Il prompt di
`openai_asset_localize_service._system_prompt` (38-74) dichiara gli
invarianti JSON (chiavi, `field`, `type`, espressioni `datum.*`) e DOT (id
dei nodi, `->`/`--`, attributi diversi da `label`).

### 8.4 Schemi OpenAI

`build_lesson_content_json_schema(*, objective_ids=(), visual_formats=())`:
con entrambi vuoti ritorna la costante **per identità** (test esistente);
altrimenti deepcopy con l'`enum` ristretto; la costante base
(`openai_lesson_content_service.py:409`, oggi `["mermaid"]`) elenca i
quattro formati; il chiamante passa `available_formats()`. Fase 4: nuovo
`build_lesson_slides_json_schema(*, visual_formats)` (deepcopy, rimozione
di `asset_type` e dei tre legacy dallo schema strict) con `visual_formats
= available_formats() − {"function"}` (A1).

### 8.5 Pre-render e fallback all'export (WP4)

`_prerender_visual_assets_for_lesson(content, *, language="it") = await
render_figure_map(...)` (WP4 usava `render_svg_map`, oggi sua proiezione)
con l'alias del vecchio nome
`_prerender_mermaid_for_lesson`; `_prerender_mermaid_for_slides(content_raw,
new_assets, *, language)` (nome storico, alias
`_prerender_visual_assets_for_slides`) fonde gli asset di Fase 3 e i
`new_assets` di Fase 4 e delega alla stessa funzione. `render_lesson_html`
e `render_slides_html` accettano `visual_svg_map=None` e fondono
`{**(mermaid_svg_map or {}), **(visual_svg_map or {})}` (`mermaid_svg_map`
mantenuto per i chiamanti esistenti); `materialize_lesson_pdf`,
`materialize_lesson_slides_pdf` e `render_slides_to_png` (video) passano
`visual_svg_map`. `_render_visual_asset_block(asset, *, visual_svg_map=None,
number=None, labels=None, variant="lesson", language=None,
lesson_code=None)` e `_build_slide_asset_html(asset, *, kind,
visual_svg_map=None, math_svg_map=None, language="it", labels=None,
lesson_code=None)` (delega completa per `visual`/`new_visual` con
`variant="slide"`, `number=None`): ramo `mermaid` testualmente identico
(`<div class="mermaid-svg">` nella dispensa, `<img class="mermaid-svg">`
nelle slide), `elif fmt in RENDERABLE_FORMATS` → `<img class="figure-svg"
src="{svg_to_data_uri(svg)}" alt="…">` oppure body `None` → `<pre
class="figure-fallback">`; tutto passa da `render_figure_html`;
`_svg_to_data_uri` del servizio slide è un re-export di
`svg_normalize.svg_to_data_uri`. Le chiavi della mappa asset di
`_build_asset_html_map` sono normalizzate con `.strip().lower()` per tutti
i kind (FIG/TAB/EQ/EX), come `_substitute_asset_refs` e
`compute_figure_numbers`: un asset con id « A » citato come `[FIG: A ]` è
reso e numerato (prima restava «Asset non trovato» pur essendo numerato;
correzione WP4). Quando il partial riceve `body_html=None`
per un formato renderizzabile, `log.error("figure_render_fallback",
lesson_code=…, asset_id=…, format=…, reason="svg_missing")` (A23): un
fallback all'export è un errore visibile nei log, non un caso silenzioso.
Il log è structlog (`PrintLoggerFactory`): i test lo osservano con
`structlog.testing.capture_logs()`, non con `caplog`.

CSS (WP4): `lesson_pdf.html.j2` restringe la card a `figure.table,
figure.equation`, `figure.visual` senza bordo, `.figure-caption` 9pt tondo
centrato, `.figure-label` in grassetto, `.figure-svg` con `max-height:
{{ max_figure_height_cm }}cm` e senza `width: 100%`, `.mermaid-fallback,
.figure-fallback` e `.missing-asset` (prima senza CSS);
`lesson_slides_pdf.html.j2` porta `.slide-asset figcaption` a 8pt tondo con
`.figure-label` in grassetto, una regola unica di altezza per
`.figure-svg`/`.mermaid-svg`/`.uploaded-image` (`max-height: 80mm` allora,
`var(--figure-h)` dal box di pagina da D12; la vecchia `.uploaded-image
{ max-height: 100% }` è rimossa) e regole per `.figure-fallback` e
`.missing-asset` (prima testo nudo). `_VIDEO_OVERRIDE_CSS` non tocca
`.slide-asset`: i frame video ereditano tutto.

### 8.6 Revisione figura ↔ testo e costo degli asset (D15, D16; WP6, 17 settembre 2026)

In Fase 3 `validate_and_fix_content_assets` esegue **fix → revisione →
localizzazione** e ritorna `(output, assets_usage)`.

- **Servizio**: `openai_figure_review_service.review_figure` (PROMPT 17,
  IT/EN in `_SYSTEM_PROMPTS`, `response_format` json_schema strict
  `{verdict, reason, source}` con `source` annullabile, nessuna
  persistenza; `gpt-4o-mini`, `max_completion_tokens` 4.000). Ingresso:
  formato, sorgente, didascalia e testo alternativo, testo integrale del
  primo blocco che cita la figura (`FIG_REF_RE` su introduzione, sezioni,
  sintesi, poi esempi e tabelle; tetto 24.000 caratteri)
  o il corpo della lezione troncato a 12.000, misura dell'originale (nodi e
  archi di `graph_rules`, incroci e difetti di `figure_geometry`, corpo
  del testo nella dispensa A4 di default da `fit_figure_width_mm`), lingua
  ed eventuale motivo del rifiuto precedente. `coerente` è il predefinito
  del prompt e dello schema Pydantic.
- **Orchestrazione** (`asset_validation_service._review_figures`, come la
  localizzazione: riscrittura del sorgente e rivalidazione): una
  `render_figure_map` sugli originali per la misura del prompt; chiamate
  concorrenti per giro, al più `figure_review_max_attempts` (2) e con al
  più `figure_review_max_parallel` (4) in volo per processo (semaforo per
  loop: il tetto vale anche fra lezioni concorrenti); per ogni
  `correggi`: `_sanitize` → controlli deterministici (`missing_source`,
  sorgente identico, `placeholder`, `type_changed`, `density_increased`,
  `nodes_removed`, `nodes_isolated`, e per i formati senza archi
  `rows_removed`, `sequence_removed`, `fields_removed`,
  `expressions_changed`, `domain_reduced`) → `_validate_slots` (la stessa del fix: parse Mermaid 11 in Chromium,
  `validate(deep=True)` per DOT, Vega-Lite e `function`) → per Mermaid e
  DOT una sola `render_figure_map` con originale (hit della cache) e
  riscrittura (DOT in cache dalla validazione profonda, Mermaid resa e
  misurata nella pagina del pre-render) → `review_acceptance`. Le rese
  della revisione sono **speculative** (`cache_failures=False`): servono a
  misurare, non a pubblicare, e un loro guasto non mette in cache negativa
  le chiavi delle figure originali (che l'export dei 60 s successivi
  salterebbe).
- **Conservazione dei nodi** (giro 1 della verifica): dai conteggi di
  `graph_rules`, che espongono anche `node_ids` (id dei nodi nominati) e
  `linked_ids` (id estremi di almeno un arco), la riscrittura deve
  contenere tutti gli id dell'originale e, per i tipi contati senza id
  (mindmap, treemap, timeline, pie, quadrant, radar, xychart, gantt), lo
  stesso numero di nodi (`nodes_removed: nodi n → m (mancanti: …)`);
  nessun nodo che nell'originale era estremo di un arco può restarne
  senza (`nodes_isolated: senza archi …`). Togliere archi resta ammesso:
  un K4,4 riscritto come abbinamento (16 → 4 archi, tutti i nodi ancora
  collegati) è accettato. Il prompt dice lo stesso: tutti i nodi con i
  loro id, il testo di un nodo si corregge cambiando l'etichetta.
- **Regola di accettazione** (WP5 lascia gli incroci diagnostici e la
  misura saltabile): riscrittura resa (`measure_unavailable` altrimenti,
  Chromium assente compreso) e misurata (`measure_skipped`); originale
  misurato → incroci non superiori e nessun codice di difetto in più;
  originale saltato o non reso → la riscrittura deve essere senza
  difetti. Con entrambe le misure saltate la riscrittura è respinta:
  «accetta solo con misura» esclude la decisione della sola validazione.
  Il revisore gira anche sulle figure con la misura dell'originale saltata:
  può proporre una versione meno densa, che entra solo se misurabile e
  senza difetti.
- **Conservazione dei dati senza archi** (giro 2 della verifica): Vega-Lite
  e `function` non hanno né archi né geometria, e la sola validazione
  accetterebbe qualunque spec valida — anche una che cancella i dati.
  `_DATA_GUARDS` (tabella per formato, D2) li misura nel sorgente prima
  della validazione: per Vega-Lite `vegalite_data_metrics` conta le righe
  inline (`data.values` e `datasets`, a ogni livello della composizione),
  i blocchi `data.sequence` e i campi citati (`field`), e la riscrittura
  non può ridurli (`rows_removed: righe 4 → 1`, `sequence_removed`,
  `fields_removed: campi a, b`); per `function` `parse_function_spec` dà
  espressioni (confrontate senza spazi) e dominio, e la riscrittura deve
  conservarle tutte (`expressions_changed`) senza restringere il dominio
  (`domain_reduced`). Il confronto è sui conteggi e sui campi, non
  sull'identità delle righe: correggere un valore sbagliato o le etichette
  resta ammesso, cancellare o sostituire i dati no. Il prompt dice lo
  stesso (PROMPT 17: «conserva tutte le righe di `data.values` e i campi
  dell'encoding», «conserva le espressioni e il dominio»).
- **Esiti**: accettazione applicata a fine revisione; rifiuto →
  originale byte-identico, voce di cache dell'originale intatta (la
  riscrittura respinta resta in cache sotto la propria chiave),
  `figure_review_rejected` e motivo al tentativo successivo; ogni
  chiamata → `figure_review_verdict` (`asset_id`, `verdict`, `accepted`,
  `outcome`, `reason`, `cost_usd`); `figure_review_measured` con
  `stage` e `duration_ms`. La revisione non solleva mai
  (`figure_review_call_failed`, `figure_review_failed`): il worker
  tratta ogni eccezione come rigenerazione della lezione. Ogni errore di
  una singola chiamata (HTTP, corpo 200 non JSON, schema, eccezione del
  client fuori da `OpenAIError`) è un tentativo perso di quella figura:
  `_ask_review` lo cattura, così `asyncio.gather` non chiude il giro con
  chiamate sorelle ancora in volo, le loro riscritture restano valutate e
  il loro usage è contato. Una chiamata pagata che non produce un verdetto
  (200 con JSON troncato dal tetto dei token, schema fuori contratto) non
  perde il costo: il servizio costruisce l'usage prima di leggere la
  risposta e lo consegna con l'eccezione (`OpenAIError.usage`), così la
  voce entra in `content_tokens.assets` e `cost_usd` compare anche nel log
  del tentativo perso. Vale anche per il gemello del fix, il cui usage
  viaggia con `AssetFixUnresolvedError` fino al log del worker.
- **Costo in tempo** (macOS, 17 settembre 2026, Chromium e CDN reali,
  client OpenAI finto, quindi senza la latenza del modello; tre giri per
  esito): con `coerente` la revisione di un flowchart Mermaid costa la
  sola resa dell'originale, 0,84-1,75 s (un Chromium per lezione con
  figure Mermaid, cache calda per l'export); con `correggi` accettato
  2,82-3,27 s in tutto, cioè 1,83-2,12 s in più per il parse e la resa
  della riscrittura (0,94-1,13 s la sola misura); DOT, Vega-Lite e
  `function` sono hit della cache della validazione profonda.
- **Costo in denaro** (D16): fix, localizzazione e revisione usano
  `openai_pricing.build_usage_dict`; le voci (`phase`, `asset_id`, campi
  dell'usage con `cost_usd`) finiscono in `content_tokens.assets` con la
  somma `assets_cost_usd` (`merge_assets_usage` nel worker, prima di
  `materialize_lesson_content`); `cost_usd` resta la chiamata principale.
  La dashboard admin (`admin_metrics_service._cost`) somma nella fase
  `content` `cost_usd` e `assets_cost_usd` di ogni riga, con una chiave
  assente che vale 0. Dopo la validazione il worker rilegge
  `content_status` (`lesson_content_cancelled_post_assets`): un
  annullamento arrivato durante fix, revisione o localizzazione scarta il
  risultato come dopo la chiamata di Fase 3. Stima con il listino corrente: 0,00063
  USD per 3.000 + 300 token su `gpt-4o-mini`. Nessuna colonna, nessuna
  migrazione, nessun backfill.

## 9. Siti `== "mermaid"` e decisione per ciascuno (D2)

Undici confronti letterali con il formato esistono a HEAD; il test
`test_asset_validation_dispatch.py` contiene un grep che **vieta nuovi
siti** fuori da quelli dichiarati qui.

| # | File:riga (HEAD `8ce8160`) | Contesto | Decisione |
|---|---|---|---|
| 1 | `asset_validation_service.py:385` | `_collect_content_slots`, filtro degli asset da validare | registro: `asset.format in RENDERABLE_FORMATS`, `kind=asset.format` |
| 2 | `asset_validation_service.py:447` | `_collect_slides_slots`, stesso filtro per le slide | come 1 |
| 3 | `asset_validation_service.py:729` | campi localizzabili degli asset di contenuto (`_LocField`) | registro: `extract_translatable`/`apply_translations` del renderer del formato |
| 4 | `asset_validation_service.py:769` | campi localizzabili dei `new_assets` delle slide | come 3 |
| 5 | `asset_validation_service.py:828` | tupla «structural» `("mermaid", "table")` dopo la localizzazione | tutti i kind non-text: rivalidazione offline dopo la traduzione |
| 6 | `asset_validation_service.py:860` | contatore `mermaid=` nel log della validazione dei contenuti | breakdown per kind |
| 7 | `asset_validation_service.py:894` | contatore `mermaid=` nel log della validazione delle slide | breakdown per kind |
| 8 | `course_lesson_pdf_service.py:424` | ramo di render `_render_visual_asset_block` | **resta letterale**: il body `<div class="mermaid-svg">` deve restare byte-identico (A11-L3); eccezione dichiarata a D2 |
| 9 | `course_lesson_pdf_service.py:641` | `_prerender_mermaid_for_lesson`, filtro `!= "mermaid"` | sostituito da `render_svg_map` (`_prerender_visual_assets_for_lesson`); da WP3 `render_figure_map`, di cui `render_svg_map` è la proiezione |
| 10 | `course_lesson_slides_pdf_service.py:181` | ramo di render `_build_slide_asset_html` | **resta letterale**, come 8 |
| 11 | `openai_asset_fix_service.py:143` | `_system_prompt`, `if kind == "mermaid"` | dict per kind (sezione 8.2) |

Le proiezioni duplicate «15 tipi senza alias» di
`openai_asset_fix_service.py:45-47` e `openai_image_to_mermaid_service.py:40-42`
sono centralizzate in `figure_theme.MERMAID_D8_TYPES` (WP2b).

Dopo WP2b restano i siti 8, 9 e 10 (9 sparisce con WP4) più due confronti
sul **kind** dello slot in `asset_validation_service._validate_slots` (gate
statico prima del batch JS e lettura del risultato JS): sono il dispatch
prescritto da Q1 — Mermaid è l'unico formato con il parse nel batch
Playwright — non confronti di formato. Il test grep li dichiara con il
numero massimo di occorrenze per file.

Il 18 settembre 2026 si aggiunge il sito 12,
`figure_render_service.render_chain_variants` (D15, §21): **resta
letterale**, perché la direzione di un grafo è una nozione del solo
Mermaid — `chain_layout` legge la sintassi di `flowchart`, e DOT ha il
suo `rankdir`, Vega-Lite e `function` non hanno direzione. Non è un
dispatch mancato: non esiste un metodo del `FigureRenderer` da chiamare
al suo posto.

## 10. Configurazione

Blocco «Figure accademiche (Fase 3/4)» di `backend/app/core/config.py:280-315`,
replicato in `.env.example` e `docker-compose.prod.yml` (WP2a). Vedi anche
[04 — Configuration](../04-configuration.md).

| Setting | ENV | Default | Significato |
|---|---|---|---|
| `figure_vegalite_enabled` | `FIGURE_VEGALITE_ENABLED` | `True` | kill-switch: `False` toglie `vegalite` dallo schema strict e dal validatore; i contenuti già in DB ricadono sul fallback `<pre>` a render |
| `figure_dot_enabled` | `FIGURE_DOT_ENABLED` | `True` | idem per `dot` |
| `figure_function_enabled` | `FIGURE_FUNCTION_ENABLED` | `True` | idem per `function` (Mermaid non è disattivabile) |
| `mermaid_cdn_version` | `MERMAID_CDN_VERSION` | `"11.17.2"` | unico pin per validatore Playwright e pre-render PDF/video; il frontend segue con il lock npm |
| `figure_render_timeout_seconds` | `FIGURE_RENDER_TIMEOUT_SECONDS` | `20` | tetto per il batch di figure di una lezione (`asyncio.wait_for`): oltre, le figure mancanti degradano a fallback e l'export prosegue |
| `figure_function_timeout_seconds` | `FIGURE_FUNCTION_TIMEOUT_SECONDS` | `10` | tetto del calcolo simbolico nel processo figlio, ucciso allo scadere; resta il risultato numerico con «Valori approssimati.» |
| `figure_render_max_workers` | `FIGURE_RENDER_MAX_WORKERS` | `2` | render CPU-bound concorrenti dell'**export** e delle anteprime `render-function`; NON copre `validate(deep=True)` dei worker di Fase 3/4, limitata da `course_lesson_{content,slides}_max_concurrency` (sezione 2.2, Fase D COR-5); 2 per la VM a 2 core |
| `figure_svg_cache_size` | `FIGURE_SVG_CACHE_SIZE` | `256` | cache LRU in memoria degli SVG (chiave: formato, hash del sorgente sanificato, `THEME_VERSION`; la lingua non entra nella chiave, sezione 2.2) e, con la stessa dimensione, dei risultati del motore `function` |
| `figure_svg_max_bytes` | `FIGURE_SVG_MAX_BYTES` | `1_500_000` | oltre, l'SVG prodotto è rifiutato (fallback) |
| `figure_dot_max_chars` | `FIGURE_DOT_MAX_CHARS` | `12_000` | limite del sorgente DOT accettato dal validatore (oltre 12.000 vale il tetto di risorsa degli asset generati e di quelli cambiati nel PATCH, `VISUAL_ASSET_CONTENT_MAX_CHARS`) |
| `graphviz_dot_path` | `GRAPHVIZ_DOT_PATH` | `None` | percorso del binario `dot`; `None` = ricerca nel `PATH` |
| `openai_figure_review_model` | `OPENAI_FIGURE_REVIEW_MODEL` | `"gpt-4o-mini"` | modello del revisore figura ↔ testo (sezione 8.6, PROMPT 17) |
| `openai_figure_review_reasoning_effort` | `OPENAI_FIGURE_REVIEW_REASONING_EFFORT` | `None` | reasoning del revisore, inviato solo ai modelli reasoning |
| `openai_figure_review_max_tokens` | `OPENAI_FIGURE_REVIEW_MAX_TOKENS` | `4_000` | `max_completion_tokens` del revisore |
| `figure_review_max_attempts` | `FIGURE_REVIEW_MAX_ATTEMPTS` | `2` | chiamate per figura; `0` = nessuna |
| `figure_review_max_parallel` | `FIGURE_REVIEW_MAX_PARALLEL` | `4` | chiamate del revisore in volo per processo (semaforo per loop), anche fra lezioni concorrenti |
| `figure_review_enabled` | `FIGURE_REVIEW_ENABLED` | `True` | kill-switch: `False` = nessuna chiamata HTTP e nessuna resa |

Un formato è offerto al modello solo se abilitato **e** la dipendenza è
presente (`available_formats()`); le guardie di lunghezza dei prompt sono
deterministiche e indipendenti dall'ambiente (A19). Dipendenze: pip
`vl-convert-python>=1.9`, `altair>=6,<7` (solo per il file dello schema),
`jsonschema>=4.18`, `sympy>=1.13`, `matplotlib>=3.9`; apt `graphviz`;
`MPLCONFIGDIR=/tmp/cache/matplotlib` e `MPLBACKEND=Agg` nel Dockerfile;
override mypy per `sympy.*`, `mpmath.*`, `jsonschema.*` (senza `py.typed`).

## 11. Assunzioni dichiarate (A1-A24)

Decisioni prese in Fase B (A1-A16) e nella ripresa del 7 settembre
(A17-A24), approvate dal docente; in forma discorsiva.

- **A1 — `function` in Fase 4.** L'alias Pydantic accetta `function`
  ovunque (il docente può aggiungerlo a mano anche nelle slide, il
  renderer lo serve), ma lo schema strict di Fase 4 offre al modello solo
  `mermaid | vegalite | dot`: P4 non ha budget per lo schema di `function`
  e D9 parla del prompt di Fase 3. `asset_type` e i tre legacy escono dallo
  schema strict di Fase 4.
- **A2 — Slide senza numero.** «Senza numerazione progressiva del testo ma
  con la stessa etichetta» è letto alla lettera: «Figura.» senza numero
  nelle slide e nei frame video, uniforme fra asset di Fase 3 e
  `new_assets`, nessuna seconda numerazione che contraddica la dispensa.
- **A3 — Niente pacchetto pip `graphviz`.** `dot` via `subprocess.run` senza
  shell, con timeout, `cwd` vuoto ed env minimale; dipendenza apt.
- **A4 — Localizzazione backend it/en, fallback it**, coerente con
  `_labels_for` e `fallbackLng`. Niente dizionario a 24 lingue, niente
  lookup DB. Le frasi della didascalia calcolata seguono la stessa regola.
- **A5 — Guardie dei prompt.** Le regole nuove in P3 pesano circa 2.400
  caratteri lordi contro i 948 del blocco sostituito: `MAX_SYSTEM_P3` sale
  a 22.500 dopo la misura reale; P4 resta sotto 14.500 (regola 3 riscritta
  rinviando alle regole di Fase 3); P5 entro il margine.
- **A6 — Baseline di qualità.** «Nessun WP si chiude con test rossi o
  warning nuovi» vale sui file toccati e sul non aumento dei conteggi; la
  baseline rossa preesistente (ruff, mypy, eslint) non viene sanata.
- **A7 — Ambiente locale.** Le installazioni previste (sympy, vl-convert,
  jsonschema, altair, graphviz, Postgres, `npm install`) sono già state
  fatte: superata.
- **A8 — Vettoriale ovunque.** Vega-Lite, DOT e `function` producono SVG
  anche per slide e video (Chromium rasterizza a 1980×1400: nitido; un PNG
  a 200 dpi sarebbe più morbido e 5-10 volte più pesante). Il «200 dpi» di
  D9 resta come `dpi` della `Figure` (geometria di tick e corpi).
- **A9 — Livello 2 di `function`** solo se il livello 1 chiude verde entro
  WP7; altrimenti lavoro successivo, con lo schema strict che continua a
  rifiutarlo.
- **A10 — Mermaid 11.17.2** come unico pin (`settings.mermaid_cdn_version`)
  e lock npm alla stessa versione.
- **A11 — «Byte-a-byte» sugli SVG Mermaid** è impossibile fra 10.9.4 e 11.
  Regressione zero dimostrata a cinque livelli: L1 input al pre-render
  identico (fixture di `content_raw`), L2 `_strip_mermaid_max_width`
  byte-identico su fixture 10.9.4, L3 blocco `<div class="mermaid-svg">`
  byte-identico a parità di SVG (cambia solo il wrapper D4), L4
  determinismo v11, L5 `revalidate_mermaid_assets.py` sul DB. Fase D ha
  qualificato due livelli. **L1**: l'input non è identico in due casi
  dichiarati altrove — `sanitize()` toglie i caratteri di controllo
  (sezione 2.1) e il filtro guarda il contenuto grezzo (sezione 2.2, dove
  ora il sorgente svuotato dalla sanificazione viene saltato). **L4**: il
  determinismo byte a byte vale per 13 dei 15 tipi D8; `classDiagram` ed
  `erDiagram` cambiano i punti di controllo dei contorni disegnati da
  rough.js fra un processo e l'altro (in 10.9.4 erano deterministici), a
  forma e resa identiche — gli estremi delle cubiche coincidono, i punti
  di controllo restano sul segmento, 0 pixel di differenza a 2×. Nessuna
  conseguenza sui documenti (l'SVG è unico dentro un export), ma due PDF
  dello stesso contenuto prodotti da processi diversi non sono
  byte-identici. Il test di determinismo copre `flowchart` (Fase D,
  REG-3).
- **A12 — Figure non citate rese in coda.** «Vanno in coda» è letto come
  collocazione: gli asset senza `[FIG:id]` compaiono dopo la sintesi, prima
  dei punti chiave, e ricevono gli ultimi numeri, nel PDF e nella vista.
  Oggi sono invisibili: cambiamento dichiarato per i contenuti con asset
  orfani (lo script L5 ne conta le lezioni).
- **A13 — Isolamento del CPU-bound.** sympy e vl-convert (Deno in-process,
  non interrompibile) girano in un processo figlio `spawn` ucciso allo
  scadere; matplotlib e numpy in thread, bounded dai limiti della spec.
  Rispetta la lettera di D9 («in `asyncio.to_thread`»: il thread attende il
  figlio). Un processo caldo dedicato è un'ottimizzazione futura.
- **A14 — Testo matematico come geometria** (`TextPath`), sezione 4.4. Il
  ripiego (symlink dei TTF di matplotlib + `fc-cache` nel Dockerfile) non è
  disponibile così com'è: il Dockerfile non ha fontconfig e il browser
  resterebbe scoperto.
- **A15 — Mermaid al salvataggio manuale**: solo gate statico D8, nessun
  Chromium nella richiesta HTTP. Il PATCH valida solo gli asset con
  `(format, content)` cambiati: un edit del testo non blocca lezioni con
  diagrammi legacy già in DB.
- **A16 — `openai_asset_fix_max_tokens` resta 4.000**; il cap sull'errore
  passa da 800 a 1.600.
- **A17 — Allowlist ruff dichiarata.** `allowed-confusables = ["−", "×",
  "–"]` in `pyproject.toml` resta: le didascalie usano i segni tipografici
  per scelta editoriale. La baseline A6 vale a parità di configurazione
  (394 violazioni con allowlist, circa 468 senza, a HEAD): il «calo»
  raccontato nei commit di WP2a è un rilassamento di configurazione, non un
  miglioramento, e la consegna lo dichiara.
- **A18 — Eccezioni con suffisso `Error`**: `SvgRejectedError`,
  `FigureTimeoutError` (regola N818); `FigureComputeError` invariato.
- **A19 — Testo dei prompt statico, schema dinamico.** P3/P4 descrivono
  sempre i quattro formati; solo l'`enum` dello schema strict è ristretto
  da `available_formats()`. Le guardie di lunghezza restano deterministiche.
- **A20 — Kind ignoto nel fix AI → `ValueError`**, non prompt LaTeX:
  `AssetKind` è un `Literal` chiuso.
- **A21 — `courseRef` via React context** (`CourseRefContext` fornito dai
  due container) invece di cinque livelli di prop-drilling per lato;
  `LessonContentView` resta memoizzato sul solo `content`.
- **A22 — `MermaidEditor.TEMPLATES` non riusa `MERMAID_D8_SAMPLES`**: i
  campioni sono minimi per il test di `foreignObject`, i template
  dell'editor sono didattici. L'assunzione regge ancora dopo il
  completamento del catalogo (8 settembre 2026, sezione 16): i template
  coprono tutti e quindici i tipi D8 ma restano sorgenti propri, con le
  proprie chiavi i18n.
- **A23 — Figura non renderizzabile all'export = errore visibile** nei log
  (`figure_render_fallback`), sezione 8.5 e rischi residui.
- **A24 — Esito dello script di rivalidazione.** Nessun DB locale ha asset
  Mermaid reali (`a4u` 0 tabelle, `a4u_e2e` 2 lezioni senza asset,
  `a4u_test` vuoto). In consegna lo script gira su un dump fornito dal
  docente (ripristinato in `a4u_e2e`), altrimenti si dichiara la run
  sintetica. Richiesta al docente: un dump o un accesso in sola lettura.

## 12. Decisioni prese e alternative scartate

- **`<img data:svg>` contro SVG inline** per Vega-Lite, DOT e `function`
  nella dispensa. Scelto `<img>`: elemento sostituito, `max-height`
  rispettato da WeasyPrint, nessuna collisione di id fra figure, font
  risolti per famiglia da Pango; Mermaid resta inline per non toccare la
  catena byte-identica. Scartato l'inline per i formati nuovi: avrebbe
  richiesto il namespacing degli id (`clipPath`, marker) e la gestione del
  `width:100% !important` oggi applicato agli SVG Mermaid. Costo accettato:
  testo non selezionabile nelle figure `<img>`.
- **Slide senza numero** (A2). Alternativa scartata: numerazione locale al
  deck, che avrebbe prodotto due numerazioni diverse per lo stesso asset
  (dispensa e slide) e una numerazione mista fra asset di Fase 3 e
  `new_assets`.
- **Figure orfane in coda** (A12). Alternativa scartata: lasciarle
  invisibili come oggi; contraddice D4 e spreca asset già generati.
- **Niente pip `graphviz`** (A3): il wrapper non espone `timeout`; con il
  binario diretto un solo `subprocess.run` copre validazione e render.
- **sympy e vl-convert in sottoprocesso `spawn`** (A13). Scartati:
  `ProcessPoolExecutor` caldo cancellabile (`terminate_workers` non esiste
  in Python 3.12 e `Pool.terminate()` abbatte tutti i job), thread con
  `wait_for` (lascerebbe un thread orfano che gira per sempre sulla VM a 2
  core), `fork` (copia lo stato del worker uvicorn: loop, connessioni,
  thread).
- **`TextPath` per il mathtext** (A14). Scartato il ripiego dei font
  installati: il Dockerfile non ha fontconfig e il browser resterebbe
  comunque senza STIX/DejaVu Sans Display.
- **it/en con fallback it** (A4). Scartati: dizionario statico a 24 lingue
  della sola parola «Figura» (24 stringhe da controllare a mano; il resto
  del PDF resterebbe in italiano) e lookup nel DB delle traduzioni (seed
  senza chiavi `courses.*`: sempre vuoto in produzione).
- **Euristica del criterio 10** (sezione 3.2.2) con il falso negativo
  accettato su rette e polinomi con `*`. Scartato il riconoscimento
  «fuzzy» su nomi di campo o titoli: falsi positivi su grafici legittimi.
  Scartato il divieto totale di `data.sequence`: serve per assi e griglie
  illustrative.
- **Testo dei prompt statico, schema dinamico** (A19). Scartata la
  generazione condizionale del testo in base a `available_formats()`: le
  guardie di lunghezza dei test diventerebbero dipendenti dall'ambiente.
- **`courseRef` via context** (A21) invece del prop-drilling; scartato
  anche il passaggio di `orgId/courseId` dentro `content` (violerebbe il
  comparatore `memo` di `LessonContentView`).
- **`TEMPLATES` Mermaid non riusano i campioni D8** (A22): i campioni sono
  minimi per il test di `foreignObject`, i template dell'editor sono
  didattici. Le 8 chiavi i18n che allora avevano sconsigliato il riuso
  esistono dal completamento del catalogo (sezione 16), ma i due insiemi
  restano separati per ragione, non per costo.
- **Gate statico duro per Mermaid al PATCH** (A15) invece del parse JS:
  un Chromium per richiesta HTTP non è accettabile; il parse vive già
  nell'editor con la stessa major.
- **`Draft7Validator` dal file di altair senza `import altair`**: importare
  altair costerebbe tempo e memoria per un solo file JSON.
- **Cache negativa a 60 s** nel registro: senza, il fix loop ripeterebbe
  lo stesso render fallito a ogni tentativo.
- **`TableBlock`/`EquationBlock` restano a card**: tabelle ed equazioni non
  sono figure e hanno già uno stile coerente nel PDF (`figure.table`,
  `figure.equation`).
- **Vega-Lite e viz-js montano l'SVG nel DOM tramite ref** (nessun
  `dangerouslySetInnerHTML`, nessuna nuova direttiva `react/no-danger`);
  `FunctionFigure` usa `<img data:svg>`. L'SVG dell'anteprima passa da
  `sanitizeSvgElement` (`lib/figureFormats.ts`: elementi attivi e
  `<style>` rimossi, `<a>` sostituiti dai figli, `on*`, `href` esterni e
  attributi con `url(…)` non interno eliminati; `url(#id)` dei gradienti
  vega resta), difesa in profondità rispetto al gate del PATCH che resta
  autoritativo. `VegaLiteDiagram` passa a vega-embed un `loader` inerte
  (`load/sanitize/http/file` rifiutano): il loader di default eseguirebbe
  davvero il fetch di un `data.url` dal browser del docente durante
  l'anteprima dell'editor, prima che il gate del PATCH lo rifiuti.
- **Larghezza delle figure nel PDF dalla banda di leggibilità** (D10/D11,
  WP3a). Nuovo modulo leaf `figure_scale.py` (mirror
  `lib/figureFormats.ts`, fixture `figure_scale_cases.json`): bande
  8-11 pt per dispensa e web, 10-14 pt per slide e video;
  `fit_figure_width_mm` riempie il box con gli SVG fluidi e li riduce al
  tetto della banda, fa crescere gli `<img>` intrinseci solo fino al fondo
  della banda, non supera mai il box (larghezza per difetto al centesimo,
  invarianti property-based) e con banda irraggiungibile dà la larghezza
  del box e `in_band=False`. Box della dispensa da
  `_compute_template_margins_cm`: 168 mm (Mermaid, padding del wrapper) o
  170 mm (`<img>`) × `max_figure_height_cm·10` (242 mm); box delle slide
  dal budget di pagina (D12). La larghezza è l'ultimo attributo
  `style="width:Wmm"` del corpo della figura, mai dentro l'SVG (catena
  Mermaid byte-identica; per `classDiagram` ed `erDiagram`, non
  deterministici fra processi per REG-3, il confronto richiede un
  `Math.random` a seme fisso iniettato nella pagina di entrambe le catene,
  come ha fatto la verifica di WP3: 0 diversi su 15). Metriche accanto all'SVG: `RenderedFigure(svg,
  metrics)` da `render_figure_map` (`render_figure_batch` per Mermaid,
  misura in Chromium con `__renderMermaidMeasured` nella stessa
  `page.evaluate`; gli altri formati con `svg_base_font_px`), costante di
  formato (`FALLBACK_BASE_FONT_PX`, `font_source="constant"`) quando la
  lettura è irrisolta. Ogni fit è una `FigureFitEntry` del `fit_report`
  con `figure_fit`; `figure_fit_out_of_band`, `figure_font_fallback` e il
  summary `figure_fit_report` (fuori banda e `font_fallback`) alimentano
  il gate D13. Fuori banda noti (misura del 17 settembre 2026 sui 57
  modelli degli editor, tabella completa con i corpi in
  [09 § Larghezza delle figure](09-pdf-export.md)): in dispensa 6 su 57
  (DOT 2 su 18, Vega-Lite 0 su 24, Mermaid 4 su 15: gantt 3,72 pt,
  treemap 4,78, timeline 5,6, radar 6,08, network 7,17, pipeline 7,7),
  tutti diagrammi larghi fermati dalla larghezza di 168-170 mm; nelle
  slide 35 su 57 (DOT 1, Vega-Lite 22, Mermaid 12), 34 fermati
  dall'altezza di 86,6 mm della pagina asset-only (verticali come state
  104 × 380, o già alti a scala 1 come i Vega-Lite da 292 px con
  etichette a 8,25 pt, che per arrivare a 10 pt dovrebbero salire a
  93,6 mm) e gantt dalla larghezza; fuori dai modelli la `function` di
  prova esce a 8,52 pt. Tutti hanno `in_band=False` e restano nel
  `figure_fit_report` come input di D13, senza cambi al tema. Costo
  dichiarato della crescita degli `<img>` fino al fondo della banda: una
  figura più alta può anticipare un salto pagina nella dispensa. Contro
  `df7af78`, su 93 dispense (31 figure in 3 posizioni) il branch ha una
  pagina in meno in 24 casi e una in più in nessuno; in una scansione
  mirata di 80 dispense (5 figure in 16 posizioni) le due figure con
  testo a 4,5-5 pt (`dot_tiny`, blocco da 79,7 a 123,7 mm;
  `vl_mixed_small`, da 84,6 a 128,7 mm) aggiungono una pagina in 3
  posizioni ciascuna; crescita massima pinnata da
  `test_intrinsic_img_below_the_band_grows_at_most_to_the_band_floor` e
  `test_intrinsic_img_grows_at_most_to_the_band_floor`. Prova in
  `tests/test_figure_scale.py`, `tests/test_lesson_pdf_figure_text_size.py`
  (WeasyPrint `.render()`, corpo reale del testo per caso) e nel golden
  del percorso misurato di `tests/test_lesson_pdf_figures.py`.
- **Larghezza dei Mermaid a schermo dalla banda di leggibilità** (D10/D11;
  supera il «tetto d'altezza solo per i diagrammi orizzontali» del giro 2).
  `MermaidDiagram` misura nel DOM il corpo del testo più piccolo
  (`measureSvgFontPx`, stesso JS di `MEASURE_SVG_FONT_PX_JS` del
  pre-render), calcola con `fitFigureWidthMm` (mirror di
  `figure_scale.py`, senza box) la larghezza a cui quel testo cade fra 8 e
  11 pt e la applica come `width: min(100%, Wpx)` su un wrapper interno
  senza padding, con l'SVG a `width: 100%` dentro: il flowchart D8 passa
  dalla larghezza piena (18,3 pt in una colonna di 900 px) a 532 px e 11 pt,
  un verticale 300×1000 da scala 3 (31 pt) a 314 px, e in una colonna
  stretta l'SVG riempie il 100 %. Nessun tetto d'altezza, nessun
  `clientWidth`/`ResizeObserver` (vale 0 nei pannelli chiusi dell'editor):
  la colonna la applica il browser a ogni resize. Vega-Lite, DOT e
  `function` restano alla dimensione intrinseca (a scala 1 sono già in
  banda). Prova in `tests/test_frontend_figure_layout.py` (geometria in
  Chromium con il modulo vero, parità del JS di misura sui 15 tipi D8) e
  `tests/test_figure_scale.py` (fixture condivisa eseguita con Node).
- **Box della figura per pagina nelle slide e nei frame video** (D12,
  16 settembre 2026; supera il cap fisso `max-height: 80mm` del template
  slide). Nuovo modulo puro `slide_geometry.py`: `SlideGeometry` è il
  mirror delle costanti CSS di `lesson_slides_pdf.html.j2` (ogni campo
  cita la riga del template, un test a regex le tiene allineate);
  `page_figure_budget` calcola per ogni pagina RESA (dopo la decisione di
  split, che dipende solo dai bullet) il budget verticale di ogni blocco
  asset: 120 mm − tag (1,5 × 9 pt) − titolo (10,37 mm per riga) − prosa
  (6,65 mm per riga) − bullet (5,24 mm per riga + 2,5 mm) − margini degli
  asset − 3 mm di safety, diviso in parti uguali fra i blocchi; `image_box`
  sottrae la didascalia stimata sul testo reale (etichetta, didascalia,
  coda `function`) e arrotonda al decimo per difetto; le righe vengono da
  `estimate_lines`, limite superiore per classi di carattere (maiuscola
  0,72 em, minuscola 0,58, cifra 0,60, CJK 1,0, spazio 0,30; grassetto
  +0,04) calibrato `real ≤ stima ≤ real + 1` sui `LineBox` di WeasyPrint
  per Helvetica, Arial, Verdana, Noto Sans, Liberation Sans, DejaVu Sans
  (lo stimatore a media 0,5 em sottostimava i titoli in maiuscolo); i
  simboli larghi (`%`, `@`, `&`, `—`, `→`, `±`, `«`, …) usano l'avanzamento
  massimo misurato sui file dei font (secondo giro di verifica: un titolo
  di soli `@` e `%` era stimato su una riga contro tre). Limite dichiarato:
  le classi delle lettere sono medie prudenti, quindi una sequenza
  artificiale di sole `m`/`w`/`W` può restare sottostimata di una riga. Il
  blocco figura emette `style="--figure-w: 255.0mm; --figure-h: Hmm"` sul
  `<figure>` dopo `aria-label` (`_FIGURE_RE` intatta) e il fit D10 usa quel
  box (255 × H); il template legge il box con `var()` (`.figure-body {
  width: var(--figure-w) }`, `max-height: var(--figure-h)` su immagini e
  fallback; residui in mm: 14 loghi e 40 teorema), senza cambi
  tipografici. Equazioni ed esempi non ricevono box: la regola generica
  `.slide-asset svg, .slide-asset img` porta `max-height:
  var(--figure-h, 80mm)`, così gli SVG MathJax restano al cap di prima
  (la premessa «cap mai raggiunto» era falsa: un `aligned` di 8 righe con
  frazioni è alto 107 mm, una `pmatrix` di 20 righe 132 mm, e senza cap
  sbordavano o sparivano dalla pagina del video; correzione del secondo
  giro di verifica, oracolo e controprova in
  `test_slide_figure_geometry.py`). Pagina asset-only con titolo su una riga: 86,6 mm di
  immagine (era 80); slide dedicata di Fase 4 con prosa su 3 righe: 61,3
  mm senza taglio (con il cap sbordava di 12 mm con un SVG alto); 5
  bullet + figura nel video: 45,4 mm; pavimento 25 mm con
  `slide_figure_box_exhausted` (pagina impossibile: sborda per il testo);
  `slide_figure_box_shared` con più blocchi; didascalia al pavimento con
  budget non clampato → `slide_figure_caption_squeezed`. Il fallback
  `<pre>` è troncato in Python (`truncate_fallback_source`, «…»,
  `figure_fallback_truncated`) perché WeasyPrint ignora `max-height` sui
  blocchi frammentati dal fondo pagina (`layout/block.py`, non `position:
  absolute`). **Settimo giro: il fallback delle slide non va a capo**
  (deviazione dichiarata dal piano, che prevedeva la troncatura su una
  stima delle righe a capo). Il `<pre>` delle slide e dei frame video è in
  `white-space: pre` (la dispensa resta in `pre-wrap` nel flusso di
  pagina): una riga di sorgente è una riga resa e `overflow: hidden`
  taglia a destra le righe più larghe dei 255 mm del box. Il perché: la
  troncatura ha bisogno delle righe rese, e dal terzo al sesto giro ogni
  stima delle righe a capo in `pre-wrap` è stata battuta da un caso nuovo
  di Pango o di Chromium. Prima gli spazi collassati (una riga DOT da 176
  caratteri stimata 1 riga contro 2, `<pre>` fuori pagina di 55 mm), poi
  spazi e tab conservati con colonne da 0,605 em e token lunghi a `ceil(2
  · w / riga)`, U+2028 e U+2029 (a capo in Pango, non in Chromium), le
  righe più alte per lingua e per script, l'ASCII e i simboli larghi per
  lingua e accanto a un altro script (V6-2: dopo Hangul, kana o ebraico
  «—», «…», «‰» e i filetti escono dal font mono fino a 1,342 em) e infine
  le regole UAX #14 di Pango (V6-1: niente a capo prima di `) ] } ! ? , .
  : ; /` né dopo `( [ {`, anche con spazi in mezzo; «voce . . . .»
  ripetuto su una riga era stimato 25 righe e ne rendeva 46, con il
  `<pre>` fuori dal body di 55 mm e la didascalia sparita dal PDF). Ogni
  correzione aggiungeva una tabella di larghezze di un motore di testo;
  senza a capo il conteggio è esatto nei due motori e non dipende da font,
  larghezza o lingua. Il costo: nel solo fallback delle slide, cioè nel
  percorso d'errore di una figura non resa (log `figure_render_fallback`),
  le righe più larghe del box si leggono fino al bordo destro. A capo
  misurati su tutti i caratteri Cc, Cf, Zs, Zl e Zp e sull'intero BMP, nel
  container e in locale, e sui piani 1 e 2 nel container: `\n`, `\r\n` e
  `\r` in entrambi i motori, U+2028 e U+2029 solo in WeasyPrint, NEL, FF e
  VT in nessuno. La divergenza si risolve contando la riga come spezzata
  e riscrivendo CRLF, CR, U+2028 e U+2029 come `\n` nel testo del `<pre>`
  (`truncate_fallback_source`, anche quando tutto entra); i NUL, che il
  parser HTML scarta insieme all'a capo che li segue in testa al `<pre>`,
  sono tolti prima di contare. Da `slide_geometry` sono usciti lo stimatore mono
  (`estimate_lines(mono=True)`, `_mono_rows`, `_mono_char_em`), le tabelle
  di larghezza per lingua e per script, il profilo CJK (ja, ko e zh sono
  «altra») e `SlideGeometry.fallback_w_mm`; `estimate_lines` resta per
  titoli, prosa, bullet e didascalie con la calibrazione di prima. Restano
  le altezze del quinto e del sesto giro: 1,3 em per riga solo per le
  righe dell'insieme base (669 caratteri verificati nel font mono) con
  lingua neutra, 1,70 em (`fallback_tall_line_budget`) altrimenti, 1,83 em
  per mn-cn e per le righe con mongolo tradizionale, 1,76 per tcy, e
  dall'ottavo giro 2,46 em per le righe con prima run ideografica (sotto);
  il marcatore paga la riga alta; vi è neutra salvo greco, cirillico, ∏, ∑
  e ∫ (⇐, ⇒, ⇔ e ∅ erano esclusi solo per la larghezza e hanno righe da
  3,210 mm). In italiano la slide da 60 righe corte resta a 24 righe più
  «…», con ja/zh-cn/ko/hi scende a 18; le righe DOT da 176 caratteri, le
  parole brevi da 200 colonne e le righe con 170 spazi o 22 tab iniziali
  passano da 12 a 24 righe più «…». Nel container le slide dei casi V6-1
  (`lead_48`, `semi_48`, `b2_48`, `leaders`, `code_paren`) e V6-2
  (`he_sym_dense`, `ucas_permille`, `ko_box`, `ko_dash`) rendono in
  WeasyPrint le righe del testo troncato, senza righe sotto il clip, con
  didascalia e «…» visibili. **Ottavo giro: prima run ideografica**
  (V7-1). In WeasyPrint l'altezza della riga dipendeva anche dall'ordine
  degli script: a parità di contenuto, «分བོད» era alta 2,006 em in it e
  2,293 in bo, «x分བོད» 1,588 e 1,300. Il meccanismo è in Pango 1.56
  (`apply_baseline_shift`, uguale in 1.58): le run di font diversi si
  allineano sulla baseline dello script della prima run della riga, cioè
  del primo carattere con script reale (spazi, cifre, punteggiatura,
  emoji e PUA sono Common o Unknown e prendono lo script che segue).
  Con Han, Hangul, Hiragana, Katakana, Bopomofo, Tangut, Nüshu o Khitan
  HarfBuzz sceglie la baseline ideografica, che senza tabella BASE
  sintetizza dal discendente (Noto Serif Tibetan −1,068 contro −0,074 di
  Noto Sans CJK: la run tibetana sale di 0,994 em). WeasyPrint prende
  altezza e baseline della riga Pango e le allinea alla strut, quindi la
  riga vale 1,3 em + |c_strut − c_testo|, con c = (alto − basso)/2 dei
  rettangoli logici spostati. Con la baseline romana o sospesa non c'è
  spostamento: senza BASE HarfBuzz dà 0 e 0,6 em a ogni font, perché il
  suo ripiego sospeso confronta il tag OpenType con il codice ISO e non
  trova mai il carattere di riferimento, e le BASE del container (30
  facce Noto CJK) hanno gli stessi valori. Pango itemizza fra due a capo,
  quindi la prima run si decide dentro la riga e il costo non dipende
  dalle righe vicine (misurato: una riga di soli Common seguita da una
  riga cinese resta a 1,3 em). Correzione: `_ideographic_lead` trova il
  primo carattere che decide (`_IDEO_SCRIPT_RE`, blocchi che contengono
  tutti gli script ideografici di Unicode 15, 16 e 17; lettera con script
  reale fuori da `_NOT_REAL_LETTER_RE`, cioè modificatori, simboli
  letterali e alfanumerici matematici), e quelle righe valgono
  `fallback_ideo_line_budget` = 2,46 em (6,075 mm). Il limite è
  analitico: c_testo di un insieme di run sta fra i valori delle coppie
  (prima run, altra run), e su tutte le coppie delle 321 facce del
  container, con i valori BASE e i ripieghi di HarfBuzz 10.2, c_testo va
  da −0,0205 a 1,1405 (baseline ideografica) e da −0,015 a 0,654
  (romana e sospesa, cioè la banda già nota); con la strut di qualunque
  font (c da −0,015 a 0,654; misurate le 278 lingue di fontconfig: da
  0,0585 in bo e dz a 0,582 in mn-cn) la riga resta sotto 2,4555 em.
  Prova di carico nel container: 2.702 righe per lingua (prima run
  ideografica, anche dopo emoji, PUA, danda, tatweel, alfanumerici
  matematici, poi un carattere di ognuna delle 213 famiglie) in it, bo,
  dz, my, kn, ja, mn-cn, tcy e te, massimo 2,2935 em, nessuna riga oltre
  la stima. Costo: le righe che iniziano con un ideogramma, un kana o un
  Hangul valgono 2,46 em invece di 1,70; la slide asset-only in bo tiene
  12 di queste righe più l'intestazione e «…» (28 omesse su 41) contro
  17 con l'id ASCII in testa, e il caso zh-cn della fixture passa da 47 a
  51 righe omesse; le righe con un id ASCII in testa non cambiano.
  Limiti dichiarati nel docstring di `slide_geometry` (script con A−D
  fuori banda accanto a font all'altro estremo sulla stessa riga: oltre a
  Nastaliq, mongolo, Siddham e Myanmar Serif, i geroglifici egizi, A−D
  0,998, in un corso bo o dz valgono 1,74 em; immagini con altri font:
  in locale, con 2.613 facce macOS, la banda senza spostamenti arriva a
  2,36 em e il limite ideografico a 2,68). Il video riceve lo stesso HTML
  (`lesson_slides_video_render_service` invariato). Scartati: costante
  derivata dalla pagina asset-only (taglia le slide legacy con bullet nel
  video), split nel video (rompe 1 slide → 1 frame), stimatore a 0,5 em,
  budget fisso per la didascalia, `line-height` espliciti su tag e
  didascalia (cambierebbe la resa delle slide in DB), `ValueError` senza
  box (rompeva un test del partial), `width: 100%` sull'`<img>` Mermaid;
  al settimo giro, un altro stimatore `pre-wrap` con le regole UAX #14 e
  le larghezze dei simboli dopo altri script (il giro successivo avrebbe
  trovato il caso seguente). Prova in
  `tests/test_slide_figure_geometry.py` (fixture
  `slide_figure_box_cases.json`, calibrazione, mirror CSS con
  `white-space: pre` e `overflow: hidden`, 14 pagine rese da WeasyPrint
  senza sbordo, controprova con il cap 80 mm che sborda, frame Chromium
  con `_VIDEO_OVERRIDE_CSS`; corpus avversario di 71 casi in `pre_sources`
  (regole UAX #14 dei giri 6 e 7, tab, spazi, righe vuote, una riga da
  25.000 caratteri, JSON minificato, 200 righe corte, CJK, kana, Hangul,
  ebraico, arabo, devanagari, kannada, tibetano, mongolo, emoji,
  separatori e controlli, riga DOT reale, e dall'ottavo giro ideogrammi,
  katakana e Hangul in testa con tibetano, birmano, emoji e PUA, più il
  controllo con l'id ASCII in testa) in 13 lingue (my aggiunta
  all'ottavo giro): righe ESATTE nei `LineBox` di WeasyPrint e nelle
  righe di Chromium, altezza stimata non inferiore a quella resa, in
  WeasyPrint anche riga per riga, e nelle slide nessuno sbordo, nessuna
  riga più alta della sua stima, didascalia
  presente, «…» reso e visibile dove il sorgente è troncato, nessun
  inchiostro oltre il bordo destro del body nel PDF e nel frame video;
  controprove con la regola `pre-wrap` di prima (più di 40 righe e
  didascalia persa sui casi V6-1), senza `overflow: hidden` (testo oltre
  il bordo), con i separatori non contati e con il modello del settimo
  giro sulle righe con prima run ideografica (katakana in testa e
  birmano in bo: righe oltre la stima e ultima riga sotto il clip);
  classificatore della prima run contro gli script di GLib (la stessa
  tabella di Pango) su tutto Unicode; insieme base su righe da 1,3
  em con DejaVu Sans Mono e con la pila del template; riga base più alta
  emulata con STIXNonUni davanti a DejaVu Sans Mono, marcatore dentro il
  `<pre>` con `language="ja"` e fuori con il profilo neutro; slide di un
  corso in giapponese a 18 righe più «…»).
- **Corpo dei testi letto dal `<style>` quando la misura manca** (D10,
  correzione di WP3). Se la misura in Chromium del pre-render fallisce
  (`mermaid_font_measure_failed`, poi `mermaid_font_measure_missing`) o la
  mappa porta una stringa, `svg_normalize.svg_base_font_px` calcola il
  corpo di ogni `<text>`/`<tspan>` con una cascata CSS minima: scansione
  dei nodi con la pila degli elementi aperti, regole del `<style>` che
  dichiarano `font-size` con selettori `tipo`/`*`/`#id`/`.classe` uniti da
  discendente o `>`, specificità e ordine, `!important`, `style` in linea
  sopra il foglio e attributo di presentazione sotto, eredità dal padre,
  `em`/`%` sul padre; il «testo proprio» è quello di `__measureSvgFontPx`
  (un nodo di testo figlio diretto). Con la sola regola radice
  `#id{font-size}` il pie (17 px nelle classi `.slice` e `.legend text`),
  il radar (12, `.radarAxisLabel`) e il sequence (16, `style` del `<text>`
  che contiene i `tspan`) valevano 14: il pie in dispensa usciva a
  13,36 pt con `in_band=True` e senza `figure_fit_out_of_band`. Ora il
  ripiego coincide con la misura su tutti i 15 tipi D8 e sui 15 modelli
  dell'editor. Una dichiarazione di corpo fuori grammatica (selettori con
  attributi, `+`, `~`, `:not()`, regole annidate o dentro una at-rule,
  `@keyframes` compresa, shorthand `font:`) o un valore che dipende dal
  contesto (`rem`, parole chiave, `var()`, `calc()`, `em`/`%` senza un
  antenato che dichiari il corpo: nel DOM valgono il contenitore HTML o i
  16 px della radice di un `<img>`) rende la lettura `unresolved`, quindi
  costante di formato con `figure_font_fallback` per OGNI formato (non
  solo per Mermaid: l'`in_band` calcolato sulla costante è un'ipotesi) e
  la voce `font_fallback` del summary `figure_fit_report`.
  `ex` e `ch` dipendono dal font e valgono come limite inferiore (0,4 em
  del padre): bastano quando il testo non può essere il minimo (il titolo
  `font-size="4ex"` del timeline di Mermaid), altrimenti la lettura è
  `unresolved`. Le pseudo-classi dinamiche e gli pseudo-elementi non
  valgono mai. Prova in `tests/test_svg_normalize.py`, nei casi
  `svg_font` della fixture condivisa, in `tests/test_mermaid_prerender.py`
  (parità dal vivo sui 15 tipi) e in
  `tests/test_lesson_pdf_figure_text_size.py` (pie e radar sintetici resi
  da WeasyPrint, con il corpo vero come oracolo).
- **`.lesson-prose .figure img { margin: 0 auto }`** invece del `margin: 0`
  scritto in Q2: la regola ha specificità (0,2,1) e annulla `mx-auto`
  (0,1,0) sull'`<img>` di `FunctionFigure` e del ramo `image`; con
  `margin: 0` le figure più strette della colonna finivano a sinistra sotto
  una didascalia centrata. `auto` orizzontale coincide con il partial del
  PDF (`.figure-svg { margin: 0 auto }`).
- **Lettera dopo il numero nel prefisso «Figura 2a.»**: `[a-z]` con
  IGNORECASE in Python accetta anche «ı» (U+0131) e «İ» (U+0130) per il
  case-mapping di `i`; il flag `iu` di JavaScript usa il simple case
  folding e non le piega. La copia frontend usa `[a-zıİ]` e la fixture
  condivisa fissa i due casi (parità eseguita con Node).
- **Placeholder dei formati legacy uguale su tutte le superfici**: nella
  vista lezione, nelle slide e nel PDF il corpo della figura legacy
  (`image_prompt`, `image_search_query`, `description`) mostra `content`
  (il prompt o la descrizione) dentro la cornice «Figura.»; nelle slide
  prima di WP5 mostrava `alt_text || caption || content`. La didascalia sta
  già nella `figcaption` e `alt_text` resta l'attributo di accessibilità:
  cambiamento di comportamento dichiarato, non una regressione di
  rendering (A11 riguarda i byte del PDF, non la vista).
- **Errori del parser client come codici, non frasi**
  (`FigureParseError` in `lib/figureFormats.ts`): `empty` e `not_object`
  sono tradotti dal componente (`courses.lessonsContent.render.figure.
  {emptySource,notAnObject}`), il messaggio nativo di `JSON.parse` o del
  renderer resta come dettaglio tecnico; nessuna frase hard-coded nei
  componenti nuovi (`tests/test_frontend_figure_i18n.py` lo verifica con
  un lessico, escludendo i template didattici in backtick).
- **Allowlist ruff dichiarata, non «miglioramento»** (A17): `pyproject.toml`
  tiene `allowed-confusables = ["−", "×", "–"]` perché le didascalie e le
  frasi calcolate usano i segni tipografici per scelta editoriale (minus,
  per, lineetta). I conteggi di `ruff check .` del repo vanno letti a
  parità di configurazione: alla chiusura del branch 373 violazioni con
  l'allowlist e 511 con `--config 'lint.allowed-confusables=[]'` (a
  `main` 432, allo stesso HEAD 8ce8160 394 / ≈468). Il numero senza
  allowlist cresce con i moduli nuovi che usano quei segni; quello con
  l'allowlist scende per le correzioni fatte nei file toccati. Nessuno dei
  due è raccontato come miglioramento della baseline (A6: la baseline
  rossa preesistente non è stata sanata, e i file toccati o nuovi sono
  puliti).
- **WP2a resta in due commit** (`bb17b49` + `b97ff74`): la correzione
  della verifica indipendente (palette nei riempimenti Mermaid,
  `latex_to_unicode` annidato) è un commit distinto e non è stata
  squashata, per lasciare bisecabile il difetto e la sua correzione;
  lo stesso vale per i commit `fix(figures): WPx — …` di WP2b, WP7, WP4 e
  WP5. Nessuno squash senza autorizzazione esplicita del docente.
- **Verifica meccanica di `PROMPTS.md`** invece del solo confronto a mano:
  `backend/scripts/check_prompts_md.py` rende i `_system_prompt(...)` con i
  segnaposto documentati e confronta i blocchi verbatim (PROMPT 3, 4, 5, 6,
  11, 12 con le varianti IT); scartata l'alternativa di un test pytest con
  skip se manca `docs/` — il confronto va eseguito quando i prompt
  cambiano, non a ogni run della suite, e uno script con exit code e diff
  è più leggibile in revisione. Due interpolazioni (durata in secondi del
  PROMPT 6, `lang_hint` del PROMPT 11) sono normalizzate esplicitamente
  nello script, con la regola scritta nella docstring.
- **Misura dell'immagine Docker sul build reale**, non sulla stima: due
  build di `backend/Dockerfile` (HEAD e un worktree temporaneo di `main`)
  con `docker image inspect --format '{{.Size}}'`; la stima a priori
  (≈57 MB di wheel compressi più apt `graphviz`) resta nel documento come
  termine di confronto, non come esito.
- **Run sintetica dello script di rivalidazione** (A24): nessun dump con
  contenuti reali è stato fornito; la run di consegna usa un corso di
  prova costruito con `course_builders` su `a4u_test` con i casi del gate
  statico e del render (sezione 14.3). Non si dichiara alcun esito sui
  contenuti di produzione: la richiesta di un dump o di un accesso in
  sola lettura resta aperta.
- **Prove residue eseguite nel container** con la stessa immagine
  misurata (probe montata in `/tmp`), invece che dedotte dai wheel: metriche
  dei font di vl-convert, `spawn` sotto uvicorn su Linux, fontconfig per
  WeasyPrint (sezione 14.3).
- **Gate editoriale dei grafi sul sorgente** (WP5, D13;
  `figure_compute/graph_rules.py`, gemello formale di `vegalite_rules`):
  costanti pubbliche `MAX_GRAPH_NODES` 30, `MAX_GRAPH_EDGES` 45,
  `MAX_LABEL_CHARS` 64, `MAX_TITLE_CHARS` 110, `MAX_MERMAID_SOURCE_CHARS`
  3.000, `MAX_GRAPH_LINES` 120, `MAX_EDGE_CROSSINGS` 4 e
  `check_graph_rules(kind, source, *, metrics=None) -> list[str]`. Soglie
  calibrate sui 57 modelli degli editor (massimi osservati 9 nodi, 8
  archi, 31 caratteri di etichetta, 55 di titolo, 29 righe, 388 caratteri
  Mermaid, 1 incrocio) e **confermate il 18 settembre 2026** sull'export
  del docente con `scripts/measure_asset_refs.py --figures`, che rende
  ogni figura con il registro di produzione e stampa distribuzione,
  percentuale oltre soglia ed esito per metrica: su 20 grafi reali nessuna
  metrica oltre soglia e nessun p90 oltre il 60 %, quindi la regola «p90
  reale oltre il 60 % della soglia → la soglia si alza, mai si boccia il
  contenuto» non ha alzato nulla e resta per il prossimo dump.
  Conteggi per tipo sul sorgente (regole nel
  docstring del modulo; i conteggi DOT coincidono con i gruppi `node` ed
  `edge` dell'SVG reso sui 18 modelli e, dopo il giro 1 della verifica
  (V1-F5), anche sugli operandi sottografo e sui grafi `strict`: `a ->
  {b c}` vale 2 archi e non 1, una catena `{a b} -> {c d} -> e` vale 6,
  una coppia ripetuta in `strict` vale 1, così un «hub» da 15 × 15 nodi
  scritto con un solo operatore non passa più come un arco; il tetto di
  risorsa `DOT_MAX_EDGES` continua a contare gli operatori); lo xychart conta le categorie
  dell'asse x e non i punti della serie (una serie mensile di cinque anni
  è una figura normale), deviazione dichiarata dalla tabella 2(d) del
  piano. Etichetta e titolo hanno soglie distinte: con una soglia unica a
  64 il titolo di 55 caratteri dello xychart starebbe al limite, con una a
  110 un'etichetta di nodo da 100 caratteri passerebbe. Agganci: in
  `MermaidRenderer.validate` DOPO il gate statico (non in
  `_mermaid_gate_once`, che gira sulle due viste del sorgente) e in
  `DotRenderer.validate` DOPO `DOT_MAX_EDGES`, che resta il tetto di
  risorsa con il suo messaggio («troppi archi (601 > 600)», tipo
  `figure_invalid`). Il messaggio editoriale è `graph_too_dense: <cosa>
  <n> > <max> — <che cosa ridurre>`, più violazioni separate da `; ` e la
  clausola «qui semplificare è la correzione richiesta: mantieni tipo e
  significato» una sola volta in coda, perché il system prompt del fix AI
  (PROMPT 12, invariato) vieta di togliere contenuti; `graph_too_dense` è
  un prefisso tipizzato di `error_type_for` (il 422 del PATCH porta
  `type="graph_too_dense"`). Scartati: le soglie nello schema Pydantic
  (un errore sull'output AI scarta la lezione intera senza passare dal
  fix) e il gate dentro `_mermaid_gate_once` (due viste, due messaggi).
- **A1 con un tetto generale sugli asset generati e su quelli cambiati**:
  `VISUAL_ASSET_CONTENT_MAX_CHARS = 12_000` sul `content` degli asset
  visivi degli output AI (`GeneratedVisualAsset` in
  `LessonContentOutput.visual_assets`, lo stesso validatore
  `cap_visual_asset_content` su `LessonSlidesOutput.new_assets`,
  `value_error` sull'elemento) e, nel PATCH di Fase 3 e 4, sui soli
  asset con `(format, content)` cambiati
  (`validate_visual_assets_or_raise`, per ogni formato, `type`
  `figure_invalid`). È il più alto dei tetti per formato con i default
  (`figure_dot_max_chars` 12.000, `VEGALITE_MAX_CHARS` 4.000, soglia
  Mermaid 3.000): protegge il server da un sorgente arbitrariamente
  grande, le soglie per formato restano nei renderer dove il fix AI può
  ancora agire. Scartati: `max_length=3000` sul solo Mermaid (un
  validatore per formato nello schema, e sull'output AI un sorgente da
  3.001 caratteri rigenererebbe la lezione invece di passare dal fix); il
  `max_length` sul modello condiviso `LessonContentVisualAsset` /
  `LessonSlideNewAsset`, com'era nella prima stesura di WP5 (giro 1 della
  verifica, V1-F2): l'editor invia sempre tutti gli asset e il gate del
  CRUD rivalida di proposito solo quelli cambiati, quindi una lezione
  storica con un Mermaid da 14.470 caratteri (nessun tetto prima di WP5,
  nessun backfill) dava 422 `string_too_long` anche alla correzione di un
  refuso nell'introduzione. Un `FIGURE_DOT_MAX_CHARS` più alto di 12.000
  non ha effetto oltre il tetto (04-configuration).
- **Incroci arco × arco con lo stesso algoritmo in Python e in Chromium**
  (WP5, D14; `figure_geometry.py` e `mermaid_prerender.
  MEASURE_SVG_GEOMETRY_JS`): tracciati degli archi (`g.edge > path` per
  DOT, con il gruppo come unità; per Mermaid gli elementi con classe
  `edge-thickness-*`, `messageLine*`, `flowchart-link`, `transition`,
  `relation`, `relationshipLine` e i `path` di `g.edgePath`), campionati a
  passo 2 nel sistema della radice, intersezione con estremi inclusi e
  collineari esclusi, scarto a meno di 2 unità da un estremo di uno dei due
  tracciati, punti entro 3 unità fusi in un incrocio; `crossings` conta i
  punti, `crossing_pairs` le coppie incidenti (punto triplo: 1 e 3). Il
  confronto fra le coppie di segmenti passa da una griglia di 16 unità
  (passo adattivo sulle tele grandi dal giro 3, voce «Costo della misura
  limitato per costruzione») invece dell'O(n²) misurato in Fase A: stessa
  risposta, lavoro proporzionale alle coppie vicine. Parità provata sui 18 modelli DOT
  (`hashTable` 1, gli altri 0) e su undici SVG sintetici; sui flowchart
  sintetici da 40 e 150 archi il JS e Python danno 12 e 48. Il giro 1
  della verifica (V1-F4) ha trovato due casi fuori parità, assenti dai
  modelli: un tracciato con un `M` intermedio (il JS, che percorre il
  tracciato con `getPointAtLength`, contava il salto come segmento) e
  l'arco ellittico `A` (Python usava la corda). Ora il JS riconosce il
  salto (distanza fra due campioni oltre la lunghezza percorsa) e ne
  cerca il confine per bisezione, Python converte l'arco in cubiche di al
  più 90° (SVG 1.1, F.6.5); i tre casi sono nella controprova. Tetti:
  50.000 segmenti per figura e 150.000 per batch (residuo in
  `window.__measureBudget`, una pagina per batch), stimati dalla lunghezza
  PRIMA di campionare; oltre, `skipped` e warning `figure_measure_skipped`
  (in Python vale in più il tetto di lavoro del giro 2, voce più sotto).
  Il pre-render dei 15 modelli più i due flowchart sintetici (45.000
  segmenti il maggiore) ha impiegato 2,85 s contro il pavimento di 60 s.
  Esclusi per scelta i `<line>` del quadrant e del gantt (griglia e assi:
  le due mediane del quadrant si incrociano per costruzione), i link del
  sankey, le curve del radar e le serie dello xychart. Applicazione:
  diagnostica per entrambi i formati (voce seguente): oltre
  `MAX_EDGE_CROSSINGS` la voce `graph_too_dense: incroci fra archi …`
  entra fra i difetti della figura, con il consiglio nella sintassi del
  formato (`rank=same`/`rankdir` per DOT, ordine delle dichiarazioni e
  direzione `LR`/`TD` per Mermaid), e produce `figure_geometry_defects`;
  per DOT la misura avviene già in `validate(deep=True)` e la figura va in
  cache con la voce, per Mermaid nella pagina del pre-render. Soglia 4 e
  non 0: un modello ufficiale ne ha uno.
- **Gli incroci non rifiutano mai una figura** (giro 1 della verifica,
  V1-F1). La prima stesura di WP5 rifiutava in
  `DotRenderer.validate(deep=True)` oltre 4 incroci: un percettrone
  multistrato 3-3-2 (8 nodi, 15 archi) ne ha 8, un 3-4-2 16 (17 con
  Graphviz 2.42.4 nell'immagine `a4u-backend-figures`), un 4-5-3 45, K3,3
  7 anche con `rank=same`, un grafo casuale da 30 nodi e 45 archi 8. Il
  prompt manda a DOT reti e bipartiti, il fix AI non può togliere archi e
  il worker, a fix esauriti, rigenerava la lezione fino a
  `course_lesson_content_auto_retry_max`: la soglia effettiva dei grafi
  non alberi era quella degli incroci, contro il vincolo di non bocciare
  figure normali. Scelto: incroci come warning e voce del report in ogni
  percorso, rifiuti solo sulle soglie del sorgente. Scartati: una soglia
  relativa al minimo del disegno a strati (il numero minimo di incroci è
  NP-difficile in generale; per due strati completi vale C(m,2)·C(n,2),
  già 30 per K4,5, quindi ogni soglia assoluta boccia un MLP normale
  entro i 45 archi); una soglia molto più alta senza dati reali su cui
  tararla. La decisione di un rifiuto resta legata alla distribuzione
  degli incroci sul dump del docente (`measure_asset_refs.py --figures`,
  riga «incroci»).
- **Difetti di lettura DOT misurati in Python con un riquadro stimato**:
  i quattro controlli di `_DOT_GEOMETRIA_JS` (testo fuori dalla tela,
  fuori dal proprio nodo o cluster, etichette sovrapposte, arco che
  attraversa un'etichetta) girano su coordinate esplicite con larghezze di
  avanzamento di Noto Sans (tabella dei caratteri 32-126, accentate come
  la base, ideogrammi a un em; dal giro 2 anche Times-Roman e Courier,
  voce più sotto), ascent 0,9 em e descent 0,25 em (sotto il
  passo di riga di Graphviz, 1,2 em). Zero difetti sui 18 modelli e
  l'attraversamento del vecchio `layers` rilevato, come l'oracolo JS.
  Scartati un Chromium per ogni validazione DOT (un browser in più nel
  worker) e le metriche di matplotlib (DejaVu, non il font del tema). I
  difetti vanno nel report, non bocciano: il riquadro è una stima.
- **`measure(svg) -> GeometryReport`** come metodo facoltativo del
  protocollo, letto con `getattr` come `render_figure_batch`: il brief
  indicava `-> list[str]`, ma una lista di stringhe perde il conteggio
  degli incroci che serve a `RenderedFigure.metrics`; la lista è
  `report.defects`. `SvgMetrics` guadagna `crossings: int | None` e
  `defects: tuple[str, ...]` (tupla e non lista: il record vive nella
  cache LRU condivisa), `FigureFitEntry` gli stessi due campi,
  `figure_fit_report` le chiavi `geometry_defects` e `measure_skipped`.
  `DotRenderer` espone `render_figure` / `render_figure_batch`: la
  geometria è misurata una volta al primo render e servita dalla cache.
- **Tag ripetuti contati, non collassati** (WP5):
  `course_lesson_content_service._count_asset_refs` restituisce un
  `Counter` per `(kind, id minuscolo)` al posto dell'insieme di
  `_collect_asset_refs`; un tag presente più di una volta produce
  `lesson_content_duplicate_asset_refs` (warning, `duplicated={"FIG:a":
  3}`) senza far fallire la materializzazione, perché il PDF normalizza
  già le ripetizioni. Il corpus dei warning unused/dangling comprende
  anche `examples[].content` e `tables[].markdown`, con la nota che il PDF
  non sostituisce i tag in quei campi.
- **Regola di posizione dei tag nel prompt di Fase 3** (WP5, D17). Scelto:
  il blocco `POSIZIONE DEI TAG — REGOLA RIGIDA` sostituisce il paragrafo
  «Per ogni asset» di P3, nomina i quattro tag con il campo id del proprio
  array (la regola vale quindi per figure, tabelle, equazioni ed esempi) e
  chiede un tag per asset, da solo su una riga propria fra righe vuote,
  dopo il paragrafo che introduce l'asset; nel testo il richiamo a parole
  («come mostra la figura»), senza ripetere il tag, senza «Figura» davanti
  al tag, mai dentro codice, formule, `caption`, `key_takeaways`,
  `references`, `examples[].content` o `tables[].markdown` (negli ultimi
  due il PDF non sostituisce i tag: giro 1, V1-F6c); DIVIETI vieta la
  numerazione a mano e `REGENERATION_SUFFIX` chiede di riscrivere i tag
  ripetuti o dentro le frasi. La regola sulle didascalie resta una sola,
  in DIVIETI. È la forma che `asset_ref_normalize` tratta come ancora
  senza toccare la frase. Scartati: lasciare «referenziato almeno una
  volta» e affidarsi solo ai rimandi di WP1 (il modello continuerebbe a
  scrivere «come mostra [FIG:a]» e, peggio, «nelle figure [FIG:a]», che il
  plurale tiene fuori dalla guardia parola-etichetta e diventa «nelle
  figure Figura 1»); portare la regola anche in P4 (il
  margine della guardia di Fase 4 è di 300 caratteri e le slide non hanno
  prosa con tag); alzare `MAX_SYSTEM_P3`. La prima stesura costava 692
  caratteri in P3 e 144 nel suffisso: prompt + suffisso di una
  rigenerazione arrivava a 29.210 caratteri, oltre la guardia (giro 1,
  V1-F6b; prima di D17 era 28.374). Il testo compatto costa 373 caratteri
  in P3 (27.923 nella variante più lunga) e 72 nel suffisso: la
  rigenerazione sta a 28.819, e `test_p3_regeneration_variant_stays_under_guard`
  applica la guardia anche a lei. Costo accettato: la regola vale per le
  lezioni generate o rigenerate da ora; lo storico resta com'è (nessun
  backfill) e i rimandi di WP1 lo coprono.
- **`measure_asset_refs.py --figures` misura ogni figura** (giro 1,
  V1-F3). La prima stesura rendeva tutti i Mermaid dell'export in un solo
  batch: il tetto di 150.000 segmenti della pagina si esauriva e le figure
  successive restavano senza incroci, escluse in silenzio dalla
  distribuzione (8 flowchart K12 da 27.801 segmenti: tre senza misura).
  Ora i Mermaid vanno a gruppi di `MAX_BATCH_MEASURE_SEGMENTS //
  MAX_MEASURE_SEGMENTS` figure (3), che non esauriscono mai il residuo
  prima del tetto per figura, e sotto la tabella delle soglie la riga
  «incroci non misurati: k su n grafi resi» conta i salti per tetto di
  figura o misura fallita. Scartata una pagina per figura (un avvio di
  Chromium per figura, tre volte il tempo); scartato alzare il tetto di
  batch nello script (lo script non riprodurrebbe più il registro di
  produzione).
- **Tetto di lavoro della misura Python** (giro 2 della verifica, V2-F1).
  La misura DOT gira nello stesso thread del render, dentro i 20 s di
  `figure_render_timeout_seconds` del batch, e il suo costo segue le
  coppie candidate della griglia (0,4-0,6 µs l'una), non i segmenti: gli
  archi che convergono sugli stessi nodi le fanno crescere col quadrato.
  Un bipartito 16×16 (256 archi, sotto `DOT_MAX_EDGES`, 49.216 segmenti,
  sotto il tetto Chromium) ne ha 12,5 milioni e costava 7,4 s; una
  lezione storica con tre figure così e una banale perdeva tutte e
  quattro all'export (timeout del batch, cache negativa, fallback nel
  PDF, nelle slide e nei frame video), mentre sul codice prima di WP5 lo
  stesso batch durava 0,4 s. Il gate editoriale non lo vede: vale solo in
  validazione e sugli asset cambiati del PATCH, e non c'è backfill.
  Scelto: `measure_svg` conta il lavoro (coppie segmento × segmento,
  etichetta × etichetta, etichetta × segmento) PRIMA di ogni confronto e
  lo riporta in `GeometryReport.work`; oltre `MAX_MEASURE_WORK`
  (1.000.000) la misura è saltata con `figure_work_cap`, oltre il residuo
  del batch (`MAX_BATCH_MEASURE_WORK`, 2.000.000, tenuto da
  `DotRenderer.render_figure_batch` e passato a `measure(svg,
  work_left=…)`) con `batch_work_cap`; con il residuo esaurito non si
  parsa nemmeno l'SVG. Le sovrapposizioni fra etichette passano da una
  griglia invece della scansione per ascissa, quadratica per una colonna
  di etichette (a parità di risultato e di ordine: test contro la
  scansione su 300 riquadri casuali). Misure del 17 settembre: i 18
  modelli stanno sotto 1.200 coppie, il grafo più costoso entro le soglie
  editoriali (45 archi, etichette da 60 caratteri) a 727.443 (0,30 s); il
  bipartito 16×16 è saltato in 0,07 s e il batch di prima torna a 0,4 s
  con le quattro figure rese; un batch di sei figure da 727.443 coppie,
  tre bipartiti e una banale dura 1,5 s (due misurate, quattro saltate
  per il batch, tre per figura, la banale misurata). Tetto
  deterministico e non a tempo: lo stesso SVG ha lo stesso esito su ogni
  macchina; con i valori scelti la misura costa al più circa 1,2 s per
  batch sulla macchina della misura, entro il timeout anche su un server
  tre volte più lento. `measure_asset_refs.py --figures` rende i DOT a
  gruppi di `MAX_BATCH_MEASURE_WORK // MAX_MEASURE_WORK` (2), come i
  Mermaid. Scartati: la misura fuori dal `wait_for` del batch (l'export
  si allungherebbe di secondi per figura senza tetto); saltare la misura
  quando il sorgente supera le soglie editoriali (il costo dipende dal
  layout, non dal conteggio: 45 archi possono valere 0,3 s); un tetto di
  segmenti più basso (non distingue un albero lungo, economico, da un
  fascio denso).
- **Costo della misura limitato per costruzione** (giro 3 della verifica,
  V3-F1 e V3-N1). Il tetto di lavoro del giro 2 contava le coppie, ma la
  misura Python dei DOT enumerava PRIMA, e tre volte, tutte le celle da 16
  unità del riquadro stimato di ogni etichetta: un'etichetta enorme
  valeva zero coppie e milioni di celle. Misure a 3d71f85 in processi
  figli con tetto (17 settembre 2026): `validate(deep=True)` con
  `fontsize=4000` 1,8 s e 371 MB, con `fontsize=8000` 7,7 s e 1,3 GB, con
  trenta etichette a `fontsize=1500` 5,5 s e 968 MB; `fontsize=1000000`,
  `size="3000,3000!"` con un solo nodo e un arco scalato con etichetta
  oltre 2 GB in 4-6 s (processi fermati); all'export, con il timeout del
  batch ridotto a 5 s, `[BAD, GOOD]` perdeva entrambe le figure (la
  verifica: 20 s e 4,4 GB con il timeout di produzione). Lo stesso
  difetto, meno esposto, stava in altri punti: il riquadro delle forme
  dei nodi era campionato a passo 2 (un riquadro arrotondato da un
  milione di punti: 1,5 s e 613 MB per la sola forma); il raggruppamento
  confrontava ogni punto d'incrocio con i vicini già visti (cento archi
  neato per un punto solo: 2,6 s con 816.706 unità contate, sotto il
  tetto); nel JS i segmenti
  erano campionati in unità locali e poi scalati (un arco scalato 5000
  volte: «Map maximum size exceeded» dopo 8 s e 2,5 GB di Chromium) e un
  intervallo con due salti tracciava un segmento fantasma da un milione
  di unità (oltre 60 s). V3-N1: con `scale(10)` una gobba stretta tagliata
  due volte valeva 0 incroci in Chromium e 2 in Python.
  Scelto il limite per costruzione descritto nel docstring di
  `figure_geometry` («Costo limitato per costruzione»): tela uguale al
  viewBox (riquadro unione se manca); passo della griglia
  `max(16, (L + H) / (2 · (√MAX_GRID_CELLS − 2)))` con `MAX_GRID_CELLS`
  250.000, quindi al più 250.000 celle su qualunque tela e passo 16 sotto
  15.936 unità di semiperimetro (tutti i modelli); riquadri ritagliati
  alla tela, con indici relativi all'origine, e celle contate in
  aritmetica e sommate al lavoro prima di enumerarle; una sola griglia
  delle etichette per sovrapposizioni, lavoro e attraversamenti; coppie
  contate prima dei confronti e provate una volta sola, nella cella
  d'angolo delle due impronte (niente insieme dei visti: sul fascio la
  memoria scende da 187 a 75 MB); raggruppamento a piano (celle di lato
  appena sotto R/√2, coppie di celle vicine decise dai riquadri dei loro
  punti, confronti solo per le coppie incerte, costo contato prima;
  stesso risultato del riferimento quadratico su 300 insiemi casuali);
  riquadro esatto delle Bézier dalle radici della derivata; geometria non
  finita saltata con `geometry_out_of_range` (prima `OverflowError`,
  assorbito solo dal registro). `GeometryReport.spent` è il lavoro
  eseguito, che `DotRenderer.render_figure_batch` sottrae al residuo anche
  quando la misura è saltata (prima una figura saltata dopo aver
  costruito la griglia non pesava sul batch). Il JS segue lo stesso
  schema: passo locale `step / k` con `k` l'allungamento massimo della
  trasformazione (V3-N1, stesso conteggio e stessi segmenti sulla gobba),
  ogni salto di un intervallo trovato per bisezione e saltato, tela,
  passo, ritaglio, conteggi delle celle, delle coppie e del
  raggruppamento come in Python, tetti propri tarati sulla sua velocità
  (9-54 ns per unità contro 0,3-0,6 µs; `MAX_BROWSER_MEASURE_WORK`
  10.000.000 e `MAX_BATCH_BROWSER_MEASURE_WORK` 20.000.000, residuo in
  `window.__measureWorkBudget`; i modelli Mermaid valgono meno di 1.300
  unità, il bipartito 5×9 entro le soglie 0,34 milioni). Esiti: i sei
  casi e la forma enorme in 0,07-0,15 s e sotto 115 MB, entrambe le
  figure rese all'export; il fascio da cento archi misurato (un incrocio)
  in 0,29 s; stessi incroci di prima sui 37 DOT della taratura del giro 2
  (modelli, bipartiti, percettroni, catene, colonne); lavoro dei modelli
  sotto 1.600 unità (ora conta anche le celle), il grafo più costoso
  entro le soglie a 764.856 (0,24 s); l'arco scalato con etichetta
  saltato per lavoro in 0,04 s. `tests/test_figure_geometry_cost.py`
  ripete i casi in processi figli (sotto 2 s e 300 MB) e sul codice di
  3d71f85 fallisce. Scartati: un tetto sul corpo del font o su `size` nel
  gate (il costo segue l'area in unità della radice, raggiungibile anche
  con molte etichette medie o con la sola scala, e gli storici non
  passano dal gate); il ritaglio alla tela con passo fisso (una tela da
  216.000 × 66.725 unità ha 56 milioni di celle); il passo sulla sola
  area (colonne illimitate su una tela lunga e bassa); un tetto a tempo
  (esito diverso da macchina a macchina); `9 · Σ n²` come costo del
  raggruppamento di prima (saltava un fascio di 45 archi, entro le
  soglie, misurato in 0,17 s).
- **Etichette che Mermaid manda a capo da sé** (giro 2, V2-F2).
  `MAX_LABEL_CHARS` misurava la riga del sorgente anche dove Mermaid 11
  spezza il testo sulla larghezza: un evento di timeline da 85 caratteri,
  resa su più righe, andava al fix AI tre volte e la lezione tornava
  `pending` (rigenerazione). Taratura in Chromium con la versione
  pinnata, etichetta di 84-92 caratteri: vanno a capo il testo delle
  forme e dei collegamenti del flowchart (anche fra virgolette, markdown,
  `@{ label }`, in `TD` e `LR`), tutte le etichette dello state
  (descrizioni, alias, transizioni, note), i nodi della mindmap, sezioni,
  periodi ed eventi della timeline, testo delle relazioni e note del
  class, testo delle relazioni dell'ER, etichette dei blocchi della
  sequence (`loop`, `alt`, `else`, `opt`, `par`, `and`, `critical`,
  `option`, `break`); restano su una riga i titoli dei subgraph,
  block-beta, messaggi, note, alias e `box` della sequence, membri e nomi
  delle classi, attributi e nomi delle entità ER, treemap, gantt, pie,
  quadrant, radar, xychart, sankey e tutto il DOT. Scelto: nei contesti
  che vanno a capo la soglia vale per la PAROLA più lunga (una parola da
  65 caratteri non va a capo e resta un rifiuto), negli altri per la
  riga, come prima; il messaggio cita la parola. Il test
  `test_mermaid_really_wraps_only_the_exempted_contexts` rifà la taratura
  a ogni run: un aggiornamento di Mermaid che smettesse di andare a capo
  lo fa fallire. Scartati: togliere `MAX_LABEL_CHARS` in quei contesti
  (una parola lunga resta illeggibile); una soglia di riga più alta per
  tutti (un messaggio di sequence da 70 caratteri resta su una riga, lì
  la soglia è una scelta editoriale).
- **Archi DOT con `tooltip`** (giro 2, V2-F3). Con `tooltip`,
  `edgetooltip`, `labeltooltip` o `URL` Graphviz avvolge il tracciato in
  `<g id="a_edgeN"><a …>`: il genitore diretto non era più `g.edge` e né
  Python né il JS lo contavano (2 archi su 4 nel caso di prova, parità
  conservata ma conteggio incompleto). Ora entrambi risalgono gli
  involucri (`<a>` e `<g>` senza classe con id `a_…`) fino al gruppo; in
  Python lo stesso vale per testi e forme dei nodi e dei cluster. Il caso
  è nella controprova Chromium (4 archi, 1 incrocio).
- **Stima del testo per famiglia, con margine** (giro 2, V2-F4). Quando il
  sorgente dichiara un proprio blocco `node [...]` il tema non inietta
  `fontname` e Graphviz rende in `Times,serif`, come le etichette
  HTML-like; la stima con Noto Sans, più larga, dava falsi
  `text_outside_owner` (6,5 / 0,8 / 8,8 / 3,5 su un'entità ER in
  tabella, dove Chromium misura al più 0,9 reali). Scelto: la tabella
  segue il primo nome del `font-family` (Noto Sans o assente; Times-Roman
  AFM per `Times…`/`serif`, uguale al Times di Chromium carattere per
  carattere; 600 millesimi per `Courier…`/`monospace`; nessuna stima,
  quindi nessun controllo, per le altre famiglie) e il riquadro stimato è
  ristretto del 2 % della larghezza per lato (`ESTIMATE_SLACK`): assorbe
  crenatura, versione del font e sostituzione del font sulla macchina che
  rende (in locale, senza Noto Sans, Graphviz dimensiona i nodi su un
  altro font: falsi 5,0 sulle etichette da 60 caratteri). Scartati: una
  tolleranza assoluta più alta (inutile sulle etichette lunghe, troppo
  larga sulle corte); saltare i controlli per ogni testo fuori tema
  (Times è il caso comune).
- **`asset_id` nei log della validazione** (giro 2, V2-N2).
  `DotRenderer.validate(deep=True)` non riceve l'asset (firma del
  protocollo) e il warning `figure_geometry_defects` usciva con
  `asset_id` vuoto; all'export la figura arriva dalla cache e il warning
  non è riemesso. Ora `asset_validation_service` apre
  `figure_asset_context(<asset_id>)` attorno alla chiamata e il renderer
  legge il `ContextVar`, che `asyncio.to_thread` copia nel thread. Una
  chiamata diretta fuori dal servizio resta senza id. Scartato un
  parametro in più nel protocollo (i renderer esterni e i fake dei test
  cambierebbero).
- **Punti chiave e riferimenti: normalizzazione solo in scrittura, storico
  reso verbatim** (questione B5, decisione D18). Scelto: due coppie di
  `field_validator` in mode "after" su `LessonContentOutput` e
  `LessonContentUpdateInput` (`app/schemas/course_lesson_content.py`),
  helper privati `_clean_key_takeaways` / `_clean_references` sul modello
  di `_clean_argomenti` e `_clean_keywords` (trim, vuoti scartati, dedup
  `lower()` con ordine e grafia della prima occorrenza; references a
  parità di `source`); `content_raw` è normalizzato dall'AI alla
  generazione e dal docente al primo salvataggio dall'editor, che invia
  sempre entrambe le liste; le lezioni mai riaperte restano com'erano.
  `LessonContentUpdateInput.key_takeaways` passa da 10 a 12
  (`KEY_TAKEAWAYS_MAX`, domanda aperta 14); l'editor scarta le righe
  reference vuote prima dell'invio (domanda 13); il worker logga
  `lesson_content_key_takeaways_below_min` quando la dedup lascia meno di
  tre voci. Scartate: la **dedup in lettura** (render PDF,
  `LessonContentView`, editor: tre copie dello stesso algoritmo, una
  divergenza permanente fra ciò che l'editor mostra e ciò che il PDF
  stampa, e un normalizzatore che dovrebbe tollerare le references-stringa
  dei test); il **mode "before"** (con `['A', 'a', ' A ']` la lista scende
  sotto `min_length=3` e la lezione verrebbe rigenerata per intero per un
  difetto cosmetico); **`uniqueItems` nello schema strict OpenAI** (fuori
  dal sottoinsieme verificabile, non copre maiuscole né trim); il
  **backfill** (vincolo del committente); il **validatore solo
  sull'output AI** (il PATCH resterebbe una via di reintroduzione e lo
  storico non si normalizzerebbe mai); il **raise su `[]` nel PATCH**
  (l'editor invia `[]` quando il docente svuota l'elenco); il collasso
  degli spazi interni e della punteggiatura finale (D18 letterale). Costo
  accettato: lo storico duplicato resta visibile su PDF, web ed editor
  finché non viene rigenerato o salvato, e il docente vede tre voci prima
  del salvataggio e una dopo, senza messaggio.

- **Revisore figura ↔ testo** (WP6, D15). Scelto: una chiamata per
  figura valida, dopo il fix e prima della localizzazione, con il testo
  integrale della prima sezione che la cita invece di `caption or
  alt_text` (il contesto del fix, tagliato a 600 caratteri, non dice che
  cosa la figura deve mostrare); `coerente` predefinito; riscrittura
  accettata solo dopo la stessa validazione del fix e la misura di WP5 su
  originale e riscrittura lette dalla stessa `render_figure_map`; rifiuto
  senza rigenerazione della lezione; kill-switch con il pattern di
  `figure_vegalite_enabled` e default acceso su `gpt-4o-mini` (0,00063 USD
  per 3.000 + 300 token, come i gemelli fix e localizzazione). Controlli
  deterministici in più rispetto al piano: stesso tipo Mermaid (un
  flowchart riscritto in mindmap avrebbe 0 incroci per definizione) e
  nodi e archi non in aumento (la riscrittura riduce la densità, mai
  aggiunge); dal giro 1 della verifica anche tutti i nodi dell'originale
  per id e nessun nodo collegato lasciato isolato (senza, 4 nodi e 4
  archi riscritti come 2 nodi e 1 arco passavano). Scartata per i nodi
  una soglia fissa sugli archi: togliere archi ridondanti è il modo
  dichiarato di ridurre gli incroci, e una soglia alla metà avrebbe
  respinto l'abbinamento ricavato da un K4,4. Scartate: il revisore anche sulle figure invalide (è il
  compito del fix, e il suo PROMPT 12 vieta di togliere contenuti); una
  riscrittura accettata per sola validazione quando la misura manca
  (Chromium assente, tetto di lavoro): un grafo non misurato può essere
  peggiore; il confronto dei difetti per testo completo (il dettaglio
  cita le etichette, che una riscrittura legittima cambia: si confrontano
  i codici); il ricorso a `course_lesson_pdf_service` per il box del fit
  (importa WeasyPrint a livello di modulo: costanti pinnate da un test); la somma di `assets_cost_usd` dentro `cost_usd` (cambierebbe il
  significato storico del campo). La dashboard admin, lasciata fuori nel
  primo giro, somma `assets_cost_usd` nella fase `content` dal giro 1
  della verifica: senza, la spesa AI mostrata agli operatori era
  sottostimata; il file è stato allineato a `ruff format` nello stesso
  commit. Dal giro 2: la conservazione dei dati per i formati senza archi
  (`_DATA_GUARDS`), perché la sola validazione accettava una riscrittura
  Vega-Lite che riduceva `data.values` da quattro righe a una, o che ne
  sostituiva campi e valori, senza alcun segnale nei log; la guardia è sul
  numero di righe e sui campi dell'encoding (per `function` sulle
  espressioni e sul dominio) e non sull'identità delle righe, così
  correggere un valore sbagliato resta una riscrittura legittima.
  Sempre dal giro 2: l'usage di una chiamata pagata senza risultato
  usabile (200 con JSON troncato dal tetto dei token) viaggia con
  l'eccezione invece di sparire — 0,00285 USD per chiamata con 3.000 +
  4.000 token, moltiplicati per i tentativi e per le figure — e lo stesso
  vale per il gemello del fix, il cui costo esce con
  `AssetFixUnresolvedError` fino al log del worker; il tetto delle
  chiamate in volo (`FIGURE_REVIEW_MAX_PARALLEL`, semaforo per loop),
  perché senza di esso le chiamate simultanee erano figure × lezioni; le
  rese della revisione dichiarate speculative (`cache_failures=False`),
  perché un timeout del loro batch metteva in cache negativa (60 s) anche
  le chiavi delle figure originali di DOT, Vega-Lite e `function`, che un
  export avviato subito dopo avrebbe saltato. Scartata al giro 2: la
  contabilizzazione del costo di una lezione annullata o rigenerata (non
  c'è riga in `content_tokens` da scrivere e il cancel-check esiste per
  non toccare quella lezione: il costo resta nei log,
  `lesson_content_cancelled_post_assets` e
  `lesson_content_assets_cost_discarded`).
- **Rimando testuale in linea e ancora dopo il blocco** (B1, D1-D2-D4;
  branch `fix/asset-refs-math-figure-scale`). Scelto: la citazione in
  linea diventa «Figura N» e l'ancora è inserita una sola volta dopo il
  blocco della prima citazione. Scartata la promozione della prima
  citazione a blocco: con 0 asset su 17 citati due volte nelle dispense
  reali e senza backfill avrebbe lasciato la frase spezzata su tutto il
  contenuto esistente. Motivazioni complete e altre cinque alternative
  scartate nella sezione 20.2.
- **Etichette dei quattro kind sotto `courses.figures.*`** (B2, D3-D5).
  Scelto: 12 chiavi nuove per lingua sotto l'unico prefisso già
  presidiato, composizione in un solo punto (`asset_label`/`asset_ref`),
  ramo teorema sul contatore delle equazioni. Scartati i sottoalberi
  separati, il rimando derivato a runtime, `theorem.labelUnnumbered` e lo
  strip del prefisso numerico (lossy): sezione 20.2.
- **Una sola grammatica per il math del PDF** (B3, D6-D8). Scelto: le rule
  di markdown-it, condivise da renderer e collector, con la guardia
  anti-currency come core rule. Scartati le due grammatiche allineate a
  mano, il pre-processing testuale fence-aware e il renderer inline
  commonmark: sezione 20.2.
- **Autoescape e math nella prosa di slide e discorso** (D19). Scelto:
  `select_autoescape(enabled_extensions=("html","xml","j2"))` sui tre env,
  filtro `css_string` per i `tpl.*` in contesto CSS, escape HTML
  dell'attributo (non `|safe`) per gli URL dei loghi, prosa e titoli dal
  collector math. Contesto e deviazioni nella
  sezione 20.5.

## 13. Rischi residui

- Mermaid resta su CDN a runtime (validatore e pre-render): offline degrada
  come oggi (pass-through nel validatore, fallback nel PDF); i tre formati
  nuovi sono offline e non degradano. Bundle locale di Mermaid: lavoro
  futuro. Il batch Mermaid di `render_figure_map` ha un tetto proprio di
  almeno 60 s (sezione 2.2): con una CDN lentissima l'export attende fino
  a un minuto prima del fallback, e un timeout del batch non entra in
  cache negativa (l'export successivo ritenta).
- Diagrammi v10 già in DB che non parsano in v11, o di tipo escluso,
  finiscono nell'elenco «da correggere» dello script L5; il gate statico
  li blocca solo alla rigenerazione o alla modifica di quel singolo asset,
  mai all'edit del testo (A15).
- Cambiamenti visibili sui contenuti esistenti: card rimossa e «Figura N.»
  (D4), figure orfane rese in coda (A12), caption già prefissate ripulite
  a render, box della pagina (D12) anche per le immagini caricate nelle
  slide (prima tagliate da `overflow: hidden` oltre 80 mm).
- **Le immagini markdown esterne del corpo della dispensa non si vedono
  più nel browser** (SEC-1, giro 7, dichiarato al giro 8):
  `MarkdownRenderer` rende `![](http://…)` come `<img src>` senza
  riscrivere l'URL, e la politica `img-src` della pagina la rifiuta —
  misurato con il pacchetto vero (immagine in errore, violazione in
  console, GET assente dalla spia; senza header, caricata). È la stessa
  classe di richiesta dell'`<image>` di Mermaid e il blocco è voluto, ma
  vale su contenuti già in DB e non c'è alcun avviso nell'editor. Se
  servisse ammetterle, la strada è un `urlTransform` che riscriva gli URL
  esterni (o li porti sullo storage), non un allargamento della politica.
  Nel PDF restano invece scaricate: è la via del markdown, sotto.
- L'endpoint `render-function` e l'export condividono il semaforo a 2 su
  una VM a 2 core: un picco di anteprime rallenta gli export. La
  validazione profonda dei worker resta fuori dal semaforo (sezione 2.2,
  Fase D COR-5): il tetto reale del CPU-bound è ~2 + 3 + 3 attività, che
  costano memoria (interpreti `spawn` con sympy o vl-convert caricati) più
  che tempo.
- Il rate limit di 30/min dell'endpoint è **per valore di
  `X-Forwarded-For`**, non per IP reale: uvicorn parte con
  `--forwarded-allow-ips=*` e prende il valore leftmost, che il client
  controlla. È infrastruttura preesistente e identica su `main` (dove
  governa anche il limite anti-brute-force del login), non introdotta da
  questo ramo, e non è il cancello del carico — l'endpoint richiede
  autenticazione e `course:edit`, e il tetto del CPU-bound è il semaforo.
  Hardening da fare fuori da questo perimetro: restringere
  `--forwarded-allow-ips` alla subnet del reverse proxy, o affiancare una
  chiave per utente/organizzazione (Fase D, SEC-3).
- **La Content-Security-Policy non c'è in sviluppo** (SEC-1, giro 7). La
  politica `img-src` sta in `frontend/nginx.conf` e vale per la pagina
  servita dall'immagine Docker; con `npm run dev` la pagina la serve Vite,
  che non ha quell'header, quindi in sviluppo il residuo del render
  client-side resta aperto: un diagramma con una shape `img:` scritto
  nell'editor fa partire la GET dal browser di chi lo scrive. Restano le
  altre difese (il gate, la guardia di rete del server, la scansione
  dell'SVG). Portarla anche in sviluppo significa aggiungere l'header alla
  configurazione del server di Vite: lavoro separato, con il rischio di
  divergenza fra i due punti in cui la politica sarebbe scritta.
- **Risorse esterne nel PDF per vie che non sono le figure.** Le figure ora
  rifiutano URL e file: il gate DOT, le regole D5 di Vega-Lite,
  `normalize_svg` e — per Mermaid, esente da A11 — il gate delle shape
  `@{ … }` e degli statement (euristica di difesa in profondità, sezione
  15) più la
  scansione dell'SVG, che è la difesa vera sul documento consegnato e dal
  giro 5 rifiuta anche l'`<a href>` esterno (sezione 3.1). Resta aperta, e
  preesistente al ramo, la via del **markdown della dispensa**:
  `MarkdownIt("commonmark", {"html": True})` lascia passare
  `![](http://…)` e `<img src="file:///…">`, e WeasyPrint scarica e
  incorpora. Lavoro separato: un `url_fetcher` di WeasyPrint che ammetta i
  soli `data:` per tutti i PDF (Fase D, SEC-1).
- Le 22 lingue non it/en ricevono etichette e frasi in italiano nel PDF
  (come il resto del documento) e nel frontend finché l'amministratore non
  lancia l'auto-translate.
- La CI non ha Chromium: il test D8 e il pre-render sono verifiche locali o
  in Docker prima del merge; in CI saltano con motivo esplicito.
- **Figura non renderizzabile all'export** (A23): un SVG rifiutato, un
  timeout del batch o `dot` assente producono il fallback `<pre
  class="figure-fallback">` nel PDF e nei frame video, con
  `log.error("figure_render_fallback", …)`. Il documento viene comunque
  prodotto: l'errore è visibile nei log, non al docente, finché non apre il
  PDF. Mitigazione: la validazione profonda nel worker rende l'SVG e lo
  mette in cache prima dell'export; la cache negativa evita tentativi
  ripetuti; lo script di rivalidazione elenca gli asset problematici.
- Metriche dei font di vl-convert nel container: se le larghezze delle
  label divergono da Noto Sans, `register_font_directory("/usr/share/fonts")`
  (verifica residua, voce 24 del «Delta»).
- `spawn` sotto uvicorn su Linux: verificato nel container in WP6
  (sezione 14.3); resta la dipendenza dal costo fisso dello spawn
  (~0,2 s per chiamata nel container) che il debounce dell'editor e la
  cache per hash attenuano.
- Localizzazione degli asset nella duplicazione in altra lingua (doc 15):
  i campi testuali di Vega-Lite/DOT/`function` seguono `extract_translatable`,
  ma il TODO tracciato in `15-course-duplication.md` resta.
- Il testo delle figure `<img>` non è selezionabile nel PDF (Q3).
- `[FIG:]` dentro esempi e tabelle (`ExampleBlock` usa `ReactMarkdown`
  direttamente) non è risolvibile né numerabile: limite dichiarato.
- Coda della didascalia di `function` (D9): dipende dalla cache dei
  risultati del motore, con traffico diverso da quella degli SVG (sezione
  6.3). Dopo la correzione WP4 un hit incompleto viene ricalcolato; il
  caso residuo (eviction fra pre-render e composizione dell'HTML) produce
  `log.warning("figure_caption_missing", …)` e una didascalia senza coda,
  mai silenziosa. Un cambio di lingua del corso fra due export non è un
  problema: la coda è composta a render nella lingua richiesta.
- **Soglie editoriali non ancora confermate su figure reali** (WP5): sono
  calibrate sui modelli degli editor, didattici e piccoli per costruzione.
  Un contenuto reale più denso ma leggibile verrebbe rifiutato e, a fix
  esauriti, la lezione rigenerata per intero. Mitigazione: margine ≥ 1,4×
  sui massimi dei modelli, test di calibrazione (massimo dei modelli sotto
  il 60 % di ogni soglia) e `measure_asset_refs.py --figures` da eseguire
  sul dump del docente prima di considerarle definitive. Nei contesti che
  Mermaid manda a capo `MAX_LABEL_CHARS` vale per la parola (giro 2): se
  una versione futura di Mermaid smettesse di andare a capo, il test di
  taratura in Chromium fallisce.
- **Clausola di semplificazione solo nel messaggio**: il PROMPT 12 del fix
  AI dice ancora «correggi solo la sintassi, non togliere contenuti»; il
  messaggio `graph_too_dense` dichiara l'eccezione, ma un modello che
  segue il system prompt alla lettera non riduce la figura e l'asset resta
  invalido e la lezione è rigenerata. Da osservare nel log
  `lesson_content_auto_retry` (`phase="asset_validation"`, `error` con
  «graph_too_dense») e, a tentativi esauriti, in `content_error` e
  nell'audit `course.lesson.content.failed`;
  il revisore di WP6 non interviene qui (vede solo figure già valide): la
  via di correzione resta una riga nel PROMPT 12.
- **Incroci DOT dipendenti dal layout dell'ambiente**: il conteggio è
  esatto sull'SVG reso, ma il layout cambia con la versione di Graphviz e
  con i font. Misurato il 17 settembre 2026: `hashTable` ha 1 incrocio e
  gli altri 17 modelli 0 sia in locale (Graphviz 15.1.1) sia
  nell'immagine `a4u-backend-figures` (Graphviz 2.42.4); il 3-4-2 ne ha 16
  e 17. Il test fissa di proposito `hashTable == 1`: un aggiornamento di
  Graphviz o dei font che sposta il layout lo fa fallire e va ricontrollato
  (la parità Python/Chromium sullo stesso SVG resta valida comunque).
- **Grafi densi di incroci salvabili** (Mermaid e DOT): gli incroci sono
  solo diagnostica, quindi una figura da 50 incroci con nodi e archi sotto
  soglia va in dispensa. Visibile in `figure_geometry_defects` e in
  `geometry_defects` del `figure_fit_report`; un rifiuto richiederebbe
  una soglia tarata sul dump reale e un fix capace di riordinare i nodi.
- **Asset storico oltre il tetto A1 salvabile solo invariato**: il PATCH
  non rivalida un asset con `(format, content)` identici al salvato, ma se
  il docente lo modifica (anche di un carattere) il tetto di 12.000
  caratteri, e per Mermaid la soglia editoriale di 3.000, lo rifiutano
  finché non è ridotto.
- **Operandi DOT per rimando**: `subgraph s` senza corpo (rimando a un
  sottografo già definito) vale zero nodi nel conteggio degli archi, dove
  Graphviz usa i nodi del sottografo; un sorgente che lo usa per un hub
  resta sotto il conteggio reale (lo cattura solo il tetto di risorsa sugli
  operatori).
- **Riquadro del testo stimato** nei difetti DOT: crenatura assente,
  sostituzioni di font del sistema e caratteri fuori tabella (larghezza
  media) possono produrre falsi difetti o mancarne; il margine del 2 % per
  lato ne assorbe una parte e un testo in una famiglia senza tabella
  (`Helvetica`, `Arial`, …) non è controllato affatto. Per questo i
  difetti sono solo diagnostica. Una chiamata diretta a
  `validate(deep=True)` fuori da `asset_validation_service` logga
  `figure_geometry_defects` con `asset_id` vuoto.
- **Punti ciechi della misura**: due incroci distinti a meno di 3 unità
  contano come uno; i flag di un arco ellittico scritti senza separatore
  (`A5 5 0 015 5`) interrompono il tracciato in Python; un salto fra
  sotto-tracciati quasi contigui, che non porta la distanza fra due
  campioni oltre la lunghezza percorsa (+1 %), non è riconosciuto dal JS,
  che traccia un segmento fantasma lungo al più un passo;
  i tipi Mermaid senza archi valgono 0 incroci per definizione; il JS
  salta la misura quando il residuo del batch è esaurito, quindi le ultime
  figure di una lezione molto densa possono restare senza conteggio
  (`measure_skipped` nel report; lo script `--figures` misura a gruppi e
  conta le misure mancanti). In Python, oltre un milione di unità di
  lavoro (celle, coppie candidate, controlli del raggruppamento) per
  figura o due milioni per batch DOT la misura è saltata
  (`figure_work_cap`, `batch_work_cap`), in Chromium oltre dieci e venti
  milioni: i grafi storici molto densi (oltre le soglie editoriali,
  nessun backfill) e le ultime figure di un batch pesante restano senza
  incroci, e una figura saltata per il batch resta così finché è in
  cache. Su una tela molto grande (testo enorme, `size="N,N!"`) il passo
  della griglia cresce e le celle si affollano: anche un grafo semplice
  può finire in `figure_work_cap` (l'arco scalato di prova vale 3,2
  milioni di unità). Una geometria non finita salta la misura
  (`geometry_out_of_range`) e un testo o una forma non finiti non sono
  controllati. Con una scala non uniforme il JS campiona con
  l'allungamento massimo e Python con la lunghezza vera del pezzo: i
  segmenti differiscono e i conteggi possono divergere (Graphviz scala in
  modo uniforme, Mermaid non scala). Un incrocio a distanza esattamente
  pari alla tolleranza degli estremi (2 unità) è deciso
  dall'arrotondamento, diverso nelle due misure (reticolo sintetico
  60 × 60: 3.596 in Python, 3.584 in Chromium, come prima del giro 3).
  Gli involucri dei collegamenti sono riconosciuti solo con la
  convenzione di Graphviz (`<a>`, `g#a_…`).
- **Punti chiave degradati a 1-2 voci** (D18): la dedup dello schema non
  fa rigenerare la lezione, quindi una dispensa può chiudere con meno dei
  tre punti chiave chiesti dal prompt. Visibile solo nel log
  `lesson_content_key_takeaways_below_min`; la correzione è una
  rigenerazione o un'aggiunta dall'editor.
- **Chiave di dedup letterale**: spazi interni diversi («Legge  di Ohm» e
  «Legge di Ohm»), punteggiatura finale, `ß`/`ss` (`lower()` e non
  `casefold()`) e caratteri invisibili come U+200B lasciano due voci
  distinte. Le references con lo stesso testo e `source` diversi restano
  entrambe per scelta.
- **Storico e copie del corso**: i duplicati scritti prima di WP5 restano
  finché la lezione non viene rigenerata o salvata dall'editor; la
  duplicazione e la traduzione del corso (`course_duplication_service`)
  copiano il dict senza schema, e due citazioni distinte possono
  coincidere dopo la traduzione senza essere deduplicate.
- **Revisore figura ↔ testo** (WP6): il giudizio di coerenza è del
  modello e nessun controllo deterministico lo verifica; i vincoli
  meccanici sono validazione, tipo Mermaid, nodi e archi non in aumento,
  nodi dell'originale conservati per id, nessun nodo collegato isolato,
  incroci e codici di difetto. Una riscrittura può quindi cambiare
  etichette o relazioni, o togliere archi, restando entro quei vincoli:
  visibile in `figure_review_verdict` (`outcome=accepted`, `reason`). La
  conservazione per id vale per i tipi che nominano i nodi; per mindmap,
  timeline, pie e simili si confronta solo il numero; per Vega-Lite e
  `function` la guardia dei dati conserva righe, campi, espressioni e
  dominio, **non i valori**: una riscrittura può cambiare i numeri di una
  colonna o le etichette di una curva restando entro il vincolo, perché
  correggere un valore sbagliato è una riscrittura legittima (il divieto
  di inventare dati assenti dal testo resta nel prompt). Un nodo
  il cui testo è l'id (DOT senza `label`, flowchart senza forma) si
  corregge solo aggiungendo un'etichetta. Il revisore
  non vede tabelle, esempi e formule della lezione, solo la sezione che
  cita la figura. Una figura giudicata incoerente e senza riscrittura
  accettabile resta com'è (`figure_review_rejected`). Le rese della
  revisione non scrivono in cache negativa (`cache_failures=False`):
  un loro guasto non toglie le figure all'export del minuto successivo,
  ma le riscritture respinte occupano posti della cache LRU degli SVG.
  Costo non registrato in `content_tokens`: le chiamate di una
  generazione poi annullata o rigenerata (restano nei log
  `lesson_content_cancelled_post_assets` e
  `lesson_content_assets_cost_discarded`); in Fase 4 l'usage di fix e
  localizzazione è solo loggato (`slides_assets_usage`).
- **La regola di posizione è un'istruzione, non un vincolo**: nessuno
  schema la impone. Un modello che ripete i tag lascia traccia in
  `lesson_content_duplicate_asset_refs` e il renderer tiene una sola
  ancora; «Figura [FIG:a]» scritto nonostante il divieto dà ancora «Figura
  Figura 1» (limite pinnato in WP1, da misurare sul dump).
- **Range di versione aperto delle dipendenze markdown**: `mdit-py-plugins`
  ha l'upper bound `<1` (`backend/pyproject.toml:34`) perché i nomi dei
  token dollarmath e la semantica di `allow_space`/`allow_digits` sono
  contratti impliciti del PDF, ma `markdown-it-py[plugins]` resta a
  `>=3.0.0` senza tetto. L'unico allarme sono i test strutturali
  (`test_all_four_dollarmath_rules_are_ours`,
  `test_math_grammar_flags_and_rulers_are_pinned`): un major che rinomina
  un token li fa fallire in suite, non in produzione. Chi aggiorna deve
  leggere quel rosso come un cambio di contratto, non come un test da
  adattare (sezione 20.6).
- **I font di produzione non sono nel container**: il default di
  `font_family` dei template è `"Roboto"` (`app/models/pdf_template.py:31`,
  `app/models/slide_template.py:42`) e lo stack ripiega su `"Inter"` e
  `"Helvetica"`, anch'essi assenti (in dispensa e nel discorso lo stack
  prosegue con `"DejaVu Sans"` e `"Liberation Sans"`, installati); l'immagine installa `fonts-dejavu-core`,
  `fonts-liberation`, `fonts-noto-core` e `fonts-noto-cjk`
  (`backend/Dockerfile:44`), nessuna delle due. La sostituzione è
  silenziosa: nessun errore, nessun log, e il PDF non ha la tipografia che
  il template dichiara. Le stime di `slide_geometry` sono calibrate sulle
  famiglie realmente presenti, quindi il modello geometrico regge; a
  cambiare sarebbe la resa se qualcuno installasse i font veri.
- **Buco di copertura della CI sui commit a un solo albero**:
  `.github/workflows/backend-ci.yml` gira sui path `backend/**`,
  `frontend-ci.yml` su `frontend/**`. Un commit che tocca **solo
  `frontend/`** non esegue la suite Python, cioè proprio i test che
  tengono allineati i mirror (parità Node delle fixture condivise,
  specchio delle chiavi i18n, inventario delle chiavi letterali); un
  commit che tocca **solo `docs/`** non esegue nessun workflow, quindi
  `check_prompts_md` non confronta `PROMPTS.md` con il codice. La rottura
  resta invisibile fino al primo commit che tocca `backend/`.

## 14. Verifiche e consegna

### 14.1 Test previsti per WP

| WP | Modulo di test | Cosa verifica |
|---|---|---|
| WP2a | `test_figure_theme.py` (committato) | chiavi i18n qualificate, fallback, `format_number`, alias `VisualAssetFormat`, parità con `figureTheme.ts`, 34 casi LaTeX → Unicode |
| WP1 | `test_mermaid_prerender.py`, `test_mermaid_theme_palette.py`, `test_mermaid_no_foreignobject.py`, `test_revalidate_mermaid_assets.py` (committati) | strip `max-width` byte-identico (L2), palette nei riempimenti, 0 `foreignObject` sui 15 campioni D8 (Playwright, skip senza Chromium o CDN), script L5 |
| WP2b | `test_figure_render_service.py` | Vega-Lite valida / `data.url` anche in layer annidato / oltre 4.000 char / `mark image` rifiutate; SVG senza `foreignObject`; **iniezione del tema** (`font-family` ⊇ «Noto Sans» e almeno un esadecimale della `PALETTE` negli SVG Vega-Lite e DOT); DOT valido / invalido (`image=` rifiutato); `dot` mancante (`monkeypatch` di `shutil.which` → `None`) → `(False, "dot_unavailable")`, `fixable=False`, mai pass-through; `skipif` solo per i casi che eseguono il binario; `run_isolated` con `tests/helpers/slow_target.py` e `timeout=1` → `FigureTimeoutError` |
| WP2b | `test_asset_validation_dispatch.py` | rimappatura `js_pos`, kind non-JS mai pass-through, `fixable`, grep che vieta nuovi `== "mermaid"` |
| WP2b | `test_svg_normalize.py`, `test_vegalite_rules.py` | prologo, rifiuti, px intrinseci, label «vedi url(x)» accettata; criterio 10 ereditato in `layer`/`vconcat`/`spec` |
| WP2b | test del filtro `_WeasyPrintSvgNoiseFilter` | record filtrato / non filtrato |
| WP7 | `test_function_figure_service.py` | `parse_expr` con `global_dict` ristretto (`importorskip("sympy")`), nessun `<image>`, font ⊆ `{Noto Sans, DejaVu Sans}`, timeout simbolico → `approximate`, HTTP 200/422/403 |
| WP4 | `test_figure_numbering.py`, `test_lesson_pdf_figures.py`, `test_figure_i18n_mirrors_frontend` | fixture condivisa BE/FE; figcaption in ordine di citazione, orfano in coda, `en`/`de`, strip del prefisso, escape della caption, fallback `<pre>`, golden byte-identico del blocco Mermaid/image/legacy nel wrapper; «Figura.» nelle slide; WeasyPrint 69 rende le label degli SVG v11 e degli `<img data:svg>` (`importorskip("weasyprint")`) |
| WP3 | `test_prompt_register.py`, `test_prompt_composition_bugs.py`, `test_prompt_figures.py` | misure reali, ordine dei marcatori, `_format_current_lesson_phase3` con `[format]`; blocco «FORMATI DELLE FIGURE» con tipi D8, regole D5, schema compatto D9 ed esempi minimi che superano i validatori reali |
| WP5 | `test_frontend_figure_i18n.py`, `test_frontend_figure_layout.py` | nessuna stringa hard-coded nei componenti nuovi, chiavi `t("…")` risolte in it/en; geometria misurata in Chromium (`margin: 0 auto`, tetto dei Mermaid solo orizzontali) e parità con Node delle copie TypeScript |
| WP6 | `scripts/check_prompts_md.py` (fuori dalla suite) | i blocchi verbatim di `docs/PROMPTS.md` identici ai `_system_prompt(...)` reali (9/9) |

Esito alla chiusura del branch (7 settembre 2026, macOS con Chromium, CDN,
`dot`, vl-convert, sympy, matplotlib, WeasyPrint e `../frontend`
disponibili): **877 test raccolti in 37 moduli, 877 passati, 0 falliti, 0
saltati** (`pytest -q -W ignore`, exit 0; i 20 test D8 e i 37 della palette
Mermaid eseguiti davvero). Comandi: `cd backend &&
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python3 -m pytest -q` con
Postgres attivo (`docker compose up -d postgres`); i test che richiedono
binari o rete saltano con motivo esplicito, mai falliscono. Vedi
[backend/11 — Tests](../backend/11-tests.md).

### 14.2 Checklist di smoke visuale del frontend

Procedura fissata in WP5 ed eseguita il 7 settembre 2026 (punto 5); gli
script e le immagini sono archiviati in `scratchpad/consegna/`
(`smoke_seed.py`, `smoke_playwright.py`, `smoke_run.log`, sette PNG) e
allegati alla consegna. Da ripetere a ogni modifica dei componenti delle
figure o dei dialog: è la verifica che i test di sorgente e di geometria
non coprono (resa reale delle quattro librerie nel browser).

1. backend avviato con `JWT_SECRET` (≥ 32 caratteri) e `DATABASE_URL` su
   `a4u_e2e`; `npm run dev` nel frontend;
2. una lezione con un asset per formato (`mermaid`, `vegalite`, `dot`,
   `function`, `image`) creata via PATCH;
3. screenshot Playwright Python (`page.screenshot(full_page=True)`) di
   `LessonContentView` con i quattro formati e del dialog di modifica,
   salvati in `scratchpad/consegna/`;
4. verifica a occhio: «Figura N.» in ordine di citazione, orfana in coda,
   nessuna card, palette e font del tema, box di errore controllato con
   `courses.figures.renderError` per un asset invalido;
5. esito WP5 (macOS, 7 settembre, rieseguito dopo le correzioni del giro
   2): backend su `:8001` (`FRONTEND_ORIGIN=http://localhost:5174`, perché
   `8000`/`5173` erano occupate da un altro progetto) e vite su `:5174` con
   un `vite.smoke.config.ts` temporaneo; seed di `a4u_e2e`
   (`scratchpad/consegna/smoke_seed.py`: org, docente `manager`, corso con
   una lezione `ready` che cita F1, F2, F3, F1, F6, F7, F3, F4) e Playwright
   Python (`scratchpad/consegna/smoke_playwright.py`): login dal form;
   `POST /lesson-assets/upload` di un PNG 480×300 → 201 (ri-encodato in
   `.jpg`); PATCH con una spec Vega-Lite senza `clip`/`scale.domain` → 422
   `lesson_content_invalid_visual_asset` con `meta.errors[0].loc =
   ["visual_assets", 1, "content"]`; PATCH con sette asset (`mermaid`
   flowchart LR, `vegalite`, `dot`, `function`, `mermaid` torta non citata,
   `mermaid` sequenceDiagram verticale, `image`) → 200; nella vista i
   cinque corpi pronti (`figure--mermaid svg`, `figure--vegalite svg`,
   `figure--dot svg`, `figure--function img`, `figure--image img`) e
   didascalie «Figura 1.» … «Figura 7.» in ordine di citazione (F1 e F3
   citate due volte con lo stesso numero, la coda calcolata di `function`
   «Zeri in x = −1, 1. … Asintoto obliquo y = x + 2.», l'orfana in coda
   alla sintesi prima dei punti chiave), nessuna card. Misure nella pagina
   (colonna di 898 px, `getScreenCTM`): sequence 650×907 senza tetto, scala
   1,38, testo 22,1 px; torta 524×450 con tetto 450 px, scala 1, testo 17
   px; flowchart LR 484×158 con tetto 448 px, scala 1,86, testo 26 px;
   `image` 480 px e `function` 499 px centrate (margini 217/217 e 207/207).
   Nel dialog i quattro editor con anteprima e badge di formato, e con una
   spec non parsabile il box controllato «Impossibile visualizzare la
   figura.» al posto dell'anteprima. Immagini:
   `scratchpad/consegna/lesson_content_view.png`,
   `lesson_content_view_sequence.png`, `lesson_content_view_image.png`,
   `lesson_content_edit.png`, `lesson_content_edit_assets.png`,
   `lesson_content_edit_function.png`, `lesson_content_edit_invalid.png`.

### 14.3 Misure e prove residue

- Dimensione dell'immagine Docker prima/dopo (`docker build -f
  backend/Dockerfile backend` su `main` e su HEAD, `docker image inspect
  --format '{{.Size}}'`); stima a priori: wheel ≈ 57 MB compressi più apt
  `graphviz`. Esito WP6 (7 settembre 2026, Docker Desktop 29.1.3, arm64,
  due build completi senza cache condivisa fra i due alberi: HEAD `2f1c58c`
  in 255 s, worktree temporaneo di `main` `40daf2f` in 232 s):
  `a4u-backend-main` **817.008.847 byte (817,0 MB)**,
  `a4u-backend-figures` **906.935.894 byte (906,9 MB)**, differenza
  **+89,9 MB** (+11 %). `docker images` riporta per le stesse immagini
  3,12 GB → 3,51 GB (dimensione espansa dei layer nello store containerd,
  +0,39 GB): i due numeri misurano cose diverse e vanno letti insieme al
  comando che li produce. Composizione misurata nel container
  (`du -sm` in `site-packages`, 502 MB → 723 MB): `vl_convert` 78 MB,
  `sympy` 74, `matplotlib` 37, `altair` 10, `mpmath` 5, `kiwisolver` 5,
  `contourpy` 2, `jsonschema` 2 (numpy e fontTools erano già in `main`);
  apt `graphviz` + `libgvc6` + `libcgraph6` + `libgd3` e le altre
  librerie trascinate ≈ 9 MB installati. La stima a priori (≈57 MB
  compressi + graphviz) era per difetto di circa 30 MB.
- Dimensione del bundle frontend prima (build pulito su HEAD prima di WP5)
  e dopo, con i kB dei chunk `vega`/`vega-lite`/`vega-embed`/`@viz-js/viz`
  (import dinamici). Base unica di misura: tabella di `vite build`, file
  `.js` e `.css` di `dist/assets` (senza `index.html` né sourcemap), kB
  minificati e gzip fra parentesi. Esito: prima di WP5 61 file, 6.333
  (1.772); WP5 (`e753c1e`/`87a30e0`) 72 file, 8.436 (2.550); dopo le
  correzioni del giro 2 72 file, 8.438 (2.551). `index.js` 3.057 (853) →
  3.095 (864); `mermaid.core.js` 678 (167) → 647 (158); chunk nuovi
  caricati solo a richiesta: `viz.js` 1.262 (485, il WebAssembly di
  Graphviz è inlinato in base64), `embed.js` 792 (276: vega + vega-lite +
  vega-embed), `step.js` 32, `time.js` 18, `figureTheme.js` 4,
  `VegaLiteDiagram.js`/`MermaidDiagram.js`/`DotDiagram.js` ≈ 1-2 ciascuno.
  Il bundle iniziale cresce di 38 kB (editor e cornice, senza librerie di
  render). Misura ripetuta in WP6 alla chiusura del branch (`npm run
  build` su `2f1c58c`, stessa base: file `.js` e `.css` di `dist/assets`,
  kB minificati e gzip): **72 file, 8.438 kB (2.551 kB gzip)**, `index.js`
  3.095 (864), `viz.js` 1.262 (485), `embed.js` 792 (276), `mermaid.core.js`
  647 (158) — identica alla misura del giro 2 di WP5 (WP6 non tocca il
  frontend). Rispetto al build pulito su HEAD prima di WP5 (61 file, 6.333
  kB / 1.772 gzip): +11 file, +2.105 kB (+779 gzip), di cui 2.054 kB nei
  due chunk caricati solo a richiesta.
- Esito di `backend/scripts/revalidate_mermaid_assets.py` sul dump del
  docente o run sintetica dichiarata (A24). Esito WP6 (7 settembre 2026):
  **run sintetica** — nessun dump con contenuti reali in
  `scratchpad/dump/`, quindi nessun esito sui contenuti di produzione
  (richiesta al docente ancora aperta). Script `scratchpad/consegna/
  revalidate_synthetic.py` su `a4u_test` (tabelle create, seed, corso di
  prova con `course_builders`, poi rimosse): 2 lezioni, 9 asset Mermaid (7
  in `content_raw`, 2 `new_assets` di slide; un `image` e un `vegalite`
  correttamente ignorati). `--skip-render --show-ok` in 0,5 s: 5 ok, 4 da
  correggere (`mermaid_init_directive` ×2 — frontmatter `config:` e
  direttiva `%%{initialize`, `mermaid_type_not_allowed:journey`,
  `mermaid_html_in_label`), 1 lezione con 1 asset non citato (la torta
  citata solo nei `key_takeaways`, che non contano come corpo). Con il
  render Mermaid 11 (Chromium + CDN, 4,4 s): 4 ok con SVG da 3,6 a 22,7
  kB e **0 `<foreignObject>`** (flowchart in fence sanificato, torta,
  alias legacy `graph`, `sequenceDiagram` delle slide), 5 da correggere
  (i 4 precedenti più `render_failed` per il flowchart con sintassi
  rotta); `--format csv` produce le sole righe da correggere. Output
  completo in `scratchpad/consegna/revalidate.txt`.
- Prova manuale di `spawn` sotto uvicorn. Esito WP2b (macOS, 7 settembre):
  app FastAPI minima servita da `uvicorn`, handler che chiama
  `asyncio.to_thread(run_isolated, …)` sui bersagli di
  `tests/helpers/slow_target.py` → `echo` ok, risultato da 2 MB ok,
  `sleep_forever` con `timeout=1` → `FigureTimeoutError`; 1,09 s per le tre
  chiamate. Sotto pytest (loop di sessione) i test di
  `test_figure_render_service.py` eseguono gli stessi bersagli. Esito WP6
  su Linux (immagine `a4u-backend-figures`, kernel 6.12 linuxkit aarch64,
  Python 3.12.14, `scratchpad/consegna/container_probe.py` montata in
  `/tmp` ed eseguita come utente `app`): app FastAPI minima servita da
  `uvicorn` in un thread, handler con `asyncio.to_thread(run_isolated,
  …)` → `echo` ok in 0,21 s, risultato da 2 MB ok in 0,21 s,
  `sleep_forever` con `timeout=1` → `FigureTimeoutError` in 1,02 s
  (macOS, stessa probe: 0,08 / 0,06 / 1,01 s). Il costo fisso dello
  `spawn` nel container è circa 0,2 s per chiamata.
- `parse_expr` con `global_dict` ristretto su sympy 1.14.0. Esito WP2b
  (7 settembre, prova manuale): registrato in sezione 4.2 — `__import__`
  e `open` → `NameError` (`Function` assente dal `global_dict`), `lambda`
  e accesso ad attributi eseguiti (fermati solo dal passo 1 sull'AST),
  simbolo non dichiarato colto da `free_symbols`, `2**1000000` →
  `ValueError` (4300 cifre). Il test `importorskip("sympy")` è di WP7.
- Metriche dei font di vl-convert nel container. Esito WP6 (stessa probe
  nel container e in locale, spec Vega-Lite con titolo lungo e titoli
  degli assi, `VEGALITE_THEME_CONFIG`): in entrambi l'SVG dichiara
  `font-family="Noto Sans"` su tutti i 18 `<text>` e le label lunghe
  («Andamento del valore osservato nel periodo 2018-2024», «Valore medio
  (unità)») sono rese intere. Nel container fontconfig risolve `Noto
  Sans` → `NotoSans-Regular.ttf` (`fonts-noto-core`), `DejaVu Sans` →
  `DejaVuSans.ttf`, `Noto Sans CJK JP` → `NotoSansCJK-Regular.ttc`, e
  vl-convert misura il testo con Noto Sans: larghezza 408 px, colonna
  delle etichette dell'asse y a 39 px. In locale (macOS senza Noto Sans
  installato: `fc-match "Noto Sans"` → Verdana) le stesse label sono
  misurate con il font di ripiego: 420 px e 51 px, cioè 12 px più larghe
  a parità di `font-family` nell'SVG. **Nessun
  `register_font_directory("/usr/share/fonts")` necessario** nel
  container; la differenza è di sola metrica sul portatile di sviluppo e
  non tocca il PDF prodotto in produzione.
- Screenshot: frame video di WP4 (`scratchpad/wp4_frame.png`),
  `LessonContentView` con i quattro formati (WP5). Esito WP4 (macOS, 7
  settembre): lezione di prova con un asset per formato (Mermaid via
  Chromium e CDN, Vega-Lite via vl-convert, DOT, `function`) più una figura
  di Fase 4 lasciata senza SVG; `render_svg_map` in 4,5 s; frame 1980×1400
  da `_screenshot_slides_sync` sull'HTML di `render_slides_html(enable_split=
  False)`: `scratchpad/wp4_frame.png` (slide `function` con la didascalia
  calcolata) e `scratchpad/wp4_frames/slide_001..005.png`; «Figura.» in
  grassetto senza numero, nessuna card, fallback `<pre>` con il sorgente
  nella quinta slide. Frontend: esito WP5 nella checklist 14.2
  (`scratchpad/consegna/lesson_content_view.png` con i quattro formati e
  l'orfana in coda; dialog di modifica in `lesson_content_edit*.png`).
- Resa di `<img src="data:image/svg+xml;base64,…">` in WeasyPrint 69
  (verifica residua del piano). Esito WP4 (macOS, 7 settembre): il testo
  degli SVG matplotlib (`svg.fonttype: none`), vl-convert e `dot` dentro
  l'`<img>` è estratto da pypdf dal PDF prodotto («etichetta», «ascissa»,
  «Volume (kt)», «Lemma»), come le label `<tspan>` dell'SVG Mermaid 11
  inline della fixture; nessun warning di WeasyPrint oltre il filtro del
  rumore SVG; «Figura 1.» … «Figura 4.» in ordine di citazione nel PDF
  della dispensa di prova (4 pagine). Test riproducibili in
  `tests/test_lesson_pdf_figures.py` (`weasyprint`/`pypdf`/`matplotlib`
  con skip esplicito, `dot` con `skipif`). Nel container Linux (WP6, stessa
  probe): matplotlib 3.11.1 emette `font-family: 'Noto Sans', 'DejaVu
  Sans', sans-serif` (guardia A14 rispettata, nessuna famiglia STIX),
  `dot` 2.42.4 (`/usr/bin/dot`, contro 15.1.1 in locale) produce lo stesso
  `font-family="Noto Sans"` e la validazione profonda passa; fontconfig
  risolve le tre famiglie del tema (punto precedente), quindi WeasyPrint
  trova gli stessi font che vl-convert e matplotlib dichiarano. La
  didascalia calcolata di `function` nel container è identica a quella
  locale («Zeri in x = −1, 1. Punti critici in x = 2 − √3, √3 + 2.
  Asintoto verticale x = 2. Asintoto obliquo y = x + 2.»). Le versioni di
  `dot` divergono (2.42 nel Debian dell'immagine, 15.1 in Homebrew): i
  test DOT asseriscono proprietà (font, colori, assenza di `<image>`),
  non byte.
- Verifica meccanica di `docs/PROMPTS.md` contro i `_system_prompt(...)`
  reali (`backend/scripts/check_prompts_md.py`, voce 21 del «Delta»).
  Esito WP6: **9/9 blocchi identici al codice** — PROMPT 3 dispense con
  grounding (24.222 caratteri), 4 verifica (1.710), 5 slide (13.837), 6
  discorso (11.156), 11 immagine → Mermaid (1.157), 12 fix Mermaid IT
  (1.361), Vega-Lite IT (1.387), DOT IT (1.029), `function` IT (1.144);
  nessuna correzione a `PROMPTS.md` necessaria (WP3 lo aveva già
  allineato). Prova negativa: su una copia del documento con una riga del
  PROMPT 11 alterata lo script esce con 1 e stampa il diff della riga
  (`scratchpad/consegna/check_prompts_md_negativo.txt`). Procedura in
  [backend/11 — Tests](../backend/11-tests.md).
- Gate di qualità alla chiusura del branch (7 settembre 2026): ruff pulito
  e formattato sui file toccati/nuovi; `ruff check .` del repo 373 con
  l'allowlist e 511 a parità di regole (A17; `main` 432, HEAD `8ce8160`
  394 / ≈468: la baseline preesistente non è stata sanata); `mypy app`
  205 errori (baseline 208 a `8ce8160`); pytest 877/877; `npm run lint` 4
  errori di baseline (`LatexEditor.tsx:283`, `i18n/scripts.ts` 64/66/68) e
  23 warning, nessuno nei file toccati; `npm run type-check` e `npm run
  build` verdi.

### 14.4 Revisione avversariale (Fase D)

Eseguita l'8 settembre 2026 su HEAD `4a1593a` (cinque revisori per
dimensione, un confutatore per ogni rilievo, un correttore). 33 rilievi
confermati dal confutatore: 14 maggiori (obbligatori) e 19 minori. Tutte
le correzioni stanno in un commit unico, `fix(figures): revisione
avversariale — …`, con il test che avrebbe colto il difetto; i rilievi non
corretti sono dichiarati in sezione 15.

| id | dimensione | severità | esito | commit |
| --- | --- | --- | --- | --- |
| COR-1 | correttezza | minore | corretto: `zip` non parallelo normalizzato in `render_svg_map` (da WP3 in `render_figure_map`), mai un'eccezione al worker | revisione avversariale |
| COR-2 | correttezza | maggiore | corretto: `validate(deep=True)` rifiuta l'espressione indefinita su tutto il dominio; il passo 1 rifiuta l'esponente costante non calcolabile | revisione avversariale |
| COR-3 | correttezza | maggiore | corretto: nodi contati come operandi, profondità = annidamento reale | revisione avversariale |
| COR-4 | correttezza | minore | corretto: `append_uncited_figure_refs` salta gli id che il token non rilegge (BE e copia `.ts`) | revisione avversariale |
| COR-5 | correttezza | minore | dichiarato come limite: `validate(deep=True)` dei worker resta fuori dal semaforo; sezioni 2.2, 10 e 13 corrette | revisione avversariale |
| COR-6 | correttezza | minore | dichiarato come limite: tetto unico per formato (Q1), accumulo su `function`/`dot` a cache fredda; sezioni 2.2 e 15 | revisione avversariale |
| COR-7 | correttezza | minore | corretto (documentazione): la `loc` del 422 si ferma a `content`, la `loc` per campo è dell'endpoint `render-function` | revisione avversariale |
| COR-8 | correttezza | minore | corretto: un `new_asset` con l'id di una figura delle Dispense non entra nel batch e non ne sostituisce l'SVG | revisione avversariale |
| REG-1 | regressione | maggiore | corretto (giro 1 + giro 3): `<br>` è un a capo di Mermaid, non HTML; il lookahead ricalca l'insieme MISURATO sul renderer (`<br/ >`, `<br / >` e `</br>` sono a capo e passano), non `lineBreakRegex`, che ne è un sottoinsieme. Il giro 2 aveva stretto il gate su quella regex, introducendo un falso positivo (sezione 14.6) | revisione avversariale (giro 3) |
| REG-2 | regressione | minore | dichiarato come limite: rinominare l'`asset_id` rende l'asset nuovo per il gate (sezione 2.3) | revisione avversariale |
| REG-3 | regressione | minore | dichiarato come limite: A11-L4 vale per 13 tipi su 15; `classDiagram` ed `erDiagram` non sono byte-deterministici, resa identica | revisione avversariale |
| REG-4 | regressione | minore | corretto: il sorgente svuotato dalla sanificazione non entra nel batch (niente Chromium a vuoto) | revisione avversariale |
| SEC-1 | sicurezza | maggiore | corretto in sette giri, con i limiti dichiarati. La correzione vera è la **Content-Security-Policy** della pagina dell'applicazione (`frontend/nginx.conf`, giro 7): `img-src 'self' data: blob: <origine upload>`, con l'origine derivata a build time da `VITE_UPLOADS_BASE_URL`, blocca la richiesta che `mermaid.render` fa partire attaccando l'SVG al documento — misurato con la pagina servita con quell'header, Mermaid 11.17.2, Chromium e un listener: 4 GET su 4 senza header, 0 con header, e nessuna immagine dell'app rotta. Il gate statico è dichiarato per quello che è — una euristica di difesa in profondità, aggirabile oggi con la 11.17.2 pinnata — e corretto per UNIONE, mai per scambio, controlli, segmentazioni e LETTURE del sorgente comprese: lista chiusa delle chiavi sul `metadata` che il lexer produce davvero, scansione `img:`/`icon:` sul blocco grezzo (giro 3, rimessa), schemi di URL, `stateDiagram` fra gli statement con URL (giro 5), le tre modalità di quotatura, il BOM e il `\r` (giro 6), la seconda lettura con `\r` come a capo e la riga `%%{…}%%` che non è un commento (giro 7). Le altre difese: isolamento di rete del pre-render (giro 2) e scansione dell'SVG estesa agli `<a href>` esterni (giro 5); la sanificazione lato client toglie il nodo dal markup ma non può precedere la prima richiesta. In sviluppo la politica non c'è (Vite non passa da nginx): dichiarato in sezione 15. La via del markdown resta dichiarata (sezione 13) | revisione avversariale (giri 5, 6 e 7) |
| SEC-2 | sicurezza | maggiore | corretto nel giro 2 (terzo vettore residuo dopo il giro 1): `\\` consumato come coppia, concatenazione con stringhe HTML e `#` trattato come commento fino a fine riga ovunque, come lo scanner di Graphviz | revisione avversariale (giro 2) |
| SEC-3 | sicurezza | minore | non corretto, motivato: `X-Forwarded-For` è infrastruttura preesistente identica a `main`, fuori perimetro; dichiarato in sezione 13 | revisione avversariale |
| I18N-1 | i18n | maggiore | corretto: estrai/applica simmetrici sulle label DOT, `\n` e `\l` intatti; regola aggiunta al prompt di localizzazione | revisione avversariale |
| I18N-2 | i18n | minore | dichiarato come limite: l'etichetta nella vista segue la lingua UI come le altre intestazioni del corpo (sezione 6.4) | revisione avversariale |
| I18N-3 | i18n | maggiore | corretto nel giro 2 (nel giro 1 era chiusa la sola aggravante del tetto D5): sostituzione chirurgica delle stringhe tradotte nel sorgente (`json_spans`), formattazione conservata e round-trip identità byte-identico per tutti e tre i formati | revisione avversariale (giro 2) |
| I18N-4 | i18n | minore | dichiarato come limite: «n.d.» è un ramo difensivo irraggiungibile dal motore, fissato da un test | revisione avversariale |
| I18N-5 | i18n | minore | dichiarato come limite: `illustrativeData` è un valore canonico condiviso, non una chiave letta (sezione 6.4) | revisione avversariale |
| I18N-6 | i18n | minore | corretto: le avvertenze del motore sono frasi localizzate it/en nell'anteprima dell'editor | revisione avversariale |
| I18N-7 | i18n | minore | dichiarato come limite: i messaggi del 422 restano italiani come tutti i `ValidationAppError`; il `type` è stabile (sezione 2.3) | revisione avversariale |
| I18N-8 | i18n | minore | corretto: banner dell'asset legacy e descrizione della fase nominano le quattro famiglie | revisione avversariale |
| I18N-9 | i18n | minore | non corretto, motivato: le tre voci sono preesistenti su `main` e fuori perimetro; il fallback di rete NON è italiano (è il messaggio inglese di axios) | revisione avversariale |
| TIP-1 | tipografia | maggiore | corretto: ripiego sans a schermo per Vega-Lite e DOT + webfont Noto Sans; il tema resta a una famiglia per non muovere la geometria | revisione avversariale |
| TIP-2 | tipografia | maggiore | corretto: superficie chiara fissa sotto ogni figura (`FIGURE_SURFACE`), Mermaid compreso | revisione avversariale |
| TIP-3 | tipografia | maggiore | corretto con un residuo dichiarato in sezione 15: testo sopra le curve con alone bianco (formula, coordinate esatte, etichette); una curva può ancora attraversare l'alone della formula | revisione avversariale |
| TIP-4 | tipografia | maggiore | corretto: nome dell'asse x sopra la freccia, mai fuso con il tick dell'estremo | revisione avversariale |
| TIP-5 | tipografia | maggiore | corretto: assi sopra i dati con riquadro bianco sui tick; etichetta esatta omessa se duplica il tick, altrimenti su una seconda riga | revisione avversariale |
| TIP-6 | tipografia | maggiore | corretto: `discreteWidth/Height` nel tema Vega-Lite (barre 419×269 invece di 147×318) | revisione avversariale |
| TIP-7 | tipografia | maggiore | corretto: `pieOpacity: "1"` nelle due copie del tema | revisione avversariale |
| TIP-8 | tipografia | minore | corretto: `axisX.labelAngle: 0` con `labelOverlap: "greedy"` | revisione avversariale |
| TIP-9 | tipografia | minore | corretto: punto fra didascalia e coda calcolata, nel partial e in `FigureFrame` | revisione avversariale |

Gate dopo le correzioni del giro 1 (8 settembre 2026): `ruff check` e
`ruff format --check` puliti sui file toccati (unica eccezione dichiarata:
`course_lesson_slides_pdf_service.py` non era formattato già a HEAD e non
è stato riformattato per non introdurre un diff estraneo); `ruff check .`
del repo **372** (era 373: un `E741` preesistente è caduto con la
correzione COR-8); `mypy app` **205** errori (invariato); pytest
**915/915** verdi (877 a HEAD, 38 test nuovi o estesi); `npm run lint` 4
errori di baseline e 23 warning, nessuno nei file toccati; `npm run
type-check` e `npm run build` verdi.

### 14.5 Secondo giro della revisione (verifica del giro 1)

Il verificatore finale del giro 1 non ha dato l'ok: quattro rilievi
dichiarati «corretto» erano più ottimistici del comportamento reale e la
correzione di SEC-1 aveva introdotto un falso positivo nuovo. Sei punti
riaperti, tutti chiusi con la riproduzione del verificatore rieseguita
alla lettera.

| punto | riproduzione del verificatore | esito del giro 2 |
| --- | --- | --- |
| SEC-1 | `A@{ label: "}", img: "http://127.0.0.1:8001/…" }`: gate `('', '')`, `validate` `(True, '')`, `mermaid.parse` verde, listener che registra la GET | corretto: `_mermaid_shape_blocks` chiude il blocco sulla `}` fuori dalle virgolette come lo stato `shapeDataStr` del lexer → gate `('mermaid_external_resource', 'img:')`, e il Chromium di pre-render non può più uscire dal CDN (`prerender_request_blocked`, listener a zero richieste) |
| SEC-2 | `digraph { # "⏎ x [image="<path>"] }` con file esistente e inesistente: gate `True`, `deep` distingue i due casi | corretto: `#` è commento fino a fine riga ovunque (come Graphviz 15.1.1) → gate `False` con lo STESSO messaggio nei due casi, nessun oracolo di esistenza |
| I18N-3 | `roundtrip_d7.py`: `vegalite` e `function` non byte-identici con traduzioni identità | corretto: sostituzione chirurgica nel sorgente (`json_spans`), round-trip byte-identico per tutti e tre i formati |
| TIP-3 | ritaglio `crop_area_formula.png`: la curva `x**2` attraversa l'alone di `f(x) = x²` | dichiarato come limite in sezione 15 (nessuna euristica di quadrante: il testo resta leggibile, la curva è interrotta dall'alone) |
| REG-1 | `<br/ >` accettato dal gate ma non riconosciuto da `lineBreakRegex` | lookahead ristretto a `/<br\s*\/?>/i` più una regola dedicata per le forme con spazio dopo la barra. Allineamento alla regex vero, ma la scelta era sbagliata: `lineBreakRegex` non è il criterio del renderer e il gate ha iniziato a rifiutare sorgenti sani. **Rifatto nel giro 3** (sezione 14.6) |
| regressione del giro 1 | `reg_atimport.py`: una label che cita `@import` o `url(https://…)` spariva dall'export | corretto: la scansione dell'SVG Mermaid guarda solo attributi e blocchi `<style>` (le stesse regioni di `svg_normalize`), le tre figure tornano a rendersi |

Gate finali dopo il giro 2 (8 settembre 2026): `ruff check` e `ruff format
--check` puliti sugli undici file toccati, senza eccezioni (il giro 2 non
tocca `course_lesson_slides_pdf_service.py`); `ruff check
.` del repo **372** (invariato); `mypy app` **205** errori (invariato,
215 sorgenti); pytest **954/954** verdi, zero saltati (**39 test in più**
rispetto ai 915 del giro 1: 13 per `json_spans`, 12 per l'isolamento di
rete, 9 casi del gate Mermaid e del gate DOT, 5 fra scanner delle shape,
scansione dell'SVG e round-trip della localizzazione); frontend non
toccato dal giro 2, gate rieseguiti a conferma (`npm run lint` 4 errori di
baseline e 23 warning, `type-check` e `build` verdi).

### 14.6 Terzo giro della revisione (verifica del giro 2)

Il verificatore del giro 2 ha confermato chiusi tutti e sei i punti
riaperti, ma non ha dato l'ok per una **regressione nuova introdotta dalla
correzione di REG-1**: stringendo il gate su `lineBreakRegex` il giro 2 ha
iniziato a rifiutare con 422 sorgenti che Mermaid rende correttamente.
Un solo punto, chiuso in questo giro.

| punto | riproduzione del verificatore | esito del giro 3 |
| --- | --- | --- |
| REG-1 (regressione del giro 2) | `reg1_br.py`: `flowchart LR\n A["uno<br/ >due"] --> B` reso con il pre-render di produzione dà due righe `uno`/`due`, SVG identico a quello di `<br>`; a `b35d26e` il gate dava `('', '')`, a `da5625d` `('mermaid_html_in_label', '<br/ >')` | corretto: il criterio del lookahead è l'insieme MISURATO sul renderer, non `lineBreakRegex`. Rieseguita la riproduzione, le quattro forme (`<br/ >`, `<br / >`, `<br  /  >`, `</br>`) tornano a `('', '')`; il gate resta identico a `da5625d` su 25 tag maligni e 9 sorgenti sani |

La causa era di metodo, non di regex: `lineBreakRegex` non è il criterio
del renderer. Mermaid dà la label al parser HTML del browser prima di
applicarla, quindi le forme `<br/ >`, `<br / >`, `<br  /  >`, `<br//>`,
`</br>`, `</br >`, `</br/>` (e le varianti maiuscole e con tabulazione)
arrivano a `lineBreakRegex` già normalizzate in `<br>` e sono a capo a
tutti gli effetti: venti forme in tutto, ognuna con un SVG identico a
quello di `<br>` a meno dell'id `mmd-N`. Il lookahead di `_HTML_TAG_RE`
ricalca ora quell'insieme (`</?[bB][rR][\s/]*>`) e la regola dedicata
`_MERMAID_BR_SPURIOUS_RE` del giro 2 è stata tolta. Il test che avrebbe
colto la regressione è un oracolo reale, non una regex:
`test_mermaid_br_forms_render_as_a_line_break` rende tutte e venti le
forme con il pre-render e confronta gli SVG; il gate è fissato sulla
stessa lista da `test_mermaid_gate_accepts_every_measured_line_break`, che
gira anche senza rete. La controprova (`<br class="x">` non è un a capo e
resta rifiutato) è in `test_mermaid_br_with_an_attribute_is_not_a_line_break`.
Corrette anche le quattro affermazioni che davano per non renderizzate
forme che il renderer rende (due righe del PR body, il commento della
regola tolta e il commento del test) e aggiunti in sezione 2.2 e 15 i tre
falsi negativi misurati del gate HTML.

Gate finali dopo il giro 3 (8 settembre 2026): `ruff check` e `ruff format
--check` puliti sui tre file toccati; `ruff check .` del repo **372**
(invariato); `mypy app` **205** errori in 32 file (invariato, 215
sorgenti); pytest **994/994** verdi, zero saltati (**40 test in più**
rispetto ai 954 del giro 2: 20 per il gate sull'insieme misurato, 20 per
l'oracolo di resa e 1 per la controprova, meno il caso `</br>` della
tabella dei rifiuti che era un falso positivo); frontend non toccato dal
giro 3.

### 14.7 Quarto giro della revisione (verifica del giro 3)

Il verificatore del giro 3 ha confermato chiusi i sei punti del dossier e
nessuna regressione, ma non ha dato l'ok: cercando varianti NUOVE del
bypass ha trovato un **residuo di SEC-1 preesistente** (vivo da `b35d26e`,
non introdotto dal giro 3) e una seconda via della stessa classe. Due
punti, entrambi chiusi in questo giro.

| punto | riproduzione del verificatore | esito del giro 4 |
| --- | --- | --- |
| SEC-1 (residuo, escape YAML) | `sec1_residuo_e2e.py`: `flowchart LR / A@{ "\x69mg": "\x68ttp://127.0.0.1:8001/E2E.png", w: 60, h: 60 } / A-->B` → gate `('', '')`, `validate` `(True, '')`, `mermaid.parse` `(True, '')`, `validate_visual_assets_or_raise` ACCETTA (l'asset si salva); con la guardia di rete disattivata parte la GET e l'SVG contiene `<image href="http://…">` | corretto: il gate delle shape è una lista CHIUSA di chiavi (`MERMAID_SHAPE_KEYS`), come quello del frontmatter. Rieseguita la riproduzione: gate `('mermaid_external_resource', '\x69mg:')`, `validate` `(False, …)`, PATCH **422**. Chiuse con la stessa correzione tutte e 13 le forme YAML della batteria (chiave/URL con escape esadecimale o unicode, virgolette singole, forma blocco, ancora/alias, chiave complessa `? img`, tag `!!str`, `<<`, `icon:`) |
| SEC-1 (seconda via, senza shape) | `sequenceDiagram / participant A / properties A: {"icon": "http://127.0.0.1:8001/G10.png"} / A->>A: x` → gate `('', '')`, `validate` True, `mermaid.parse` True, SVG reso con `<image x="130" y="171" xlink:href="http://…">` | corretto: gate degli statement `MERMAID_URL_STATEMENTS` (mappa famiglia → parole chiave, MISURATA rendendo ogni parola in tutte e 15 le famiglie). Rieseguita la riproduzione: gate `('mermaid_external_resource', 'properties')`, PATCH 422. Chiusi con la stessa correzione `links`, `link`, `class link` e `click … href`, `;` come separatore compreso |

La causa era di nuovo di metodo: il documento faceva il ragionamento
giusto sugli escape YAML per il **frontmatter** (e lì sceglieva una lista
chiusa) e non lo applicava al blocco `@{ … }`, che Mermaid passa allo
STESSO js-yaml. Il test che avrebbe colto il difetto è doppio: la
batteria statica `MERMAID_SHAPE_BYPASSES` (13 forme della chiave `img`,
nessuna rete) e un oracolo di rendering,
`test_the_gate_rejects_sources_that_really_emit_an_external_resource`, che
per ognuna delle otto vie rende il sorgente con il pre-render di
produzione, verifica che l'SVG contenga davvero il riferimento esterno —
così il 422 non può essere un falso positivo su una forma inerte — e poi
verifica che il gate la rifiuti. La controprova sta in
`test_mermaid_shape_gate_accepts_every_key_mermaid_reads` e in
`test_mermaid_gate_keeps_the_keywords_that_are_only_text`: le undici
chiavi legittime, la chiave quotata, il commento `#`, e `click`/`link`
come testo in `mindmap`, `timeline`, `sankey-beta`, `erDiagram`,
`classDiagram` (`Link`) e dentro una label con `;`.
Corretta anche l'affermazione falsa di sezione 15 («il gate impedisce di
SALVARLA») e completate le due affermazioni incomplete di sezione 3.1
(l'origine YAML del blocco `@{ … }` e le vie di `<image>` che non passano
dalle shape). Il prompt di riparazione Mermaid nomina ora la lista chiusa
e gli statement vietati, leggendoli dalle stesse costanti del gate: senza
di che il fix AI non potrebbe convergere sul 422 nuovo.

Gate finali dopo il giro 4 (8 settembre 2026): `ruff check` e `ruff format
--check` puliti sui quattro file toccati; `ruff check .` del repo **372**
(invariato); `mypy app` **205** errori in 32 file (invariato, 215
sorgenti); pytest **1046/1046** verdi, zero saltati (**52 test in più**
rispetto ai 994 del giro 3: 13 per le forme YAML della chiave `img`, 11
per gli statement con URL, 9 per le chiavi legittime, 8 per le parole
chiave che restano testo, 8 per l'oracolo di rendering, 1 per lo splitter
delle voci, 1 per la controprova della shape piana e 1 per il falso
positivo dichiarato del `@{` citato in una label); frontend non toccato
dal giro 4.

**Correzione del giro 5.** Le due righe qui sopra dicevano il vero sui
vettori che il verificatore del giro 3 aveva portato, ma non su quello che
la correzione aveva fatto al resto: sostituendo la scansione larga
`\bimg\s*:` con la sola lista chiusa, il giro 4 ha RIAPERTO sette vettori
che il giro 3 rifiutava e ha aggiunto tre falsi positivi. Il conteggio è
nella tabella di sezione 14.8. La riga di sezione 14.4 che dava SEC-1 per
chiuso nel giro 4 era quindi falsa ed è stata riscritta.

### 14.8 Quinto giro della revisione (verifica del giro 4)

Il verificatore del giro 4 non ha dato l'ok: SEC-1 restava aperto e la
correzione del giro 4 aveva introdotto una regressione di sicurezza. La
causa era di metodo, non di dettaglio — il gate statico inseguiva il
parser vero e ogni giro sostituiva il controllo precedente invece di
sommarvisi. Il giro 5 cambia impostazione (sezione 3.1): gate dichiarato
best-effort, correzione per unione, e le tre difese vere rese complete.

| punto | riproduzione del verificatore | esito del giro 5 |
| --- | --- | --- |
| SEC-1 (regressione del giro 4) | `verifica-giro4/gate_min.py` e `sec1_minime.py`: `A@{ label: "a⏎b", img: "http://…" }` e `A@{ label: ( , img: "http://…" }` → gate `('', '')`, PATCH 200, `<image href>` nell'SVG e GET arrivata al listener; il giro 3 li rifiutava con `\bimg\s*:` | corretto: `_mermaid_shape_metadata` ricostruisce il testo che il lexer consegna ad `addVertex` (`/\n\s*/g` → `<br/>` dentro le virgolette) e la segmentazione segue quello; le parentesi tonde non contano come collezione YAML; la scansione `img:`/`icon:` sul blocco grezzo torna, sommata e non sostituita. Rieseguita: gate `('mermaid_external_resource', 'img:')` per entrambi |
| SEC-1 (escape YAML dietro la desincronizzazione) | `verifica-giro4/sec1_giro4_nuove.py`: sette varianti (`"\x69mg"` dopo un a capo nella stringa, `#` che per YAML non è un commento, URL protocol-relative) → tutte accettate dal giro 4, due con `<image href>` reso | corretto dalla stessa segmentazione: rieseguita, tutte e sette rifiutate, il controllo sano `C1_sana` resta accettato |
| SEC-1 (`stateDiagram` fuori dalla mappa) | `verifica-giro4/state_click2.py`: `stateDiagram-v2 / [*] --> A / click A href "http://…"` → gate `('', '')`, PATCH 200, SVG con `<a xlink:href="http://…" target="_blank">` nel PDF | corretto due volte: `stateDiagram` e `stateDiagram-v2` entrano in `MERMAID_URL_STATEMENTS` (case-insensitive, come il lexer di stato), e la scansione dell'SVG riconosce l'`<a href>` esterno. Rieseguita: gate `('mermaid_external_resource', 'click')`, PATCH 422, e con il gate disattivato l'export scarta comunque la figura (`risorsa esterna nell'SVG: <a href esterno>`) |
| SEC-1 (residuo vero: anteprima e vista client-side) | il render Mermaid nel browser di chi guarda non passa da alcun controllo: il payload persistito fa partire la richiesta DAL BROWSER DEL LETTORE | corretto: `sanitizeMermaidSvg` in `lib/figureFormats.ts`, chiamata da `MermaidDiagram.tsx` prima di mettere il markup nello stato. Prova reale in Chromium (`tests/test_frontend_mermaid_sanitize.py`): il modulo è compilato con l'esbuild del frontend, Mermaid rende i vettori, e il nodo inserito nella pagina tenta la GET senza sanificazione e non la tenta con. Residuo dichiarato in sezione 15: `mermaid.render` misura il diagramma su un nodo temporaneo che attacca al documento, quindi quella prima GET parte comunque |

**Confronto obbligatorio con gli alberi dei giri precedenti.** `939f0a5`
(giro 3) e `6c3e067` (giro 4) sono stati estratti con `git archive` e i
loro `mermaid_static_gate` eseguiti fianco a fianco con quello di oggi sui
corpora dei verificatori — 81 sorgenti sani (i 15 campioni D8, i 14
benigni del giro 2, i 35 del giro 4, 17 nuovi mirati alle modifiche) e 27
vettori maligni scelti fra quelli dei quattro giri (un sottoinsieme
curato: gli script dei verificatori ne contengono 77, e il confronto sul
set completo è nella tabella della sezione 14.9):

| gate | vettori maligni ACCETTATI | falsi positivi sui sani |
| --- | --- | --- |
| giro 3 (`939f0a5`) | 13 su 27 | 2 su 81 |
| giro 4 (`6c3e067`) | 13 su 27, **7 dei quali riaperti** | 4 su 81 |
| giro 5 (oggi) | **0 su 27** | 8 su 81 |

Nessun sorgente rifiutato da uno dei due alberi precedenti è accettato
oggi: è la condizione dell'unione, verificata caso per caso. I quattro
falsi positivi aggiunti sono misurati sul pre-render (i sorgenti si
rendono davvero) e dichiarati in sezione 15; la parte eseguibile del
confronto vive in
`test_the_gate_never_reopens_a_vector_a_previous_round_rejected`, che
rifà passare al gate ogni vettore storico.

Gate finali dopo il giro 5 (8 settembre 2026): `ruff check` e `ruff
format --check` puliti sui file toccati; `ruff check .` del repo **372**
(invariato); `mypy app` **205** errori in 32 file (invariato, 215
sorgenti); pytest **1150/1150** verdi, zero saltati (**104 test in più**
rispetto ai 1046 del giro 4: 10 per la desincronizzazione blocco/flow, 6
per gli statement di `stateDiagram` e 3 per `click` che resta testo, 7 per
la scansione dell'`<a href>` esterno e 15 per la sua controprova sui
campioni D8 resi, 43 per la rete storica dell'unione, 4 per i falsi
positivi dichiarati e 3 per le forme sane equivalenti, 1 per il `metadata`
del lexer e le nuove segmentazioni, 4 nell'oracolo di rendering, 8 nella
prova Playwright della sanificazione client);
`npm run lint` 4 errori di baseline e 23 warning, nessuno nei
file toccati; `npm run type-check` e `npm run build` verdi.

### 14.9 Sesto giro della revisione (verifica del giro 5)

Il verificatore del giro 5 ha rifatto per conto suo tutto il lavoro del
correttore — `git archive` di `939f0a5` e `6c3e067`, corpus estratto con
l'AST direttamente dagli script dei verificatori dei giri 1-4 (77 vettori
maligni e 65 sorgenti sani, quasi il triplo del sottoinsieme di 27 usato
in sezione 14.8), sanificazione client provata in Chromium senza
intercettazioni — e ha confermato sei punti su sette e l'unione. Non ha
dato l'ok per due ragioni, ed erano entrambe fondate.

La prima: **il gate era aggirabile oggi**, con la 11.17.2 pinnata. Il
verificatore ha trovato dieci sorgenti nuovi che il gate accettava e che
producevano davvero un riferimento esterno nell'SVG reso, con la GET
arrivata al suo listener. La seconda: **tre righe della documentazione
sovrastimavano** rispetto a ciò che il codice fa.

| punto | riproduzione | esito del giro 6 |
| --- | --- | --- |
| apice singolo dispari in uno scalare YAML piano | `verifica-giro5/nuovi_vettori.py`: `A@{ label: x'y, "\x69mg": "\x68ttp://…" }` → gate `('', '')`, PATCH 200, `<image href="http://…">` nell'SVG e GET arrivata al listener. L'apice non è un delimitatore né per il lexer (è un carattere di `NODE_STRING`) né per js-yaml in mezzo a uno scalare piano, ma lo era per `_split_top_level`, che vedeva UNA voce con chiave `label` | corretto: `_split_top_level` ha tre modalità di quotatura e il gate ne prova due per volta (`any`+`yaml` per le shape, `any`+`double` per gli statement), rifiutando se una qualsiasi segnala. Rieseguita: gate `('mermaid_external_resource', '\x69mg:')` e PATCH 422, dove prima erano `('', '')` e 200. Se il gate venisse scavalcato restano le altre due difese, misurate sullo stesso vettore: la guardia di rete annulla la GET (`prerender_request_blocked`) e la figura non entra nel documento — qui perché senza l'immagine il render torna vuoto (`mermaid_render_returned_empty`), nel vettore gemello con `<a xlink:href>` perché la scansione dell'SVG lo trova (`<a href esterno>`) |
| apice singolo dispari che nasconde il `;` | stessa fonte: `A["x"] --> B[it's]; click A href "http://…"` e, dalla sonda del giro 6, `A->>A: l'x; properties A: {"icon": "http://…"}` → gate `('', '')` e `<a xlink:href>` / `<image xlink:href>` nell'SVG | corretto dalla stessa unione di segmentazioni (`_QUOTING_DOUBLE` per gli statement). Rieseguita: gate `('mermaid_external_resource', 'click')` e `('…', 'properties')` |
| `U+FEFF` davanti alla parola chiave di uno statement | stessa fonte: `\ufeffproperties A: {"icon": "http://…"}` in `sequenceDiagram` e la stessa via per `click`/`link`/`links` in `flowchart`, `classDiagram`, `sequenceDiagram` e `stateDiagram`. Il BOM è whitespace per `\s` di JavaScript e non per `str.strip()` di Python | corretto: `_MERMAID_TRIM_RE` toglie il BOM insieme agli spazi. Verificate anche le forme `graph`, `classDiagram-v2`, `stateDiagram` v1, dopo un `;`, con frontmatter e con due BOM di fila |
| `\r` come a capo del lexer (trovato dal giro 6) | sonda del giro 6: `A --> B\rclick A href "http://…"` → gate `('', '')` e `<a xlink:href>` nell'SVG; `str.split("\n")` non vede il `\r`, il lexer sì | corretto: il `\r` entra fra i separatori di statement, non nella divisione in righe (spostare quella riammetterebbe `---\rtitle: x\r---\rflowchart LR`, oggi rifiutato per tipo: sarebbe uno scambio) |
| doc: «nessun sorgente D8 che passi il gate li produce» (§3.1, scansione dell'SVG) | falsa: due dei vettori del verificatore passano il gate e producono `<image href>` | riscritta: la scansione è dichiarata difesa indipendente, e la frase dice che sorgenti così sono stati trovati in ognuno dei sei giri |
| doc: «le difese vere sono tre e ora sono complete» (§3.1, §14.4, PR body) | falsa: §15 documenta che la terza non lo è | riscritta in tutti e tre i punti: le prime due sono complete, la terza ha il residuo del render client-side, con la misura |
| doc: «una versione futura di Mermaid può aggiungere una via nuova» (§15) | falsa: è aggirabile OGGI con la 11.17.2 pinnata | riscritta: l'aggirabilità è di oggi, con i tre vettori misurati, e nulla prova che il giro 6 sia stato l'ultimo |
| doc: «27 vettori maligni di tutti e quattro i giri» (§14.8, PR body) | è un sottoinsieme curato: gli script dei verificatori ne contengono 77 | detto: la riga dichiara il sottoinsieme e rimanda alla tabella su corpus completo qui sotto |

**Confronto obbligatorio, su corpus completo.** `939f0a5` (giro 3),
`6c3e067` (giro 4) e `acc02a1` (giro 5) sono stati estratti con `git
archive` in tre alberi separati e i loro `mermaid_static_gate` caricati
con `importlib` accanto a quello dell'albero di lavoro. Il corpus è quello
estratto con l'AST dagli script dei verificatori dei giri 1-4, più i 31
vettori nuovi del verificatore del giro 5 e i 27 della sonda del giro 6:
**135 vettori maligni e 87 sorgenti sani**.

| gate | maligni ACCETTATI (135) | falsi positivi (87) |
| --- | --- | --- |
| giro 3 (`939f0a5`) | 100 | 2 |
| giro 4 (`6c3e067`) | 85 | 4 |
| giro 5 (`acc02a1`) | 65 | 5 |
| giro 6 (oggi) | **31** | 5 |

Sul solo corpus storico (77 maligni, 65 sani), che è quello con cui il
verificatore del giro 5 ha misurato, i numeri sono 43 / 29 / 9 / **9**
accettati e 1 / 4 / 4 / **4** falsi positivi: il giro 6 non tocca né i
nove vettori storici ancora accettati né i quattro falsi positivi già
dichiarati in sezione 15. I **34 vettori che rifiuta e che il giro 5
accettava** stanno tutti fra i 58 aggiunti dai giri 5 e 6, e i 22 che
restano accettati fra quelli sono inerti (paragrafo successivo). Nessun
sorgente rifiutato da uno dei tre alberi precedenti è accettato oggi.

**I 31 vettori ancora accettati sono inerti, e lo sono per misura, non
per deduzione.** Rendendoli tutti con il pre-render e la guardia di rete
DISATTIVATA, verso un listener HTTP locale: **0 GET arrivate** e nessun
`<image>`, `<a href>` esterno o `url(http…)` negli attributi degli SVG
resi. Sono sorgenti che non parsano (`style A fill:url(…)`,
`A@ { img: … }`, `%%{ … }%%` a metà riga, le parentesi non bilanciate,
`Click` maiuscolo, `click` in `block-beta`, `treemap-beta`, `pie`,
`quadrantChart`, `xychart-beta`, `radar-beta`) o che rendono senza alcun
riferimento (`mindmap ::icon`, che richiede un pacchetto di icone
registrato, e `gantt click href`). L'unico che porta l'host nell'SVG è
`erDiagram` con `click A href "http://…"`, dove `click` diventa il nome
di un'entità e l'URL finisce nell'`id` del nodo
(`id="mmd-N-entity-http://…"`) e nel testo dell'entità disegnata. La
figura entra quindi nel documento (`render_figure_map` la rende, la
scansione non ha nulla da segnalare), ma l'`id` è testo in un attributo,
non un riferimento: non parte alcuna richiesta, né dal server né dal
browser del lettore. Rifiutarlo significherebbe rifiutare l'entità che si
chiama `click` in ogni `erDiagram`, cioè un falso positivo su contenuto
legittimo: resta accettato, dichiarato qui.

**Falsi positivi.** Restano i quattro del giro 5, invariati e già
dichiarati in sezione 15; il giro 6 non ne aggiunge nessuno sul corpus
storico. Il quinto della tabella (`A@{ label: 'x, img: y' }`) è un
sorgente sano che ho aggiunto io al corpus e che `acc02a1` rifiutava già:
lo prende la scansione `\b(?:img|icon)\s*:` sul blocco grezzo, la stessa
rete del giro 3 che vale già per `A@{ label: "img: la sorgente" }`.
Verificate invece sane, e passanti, tutte le forme che l'apostrofo rende
sospette e che sono normalissime in italiano: `A["l'esempio d'uso"]`,
`A[l'esempio] --> B[d'oro]`, `A@{ label: l'uso }`,
`A@{ shape: rect, label: "l'a, la b" }`, `A->>B: l'esempio`,
`stateDiagram-v2 / [*] --> A: l'avvio`, più il BOM davanti a un commento
`%%`, un sorgente con fine riga CRLF e un `\r` dentro una label quotata.

Gate finali dopo il giro 6 (8 settembre 2026): `ruff check` e `ruff
format --check` puliti sui file toccati; `ruff check .` del repo **372**
(invariato); `mypy app` **205** errori in 32 file (invariato, 215
sorgenti); pytest **1225/1225** verdi, zero saltati (**75 test in più**
rispetto ai 1150 del giro 5: 10 per l'apice singolo, 16 per il BOM e il
`\r`, 11 per le forme sane che devono continuare a passare, 1 per le tre
modalità di quotatura in isolamento, 26 per la rete storica dell'unione,
11 nell'oracolo di rendering, che rende ogni vettore nuovo e verifica che
l'SVG contenga davvero il riferimento esterno prima di chiedere il 422);
il frontend non è stato toccato — `npm run lint` 4 errori di baseline e 23
warning, `npm run type-check` e `npm run build` verdi.

### 14.10 Settimo giro: la correzione vera di SEC-1 è la politica del browser

Il verificatore del giro 6 ha trovato altre due classi di sorgenti che il
gate accettava e che rendevano davvero un riferimento esterno, entrambe
per lo stesso motivo — il gate legge il sorgente in un modo, Mermaid in un
altro — e il giro 7 ha smesso di rincorrere: il gate resta e si corregge
(sotto), ma **il controllo che chiude SEC-1 sul residuo è la
Content-Security-Policy della pagina**.

**1. La politica.** Il progetto aveva già `img-src 'self' data: blob:`,
ma solo sulle risposte del backend (`middleware/security_headers.py`); la
pagina dell'applicazione la serve nginx, che aveva gli altri header di
sicurezza e non questo, e il browser applica la politica del documento.
`frontend/nginx.conf` ora porta `add_header Content-Security-Policy
"img-src 'self' data: blob:<origine upload>" always;` — **solo `img-src`**,
senza `default-src`, così script, stili, font (Google Fonts sta in
`index.html`), connessioni e media restano invariati e il raggio d'azione
è le sole immagini. L'origine degli upload non può essere fissa: in
produzione gli upload stanno sullo storage OVH
(`VITE_UPLOADS_BASE_URL=https://progettiersaf.com/media/uploads`) e con
`'self'` secco le immagini caricate sparirebbero. È derivata a build time
dalla STESSA variabile che decide da dove il frontend carica le immagini
(`src/lib/media.ts`): lo stage `runtime` di `frontend/Dockerfile`
sostituisce il segnaposto `__A4U_UPLOADS_ORIGIN__` con
`schema://host[:porta]` se il valore è assoluto, con la stringa vuota se è
relativo, e fallisce il build se il segnaposto resta.

Verificato sul file generato DENTRO l'immagine, nei due casi:

| build arg | `docker run --rm <img> cat /etc/nginx/conf.d/default.conf` |
| --- | --- |
| `VITE_UPLOADS_BASE_URL=/uploads` | `add_header Content-Security-Policy "img-src 'self' data: blob:" always;` |
| `…=https://progettiersaf.com/media/uploads` | `add_header Content-Security-Policy "img-src 'self' data: blob: https://progettiersaf.com" always;` |

Verificato che **blocchi davvero**, con la pagina servita da un server
vero con quell'header, Mermaid 11.17.2 (il pacchetto di `node_modules`,
non un CDN), Chromium via Playwright e un listener HTTP che registra le
GET. Quattro vettori che rendono un `<image href>`: la shape `img:` nuda,
la stessa dietro un `\r`, la stessa dietro `%%{x}%%` e `sequenceDiagram`
con `properties A: {"icon": …}`.

| pagina | GET arrivate al listener | immagini dell'app |
| --- | --- | --- |
| senza header (controllo) | **4 su 4** (una per vettore) | tutte caricate |
| `img-src 'self' data: blob: http://127.0.0.1:8003` | **0** (4 violazioni in console) | stessa origine, origine upload, `data:`, `blob:` caricate; host estraneo bloccato |
| `img-src 'self' data: blob:` (default) | **0** | come sopra, ma l'origine upload è bloccata — è la misura del perché la derivazione a build time serve |

Il controllo senza header è la prova che l'apparato misura qualcosa: le
GET partono anche con `sanitizeMermaidSvg` attiva, perché
`mermaid.render` attacca l'SVG al documento prima che la sanificazione
possa intervenire. Con la politica, i tre vettori con la shape `img:`
falliscono il render in Chromium (`EncodingError: The source image cannot
be decoded`), cioè il diagramma non compare: è il comportamento voluto, e
quei sorgenti prendono comunque 422 al salvataggio.

**2. Le due classi ancora aperte del gate.** Chiuse comunque, perché la
regola dell'unione non ammette vettori noti e accettati. La correzione è
una seconda LETTURA del sorgente, non un cambio di quella esistente
(`_mermaid_gate_views`, sezione 3.1): `\r` normalizzato in `\n` come
`cleanupText`, direttive tolte come `removeDirectives` e commento
riconosciuto come da `cleanupComments`. Il gate ripete tutti i controlli
su entrambe le letture e rifiuta se una segnala.

| classe | riproduzione a `1ca2284` | oggi |
| --- | --- | --- |
| `\r` dentro una riga di commento | `flowchart LR⏎%%nota\r  A@{ img: "http://…" } --> B` → gate `('', '')`, `<image href>` nell'SVG e **GET arrivata** al listener. Stessa via per `click` in `flowchart`, `graph`, `classDiagram`, `stateDiagram-v2`, per `links`/`properties` in `sequenceDiagram`, per un tag HTML nella label, per una chiave `config:` nel frontmatter e con `%%\r` nudo | `('mermaid_external_resource', 'img:')` e gli esiti corrispondenti per le altre forme |
| riga `%%{…}%%` (direttiva, non commento) | `flowchart LR⏎%%{x}%% A@{ img: "http://…" } --> B` → gate `('', '')`, `<image href>` e **GET arrivata**. Stessa via per `click`, `properties`, un tag HTML e la chiave con escape `"\x69mg"` | `('mermaid_external_resource', 'img:')` |

**Oracolo di rendering, 19 vettori.** Ognuno reso con il pre-render vero:
tutti producono un `<image href>` o un `<a xlink:href>` verso l'host
dell'autore (5 hanno fatto arrivare la GET al listener con la guardia di
rete disattivata), e tutti sono rifiutati dal gate di oggi. Con la
guardia attiva — la produzione — le GET sono **0**. Nessun sorgente con
il gate aperto rende un riferimento esterno.

**Confronto dell'unione su CINQUE alberi** (`939f0a5`, `6c3e067`,
`acc02a1`, `1ca2284` e l'albero di lavoro), corpus estratto con l'AST
dagli script dei giri 1-6 più i 19 vettori e le 8 controprove del giro 7:
**301 maligni e 364 sani**.

| gate | maligni ACCETTATI (301) | falsi positivi (364) |
| --- | --- | --- |
| giro 3 (`939f0a5`) | 214 | 20 |
| giro 4 (`6c3e067`) | 161 | 67 |
| giro 5 (`acc02a1`) | 130 | 78 |
| giro 6 (`1ca2284`) | 98 | 82 |
| giro 7 (oggi) | **62** | 87 |

**Nessun vettore riaperto** rispetto ad alcuno dei quattro alberi. I
cinque «falsi positivi» in più rispetto a `1ca2284` sono cinque vettori
di attacco della sonda del giro 6 che il classificatore del corpus conta
come sani perché non contengono un `http:` letterale (portano un tag
HTML, una chiave `config:` o un URL con escape `\x68ttp`): sul corpus
benigno vero i falsi positivi restano **gli stessi 82**, e sul corpus
benigno curato del giro 5 restano **4 su 75**, quelli già dichiarati in
sezione 15. I 62 maligni ancora accettati sono i 31 inerti del giro 6 più
i vettori che i verificatori precedenti avevano già misurato come non
renderizzabili.

**Test aggiunti.** 19 vettori delle due classi in
`test_mermaid_static_gate_rejects_cr_and_directive_lines` (verificati uno
per uno contro l'albero `1ca2284`: tutti `PASSA` prima, tutti rifiutati
adesso), 7 controprove sane in
`test_mermaid_static_gate_accepts_cr_and_directive_lines_without_statements`
(CRLF, percentuali nelle label, `%%{wrap}%%` da solo e in testa, `%%`
nudo, due commenti separati da un `\r`, `\r` dentro una label — le ultime
due rese davvero da Mermaid) e 7 in
`test_frontend_csp_header.py`, che pinna la forma della direttiva, il
fatto che sia l'unica (niente `default-src`) ed esegue lo stesso blocco
`sh` del `Dockerfile` sui valori reali di `VITE_UPLOADS_BASE_URL`.

Gate finali dopo il giro 7 (8 settembre 2026): `ruff check` e `ruff
format --check` puliti sui file toccati; `ruff check .` del repo **372**
(invariato); `mypy app` **205** errori in 32 file (invariato, 215
sorgenti); pytest **1258/1258** verdi, zero saltati (**33 test in più**
rispetto ai 1225 del giro 6: 19 vettori, 7 controprove sane, 7 sulla
politica); frontend non toccato nel codice (solo `nginx.conf` e
`Dockerfile`) — `npm run lint` 4 errori di baseline e 23 warning,
`npm run type-check` e `npm run build` verdi.

### 14.11 Ottavo giro: i sette rilievi minori della chiusura

I due verificatori del giro 7 hanno dato l'ok — nessun rilievo bloccante o
maggiore — e hanno lasciato sette rilievi **minori**. Sono chiusi tutti:
quattro da tre correzioni (la derivazione dell'origine nel `Dockerfile`,
un'asserzione su `nginx.conf`, il nodo residuo nel frontend), tre
dichiarando qui il confine che il documento non diceva.

**1. Il confine della politica.** `img-src` è l'**unica** direttiva:
tutto ciò che non è un'immagine esce comunque dalla pagina. Rimisurato in
proprio, iniettando 14 costrutti nel documento vivo servito con la
politica di produzione (`img-src 'self' data: blob: http://127.0.0.1:8003`,
Chromium, spia HTTP che registra le GET):

| costrutto | direttiva che deciderebbe | GET con la politica attiva |
| --- | --- | --- |
| `<img>`, `<image href>`, `poster`, `<input type=image>` | `img-src` | **0** (4 violazioni in console) |
| `@import` nel `<style>` dell'SVG e in quello della pagina, `<link rel=stylesheet>` | `style-src` | 3 su 3 |
| `@font-face` | `font-src` | 1 |
| `<iframe>`, `<foreignObject><iframe>` | `frame-src` | 2 |
| `<video><source>` | `media-src` | 1 |
| `<object data>` | `object-src` | 1 |
| `<link rel=prefetch>` | `prefetch-src` | 1 |
| `fetch()` | `connect-src` | 1 |

Sette classi di direttiva, dieci costrutti, passano. **Oggi non è un
buco**, ed è misurato: il verificatore del giro 7 ha costruito 22 sorgenti
apposta per far emettere a Mermaid un `@import`, un `@font-face` o un
`<iframe>` (`style A fill:url(…)`, `classDef` con la graffa chiusa a mano,
`linkStyle`, `accTitle:`/`accDescr {}`, `subgraph`, `mindmap ::icon(URL)`)
e nessuno ci riesce — il lexer dei flowchart non ammette `@`, `(`, `)` in
`NODE_STRING` e con `htmlLabels: false` non esistono `<foreignObject>`; i
due casi in cui `@import` compare davvero nell'SVG (`accTitle`, `accDescr`)
lo mettono in `<title>`/`<desc>` come TESTO, 0 GET. Ha poi reso nel
browser i 58 maligni che il gate accetta oggi: 31 resi, **0 GET**, nessun
`<image>` né `href="http"`. Il confine conta perché il meccanismo è lo
stesso dell'`<image>`: un `<style>` con `@import` partirebbe **durante**
`mermaid.render`, e `sanitizeSvgCss` — che pure toglie `@import` e i
`url(…)` esterni — arriverebbe tardi esattamente come per l'`<image>`. Se
un domani `htmlLabels` tornasse `true`, o una versione nuova di Mermaid
emettesse un `<style>` con `@import`, la politica non fermerebbe nulla:
allargarla a `default-src` ha un raggio d'azione molto maggiore e va
deciso a parte (sezione 15).

**2. La derivazione dell'origine degli upload era fragile.** Il blocco
`RUN` del `Dockerfile` confrontava il valore grezzo con un `case` della
shell, che è sensibile alle maiuscole e agli spazi, e verificava la riga
generata con un `grep -F` sulla sola presenza. Tre valori plausibili
passavano il build in silenzio; eseguendo il blocco tale e quale (la
tecnica del test):

| `VITE_UPLOADS_BASE_URL` | prima (`1bbbdce`) | oggi |
| --- | --- | --- |
| `HTTPS://Progettiersaf.com/media/uploads` | build **verde**, `img-src 'self' data: blob:` — origine PERSA | `… blob: https://progettiersaf.com` |
| ` https://a.example.com/u` (spazio iniziale, `.env` copiato male) | build **verde**, origine PERSA | `… blob: https://a.example.com` |
| `https://a.example.com"; add_header X-Evil "1` | build **verde**, con `add_header X-Evil "1" always;` in più nel file generato | build **FALLITO** |

I primi due sono un guasto visibile solo in produzione: `media.ts` usa
`/^(https?:)?\/\//i`, insensibile alle maiuscole, e il browser ignora gli
spazi ai bordi dell'attributo, quindi le immagini caricate venivano
chieste all'host giusto mentre la politica non lo elencava — tutte
bloccate, senza un errore in build né in avvio. La correzione normalizza
il valore (spazi ai bordi tolti, schema e host in minuscolo: per la CSP
sono comunque insensibili alle maiuscole, e il path non entra
nell'origine) e sostituisce il `grep -F` con un `grep -E` sulla **forma**
della riga, così un apice doppio, uno spazio interno o un path residuo
fermano il build. Verificato anche costruendo davvero lo stage `runtime`
con `docker build` (busybox `sed`/`tr`/`grep`): i tre valori sopra danno
l'esito della colonna «oggi», e i quattro valori legittimi
(`/uploads`, l'URL OVH, `//cdn…`, `http://127.0.0.1:9000/uploads`) sono
invariati.

**3. La politica arriva su ogni risposta solo per ereditarietà.** nginx
eredita gli `add_header` del livello superiore SOLO se il livello corrente
non ne ha nessuno: **un** `add_header` dentro un `location` cancella lì
tutti e cinque quelli del `server`, la Content-Security-Policy compresa.
Misurato su nginx 1.27.5, aggiungendo `add_header X-Futuro "1" always;`
dentro `location / { … }` del file del repo: la risposta porta
`X-Futuro: 1` e nessuno degli altri cinque, `nginx -t` passa e il test del
giro 7 restava verde. Ora `test_frontend_csp_header.py` asserisce che
nessun blocco `location` contenga un `add_header`, e `nginx.conf` lo dice
in un commento sopra i cinque header.

**4. Ogni render Mermaid fallito lasciava una copia visibile nel `<body>`.**
`mermaid.render` misura la geometria in un `<div id="d<id>">` che ATTACCA
al documento e lo toglie solo quando arriva in fondo (`removeTempElements`,
`mermaid.core.mjs`): se `draw` lancia, il div resta. Con la politica
l'innesco è certo — una shape `img:` esterna non carica più e il render
lancia `EncodingError` — e l'editor rende a ogni battuta. Misurato in
Chromium con il pacchetto di `node_modules`, dieci render dello stesso
sorgente:

| percorso | errori | nodi rimasti nel `<body>` |
| --- | --- | --- |
| `mermaid.render` nudo (com'era) | 10 | **10**, `<div>` in flusso normale larghi 1264 px, `visibility: visible`, con dentro l'SVG del diagramma |
| `renderMermaidSvg` (oggi) | 10 | **0**, `body.children` torna al valore di partenza |

La correzione è `renderMermaidSvg` in `lib/figureFormats.ts`: `finally`
con `document.getElementById('d' + id)?.remove()`, no-op sul percorso
felice. Non è un difetto introdotto dal ramo (senza alcuna politica, un
`img:` verso un host morto lascia lo stesso nodo), ma è la politica a
renderlo certo per i contenuti già in DB e per la digitazione.

**5. Le immagini markdown del corpo della dispensa sono una sorgente
`<img>`, e la politica le blocca.** `MarkdownRenderer` monta
`ReactMarkdown` senza `urlTransform`, `allowedElements` o `rehype-raw`,
quindi `![](http://…)` arriva intatto: con il pacchetto vero del progetto,
`Testo ![alt](http://127.0.0.1:8001/estranea.png) altro` rende
`<p>Testo <img src="http://127.0.0.1:8001/estranea.png" alt="alt"/> altro</p>`.
Quel markup nella pagina servita con la politica: immagine in `errore`,
una violazione in console, **GET assente** dalla spia; nella stessa pagina
senza header, immagine `caricata` e GET registrata. **L'effetto è voluto**
— è la stessa classe di richiesta dell'`<image>` di Mermaid — ma è un
cambiamento visibile su contenuti già in DB, e va contato fra le sorgenti
di immagine dell'applicazione (sezione 7 e voce di sezione 13), non solo
come rischio del PDF.

**Test aggiunti.** 10 in `test_frontend_csp_header.py` (4 valori
normalizzati che oggi producono l'origine giusta, 5 malformati che devono
far FALLIRE il build, l'asserzione sull'ereditarietà degli `add_header`) e
4 in `test_frontend_mermaid_render_cleanup.py` (il componente passa dal
wrapper, la rimozione sta in un `finally`, i dieci render falliti con la
controprova a `mermaid.render` nudo, il diagramma sano che non lascia
nulla).

Gate dopo il giro 8 (8 settembre 2026): `ruff check` e `ruff format
--check` puliti sui file toccati; `ruff check .` del repo **372**
(invariato); `mypy app` **205** errori in 32 file (invariato); pytest
**1272/1272** verdi, zero saltati (**14 test in più** rispetto ai 1258 del
giro 7); `npm run lint` 4 errori di baseline (nessuno nei file toccati),
`npm run type-check` e `npm run build` verdi.

## 15. Limiti dichiarati e lavori futuri

Limiti noti alla chiusura del branch, con la ragione per cui restano e
la direzione del lavoro successivo. Nessuno è nascosto dal codice: ogni
caso produce un fallback visibile, un log o un errore esplicito.

- **Localizzazione del `content` delle figure nella duplicazione in altra
  lingua** (doc 15). La duplicazione traduce `caption` e `alt_text`
  (`CONTENT_RAW_TRANSLATE_PATHS`, `SLIDES_RAW_TRANSLATE_PATHS`) ma non i
  testi interni alle sorgenti: label dei nodi Mermaid e DOT, `title` /
  `axis.title` / `text` di Vega-Lite, `label` di espressioni e annotazioni
  di `function`. A generazione la rete di sicurezza i18n usa già
  `extract_translatable`/`apply_translations` dei renderer (D7): il lavoro
  futuro è instradare la duplicazione sugli stessi metodi, con la
  rivalidazione offline dopo la traduzione. La didascalia calcolata di
  `function` non è persistita e segue sempre la lingua del corso a render.
  **Caso peggiorato dal catalogo (§18)**: `extract_translatable` di
  Vega-Lite raccoglie `title` e i `*.title` dei canali, non le stringhe
  dentro `data.values` — e nei tipi che il catalogo ora raccomanda
  (torta, ciambella, barre ordinate, mappa di calore) le categorie SONO
  l'etichettatura: su una torta due terzi del testo reso resterebbero in
  italiano. In generazione il buco è chiuso dal prompt (il blocco LINGUA
  nomina esplicitamente `data.values`); nella duplicazione no. Chi
  implementerà l'estrazione deve soddisfare due condizioni misurate: (a)
  tradurre INSIEME le liste `sort` che ripetono le stesse categorie, o
  l'ordine dichiarato smette di corrispondere e la figura torna
  alfabetica in silenzio; (b) tenere sotto controllo il numero di campi
  di localizzazione, perché il meccanismo è una chiave per percorso e
  una spec da 200 righe con due campi testuali ne produrrebbe 400 per
  figura.
- **Righe lunghe del fallback tagliate a destra nelle slide** (D12,
  settimo giro). Nelle slide e nei frame video il `<pre>` con il sorgente
  di una figura non resa è in `white-space: pre`: una riga più larga dei
  255 mm del box si legge fino al bordo destro e il resto è nascosto da
  `overflow: hidden`. È il prezzo di un conteggio esatto delle righe
  rese, dopo sei giri in cui ogni stima delle righe a capo in `pre-wrap`
  è stata battuta da Pango o da Chromium (sezione 12, voce D12); resta
  confinato al percorso d'errore, che scrive sempre
  `figure_render_fallback`, e la dispensa mostra il sorgente intero in
  `pre-wrap`. Il lavoro futuro, se servisse leggere il sorgente anche
  nelle slide, è andare a capo in Python con una larghezza di colonna
  prudente e passare al `<pre>` righe già spezzate, senza affidare l'a
  capo al motore.
- **Righe del fallback con ideogrammi in testa più care nelle slide**
  (D12, ottavo giro). Una riga del `<pre>` il cui primo carattere con
  script reale è un ideogramma, un kana o un Hangul vale 2,46 em invece di
  1,70, perché Pango allinea le altre run sulla baseline ideografica e in
  WeasyPrint la riga cresce fino a 2,293 em nel container (limite
  analitico 2,4555 su tutte le coppie di font). Nei corsi con sorgenti
  Mermaid o DOT dagli id cinesi, giapponesi o coreani il fallback delle
  slide mostra circa un terzo di righe in meno prima di «…»; resta nel
  percorso d'errore. Se servisse di più, la strada è dare a ogni riga del
  `<pre>` un blocco ad altezza fissa, che rende l'altezza indipendente
  dai font in entrambi i motori.
- **Geometria dei tipi Mermaid a tela fissa** (§18). Radar e treemap non
  ridimensionano il riquadro sulle etichette: il tema allarga i margini
  del radar (100/240 px) e i test misurano il bbox reale di ogni `<text>`
  contro il `viewBox`, ma un'etichetta molto lunga esce comunque —
  l'oracolo la vede, il renderer no. Nel treemap Mermaid riduce il corpo
  del testo fino a farlo stare in larghezza con ~10 px di margine, e
  l'impaginazione la decide il font di Chromium mentre il PDF ridisegna
  con quello di WeasyPrint: in produzione sono lo stesso «Noto Sans»
  (installato nel container), su una macchina senza quel font le
  metriche divergono e il margine sottile si perde. Le intestazioni di
  sezione del treemap usano `cScaleLabel` (bianco sopra il blu della
  palette) su un riempimento al 60% di opacità, quindi con un contrasto
  più basso di quello calcolato per il colore pieno: è un limite di
  Mermaid, non del tema. Lavoro futuro: portare `PALETTE_LABEL` a
  dipendere dall'opacità del riempimento, o riportare l'opacità a 1.
- **Mermaid via CDN a runtime** (validatore e pre-render caricano
  `mermaid@11.17.2` da jsdelivr in Chromium). Offline degrada come prima
  del branch: pass-through nel validatore, fallback `<pre>` nel PDF con
  `figure_render_fallback` nei log; i tre formati nuovi sono offline. Il
  batch Mermaid ha un tetto proprio di almeno 60 s (sezione 2.2). Lavoro
  futuro: bundle locale di `mermaid.esm` servito dalla pagina di
  pre-render (o pre-render via CLI + Node), che toglierebbe anche l'unica
  dipendenza di rete della CI per i test D8.
- **Livello 2 del formato `function`** (A9): curve parametriche, polari,
  coniche implicite, successioni e ricorrenze non sono nel `kind` della
  spec. Lo schema strict e `FunctionFigureSpec` li rifiutano (422
  Pydantic su `kind` ignoto), il prompt P3 non li propone. Il livello 1
  (`function_study`, `tangent`, `area`, `family`, `level_curves`) è
  verde con 117 test. Aggiungere un `kind` richiede: parser (nuove
  variabili libere), numerico (campionamento del parametro), simbolico
  (forme esatte dei punti notevoli), disegno, frasi i18n it/en, template
  dell'editor.
- **Testo non selezionabile nelle figure `<img>`** (Q3): Vega-Lite, DOT e
  `function` entrano nel PDF della dispensa come `<img
  src="data:image/svg+xml;base64,…">` (e così tutte le figure nelle slide
  e nei frame video). Il testo è estraibile (pypdf lo legge, verificato
  in `test_lesson_pdf_figures.py`) ma non selezionabile come testo
  scorrevole nel visualizzatore. Scelta deliberata (sezione 12: elemento
  sostituito, `max-height` rispettato, nessuna collisione di id); passare
  all'SVG inline richiederebbe il namespacing degli id e una gestione
  separata del `width: 100% !important` oggi applicato ai Mermaid inline.
- **Figure orfane in coda** (A12): gli asset non citati con `[FIG:id]` nel
  corpo (introduzione → sezioni → sintesi) compaiono dopo la sintesi e
  prima dei punti chiave, con gli ultimi numeri, nel PDF e nella vista.
  Prima del branch erano invisibili: per i contenuti già in DB con asset
  orfani è un cambiamento visibile, che lo script di rivalidazione
  quantifica (colonna «lezioni con asset non citati»). Una citazione
  solo nei `key_takeaways` o nelle `references` non conta come corpo
  (quei campi non passano dalla sostituzione dei tag). Lavoro futuro:
  un avviso nell'editor per gli asset non citati.
- **Figura non renderizzabile all'export = errore nei log, non al docente**
  (A23). Un SVG rifiutato, un timeout del batch, `dot` assente o una spec
  che il figlio non rende producono il fallback `<pre
  class="figure-fallback">` con il sorgente nel PDF e nei frame video, e
  `log.error("figure_render_fallback", lesson_code, asset_id, format,
  reason)`. Il documento viene comunque prodotto; il docente lo scopre
  aprendo il PDF. Mitigazioni in essere: `validate(deep=True)` nel worker
  rende e mette in cache prima dell'export, cache negativa di 60 s, script
  di rivalidazione. Lavoro futuro: esporre gli asset in fallback
  nell'esito dell'export (campo `pdf_error`/badge nella vista) invece che
  nel solo log.
- **`[FIG:]` dentro esempi e tabelle** non è risolvibile né numerabile
  (`ExampleBlock` usa `ReactMarkdown` direttamente; il PDF non sostituisce
  i tag dentro `examples[].content`): limite del modello dei tag, non
  delle figure.
- **Le 22 lingue non it/en** ricevono etichette e frasi calcolate in
  italiano nel PDF e nel frontend finché l'amministratore non lancia
  l'auto-translate delle chiavi `courses.figures.*` (A4).
- **Baseline rossa preesistente** di ruff/mypy/eslint (A6, A17) non sanata:
  fuori perimetro; i file toccati o nuovi sono puliti e i conteggi sono
  riportati a parità di configurazione (sezione 14.3).
- **Campione reale** per lo script di rivalidazione (A24): aperto, da
  eseguire su un dump di staging prima del rilascio. La revisione
  avversariale di Fase D è chiusa in sette giri (sezioni 14.4-14.10).

Limiti aggiunti dalla revisione avversariale di Fase D, con l'id del
rilievo che li ha resi espliciti.

- **Il tetto di `figure_render_timeout_seconds` è per batch, non per
  figura** (COR-6): per `function` e `dot`, che rendono una figura alla
  volta, N figure competono per lo stesso tetto. Con la cache calda —
  percorso nominale, perché la validazione del worker rende e mette in
  cache prima dell'export — non si vede; con la cache fredda (riavvio,
  sfratto della LRU, figura modificata a mano, che il gate `deep=False`
  non pre-renderizza) la lezione può perdere in blocco le figure di quel
  formato, con il fallback di A23. È la forma prescritta da Q1; il lavoro
  futuro è un tetto per figura o un floor per formato come quello di
  Mermaid.
- **`figure_render_max_workers` non copre la validazione profonda dei
  worker** (COR-5): il tetto reale del CPU-bound è ~2 (export e anteprime)
  più una validazione per lezione in volo, cioè fino a 6 secondo
  `course_lesson_{content,slides}_max_concurrency`. Costo di memoria, non
  di correttezza: serializzarla allungherebbe la generazione, quindi la
  scelta va presa con un setting proprio.
- **Rinominare l'`asset_id` di un asset legacy escluso lo fa rivalidare**
  (REG-2): il confronto di A15 è per `asset_id`, quindi un `journey` già
  in DB passa indenne a un edit del testo ma riceve 422 se il docente ne
  cambia l'id, con un errore che parla del `content`.
- **A11-L4 vale per 13 tipi su 15** (REG-3): `classDiagram` ed `erDiagram`
  non sono byte-deterministici fra processi (rough.js senza seed), a forma
  e resa identiche; due PDF dello stesso contenuto prodotti da processi
  diversi non sono byte-identici. Il determinismo byte a byte richiederebbe
  un seed che `mermaid.initialize` non espone.
- **Risorse esterne per la via del markdown della dispensa** (SEC-1): le
  figure non caricano più nulla, ma `MarkdownIt(..., html=True)` lascia
  passare `<img src="file:///…">` e `![](http://…)` nel testo, e WeasyPrint
  li scarica. Preesistente al ramo; il rimedio è un `url_fetcher` che
  ammetta i soli `data:` per tutti i PDF. **Nel browser, invece, dal giro
  7 la politica le blocca**: `![](http://…)` diventa un `<img src>` che
  `img-src` rifiuta (misurato, sezione 14.11) — quindi la stessa dispensa
  mostra l'immagine nel PDF e non nella pagina, ed è la sola sorgente di
  immagine dell'applicazione che la politica tocca (sezione 13).
- **Il gate statico Mermaid è una euristica di difesa in profondità**
  (SEC-1, giri 5 e 7).
  Riconoscere leggendo il sorgente come testo se un diagramma caricherà
  una risorsa significa rincorrere un lexer con stati, js-yaml e una
  decina di grammatiche: quattro giri di correzione l'hanno perso a turno,
  e il quarto, sostituendo un controllo invece di sommarlo, ha riaperto
  sette vettori che il terzo rifiutava. Dal giro 5 il gate si corregge
  solo per unione (nessun sorgente già rifiutato può tornare ammesso, con
  il confronto sugli alberi `939f0a5` e `6c3e067` in sezione 14.8) e la
  sicurezza NON si regge su di lui: si regge sui quattro controlli di
  sezione 3.1 — isolamento di rete del pre-render, scansione dell'SVG
  reso e, nel browser, la politica del documento (in produzione, non in
  sviluppo). La sanificazione lato client è il quarto e viene per ultima
  con il suo limite: toglie il riferimento dal markup che entra nella
  pagina, ma **non può precedere la prima richiesta** — misurato, sulla
  pagina di controllo senza header le GET dei vettori `img:` arrivano
  comunque al listener con `sanitizeMermaidSvg` nel percorso, e con
  l'header sono zero.
  Il gate resta perché dà al docente un 422 leggibile invece di una figura
  che sparisce, e perché impedisce che il payload arrivi in DB. **È
  aggirabile oggi, non in una versione futura di Mermaid**: con la
  11.17.2 pinnata, il giro 5 ha trovato sorgenti che lo passavano e
  rendevano un `<image href>` con la GET arrivata al listener (apice
  singolo dispari in uno scalare YAML piano, `U+FEFF` davanti alla parola
  chiave di uno statement), il giro 6 ne ha aggiunto uno suo (il `\r`
  come a capo del lexer) e il verificatore del giro 6 altre due classi
  intere (il `\r` dentro una riga di commento e la riga `%%{…}%%`, che
  per Mermaid è una direttiva e non un commento), chiuse dal giro 7 con
  una seconda lettura del sorgente. **Nulla prova che il giro 7 sia stato
  l'ultimo**: è l'esito atteso di una euristica testuale contro un parser
  vero, e sette giri di controesempi sono la misura del fatto che questa
  gara non si vince. Per questo l'analisi di sicurezza non si appoggia al
  gate: chi trova un nuovo aggiramento incontra l'isolamento di rete (0
  GET dal server), la scansione dell'SVG (la figura non entra nel
  documento) e, nel browser, la Content-Security-Policy della pagina, che
  è la correzione vera del residuo client-side (sezione 14.10) — in
  produzione, non in sviluppo (voce sotto). La direzione resta un vero
  lexer Mermaid condiviso con il frontend.
- **Il gate delle shape Mermaid è un soprainsieme del lexer** (SEC-1,
  giro 2, allargato dai giri 4 e 5): `_mermaid_shape_blocks` considera
  shape OGNI `@{` del corpo, anche uno scritto dentro una label
  (`A["Sintassi: @{"]`), perché seguire anche le virgolette di primo
  livello significherebbe far sparire il gate su un sorgente con una
  virgoletta non chiusa. Fino al giro 3 la conseguenza era un 422 solo se
  dopo quel `@{` compariva anche un URL; con la lista chiusa del giro 4
  basta una chiave sconosciuta, quindi ricevono 422 anche
  `A["Sintassi: @{"] --> B` e `A["insieme @{a}"] --> B`, misurati e resi
  correttamente da Mermaid. `A["esempio @{shape: rect}"]` passa, perché
  `shape` è nella lista. Il costo è un errore esplicito e leggibile al
  docente, non una figura che sparisce in silenzio.
- **Quattro falsi positivi aggiunti dall'unione del giro 5**, tutti
  misurati sul pre-render (i sorgenti si rendono davvero) e tutti con un
  422 esplicito che spiega la ragione; il confronto completo dei tre gate
  su 81 sorgenti sani è in sezione 14.8 (8 falsi positivi su 81, erano 4
  con il gate del giro 4 e 2 con quello del giro 3). Il giro 6 non ne
  aggiunge nessuno: sul corpus benigno storico dei verificatori restano
  esattamente questi quattro (sezione 14.9), e le forme con l'apostrofo
  — normali in italiano — passano tutte.
  - `A@{ label: "img: la sorgente" }` e `A@{ label: "icon: la sua icona" }`:
    la scansione `\b(?:img|icon)\s*:` guarda il blocco GREZZO e non
    distingue una chiave da una label che ne parla. È la rete del giro 3,
    rimessa perché è l'unica che tiene le forme che nessuna segmentazione
    spezza; la stessa frase fuori da un blocco `@{ … }`
    (`A["img: sorgente"]`) passa.
  - `A@{ label: f(x, y) }`: le parentesi tonde non proteggono più la
    virgola, perché per js-yaml non sono un indicatore di collezione. Il
    sorgente si rende, ma con la label TRONCATA a `f(x` — è già rotto per
    l'autore, e il messaggio gli chiede di virgolettare (`label: "f(x, y)"`
    passa e rende la label intera).
  - `stateDiagram-v2` con una nota multiriga una cui riga comincia
    esattamente con `click` (`note right of A ⏎ click qui ⏎ end note`): lo
    scanner degli statement non conosce il corpo delle note. La nota su una
    riga sola (`note right of A: click qui`) passa, come passa `A --> B:
    click qui`.
- **Tre forme di tag sfuggono al gate HTML delle label** (REG-1, giro 3):
  misurate sul pre-render, `A[riga<br/x>due]` e `A[riga< br>due]` finiscono
  in chiaro nella label (`<br x="">`, `< br>`) e `A[riga</ br>due]` viene
  tolto in silenzio, ma tutte e tre passano il gate, perché la regola
  generica dei tag vuole un nome di elemento subito dopo `<` o `</` e non
  attraversa la barra. È la stessa classe del `<b` non chiuso già
  dichiarata in sezione 2.2: costo estetico in una label, nessuna risorsa
  esterna e nessuna persistenza di contenuto pericoloso. Allargare la
  regola significherebbe riscriverla come un parser di tag, con il rischio
  di falsi positivi sulle frecce; la direzione è il lexer Mermaid condiviso
  con il frontend.
- **Il render di Mermaid nel browser tocca il documento prima della
  sanificazione; a fermare la richiesta è la politica del browser, che in
  sviluppo non c'è** (SEC-1, giri 5 e 7). Editor e vista lezione rendono
  client-side; dal giro 5 il markup passa da `sanitizeMermaidSvg` prima di
  entrare nella pagina, quindi il nodo che il lettore vede non ha né
  `<image>` né `<a>` verso l'esterno (misurato in Chromium,
  `test_frontend_mermaid_sanitize.py`). La sanificazione però **non può
  arrivare prima della prima richiesta**: `mermaid.render` calcola la
  geometria su un nodo temporaneo che ATTACCA al documento, quindi per una
  shape `img:` la GET parte durante il render. Nessuna opzione di Mermaid
  lo evita: `securityLevel: 'sandbox'` sposta il render in un `<iframe
  sandbox="">` (letto in `mermaid.core.mjs`, `sandboxedIframe`), ma
  l'attributo `sandbox` isola l'origine, non la rete. Il giro 7 lo chiude
  dove conta, cioè nel documento: la pagina servita da
  `frontend/nginx.conf` porta `img-src 'self' data: blob: <origine degli
  upload>` e il browser rifiuta la richiesta (misurato: 4 GET su 4 senza
  header, 0 con header; sezione 14.10). **Resta scoperto lo sviluppo**:
  con `npm run dev` la pagina la serve Vite, che non passa da nginx e non
  ha quell'header, quindi chi scrive un diagramma con una shape `img:`
  nell'editor locale fa partire la GET dal proprio browser (contenuto
  proprio, richiesta propria). Portare la politica anche lì significa
  scriverla in un secondo punto — la configurazione del server di Vite —
  con il rischio che i due divergano: lavoro separato. Il test
  `test_the_render_of_mermaid_itself_still_fetches_declared_limit`
  continua a segnalare il giorno in cui Mermaid cambia comportamento da
  sé. Il nodo temporaneo che il render lascia indietro quando fallisce non
  resta più nella pagina (`renderMermaidSvg`, sezione 14.11): la GET
  precoce, invece, resta il limite qui dichiarato.
- **La politica governa le sole immagini** (SEC-1, giro 8). Non c'è
  `default-src`, quindi `@import` e `<link rel=stylesheet>` (`style-src`),
  `@font-face` (`font-src`), `<iframe>` e `<foreignObject><iframe>`
  (`frame-src`), `<object>` (`object-src`), `<video><source>`
  (`media-src`), `prefetch` e `fetch` (`prefetch-src`, `connect-src`)
  restano liberi: misurato, sette classi di direttiva e dieci costrutti
  passano con la politica attiva, mentre tutto ciò che è immagine è
  bloccato (sezione 14.11). Oggi non è un buco perché con
  `htmlLabels: false` e il lexer dei flowchart Mermaid 11.17.2 non emette
  nessuno di quei nodi — verificato su 22 sorgenti costruiti apposta e sui
  58 maligni che il gate accetta, 0 GET — ma è una superficie residua, non
  solo un vantaggio di compatibilità: se `htmlLabels` tornasse `true` o
  una versione nuova di Mermaid emettesse un `<style>` con `@import`, la
  richiesta partirebbe durante `mermaid.render` e il controllo che
  resterebbe sarebbe `sanitizeSvgCss`, che arriva dopo. Allargare la
  politica a `default-src` è la direzione, ma tocca script, stili, font e
  connessioni di tutta l'applicazione (Google Fonts in `index.html`, il
  CDN di Mermaid, le chiamate all'API): va deciso e misurato a parte.
- **L'origine ammessa dalla politica è derivata da una sola variabile**
  (SEC-1, giro 7). `img-src` aggiunge l'origine di
  `VITE_UPLOADS_BASE_URL`, che è quella da cui il frontend carica le
  immagini caricate dal docente e — con `STORAGE_BACKEND=ovh_*` — anche
  gli avatar, perché `OVH_PUBLIC_BASE_URL` e `VITE_UPLOADS_BASE_URL`
  stanno sullo stesso host. Un deployment che serva le immagini da un
  terzo host (per esempio `STORAGE_BACKEND=local` con `PUBLIC_BASE_URL`
  diverso dall'origine del frontend) vedrebbe quelle immagini bloccate
  finché l'origine non viene aggiunta a mano alla direttiva: la
  derivazione automatica non la indovina. Dichiarato in
  `docs/07-deployment.md` insieme al fatto che cambiare la variabile
  richiede un `docker compose build frontend`, non un riavvio. Il valore
  deve essere un URL assoluto o un path: dal giro 8 spazi ai bordi e
  schema in maiuscolo sono normalizzati e ogni altra forma anomala fa
  **fallire il build** invece di generare una direttiva sbagliata
  (sezione 14.11); resta che la politica e `media.ts` leggono la stessa
  variabile con due funzioni diverse, quindi ogni cambiamento a una delle
  due va fatto guardando l'altra.
  **Fino al giro 3 l'affermazione scritta qui era falsa**: il gate era una
  lista di pattern testuali su un blocco che Mermaid dà a js-yaml,
  `A@{ "\x69mg": "\x68ttp://…" }` veniva ACCETTATO dal PATCH, il payload
  restava in DB e ogni lettore lo rendeva client-side. **Fino al giro 4
  era falsa la riga che dava SEC-1 per chiuso**: il gate del giro 4
  riapriva sette vettori del giro 3 e nessuna sanificazione client
  esisteva. **Fino al giro 5 erano sovrastimate le righe che davano le
  tre difese per «complete»**: questa non lo è, e il verificatore del
  giro 5 lo ha misurato riproducendo la GET del render in Chromium senza
  intercettazioni. Il rimedio simmetrico che manca ancora è portare il
  gate statico anche nel frontend, così che l'editor spieghi al docente
  perché quel diagramma non si potrà salvare.
- **Le chiavi di shape scritte in forme YAML non piane sono rifiutate**
  (SEC-1, giro 4): la lista chiusa ammette una chiave solo in forma piana
  o quotata senza escape, quindi `A@{ ? shape : rect }`, `A@{ "lab\x65l":
  "x" }` e un blocco scalare (`label: |`) prendono un 422 anche se
  Mermaid li leggerebbe. È il verso in cui la lista chiusa deve
  sbagliare — lo stesso del gate del frontmatter — e nessun prompt
  produce quelle forme; il costo è un 422 su una scrittura esotica ma
  legittima, il messaggio elenca le chiavi ammesse.
- **Una curva può attraversare l'alone della formula** (TIP-3): il testo
  delle figure `function` è disegnato sopra le curve con un alone bianco,
  quindi resta sempre leggibile, ma la formula è ancorata a una posizione
  fissa (in alto a destra) e la curva che le passa sotto appare interrotta
  dall'alone — visibile per esempio su `x**2` nella spec `FN_AREA`.
  Sceglierne il quadrante in base ai dati renderebbe il disegno dipendente
  dal campionamento, cioè meno deterministico di quanto A11 richieda: il
  lavoro futuro è una collocazione calcolata una sola volta dai valori
  esatti, non dai punti campionati.
- **La localizzazione può ricadere sulla forma compatta** (I18N-3, giro 2):
  la sostituzione chirurgica conserva la formattazione della spec, ma se la
  traduzione è più lunga dell'originale e farebbe superare il tetto D5 di
  4.000 caratteri a una spec che lo rispettava, `apply_translations`
  riserializza in forma compatta per recuperare quel margine (la
  formattazione si perde, la figura resta valida). Se nemmeno la forma
  compatta basta, la spec localizzata supera il tetto e la figura degrada
  come in A23 in quella lingua.
- **Il rate limit dell'endpoint è per valore di `X-Forwarded-For`**
  (SEC-3): infrastruttura preesistente identica a `main`; non è il cancello
  del carico (autenticazione, `course:edit` e semaforo lo sono).
- **L'etichetta «Figura N.» nella vista segue la lingua dell'interfaccia**
  (I18N-2), come «Summary» e «Key takeaways» del corpo, mentre PDF, slide,
  frame e coda calcolata seguono la lingua del corso: con UI e corso in
  lingue diverse la didascalia della vista è mista. Vale anche per il
  rimando in linea nella prosa della vista slide e della vista discorso,
  aggiunto insieme al rimando delle due superfici PDF: il numero è lo
  stesso ovunque, la parola («Figura», «Figure») segue l'interfaccia solo
  a schermo. Da decidere per l'intera vista, non per la sola figura.
- **I messaggi del 422 delle figure sono in italiano** (I18N-7) come tutti
  i `ValidationAppError` del prodotto, e si mescolano a quelli inglesi di
  jsonschema e vl-convert. Il `type` è stabile: la traduzione lato client è
  possibile senza toccare il backend.
- **«n.d.» non è localizzato** (I18N-4): `format_number(NaN)` e
  `_exact_or_approx` senza valore restituiscono l'abbreviazione italiana
  anche in inglese. Il motore non produce mai quel `computed` (le voci non
  finite sono scartate a monte e le spec con `NaN` rifiutate dalla
  validazione): è un ramo difensivo, fissato da un test.
- **`courses.figures.illustrativeData` non è letta dal codice** (I18N-5): è
  il valore canonico condiviso BE/FE della dicitura che il prompt P3 chiede
  al modello nella lingua del corso (sezione 6.4).
- **Tre voci preesistenti fuori perimetro** (I18N-9): il fallback di rete
  del frontend mostra il messaggio inglese di axios anche con UI italiana,
  il placeholder «[immagine mancante: …]» del PDF resta italiano in ogni
  lingua e il fix AI comprime la lingua del corso a it/en (A4). Tutte e tre
  sono identiche su `main`.
- **I PDF già materializzati non si invalidano da soli** (vincolo del
  committente, branch `fix/asset-refs-math-figure-scale`): un PDF esportato
  prima del branch continua a mostrare la frase spezzata, il LaTeX
  appiattito e le figure fuori scala finché il docente non ri-esporta la
  lezione, dal pulsante «Rigenera PDF» dell'editor o dall'endpoint
  `POST /orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/pdf/export`
  (e i gemelli slide e discorso, più le tre varianti `export-all`).
  `request_lesson_pdf` accetta il ri-export di una lezione già esportata
  (`pdf_status` ∈ `empty`/`ready`/`failed`); non esiste un endpoint che
  cancelli il file materializzato né un percorso di invalidazione in massa.
  Nessun backfill del `content_raw`: la normalizzazione delle liste e i
  rimandi si applicano a render, e lo storico si riscrive solo con una
  rigenerazione o con il primo salvataggio dall'editor (sezione 20.1).
- **La crescita delle figure sotto banda può spostare i salti pagina**: un
  `<img>` intrinseco che cresce fino al fondo della banda è più alto.
  Misura del verificatore su 93 dispense (31 figure in 3 posizioni): il
  branch toglie una pagina in 24 casi e non ne aggiunge in nessuno; in una
  scansione mirata su 80 dispense, le due figure con testo a 4,5-5 pt ne
  aggiungono una in 3 posizioni ciascuna (sezione 20.6).
- **Tre divergenze dichiarate fra PDF e vista web** (sezione 20.7): `$ x_0 $`
  resta prosa nel PDF (`allow_space=False`) ed è math nel frontend; `$$…$$`
  in frase è display style nel PDF e in linea nel frontend; la vista
  conserva il pre-processing testuale dei delimitatori
  (`normalizeMathDelimiters` in `MarkdownRenderer.tsx:70`) che il PDF ha
  eliminato. Tutte e tre sono accettate e rinviate a un ticket separato.
- **Guardia «parola-etichetta» e soglie dei grafi: misurate il 18
  settembre 2026** (sezione 20.3). «Nella Figura [FIG:a]» dà «Nella Figura
  1»: la guardia è attiva perché l'export del docente conta 8 occorrenze
  su 46 tag. Le costanti di `graph_rules.py` restano quelle calibrate sui
  57 modelli degli editor, ora confermate anche sui 20 grafi reali
  dell'export. Residuo: il plurale («Le figure [FIG:a]») non attiva la
  guardia.

## 16. Catalogo dei modelli degli editor (8 settembre 2026)

Richiesta del docente dopo la prova in produzione: «per i grafici
Vega-Lite vedo pochi template, ne possiamo aggiungere altri? Tutti i
diagrammi possibilmente… Mancano parecchi grafici come quelli a torta.
Vorrei che ci fossero proprio tutti». Il menu «Inserisci template…» dei
tre editor testuali passa da 16 a 47 modelli.

| Editor | Prima | Ora | Copertura |
|---|---|---|---|
| `VegaLiteEditor.tsx` | 5 | 24 | otto famiglie d'uso, torta e ciambella comprese |
| `MermaidEditor.tsx` | 7 | 15 | **tutti** i tipi di `MERMAID_D8_TYPES` |
| `DotEditor.tsx` | 4 | 8 | gerarchie, flussi, relazioni, modelli, architetture (→ **18** il 9 settembre, sezione 19) |

**Ordinamento per famiglia d'uso, non alfabetico.** `SourceTemplate`
guadagna `groupKey` (chiave i18n della famiglia) e `FigureSourceEditor`
esporta `TemplateSelect`, il `Select` che raggruppa i modelli consecutivi
con lo stesso `groupKey` in un `SelectGroup` con la propria `SelectLabel`.
Lo usa anche `MermaidEditor`, che ha un layout proprio e non passa da
`FigureSourceEditor`. Le famiglie sono: Vega-Lite — confronto fra
categorie, parte sul tutto, distribuzione, andamento nel tempo,
correlazione, matrice, incertezza, graduatoria; Mermaid — processi e
flussi, struttura e modelli, organizzazione dei concetti, quantità e
ripartizioni, pianificazione e decisione; DOT — gerarchie e alberi,
flussi e dipendenze, relazioni e reti, modelli e strutture, architetture.

**Ogni modello è dimostrato, non presunto** —
`backend/tests/test_frontend_figure_templates.py` estrae i modelli dai
sorgenti TypeScript e li fa passare dal validatore e dal renderer di
produzione: Vega-Lite da `REGISTRY["vegalite"].validate(deep=True)` (schema
v6 + regole D5 + criterio 10 + `vl_convert`), Mermaid dal gate statico D8 e
dal pre-render Chromium con **zero** `<foreignObject>`, DOT da
`REGISTRY["dot"].validate(deep=True)` e dal binario. Il test verifica anche
che ogni modello e ogni famiglia abbiano la chiave in `it.json` **e** in
`en.json`, che nessuna chiave `templates.*` resti orfana nei locale, che i
gruppi siano contigui nel menu e che i quindici tipi D8 siano coperti;
tre controprove (`line` senza `clip`, `image=` in DOT, `journey`) provano
che l'oracolo fallirebbe davvero su un modello rotto. Motivo: un modello
scelto dal menu e poi rifiutato al salvataggio con un 422 è un difetto
peggiore della sua assenza.

Note di merito emerse scrivendo i modelli:

- **Torta e ciambella passano le regole D5**, verificato eseguendo il
  validatore e non deducendolo: `arc` non è fra i mark che richiedono
  `clip` (`line | area | point | trail`) e `theta`/`color` non sono canali
  `x`/`y`, quindi non serve `scale.domain`. La ciambella è lo stesso mark
  con `innerRadius`.
- **Parentesi angolari nelle label Mermaid**: nel flowchart si scrivono
  come entità (`&lt;`), altrimenti Mermaid le legge come tag e cancella il
  testo — difetto visto in produzione su un diagramma con i generici Java.
  Nel diagramma delle classi la forma giusta è quella nativa `List~String~`,
  che Mermaid rende come `List<String>`. Entrambi i modelli portano il caso
  ed entrambi sono fissati da un test sull'SVG reso.
- **Il violino usa `transform.density` con `encoding.column`**: passa le
  regole D5 (nessun `params`, `clip: true` sull'area, `scale.domain` sui
  due assi quantitativi) e sta in 393 px con `width: 140` per faccetta.
  La semidensità è calcolata e disegnata con `x`/`x2`: `stack: "center"`
  centrerebbe la pila sul massimo e non sullo zero, con metà riquadro
  bianco (§18).
- **La mappa di calore dichiara `scale.scheme` sul colore**: il tema
  inietta la sola scala categoriale (`range.category`), quindi senza
  schema esplicito una scala continua userebbe il default di Vega.
- **I dati sono dichiaratamente illustrativi**: ogni modello con dati
  inventati lo dice nella `title` («dati illustrativi») o lo rende
  evidente dai nomi dei campi, come chiede il registro del prodotto.

Non incluso, con il motivo misurato: nessun modello Vega-Lite che tracci
una funzione matematica (`data.sequence` + `calculate`), perché
l'euristica del criterio 10 lo rifiuta per progetto e quel contenuto va nel
formato `function`; nessun tipo Mermaid fuori da D8 (`journey`,
`gitGraph`, `kanban`, `packet-beta`, `architecture-beta`), che il gate
rifiuta.

## 17. Il catalogo nel prompt di generazione (8 settembre 2026)

La seconda metà della richiesta del docente — «tutti i diagrammi
possibilmente, anche in produzione di contenuti e non solo in modifica» —
non si risolve con i modelli degli editor (§16): quelli servono a chi
scrive a mano. In generazione il repertorio lo decide il prompt, e il
prompt di Fase 3 diceva per Vega-Lite soltanto «barre, linee, punti,
aree». Da qui il difetto misurato in produzione: il modello produceva
quasi solo barre e linee, mai una torta, mai una distribuzione, mai una
mappa di calore.

**Che cosa cambia in Fase 3** (`openai_lesson_content_service._system_prompt`):

- la tabella «dal contenuto al formato» nomina ora **tutti e quindici** i
  tipi Mermaid di `MERMAID_D8_TYPES`, ciascuno con il proprio caso d'uso
  fra parentesi (`gantt` pianificazione e dipendenze temporali,
  `sankey-beta` flussi che si ripartiscono fra stadi, `quadrantChart`
  posizionamento su due criteri, `radar-beta` profilo su più criteri,
  `treemap-beta` gerarchia con quantità, `block-beta` architettura a
  blocchi, `xychart-beta` serie breve su assi). Prima ne nominava otto:
  la riga «Tipi ammessi» li elencava tutti, ma senza un caso d'uso
  accanto al nome il modello non li sceglieva;
- la sezione Vega-Lite guadagna il **CATALOGO per famiglia d'uso**, una
  riga per famiglia con i tipi e il costrutto che li produce: confronto
  fra categorie, parte sul tutto, distribuzione, andamento nel tempo,
  correlazione, matrice, incertezza, graduatoria. Le otto famiglie e i
  ventiquattro tipi sono gli stessi del menu degli editor;
- la **torta** è guidata, non solo autorizzata: «poche categorie —
  indicativamente fino a sei — che compongono un intero e hanno quote
  nettamente diverse; con molte categorie o valori vicini le barre
  ordinate si leggono meglio. Mai per confrontare grandezze che non
  sommano a un tutto». Il docente l'ha chiesta, il registro accademico
  del prodotto impone di dire anche quando non si usa;
- l'**esempio** Vega-Lite resta uno solo e resta quello che c'era (barre
  con dati inline, `clip`, `scale.domain`, `axis.title` con l'unità): il
  budget non ne consente un secondo, e l'esempio deve mostrare i vincoli
  che il validatore rifiuta più spesso, non un tipo di grafico esotico
  che il catalogo nomina in una riga.

**Fase 4** (`openai_lesson_slides_service._system_prompt`) crea
`new_assets` senza avere in contesto il prompt di Fase 3: il rinvio
«valgono gli STESSI formati, regole e limiti di Fase 3» non porta con sé
il repertorio. La regola 3 ripete quindi il catalogo in forma breve (le
otto famiglie con i tipi, il criterio della torta, i sette tipi Mermaid
che la riga dei formati non nominava), senza graffe — il prompt di Fase 4
non può contenerne (`test_prompt_composition_bugs.py`). Fase 5 non tocca
i formati: vieta solo di leggere a voce le sorgenti.

**Misure (caratteri) e guardie.** Prima → dopo: P3 con grounding
24.155 → **26.142**, senza grounding 22.703 → **24.690** (il catalogo
pesa 1.987); P4 13.747 con tutti gli argomenti e 13.817 con i default →
**14.583** e **14.653** (pesa 836); P5 invariato, 11.074 e 11.169. Le
guardie di `test_prompt_register.py` salgono alla misura reale della
variante più lunga + ~5%: `MAX_SYSTEM_P3` 25.400 → **27.400**,
`MAX_SYSTEM_P4` 14.500 → **15.400**; `MAX_SYSTEM_P5` resta 12.500. Il
commento sopra le costanti porta i numeri misurati, come le volte
precedenti.

**Il catalogo non promette nulla di non provato.**
`test_frontend_figure_templates.test_the_p3_catalogue_names_every_proven_vegalite_template`
lega le due metà: la mappa `_P3_CATALOGUE_TERMS` deve coprire
esattamente gli id dei modelli estratti da `VegaLiteEditor.tsx` (che
poche righe più sotto passano schema, regole D5, criterio 10 e render con
`vl_convert`) e ogni termine deve comparire nel catalogo del prompt,
insieme al nome italiano di ogni famiglia preso da `it.json`. Un modello
nuovo nell'editor, o un tipo tolto dal prompt, fa fallire il test. In
`test_prompt_figures.py` restano i controlli di presenza: i quindici tipi
Mermaid nella tabella dei casi d'uso con il loro criterio, le otto
famiglie e i quattordici costrutti Vega-Lite del catalogo, il criterio
della torta, il catalogo breve di Fase 4 senza graffe.

## 18. Revisione del catalogo: la figura si giudica RESA (9 settembre 2026)

Due verificatori indipendenti hanno riletto §16 e §17 rendendo davvero i
modelli e leggendo i PDF. Il catalogo era corretto — nessun modello viene
rifiutato al salvataggio — ma «valido» e «leggibile» non sono la stessa
cosa: le prove passavano perché `validate(deep=True)` e `render_svg()`
dicono soltanto che un SVG esiste, non che il lettore ci trovi il testo.
I difetti trovati stanno tutti in quello scarto.

**L'oracolo nuovo: la geometria del testo reso.** Per i tipi Mermaid a
tela fissa si misura in Chromium il bbox reale di ogni `<text>` e lo si
confronta con il `viewBox`
(`test_mermaid_template_keeps_its_text_inside_the_canvas`, con la
controprova su un'etichetta di legenda lunga). Per Vega-Lite si guarda
l'ellissi: il tema tronca le etichette d'asse oltre `labelLimit` e il
testo reso finisce con «…»
(`test_no_vegalite_label_is_truncated_by_the_theme`, con la controprova
su un'etichetta oltre il limite). Sono controlli che un difetto di resa
lo vedono; l'ispezione a occhio dei render, che il commit precedente
dichiarava, non lo aveva visto.

**Correzioni nel TEMA — valgono anche per le figure generate dal
modello**, che è la metà della richiesta del docente che i modelli degli
editor non coprono. `THEME_VERSION` sale a `2026.09.4` (la chiave di
cache degli SVG cambia, come per ogni modifica visibile).

| Difetto misurato | Correzione | Prova |
|---|---|---|
| `axis.labelLimit: 120` taglia le categorie oltre ~22 caratteri: le barre **orizzontali** — il tipo che esiste apposta per le etichette lunghe — uscivano con «Esercitazion…», «Studio indiv…» | `axisY.labelLimit: 220` (l'asse y porta le categorie per esteso; sull'asse x restano numeri ed etichette brevi) | i quattro testi tornano interi; nessuno dei 24 modelli resi contiene «…» |
| Radar: tela 700×700 con margini di 50 px, l'etichetta dell'asse di sinistra e la legenda di destra fuori dal `viewBox`, e nel PDF si leggeva «ittura» e due voci entrambe «Rilevazione» | `radar: {marginLeft: 100, marginRight: 240}` (il margine destro ospita la legenda) | overflow massimo da 30,5 px a 0; PDF WeasyPrint rileggibile, con 17% di margine sull'etichetta più lunga |
| `sankey-beta` scrive nome e valore in un solo `<text>` separati da un a capo: la specifica SVG dice di RIMUOVERE i fine riga, WeasyPrint lo fa («Lezioni48») e Chromium no («Lezioni 48») | `_join_mermaid_text_newlines` nel post-processing del pre-render, accanto a `_strip_mermaid_max_width` | il PDF legge «Lezioni 48», «Esercitazioni 24»; no-op byte per byte sulle due fixture storiche |

Il margine del radar è misurato, non scelto a occhio: con 200 px la
legenda resta a 7 px dal bordo e il font di WeasyPrint (più largo di
quello di Chromium, che ha deciso l'impaginazione) mangiava l'ultima
lettera; con 240 px il margine è il 17% della larghezza del testo. Resta
noto e tollerato un solo sconfinamento: Mermaid disegna il **titolo** del
radar a filo del bordo superiore (`y = -altezza/2`,
`dominant-baseline: hanging`), quindi l'em box sporge di ~2,6 px anche
nel campione ufficiale D8; rasterizzando, la prima riga di pixel è vuota
— l'inchiostro sta dentro. La tolleranza del test è 4 px.

**Correzioni nei MODELLI**, ognuna verificata sull'SVG reso:

- **ordine delle categorie** (`heatmap`, `barsStacked`, `barsNormalized`,
  `pie`, `donut`): senza `sort` Vega-Lite dispone i domini nominali e
  ordinali in ordine alfabetico, e l'orario delle lezioni usciva
  «Giovedì, Lunedì, Martedì…» con le fasce «11-13, 14-16, 9-11». Ora i
  cinque modelli dichiarano `sort` — elenco esplicito dove l'ordine è
  cronologico o logico, `{"field": …, "order": "descending"}` sulla torta
  e sulla ciambella — e il test confronta l'ordine dei testi **nell'SVG
  reso**, non la presenza di `sort` nella sorgente. Sulla torta la misura
  ha corretto anche la correzione: `color.sort` mette in ordine la
  LEGENDA, ma gli spicchi restano sparsi (86,4° → 57,6° → 172,8° →
  43,2°) e `sort` su `theta` li scompiglia; l'ordine angolare lo decide
  il canale `order`, e il test lo verifica sulla geometria degli archi
  resi (ampiezze non crescenti girando in senso orario da ore 12);
- **`barsWithError`**: il secondo layer codificava `y` sul campo `lo`
  senza `axis.title`, e Vega-Lite fondeva i titoli dei due layer in
  «Media (unità), lo» — il nome interno di un campo esposto al lettore.
  Con `axis.title` sul layer e `ticks: {"size": 12}` i testi resi sono
  `['A', 'B', 'C', 'Gruppo', 'Media (unità)', …]` e le barre d'errore
  hanno cappucci normali invece di una riga a tutta banda;
- **`violin`**: `stack: "center"` centra la pila sul massimo, non sullo
  zero, e i violini uscivano appoggiati al bordo destro con metà riquadro
  bianco. Ora la semidensità è calcolata (`datum.density / 2` e il suo
  opposto) e disegnata con `x`/`x2`: i due violini sono simmetrici sullo
  zero (centro misurato 70,0 px su una faccetta larga 140);
- **`treemap`**: Mermaid deduce il corpo del testo dall'**altezza** della
  piastrella e poi lo riduce fino a farlo stare in larghezza con ~10 px
  di margine — un margine così sottile si perde appena il font del PDF
  ha metriche diverse da quello con cui Chromium ha impaginato. Le
  quantità del modello passano da 48/12/24/16 a 40/20/20/20 (sempre 100
  ore): stesse etichette accademiche, margine minimo da 10,5 a 27,1 px.

**Correzioni nei PROMPT.** Le clausole nuove non sono raccomandazioni:
ognuna nasce da una spec che passa `validate(deep=True)` e rende una
figura illeggibile, e ognuna ha il test che lo dimostra
(`test_prompt_figures.py`, sezione «lettura letterale»).

- **dominio dei valori derivati**: nei quattro tipi in cui il valore
  dell'asse quantitativo non è nei dati scritti dal modello
  (normalizzate, istogramma, scatola, violino) `scale.domain` è
  obbligatorio ma nessuno diceva come sceglierlo, e `bar` non è fra i
  mark con `clip` obbligatorio. Misurato: `stack: "normalize"` con
  dominio [0, 100] è **valido** e rende un asse «0%, 2000%, … 10000%»;
  con `aggregate: "count"` e un dominio più corto del massimo effettivo
  le barre escono dal riquadro (ordinata −110 px su un'area alta 433);
- **ordine delle categorie**: la stessa regola dei modelli, perché il
  difetto è dell'ordinamento di default di Vega-Lite e vale per ogni
  figura generata;
- **legenda**: «solo con più serie» era scritta per i grafici a x/y; su
  `arc` e `rect` la legenda è l'unico canale che nomina i dati, e la
  lettura letterale produceva quattro spicchi colorati anonimi (SVG con
  un solo testo, il titolo, contro sei con la legenda);
- **onestà dei dati**: la clausola «fonte nella caption oppure "Dati
  illustrativi, non sperimentali"» stava dentro il paragrafo Vega-Lite,
  mentre la tabella raccomanda sei tipi Mermaid quantitativi (`pie`,
  `xychart-beta`, `sankey-beta`, `treemap-beta`, `radar-beta`,
  `quadrantChart`): la stessa ripartizione 60/40 inventata era dichiarata
  illustrativa con `arc` e muta con `pie` di Mermaid. Ora è una riga sola
  nel blocco «FORMATI DELLE FIGURE», per tutti e quattro i formati, e
  vale anche in Fase 4;
- **DOT aveva il catalogo mancante e un divieto sbagliato**: il prompt
  diceva «nessun colore, font o stile», ma cinque degli otto modelli
  provati si reggono su `shape` (`doublecircle` per uno stato accettante,
  `Mrecord` per una struttura dati, `point` per l'ingresso di un automa)
  e il validatore le ammette. Un automa generato usciva con tutti gli
  stati identici: non impoverito, con la notazione sbagliata. Ora il
  divieto è «nessun colore né font; `shape` SOLO quando porta
  significato» e il paragrafo elenca gli otto tipi, legati ai modelli da
  `test_the_p3_paragraph_names_every_proven_dot_template`;
- **limiti dei tipi quantitativi Mermaid** nella tabella: `sankey-beta` è
  l'unico tipo con colori propri (schemeTableau10 di d3, hard-coded nel
  renderer, §5) e va usato «solo quando il flusso è il contenuto»; il
  radar vuole etichette brevi, il treemap quantità confrontabili;
- **`data.values` nel blocco LINGUA**: nei tipi che il catalogo
  raccomanda (torta, ciambella, barre ordinate, mappa di calore) le
  categorie SONO l'etichettatura, e stanno lì; l'elenco «in particolare»
  — dove il modello guarda — non le nominava;
- **Fase 4** nominava otto tipi Mermaid ammessi e nessuno dei cinque
  esclusi, pur creando `new_assets` senza il prompt di Fase 3 in
  contesto: `journey` è la scelta naturale per una slide sul «percorso
  dello studente», emette `<foreignObject>`, viene rifiutato dal gate e
  costa un giro di riparazione. L'elenco arriva dalla stessa costante di
  Fase 3 (`MERMAID_EXCLUDED_TYPES`), non da una lista riscritta a mano.

**Misure (caratteri) e guardie.** P3 con grounding 26.142 → **27.236**,
senza grounding 24.690 → **25.817**, con ruolo/stile/EQF interpolati
**27.289** (la variante più lunga); P4 **15.100** in entrambe le
varianti; P5 invariato. Le correzioni pesano 1.127 caratteri in P3 e 447
in P4. `MAX_SYSTEM_P3` sale da 27.400 a **27.700** (~1,5% di margine
sulla misura reale); `MAX_SYSTEM_P4` resta 15.400 e `MAX_SYSTEM_P5`
12.500. Il commento sopra le costanti porta i numeri misurati.

**`docs/PROMPTS.md` non è più una promessa.** Il repository aveva già il
confronto meccanico (`backend/scripts/check_prompts_md.py`), ma finché
restava un comando da lanciare a mano la deriva passava in silenzio: il
documento aveva perso tre righe del PROMPT 12 (il divieto di risorse
esterne nelle shape `@{ ... }`), aggiunte al codice diversi commit prima.
Ora `tests/test_prompts_md_matches_code.py` invoca la stessa funzione di
confronto dello script e fallisce sul diff, con la controprova che tolta
una riga il confronto la vede. I nove blocchi sono riallineati
rigenerandoli dai renderer dello script, non trascrivendoli a mano.

## 19. I dieci grafi che mancavano a DOT (9 settembre 2026)

Richiesta del docente dopo §16: l'editor Vega-Lite offre 24 modelli e
quello Mermaid 15 (tutti i tipi ammessi da D8), «per i grafi ne vedo solo
otto, vorrei il catalogo completo anche lì». Il menu di `DotEditor.tsx`
passa da 8 a **18** modelli; i tre editor testuali da 47 a **57**.

| Modello | Famiglia | Che cosa mostra |
|---|---|---|
| `parseTree` | gerarchie e alberi | albero di derivazione di una frase (`ordering=out` tiene le foglie nell'ordine delle parole) |
| `taxonomy` | gerarchie e alberi | tassonomia a due livelli |
| `callGraph` | grafi orientati e dipendenze | grafo delle chiamate, con una funzione richiamata da due rami |
| `weighted` | grafi non orientati e reti | grafo pesato: le distanze fra le sedi del campus |
| `bipartite` | grafi non orientati e reti | abbinamento studenti/relatori, i due insiemi in ranghi distinti (`rank=same`) |
| `network` | grafi non orientati e reti | topologia di rete, con la base di dati in `shape=cylinder` |
| `hashTable` | automi e strutture dati | tabella hash: bucket in `Mrecord` con le porte e una catena di trabocco |
| `layers` | architetture | architettura in tre livelli, un `cluster` per livello |
| `shortestPath` | cammini e flussi | cammino minimo in evidenza con `penwidth` e non col colore: si legge anche in bianco e nero |
| `flowNetwork` | cammini e flussi | rete di flusso, portata e capacità sugli archi (`8/10`) |

**Le famiglie del menu restano l'ordinamento, non l'alfabeto** (§16):
alle cinque esistenti si aggiunge `paths` («Cammini e flussi» / «Paths
and flows»), e tre etichette diventano il nome del tipo di grafo che
contengono — «Grafi orientati e dipendenze», «Grafi non orientati e
reti», «Automi e strutture dati». Le chiavi i18n non cambiano, quindi
nessuna traduzione resta orfana; l'ordine è gerarchie → grafi orientati →
grafi non orientati → automi e strutture → architetture → cammini.

**Il difetto che l'occhio non aveva visto: `layers`.** Nella prima
stesura i due nodi di ogni livello stavano impilati in colonna e
l'etichetta del `cluster` era centrata sopra di essi: la freccia che
entra nel livello scendeva esattamente lungo quella colonna e
**attraversava le parole** «Livello applicativo» e «Livello di
persistenza» (misurato: 6,2 px dentro il riquadro del testo, cioè in
mezzo alle lettere). La figura era valida, resa e sbagliata da leggere.
Correzione in due mosse, entrambe nel sorgente del modello: `rank=same`
mette i due nodi del livello in riga — il `cluster` diventa largo e il
diagramma prende la forma a gradini che si legge meglio in colonna di
testo — e `labeljust="r"` porta il nome del livello fuori dalla colonna
delle frecce. Provate anche, e scartate perché l'arco continua a passare
sul testo: la sola `labeljust="l"`, il nome del livello accorciato,
`labelloc="b"`.

**L'oracolo di leggibilità, esteso a DOT.** §18 aveva dato a Mermaid e
Vega-Lite una misura sull'SVG reso; DOT restava giudicato da
`validate(deep=True)` e da `render_svg()`, che dicono solo che un SVG
esiste. Ora `test_dot_template_is_legible_once_rendered` misura in
Chromium, per tutti e diciotto i modelli, il bbox reale di ogni `<text>`,
il riquadro del nodo o del `cluster` che lo possiede e il tracciato di
ogni arco, e rifiuta quattro difetti: testo fuori dalla tela, testo fuori
dal proprio nodo, etichette sovrapposte, arco che attraversa
un'etichetta. La controprova
(`test_the_dot_geometry_check_would_catch_an_edge_across_a_label`) rende
la versione di `layers` precedente alla correzione e verifica che
l'oracolo la bocci: senza, il controllo sarebbe una formalità. I dieci
sorgenti sono stati resi e **guardati** uno per uno prima di entrare nel
menu; l'unico che l'occhio aveva approvato e la misura ha ripreso è
`layers`.

**Prompt di Fase 3 e guardia.** Il paragrafo «DOT (Graphviz)» nomina i
tipi nuovi senza spiegarli — un elenco, non un manuale — e passa da 620 a
**901** caratteri (+281). Misure del system prompt: P3 27.236 → **27.517**
con grounding, 25.817 → **26.098** senza, 27.269 → **27.550** con
ruolo/stile/EQF interpolati (la variante più lunga); P4 e P5 invariati,
il paragrafo DOT sta solo in Fase 3. Con `MAX_SYSTEM_P3 = 27.700` il
margine sarebbe sceso a 150 caratteri (0,5%): la guardia sale a
**28.900**, cioè la misura reale + ~5%, e il commento sopra la costante
porta i numeri. Il legame fra le due metà resta meccanico:
`test_the_p3_paragraph_names_every_proven_dot_template` chiede
l'uguaglianza fra gli identificativi dei diciotto modelli e i termini che
il paragrafo nomina, così un modello nuovo nell'editor obbliga ad
aggiornare il prompt e viceversa. `docs/PROMPTS.md` è riallineato con
`backend/scripts/check_prompts_md.py` (9/9 blocchi identici al codice).

## 20. Riferimenti, LaTeX e leggibilità delle figure (16-17 settembre 2026)

Branch `fix/asset-refs-math-figure-scale`, otto commit su `main`. Il punto
di partenza non è una misura interna: sono **quattro difetti che il
docente vede sul prodotto finito**, tutti nel tratto `content_raw` → PDF.
Un asset citato più volte nel testo veniva reso più volte e la frase che
lo citava usciva spezzata in due paragrafi; il LaTeX della prosa arrivava
nel PDF crudo o appiattito; il testo delle stesse figure era a 19,8 pt in
dispensa e a 3,2 pt nelle slide; diagrammi densi o con archi incrociati
passavano tutte le validazioni perché nessuna misura li guardava.

Criterio di riuscita fissato prima di scrivere codice: **per ogni
patologia esiste un test che fallisce se torna**. Vincoli del committente
non ridiscutibili, riportati qui in chiaro perché governano tutto il
capitolo: nessun backfill del `content_raw`; figure entro i margini; il
revisore AI figura ↔ testo si fa, con il costo contabilizzato;
`THEME_VERSION` invariato (`figure_theme.py:37`, `2026.09.4`) e nessun SVG
riscritto — la larghezza va sul wrapper, mai dentro l'SVG; stringhe UI
solo in `it.json` ed `en.json`; **i PDF già materializzati non si
invalidano da soli**.

**I PDF già materializzati restano come sono.** Nessuna delle otto
modifiche tocca i file PDF esistenti: un PDF ha il testo e le figure del
momento in cui è stato reso e continua ad averli finché non viene
ri-esportato a mano. Le vie di ri-export verificate sul codice sono due,
ed è la stessa: il pulsante «Rigenera PDF» dell'editor
(`courses.lessonsPdf.lesson.regenerate` in `it.json`) e l'endpoint che
chiama,
`POST /api/v1/orgs/{org_id}/courses/{course_id}/lessons/{lesson_id}/pdf/export`
(`app/api/v1/courses.py:2282`), con i gemelli `…/slides-pdf/export`,
`…/speech-pdf/export` e le tre varianti `export-all` di corso.
`request_lesson_pdf` accetta `pdf_status` ∈ `empty`/`ready`/`failed`, cioè
il ri-export di una lezione già esportata è ammesso e riparte dal worker;
rifiuta solo `pending`/`processing`. **Non esiste** un endpoint che
cancelli il file materializzato né un percorso che invalidi i PDF in
massa: l'aggiornamento è per lezione, su richiesta del docente.

### 20.1 I quattro difetti e la garanzia che li tiene fuori

| # | Che cosa vedeva il docente | Garanzia | Test che fallisce se il difetto torna |
| --- | --- | --- | --- |
| 1 | Asset citato N volte reso N volte, con la frase spezzata in due paragrafi | La citazione in linea diventa il rimando testuale «Figura N»; l'ancora è inserita una sola volta dopo il blocco della prima citazione | `tests/test_lesson_pdf_figures.py::test_inline_citation_keeps_the_sentence_and_anchors_the_figure_after_the_paragraph` (frase intera, un solo `<figure>`, nessun `[FIG:A]` nell'HTML); parità della fixture in `tests/test_asset_ref_normalize.py::test_every_handled_id_has_exactly_one_anchor_and_no_inline_tag` |
| 2 | LaTeX grezzo o appiattito nel PDF | La grammatica del math è solo la catena di rule di markdown-it, condivisa da renderer e collector; nessun pre-processing testuale | `tests/test_lesson_pdf_math.py::test_math_corpus_pdf_text_has_no_latex_residue` (testo estratto dal PDF senza `\comando`, `$$`, `{,}`, `^{`, `_{`), con `::test_all_four_dollarmath_rules_are_ours`, `::test_no_textual_math_preprocessing_remains` e `::test_weasyprint_non_rende_mathml` |
| 3 | Testo delle figure fuori scala (19,8 pt in dispensa, 3,2 pt in slide per lo stesso diagramma) | La larghezza viene dal corpo del testo più piccolo dell'SVG e dalla banda di leggibilità: 8-11 pt dispensa e web, 10-14 pt slide e video | `tests/test_lesson_pdf_figure_text_size.py::test_figure_text_size_in_the_lesson_pdf` e `::test_figure_text_size_in_the_slides_pdf` (WeasyPrint `.render()`, corpo misurato sui box reali), controprova `::test_the_oracle_sees_the_pre_d10_geometry` |
| 4 | Diagrammi densi o con archi incrociati senza nessuna misura che lo rilevi | Gate editoriale sul sorgente (nodi, archi, etichetta, titolo, righe, caratteri) e misura geometrica degli incroci e dei difetti di lettura sulla resa | `tests/test_graph_rules.py::test_the_failing_oracle_is_now_rejected` e `tests/test_figure_geometry.py::test_dot_templates_have_no_python_defects_and_hashtable_has_one_crossing` (parità Python/Chromium sui 18 DOT) |

Alla stessa famiglia appartiene la garanzia del box delle slide, che non
nasce da un difetto segnalato ma dalla misura: il cap fisso di 80 mm
tagliava le figure. L'oracolo è
`tests/test_slide_figure_geometry.py::test_rendered_slides_never_overflow_the_body`
con la controprova `::test_the_oracle_catches_the_old_80mm_constant`, che
rimette la costante e **deve** vedere lo sbordo.

### 20.2 Le sei decisioni (B1-B6)

Sei questioni, ciascuna con due proponenti a lente diversa (coerenza col
repo, robustezza e testabilità), tre revisori (architettura, avversario,
regressione) e un sintetizzatore; B1-B5 vengono dal brief, **B6 è stata
aggiunta** dopo le misure di §3(e) ed è la prima deviazione dichiarata dal
perimetro iniziale. Ogni sintesi ha eseguito prototipi sulla catena reale
(markdown-it 4.2.0, react-markdown 10.1.0, WeasyPrint 69, Chromium): i
valori attesi delle fixture sono calcolati, non stimati.

**B1 — rimando testuale in linea, ancora dopo il blocco** (D1, D2, D4;
3/3 per l'ibrida). Per un id numerato con sole citazioni in linea, il
normalizzatore converte ogni citazione nel rimando testuale («Figura 1»,
senza punto, lingua del corso) e inserisce **una** ancora `[FIG:id]` su
riga propria subito dopo il blocco che contiene la prima citazione. Il
blocco sono le righe contigue non vuote; i fence e i blocchi `$$…$$`
chiusi sono unità opache (`dollarmath` ha `allow_blank_lines=True`, quindi
una riga vuota interna non li chiude); se la prima riga è un item di
lista, il blocco si estende all'intera lista. Superficie: modulo puro
`asset_ref_normalize.py`, mirror `frontend/src/lib/assetRefNormalize.ts`,
fixture condivisa `asset_ref_normalize_cases.json`.

*Alternative scartate.* (a) Promuovere la prima citazione a blocco «come
oggi» (alternativa b del brief): con 0 asset su 17 citati due volte nelle
due dispense reali e 8 tag su 8 in linea nella lezione e2e, e senza
backfill, avrebbe lasciato la frase spezzata su **tutto** il contenuto
esistente; l'ancora dopo il blocco ripara le dispense già generate senza
toccarle. (b) Lasciare i tag byte-identici dentro fence e code span:
`_substitute_asset_refs` e `preprocessAssetRefs` sono sostituzioni
globali, quindi il tag sarebbe diventato comunque un secondo `<figure>` o
markup escapato nel `<pre>` (provato su markdown-it e react-markdown);
«Figura N» nel codice è il male minore, e i tag sono convenzione interna
che il lettore non deve vedere. (c) Regola pura «prima riga vuota» senza
l'estensione alla lista: spezza la lista in due `<ul>`/`<ol start="2">` su
entrambi i parser. (d) Regola dedicata agli heading: una regex in meno da
specchiare, caso pinnato in fixture. (e) Template `{{n}}` passato al
modulo puro invece della callable `reference(kind, id, n)`: duplicava
`_interpolate` di `figure_theme` e nel frontend avrebbe richiesto
`t(key, { n: "{{n}}" })`, che regge solo per il default `skipOnVariables`
di i18next. (f) Guardia «parola-etichetta» («Nella Figura [FIG:a]» →
«Nella Figura Figura 1»): esclusa dal primo rilascio con 0 occorrenze
misurabili e nessun `content_raw` reale in locale, **attivata il 18
settembre 2026** sull'export del docente, che la conta 8 volte su 46 tag
(§20.3(b)); vedi il capoverso qui sotto.

**La guardia «parola-etichetta» (18 settembre 2026).** Quando la parola
dell'etichetta precede già il tag sulla stessa riga, a meno di spazi e su
parola intera, il rimando emette il **solo numero**: «La figura
[FIG:x] rappresenta…» diventa «La figura 1 rappresenta…» e non «La figura
Figura 1 rappresenta…». La parola non è un elenco italiano scritto a
mano: è il testo che la callable `reference` stessa mette prima del
numero, cioè la chiave i18n del rimando (`courses.figures.*.ref`), quindi
la regola vale identica in italiano e in inglese e, per la famiglia
teorema, usa la parola del teorema («il lemma [EQ:x]» → «il lemma 2»). Il
confronto ignora le maiuscole e ha un confine di parola a sinistra
(classe esplicita, perché `\w` è unicode in Python e solo ASCII in
JavaScript): «Il sistema si configura [FIG:a]» resta «si configura Figura
1». La guardia vive in `_reference_text` del modulo puro, quindi vale
ovunque il rimando in linea sia prodotto — `normalize_asset_refs` e
`cite_asset_refs`, cioè dispensa, slide, discorso e le tre viste — ed è
specchiata in `assetRefNormalize.ts` e pinnata nella fixture condivisa,
che entrambi i lati eseguono. Limiti dichiarati, quattro, tutti pinnati
in fixture: (1) il confronto è sulla parola **completa**, quindi il
plurale non corrisponde («Le figure [FIG:a]» → «Le figure Figura 1»); (2)
un segno di punteggiatura fra la parola e il tag annulla la guardia («La
figura, [FIG:a]»); (3) «a meno di spazi» è il solo `[ \t]` — non la
classe larga `_WS` usata altrove nel modulo — quindi uno spazio
unificatore (U+00A0) fra parola e tag disattiva la guardia e la
ripetizione sopravvive («La figura Figura 1»); (4) la regola è lessicale
e non distingue il sostantivo dal verbo omografo, quindi «Il ciclo
completo figura [FIG:a] a pagina seguente» diventa «… figura 1 a pagina
seguente»: è l'unico caso in cui la guardia rende l'uscita meno
informativa di prima, perché il rimando degenera in una cifra senza
etichetta. Aggiunta a (4), la parola incollata al tag («La figura[FIG:a]»)
dà «La figura1», malformata quanto l'ingresso. Frequenza dei limiti (3) e
(4) e della parola incollata nell'export del docente: **0 occorrenze** per
ciascuno (§20.3(b)), misurate con `re.findall` su `content_raw` — U+00A0
davanti a un tag 0, `(figura|tabella|equazione|esempio)[KIND:` 0, e tutte
e 8 le occorrenze della parola-etichetta hanno l'articolo davanti («La
figura», «La tabella»), mai il verbo.

**B2 — le etichette nuove sotto un prefisso già presidiato** (D3, D5;
3/3 per l'ibrida su base A). Tutte le chiavi nuove stanno sotto
`courses.figures.`, come sotto-oggetti per famiglia: 12 per lingua
(24 → 36), specchiate in `figure_theme.FIGURE_I18N` it/en e in
`it.json`/`en.json`. La composizione avviene in un solo punto
(`figure_theme.asset_label` / `asset_ref`); il ramo teorema è la famiglia
`THM` sul contatore `EQ`, decisa da `equation_label_family`.

| chiave | it | en |
| --- | --- | --- |
| `courses.figures.ref` | `Figura {{n}}` | `Figure {{n}}` |
| `courses.figures.table.{label,labelUnnumbered,ref}` | `Tabella {{n}}.` · `Tabella.` · `Tabella {{n}}` | `Table …` |
| `courses.figures.equation.{label,labelUnnumbered,ref}` | `Equazione {{n}}.` · `Equazione.` · `Equazione {{n}}` | `Equation …` |
| `courses.figures.example.{label,labelUnnumbered,ref}` | `Esempio {{n}}.` · `Esempio.` · `Esempio {{n}}` | `Example …` |
| `courses.figures.theorem.{label,ref}` | `{{kind}} {{n}}.` · `{{kind}} {{n}}` | idem |

*Alternative scartate.* (a) Sottoalberi separati `courses.tables.*`,
`courses.equations.*`, `courses.examples.*`: il perimetro presidiato è un
prefisso unico (`_PREFIX = "courses.figures."` nel test di specchio);
tre prefissi avrebbero moltiplicato `FIGURE_I18N`, il test e le
asserzioni, e per il teorema avrebbero chiesto un quarto sottoalbero
`courses.theorem.*` già occupato da chiavi di `_labels_for`; stesso costo
in chiavi (36), robustezza minore. (b) Rimando derivato a runtime da
`label.rstrip(".")`: una derivazione invisibile al traduttore e al test di
parità dei segnaposto; il rimando è una chiave esplicita e la coerenza
«`ref` + "." == `label`» è un oracolo di test. (c)
`theorem.labelUnnumbered = "{{kind}}."`: avrebbe prodotto «Teorema. di
Pitagora» in ogni slide, frame video e vista slide; senza numero resta la
sola parola del kind e le slide sono byte-identiche. (d) Stringhe in
`_labels_for` o nel servizio slide: vietato da D5 e senza test di
specchio. (e) Chiavi i18n composte nel frontend (`t(assetLabelKey(…))`):
sfuggono alla guardia `test_literal_t_keys_resolve_in_locale`; le chiavi
restano letterali nei componenti. (f) Sostituire `MarkdownRenderer` con
`TableBlock`/`ExampleBlock` nelle slide: perderebbe
`normalizeMathDelimiters` e i `\(…\)` nelle celle. (g) **Strip del
prefisso numerico per kind** («Tabella 1: Confronto» → «Confronto»):
provato **lossy** — «ES6: arrow functions» → «arrow functions»,
«Formula 1: la corsa» → «la corsa», «Definizione 2: Limite» → «Limite»;
rinviato, variante stretta disponibile come questione separata.

**B3 — una sola grammatica per il math del PDF** (D6, D7, D8; 3/3 per
l'ibrida con struttura A). La grammatica è **solo** la catena di rule di
markdown-it, condivisa da renderer e collector: `dollarmath` con
`allow_labels=False`, `double_inline=True`, `allow_space=False`,
`allow_digits=True`
(`course_lesson_pdf_service.py:372-377`); una core rule
`math_currency_guard` prima di `text_join` declassa a testo gli importi;
quattro `add_render_rule` legate a un unico `_render_math_token`;
`math_inline_double` ha sempre chiave `(sorgente, "block")` — un solo SVG
per formula — e markup deciso dalla forma (span a blocco con i
delimitatori su righe proprie, span in linea altrimenti), mai un `<div>`
dentro `<p>`/`<td>`/`<h2>`. In WP2 la rule inline `math_bsdelim` sostituisce
`_normalize_math_delimiters`, eliminata.

*Alternative scartate.* (a) Due grammatiche allineate a mano (D8-B): la
guardia «niente lettere» del validatore esclude la notazione decimale
italiana (`$15{,}9$`, `$0{,}866$`) e le potenze numeriche (`$2^{10}$`), e
avrebbe imposto una seconda regex nel servizio PDF, cioè due grammatiche
da tenere uguali a ogni release del plugin. (b) Pre-processing testuale
«fence-aware» per `\(..\)` e `\[..\]`: sul corpo assemblato l'HTML degli
esempi (`<pre><code>`) e il fallback delle figure non sono fence e
verrebbero riscritti (`a\[0\]` → `a$$0$$`, misurato); i limiti di distanza
sarebbero costanti magiche. Una rule inline limitata al paragrafo non
vede né fence né code span né HTML block, e rifiuta `\[1\]` e `\[FIG:x\]`.
(c) Guardia anti-currency dentro la rule di render con predicato di solo
contenuto: andrebbe replicata nel collector e non vede i vicini del token
(`US$50 e US$70` diventava math «50 e US», con perdita di testo). Il
predicato adottato è l'**unione** di «importo + separatore» e «cifra
adiacente fuori dai delimitatori»: la sola regex di A lasciava passare
`US$50 e US$70`, la sola adiacenza di B lasciava `5$, 10$`. (d)
`math_inline_double` sempre a blocco: spezza `Sia $$E$$ la relazione.`,
che il frontend tiene in linea; sempre inline: due SVG per la stessa
formula e disallineamento dal collector. (e) Renderer inline su preset
commonmark: markdown-it escapa `"` come `&quot;` (i golden usano `&#34;`) e
introduce enfasi, link e code nelle didascalie che il frontend rende
letterali; adottato il preset `zero` più la grammatica math e una rule
`text` markupsafe, byte-identica a `markupsafe.escape` senza math. (f)
`allow_digits=False`: perde `la base 2$^{10}$` e `2$\pi$`, e i falsi
positivi che toglie sono già coperti dalla core rule.

**B4 — `base_font_px` si misura, non si assume** (D10, D11; 2 A + 1
ibrida → opzione iii). Per Mermaid il corpo del testo lo misura Chromium
nella **stessa pagina** del pre-render (`__renderMermaidMeasured`, una
sola `page.evaluate`); per DOT, Vega-Lite e `function` lo legge Python
dagli attributi e dal foglio di stile (`svg_base_font_px`, con provenienza
`measured` / `parsed` / `root_rule` / `constant`). Le metriche viaggiano
accanto all'SVG in `RenderedFigure`, valore della cache LRU; la larghezza
è l'ultimo attributo `style="width:Wmm"` del corpo della figura.

*Alternative scartate.* (a) Regola radice dello `<style>` più una
costante: falsa in 8 tipi su 15 — Mermaid scrive `#mmd-N{font-size:14px}`
per tutti e poi dimensiona `sequence` a 16 px con `style` inline,
`gantt`/`quadrant`/`xychart` con l'attributo, `treemap` da 10 a 38 px,
`timeline` con `4ex`, e `pie` (17/25) e `radar` (12/14) con regole di
classe che nessuna regex sul tag vede. Gli 8 sono il complemento della
2(c), dove la radice predice il font reale in 7 tipi su 15; il conteggio
più basso che circolava nelle sintesi (6) misura un'altra cosa, cioè i
tipi in cui la costante sbaglia il font **minimo** dell'SVG, e non è il
numero che serve qui perché la banda di leggibilità va garantita su tutti
i testi della figura. Tenuta come **secondo** canale, marcata
`source="root_rule"`. (b) Pin per tipo misurato una volta: una
tabella da rimisurare a ogni bump di `mermaid_cdn_version`, cieca ai testi
di dimensione mista nello stesso SVG, e comunque da accompagnare a un
parser per gli altri formati. (c) Scala naturale anche per gli SVG
fluidi: porta il flowchart D8 a 10,5 pt quando 11 e 14 sono disponibili.
Adottato «riempi il box, poi riduci al tetto della banda» per i fluidi e
«scala 1, cresci solo fino al fondo della banda» per gli `<img>`
intrinseci. (d) Cambiare il ritorno di `window.__renderMermaid` in
`{svg, metrics}`: rompe due test che lo chiamano direttamente e si
aspettano una stringa. (e) Metodo nuovo nel `Protocol FigureRenderer`:
obbligherebbe i quattro fake dei test e ogni renderer registrato;
dispatch con `getattr`. (f) Kill-switch `figure_fit_enabled`: un setting da
documentare per un comportamento che deve essere unico. (g) `clientWidth`
via ref nel frontend: vale 0 nei pannelli chiusi dell'editor e non reagisce
al resize; adottato `width: min(100%, Wpx)` su un wrapper interno senza
padding. (h) Font per superficie nel tema, per portare Vega-Lite in banda
nelle slide: cambierebbe `THEME_VERSION`. (i) Riscrivere l'SVG con
`width`/`height` in mm sulla radice: vietato dal committente; la larghezza
sta sul wrapper e la catena Mermaid resta byte-identica.

**B5 — nessuna dedup in lettura** (D18; A, B e ibrida convergono sul
codice di A). `key_takeaways` e `references` si normalizzano al solo
confine di **scrittura**, in un solo file
(`app/schemas/course_lesson_content.py`): trim, voci vuote scartate, dedup
`lower()` con ordine e grafia della prima occorrenza, references a parità
di `source`; quattro validatori in mode **after** su `LessonContentOutput`
(output AI) e `LessonContentUpdateInput` (PATCH del docente). Lo storico si
normalizza al primo salvataggio dall'editor — che invia sempre entrambe le
liste — o alla rigenerazione. Nessun backfill.

*Alternative scartate.* (a) Dedup in lettura (PDF, vista, editor): tre
copie dello stesso algoritmo (Python, TS, e un modulo TS puro per il
runner Node), una divergenza permanente fra ciò che l'editor mostra e ciò
che il PDF stampa, e un normalizzatore costretto a tollerare le
references-stringa dei golden. La divergenza fra editor e PDF resta
**temporale**, non spaziale: in ogni istante le tre superfici leggono lo
stesso `content_raw`. (b) Mode «before»: `['A','a',' A ']` scenderebbe
sotto `min_length=3` e farebbe rigenerare l'intera lezione per un difetto
cosmetico; in after-mode i vincoli contano l'elenco grezzo e la lista
persistita può degradare a 1-2 voci. (c) `uniqueItems` nello schema strict
OpenAI: fuori dal sottoinsieme verificabile e cieco al case-insensitive e
al trim. (d) Backfill del `content_raw`: vincolo del committente, e le due
dispense reali non mostrano duplicati. (e) Validatore sul solo output AI:
lascerebbe il PATCH come via di reintroduzione. (f) `raise` su `[]` nel
PATCH: l'editor invia `[]` quando il docente svuota l'elenco, ed è un
azzeramento legittimo. (g) Fixture JSON condivisa: `tests/fixtures/` è
riservata ai casi con mirror TS, e qui il TS non partecipa.

**B6 — box della figura per pagina effettiva** (D12; 3/3 per l'ibrida,
modello B e superficie A). Questione **aggiunta al brief** dopo le misure:
il cap fisso `max-height: 80mm` del template slide tagliava le figure.
Nuovo modulo puro `slide_geometry.py`, con le costanti CSS del template
citate per riga e pinnate da un test a regex: `page_figure_budget` calcola
per ogni pagina **resa** (dopo la decisione di split) il budget verticale
di ogni blocco asset, `image_box` ne ricava il box dell'immagine al netto
della didascalia stimata sul testo reale, il fit di B4 lavora dentro quel
box. Con titolo su una riga l'immagine passa da 80 a **86,6 mm**.

*Alternative scartate.* (a) Costante derivata dallo spazio libero della
pagina asset-only, misurato sul template reale invece che preso dai
commenti del CSS: applicata a tutte le pagine,
nel video — dove `render_slides_html(enable_split=False)` non separa
bullet e figura — avrebbe tagliato ogni slide legacy con bullet e perfino
la slide dedicata di Fase 4; sposta il cap senza toglierne la natura
arbitraria, e il taglio è silenzioso (`overflow: hidden`). (c) Split anche
nel video: rompe il contratto «1 slide JSON → 1 frame» del mapping
audio ↔ frame, ed è fuori brief. (d) Stimatore delle righe a larghezza
media 0,5 em: sottostima i titoli in maiuscolo sui font del container
(5-6 casi su 28, una riga da 28 pt vale 10,37 mm, più della safety);
sostituito dallo stimatore per classi di carattere, con 0 sottostime e al
più una riga di sovrastima sui `LineBox` reali. (e) Budget fisso per la
didascalia: la didascalia ammette 600 caratteri e la coda calcolata di
`function` concatena fino a 12 valori per categoria. (f) Parametrizzare il
CSS con la geometria e i `line-height` espliciti: renderebbe i due motori
concordi al centesimo, ma cambierebbe la resa di **tutte** le slide già in
DB per guadagnare ~3 mm di figura.

**Deviazione dichiarata: il fallback delle slide non va a capo.** Il
progetto B6 prevedeva di troncare il `<pre>` del sorgente stimando le
righe a capo di `white-space: pre-wrap`. Sei giri di revisione hanno
trovato ogni volta un caso nuovo in cui la stima era battuta da Pango o da
Chromium: spazi collassati, spazi e tab conservati, U+2028 e U+2029 (a
capo in Pango, non in Chromium), profili di lingua e di script, e infine
le regole UAX #14 (niente a capo prima di `) ] } ! ? , . : ; /` né dopo
`( [ {`). Il `<pre>` delle slide e dei frame video è quindi in
`white-space: pre` (`lesson_slides_pdf.html.j2:276`): una riga di sorgente
è una riga resa, `overflow: hidden` taglia a destra le righe più larghe
del box, e il conteggio è esatto nei due motori. Resta il costo
dell'altezza di riga, che dipende da lingua e script e non dal ritorno a
capo: riga base **1,30 em**, riga alta **1,70 em**, riga con prima run
ideografica **2,46 em** (`slide_geometry.py:392`, `:395`, `:398`). La
dispensa non cambia: resta in `pre-wrap` nel flusso di pagina. Il costo si
paga solo nel percorso d'errore di una figura non resa.

### 20.3 Le sei misure e la decisione che ne discende

| Misura | Stato | Esito | Decisione che ne discende |
| --- | --- | --- | --- |
| (a) grep dei log di produzione (eventi di setup, fallback e timeout) | **Eseguita il 18 settembre 2026**, ma su una finestra di poche ore: il container `a4u-backend-1` era stato ricreato quel mattino e i log precedenti non esistono più (nessun container più vecchio dello stesso progetto) | Zero occorrenze dei sette eventi su 273 righe; nella stessa finestra 32 `lesson_content_request` e 6 `lesson_content_generated`, quindi la finestra contiene generazione vera. Campione piccolo: da ripetere quando il container avrà accumulato storia | Se `mathjax_renderer_setup_failed` > 0 negli ultimi 30 giorni, al pin `mathjax_cdn_version` e alla guardia di rete si aggiunge un **retry del launch**; se = 0, restano pin e guardia. In ogni caso il sintomo «a volte sì a volte no» è spiegato dai difetti riprodotti, non dal CDN |
| (b) ordine citazione/ancora su lezioni reali | **Eseguita il 18 settembre 2026** sull'export pgAdmin di 4 lezioni (M2.L2 e M12.L7 di Analisi Matematica, M4.L1 di Economia degli intermediari finanziari, M4.L3 di Misure Meccaniche e Termiche) | 46 tag, 46 id distinti, **0 id citati più di una volta**: la duplicazione che il docente vedeva non compare in queste quattro lezioni e il fix resta preventivo. **35 occorrenze su 46 sono in linea** dentro una frase (11 su riga propria): prima del branch ognuna di quelle 35 spezzava la frase in due paragrafi. Nessun asset non citato, nessun tag orfano. Conteggio della parola-etichetta: **8 su 46**, in due lezioni su quattro (M2.L2 4, M12.L7 4), tutte della forma «La figura [FIG:…]» / «La tabella [TAB:…]» | La regola di B1 non dipende dalla frequenza, ed è confermata: senza rimando in linea, 35 frasi su 46 uscivano spezzate. Il conteggio > 0 **attiva la guardia parola-etichetta** (regola decisa in B1: si attiva se la misura è > 0) |
| (c) font base per tipo Mermaid | **Eseguita** (tabella 2(c)) | La radice predice il font reale in 7 tipi su 15; tre canali (regola CSS, `style` inline, attributo) e un'unità relativa (`4ex`) | Né lettura dalla regola radice né pin per tipo: si misura nella pagina Chromium già aperta dal pre-render e si legge in Python per gli altri formati (B4) |
| (d) distribuzione su figure reali | **Eseguita il 18 settembre 2026** con `--figures` sull'export del docente: 26 figure dichiarate (18 Mermaid, 5 Vega-Lite, 2 DOT, 1 `function`), 25 rese con i renderer di produzione, 20 grafi misurati | Nessuna metrica oltre soglia e nessun p90 oltre il 60 % previsto dalla regola: nodi p90 9,5 (soglia 30), archi 9,1 (45), etichetta 20,4 (64), titolo 25,1 (110), righe 12,4 (120), sorgente Mermaid 391,9 (3000), incroci p90 0 e massimo 1 (4); 0 incroci non misurati su 20 grafi | Le soglie di `graph_rules.py` **non si toccano**, ma smettono di essere provvisorie sui 57 modelli degli editor: sono confermate anche sui 20 grafi reali di quattro corsi. La regola di ricalibrazione resta in piedi per il prossimo dump (p90 > 60 % della soglia → la soglia si alza, mai si boccia il contenuto) |
| (e) page-break senza tetto d'altezza | **Eseguita** (WeasyPrint 69, template reale) | Senza `max-height` un SVG 300×1600 viene **tagliato** (sbordo +650,7 mm); il buco tipografico prima di una figura a pagina intera c'è in ogni configurazione | `fit_figure_width_mm` riceve `box_h_mm` = altezza utile − 8,3 mm di chrome della figura (≈ 248,7 mm su A4); **nessun two-pass** sul residuo di pagina (guadagna una pagina ma produce figure larghe 16-26 mm); il buco è accettato e documentato |
| (f) costo di `__measureSvg` nel batch | **Eseguita** (15 modelli, 3 ripetizioni, Chromium) | Batch reale 1.867/1.965 ms → 2.224/2.289 ms con la misura; sola misura 355-359 ms per batch, 23,7 ms per figura, dominata dal campionamento | Passo 2 px e confronto O(n²) **entro il budget**, con tetto per figura e cumulativo per batch e uscita anticipata; il selettore deve includere `line` e `polyline` e filtrare per classe degli archi (senza filtro `sequence` conta 314 falsi incroci) |

I due comandi da eseguire sul server, con i nomi degli eventi e delle
colonne verificati a HEAD (il primo è stato eseguito il 18 settembre 2026
con l'esito di §20.3(a); il secondo lo stesso giorno, con l'esito di
§20.3(b) e (d)). Il nome del container si ricava con
`docker ps --format '{{.Names}}'`, e conviene togliere il
prefisso `"event": ` dal filtro, che funziona così sia con i log in JSON
sia con quelli leggibili:

```bash
docker logs --since 720h <container-backend> 2>&1 \
  | grep -oE '"event": "(mathjax_renderer_setup_failed|mermaid_renderer_setup_failed|figure_render_fallback|asset_validator_timeout|math_convert_failed|lesson_content_unused_assets|lesson_content_dangling_asset_refs)"' \
  | sort | uniq -c
```

```sql
SELECT json_agg(t) FROM (
  SELECT l.id::text AS id, l.lesson_code, c.title AS course_title, c.language_code,
         l.content_raw, l.slides_raw
  FROM course_lesson l JOIN course c ON c.id = l.course_id
  WHERE l.content_raw::text LIKE '%[FIG:%'
  ORDER BY jsonb_array_length(COALESCE(l.content_raw->'visual_assets','[]'::jsonb)) DESC
  LIMIT 4) t;
```

L'export si dà in pasto a `backend/scripts/measure_asset_refs.py` (sola
lettura di un JSON, nessun accesso al DB), che riporta ordine
citazione/ancora (`in linea` / `ancora`), id ripetuti, citazioni per kind,
asset non citati e tag orfani, e per ogni occorrenza zona (`corpo` /
`coda`) e campo — quindi anche le citazioni dentro esempi
(`examples[i].content`) e tabelle (`tables[i].markdown`); con `--figures`
rende ogni figura con i renderer reali e misura nodi, archi, etichette e
incroci, cioè i numeri che ricalibrano le soglie di WP5.

Un dato che lo script **non** produce è il conteggio della parola-etichetta
davanti al tag, cioè della forma «Figura [FIG:», da cui dipende la guardia
di B1: nessuna colonna del report lo riporta. Va misurato a parte sullo
stesso export:

```bash
grep -oiE '(figura|tabella|equazione|esempio) \[(FIG|TAB|EQ|EX):' export.json \
  | sort | uniq -c
```

Sull'export del 18 settembre 2026 il conteggio è **8 su 46 tag**
(`5 figura [FIG:` e `3 tabella [TAB:`), distribuite in due lezioni su
quattro (M2.L2 4 = 2 figura + 2 tabella, M12.L7 4 = 3 figura + 1
tabella): sopra lo zero che teneva la guardia fuori dal primo rilascio,
quindi la guardia è stata attivata (§20.2, B1).

Attenzione a non contare di più con una regex più larga: prendendo la
parola *qualsiasi* attaccata al tag le occorrenze diventano 21 — fra
queste «La mappa [FIG:fig_course_synthesis_map]», che qualcuno conta come
nona occorrenza della guardia. Non lo è: la guardia confronta il tag con
la parola della **sua** etichetta (`courses.figures.*.ref`), e «mappa»
non è quella parola, quindi il rimando resta intero («La mappa Figura
N»), come deve. Il conteggio che decide la regola di B1 è il primo, 8.

### 20.4 I quattro censimenti

**2(a) — copie del pattern `[KIND:id]`.** Quindici sedi più tre
produttori, non «una copia viva».

| Esito del censimento | Sedi | Nota |
| --- | --- | --- |
| Canonica, invariata | 1 (`_ASSET_REF_RE` del servizio PDF) | `\[(FIG\|TAB\|EQ\|EX):([^\]\n]+)\]`, id normalizzato a valle con `.strip().lower()` |
| Invariate o generalizzate per kind | 8 (numerazione BE e FE, renderer e editor del frontend, script di rivalidazione) | Devono restare speculari a due a due |
| Allineata | 1 (`course_lesson_content_service`, classe `[^\]]+` che attraversava il newline) | Un tag spezzato risultava «citato» per i warning ma nessun renderer lo sostituiva |
| Rimossa | 1 (copia morta nel CRUD del contenuto) | Sola definizione, nessun chiamante |
| Lasciate, perché filtri e non parser | 4 (stripper di rumore, gate anti-allucinazione, gate i18n, guardia dei delimitatori math BE e FE) | Deliberatamente più larghe del parser: documentato |

Su `[FIG: X ]` tutte le forme con classe di id matchano e normalizzano a
valle: nessuna regex andava toccata per gli spazi.

**2(b) — stringhe d'autore che raggiungono un template senza renderer.**
Il censimento ha trovato la causa del difetto 2 fuori dalla dispensa.

| Superficie | Autoescape prima | Campi d'autore senza math | Esito |
| --- | --- | --- | --- |
| Dispensa (`lesson_pdf.html.j2`) | ON | punti chiave, citazioni dei riferimenti, didascalie, etichette di equazione, titoli degli esempi, didascalie di tabella | Resi con `render_markdown_inline` (solo math, testo byte-identico a `markupsafe.escape`) |
| Slide (`lesson_slides_pdf.html.j2`) | **OFF** (`select_autoescape(["html","xml"])` non riconosce `.j2`) | titolo, prosa e bullet | Autoescape acceso con `enabled_extensions=("html","xml","j2")`; prosa e titoli dal collector math |
| Discorso (`lesson_speech_pdf.html.j2`) | **OFF** | testo, note di regia, titolo di slide | Come sopra, più un escape **CSS-string** dedicato per il piè di pagina: un `"` nel titolo del corso lo faceva sparire |

Gli `|safe` esistenti prima del branch erano due in tutto: uno nella
dispensa (`{{ body_html | safe }}`, `lesson_pdf.html.j2:438` su `main`),
uno nelle slide (`{{ asset_html|safe }}`, `lesson_slides_pdf.html.j2:390`
su `main`), nessuno nel discorso. I `tpl.*` in contesto CSS e URL sono
stati trattati come CSS, non come HTML.

**2(c) — quindici tipi Mermaid, font della radice e font effettivi**
(misurati con la catena reale **prima** del branch, cioè con la larghezza
piena e senza il fit di B4). Numeri chiave: radice identica per tutti
(`width="100%"`, nessun `height`, `#mmd-N{font-size:14px}`); **in banda in
dispensa 2 tipi su 15, in banda nelle slide 3 su 15, nessun tipo in banda
su entrambe le superfici**; la radice predice il font reale in **7 tipi su
15**; il testo effettivo arriva per tre canali diversi più un'unità
relativa (`timeline`: 14 px più `4ex`, cioè 29,3 px); `treemap` mescola
cinque dimensioni nello stesso SVG (10, 12, 14, 23, 38 px). Estremi
misurati: `state` 24,8 pt in dispensa contro `gantt` 3,7 pt; `block` 25,9
pt nelle slide contro `radar` 3,9 pt. Conclusione: la leva non è il font,
è la larghezza del viewBox (pt ≈ 6670 / `vb_w`).

**2(d) — i 57 modelli degli editor.** Numeri chiave per formato, sempre
allo stato **precedente** al branch (la colonna «resa» è il corpo del
testo con la geometria di allora, non con il fit di B4).

| Formato | Modelli | Distribuzione (min/mediana/p90/max) | Resa | Incroci |
| --- | --- | --- | --- | --- |
| Mermaid | 15 | nodi 2/4/7/10, archi 0/2/5,6/6, etichetta 6/19/35,6/55 caratteri | vedi 2(c) | non misurabili sul sorgente |
| DOT | 18 | nodi 3/5/8/9, archi 2/5/7,3/8, etichetta resa 1/12,5/22/23 | in banda in dispensa 16/18 (fuori: `pipeline` 7,70 pt, `network` 7,17), in banda nelle slide 15/18 | **1 su 18**: `hashTable` |
| Vega-Lite | 24 | record dati 3/7,5/12/15, etichette ≤ 28, titoli ≤ 73, righe ≤ 39 | font 11-13 px, scala 1,00 su entrambe le superfici → **8,25 pt** in dispensa (23/24 in banda) e **8,25 pt nelle slide (0/24 in banda)** | 0 |

Nessuno dei 18 modelli DOT usa la forma a blocco `graph [`, e l'unico
modello con un incrocio (`hashTable`) passava già l'oracolo di
leggibilità: da qui la decisione, in WP5, di tenere gli incroci **fra i
diagnostici** e non fra i motivi di rifiuto.

La misura del 17 settembre 2026 sui 57 modelli resi e passati a
`fit_figure_width_mm` con i box reali — 6 fuori banda in dispensa, 35
nelle slide — sta nella tabella di [09 § Larghezza delle
figure](09-pdf-export.md) ed è l'elenco dei fuori banda **noti** che il
gate editoriale deve riconoscere come tali e non come regressioni.

### 20.5 I work package e i commit

Undici commit, `git log --format='%h %s' main..HEAD` in ordine di lavoro.

| WP | Commit | Che cosa cambia |
| --- | --- | --- |
| WP0 | `b82dbc6` `fix(pdf): la regola di render che mancava — quattro token dollarmath, guardia anti-currency, fallback loggato` | `_install_math_grammar` come unica grammatica del math; `math_inline_double` e `math_block_label` non restano più alla rule di default del plugin (che emetteva `<div class="math inline">` senza consultare la mappa SVG); guardia anti-currency; `MathSvgMap` con `requested`/`misses` e `math_render_fallback` con `reason`; `span.math-block` nei due template; upper bound `mdit-py-plugins<1` |
| WP1 | `1ce8c3d` `feat(pdf): rimandi testuali, ancora dopo il blocco, etichette dei quattro kind` | `asset_ref_normalize` con mirror TS e fixture condivisa; `compute_asset_numbers` con contatore indipendente per FIG/TAB/EQ/EX sul corpo **non** normalizzato (la numerazione delle figure esistenti non cambia); `append_uncited_asset_refs` accoda anche tabelle, equazioni ed esempi mai citati; 12 chiavi i18n nuove per lingua; regex morta rimossa dal CRUD; `scripts/measure_asset_refs.py` |
| WP2 | `df7af78` `fix(pdf): il resto della catena LaTeX — grammatica unica, collector per parse, math nei campi inline` | rule inline `math_bsdelim` al posto del pre-processing testuale, eliminato; collector che **parsa** lo stesso testo del renderer con le stesse istanze; `render_markdown_inline` su didascalie, etichette, titoli, punti chiave e riferimenti; `inlineMath.ts` e `InlineMath.tsx` nel frontend; pin `mathjax_cdn_version` e guardia di rete sulla pagina MathJax |
| WP3a | `0bb4865` `feat(figures): larghezza delle figure dalla banda di leggibilità` | misura del corpo del testo (Chromium per Mermaid, Python per gli altri), `RenderedFigure` nella cache, `figure_scale.fit_figure_width_mm`, `figure_fit_out_of_band` e `figure_fit_report`; mirror frontend; oracoli sullo stato precedente: flowchart v11 **13,1 → 11,0 pt** in dispensa (13,3 con i 170 mm di `main`, prima che il branch togliesse i margini negativi del wrapper Mermaid), **19,9 → 14,0 pt** nelle slide, **18,3 → 11 pt** a schermo |
| WP3b | `c0ad70f` `feat(figures): box della figura per pagina nelle slide e nei frame video` | `slide_geometry.py`, budget per pagina resa, `--figure-w`/`--figure-h` sul `<figure>`, il template passa a `var(--figure-h)`; immagine asset-only da 80 a 86,6 mm; fallback `<pre>` in `white-space: pre` con troncatura |
| WP4 | `f397a9f` `fix(pdf): autoescape, math nelle slide e nel discorso, duplicati di slide, guardia di rete nel video` | autoescape sui tre env con `.j2`, filtro `css_string`, piè di pagina del discorso; math nella prosa di slide e discorso e nelle due viste; una sola chiave per i riferimenti (asset citato due volte dalla stessa slide reso una volta); regola max-1-visivo solo sulle slide toccate dal PATCH; `block_external_requests` prima di `set_content` nei frame video |
| WP5 | `5d96199` `feat(figures): gate editoriali, incroci, prompt di Fase 3, dedup delle liste` | `graph_rules.py` con le costanti provvisorie e i messaggi `graph_too_dense:`; `figure_geometry.py` (incroci arco × arco e quattro difetti di lettura, parità Python/Chromium sui 18 DOT); tetto A1 sul `content` degli asset; `_count_asset_refs` con `Counter`; dedup di punti chiave e riferimenti in scrittura; regola di posizione dei tag nel prompt di Fase 3 |
| WP6 | `bb48622` `feat(figures): revisore AI figura↔testo con costo contabilizzato` | `openai_figure_review_service` innestato fra fix e localizzazione, verdetto `coerente`/`correggi` con controlli deterministici e resa speculativa; un rifiuto lascia l'originale **byte-identico**; kill-switch `figure_review_enabled` e triade di setting (`config.py:295-300`); fix e localizzazione passano a `build_usage_dict`, il worker fonde l'usage in `content_tokens.assets` e `assets_cost_usd` |
| WP7 | `3de554f` `docs: capitolo 20 di 17-visual-figures e riallineamento al codice del branch` | questo capitolo e i riallineamenti di §6, §12, §13, §15 e dei venti documenti del piano; nessun file di codice, salvo due commenti stantii corretti |
| Fase D | `e026ae8` `fix(pdf): revisione avversariale — dieci correzioni e gli esiti nel capitolo 20` | parità Python/JS dei mirror (`strip` contro `trim`), ancora che non spezza più una lista attaccata, guardia anti-currency sui `$$..$$` in frase, arco degenere contato anche da Chromium, nessuna chiamata sterile del revisore senza la resa, guardia unica degli oracoli Chromium; esiti in §20.8 |
| WP8 | `07a7e31` `fix(pdf): il rimando testuale vale anche nella prosa delle slide e del discorso` | `AssetRefs` + `lesson_asset_refs` come UNICO contratto della numerazione (i numeri nascono dal corpo della dispensa, chi non lo rende li riusa); `cite` su titolo, prosa e bullet delle slide, su `seg.text`/`seg.delivery_notes`/titolo di slide del discorso e sulle didascalie in una riga dei quattro blocchi; `slide_asset_ref_unresolved` / `speech_asset_ref_unresolved` per i tag che nessun numero risolve; mirror `lib/lessonAssetRefs.ts` condiviso dalle tre viste; `asset_refs` come chiave sintetica del collector del math. Oracolo della prova di consegna rimisurato sulle stesse tre lezioni: `asset_tags_left` nei PDF slide **1 → 0** su tutte e tre (L1, L2, L3), con «Figura 1» al posto di «[FIG:iter]» |

Deviazioni dichiarate rispetto al piano approvato: **WP3 in due commit**
(3a scala, 3b box slide) per la revisionabilità; **B6 aggiunta al brief**;
il fallback delle slide senza ritorno a capo (sopra); gli **incroci oltre
la soglia sono diagnostici** anche nella validazione profonda DOT, perché
un percettrone 3-4-2 ne ha 16 e finiva in rigenerazione; i loghi in `src`
restano all'escape HTML dell'attributo invece del `|safe` previsto, perché
è la codifica corretta per WeasyPrint.

### 20.6 Limiti dichiarati e rischi residui del branch

- **Il blocco teorema sborda dalla slide, senza log (B6).** Il budget
  D12 (§20.2, WP3b) nasce per il box della FIGURA: un `figure.equation` in
  famiglia teorema — enunciato più passi di dimostrazione — non ci passa,
  ricade sul cap CSS `var(--figure-h, 80mm)` e, quando il contenuto è più
  alto, esce dai 120 mm di `.slide-body`. La prova di consegna lo ha
  misurato con WeasyPrint sulle tre lezioni rappresentative, **identico
  prima e dopo il branch**: L1 pagina 3 **+12,12 mm**, L2 pagina 3
  **+19,30 mm**, L3 pagina 3 **+12,39 mm**. È l'unico sbordo che il branch
  non intercetta, e siccome `.slide-body` ha `overflow: hidden` il
  contenuto in eccesso — di norma la fine della dimostrazione — **sparisce
  in silenzio**, senza nemmeno un `slide_figure_box_exhausted`. NON
  corretto: B6 tiene il cap del teorema invariato; il rimedio è estendere
  il budget a un blocco di TESTO, cosa che cambia l'impaginazione di ogni
  slide di teorema già prodotta e va misurata prima. Vedi
  [10 — § Rendering](10-lesson-slides.md#rendering-di-titolo-prosa-bullet-e-riferimenti-wp4).
- **Il testo letto dalla voce conserva il tag (WP8).**
  `sanitize_tts_text` non tocca le parentesi quadre: un `[FIG:iter]` nel
  `speech_segments[].text` sopravvive e la voce lo legge, mentre il PDF
  del discorso e la vista ora mostrano «Figura 1». La scelta è
  deliberata — il rimando è una normalizzazione di render, il testo del
  parlato è contenuto persistito — ed è pinnata da un test; vedi
  [11 — § PDF del discorso](11-lesson-speech.md#pdf-del-discorso--course_lesson_speech_pdf_servicepy-wp4).
  La divergenza non resta fra due artefatti separati: il frame video passa
  dallo stesso `render_slides_html`, quindi DENTRO lo stesso video la
  slide scrive «Figura 1» mentre la voce legge il tag. Conseguenza attesa
  della scelta, non un difetto in più.
- **I nuovi asset di Fase 4 non hanno numero (WP8).** La numerazione è
  quella della dispensa; un asset dichiarato solo in `slides_raw.new_*`
  non ne riceve uno, perché assegnarglielo lo farebbe collidere con i
  numeri del corpo. Un tag che lo cita resta letterale e produce
  `slide_asset_ref_unresolved`: sulla slide il blocco c'è comunque, con
  l'etichetta non numerata «Figura.» (A2).
- **Dati reali del docente: arrivati il 18 settembre 2026** (export
  pgAdmin di 4 lezioni, §20.3(b) e (d)). Le due conseguenze che pendevano
  sono chiuse: le soglie di `graph_rules.py`, calibrate sui 57 modelli
  degli editor, restano invariate ma sono ora confermate sui 20 grafi
  reali di quattro corsi (nessun p90 oltre il 60 % della soglia), e la
  guardia «parola-etichetta» è **attiva** (8 occorrenze su 46 tag). Resta
  un campione di quattro lezioni: la regola di ricalibrazione vale per il
  prossimo dump.
- **Crescita degli `<img>` sotto banda.** Una figura che cresce fino al
  fondo della banda è più alta e può anticipare un salto pagina nella
  dispensa. Misura del verificatore su 93 dispense (31 figure in 3
  posizioni): il branch toglie una pagina in **24 casi** e non ne aggiunge
  in nessuno; in una scansione mirata su 80 dispense, le due figure con
  testo a 4,5-5 pt aggiungono una pagina in 3 posizioni ciascuna.
- **Range di versione aperto delle dipendenze markdown.** `mdit-py-plugins`
  ha ora l'upper bound `<1` (`backend/pyproject.toml:34`) perché i nomi dei
  token e la semantica di `allow_space`/`allow_digits` sono contratti
  impliciti del PDF; `markdown-it-py[plugins]` resta a `>=3.0.0` senza
  tetto. Il tripwire sono i **test strutturali**
  (`test_all_four_dollarmath_rules_are_ours`,
  `test_math_grammar_flags_and_rulers_are_pinned`): un major che rinomina
  un token li fa fallire prima del rilascio, non in produzione.
- **Font di produzione assenti dal container.** Il default di
  `font_family` è `"Roboto"` (`app/models/pdf_template.py:31`,
  `app/models/slide_template.py:42`) e lo stack ripiega su `"Inter"` e
  `"Helvetica"`, anch'essi assenti; in dispensa e nel discorso lo stack
  prosegue con `"DejaVu Sans"` e `"Liberation Sans"`, che ci sono, nelle
  slide no; l'immagine installa `fonts-dejavu-core`,
  `fonts-liberation`, `fonts-noto-core` e `fonts-noto-cjk`
  (`backend/Dockerfile:44`) e nessuna delle due famiglie richieste. La
  sostituzione è **silenziosa**: le stime di `slide_geometry` sono
  calibrate sulle sei famiglie realmente presenti, quindi il modello
  regge, ma il PDF non è tipograficamente quello che il template dichiara.
- **Buco di copertura della CI.** `backend-ci.yml` gira solo sui path
  `backend/**`, `frontend-ci.yml` solo su `frontend/**`. Un commit che
  tocca **solo `frontend/`** non esegue la suite Python, cioè proprio i
  test che tengono allineati i mirror (parità Node delle fixture
  condivise, specchio delle chiavi i18n, inventario delle chiavi
  letterali); un commit che tocca **solo `docs/`** non esegue nessun
  workflow, quindi `check_prompts_md` non verifica `PROMPTS.md`. I mirror
  si rompono in silenzio finché non arriva un commit che tocca `backend/`.
- **Stringhe UI nelle sole it/en.** Le 12 chiavi nuove esistono solo in
  `it.json` ed `en.json`; le altre 22 lingue ricadono sul fallback
  `fallbackLng: "it"` di i18next (`frontend/src/i18n/index.ts:113`) e
  vedono le etichette in italiano finché l'amministratore non lancia
  l'auto-translate. Il backend fa la stessa cosa con `figure_labels`.
- **Costo del revisore AI.** Il costo di fix, revisione e localizzazione
  è in `content_tokens.assets` con il `cost_usd` per chiamata, sommato in
  `assets_cost_usd`; il kill-switch `figure_review_enabled` spegne la fase
  senza toccare il resto. Resta fuori dal conteggio il costo delle
  chiamate di una generazione poi annullata o rigenerata, che finisce nei
  soli log.
- **PDF storici.** Ribadito perché è la domanda che il docente farà per
  prima: nessun PDF già materializzato cambia da solo. Finché la lezione
  non viene ri-esportata, il PDF mostra la frase spezzata, il LaTeX
  appiattito e le figure fuori scala di prima.
- **`$` non matematici nei campi inline, senza via di fuga.** Il preset
  `zero` di `render_markdown_inline` ha la sola grammatica del math:
  niente `code`, niente `escape`. Una COPPIA di `$` in testo non
  matematico diventa quindi una formula anche dentro i backtick, e `\$`
  non è onorato. Su didascalie, punti chiave, bullet e prosa delle slide e
  del discorso «Il percorso e' $HOME/$USER/bin» esce «HOME/USER/bin» in
  corsivo, «La regex `^a$` e `^b$`» perde i due `$`; su `main` quei campi
  restavano letterali perché non rendevano alcun math. È la conseguenza
  dichiarata di B3/D9 (`docs/courses/09-pdf-export.md:236-240`), non una
  svista: nel CORPO il code span protegge, come su main, e la vista web
  degli stessi campi usa la stessa grammatica (`lib/inlineMath.ts`),
  quindi l'autore vede in app la formula che finirà nel PDF. La guardia
  currency copre i soli importi, `$$..$$` compresi (Fase D).
- **Divergenza Python/Chromium sugli incroci in un caso ordinario.** Dove
  la parametrizzazione è molto disuniforme (cappi, gomiti stretti) il
  campionamento per parametro di `figure_geometry` può produrre corde più
  lunghe del passo e perdere un incrocio a piccolo angolo vicino
  all'attacco di un arco, che Chromium trova: su un digraph di 10 nodi e
  22 archi senza attributi Python conta 4 e Chromium 5, con convergenza a
  5 già a passo 1,5. Portare il passo sotto 2 costerebbe circa il 50 % di
  segmenti in più contro i tetti dichiarati e, misurato su 150 grafi di
  fuzz, non riduce le divergenze: il passo resta 2 e il limite è ora nel
  docstring del modulo. In produzione i DOT si misurano solo in Python e
  la misura è diagnostica (un warning e una riga del report).

### 20.7 Lavori futuri

- **Retry del launch di MathJax** (§20.3(a)): deciso, ma solo se il grep
  dei log conta almeno un `mathjax_renderer_setup_failed` negli ultimi 30
  giorni. Pin del CDN e guardia di rete sono già dentro. Prima lettura del
  18 settembre 2026: zero occorrenze, su una finestra di poche ore e sei
  lezioni generate — priorità bassa finché il conteggio su trenta giorni
  non dice altro.
- **Soglie di `graph_rules.py` sul prossimo dump** (§20.3(d)): il primo
  export (20 grafi reali) le conferma senza toccarle; la regola «p90 >
  60 % della soglia → la soglia si alza» resta in piedi per un campione
  più largo di quattro lezioni.
- **Plurale davanti al tag** (B1): la guardia «parola-etichetta» è attiva
  dal 18 settembre 2026, ma confronta la parola completa, quindi «Le
  figure [FIG:a]» dà ancora «Le figure Figura 1» (0 occorrenze
  nell'export; limite pinnato in fixture). Si chiuderebbe con una lista di
  plurali per lingua, cioè con altre chiavi i18n: non vale il prezzo
  finché la misura resta a zero.
- **Strip del prefisso numerico** per tabelle, equazioni ed esempi (B2):
  la forma larga è lossy; resta disponibile una variante stretta,
  verificata su 23 casi, come questione separata.
- **`$ x_0 $` nella prosa del PDF resta prosa** (`allow_space=False`)
  mentre il frontend lo rende come math: divergenza accettata e coerente
  con il validatore, da chiudere scegliendo un solo comportamento.
- **`$$…$$` in frase** ha chiave `block` e quindi display style nel PDF,
  in linea nel frontend: seconda divergenza accettata.
- **L4 nella vista lezione**: il frontend conserva il pre-processing
  testuale dei delimitatori (`normalizeMathDelimiters` in
  `MarkdownRenderer.tsx:70`, applicato a `:110`), che il PDF ha eliminato;
  ticket separato, fuori perimetro.
- **C12**, `segment_id` ripetuto nella mappa del discorso: limite pinnato
  in `tests/test_pdf_templates_autoescape.py`, non corretto.
- **Via di fuga per il `$` letterale nei campi inline**: aggiungere la
  rule `code` o la rule `escape` al preset `zero` chiuderebbe il limite
  qui sopra, ma cambierebbe la grammatica anche nel frontend (`\$5` oggi
  resta `\$5` su entrambi i lati): scelta del committente, non una
  correzione di revisione.

### 20.8 Revisione avversariale (Fase D)

Eseguita il 17-18 settembre 2026 su HEAD `3de554f` (il branch così come lo
descrivono le sezioni qui sopra), con **undici revisori**: le nove
dimensioni del brief più la **parità delle fixture condivise** e i **`try`
di Chromium** nella suite. Ogni rilievo è passato da un confutatore prima
di arrivare al correttore. Bilancio: **32 rilievi** — 12 confermati (1
maggiore, 9 minori, 2 note), 8 confutati, 12 note. Dei 12 confermati,
**10 sono corretti**, ciascuno con il test che avrebbe colto il difetto,
e **2 sono dichiarati come limiti** (`prosa-1` e `incroci-1`) in §20.6 e
nel codice che li produce, perché chiuderli significherebbe ribaltare una
decisione del committente o cambiare il passo di campionamento senza
guadagno misurato.

| dimensione | che cosa è stato provato, con che numeri | confermati (esito) | confutati (motivo) | note |
| --- | --- | --- | --- | --- |
| **normalizzazione** | 90 markdown scritti a mano (liste annidate e sciolte, tabelle, blockquote, fence aperte, `$$` non chiusi, CRLF, HTML block, quattro kind, id equivalenti) più **6.000 campioni di fuzz** (seme 20260917). Parità Python↔TypeScript sul bundle esbuild di `assetRefNormalize.ts` in node 22.14: **6.070 input confrontati byte a byte, 0 divergenze**; invarianti del modulo («una sola riga-ancora per chiave gestita», «nessun tag gestito fuori da un'ancora», idempotenza) **0 violazioni su 6.070**, su entrambi i lati. Resa reale della dispensa: **270 rendering** (90 markdown × introduzione, sezione, sintesi) attraverso `render_lesson_html`, ogni asset gestito reso **1 volta in 261 casi su 270**, **mai 2 volte**, 0 tag residui nel testo visibile, 0 blocchi `missing-asset`; i 9 casi restanti sono le tre fence non chiuse. Oracolo strutturale su 1.113 firme di blocco, PDF vero (WeasyPrint 69 + pypdf) su 3 casi, frontend vero (`remark-parse`/`gfm`/`math`/`rehype`), confronto con `main` @`7446432` in worktree, baseline `tests/test_asset_ref_normalize.py` 220 verdi | `normalizzazione-1` (minore): l'ancora spezzava in due una lista sciolta attaccata al paragrafo, con figura in mezzo e `<ol start="2">` — **corretto** in `59a3998`. `normalizzazione-3` (minore): `.strip()` e `.trim()` non tolgono la stessa classe di caratteri (U+FEFF, U+0085, U+001C-001F) e i due mirror producevano documenti diversi — **corretto** in `552cb81` | `normalizzazione-2` (tag su riga propria dentro un paragrafo che spezza la frase): è la regola di D2 alla lettera (`^[ \t]*\[KIND:id\][ \t]*$`), l'HTML è byte-identico a `main` e non è la patologia 1, che richiede una citazione **in linea**. `normalizzazione-4` (tag dentro esempi, titoli di esempio e didascalie di tabella non normalizzati): limite già scritto in §13 e §15 **su `main`** e fuori dal perimetro del piano (r. 217 «MD \| invariato», r. 440 `cite_asset_refs` sul solo tail) | `normalizzazione-5`: con una fence non chiusa la figura non è resa affatto e l'HTML del blocco esce come testo visibile — conseguenza della regola dichiarata, identica su `main` |
| **numerazione** | 24 lezioni sintetiche in 4 lotti (realistiche, avversariali, sei lingue senza chiavi proprie, patologiche), **134 figure numerate**, rese con `render_lesson_html` sul branch e su `main` @`7446432` in worktree. Numeri `{id → N}`: **134/134 identici fra `main` e branch, 0 divergenze**. Quattro superfici insieme (PDF `main`, PDF branch, frontend `main` e frontend branch eseguiti in node): **0 disallineamenti su 134**. Tutti e quattro i kind: **56 asset** (26 FIG, 10 TAB, 11 EQ, 9 EX) fra HTML della dispensa e `computeAssetNumbers`, **0 divergenze**, con contatori per kind indipendenti e famiglia teorema sul contatore EQ. Parità del normalizzatore: 24 lezioni × 3 campi = **72 confronti, 0 divergenze**. Slide: **138 etichette** ispezionate, **0 portano un numero** su entrambe le versioni (stesso percorso dei frame video). Sei lingue di fallback identiche 6/6. Fixture storica `figure_numbering_cases.json`: sezione `cases` **identica a `main`** (11 casi), le altre sono aggiunte. Il branch rende **2 figure in più** di `main` (quelle che finivano escapate in `<pre>`) e **nessuna in meno**, con lo stesso numero. Test mirati: **392 passed, 0 failed, 0 skipped** | nessuno | — | — |
| **prosa** | 91 testi (valuta e prezzi in tabella, variabili di shell, template string, regex con `$`, backslash markdown, `\(`/`\[` letterali, dollari in code span e fence, 13 lingue non latine, liste con `$`) × **16 superfici** di resa (10 dispensa, 3 slide, 3 discorso) × 2 alberi = **2.912 rese**, confrontate sul testo **visibile** estratto dall'HTML, con repro minimo e controllo su PDF vero (WeasyPrint + pypdf). Neutralizzata la trappola dell'install editable, che al primo giro faceva importare al worktree `main` il codice del branch. Corpo della dispensa: **364 coppie, 0 perdite nuove**, e **212 coppie recuperate** rispetto a `main` su 56 testi distinti. Valuta e prezzi: **448 coppie, 0 regressioni**. Lingue non latine: **208 coppie, 0 regressioni** | `prosa-1` (minore): nei campi inline e nella prosa di slide e discorso un `$…$` che non è una formula diventa formula e il testo sparisce (152 coppie su 14 testi) — **non corretto, dichiarato** come limite in §20.6 e nel docstring di `render_markdown_inline` (`f92a7e3`): è la conseguenza di B3/D9 e la via di fuga tocca anche il frontend. `prosa-3` (nota): la guardia anti-currency non guardava `math_inline_double`, e un `$$` di shell in frase diventava display — **corretto** in `591afb2` | `prosa-2` (`\(..\)` e `\[..\]` letterali che diventano formule): su `main` il corpo della dispensa li rendeva **già** come formule e in più corrompeva code span e fence; è la feature dichiarata L7, applicata insieme al frontend con lo specchio pinnato da un test; la superficie di testo corrotto **diminuisce**, non aumenta | `prosa-4`: la dichiarazione «testo byte-identico a `markupsafe.escape`» era senza riserve — chiusa da `f92a7e3` |
| **mermaid_bytes** | Censimento dei modelli nei tre editor (15 Mermaid + 24 Vega-Lite + 18 DOT = **57**, gli altri editor non ne dichiarano). **Sei esecuzioni** complete (4 in ordine normale, 2 in ordine inverso, cioè con id `mmd-N` diversi) su `main` e sul branch: **57/57 modelli resi** ogni volta, 0 `missing`; **57/57 sha256 dei sorgenti identici**; **112 coppie di SVG byte-identiche su 114**. L'unica differenza, `mermaid__class.svg`, si riproduce **anche fra due esecuzioni dello stesso codice** (4 coppie su 4) ed è il jitter senza seme di rough.js già dichiarato (REG-3): neutralizzando i soli attributi `d="…"` i file diventano identici. I 15 blocchi `<style>` degli SVG Mermaid hanno sha256 identico file per file; `THEME_VERSION` è `2026.09.4` su entrambi gli alberi e su entrambi i lati dello specchio, e la chiave di cache resta `(fmt, sha256(sanitized), THEME_VERSION)`; le 5 figure `function` sono 5/5 byte-identiche. La catena nuova gira davvero: 57/57 `RenderedFigure` con `metrics` non-None, `mermaid__flowchart` misurato in Chromium | nessuno | — | `mermaid_bytes-1`: il capitolo dichiara `erDiagram` non deterministico fra processi, ma il modello `er` degli editor è byte-identico su sei processi |
| **banda** | **Due oracoli indipendenti per ogni caso**: camminata sui box di WeasyPrint 69 e corpo **reale** dei glifi scritti nel PDF letti con pdfplumber (con `width` invece di `size` per il testo ruotato, che generava finti 2,98 pt). 57 modelli × 2 superfici = **114 misure**; 15 figure fluide e intrinseche sintetiche × 2 superfici (`width="100%"`, `<img>` in px e in pt, viewBox senza width, font in `em` e in `%`, stylesheet interno); 4 figure × 4 template di dispensa = 16 misure e 4 figure × 3 configurazioni di pagina slide = 12; 8 contesti di blocco × 2 dimensioni = 16; coerenza log↔report su 57 × 2 con `capture_logs`. Parità Python↔TS eseguendo il modulo **vero** `figureFormats.ts` in node: tutti i **29 casi `fit`** della fixture (il test committato ne esegue 2), **200 input casuali** (seme 20260917) e **200 estremi** (1e-9…1e12, ±Infinity, NaN, 0, negativi), 18 SVG per `svgIntrinsicBox` e 11 valori per `formatMm` | nessuno | — | `banda-1`: il chiamante frontend del fit non converte il corpo misurato in px naturali (`× pxPerUnit`); latente finché Mermaid resta `width="100%"` |
| **incroci** | 44 sorgenti DOT, **101 confronti** Python↔Chromium sui 57 modelli e sui grafi sintetici, **tre giri di fuzz** (120 + 150 + 200 grafi, 11 rifiutati dal validatore), più le soglie di `graph_rules` e un caso Mermaid denso | `incroci-1` (minore): il conteggio di `figure_geometry` e quello di `MEASURE_SVG_GEOMETRY_JS` divergono su DOT ordinari, fuori dai due limiti dichiarati — **non corretto, dichiarato**: su un digraph di 10 nodi e 22 archi senza attributi Python conta 4 e Chromium 5, con convergenza a 5 già a passo 1,5; scendere sotto il passo 2 costa circa il 50 % di segmenti in più contro i tetti e sul fuzz **non riduce** le divergenze (§20.6 e docstring del modulo, `f92a7e3`). `incroci-2` (nota): un arco con tracciato di lunghezza zero (self-loop con `splines=curved`) era contato da Python e scartato da Chromium, 7 contro 6 — **corretto** in `04afc1d`, registrando l'unità prima del controllo di lunghezza | — | — |
| **spesa** | Dodici sonde sul percorso del revisore e dei gate, tutte verdi, con conteggio delle POST e del costo per figura e con la controprova sullo stesso caso eseguita su `main` | `spesa-1` (minore): con la resa non disponibile per l'intero lotto (Chromium o `dot` assenti, batch in timeout) la revisione spendeva `figure_review_max_attempts` chiamate per figura, che `review_acceptance` respinge comunque con `measure_unavailable` — **4 POST e 0,0025 USD su due sole figure**, sorgenti invariati. **Corretto** in `e582ff5`: se nessun grafo del lotto è stato reso, i grafi escono con `figure_review_skipped reason=render_unavailable` | `spesa-2` (i gate editoriali di WP5 come «vera spesa nuova»): la controprova sulla stessa figura rifiutata dà **3 POST su `main` e 3 sul branch**, con la stessa eccezione; il salto da 0 a 0,00189 USD è la **contabilizzazione** introdotta da D16, non spesa nuova. La rigenerazione della lezione dopo un rifiuto è un limite dichiarato in tre punti | `spesa-3`: `docs/04-configuration.md:225` chiama `FIGURE_REVIEW_MAX_PARALLEL` «tetto di spesa», ma è un tetto di concorrenza. `spesa-4`: stima richiesta dal compito — il revisore costa **0,0016-0,0028 USD** per una lezione di 6 figure, **0,0033-0,0056 USD** nel caso peggiore a due tentativi |
| **escaping** | Dispensa, slide e discorso resi su `main` e sul branch con titoli, docente, prosa, bullet, note e URL ostili (virgolette, `&`, apici, `<script>`, query string con `&` ed entità), in quattro varianti (base, ricca, breakout, CSS), con lettura del PDF e sonde separate su CSS dei template, loghi e pagina Chromium | nessuno | — | `escaping-1`: nella dispensa 27 `tpl.*` in contesto CSS restano all'escape HTML, contro la regola dichiarata in §20.4 e la riga del piano «`\|safe` mirato sugli URL/CSS» per `lesson_pdf.html.j2` |
| **i18n** | Chiavi di interfaccia del branch contro `main`: **1.928 per lingua** (it ed en allineate) contro 1.916, cioè **+12 per lingua**, con il controllo delle chiavi orfane; resa delle etichette in tutte le lingue della cartella dei locali, compreso il fallback; specchio backend↔frontend delle etichette dei quattro kind; caso `kind` vuoto; guardia sulle stringhe italiane hard-coded nell'inventario dei componenti | nessuno | `i18n-1`: rieseguita la guardia sui sorgenti e la resa su tutte le lingue, il rilievo non si regge | `i18n-2`: il rimando in linea nella vista lezione segue la lingua dell'**interfaccia**, non «la lingua del corso» come dice il messaggio di WP1 (una frase italiana mostra «Come mostra Figure 1 il flusso»). `i18n-3`: con `kind: ""` il rimando nuovo diverge fra PDF e vista, «Formula 1» contro «Teorema 1»/«Theorem 1», perché i due lati hanno default del kind diversi |
| **parità delle fixture** | Le fixture condivise fra Python e TypeScript eseguite sui due lati e confrontate byte a byte: **272 job dalle fixture committate**, **600 casi generati** e **9.500 casi casuali**, più le riduzioni minime per il repro | `parita_fixture-1` (minore): in `asset_ref_normalize` U+FEFF risolve i tag sul frontend e non nel PDF, e sposta il confine del blocco `$$`. `parita_fixture-2` (minore): la stessa asimmetria `.strip()`/`.trim()` in `_norm` di `figure_numbering`, nella coda degli asset non citati e in `proof_steps`, con numerazione, coda e famiglia EQ/THM divergenti. **Entrambi corretti** in `552cb81`, con `_trim`/`trim` propri e la stessa classe di caratteri sui due lati, e con l'oracolo indipendente della fixture riallineato | `parita_fixture-3` (arrotondamento di `format_mm`/`formatMm`): resta una differenza reale ma è una **nota di copertura** della fixture, non un difetto di parità del branch | `parita_fixture-4`: `format_mm`/`formatMm` arrotondano diversamente a metà intervallo (half-even in Python, half-away-from-zero in JS) e divergono oltre 1e21; i 5 casi della fixture non lo coprono. `parita_fixture-5`: `docs/backend/11-tests.md` dichiara `svg_font` condivisa, ma nessun codice JS esegue quei 23 casi. `parita_fixture-6`: di `math_grammar_cases.json` il lato JS riceve 6 casi su 36, i 30 `block` non arrivano mai a `lib/inlineMath.ts` |
| **`try` di Chromium** | Scansione AST di tutti i `try → pytest.skip` nei file che passano da Chromium, con baseline `-rs` registrata, e **iniezione di errori dopo il `launch()`**: `_MISURA_JS` e `_DOT_GEOMETRIA_JS` sostituiti con un `throw`, `build_mermaid_renderer_html`, `Page.evaluate` e `Page.wait_for_function` che sollevano, più un guasto **parziale** sul solo `mmd-0` | `chromium_try-1` (**maggiore**): `mermaid_overflow` avvolgeva in un solo `try → skip` l'intera sessione (launch, `new_page`, `set_content`, `evaluate`), e un errore del JS dell'oracolo diventava **16 skipped** con il messaggio falso «Chromium non disponibile». `chromium_try-2` (minore): cinque fixture avvolgevano allo stesso modo l'intera chiamata di pre-render, **82 skipped**. `chromium_try-5` (minore): cinque `if all(... is None): pytest.skip`, cioè un guasto di resa dopo il launch che diventa skip senza passare da nessun `except`. **Tutti e tre corretti** in `29ba7a9`: guardia unica `tests/chromium_guard.py` (`require_cdn`, `require_chromium`, `render_batch_or_fail`), `try` sul solo `launch()`, e con gli stessi errori iniettati **zero skip e tutti rossi**; il test strutturale nuovo elenca i sette siti senza la correzione | `chromium_try-3` e `chromium_try-4`: rieseguita l'iniezione su `Page.evaluate` e su `Page.wait_for_function`, i due siti segnalati non producono lo skip silenzioso descritto | — |

I sette commit della correzione, in ordine di lavoro.

| Commit | Che cosa chiude |
| --- | --- |
| `552cb81` `fix(figures): revisione — parità Python/JS di strip e trim nei mirror` | `normalizzazione-3`, `parita_fixture-1`, `parita_fixture-2`: `_trim`/`trim` con la stessa classe di caratteri in `asset_ref_normalize` e `figure_numbering` e nei due mirror `.ts`; dieci casi nuovi nelle due fixture condivise, che falliscono in entrambe le direzioni (BOM dal lato Python, NEL dal lato JavaScript) |
| `59a3998` `fix(figures): revisione — l'ancora non spezza più una lista attaccata` | `normalizzazione-1`: l'ancora segue l'intera lista attaccata al paragrafo, niente `<ol start="2">`; tre casi nuovi nella fixture, due dei quali rossi con la regola vecchia |
| `591afb2` `fix(pdf): revisione — la guardia currency vale anche sui $$..$$ in frase` | `prosa-3`: la guardia anti-currency copre `math_inline_double`; tre casi nuovi in `math_grammar_cases.json`, verificati anche sul mirror `inlineMath.ts` |
| `04afc1d` `fix(figures): revisione — arco degenere contato anche da Chromium` | `incroci-2`: l'unità è registrata prima del controllo di lunghezza, `edges` torna a coincidere (7 contro 7); il caso `cappio_degenere` entra nell'oracolo di parità |
| `e582ff5` `fix(figures): revisione — niente chiamate sterili senza la resa dei grafi` | `spesa-1`: senza nessun grafo reso la revisione non chiama il modello, `figure_review_skipped reason=render_unavailable`, costo 0; il test nuovo fallisce senza la guardia |
| `29ba7a9` `fix(figures): revisione — gli oracoli Chromium non si autoassolvono più` | `chromium_try-1`, `chromium_try-2`, `chromium_try-5`: guardia unica, `try` sul solo `launch()`, e un test strutturale che vieta la resa e la misura dentro un `try` che salta |
| `f92a7e3` `docs: revisione — due limiti della Fase D dichiarati dove si leggono` | `prosa-1` e `incroci-1` dichiarati in §20.6, nel docstring di `render_markdown_inline` e in quello di `figure_geometry`; `prosa-4` chiusa; la via di fuga per il `$` letterale va fra i lavori futuri |

Gate dopo la correzione, sull'intero albero: `ruff check .` **331 rilievi**,
esattamente la misura di partenza; `mypy app` **205 errori in 32 file su
221 controllati**, esattamente la misura di partenza; suite backend intera
con `-rs` **2.608 passed, 0 failed, 0 skipped** (nessuna riga `SKIPPED` nel
riepilogo); `npm run lint` **4 errori e 23 avvisi**, la baseline nota;
`npm run type-check` e `npm run build` puliti.

I 4 errori di `npm run lint` stanno **fuori** dai file del branch e sono
gli stessi di `main`: `src/components/shared/LatexEditor.tsx:283`
(`react/no-danger`, regola non definita nella configurazione) e
`src/i18n/scripts.ts:64/66/68` (`no-irregular-whitespace` e due
`no-misleading-character-class`). Vale la pena scriverne i nomi: sono i
soli quattro, e attribuirli ai file toccati farebbe credere a una
regressione che non c'è.

### 20.9 Correzione della prova di consegna (18 settembre 2026)

Cinque rilievi dalla verifica di WP8, su HEAD `0c77846`. Due erano
difetti di codice, due frasi di documentazione troppo larghe, uno un
errore del rapporto.

| rilievo | esito |
| --- | --- |
| **`aria-label` non citato** (minore). Nel partial l'accessible name nasceva da `caption_text(caption)` PRIMA del `caption_renderer`, che era il solo punto in cui passava `cite`: con `alt_text` vuoto — default dello schema, quindi raggiungibile — il PDF usciva con `aria-label="Dipendenze di [FIG:iter]"` accanto a un `<figcaption>` che diceva «Dipendenze di Figura 1». Il mirror `FigureFrame.tsx` faceva già il contrario (`aria-label={altText \|\| text}` con `text = cite(stripped)`) | **corretto**: `cite` è un parametro di `render_figure_html` e si applica subito dopo `caption_text`, quindi vale per la didascalia e per l'accessible name insieme; `alt_text` resta testo d'autore, mai citato, come nel mirror. Due test nuovi, uno rosso prima (`['ciclo', 'Dipendenze di [FIG:iter]']`) e uno a guardia dell'eccesso di zelo |
| **Perimetro dell'avviso** (nota). `slide_asset_ref_unresolved` elenca i tag dei soli campi di prosa (`refs.unresolved(title, body, *bullets)`): un tag irrisolto in una didascalia resta letterale senza alcun evento, e la doc non lo diceva | **dichiarato**: la frase è stretta in [10 — § Rendering](10-lesson-slides.md) e in [09 — § Rimandi](09-pdf-export.md). Non è una rottura di parità — la dispensa non emette alcun evento — e portare le didascalie dentro l'elenco vorrebbe dire duplicare fuori dai renderer di blocco la mappa kind → campo della didascalia |
| **Corpo della dispensa malformato** (nota). Da WP8 slide e discorso leggono `content_raw` per averne i numeri: una voce di `sections` che non è un oggetto li faceva fallire con `AttributeError` dove prima non toccavano quel corpo | **corretto**: `_build_lesson_body_markdown` salta le voci non-oggetto, come già `_asset_ids_by_kind`. Tre casi nuovi (assente, lezione-verifica, sezione non-oggetto), il terzo rosso prima |
| **Frame video e voce** (nota). Il limite del TTS era dichiarato, ma nessun punto diceva che dopo WP8 la divergenza si vede DENTRO lo stesso video: il frame passa da `render_slides_html` e scrive «Figura 1», la voce legge il tag | **dichiarato** in §20.6 e in [11 — § Limite dichiarato](11-lesson-speech.md). Nessuna correzione: il parlato è contenuto persistito e non c'è backfill |
| **Localizzazione della baseline di lint** (nota). Il rapporto di WP8 attribuiva i 4 errori a `MarkdownRenderer.tsx` e `assetRefNormalize.ts`, cioè a due file del commit | **corretto nel testo**: i nomi giusti sono qui sopra, misurati con `npm run lint`; il conteggio (4 errori, 23 avvisi) era ed è quello |

### 20.10 Prima e dopo su quattro lezioni reali (18 settembre 2026)

Il confronto chiesto dalla consegna, rifatto sull'export del docente al
posto delle lezioni costruite: M2.L2 e M12.L7 di Analisi Matematica,
M4.L1 di Economia degli intermediari finanziari, M4.L3 di Misure
Meccaniche e Termiche. Ventuno PDF resi con la catena vera in tre stati:
`7446432` (prima della campagna), `b34d1bb` (campagna senza la guardia
parola-etichetta) e `004e287` (con la guardia). Gli SVG delle 26 figure
sono byte-identici fra gli stati, quindi ogni differenza viene dalla
geometria e dal testo, non dal disegno; zero `figure_render_failed` e
zero `figure_render_fallback` nei tre stati. L'oracolo WeasyPrint e il
`figure_fit_report` di produzione coincidono entro 0,005 pt su 37 istanze.

| misura | prima | dopo |
| --- | --- | --- |
| Frasi spezzate dal blocco della figura | 18 (le citazioni in linea con testo su entrambi i lati; le altre 17 chiudevano il periodo o stavano nell'introduzione degenere di M2.L2) | 0 |
| Residui LaTeX nel testo estratto | 112 (dispensa M2.L2 18, slide M4.L3 30, slide M12.L7 64) | 0 |
| Etichette numerate nelle dispense | 26 (solo «Figura N.») | 57 (Figura 26, Tabella 6, Equazione 19, Definizione 4, Teorema o Lemma 2) |
| «La figura Figura 1» nel corpo reso | 8 senza la guardia (M2.L2 4, M12.L7 4) | 0 |
| Figure fuori banda in dispensa | 14 su 26 | 10 su 26 |
| Figure fuori banda nelle slide | 9 su 11 | 11 su 11 |
| Sbordo dalla colonna di testo della dispensa | 1,00 mm su 14 pagine (wrapper Mermaid a 170 mm dentro 170 mm) | 0 |
| Sbordo dentro il corpo della slide | 7 pagine su 53, fino a 16,97 mm | 0 |
| Pagine della dispensa | M2.L2 20, le altre invariate | M2.L2 19 |

Due esiti vanno letti insieme, perché sono il prezzo dichiarato del box
per pagina (D12). Nelle slide il budget reale di una pagina con titolo,
prosa e didascalia lascia alla figura 61,6-68,3 mm invece degli 80 mm del
vecchio tetto fisso: lo sbordo sparisce, ma il corpo del testo scende e
due figure che stavano in banda ne escono (`fig_liquidity_dimensions`
12,49 → 8,59 pt, `fig_capacitivo_geometrie` 11,15 → 9,52 pt). Le altre
nove erano già sotto i 10 pt prima, e non per il fit: quei grafi hanno
20-40 etichette e resterebbero sotto i 10 pt anche a piena larghezza. In
dispensa il bilancio è invece positivo in entrambe le direzioni: nessuna
figura esce dalla banda e quattro ci rientrano. Tutte le voci fuori banda
sono elencate nel `figure_fit_report`, che è l'input del gate editoriale.

Due cose viste sul contenuto reale e non imputabili alla campagna: la
tabella a sette colonne di M4.L3 (`tab_confronto_trasduttori`, 195,9 mm)
sborda di 29,06 mm dalla colonna di testo, identica prima e dopo;
l'introduzione di M2.L2 contiene dodici tag su una riga sola con sei
apici inversi spaiati, e nove dei dodici rimandi finiscono in
monospaziato — la resa migliora comunque (prima due apici nudi e due code
span vuoti fra i blocchi, ora una riga di dodici rimandi numerati da
Figura 1 a Figura 12 e nessun apice nel testo estratto).

## 21. La direzione delle catene lineari (18 settembre 2026)

Il difetto arriva come i quattro del capitolo 20: **tre schermate del PDF
di produzione**. Un flowchart che è una catena lineare di passi,
dichiarato `flowchart LR`, esce dalla dispensa largo e basso, con le
etichette a 2-3 pt. Lo stesso contenuto dichiarato `flowchart TB` si
legge. Il docente indica come buono un caso `TB` da sette nodi e come
illeggibili due casi `LR` da otto e dodici nodi.

### 21.1 La misura

Le figure sono state rese con il motore di produzione (Mermaid 11 in
Chromium, pin `settings.mermaid_cdn_version`) e misurate nel box della
dispensa — 168 × 242 mm, cioè la larghezza del contenuto su A4 con
margine di 20 mm meno i 2 mm di padding del wrapper Mermaid, per
l'altezza utile di una pagina intera. Corpo di base del tema 14 px.

| catena | dichiarata | viewBox (uu) | box reso | corpo |
| --- | --- | --- | --- | --- |
| 8 nodi | `LR` | 1922 × 77 | 168,0 × 6,8 mm | **3,47 pt** |
| 8 nodi | `TB` | 275 × 750 | 76,3 × 207,8 mm | **11,00 pt** |
| 12 nodi | `LR` | 2923 × 62 | 168,0 × 3,6 mm | **2,28 pt** |
| 12 nodi | `TB` | 260 × 1118 | 56,4 × 242,0 mm | **8,59 pt** |

La banda della dispensa è 8-11 pt (D11): le due `LR` sono fuori banda di
un fattore 3-4, le due `TB` ci stanno (la catena di 8 nodi arriva al
tetto e viene riportata a 11,00).

**La serpentina non è una strada.** L'alternativa naturale — spezzare la
catena in righe con dei `subgraph` e un `direction LR` dentro — è stata
provata e scartata: quando ci sono archi che attraversano il gruppo,
Mermaid ignora il `direction` del sottografo. Due colonne da sei danno
3023 × 132 uu, cioè **2,21 pt**, peggio del `TB`. L'unica leva è la
direzione del grafo.

### 21.2 La regola: si misura, non si indovina

Il sorgente salvato **non cambia** (vincolo del committente: nessun
backfill, la scelta della disposizione vive nella resa). La catena di
decisione è:

1. `figure_compute/chain_layout.vertical_chain_variant(source)` — funzione
   PURA, gemella di `graph_rules`: riconosce una catena lineare dichiarata
   in orizzontale e ritorna il sorgente con il **solo** token di direzione
   cambiato (`LR` → `TB`, `RL` → `BT`), byte per byte identico altrove;
   `None` in ogni altro caso. Riconoscimento conservativo: intestazione
   `flowchart|graph LR|RL` come prima riga utile, con il `;` finale
   facoltativo come nel corpo (frontmatter e commenti `%%` prima sono
   ammessi, una direttiva `%%{…}%%` no), nessun
   `subgraph`/`end`/`direction`, archi solo semplici (`-->`, `---`),
   grado entrante e uscente al più 1, un solo componente connesso, almeno
   tre nodi.
   **Che cosa è uno spazio.** `str.strip()` e `String.prototype.trim()`
   non hanno lo stesso insieme: il primo toglie anche U+001C-U+001F e
   U+0085, il secondo anche U+FEFF. Le due parti sceglievano due
   direzioni diverse per lo stesso sorgente (misurato: 4 divergenze su 8
   casi avversari). Il riconoscimento usa l'**intersezione** dei due
   insiemi, scritta carattere per carattere in `_SPACE_CLASS` e nella
   costante `SPACE` del mirror: un BOM o un separatore di file non è uno
   spazio, è un carattere qualunque, e ferma il riconoscimento da tutte e
   due le parti.
2. `figure_render_service.render_chain_variants(...)` — dopo il
   pre-render, per le sole figure Mermaid che nel box di quella superficie
   escono **sotto** la banda e il cui sorgente è una catena orizzontale,
   rende anche la variante, in un unico batch. La variante viaggia accanto
   all'originale in `RenderedFigure.chain_variant` e non entra mai nella
   cache al posto suo: ha una chiave propria, `(mermaid, sha256 del
   sorgente variante, THEME_VERSION)`.
   Il riconoscimento legge il sorgente **sanificato**, cioè quello che il
   renderer disegna (`MermaidRenderer.sanitize`: fence markdown residui,
   righe-segnaposto `mermaid`/`all`, caratteri di controllo). Sul grezzo
   una sola riga spuria — di quelle che l'AI emette e che il gate D8 del
   salvataggio accetta — faceva perdere la catena: la pagina disegnava la
   catena orizzontale e la variante non veniva nemmeno tentata, mentre la
   vista, che sanifica prima di misurare, la ribaltava.
3. `course_lesson_pdf_service._figure_width_style` — **misura** le due
   varianti nel box vero della figura e tiene quella con il corpo più
   grande. Se la variante non migliora, resta l'originale. La scelta
   emette `figure_direction_flipped` con `asset_id`, `text_pt_before` e
   `text_pt_after`, e la voce del `figure_fit_report` porta
   `direction_flipped=True`.

Il punto 3 è il cuore: nessuna soglia di «quanti nodi sono troppi»,
nessuna euristica sul contenuto. Si rende, si misura, si sceglie. Un
falso positivo del riconoscimento non può alterare il disegno — la
variante differisce dall'originale per due caratteri — e al massimo costa
una resa che la misura poi scarta.

Box per superficie: la dispensa usa il box **vero** del template
(`lesson_mermaid_box_mm`), quindi la decisione del pre-render coincide con
quella della resa. Le slide usano la slide di **riferimento**
(`reference_slide_figure_box_mm`, 255 × 86,6 mm: un blocco, titolo e
didascalia di una riga, lo stesso caso dell'oracolo D12), perché il box
vero nasce per pagina dal testo reale; su una slide più densa il box è più
basso, e lì la misura decide comunque fra le varianti disponibili. Sulla
slide, che è larga e bassa, la variante verticale di norma **perde**: la
catena di 12 nodi dà 3,46 pt in `LR` e 3,07 pt in `TB`, e resta `LR`.

### 21.3 Il costo

Una resa in più **solo** per le figure fuori banda che sono catene
orizzontali. Misurato sull'export di quattro lezioni reali del docente
(26 asset, 18 Mermaid):

| lezione | asset | Mermaid | catene orizzontali | pre-render | varianti | seconda volta |
| --- | --- | --- | --- | --- | --- | --- |
| M2.L2 | 14 | 6 | 0 | 4,21 s | **0,00 s** | 0,000 s |
| M4.L1 | 4 | 4 | 0 | 0,71 s | **0,00 s** | 0,000 s |
| M4.L3 | 4 | 4 | 0 | 0,69 s | **0,00 s** | 0,000 s |
| M12.L7 | 4 | 4 | 0 | 0,77 s | **0,00 s** | 0,000 s |
| M2.L2 + catena da 12 nodi | 15 | 7 | 1 | 2,27 s | **0,63 s** | 0,001 s |

Sulle quattro lezioni reali il costo è **esattamente zero**: nessuna delle
18 figure Mermaid è una catena orizzontale, quindi non parte nessun
Chromium in più. Aggiungendo la catena del docente il costo è una pagina
Chromium, 0,63 s a cache fredda e 0,001 s dalla seconda volta. La chiave
di cache della variante è la stessa per le due superfici: una pipeline che
esporta dispensa e slide la paga una volta sola.

**Due effetti da conoscere.** Sulla superficie delle slide la variante
viene resa e poi **sempre** scartata per le catene (misurato su tre: 8
nodi 6,09 contro 4,68 pt, 12 nodi 4,01 contro 3,07, 40 nodi 1,17 contro
0,90): su un box largo e basso il verticale non può vincere, quindi lì la
resa in più è costo senza beneficio, ammortizzato dalla cache solo quando
si esportano anche le dispense. Resta comunque una misura e non una
regola: è il prezzo di non indovinare. E per una catena molto lunga il
ribaltamento migliora senza risolvere: 40 nodi nella dispensa vanno da
0,77 a 2,52 pt con `direction_flipped=True`, ma la variante è un nastro
largo 11,8 mm su 168 e la voce resta in `figure_fit_out_of_band`. Nessuna
delle due disposizioni si legge: quel caso lo chiude il prompt (§21.5),
non la resa.

### 21.4 Nella vista

`MermaidDiagram.tsx` applica la stessa regola a schermo: se il diagramma
reso esce sotto la banda nel **box di riferimento della sua superficie** e
il sorgente è una catena orizzontale, rende anche la variante e tiene
quella con il corpo più grande. La vista non conosce il template del
docente: usa il box di riferimento proprio perché la disposizione che si
vede a schermo sia quella che arriverà nel documento esportato. Il
componente riceve la superficie dal chiamante (`variant`, `lesson` per
difetto): la dispensa misura su `LESSON_REFERENCE_BOX_MM` (168 × 242 mm),
la vista delle slide su `SLIDE_REFERENCE_BOX_MM` (255 × 86,6 mm, mirror di
`reference_slide_figure_box_mm`), ciascuna con la propria banda. Decidere
sempre con il box della dispensa faceva dire il contrario all'anteprima e
all'export: per la catena di 12 nodi la dispensa ribalta (2,64 → 8,59 pt)
e la slide no (4,01 pt in `LR` contro 3,07 in `TB`). La larghezza **resa**
resta quella di sempre (`fittedWidthPx`, senza box e con la banda del web:
sul web il limite è la colonna).

Prima di misurare, la vista sanifica come il backend: le righe spurie e
in più i caratteri di controllo di `_CONTROL_CHARS_RE` (`isControlChar`,
scritto come intervallo di codici perché `no-control-regex` vieta il
letterale). Così le due parti partono dagli stessi byte.

Il mirror della funzione pura è `frontend/src/lib/chainLayout.ts`, con
parità provata dalla fixture condivisa
`backend/tests/fixtures/chain_layout_cases.json`, eseguita dai due lati
come `figure_scale_cases.json`.

### 21.5 Nei prompt

Il catalogo dei tipi di Fase 3 e di Fase 4 porta la regola, entro il
budget dei prompt esistente (`MAX_SYSTEM_P3` 28.900, `MAX_SYSTEM_P4`
15.400, invariati): «Catena lineare oltre quattro passi: `flowchart TB`;
`LR` se corta o ramificata.» Il testo è riportato verbatim in
[PROMPTS](../PROMPTS.md) e `scripts/check_prompts_md.py` lo verifica.

La regola nel prompt e la regola nella resa sono complementari: il prompt
riduce i casi, la resa li chiude comunque, perché il contenuto già
generato non si tocca.

### 21.6 Gli oracoli

| che cosa | test |
| --- | --- |
| Le due catene reali del docente: pt prima e dopo sul PDF vero | `tests/test_lesson_pdf_chain_direction.py::test_the_lesson_pdf_measures_both_and_keeps_the_bigger` (WeasyPrint `.render()`, corpo letto dai box reali) |
| Nessun ribaltamento per il grafo con diramazioni (`fig_market_structure`, 7 nodi e 9 archi), per un sorgente già `TB`, per una catena già in banda, per un sorgente con `subgraph` | stesso file, `::test_only_the_horizontal_chains_get_a_variant` |
| Il sorgente salvato non cambia, e la variante differisce per il solo token di direzione | `::test_the_saved_source_never_changes` e `tests/test_chain_layout.py::test_only_the_direction_token_changes` |
| Le figure non interessate conservano lo stesso identico SVG; i 15 modelli Mermaid degli editor non sono catene | `::test_the_untouched_figures_keep_the_very_same_svg`, `::test_no_editor_template_is_a_horizontal_chain` |
| Parità Python ↔ TypeScript sui 36 casi della fixture, compresa la classe avversaria degli spazi (BOM, U+001C, U+0085, U+00A0, U+2028, U+3000) | `tests/test_frontend_figure_layout.py::test_frontend_chain_layout_matches_the_shared_fixture` |
| Il candidato si decide sul sorgente sanificato: fence in coda, riga `all`, carattere di controllo | `tests/test_figure_render_service.py::test_the_chain_is_recognised_on_the_source_the_renderer_draws` |
| La vista toglie gli stessi caratteri di controllo del backend, e decide la direzione sul box della propria superficie | `tests/test_frontend_figure_layout.py::test_the_view_strips_the_control_chars_the_backend_strips`, `::test_the_view_decides_the_direction_on_the_box_of_its_own_surface` |
| La voce entra nel `figure_fit_report` | `tests/test_lesson_pdf_figures.py::test_figure_fit_report_summary_lists_the_out_of_band_figures` |

## 22. Monocultura: quali figure e quante (18 settembre 2026)

Il difetto non è la singola figura, che i capitoli 18-21 hanno reso
leggibile: è che quasi tutte le figure sono la **stessa** figura, e che
sono **poche**. Il docente esporta quattro lezioni di quattro corsi
diversi e le conta.

### 22.1 La misura di partenza

Delle **14 figure generate dal modello**: 13 `mermaid` e una `function`.
Zero `vegalite`, zero `dot`. Dentro Mermaid, **13 flowchart su 13**:
nessun sequence, state, class, er, mindmap, timeline, quadrant, pie,
sankey, radar, treemap, xychart. Le altre 12 figure dell'export (id
`A3`..`A14` in M2.L2) sono campioni di catalogo inseriti a mano dal
docente dagli editor (§16), non prodotte dal modello: contate insieme
alle altre farebbero sembrare vario un export che non lo è.

| lezione | figure generate | formati | tipi Mermaid |
| --- | --- | --- | --- |
| M2.L2 — Analisi Matematica (limiti) | 2 | mermaid 1, function 1 | flowchart 1 |
| M4.L1 — Economia degli intermediari | 4 | mermaid 4 | flowchart 4 |
| M4.L3 — Misure Meccaniche e Termiche | 4 | mermaid 4 | flowchart 4 |
| M12.L7 — Analisi Matematica (integrali) | 4 | mermaid 4 | flowchart 4 |

Due esempi dicono il costo didattico meglio della tabella. **M2.L2** è
una lezione sui limiti di funzioni reali: il contenuto chiede grafici di
funzione — `sin(1/x)` che non ha limite, il confronto fra `x`, `x²` e
`sin x`, gli asintoti, la costruzione epsilon-delta — e il modello ha
prodotto un flowchart di «successioni che convergono» più un solo
grafico. **M12.L7** è sui teoremi integrali: quattro flowchart, nessuna
figura geometrica.

Il conteggio è l'altra metà del difetto: **tre lezioni su quattro hanno
esattamente quattro figure**, e nessuna ne ha di più. Il prompt diceva
«1-3 figure per lezione», ma quel tetto **non era il vincolo**: tre
lezioni ordinarie su quattro lo superavano già. A mancare era il criterio
— quante figure chiede il contenuto, sezione per sezione — e il
risultato, quattro figure ovunque, è la firma di una consuetudine, non
di un limite. La correzione (numerosità legata alle sezioni) resta
quella giusta; la diagnosi, scritta per la prima volta come «il tappo era
1-3», era imprecisa sulla causa ed è corretta qui.

### 22.2 Perché il catalogo non bastava

I quattro formati esistono e funzionano dal capitolo 2; il catalogo dei
quindici tipi Mermaid e delle otto famiglie Vega-Lite è nel prompt dal
capitolo 17. Il difetto era **l'ordine del ragionamento**. Il blocco
apriva così:

> struttura, processo o relazione qualitativa → `mermaid`, con il tipo
> scelto dal contenuto: flowchart (processo, decisione), …

Il primo formato nominato era `mermaid`, raggiunto dal criterio più largo
che si possa scrivere («struttura, processo o relazione qualitativa»:
quasi ogni contenuto universitario ci rientra), e il primo tipo nominato
era il flowchart. Un modello che legge in ordine non arriva mai a
`function`: ha già trovato una casella che accetta tutto. E i criteri
stavano **fra parentesi dopo il nome del formato** — «flowchart
(processo, decisione)» — cioè si leggevano partendo dal formato, non dal
contenuto.

### 22.3 Le due regole nuove

**Scelta** — il blocco `FORMATI DELLE FIGURE` apre ora con la regola,
non con l'elenco: «per OGNI figura decidi prima CHE COSA deve far
vedere, poi leggi qui sotto quale formato lo mostra. Mai il contrario:
non partire dal formato che sai già scrivere.» Le frecce sono girate —
si legge «contenuto → formato», non «formato (contenuto)» — e l'ordine
delle righe è invertito: `function`, `vegalite` e `dot` vengono **prima**
del flowchart, che è dichiarato esplicitamente «l'ULTIMA scelta, non la
prima: un elenco di concetti collegati da frecce NON è un processo».
Ogni riga porta due o tre esempi legati alle discipline (`sin(1/x)`
vicino a zero, l'area fra due curve, un automa a stati finiti, l'albero
di derivazione di una grammatica): un criterio senza esempio resta
astratto.

Due blocchi chiudono la regola. `REALTÀ`: mai inventare numeri per avere
un grafico (`vegalite` solo su dati dei documenti o notori e verificabili
nel testo), mai una figura decorativa, «meglio una figura in meno che una
inventata». Senza, la spinta alla varietà diventa una spinta a inventare
dati per poter disegnare un Vega-Lite. `VARIETÀ`, dichiarata come regola
editoriale e non come obbligo cieco: in una lezione con almeno tre
figure, **se il contenuto lo consente**, non più di due flowchart; in
matematica, fisica e ingegneria, dove una sezione lega due grandezze, si
valuta esplicitamente una figura `function`.

**Numerosità** — in `REQUISITI — ASSET VISIVI` il tetto «1-3 figure per
lezione» è sostituito dal criterio: «la figura segue il contenuto,
sezione per sezione: ogni sezione che introduce una struttura, un
andamento, una relazione fra grandezze, dei dati o un processo merita la
SUA figura. Indicativamente 4-8 per lezione ordinaria, 0-2 per la lezione
introduttiva.» L'antidoto sta nella stessa voce, non trenta righe più in
basso: «Non è una quota da riempire: non inventare contenuto per arrivare
al numero, e una sezione puramente discorsiva resta senza figura.»

La Fase 4 riceve la regola di scelta in forma breve, senza `function`
(che in Fase 4 non è fra i formati, A1: una relazione fra grandezze si
REFERENZIA dalla figura di Fase 3, non si ricrea), e il budget delle
slide è ritarato sulla nuova numerosità.

### 22.4 Gli altri tetti, verificati uno per uno

Alzare il numero nel prompt non serve se un tetto a valle lo riporta
giù. Nessun altro cambio è stato necessario, ma ognuno è stato guardato:

| tetto | valore | esito |
| --- | --- | --- |
| Schema JSON strict di Fase 3 | nessun `maxItems` su `visual_assets` | già libero |
| `OPENAI_LESSON_CONTENT_MAX_TOKENS` | 32.000 | il `content_raw` reale di una lezione a 4 figure misura ~10,5k token, quello a 14 figure ~11,1k: una figura pesa in mediana 568 caratteri (~142 token). Passare da 3 a 8 figure costa ~700 token: margine ampio |
| Range delle slide di Fase 4 | `_expected_slide_range` + una slide per ogni asset visivo, tabella e `new_asset` | il tetto **cresce già** con il numero di figure (`materialize_lesson_slides`): con 8 figure e 2 tabelle il totale sta nel range per 15, 30, 45, 60 e 90 minuti |
| `_VALIDATION_TIMEOUT_S` (batch JS Mermaid/KaTeX) | 90 s per lezione | una sola pagina headless per tutta la lezione: il costo per figura è un `page.evaluate` |
| `FIGURE_RENDER_TIMEOUT_SECONDS` | 20 s per figura | `vegalite`/`dot`/`function` sono validati in sequenza, uno per volta |

La misura con il motore reale (`asset_validation_service._validate_slots`,
Chromium + matplotlib/sympy + Vega), macchina di sviluppo:

| batch | tempo |
| --- | --- |
| 3 figure miste (2 mermaid + 1 function) | 0,9-1,6 s |
| 8 figure miste (4 mermaid, 1 dot, 1 vegalite, 2 function) | 1,3-2,4 s |
| 8 figure tutte calcolate (dot, vegalite, function: il caso peggiore, validazione sequenziale) | 1,33 s |

Otto figure costano **meno di tre secondi** di validazione: due ordini di
grandezza sotto il timeout di batch, e la generazione OpenAI della
lezione ne dura decine. Il costo che cresce davvero è quello dei token di
output, ~700 in più su un cap di 32.000.

### 22.5 La diagnostica, perché la prossima misura non richieda l'export

Per contare 13 flowchart su 14 il docente ha dovuto esportare il database
e riaprire le lezioni a una a una: in produzione del mix non restava
traccia. `app/services/figure_mix.py` (modulo puro, libreria standard più
`graph_rules`) calcola il conteggio per formato e, dentro Mermaid, per
tipo di diagramma. Lo usano due chiamanti, apposta gli stessi:

- `materialize_lesson_content` emette `lesson_content_figure_mix`
  (`figures`, `formats`, `mermaid_types`) a ogni lezione materializzata e
  aggiunge `lesson_content_figure_monoculture` come **warning** quando
  una lezione con almeno tre figure usa un solo formato e un solo tipo.
  Non blocca nulla: una lezione con tre flowchart resta valida e va in
  `ready`. È una misura, non un gate;
- `scripts/measure_asset_refs.py` guadagna la **sezione (c)**: mix per
  lezione e in aggregato, con la quota di ciascun formato e di ciascun
  tipo Mermaid, e il numero di lezioni in monocultura. Stesso comando che
  il docente ha già usato per le altre misure.

Contare con la stessa funzione in tutti e due i posti è il punto: se
divergessero, la misura sull'export e i log direbbero due cose diverse
sullo stesso corso.

### 22.6 Gli oracoli

| che cosa | test |
| --- | --- |
| La regola di scelta mette il contenuto prima del formato, `function`/`vegalite`/`dot` prima del flowchart, e il flowchart è dichiarato ultima scelta | `tests/test_prompt_figures.py::test_p3_selection_rule_puts_the_content_first_and_the_flowchart_last` |
| Ogni formato porta i propri esempi disciplinari | `::test_p3_selection_rule_binds_each_format_to_a_discipline_example` |
| I vincoli di realtà e la varietà hanno la stessa forza, e il freno non è un numero | `::test_p3_reality_and_variety_rules_are_stated_with_the_same_force` |
| La numerosità è sezione per sezione, con l'ordine di grandezza 4-8 / 0-2, e il vecchio «1-3 figure per lezione» non è in nessuna variante | `::test_p3_figure_count_follows_the_content_section_by_section` |
| La numerosità porta il proprio antidoto («non è una quota da riempire») | `::test_p3_figure_count_is_not_a_quota_to_fill` |
| Fase 4 ripete la scelta senza offrire `function` e il budget delle slide è ritarato | `::test_p4_selection_rule_mirrors_phase3_without_offering_function`, `::test_p4_slide_budget_accounts_for_the_new_figure_count` |
| Il range delle slide ha posto per otto slide dedicate, a ogni durata | `::test_slide_range_has_room_for_eight_dedicated_figure_slides` |
| Il budget dei prompt resta sotto la guardia dichiarata, nella variante di rigenerazione compresa | `tests/test_prompt_register.py` (`MAX_SYSTEM_P3` 31.500, `MAX_SYSTEM_P4` 18.200), `::test_p3_regeneration_variant_stays_under_guard`, `::test_p4_regeneration_variant_stays_under_guard` |
| La chiusa «Dati illustrativi» non autorizza numeri inventati, in P3 e in P4 | `tests/test_prompt_figures.py::test_p3_illustrative_label_is_not_a_licence_to_invent_numbers`, `::test_p4_repeats_the_reconciliation_of_the_illustrative_label` |
| Le rette e i polinomi ausiliari di Vega-Lite non contraddicono la riga sulle funzioni | `::test_p3_auxiliary_polynomials_do_not_contradict_the_function_rule` |
| P4 nomina ogni tipo Mermaid una volta sola, con il proprio criterio e `classDiagram` compreso | `::test_p4_names_every_mermaid_type_once_with_its_own_criterion` |
| Una figura di Fase 3 che nessuna slide referenzia è segnalata, e otto figure con la loro slide non lo sono | `tests/test_lesson_slides_asset_coverage.py` |
| `single_mermaid_type` non dichiara un tipo unico dove unico non è | `tests/test_lesson_content_figure_mix.py::test_the_single_mermaid_type_is_none_when_there_is_no_monoculture` |
| La colonna «senza slide» della sezione (c) conta le figure mai arrivate a una slide | `::test_script_section_c_counts_the_figures_that_never_reached_a_slide` |
| Il mix conta formati e tipi Mermaid, legge sia i modelli Pydantic sia i dizionari di `content_raw`, ordina in modo stabile | `tests/test_lesson_content_figure_mix.py` (prima metà) |
| Il log è emesso alla materializzazione con i conteggi giusti su quattro formati; il warning scatta con tre flowchart e non con due flowchart più un grafico di funzione | `::test_materialization_logs_the_mix_of_four_formats`, `::test_three_flowcharts_warn_and_the_lesson_is_still_materialized`, `::test_two_flowcharts_and_a_function_graph_do_not_warn` |
| La sezione (c) dello script riporta lo stesso mix su un export della forma di quello reale | `::test_script_section_c_reports_the_mix_per_lesson_and_aggregated`, `::test_script_and_materialization_share_one_measure` |
| `docs/PROMPTS.md` è identico ai prompt renderizzati | `scripts/check_prompts_md.py` |

### 22.7 La revisione del 20 settembre: coerenza del prompt, guardia di P4, figure che non arrivano alle slide

La campagna è stata riletta riga per riga prima di mandarla in
produzione. Quattro correzioni, tutte misurate.

**La guardia di P4 non copriva il prompt che parte davvero.**
`openai_lesson_slides_service` concatena `REGENERATION_SUFFIX` (803
caratteri) al system prompt a ogni rigenerazione delle slide: il prompt
inviato misurava 17.049 caratteri contro una guardia di 16.900. I due
test che pinnavano il tetto misuravano `_system_prompt("it")` senza
suffisso, e uno dichiarava in docstring che «la variante con i default è
la più lunga» — falso due volte, per il suffisso e per le etichette reali
della tassonomia (`zh-cn` più il ruolo e l'EQF più lunghi della
migrazione 0009 valgono 16.674 contro 16.630). Fase 3 aveva già
`test_p3_regeneration_variant_stays_under_guard`; ora ce l'ha anche Fase
4, e `MAX_SYSTEM_P4` sale a **18.200**, cioè la misura reale (17.474) più
il ~4% della convenzione. Nota di correzione: il messaggio del commit
precedente attribuiva a P4 la misura «28.003», che è il VECCHIO P3 con
ruolo/stile/EQF interpolati; P4 valeva 16.246.

**`REALTÀ` e `ONESTÀ DEI DATI` dicevano il contrario l'una dell'altra.**
A otto righe di distanza il prompt vietava di inventare numeri per avere
un grafico e poi permetteva di chiudere una figura numerica con «Dati
illustrativi, non sperimentali» *in alternativa* alla fonte: letta da un
modello, la seconda è il permesso esplicito di produrre numeri non
documentali purché etichettati. Siccome lo zero-Vega-Lite misurato è
proprio il difetto da correggere, era la regola che decideva se il
modello avrebbe osato una `vegalite`. Ora la chiusa etichetta i soli
valori schematici — una scala di comodo, una curva di esempio — e rimanda
a `REALTÀ`; la stessa frase è riscritta in P4, dove le due righe stavano
nello stesso paragrafo.

**I polinomi ausiliari di Vega-Lite.** «Rette e polinomi ausiliari con
`data.sequence`» e «le potenze su una `sequence` NON si tracciano in
Vega-Lite» si annullavano a quarantasei righe di distanza: un polinomio è
una potenza. L'ausilio è ora dichiarato per quello che è, un livello
SOVRAPPOSTO ai dati, «mai come figura a sé».

**L'elenco Mermaid di P4 era spezzato in due**, con sei tipi dentro
`SCELTA` (criterio accanto) e otto nudi dentro `CATALOGO`; `classDiagram`
non compariva in nessuno dei due pur essendo in `MERMAID_D8_TYPES`. Ora
l'elenco è uno solo, dentro `SCELTA`, un criterio per tipo: è la tesi
stessa della campagna, e vale anche per la lista breve di Fase 4. In P3
la riga «MERMAID 11. Tipi ammessi: …» ripete i quindici nomi già presenti
nella regola di scelta (207 caratteri): resta, perché è generata da
`MERMAID_D8_TYPES` e fa da whitelist esplicita accanto alla riga
«Esclusi:», e il margine di `MAX_SYSTEM_P3` la regge.

**La figura che non arriva alla slide.** La Fase 3 aveva la sua misura
(`lesson_content_figure_mix`), la Fase 4 no. `materialize_lesson_slides`
valida che ogni `references_assets` esista e che una slide non porti più
di un asset visivo, ma nessun controllo diceva che ogni figura avesse la
sua slide: sull'export reale M4.L1 ha quattro figure e quattordici slide,
e `fig_markets_intermediaries` non compare in nessun `references_assets`.
Una figura scartata sparisce dal deck e dal video (la Fase 5 parla le
slide che esistono) e resta solo in coda alla dispensa, accodata da
`figure_numbering.append_uncited_asset_refs`. Il warning nuovo —
`lesson_slides_unreferenced_assets` (`unreferenced_assets`,
`phase3_assets`, `total_slides`) — non blocca niente, come il gemello di
Fase 3, e la stessa misura è la colonna «senza slide» della sezione (c)
di `measure_asset_refs.py`:

```
| M4.L1 | Economia degli intermediari finanziari | 4 | 1 | mermaid 4 | flowchart 4 | SI | 2 |
senza slide in M4.L1: fig_markets_intermediaries, tab_markets_vs_intermediaries
```

Con il tetto vecchio la perdita c'era già; con 4-8 figure cresce con il
numero, e ora si vede senza riaprire le lezioni.

**Nota sul lato slide.** Il conteggio non scarta nulla per ragioni di
numero: `_expected_slide_range` somma `asset_slides` al tetto e il
fallimento duro è al doppio. Resta fuori dalla campagna, per vincolo
esplicito, la taratura di P5: con un deck di ~30 slide le bande
suggerite (120-180 s per una slide concept densa) non stanno più nella
somma pinnata a ±5% dei minuti di lezione. Non blocca —
`course_lesson_speech_service` riscala le durate e logga
`lesson_speech_durations_rescaled`, l'hard fail è solo fuori da [0,5x,
2x] — ma è il primo candidato del giro successivo.

### 22.8 Che cosa resta da verificare

Il prompt non è stato provato contro il modello: nessuna chiamata
OpenAI. La prova è del docente, in produzione, e la misura è già pronta —
`scripts/measure_asset_refs.py` sull'export, o la riga
`lesson_content_figure_mix` nei log. Le domande sono tre: la quota di
flowchart scende sotto il 90%; `vegalite` e `dot` compaiono almeno una
volta; le lezioni di matematica producono figure `function` dove il
contenuto lega due grandezze. Se il conteggio sale ma la qualità scende —
figure riempitive, numeri inventati — è il blocco `REALTÀ` a non aver
tenuto, e va irrigidito prima di rialzare l'intervallo.

### 22.9 Chiusura della revisione del 20 settembre (secondo giro)

Il secondo giro ha chiuso con `ok=true` e sette rilievi, due minori e
cinque note. Le correzioni fatte, con la misura che le motiva.

- **Il tetto di tempo della resa ora scala con il lotto.** Era per BATCH e
  non per figura: raddoppiando le figure si dimezzava il budget di
  ciascuna. `_batch_timeout` prende il massimo fra il tetto della singola
  resa, il pavimento del formato (60 s per Mermaid, costo fisso di
  Chromium e della CDN) e una quota di 6 s per figura del lotto. Con otto
  figure il batch ha 48 s invece dei 20 del tetto di base. Il tempo
  misurato resta molto sotto: otto figure in quattro formati si rendono in
  1,50 s a batch caldo, contro 1,60 s per quattro figure.
- **Il prompt non promuove più un formato spento.** La regola di scelta
  offre `function`, `vegalite` e `dot`, ma l'enum dello schema segue
  `available_formats()`, cioè i kill-switch: con `vegalite` spento il
  prompt chiedeva proprio ciò che il modello non poteva produrre. Ora
  `_system_prompt` riceve i formati disponibili e, quando ne manca uno,
  aggiunge una riga «NON DISPONIBILI in questo corso: …». Con tutti i
  formati accesi il blocco non compare e `PROMPTS.md` resta verbatim.
- **La guardia di lunghezza di P4 cerca il massimo invece di sceglierlo.**
  Il test misurava la variante con l'etichetta di stile più lunga della
  tassonomia («Collaborativo», 13 caratteri), ma il massimo reale è il
  ripiego del servizio quando il termine manca: «indicato nel messaggio»
  (22) e «(non specificato)» (17), che un corso senza
  `stile_insegnamento_term_id` produce davvero. Ora il test prova le tre
  forme per ogni durata e confronta il massimo: 17.483 contro un tetto di
  18.200.

Resta dichiarato, senza correzione perché non è un difetto ma un dato da
conoscere.

- **Il costo per lezione non cresce solo dei token del prompt.** La stima
  di 600 token di ingresso vale per il prompt (P3 da 28.003 a 29.810
  caratteri, P4 da 15.116 a 16.560), ma il costo dominante è a valle:
  ogni figura pesa 539-606 caratteri dentro `content_raw`, quindi passare
  da quattro a otto figure aggiunge circa 800 token di USCITA alla Fase 3,
  che tornano come ingresso della Fase 4 e, via `slides_raw`, della Fase
  5, più le slide dedicate (432-622 caratteri l'una). L'ordine di
  grandezza resta sotto il centesimo per lezione con i modelli
  predefiniti.
- **Il tetto dei token di uscita della Fase 3 regge.** Misura sulle
  quattro lezioni reali: il JSON prodotto sta fra 41.100 e 44.500
  caratteri, cioè circa 14.000 token, contro `openai_lesson_content_max_tokens`
  = 32.000. Quattro figure in più ne aggiungono meno di mille.
- **La Fase 3 non conosce la durata della lezione**, quindi «4-8 figure»
  è piatto su ogni durata, mentre il dimensionamento del testo scala per
  profondità e quello delle slide per minuti. Il rischio è sul taglio
  breve (una lezione da 15 minuti con otto figure avrebbe più slide
  dedicate che di contenuto); la regola è però formulata sezione per
  sezione, e nelle quattro lezioni reali le sezioni sono sempre sei.

### 22.10 Prima misura in produzione (20 settembre 2026) e la riga nel messaggio della lezione

Il docente ha generato 42 lezioni con il prompt nuovo, tutte dopo l'avvio
del container aggiornato (16:29 UTC, prima misura alle 16:36). Il
conteggio viene dai log `lesson_content_figure_mix`.

| figure per lezione | lezioni |
| --- | --- |
| 3 | 32 |
| 2 | 8 |
| 1 | 2 |
| 4 o più | 0 |

| formati | lezioni |
| --- | --- |
| solo `mermaid` | 32 |
| `mermaid` + `function` | 3 |
| `mermaid` + `dot` | 5 |
| solo `function` | 1 |
| `mermaid` + `dot` + `function` | 1 |

Due letture opposte, ed entrambe contano. La varietà si è mossa: dieci
lezioni su quarantadue portano un formato diverso dal flowchart, e una è
fatta di soli grafici di funzione, dove prima l'export reale ne aveva uno
su quattordici figure. Il numero no: la distribuzione è tagliata netta a
tre, e non esiste alcun tetto nel codice — schema senza `maxItems`,
nessun limite nel servizio, il prompt renderizzato dice davvero «4-8».

Il modello si ferma da solo, e la causa più probabile è la posizione
dell'istruzione: una riga dentro un prompt di sistema da trentamila
caratteri, in concorrenza con il blocco `REALTÀ` che gli chiede prudenza.
Da qui la correzione: il numero entra anche nel messaggio utente della
singola lezione, che è corto e specifico, con l'intervallo già calcolato
sulle sezioni di QUELLA lezione (`_figure_count_request`): «questa lezione
ha 6 sezioni; prevedine da 4 a 6 …», più la riga della scelta del formato.
L'intervallo non promette mai più figure delle sezioni disponibili e il
tetto resta otto; la lezione introduttiva resta a 0-2.

La prossima misura è di nuovo del docente, con lo stesso comando. Se il
numero non si muove neanche così, la leva successiva è lo schema
(`minItems` sull'array), che però è un obbligo cieco: produrrebbe figure
anche dove il contenuto non le chiede, ed è la ragione per cui non è la
prima scelta.

### 22.11 Seconda misura in produzione: la varietà segue il numero

Stesse 42 lezioni, rigenerate dopo la riga nel messaggio della singola
lezione (`_figure_count_request`). Conteggi dai log
`lesson_content_figure_mix`.

| | prima riga solo di sistema | con la riga nel messaggio |
| --- | --- | --- |
| figure totali | 114 | 154 |
| media per lezione | 2,7 | 3,7 |
| lezioni con 4 o più figure | 0 su 42 | 22 su 42 |
| Mermaid | 101 (89%) | 92 (60%) |
| Graphviz `dot` | 6 (5%) | 33 (21%) |
| grafici `function` | 7 (6%) | 29 (19%) |
| Vega-Lite | 0 | 0 |
| lezioni con il solo Mermaid | 32 su 42 | 13 su 42 |
| lezioni in monocultura (log dedicato) | 32 | 10 |

Il dato interessante non è solo che il numero è salito: è che la varietà
è salita CON il numero, senza toccare la regola di scelta. Finché il
modello si concedeva tre figure sceglieva tre volte lo strumento più
generico; con quattro o cinque da distribuire fra sei sezioni ha iniziato
a usare il grafo per le strutture e il grafico di funzione per gli
andamenti. Mermaid passa dall'89% al 60% delle figure, e le lezioni fatte
di soli diagrammi di flusso scendono da 32 a 13.

Vega-Lite resta a zero, ed è coerente con il vincolo dichiarato: il
prompt lo ammette solo su dati presenti nei documenti del corso e vieta di
inventarli. I corsi misurati (matematica, misure, teoria) non ne portano.
La verifica vera sarà un corso con dati reali: se anche lì resta a zero,
il vincolo è troppo stretto e va allentato, non il catalogo.

Restano dieci lezioni in monocultura su quarantadue. Il passo successivo,
se serve, è il suggerimento per sezione: leggere lo scopo dichiarato di
ogni sezione della scaletta e proporre nel messaggio della lezione il
formato adatto a quella sezione, invece di lasciare la scelta a una regola
generale.
