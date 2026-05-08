# 03 — Architecture generation (Fase 1)

Generazione AI dell'**architettura del corso**: moduli + lezioni con titoli, sintesi
e bibliografia consigliata. Implementa §4 di `prompt_generazione_corsi.md`.

## Flusso

```
draft / architecture_ready / architecture_approved
   │
   │  POST /architecture/generate { regeneration_hint? }
   ▼
architecture_pending  + architecture_progress=0
   │
   │  worker tick — apre sessione DB autonoma
   ▼
status=architecture_pending, progress=5%, phase=preparing_prompt
   │
   │  build_user_prompt (§4.2)
   ▼
progress=15%, phase=calling_openai
   │
   │  ┌──────────────────────────────────────────────┐
   │  │ ticker background: ease-out 15→85% in 75s    │
   │  │ con sessione DB indipendente                 │
   │  └──────────────────────────────────────────────┘
   │  generate_architecture (§4.1 system + §4.3 schema)
   ▼
progress=90%, phase=materializing
   │
   │  drop modules + lessons esistenti, ricrea da AI output
   ▼
status=architecture_ready, progress=100%
   │
   │  POST /architecture/approve
   ▼
status=architecture_approved
```

Su qualsiasi errore: `status='draft'`, `architecture_error` popolato,
`architecture_progress=0`.

## Worker — `course_architecture_worker.py`

Pattern: gemello di `course_document_worker`. Lifespan-managed.

Polling: `SELECT WHERE status = 'architecture_pending'`. Per ogni corso:
1. `_set_progress(course, pct=5, phase='preparing_prompt')` con commit immediato
2. Build user prompt da `course_architecture_service.build_user_prompt`
3. `_set_progress(pct=15, phase='calling_openai')`
4. **Ticker background**: `_progress_ticker(course_id, 15→85, 75s)` —
   `asyncio.create_task` con sessione DB autonoma per non bloccare la
   transazione del worker mentre attende OpenAI
5. Chiamata `openai_architecture_service.generate_architecture`
6. Cancellazione ticker (`task.cancel()` + await soppresso)
7. `_set_progress(pct=90, phase='materializing')`
8. `materialize_architecture` (drop + recreate)
9. `progress=100`, `phase=NULL`, audit `course.architecture.generated`, commit

Curva ease-out del ticker: `eased = 1 - (1 - ratio)²`. Avanza veloce
all'inizio, rallenta verso il limite. Aggiornamento ogni 2s. Si ferma se
trova lo status diverso da `architecture_pending` o se viene cancellato.

## Service di orchestrazione — `course_architecture_service.py`

### `request_generation`

```python
def request_generation(db, course, actor_id, regeneration_hint) -> Course
```

- Verifica status ∈ {draft, architecture_pending, architecture_ready,
  architecture_approved}.
- **I documenti sono opzionali**: se nessun documento è `ready`, il prompt
  riceve "(Nessun documento di riferimento elaborato.)" come placeholder.
- Imposta `status='architecture_pending'`, azzera `architecture_error`,
  salva `architecture_regeneration_hint`.
- Audit `course.architecture.generate.requested`.

### `build_user_prompt`

Costruisce il prompt §4.2 con:

1. **Parametri pedagogici** (titolo, obiettivi, lingua, argomenti chiave)
2. **Tassonomie** (8 valori formattati come "key: label")
3. **Numeri** (CFU, modules_count, lessons_per_module, lesson_duration_minutes)
4. **Documenti di riferimento**: `_build_documents_context` concatena i
   `summary` JSONB dei doc `ready` con budget totale di
   `settings.course_architecture_documents_context_max_chars` (default 60000)
5. **Architettura precedente** (solo per rigenerazione): outline dei moduli
   esistenti, codici, descrizioni, lezioni
6. **Hint utente** (se presente, come §9.2)

### `materialize_architecture`

```python
async def materialize_architecture(db, course, architecture, raw, usage)
```

- Validazioni §4.4:
  - `len(modules) == numero_moduli`
  - per ogni modulo: `len(lessons) == numero_lezioni_per_modulo`
  - M1.L1 ha `is_introductory=True` e `recommended_bibliography` non vuota
  - tutte le altre lezioni hanno `is_introductory=False` e bibliografia vuota
  - `source='general_knowledge_suggestion'` ⇒ `confidence='to_verify'`
- Cancella tutti i moduli + lezioni esistenti del corso (cascade).
- Crea nuove righe con position 1-based, `module_code='M{N}'`,
  `lesson_code='M{K}.L{N}'`.
- Aggiorna `course.course_overview`, `pedagogical_rationale`,
  `architecture_raw`, `architecture_tokens`, `architecture_generated_at`,
  `status='architecture_ready'`.

### `approve_architecture`

```python
async def approve_architecture(db, course, actor_id) -> Course
```

- Verifica status = `architecture_ready`.
- Sets `status='architecture_approved'`. Audit.

## OpenAI architecture — `openai_architecture_service.py`

- **Modello**: `settings.openai_modules_lessons_model` (default `gpt-5.5`)
- **Response format**: `json_schema` strict (`ARCHITECTURE_JSON_SCHEMA`)
- **System prompt**: copia letterale §4.1 + `REGENERATION_SUFFIX` §9 se è
  rigenerazione (struttura del prompt augmentato con istruzioni sul mantenere
  coerenza vs hint utente)
- **Parametri**: NO `temperature` (gpt-5.x supporta solo default 1.0),
  `max_completion_tokens=settings.openai_architecture_max_tokens` (default 8000)

> **Nota tecnica**: la famiglia gpt-5.x richiede `max_completion_tokens` (non
> `max_tokens`) e non supporta `temperature` custom. Il documento spec
> indicava temp=0.4 ma è stato rimosso per compatibilità.

### JSON Schema (estratto)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["course_overview", "pedagogical_rationale", "modules"],
  "properties": {
    "course_overview": { "type": "string" },
    "pedagogical_rationale": { "type": "string" },
    "modules": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["module_id", "title", "description", "lessons"],
        "properties": {
          "module_id": { "type": "string" },
          "title": { "type": "string" },
          "description": { "type": "string" },
          "lessons": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["lesson_id", "title", "summary", "is_introductory", "recommended_bibliography"],
              ...
            }
          }
        }
      }
    }
  }
}
```

## Frontend

### `GenerateArchitectureDialog.tsx`

Dialog di conferma con `<Textarea>` opzionale per il `regeneration_hint`
(max 2000 caratteri). Title/description differenti per `isRegeneration`.

### Polling

`CourseEditorPage.useQuery.refetchInterval`: 5000ms quando
`status === 'architecture_pending'`. Ferma quando lo status cambia.

### Progress UI

In `ArchitectureSection`, durante `architecture_pending`:

```tsx
<Loader2 spinning />
{phase ? t(`courses.architecture.phases.${phase}`) : ...}
{progress}%
<Progress value={progress} />
{archEta.etaMs && <span>{t("courses.architecture.eta", { time: formatDuration(archEta.etaMs) })}</span>}
{archEta.elapsedMs && <span>{t("courses.architecture.elapsed", { time: formatDuration(archEta.elapsedMs) })}</span>}
```

Le chiavi i18n delle fasi: `preparing_prompt`, `calling_openai`,
`materializing`. `defaultValue` fallback al messaggio generico
`pendingMessage` per fasi non riconosciute.

**ETA + tempo trascorso**: la sezione usa `useTaskEta(\`arch:${course.id}\`,
isPending, archPct)` che persiste lo `started_at` in `sessionStorage`
(sopravvive a refresh / navigation), calcola elapsed = `now - started`,
ed estrapola ETA come `elapsed × (100 - progress) / progress` quando
`progress ≥ 5%`. Sotto soglia mostra solo "trascorso". Vedi
[Frontend 08 — Hooks](../frontend/08-hooks.md#usetasketa-taskkey-isactive-progress--srchooksusetasketa).

### `CourseArchitectureView.tsx`

Vista read-only/edit della struttura generata. Vedi
[04 — Manual editing](04-manual-editing.md) per i dettagli sulle
operazioni CRUD sopra l'output AI.

## Configurazione

| Var | Default | Descrizione |
|---|---|---|
| `openai_modules_lessons_model` | `gpt-5.5` | Modello per Fase 1 + lezioni single-modulo |
| `openai_architecture_max_tokens` | `8000` | Cap su completion per architettura (alza a 16000 se vedi `finish_reason=length`) |
| `openai_architecture_reasoning_effort` | `medium` | `[minimal, low, medium, high]` per gpt-5.x/o1/o3/o4; ignorato su modelli classici |
| `course_architecture_poll_interval_seconds` | `4` | Worker tick |
| `course_architecture_documents_context_max_chars` | `60000` | Budget context documenti nel prompt |

## API endpoint

| Metodo | Path | Permission | Descrizione |
|---|---|---|---|
| `POST` | `/architecture/generate` | `course:generate` | Body `{regeneration_hint?: string}`. 202 → status pending |
| `POST` | `/architecture/approve` | `course:generate` | `architecture_ready` → `architecture_approved` |

Lettura: `GET /courses/{id}` ritorna `CourseOut` con campo `modules: CourseModuleOut[]`
e tutti i metadati `architecture_*` (incluso `progress`).

## Audit

- `course.architecture.generate.requested`
- `course.architecture.generated`
- `course.architecture.generation.failed` (con `phase`: openai_call | materialize)
- `course.architecture.approved`
