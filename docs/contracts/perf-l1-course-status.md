# Contratto — Stato leggero del corso (Prestazioni, Livello 1)

Proprietario: lead (team-lead). Modifiche solo via richiesta `CONTRATTO:`.
Consumatori: `backend` (implementa), `frontend` (consuma), `reviewer` (verifica).

## Perché
L'editor del corso oggi ripolla ogni 4-5 s `GET /orgs/{org}/courses/{id}`, che restituisce
`CourseOut` con `content_raw`/`slides_raw`/`speech_raw` di ogni lezione: 1,5 MB compressi per poll
su un corso da 72 lezioni (misura di produzione, 03/10/2026). Questo endpoint espone **solo** stati,
avanzamento e timestamp, così il polling costa pochi KB e il dettaglio si ricarica solo quando
cambia qualcosa di sostanziale.

## Endpoint

`GET /api/v1/orgs/{org_id}/courses/{course_id}/status`

- **Autorizzazione:** identica a `GET /orgs/{org_id}/courses/{course_id}`
  (`api/v1/courses.py:246-263`): `require(P.COURSE_VIEW)`, `_ensure_org`, poi le stesse regole di
  visibilità di `course_service.get_course` (permessi risolti con `resolve_permissions`, una sola
  volta). Stessi errori del dettaglio: **403** `not_a_member` per chi non è membro dell'org,
  **403** `permission_denied` se manca il permesso, **404** `organization_not_found` per un'org
  inesistente (visibile solo al platform admin), **404** `course_not_found` se il corso non
  esiste o non è visibile. Formato errore: quello standard di `core/errors.py`.
- **Accesso ai dati:** solo colonne scalari elencate sotto (`load_only` / select di colonne).
  Vietato caricare JSONB grandi (`*_raw`, `*_tokens`, `architecture_raw`, `glossary_raw`,
  `lessons_structure_raw`, `summary`, `bibliography`, `figure_*` di lezione) e vietati i
  `selectinload` di `_eager_options`. Nessun accesso lazy in async.
- **Ordinamento:** moduli per `position`, lezioni per `position`, documenti per `created_at, id`
  (il dettaglio non ha un ordine garantito: il client abbina sempre per `id`, mai per posizione).
- **Caching HTTP:** header `ETag` forte = `"` + sha256 esadecimale del corpo JSON serializzato +
  `"`; se la request porta `If-None-Match` che combacia → **304** senza corpo, con l'`ETag`.
  Il confronto è debole (accetta anche `W/"…"`, perché nginx con gzip rende deboli gli ETag),
  su una lista separata da virgole, e accetta `*`. Sempre
  `Cache-Control: no-cache`. (Il browser rivalida da solo: il client non deve gestire il 304.)
- **Rate limit:** nessuno.
- **Dimensione attesa:** corso da 96 lezioni ≤ 200 KB non compressi; tempo server p95 ≤ 50 ms
  in locale.

## Risposta `CourseStatusOut` (200)

Tutti i campi sono sempre presenti (nessun `exclude_none`): i valori assenti sono `null`.
Tipi come in `CourseOut` / `CourseModuleOut` / `CourseLessonOut` / `CourseDocumentOut`
(`schemas/course.py`, `schemas/course_architecture.py`); datetime in ISO 8601 con fuso.

```jsonc
{
  "course_id": "uuid",
  "status": "CourseStatus",
  "updated_at": "datetime",
  "architecture_progress": 0,
  "architecture_progress_phase": "str|null",
  "architecture_error": "str|null",
  "architecture_attempts": 0,
  "architecture_generated_at": "datetime|null",
  "glossary_status": "str",
  "glossary_generated_at": "datetime|null",
  "glossary_error": "str|null",
  "documents": [
    {
      "id": "uuid",
      "summary_status": "DocumentSummaryStatus",
      "summary_generated_at": "datetime|null",
      "summary_error": "str|null",
      "summary_attempts": 0,
      "summary_coverage": "str|null",
      "summary_chunks_total": "int|null",
      "summary_chunks_done": "int|null",
      "figures_status": "str|null",
      "figures_error_code": "str|null",
      "figures_count": "int|null",
      "figures_coverage": "str|null",
      "figures_pages_total": "int|null",
      "figures_pages_done": "int|null",
      "figures_progress": "object|null",      // dict piccolo, come in CourseDocumentOut
      "figures_requested_at": "datetime|null"
    }
  ],
  "modules": [
    {
      "id": "uuid",
      "lessons_structure_status": "str",
      "lessons_structure_progress": 0,
      "lessons_structure_progress_phase": "str|null",
      "lessons_structure_error": "str|null",
      "lessons_structure_attempts": 0,
      "lessons_structure_generated_at": "datetime|null",
      "lessons_structure_approved_at": "datetime|null",
      "architecture_modified_at": "datetime|null",
      "lessons": [
        {
          "id": "uuid",
          "lesson_structure_modified_at": "datetime|null",
          // Per ciascun prefisso P in: content, slides, speech
          //   P_status, P_progress, P_progress_phase, P_error, P_attempts,
          //   P_generated_at, P_approved_at, P_modified_at
          // Per ciascun prefisso Q in: pdf, slides_pdf, speech_pdf
          //   Q_status, Q_progress, Q_progress_phase, Q_error, Q_attempts,
          //   Q_generated_at
          "video_status": "str",
          "avatar_video_status": "str"
        }
      ]
    }
  ]
}
```

Nomi, tipi e default di ogni campo `P_*`/`Q_*` sono **identici** a quelli di `CourseLessonOut`
(es. `content_status: str = "empty"`, `pdf_progress: int = 0`). `video_status` e
`avatar_video_status` sono le colonne omonime di `CourseLesson`.

## Regole del client (frontend)

1. **Chi polla.** Solo `/status`, con query key `["courses", "status", orgId, courseId]`. La query
   del dettaglio (`["courses", "detail", orgId, courseId]`) **non** ha più `refetchInterval`.
2. **"Attivo".** Il corso è attivo se vale almeno una di: `status === "architecture_pending"`;
   `glossary_status` ∈ {pending, processing}; un documento con `summary_status` o `figures_status`
   ∈ {pending, processing}; un modulo con `lessons_structure_status` ∈ {pending, processing}; una
   lezione con uno qualsiasi tra `content/slides/speech/pdf/slides_pdf/speech_pdf/video/avatar_video`
   `_status` ∈ {pending, processing}.
3. **Intervalli.** Attivo: 2 s; se il corpo non cambia (stesso contenuto) per poll consecutivi,
   raddoppio fino a 10 s (2 → 4 → 8 → 10); torna a 2 s al primo cambiamento. Non attivo: 30 s.
   Con la scheda nascosta il poll si ferma (default TanStack, non forzare il contrario).
4. **Cambi sostanziali → ricarica del dettaglio** (una sola `invalidateQueries` della detail key):
   - insieme degli id di documenti, moduli o lezioni diverso;
   - cambio di un qualsiasi `*_status`, `*_generated_at`, `*_approved_at`, `*_modified_at`,
     `*_error`, `figures_error_code`, `figures_count`, `figures_coverage`;
   - cambio di `status` del corso.
   **Eccezione 2:** un `*_status` che passa fra due stati attivi (`pending` ↔ `processing`) non
   cambia i dati del dettaglio: si tratta come avanzamento (regola 5, patch locale). Si ricarica
   quando uno stato entra o esce dall'insieme attivo (es. `processing` → `ready`/`failed`).
   **Confronto con la cache:** il cambio sostanziale si valuta fra la risposta `/status` e il
   dettaglio **in cache** (non solo fra due poll consecutivi): a ogni poll, se il dettaglio in
   cache non riflette uno stato sostanziale del `/status`, si ricarica. Così un reload fallito o
   una risposta di mutazione arrivata in ritardo non lasciano il dettaglio indietro.
   **Eccezione:** `video_status` e `avatar_video_status` contano per la regola 2 ("attivo") ma un
   loro cambio **non** ricarica il dettaglio, che non contiene questi campi (le viste video hanno
   i loro endpoint batch).
   Prima di ricaricare, il client può verificare che il cambio sostanziale non sia già presente
   nella cache del dettaglio (es. scritto da una mutazione): in quel caso non ricarica.
   **Non** usare `updated_at` come segnale (cambia a ogni tick di avanzamento dell'architettura).
5. **Solo avanzamento → patch locale** (`setQueryData` sulla detail key, nessuna request):
   `*_progress`, `*_progress_phase`, `*_attempts`, `architecture_progress*`, `summary_chunks_*`,
   `figures_pages_*`, `figures_progress`. Si creano oggetti nuovi solo per le entità cambiate
   (il resto mantiene l'identità, per non far ri-renderizzare righe ferme).
6. **Mutazioni.** Dopo una mutazione che avvia un job (generate, export, regenerate…) il client
   invalida anche la status key, così il poll riparte subito a 2 s.

## Fuori contratto
Video e avatar-video mantengono i loro endpoint batch (`/lessons-video/status`,
`/lessons-avatar-video/status`), resi leggeri dal task B3 senza cambiare la forma della risposta.
