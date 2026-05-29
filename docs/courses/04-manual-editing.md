# 04 — Manual editing & AI lesson generation

CRUD manuale di moduli e lezioni sopra l'output della Fase 1, più generazione
AI delle lezioni quando l'utente aggiunge un nuovo modulo.

## Stati ammessi

CRUD manuale è permesso in **tutti gli stati stabili downstream** — non solo a
livello di architettura, ma fino a corso ormai quasi pronto (video / avatar). La
costante `EDITABLE_STATUSES` in `course_architecture_crud.py:56-69` elenca:

| Stato | Note |
|---|---|
| `architecture_ready` / `architecture_approved` | Fase 1 |
| `lessons_structure_ready` / `lessons_structure_approved` | Fase 2 |
| `content_ready` / `content_approved` | Fase 3 |
| `slides_ready` / `slides_approved` | Fase 4 |
| `speech_ready` / `speech_approved` | Fase 5 |
| `video_ready` | Fase 6 |
| `avatar_video_ready` | Fase 6b |

L'architettura e le sub-tab restano quindi editabili anche a corso quasi pronto:
l'utente può **tornare indietro** a correggere un titolo modulo, aggiungere una
lezione o riordinare moduli senza essere bloccato. Lo **stale-detection**
(`frontend/src/lib/staleness.ts`) segnala quando il downstream è da rigenerare —
gli edit settano `architecture_modified_at` sul modulo (`_touch_module`,
`course_architecture_crud.py:76-84`), che il frontend confronta con i timestamp
di generazione delle fasi successive.

Stati **esclusi** esplicitamente:

| Stato | Perché escluso |
|---|---|
| `draft` | Il corso non ha ancora un'architettura. |
| `*_pending` (es. `architecture_pending`, `content_pending`, …) | I worker AI stanno attivamente scrivendo: race condition. |
| `published` / `archived` | Stato terminale, non si tocca. |

`_ensure_editable(course)` (`course_architecture_crud.py:95-101`) solleva
`ConflictError(code='architecture_not_editable')` se lo status non è in
`EDITABLE_STATUSES` (vedi semantica `409` in
[05 — API reference](05-api-reference.md)).

> **Lo status non viene modificato** dagli edit manuali — sono ortogonali al
> ciclo draft → pending → ready → approved.

## Permesso

Tutti gli endpoint CRUD richiedono `course:edit`. La generazione AI delle
lezioni di un modulo richiede `course:generate`.

## Service — `course_architecture_crud.py`

### Modulo

| Funzione | Comportamento |
|---|---|
| `create_module(course, payload)` | Append in coda con position+1, `module_code='M{N}'` |
| `update_module(course, module_id, payload)` | Patch title/description |
| `delete_module(course, module_id)` | Cascade delete lessons, poi `_renumber_modules` |
| `reorder_modules(course, new_order)` | Riassegna position e codici (vedi sotto per il fix anti-collisione) |

### Lezione

| Funzione | Comportamento |
|---|---|
| `create_lesson(course, module_id, payload)` | Append, `lesson_code='M{K}.L{N}'` |
| `update_lesson(course, lesson_id, payload)` | Patch title/summary/is_introductory/recommended_bibliography |
| `delete_lesson(course, lesson_id)` | Delete + `_renumber_lessons` del modulo |
| `reorder_lessons(course, module_id, new_order)` | Riassegna position e codici delle lezioni del modulo |

### Reorder — fix per UNIQUE constraint

L'unique `uq_course_lesson_code` è su `(course_id, lesson_code)` (globale nel corso).
Quando si fa reorder di moduli:

1. Carica tutte le lezioni di tutti i moduli (snapshot in memoria)
2. Bump moduli a temp `_M{n}_tmp`
3. Bump **tutte** le lezioni a temp `_tmp_{counter}` globalmente unico (il counter
   incrementa attraverso tutti i moduli)
4. Assegna codici finali moduli → flush
5. Assegna codici finali lezioni → flush

> Bug storico: bumping per-modulo non è sufficiente. Quando swappi M5↔M6, l'assegnazione
> finale `M5.L1` collide con i codici "vecchi" del modulo non ancora processato. La fix
> separa il bumping in due passi globali (moduli, poi lezioni con counter globale).

### `regenerate_module_lessons`

```python
async def regenerate_module_lessons(db, course, actor_id, module_id) -> Course
```

1. `_ensure_editable(course)` — status in `EDITABLE_STATUSES` (vedi [Stati ammessi](#stati-ammessi))
2. Costruisce user prompt con `_build_module_lessons_user_prompt`:
   - Parametri corso (titolo, obiettivi, argomenti chiave, overview, razionale)
   - **Altri moduli del corso** (codice + titolo + descrizione + outline lezioni)
   - **Modulo target** (codice, titolo, descrizione)
   - Compito: "Genera esattamente N lezioni" (N = `course.lessons_per_module`)
3. Chiama `openai_module_lessons_service.generate_module_lessons`
4. Su `OpenAINotConfiguredError` → `ValidationAppError` (admin error)
5. Su `OpenAIModuleLessonsError` → `ValidationAppError` (`module_lessons_generation_failed`)
6. **Sostituisce** le lezioni esistenti del modulo (delete cascata, ricrea con position 1..N)
7. Audit `course.module.lessons.generated` con metadata (count, tokens, model)

## OpenAI module lessons — `openai_module_lessons_service.py`

- **Modello**: `settings.openai_modules_lessons_model` (default `gpt-5.5`)
- **Response format**: `json_schema` strict per `{ lessons: [{title, summary}] }`
- **System prompt**: focalizzato su un singolo modulo, con regole su progressione,
  coerenza con titolo+descrizione, evitare ridondanza, lingua di output
- **Parametri**: `max_completion_tokens=settings.openai_architecture_max_tokens`
  (riusa lo stesso cap)
- Schema minimale: solo title + summary (no bibliografia, perché in genere il
  modulo aggiunto manualmente non è introduttivo)

## Frontend

### `CourseArchitectureView.tsx`

Vista principale con CRUD inline. Per ogni riga:

- Modulo: `Modulo N` badge + title + ↑↓ + ✏️ + 🗑️
- Lezione: `Lezione N` badge + title + intro badge (se applicabile) + ↑↓ + ✏️ + 🗑️
- Pulsante "Aggiungi lezione" in fondo a ogni modulo
- Pulsante "Aggiungi modulo" in fondo all'elenco

Helper di formatting:

```ts
moduleLabel("M1") → t("courses.architecture.moduleLabel", { n: "1" })  // "Modulo 1"
lessonLabel("M1.L3") → t("courses.architecture.lessonLabel", { n: "3" })  // "Lezione 3"
```

> Backend: `module_code`/`lesson_code` mantengono "M1"/"M1.L1" per uso interno
> (audit, prompt AI, integrazioni future). Solo la presentazione UI usa "Modulo N"/"Lezione N".

#### Optimistic reorder

Le mutation di reorder usano `onMutate` per aggiornare la cache TanStack Query
prima della chiamata HTTP, replicando la rinumerazione del backend. Su errore,
`onError` ripristina lo snapshot precedente.

```ts
const renumberModulesInCache = (current: CourseOut, ids: string[]): CourseOut => {
  // riproduce l'algoritmo del backend localmente, includendo
  // anche il rinumero dei lesson_code interni
};
```

I pulsanti ↑↓ NON sono disabilitati durante `mutation.isPending` — l'utente
può cliccare rapidamente; le richieste vengono serializzate dal DB.

#### Auto-trigger AI dopo create modulo

Dopo `moduleCreateMut.onSuccess`:

```ts
const newModule = fresh.modules.find((m) => m.lessons.length === 0);
if (newModule) {
  moduleGenerateLessonsMut.mutate(newModule.id);
}
```

#### Progress UI per generazione lezioni

Backend è sync (~20-25s). Progress simulato lato client con ease-out su 25s,
cap a 90%:

```ts
const [genProgress, setGenProgress] = useState(0);
useEffect(() => {
  if (!moduleGenerateLessonsMut.isPending) {
    setGenProgress(0);
    return;
  }
  const start = Date.now();
  const id = setInterval(() => {
    const ratio = Math.min(1, (Date.now() - start) / 25_000);
    setGenProgress(Math.round((1 - (1 - ratio) ** 2) * 90));
  }, 400);
  return () => clearInterval(id);
}, [moduleGenerateLessonsMut.isPending]);
```

UI: `<Loader2/> + label + {progress}%` + `<Progress value={genProgress}/>`. La pill
si smonta quando `module.lessons.length > 0` (server ha risposto).

### `ModuleEditDialog.tsx`

Dialog `max-w-2xl` con:

- Header: badge mono `Modulo N` + title
- Title field con counter live `current/max` + hint
- Description textarea (rows=7, resize-y)
- Edit-mode info pill: "Questo modulo contiene N lezioni" (con pluralizzazione i18n)
- Auto-focus titolo all'apertura
- Submit con `⌘+↵` / `Ctrl+↵` (handler `onKeyDown` su DialogContent)

### `LessonEditDialog.tsx`

Dialog `max-w-3xl` con `ScrollArea` interno. Header: badge `Lezione N` +
moduleLabel padre.

Campi:
- Title (counter, hint, autofocus)
- Summary textarea
- Toggle "Lezione introduttiva" come riquadro evidenziato (icona, hint, bordo brand quando attivo)
- **Editor bibliografia** (visibile solo se `is_introductory=true`):
  - Pulsante "Aggiungi libro" (max 20)
  - Per ogni voce: authors, title, publisher, year, note (textarea), source (select), confidence (select)
  - Numero progressivo `#N` + pulsante elimina
  - Regola §4.4 enforced: `source='general_knowledge_suggestion'` ⇒ `confidence` auto-imposta a `to_verify` e diventa disabled

> Sul submit, se `is_introductory=false`, la bibliografia viene azzerata (clean state).

### Timeout API

`coursesApi.modules.generateLessons` usa `timeout: 300_000` (5 min) per
override del default 20s di axios — la chiamata sync attende OpenAI
~20-30s.

## API endpoint

Modulo:

| Metodo | Path | Permission |
|---|---|---|
| `POST` | `/modules` | `course:edit` |
| `PATCH` | `/modules/{id}` | `course:edit` |
| `DELETE` | `/modules/{id}` | `course:edit` |
| `POST` | `/modules/reorder` | `course:edit` |
| `POST` | `/modules/{id}/generate-lessons` | `course:generate` |

Lezione:

| Metodo | Path | Permission |
|---|---|---|
| `POST` | `/modules/{id}/lessons` | `course:edit` |
| `PATCH` | `/lessons/{id}` | `course:edit` |
| `DELETE` | `/lessons/{id}` | `course:edit` |
| `POST` | `/modules/{id}/lessons/reorder` | `course:edit` |

Tutti restituiscono `CourseOut` aggiornato (eager-loaded via `_refresh_full`).

## Audit

- `course.module.{created|updated|deleted}`
- `course.modules.reordered`
- `course.module.lessons.generated`
- `course.lesson.{created|updated|deleted}`
- `course.lessons.reordered`
