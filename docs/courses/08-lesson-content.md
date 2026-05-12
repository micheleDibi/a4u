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
- **Asset visivi**: Mermaid (diagram/schema/chart), formule LaTeX,
  tabelle markdown, image_prompt/image_search_query (placeholder).
- **Esempi**, **references**, **coverage_check**.

> **Nota**: il campo `exercises_for_self_study` previsto nella spec
> originale è stato rimosso (vedi prompt §6 — "NON GENERARE ESERCIZI").
> Lo schema `content_raw` espone `examples` ma non `exercises`.

L'output è validato (10 validazioni di §6.4) e materializzato come
JSONB `content_raw` su `course_lesson`. La UI rende live Mermaid +
KaTeX + tabelle.

## Glossario corso (§10.1)

Single-shot, riusato in tutte le fasi successive (`{{glossario}}` nel
user prompt di Fase 3, e in futuro Fasi 5 e 6). Generato **automaticamente
dal worker della Fase 3** al primo passaggio se `glossary_status='empty'`,
oppure manualmente via `POST /glossary/regenerate`.

State machine: `empty → processing → ready (+failed)`.

## Schema dati (migration 0015)

### `course_lesson` (10 colonne nuove)

| Colonna | Tipo | Note |
|---|---|---|
| `content_status` | VARCHAR(40) | CHECK ∈ (empty, pending, processing, ready, approved, failed) |
| `content_raw` | JSONB | output AI completo (verbatim §6.3) |
| `content_tokens` | JSONB | `{prompt, completion, total, model}` |
| `content_attempts` | SMALLINT | counter retry |
| `content_error` | TEXT | messaggio errore |
| `content_generated_at` | TIMESTAMPTZ | |
| `content_approved_at` | TIMESTAMPTZ | |
| `content_regeneration_hint` | TEXT | hint utente §9.3 |
| `content_progress` | SMALLINT | 0..100 (CHECK) |
| `content_progress_phase` | VARCHAR(50) | preparing_prompt, calling_openai, materializing |

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

## Architettura backend

### Servizi OpenAI

- `openai_glossary_service.py` — wrapper § 10.1 con system prompt
  minimal (10-30 termini), JSON schema strict, gestione errori
  `OpenAIGlossaryError` + diagnostica empty-content per gpt-5.5
  reasoning tokens.
- `openai_lesson_content_service.py` — wrapper §6 con system prompt
  verbatim, addendum §9.3 per rigenerazione, JSON schema completo
  §6.4, `OpenAILessonContentError`. Timeout 600s (lezione completa
  60-120s di reasoning).

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
  - `request_lesson_generation` / `request_all_lessons_generation`
  - `materialize_lesson_content` — applica le **10 validazioni §6.4**:
    1. `lesson_id` ↔ `lesson_code` match
    2. `section_id` univoci
    3. `asset_id` univoci per tipo (visual_assets, tables, equations, examples)
    4. Cross-field: ogni `objectives_addressed` esiste in Fase 2
    5. Cross-field: ogni `topics_addressed` esiste nei `mandatory_topics`
    6. Coverage completa: unione su sections copre TUTTI obiettivi/topic
    7. `coverage_check.objectives_covered` coerente con sections
    8. `coverage_check.topics_covered` coerente con sections
    9. Asset orfani (referenziati ma non definiti) → warning soft
    10. Asset non referenziati nel testo → warning soft
  - `approve_lesson_content` / `approve_all_lessons_content`
  - `_recompute_course_content_status`
- `course_lesson_content_crud.py` — edit manuale di `content_raw`
  (richiede status `ready`/`approved`). Validazioni allentate (solo
  unicità ID, no coverage hard).

### Worker parallelo

`course_lesson_content_worker.py` — speculare al worker Fase 2 ma
scoped a livello LEZIONE:
- `_inflight: set[UUID]` su `lesson_id` (claim atomico in `_tick`,
  vedi [02 — Architecture](../02-architecture.md#pattern-batch-parallelo-lesson_structure-lesson_content-lesson_pdf))
- `_semaphore = asyncio.Semaphore(course_lesson_content_max_concurrency)`
  (default `3`, output 5x più grande di Fase 2)
- Polling: `course_lesson_content_poll_interval_seconds` (default `4`)
- Glossary auto-trigger: al primo task del corso, se
  `glossary_status not in ('ready','approved')`, chiama sync
  `course_glossary_service.ensure_glossary_ready` (~10-20s).
- Ticker progress: ease-out 15→85% in ~90s (lezione più lunga di
  Fase 2 → ticker più lento).
- **Auto-retry trasparente** — `_apply_failure(lesson, *,
  recoverable, auto_retry_max)` è invocato in tutti i 4 percorsi di
  errore (glossary_gate, openai_call, materialize). Se `recoverable`
  e `content_attempts < course_lesson_content_auto_retry_max` (default
  5), riporta `content_status='pending'` (il prossimo tick ritenta) e
  azzera `content_error` — l'utente non vede mai il messaggio. Solo
  dopo `auto_retry_max` esauriti `→ failed`. Errori non recuperabili
  (`OpenAINotConfiguredError` — config issue, non si risolverà
  ritentando) vanno a `failed` subito.

### API endpoints (6 nuovi)

| Metodo | Path | Permesso | Effetto |
|---|---|---|---|
| `POST` | `/lessons/{lid}/content/generate` | `course:generate` | Set lezione `pending`. 202. |
| `POST` | `/lessons-content/generate-all` | `course:generate` | Set tutte le lezioni `pending`. 202. |
| `POST` | `/lessons/{lid}/content/approve` | `course:generate` | Approve lezione singola (richiede `ready`). |
| `POST` | `/lessons-content/approve-all` | `course:generate` | Approve batch (richiede tutte `ready`). |
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
  `index.css`, niente `@tailwindcss/typography`).
- `MermaidDiagram.tsx` — lazy-load di `mermaid` (dynamic import) +
  init + render SVG. **Pre-validazione con `mermaid.parse(code,
  { suppressErrors: true })` PRIMA del render**: se la sintassi è
  invalida, mostra una error UI controllata (icona ⚠ ambra +
  collapsible "Mostra dettagli" col messaggio di parse). Senza la
  pre-validazione, `mermaid.render()` su syntax invalida inietta nel
  DOM una grossa SVG bomb-icon che rompe il layout della pagina.
  Strip programmaticamente l'attributo `max-width` inline dell'SVG
  generato (mermaid lo emette di default a ~150px) e applica
  `[&_svg]:!w-full [&_svg]:!max-w-none` Tailwind per fillare il
  container.

### Componenti shared (editing) — editor user-friendly

L'edit manuale del contenuto **non espone più la sintassi grezza**.
Quattro editor specializzati nascondono markdown, mermaid e LaTeX:

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
  con debounce 500ms. Dropdown **template** (`flowchart`, `sequence`,
  `state`, `er`, `mindmap`, `class`, `gantt`) sostituisce il
  contenuto con uno scheletro funzionante.

I dati salvati restano **markdown / LaTeX / mermaid stringhe** —
backend, schema e renderer di vista invariati.

### Vista principale

`CourseLessonContentView.tsx` (Tab 6 dell'editor):
- Header con aggregate progress (0..100%) + pulsanti Generate/Approve all
- **ETA + tempo medio per lezione** durante un batch attivo: `useBatchEta`
  (vedi [Frontend 08 — Hooks](../frontend/08-hooks.md)) deriva la velocità
  dai timestamp `content_generated_at` delle lezioni completate nella
  recent window (90 min) e stima il rimanente come `avgPerLesson × remaining`
- **Sub-pannello Glossario** (collapsible): chip dei termini con
  tooltip su `usage_note` + pulsante Rigenera
- Lista per modulo con sub-card per lezione (status badge + Progress
  live + bottoni contestuali Generate/Regenerate/Retry/Approve/Edit)
- Quando lezione è `ready`/`approved` ed espansa: render completo via
  `LessonContentView.tsx` (Mermaid live, KaTeX, tabelle, esempi card)

### Dialogs

- `LessonContentGenerateDialog.tsx` — 4 modi: generate/regenerate per
  singola lezione o batch corso. Textarea hint per regenerate.
- `LessonContentEditDialog.tsx` — `max-w-6xl` con pannello unico
  scrollabile organizzato in `SectionGroup` collassabili:
  - Testo della lezione (intro / sections / summary) → `RichTextEditor`
  - Asset visivi → `MermaidEditor` quando `format='mermaid'`,
    `<Textarea>` semplice per gli altri formati
  - Tabelle → `TableEditor`
  - Formule → `LatexEditor` (latex) + `RichTextEditor` (explanation)
  - Esempi → `RichTextEditor`
  - Key takeaways / References → `<Input>`
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
    e applica un flash visivo `ring-2 ring-amber-400` per ~2.2s. Per
    i `references[]` (citazioni senza ID-token) lo stesso pulsante fa
    un substring match case-insensitive della `citation` — best-effort.
    Se nessuna occorrenza viene trovata → toast informativo.

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
  "mermaid": "^11",
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

(KaTeX era già installato per il summary documenti.)

### i18n

Locali aggiornati: solo IT/EN canonici (le altre 22 lingue saranno
completate via "Completa con AI" in app). Namespace
`courses.lessonsContent.*` e `courses.glossary.*`.

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
COURSE_LESSON_CONTENT_DOCUMENTS_CONTEXT_MAX_CHARS=20000
# Numero massimo di retry automatici prima di transitare a `failed`.
# La UI vede la lezione come "in elaborazione" durante i retry.
COURSE_LESSON_CONTENT_AUTO_RETRY_MAX=5
COURSE_LESSON_STRUCTURE_AUTO_RETRY_MAX=5
```

`OPENAI_LESSON_CONTENT_MAX_TOKENS=32000` è calibrato per output
8-15k token + reasoning gpt-5.5. Aumentare se viene osservato
`finish_reason="length"` con `reasoning_tokens` alti.

`OPENAI_LESSON_CONTENT_REASONING_EFFORT=high` di default — è il task più
complesso del pipeline (markdown lungo + asset + bibliografia + JSON
schema strict). Per accelerare drasticamente un corso grande, abbassare a
`medium` taglia ~40% dei tempi con qualità leggermente inferiore.

## Cosa NON fa questa iterazione (out of scope)

1. **Generazione effettiva delle immagini** (DALL-E / Stable
   Diffusion): mostriamo solo il `image_prompt` come placeholder.
2. **Cascade invalidation di Fase 4-5** su edit di Fase 3.
3. **Versioning storico** delle rigenerazioni (`content_raw` snapshotta
   solo l'ultima versione).
4. **Auto-trigger di Fase 3 su approve di Fase 2** (l'utente preferisce
   trigger manuale).
5. **Multi-lingua del glossario** (generato 1 volta nella
   `course.language_code`).
6. **Image search effettiva** (Bing/Pexels) — `image_search_query` è un
   placeholder testuale.
7. **Streaming** dell'output AI (json_schema strict richiede non-streaming).
8. **Esercizi auto-studio**: l'iterazione precedente includeva
   `exercises_for_self_study`; ora rimosso dal prompt e dallo schema.

> **Già fatto** (era out-of-scope nelle versioni precedenti del
> documento): editor WYSIWYG del markdown — vedi sezione
> "Componenti shared (editing) — editor user-friendly" sopra.
