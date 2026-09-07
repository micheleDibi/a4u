# 17 — Figure accademiche: Mermaid 11, Vega-Lite, DOT e figure calcolate

Sintesi di progettazione (Fase B) delle **quattro famiglie di asset
visivi** generati o modificati in Fase 3 (dispense) e Fase 4 (slide):
diagrammi **Mermaid 11**, grafici **Vega-Lite** (vl-convert), grafi
**Graphviz DOT** e figure matematiche **calcolate** (`function`: sympy +
matplotlib). Le quattro famiglie passano da un **registro di renderer
unico**, condividono un **tema accademico unico** e ricevono didascalie
«Figura N.» identiche in editor, vista lezione, slide, PDF dispensa, PDF
slide e frame video.

Questo documento è la versione **v1**, scritta prima del codice del
registro come richiede §5 del brief: fissa le firme, gli algoritmi e le
decisioni che i work package (WP) successivi implementano. Le sezioni
«Verifiche e consegna» contengono segnaposto espliciti che il WP6
completa. Lo stato di avanzamento è nella sezione 1.

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

I riferimenti a file e righe sono al commit `8ce8160` del branch
`feat/academic-figures` (HEAD al momento della v1); dove un modulo non
esiste ancora, la sezione descrive il contratto che il WP indicato deve
rispettare.

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

Stato dei work package al momento della v1 (dettaglio in
`docs/courses/README.md` e nel report di consegna):

| WP | Contenuto | Stato |
|---|---|---|
| WP2a | alias `VisualAssetFormat`, `figure_theme.py` + `figureTheme.ts`, setting `figure_*`, `.env.example`, compose, pyproject, Dockerfile, CI | committato (`bb17b49`, `b97ff74`) |
| WP1 | Mermaid 11: `mermaid_prerender.py` estratto, pin unico, `htmlLabels:false` top-level BE+FE, prompt fix/digitalizzazione, script `revalidate_mermaid_assets.py`, test D8 | committato (`8ce8160`) |
| WP2b-0 | questo documento (v1) | questo commit |
| WP2b | `figure_render_service.py`, `svg_normalize.py`, `figure_compute/`, dispatch del validatore, fix AI, localizzazione, PATCH 422, builder degli schemi OpenAI, filtro log | committato (`61689b6`; correzioni `5cac685` e giro 2: gate Mermaid `initialize`/frontmatter, nomi DOT quotati, JSON annidato) |
| WP7 | formato `function` (schema, parsing, calcolo, disegno, endpoint) | committato (livello 1: `function_study`, `tangent`, `area`, `family`, `level_curves`; il livello 2 — parametriche, polari, coniche, successioni — resta lavoro successivo, A9) |
| WP4 | numerazione, partial `figure.html.j2`, PDF dispensa/slide, frame video | da fare |
| WP3 | prompt P3/P4/P5, guardie di lunghezza, `PROMPTS.md` | da fare |
| WP5 | frontend: `FigureFrame`, renderer ed editor per formato, dialog, i18n | da fare |
| WP6 | documentazione, misure, consegna | da fare |

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

async def render_svg_map(assets: list[dict], *, language: str) -> dict[str, str]: ...
async def validate_visual_assets_or_raise(assets, *, previous, loc_root, code) -> None: ...
```

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

### 2.2 `render_svg_map`: l'unico punto asincrono

`render_svg_map(assets, *, language)` è chiamata dai tre materializzatori
(PDF dispensa, PDF slide, frame video) al posto dell'attuale
`_prerender_mermaid_for_lesson` (`course_lesson_pdf_service.py:633-656`)
e `_prerender_mermaid_for_slides` (`course_lesson_slides_pdf_service.py:271-290`).
È l'**unico** punto in cui compaiono `asyncio.to_thread`,
`asyncio.wait_for(settings.figure_render_timeout_seconds)` e
`asyncio.Semaphore(settings.figure_render_max_workers)`; il semaforo è
tenuto dal chiamante async e rilasciato anche su timeout, mai acquisito
dentro il thread.

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
3. raggruppa i restanti per formato e chiama **una** `render_svg_batch`
   per formato in `to_thread` sotto semaforo e `wait_for`: per Mermaid è
   `_prerender_mermaid_to_svg_batch_sync` con `_sanitize_mermaid_code`
   prima e `_strip_mermaid_max_width` dopo (un Chromium per lezione, come
   oggi); per gli altri formati la normalizzazione SVG (sezione 7);
4. ritorna `{asset_id: svg}`; le chiavi assenti attivano il fallback del
   partial (`<pre class="figure-fallback">`).

`render_svg_map` **non solleva mai**: timeout, eccezioni del renderer e
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
function_spec_invalid`; `msg` è troncato a 600 caratteri; per `function`
`loc` prosegue dentro la spec (`["visual_assets", i, "content",
"expressions", 0, "expr"]`). Il frontend legge `meta.errors` da
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
(`<script>`, `<table>`, `<svg>`, `<h1>` sono rifiutati come `<br>` e
`<b>`), applicata alle sole righe del corpo che non sono commenti `%%`
(un tag in un commento o nel frontmatter non viene renderizzato: giro 2).
Non sono tag e passano: le frecce (`-->`, `<|--`, `->>`, `<<->>`), le
annotazioni `<<interface>>`, un `<` isolato (`A[a < b]`) e un `<b` non
chiuso (`A[x <b] --> B`: la sezione degli attributi non attraversa `]`,
`)`, `}`). Falso positivo dichiarato: i tipi generici di `classDiagram`
scritti con `<…>` (`List<int>`) sono rifiutati; Mermaid vuole `List~int~`
e il messaggio lo suggerisce. Il parse JS resta nel batch di
`_validate_slots` (sezione 8.1): al salvataggio manuale il gate statico
basta (A15), il parse vive già nell'editor con la stessa major 11.

`render_svg_batch` delega a `mermaid_prerender._prerender_mermaid_to_svg_batch_sync`
(Playwright, pin `settings.mermaid_cdn_version`, `mermaid_initialize_js(use_max_width=True)`);
l'SVG Mermaid **non** passa dalla normalizzazione di sezione 7: la catena
resta byte-identica rispetto a oggi (A11, livello L3).

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
  `"`, `\\` conservato, `\`+newline ignorato, `"a" + "b"` uniti,
  commenti `/* */`, `//` e righe `#` saltati fuori dalle stringhe,
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
limite); ≤ 80 nodi, profondità ≤ 12.

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
viewBox. `function_plot` non produce mai raster (`<image>`): `contour` e
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
anti-doppia coda: `if caption.rstrip().endswith(tail): tail = ""`.

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
non errori di input); 403 da `require`. Il client chiama con `timeout:
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

- `THEME_VERSION = "2026.09.2"`: entra nella chiave di cache degli SVG; ogni
  modifica visibile del tema lo incrementa.
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
  11.17.2 da `test_mermaid_theme_palette`). Limite noto: `sankey-beta`
  colora i nodi con `schemeTableau10` di d3, hard-coded nel renderer.
  `security_level`: `loose` nei Chromium headless del backend, `strict` nel
  browser dell'utente (default di `mermaidConfig` nel `.ts`).
- Vega-Lite: `VEGALITE_THEME_CONFIG` (`figure_theme.py:458`): `font`,
  `background: "transparent"` (lo schema non ammette `null`), `view`
  360×220 senza bordo, `axis`/`legend`/`header`/`title`/`text` con font,
  dimensioni e colori del tema, `range.category = PALETTE`, `mark.color =
  PALETTE[0]`, `line.strokeWidth 2`, `point` pieno, `area` 0,35 con linea.
  È iniettato dal renderer: il modello non scrive mai `config` (D5).
- DOT: `DOT_DEFAULTS` (`graph`, `node`, `edge` con `fontname="Noto Sans"`,
  `bgcolor="transparent"`, nodi `box` arrotondati, colori dei neutri) e
  `dot_defaults_prelude(skip=…)`.
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
`FigureFrame.extraCaption`. L'output non contiene righe vuote perché nella
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
`render_svg_map` serve un hit della cache SVG di `function` solo se anche
il risultato è in cache (`FunctionRenderer.result_cached`), altrimenti
rimanda la figura al renderer, e `FunctionRenderer.render_svg` sull'hit
incompleto ricalcola con `render_function_sync` (nel thread di render con
semaforo e timeout, mai in quello dell'HTML), che ripopola la cache dei
risultati; l'SVG è byte-identico (`hashsalt` fisso). Se nonostante ciò il
risultato manca al momento della composizione (eviction fra
`render_svg_map` e `render_lesson_html`, in pratica impossibile: i due
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
ricevono CSS in entrambi i template. `TableBlock`/`EquationBlock` nel
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
levels,none,truncated}` (24 chiavi per lingua; `zero_intervals` è la frase
dei plateau su cui la funzione si annulla, `truncated` quella delle
categorie troncate al tetto di `MAX_NOTABLE_POINTS`). `figure_labels(language)` (`:644-649`) ritorna una
copia con fallback `it` (`de`, `None`, `ja` → it; `en-GB` → en), coerente
con `_labels_for` del PDF (`course_lesson_pdf_service.py:1097-1132`) e
con `fallbackLng: "it"` del frontend. `_interpolate` gestisce `{{n}}` e
`{{ n }}`. Le altre 22 lingue ricevono le etichette in italiano nel PDF
(come «Sintesi» oggi) e nel frontend finché l'amministratore non lancia
l'auto-translate. Niente lookup nel DB delle traduzioni: il seed ha 226
chiavi e nessuna `courses.*` (in produzione sarebbe sempre vuoto). Test di
specchio `test_figure_i18n_mirrors_frontend` (flatten del JSON annidato;
skip esplicito finché `courses.figures` manca da `it.json`, arriva in WP5).

## 7. Normalizzazione degli SVG (Q3)

`svg_normalize.normalize_svg(svg, *, max_bytes) -> NormalizedSvg(svg,
width_px, height_px)`, modulo puro (regex sul solo tag radice + scansione),
per gli SVG di vl-convert, `dot` e matplotlib. **Mermaid non passa da qui.**

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
`expressions[i].label` e `annotations[i].label`. La tupla «structural»
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
render_svg_map(...)` con l'alias del vecchio nome
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
`.figure-label` in grassetto, una regola unica `max-height: 80mm` per
`.figure-svg`/`.mermaid-svg`/`.uploaded-image` (la vecchia `.uploaded-image
{ max-height: 100% }` è rimossa) e regole per `.figure-fallback` e
`.missing-asset` (prima testo nudo). `_VIDEO_OVERRIDE_CSS` non tocca
`.slide-asset`: i frame video ereditano tutto.

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
| 9 | `course_lesson_pdf_service.py:641` | `_prerender_mermaid_for_lesson`, filtro `!= "mermaid"` | sostituito da `render_svg_map` (`_prerender_visual_assets_for_lesson`) |
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
| `figure_render_max_workers` | `FIGURE_RENDER_MAX_WORKERS` | `2` | render CPU-bound concorrenti (worker + anteprime `render-function`); 2 per la VM a 2 core |
| `figure_svg_cache_size` | `FIGURE_SVG_CACHE_SIZE` | `256` | cache LRU in memoria degli SVG (chiave: formato, hash, `THEME_VERSION`, lingua) |
| `figure_svg_max_bytes` | `FIGURE_SVG_MAX_BYTES` | `1_500_000` | oltre, l'SVG prodotto è rifiutato (fallback) |
| `figure_dot_max_chars` | `FIGURE_DOT_MAX_CHARS` | `12_000` | limite del sorgente DOT accettato dal validatore |
| `graphviz_dot_path` | `GRAPHVIZ_DOT_PATH` | `None` | percorso del binario `dot`; `None` = ricerca nel `PATH` |

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
  determinismo v11, L5 `revalidate_mermaid_assets.py` sul DB.
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
- **A22 — `MermaidEditor.TEMPLATES` non riusa `MERMAID_D8_SAMPLES`**: i 7
  template restano (8 tipi senza chiave i18n e 6 id da rinominare non
  valgono il costo).
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
  didattici; il riuso avrebbe costretto 8 chiavi i18n e 6 rinomine.
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
  `sanitizeSvgElement` (`lib/figureFormats.ts`: elementi attivi rimossi,
  `<a>` sostituiti dai figli, `on*` e `href` esterni eliminati), difesa in
  profondità rispetto al gate del PATCH che resta autoritativo.
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

## 13. Rischi residui

- Mermaid resta su CDN a runtime (validatore e pre-render): offline degrada
  come oggi (pass-through nel validatore, fallback nel PDF); i tre formati
  nuovi sono offline e non degradano. Bundle locale di Mermaid: lavoro
  futuro. Il batch Mermaid di `render_svg_map` ha un tetto proprio di
  almeno 60 s (sezione 2.2): con una CDN lentissima l'export attende fino
  a un minuto prima del fallback, e un timeout del batch non entra in
  cache negativa (l'export successivo ritenta).
- Diagrammi v10 già in DB che non parsano in v11, o di tipo escluso,
  finiscono nell'elenco «da correggere» dello script L5; il gate statico
  li blocca solo alla rigenerazione o alla modifica di quel singolo asset,
  mai all'edit del testo (A15).
- Cambiamenti visibili sui contenuti esistenti: card rimossa e «Figura N.»
  (D4), figure orfane rese in coda (A12), caption già prefissate ripulite
  a render, cap di 80 mm anche per le immagini caricate nelle slide (oggi
  tagliate da `overflow: hidden` oltre 80 mm).
- L'endpoint `render-function` e i worker condividono il semaforo a 2 su
  una VM a 2 core: un picco di anteprime rallenta gli export; il rate limit
  di 30/min per IP può essere stretto dietro NAT (regolabile).
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
- `spawn` sotto uvicorn su Linux: prova manuale in Docker documentata qui
  in WP2b (voce 6 del «Delta»).
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
| WP3 | `test_prompt_register.py`, `test_prompt_composition_bugs.py` | misure reali, ordine dei marcatori, `_format_current_lesson_phase3` con `[format]` |

Comandi: `cd backend && DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
python3 -m pytest -q` con Postgres attivo (`docker compose up -d
postgres`); i test che richiedono binari o rete saltano con motivo
esplicito, mai falliscono. Vedi [backend/11 — Tests](../backend/11-tests.md).

### 14.2 Checklist di smoke visuale del frontend

(da completare in WP6 con gli esiti; procedura fissata in WP5)

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
5. esito WP5 (macOS, 7 settembre): eseguito con backend su `:8001`
   (`FRONTEND_ORIGIN=http://localhost:5174`, perché `8000`/`5173` erano
   occupate da un altro progetto) e vite su `:5174` con un `vite.smoke.config.ts`
   temporaneo; seed di `a4u_e2e` (`scratchpad/consegna/smoke_seed.py`: org,
   docente `manager`, corso con una lezione `ready`) e Playwright Python
   (`scratchpad/consegna/smoke_playwright.py`): login dal form; PATCH con
   una spec Vega-Lite senza `clip`/`scale.domain` → 422
   `lesson_content_invalid_visual_asset` con `meta.errors[0].loc =
   ["visual_assets", 1, "content"]`; PATCH con `mermaid`, `vegalite`,
   `dot`, `function` e un secondo `mermaid` non citato → 200; nella vista
   i quattro renderer pronti (`figure--mermaid svg`, `figure--vegalite
   svg`, `figure--dot svg`, `figure--function img`) e didascalie «Figura
   1.» … «Figura 5.» in ordine di citazione (F1 e F3 citate due volte con lo
   stesso numero, la coda calcolata di `function` «Zeri in x = −1, 1. …
   Asintoto obliquo y = x + 2.», l'orfana in coda alla sintesi prima dei
   punti chiave), nessuna card; nel dialog i quattro editor con anteprima
   e badge di formato, e con una spec non parsabile il box controllato
   «Impossibile visualizzare la figura.» al posto dell'anteprima. Immagini:
   `scratchpad/consegna/lesson_content_view.png`,
   `lesson_content_edit.png`, `lesson_content_edit_assets.png`,
   `lesson_content_edit_function.png`, `lesson_content_edit_invalid.png`.
   L'asset `image` non è stato incluso (richiede un file caricato sullo
   storage).

### 14.3 Misure e prove residue

- Dimensione dell'immagine Docker prima/dopo (`docker build -f
  backend/Dockerfile backend` su `main` e su HEAD, `docker image inspect
  --format '{{.Size}}'`); stima a priori: wheel ≈ 57 MB compressi più apt
  `graphviz`. Esito: (da completare in WP6).
- Dimensione del bundle frontend prima (build pulito su HEAD prima di WP5)
  e dopo, con i kB dei chunk `vega`/`vega-lite`/`vega-embed`/`@viz-js/viz`
  (import dinamici). Esito WP5 (`vite build`, kB minificati, gzip fra
  parentesi): totale JS+CSS 6.333 (1.772) → 8.431 (2.548), 61 → 71 chunk;
  `index.js` 3.057 (853) → 3.092 (863); `mermaid.core.js` 678 (167) → 647
  (158); chunk nuovi caricati solo a richiesta: `viz.js` 1.262 (485, il
  WebAssembly di Graphviz è inlinato in base64), `embed.js` 792 (276:
  vega + vega-lite + vega-embed), `step.js` 32, `time.js` 18,
  `figureTheme.js` 4, `VegaLiteDiagram.js`/`MermaidDiagram.js`/
  `DotDiagram.js` ≈ 1-2 ciascuno. Il bundle iniziale cresce di 35 kB
  (editor e cornice, senza librerie di render).
- Esito di `backend/scripts/revalidate_mermaid_assets.py` sul dump del
  docente o run sintetica dichiarata (A24). Esito: (da completare in WP6).
- Prova manuale di `spawn` sotto uvicorn. Esito WP2b (macOS, 7 settembre):
  app FastAPI minima servita da `uvicorn`, handler che chiama
  `asyncio.to_thread(run_isolated, …)` sui bersagli di
  `tests/helpers/slow_target.py` → `echo` ok, risultato da 2 MB ok,
  `sleep_forever` con `timeout=1` → `FigureTimeoutError`; 1,09 s per le tre
  chiamate. Sotto pytest (loop di sessione) i test di
  `test_figure_render_service.py` eseguono gli stessi bersagli. Su Linux
  (Docker) la prova resta da eseguire in WP6.
- `parse_expr` con `global_dict` ristretto su sympy 1.14.0. Esito WP2b
  (7 settembre, prova manuale): registrato in sezione 4.2 — `__import__`
  e `open` → `NameError` (`Function` assente dal `global_dict`), `lambda`
  e accesso ad attributi eseguiti (fermati solo dal passo 1 sull'AST),
  simbolo non dichiarato colto da `free_symbols`, `2**1000000` →
  `ValueError` (4300 cifre). Il test `importorskip("sympy")` è di WP7.
- Metriche dei font di vl-convert nel container. Esito: (da completare in
  WP6).
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
  con skip esplicito, `dot` con `skipif`). Nel container Linux la prova va
  ripetuta per i font (WP6).
- Esiti della revisione avversariale di Fase D (correttezza del dispatch,
  regressione ai cinque livelli di A11, sicurezza di spec e `dot`, i18n,
  tipografia). (da completare in WP6)
- Verifica meccanica di `docs/PROMPTS.md` contro i `_system_prompt(...)`
  reali (`backend/scripts/check_prompts_md.py`). (da completare in WP6)
