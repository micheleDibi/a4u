# Backend 10 — Alembic (`alembic/`)

Migrazioni database. Ogni cambio schema deve passare da Alembic; nessun
DDL applicato fuori dalle migrazioni.

---

## `alembic.ini`

Config Alembic. Punti chiave:

- `script_location = alembic`.
- `prepend_sys_path = .` per consentire `from app...` in `env.py`.
- `sqlalchemy.url =` lasciato vuoto: `env.py` lo imposta dinamicamente
  da `Settings`.
- `file_template`: prefisso temporale + slug.
- Logging stdlib per Alembic, sqlalchemy, root.

---

## `alembic/env.py`

**Scopo**: bootstrap async per le migrazioni. Funziona sia in modalità
offline sia online.

Flusso:

1. Inietta `DATABASE_URL` da `Settings` in `config.set_main_option`.
2. Carica logging da file `alembic.ini` (se presente).
3. `from app.models import *`: registra tutti i modelli su `Base.metadata`.
4. `target_metadata = Base.metadata`.
5. **Modalità offline** (`run_migrations_offline`):
   - `context.configure(url, target_metadata, literal_binds=True,
     dialect_opts={"paramstyle": "named"}, compare_type=True)`.
   - `context.run_migrations()` in transazione.
6. **Modalità online** (`run_migrations_online`):
   - `async_engine_from_config(prefix="sqlalchemy.")`.
   - `connection.run_sync(do_run_migrations)`.
   - `do_run_migrations`: configura `context` e applica tutte le revisioni
     pendenti.
7. `if context.is_offline_mode(): run_offline; else: asyncio.run(run_online)`.

`compare_type=True`/`compare_server_default=True` arrichiscono l'autogenerate.

---

## `alembic/script.py.mako`

Template per le nuove revisioni generate da `alembic revision`.
Genera il file con import standard, `revision`, `down_revision`,
`branch_labels`, `depends_on`, e funzioni `upgrade()`/`downgrade()` vuote.

---

## `alembic/versions/0001_initial.py`

**Migrazione iniziale**: crea l'intero schema.

### Sequenza `upgrade()`

1. **Estensioni**: `CREATE EXTENSION IF NOT EXISTS citext` (per
   `User.email`), `CREATE EXTENSION IF NOT EXISTS "uuid-ossp"`.
2. **`users`**: PK UUID, email CITEXT UNIQUE, password_hash, full_name,
   `is_platform_admin`, `is_active`, `last_login_at`, `failed_login_count`,
   `locked_until`, timestamps.
3. **`organizations`**: PK UUID, anagrafica completa, `logo_path`,
   `created_by_user_id` (FK SET NULL), `deleted_at` con indice,
   timestamps.
4. **`organization_roles`**: PK UUID, `code` UNIQUE, `name_it`,
   `description`, `rank`.
5. **`permissions`**: PK UUID, `code` UNIQUE, `description`, `scope`.
6. **`role_permissions`**: PK composta `(role_id, permission_id)`,
   FK CASCADE.
7. **`organization_role_permissions`**: PK composta `(organization_id,
   role_id, permission_id)`, `granted` BOOL default true, FK CASCADE.
8. **`memberships`**: PK UUID, `(user_id, organization_id)` UNIQUE,
   FK su `users` CASCADE / `organizations` CASCADE / `organization_roles`
   RESTRICT, `joined_by_user_id` SET NULL, `joined_at` server_default
   now(). Indici su `user_id` e `organization_id`.
9. **`membership_permission_overrides`**: PK composta `(membership_id,
   permission_id)`, FK CASCADE.
10. **`invitations`**: PK UUID, FK org CASCADE / role RESTRICT / created_by
    SET NULL, `token_hash` UNIQUE, `expires_at`, `accepted_at`,
    `revoked_at`, timestamps. Indici su `token_hash` e
    `(organization_id, email)`.
11. **`refresh_tokens`**: PK UUID, FK user CASCADE, `token_hash` UNIQUE,
    `expires_at`, `revoked_at`, `replaced_by_id` self-FK SET NULL,
    `user_agent`, `ip`, timestamps. Indici su `user_id` e `token_hash`.
12. **`login_attempts`**: PK UUID, `email`, `ip`, `success`, `created_at`
    server_default. Indici `(email, created_at)` e `(ip, created_at)`.
13. **`slide_templates`**: tutti i campi del modello con i default
    server-side.
14. **`pdf_templates`**: idem, con campi specifici PDF.
15. **`avatars`**: PK UUID, FK org CASCADE, `name`, `image_path`,
    `description`, `metadata` JSONB default `{}`, timestamps.
16. **`audit_logs`**: PK UUID, `created_at` server_default, FK
    actor/organization SET NULL, `action`, `target_type`/`target_id`,
    `payload` JSONB, `request_id`, `ip`, `user_agent`. Indici su
    `(organization_id, created_at)`, `(actor_user_id, created_at)`,
    `(action, created_at)`.

### Sequenza `downgrade()`

Drop delle tabelle in ordine inverso (rispetta le FK).

> Le estensioni `citext` e `uuid-ossp` non vengono droppate dal
> `downgrade` (raramente desiderabile rimuoverle).

---

## `alembic/versions/0004_avatar_v2.py`

**Migrazione avatar v2**: passa dal modello org-scoped al modello
1:1-per-utente con clip generative MiniMax e prompt admin. Rimuove anche
il permesso `avatar:manage` dal sistema.

### Sequenza `upgrade()`

1. **Drop tabella vecchia `avatars`** (org-scoped).
2. **`avatar_clip_prompts`**: PK UUID, `position` UNIQUE, `prompt` TEXT,
   `label_it`, `is_active`, timestamps.
3. **`avatars`** (nuovo): PK UUID, `user_id` UNIQUE FK `users.id`
   CASCADE, `image_path`, `audio_path`, `audio_text`, `audio_lang`,
   `clips_status` default `pending`, timestamps.
4. **`avatar_clips`**: PK UUID, FK `avatar_id` CASCADE, FK `prompt_id`
   SET NULL, `position`, `prompt_text`, `status`, `minimax_task_id`,
   `minimax_file_id`, `video_path`, `error_message`, `started_at`,
   `completed_at`, timestamps. Indici: `avatar_id`,
   `(avatar_id, position)`, `minimax_task_id`.
5. **Cleanup permesso `avatar:manage`**:
   - `DELETE FROM membership_permission_overrides USING permissions
     WHERE permission_id = permissions.id AND code = 'avatar:manage'`.
   - `DELETE FROM organization_role_permissions USING permissions WHERE
     permission_id = permissions.id AND code = 'avatar:manage'`.
   - `DELETE FROM role_permissions USING permissions WHERE
     permission_id = permissions.id AND code = 'avatar:manage'`.
   - `DELETE FROM permissions WHERE code = 'avatar:manage'`.

### Sequenza `downgrade()`

Drop di `avatar_clips`, `avatars`, `avatar_clip_prompts`. Ricrea la
tabella `avatars` org-scoped vecchia. Reinserisce il permesso
`avatar:manage` in `permissions` (gli override vanno ricreati a mano se
necessario).

> Il seed dei 5 prompt iniziali NON è eseguito dalla migrazione: è
> idempotente e vive in `app/db/seed.py::_seed_avatar_clip_prompts`,
> chiamato a startup da `ensure_seed`.

---

## `alembic/versions/0005_avatar_voice_script.py`

**Migrazione voice script avatar**: rimuove il campo libero `audio_text`
da `avatars` e introduce la tabella per gli script standardizzati che
l'utente deve leggere durante la registrazione (uno per lingua).

### Sequenza `upgrade()`

1. `op.drop_column("avatars", "audio_text")` (la colonna era stata
   creata nel `0004` come `Text NULL`).
2. `op.create_table("avatar_voice_scripts", ...)` con:
   - `language_code` `String(10)` PK + FK `languages.code` ON DELETE
     CASCADE,
   - `text` `Text` NOT NULL,
   - `created_at`/`updated_at` server_default + onupdate.

### Sequenza `downgrade()`

Drop di `avatar_voice_scripts` e ricreazione di `avatars.audio_text` come
`Text NULL`.

> Il seed di IT/EN è idempotente in
> `app/db/seed.py::_seed_avatar_voice_scripts` (dict
> `AVATAR_VOICE_SCRIPTS_SEED`), chiamato da `ensure_seed`.

---

## `alembic/versions/0006_pdf_bg_opacity.py`

**Migrazione opacità sfondo PDF**: rende configurabile l'opacità della
filigrana che prima era hardcoded a 15% nel preview.

### Sequenza `upgrade()`

`op.add_column("pdf_templates", sa.Column("background_opacity_pct",
sa.SmallInteger(), nullable=False, server_default="15"))`. Il default
server-side è già `15` per le righe esistenti.

### Sequenza `downgrade()`

`op.drop_column("pdf_templates", "background_opacity_pct")`.

---

## `alembic/versions/0007_org_course_settings.py`

**Migrazione parametri corso per organizzazione**: introduce la tabella
`organization_course_settings` (1:1 con `organizations`) e seeda il
permesso `course_config:manage`.

### Sequenza `upgrade()`

1. `op.create_table("organization_course_settings", ...)` con tutte le
   colonne business: PK UUID, `organization_id` UUID FK
   `organizations.id` ON DELETE CASCADE **UNIQUE** (1:1),
   `modules_per_cfu` (default 1), `lessons_per_module` (default 8),
   `lesson_duration_minutes` (default 15),
   `assessment_lesson_enabled` (default `true`),
   `multiple_choice_questions_count` (default 30),
   `open_questions_count` (default 6), timestamps `created_at` /
   `updated_at` con `server_default=func.now()`. CHECK constraint
   `>= 1` sui 3 campi durata/struttura, `>= 0` sui 2 campi domande.
2. **Backfill** delle organizzazioni esistenti:
   ```sql
   INSERT INTO organization_course_settings (id, organization_id)
   SELECT uuid_generate_v4(), id
   FROM organizations
   WHERE deleted_at IS NULL;
   ```
   I valori delle colonne business sono presi dai server_default.
3. **Seed permission** `course_config:manage`: inserisce la riga in
   `permissions` con descrizione articolata e collega ai role default
   `creator` e `org_admin` via `role_permissions`.

### Sequenza `downgrade()`

- `DROP TABLE organization_course_settings`.
- `DELETE FROM role_permissions WHERE permission_id IN (SELECT id
  FROM permissions WHERE code='course_config:manage')`.
- `DELETE FROM permissions WHERE code='course_config:manage'`.

---

## `alembic/versions/0008_permission_description_text.py`

**Migrazione descrizione permessi a TEXT**: la colonna
`permissions.description` era `VARCHAR(255)`. La nuova descrizione di
`course_config:manage` (~380 caratteri, articolata) eccedeva il limite
e produceva `StringDataRightTruncationError` al seed/upsert.

### Sequenza `upgrade()`

```python
op.alter_column(
    "permissions",
    "description",
    existing_type=sa.String(255),
    type_=sa.Text(),
    existing_nullable=True,
)
```

### Sequenza `downgrade()`

L'inverso: torna a `String(255)`. Attenzione: se nel frattempo sono
stati inseriti testi più lunghi, il downgrade fallirà.

---

## `alembic/versions/0009_courses.py` — `0024_lesson_speech_pdf.py`

**Migrazioni del dominio Corsi**. Lista sintetica (vedi
[Courses 01 — Data model](../courses/01-data-model.md) per il dettaglio
dei campi):

| Revision | Aggiunge |
|---|---|
| `0009_courses` | `languages`, `course_taxonomy_terms`, `course`, `course_document`, `course_module`, `course_lesson`. Seeda permessi `course:*`. |
| `0010_course_extras` | `course.lessons_per_module`, `architecture_meta`, `architecture_failure`, `architecture_failed_at`. |
| `0011_course_document_summary_meta` | Su `course_document`: `text_extracted_at`, `text_chars_extracted`, `summary_tokens`, `summary_attempts`. |
| `0012_lesson_bibliography` | `course_lesson.recommended_bibliography` JSONB default `[]`. |
| `0013_architecture_progress` | `course.architecture_progress` (smallint default 0), `architecture_progress_phase` (varchar 50). |
| `0014_lesson_structure` | Su `course_module` 10 colonne `lessons_structure_*` (Fase 2): status/raw/tokens/attempts/error/generated_at/approved_at/regeneration_hint/progress/progress_phase. + CHECK constraints. |
| `0015_lesson_content` | Su `course_lesson` 10 colonne `content_*` (Fase 3) + 4 campi struttura Fase 2 (`learning_objectives`, `mandatory_topics`, `prerequisites`, `section_outline`). Su `course` 5 colonne `glossary_*` (§10.1). |
| `0016_lesson_pdf_export` | Su `course_lesson` 8 colonne `pdf_*` (§7) + FK `pdf_template_id → pdf_templates.id` ON DELETE SET NULL. |
| `0017_didactic_setup_confirmed` | `course.didactic_setup_confirmed_at TIMESTAMPTZ NULL` per lock setup didattico (Tab 1+2 read-only quando valorizzato). |
| `0018_stale_detection_timestamps` | 3 colonne `*_modified_at` per stale-detection cascata: `course_module.architecture_modified_at`, `course_lesson.lesson_structure_modified_at`, `course_lesson.content_modified_at`. Set solo da CRUD manuali, mai dai worker AI. |
| `0019_lesson_slides` | Su `course_lesson` 11 colonne `slides_*` (Fase 4): status/raw/tokens/attempts/error/generated_at/approved_at/modified_at/regeneration_hint/progress/progress_phase + CHECK constraints. Su `course.status` aggiunge `slides_approved` al CHECK constraint (drop-and-recreate, simmetrico a content_approved). |
| `0020_lesson_slides_pdf` | Su `course_lesson` 8 colonne `slides_pdf_*` (Fase 4 PDF) + FK iniziale a `pdf_templates.id` (poi rerouted in 0022). |
| `0021_pdf_template_kind` | Su `pdf_templates` aggiunge `kind` VARCHAR(20) ('lesson' \| 'slides') come discriminatore tra template per lezione testo e template per slide. **Decisione poi rivista dalla 0022**. |
| `0022_unify_slide_templates` | Cambia destinazione FK `course_lesson.slides_pdf_template_id` da `pdf_templates(id)` → `slide_templates(id)` (azzera prima i valori esistenti perché incompatibili). Aggiunge `slide_templates.margin_mm` (default 20) + `slide_templates.background_opacity_pct` (default 15) con CHECK constraints. **Drop** del campo `pdf_templates.kind` (rollback di 0021). Decisione di prodotto: i template per le slide (avatar video + export PDF Fase 4) vengono unificati su `slide_templates`; `pdf_templates` resta dedicato a PDF lezione testo + PDF discorso (entrambi prosa A4 portrait). |
| `0023_lesson_speech` | Su `course_lesson` 11 colonne `speech_*` (Fase 5): mirror integrale di `slides_*`. Su `course.status` aggiunge `speech_approved` al CHECK constraint (drop-and-recreate, simmetrico a slides_approved). |
| `0024_lesson_speech_pdf` | Su `course_lesson` 8 colonne `speech_pdf_*` (Fase 5 PDF) + FK `speech_pdf_template_id → pdf_templates.id` ON DELETE SET NULL (NON `slide_templates`: il discorso è prosa pura, usa lo stesso template del PDF lezione testo). |

**Vincoli notevoli**:

- `uq_course_module_code` UNIQUE su `(course_id, module_code)`.
- `uq_course_lesson_code` UNIQUE su `(course_id, lesson_code)` — globale
  nel corso, NON per modulo. Vedi nota su renumber in
  [Manual editing](../courses/04-manual-editing.md).
- `course.status` CHECK constraint con **17 valori** dopo le migrations
  0019/0023 (aggiunti `slides_approved` e `speech_approved`).
- ON DELETE CASCADE su tutte le FK `course → modules → lessons` e su
  `course → documents`.
- ON DELETE SET NULL su tutte le FK template (`pdf_template_id`,
  `slides_pdf_template_id`, `speech_pdf_template_id`) per non bloccare
  l'eliminazione di un template usato da export passati.

**Round-trip testato**: tutte le migration hanno `downgrade()` che
ripristina lo schema precedente bit-a-bit (dropping CHECK constraints
nell'ordine inverso, dropping colonne in ordine inverso, ripristinando
gli stati intermedi delle FK in 0022/0024).

**Seed**:

- `_seed_languages`: 24 lingue UE (codici BCP47).
- `_seed_course_taxonomies`: 8 tassonomie di sistema con righe iniziali
  in IT/EN (categoria, stile_insegnamento, profondita_contenuto, ruolo_docente,
  dimensione_pubblico, livello_conoscenza, destinatari, livello_eqf).
- `_seed_course_permissions`: aggiunge i codici `course:*` a `permissions`
  e i mapping default a `role_permissions` per ciascun ruolo.

---

## `alembic/versions/0025_lesson_video.py`

**Migrazione video MP4 della lezione (Fase 6 §9)**: aggiunge i campi per
la generazione del video MP4 della lezione (TTS XTTS-v2 + slide PNG +
ffmpeg). Vedi [Courses 12 — Lesson video](../courses/12-lesson-video.md).

### Sequenza `upgrade()`

1. Su `course_lesson`, 8 colonne `video_*`:
   - `video_status` `VARCHAR(40)` NOT NULL `server_default='empty'`
     (`empty|pending|processing|ready|failed|cancelled`).
   - `video_progress` `SMALLINT` NOT NULL `server_default='0'` (0-100).
   - `video_progress_phase` `VARCHAR(50)` nullable
     (`preparing|tts|rendering_slides|encoding`).
   - `video_path` `VARCHAR(500)` nullable — path relativo
     (`lesson_videos/...`).
   - `video_attempts` `SMALLINT` NOT NULL `server_default='0'`.
   - `video_error` `TEXT` nullable.
   - `video_generated_at` `TIMESTAMPTZ` nullable.
   - `video_tokens` `JSONB` nullable — metadata della run
     (`audio_duration_s`, `video_duration_s`, `encode_duration_ms`,
     `tts_duration_ms`, `device`, `model_xtts`, `num_segments`,
     `num_slides`, `file_size_bytes`).
2. CHECK constraint `ck_course_lesson_video_status` sui 6 valori di
   `video_status`.
3. CHECK constraint `ck_course_lesson_video_progress`
   (`video_progress >= 0 AND video_progress <= 100`).
4. Index `ix_course_lesson_course_video_status` su
   `(course_id, video_status)` per le query batch.

### Sequenza `downgrade()`

Drop dell'index, dei 2 CHECK constraint e delle 8 colonne in ordine
inverso.

---

## `alembic/versions/0026_avatar_tts_latents_and_course_video_language.py`

**Migrazione rifinitura Fase 6**: tre cambi correlati attorno al TTS dei
video. Due dei tre interventi (la cache di latenti XTTS) sono stati poi
rimossi dalla `0027` quando il TTS è migrato su RunPod.

### Sequenza `upgrade()`

1. **Cache latenti TTS dell'avatar** — su `avatars`, 4 colonne
   `tts_latents_*` per persistere su disco i conditioning latents XTTS-v2
   estratti una sola volta per voce:
   - `tts_latents_status` `VARCHAR(16)` NOT NULL `server_default='pending'`.
   - `tts_latents_path` `VARCHAR(500)` nullable.
   - `tts_latents_generated_at` `TIMESTAMPTZ` nullable.
   - `tts_latents_error` `TEXT` nullable.
   - CHECK constraint `ck_avatar_tts_latents_status`
     (`pending|processing|ready|failed`).
2. **Force re-upload audio** — `avatars.audio_path` reso NULLABLE; poi
   `UPDATE avatars SET audio_path = NULL, audio_lang = NULL` per tutti
   gli avatar esistenti (gli audio pre-esistenti non hanno mai avuto i
   latents estratti; scelta consapevole di ripartire puliti). Il file
   fisico su `/uploads/avatars/*` resta, viene rimosso solo il
   riferimento DB.
3. **Lingua TTS per-corso** — `course.video_language_code`
   `VARCHAR(10)` nullable: override opzionale della
   `course.language_code` per la voce nei video (NULL → fallback su
   `language_code`). FK `fk_course_video_language → languages.code`
   ON DELETE SET NULL.

### Sequenza `downgrade()`

In ordine inverso: drop della FK e di `course.video_language_code`;
`audio_path` rimesso a NOT NULL (le righe NULL ricevono prima un
placeholder `''`, i path eliminati non sono ripristinabili); drop del
CHECK constraint e delle 4 colonne `tts_latents_*`.

---

## `alembic/versions/0027_drop_avatar_tts_latents.py`

**Migrazione rimozione cache latenti TTS**: con la migrazione del TTS su
RunPod Serverless (GPU) l'estrazione dei conditioning latents avviene al
volo (~1 s su GPU), quindi il sottosistema di pre-training dei latents
perde scopo.

### Sequenza `upgrade()`

Drop del CHECK constraint `ck_avatar_tts_latents_status` e delle 4
colonne `avatars.tts_latents_*` introdotte dalla `0026`.

`course.video_language_code` (anch'essa introdotta dalla `0026`)
**resta**: è la lingua TTS per-corso, ancora necessaria con RunPod.

### Sequenza `downgrade()`

Ricrea le 4 colonne `tts_latents_*` e il relativo CHECK constraint
(stessa definizione della `0026`).

---

## `alembic/versions/0028_assessment_lesson.py`

**Migrazione lezione di verifica delle competenze**: aggiunge il flag
`is_assessment` a `course_lesson`. Quando
`course.assessment_lesson_enabled` è attivo, l'ultima lezione di ogni
modulo viene materializzata come verifica delle competenze (domande a
scelta multipla + aperte) invece che come lezione didattica. Vedi
[Courses 14 — Assessment lesson](../courses/14-assessment-lesson.md).

### Sequenza `upgrade()`

`op.add_column("course_lesson", sa.Column("is_assessment",
sa.Boolean(), nullable=False, server_default=sa.text("false")))`. Il
default server-side `false` garantisce zero impatto sulle lezioni
esistenti.

### Sequenza `downgrade()`

`op.drop_column("course_lesson", "is_assessment")`.

---

## `alembic/versions/0029_avatar_video.py`

**Migrazione "Video con Avatar" (Fase 6b §9b)**: aggiunge i campi per la
scheda "Video con Avatar" delle lezioni — un video di avatar parlante
(lip-sync MuseTalk su RunPod) sovrapposto al video MP4 già generato
della lezione. Vedi
[Courses 13 — Avatar video](../courses/13-avatar-video.md).

### Sequenza `upgrade()`

1. Su `avatars`, 3 colonne `musetalk_*` — parametri MuseTalk per-avatar
   passati a `synth_random_lipsync`, con default = i valori del comando
   testato manualmente:
   - `musetalk_extra_margin` `SMALLINT` NOT NULL `server_default='15'`.
   - `musetalk_left_cheek_width` `SMALLINT` NOT NULL
     `server_default='110'`.
   - `musetalk_right_cheek_width` `SMALLINT` NOT NULL
     `server_default='110'`.
2. Su `course_lesson`, 8 colonne `avatar_video_*` — gemelle delle
   `video_*` della `0025`:
   - `avatar_video_status` `VARCHAR(40)` NOT NULL `server_default='empty'`
     (`empty|pending|processing|ready|failed|cancelled`).
   - `avatar_video_progress` `SMALLINT` NOT NULL `server_default='0'`
     (0-100).
   - `avatar_video_progress_phase` `VARCHAR(50)` nullable
     (`preparing|lipsync|overlay`).
   - `avatar_video_path` `VARCHAR(500)` nullable — path relativo
     (`lesson_avatar_videos/...`).
   - `avatar_video_attempts` `SMALLINT` NOT NULL `server_default='0'`.
   - `avatar_video_error` `TEXT` nullable.
   - `avatar_video_generated_at` `TIMESTAMPTZ` nullable.
   - `avatar_video_tokens` `JSONB` nullable — metadata della run.
3. CHECK constraint `ck_course_lesson_avatar_video_status` sui 6 valori
   di `avatar_video_status`.
4. CHECK constraint `ck_course_lesson_avatar_video_progress`
   (`avatar_video_progress >= 0 AND avatar_video_progress <= 100`).
5. Index `ix_course_lesson_course_avatar_video_status` su
   `(course_id, avatar_video_status)` per le query batch.

Tutti i default sono invariati: zero impatto sulle righe esistenti.

### Sequenza `downgrade()`

Drop dell'index, dei 2 CHECK constraint, delle 8 colonne
`avatar_video_*` e delle 3 colonne `musetalk_*`, tutto in ordine
inverso.

---

## `alembic/versions/0030_course_video_avatar_status.py`

**Estensione `CourseStatus` con 4 nuovi valori** per le Fasi 6 / 6b
(prima non rappresentate a livello corso): estende il CHECK constraint
`ck_course_status_valid` con `video_pending`, `video_ready`,
`avatar_video_pending`, `avatar_video_ready`. I service video/avatar
ricalcolano automaticamente `course.status` dopo ogni mutazione del
relativo `*_status` di una lezione (simmetrico a slides/speech). Vedi
[Courses 12 — Lesson video](../courses/12-lesson-video.md) e
[Courses 13 — Avatar video](../courses/13-avatar-video.md).

---

## `alembic/versions/0031_course_duplication.py`

**Tabella `course_duplication_job`** per orchestrare il job background
di duplicazione corso in altra lingua. Vedi
[Courses 15 — Duplicazione corso](../courses/15-course-duplication.md).

### Sequenza `upgrade()`

1. `CREATE TABLE course_duplication_job` con:
   - PK `id UUID`.
   - FK `source_course_id` → `course(id) ON DELETE CASCADE`.
   - FK `target_course_id` → `course(id) ON DELETE SET NULL`
     (popolata dopo la phase `cloning_structure` del worker).
   - FK `target_language_code` → `languages(code) ON DELETE RESTRICT`.
   - `status` VARCHAR(40) NOT NULL `server_default='pending'`.
   - `progress` SMALLINT NOT NULL `server_default='0'`.
   - `progress_phase` VARCHAR(50) nullable.
   - `error` TEXT nullable.
   - `attempts` SMALLINT NOT NULL `server_default='0'`.
   - `tokens` JSONB nullable (aggregato cost/token).
   - FK `requested_by_user_id` → `users(id) ON DELETE SET NULL`.
   - `started_at`, `finished_at` TIMESTAMPTZ nullable.
   - `created_at`, `updated_at` TIMESTAMPTZ NOT NULL `default now()`.
2. CHECK constraint `ck_course_duplication_job_status` su
   `status IN ('pending','processing','ready','failed')`.
3. CHECK constraint `ck_course_duplication_job_progress`
   (`progress >= 0 AND progress <= 100`).
4. Index `ix_course_duplication_job_source` su `source_course_id`.
5. Index `ix_course_duplication_job_target` su `target_course_id`.
6. Index `ix_course_duplication_job_status` su `status`.
7. **Unique parziale** `uq_course_duplication_active` su
   `(source_course_id, target_language_code) WHERE status IN
   ('pending','processing')` — impedisce a livello DB job concorrenti
   per la stessa coppia (source, lingua target). Funzionalità
   Postgres-only.

### Sequenza `downgrade()`

`DROP INDEX uq_course_duplication_active` + drop dei 3 index normali +
`DROP TABLE course_duplication_job`.

---

## Workflow per nuove migrazioni

```bash
cd backend
source .venv/Scripts/activate           # se il venv non è già attivo
# Modifichi un modello (es. aggiungi campo)
alembic revision --autogenerate -m "add foo to bar"
# Verifica il file generato in alembic/versions/
# Aggiusta a mano se necessario (rinomina, default complessi)
alembic upgrade head
```

> Convenzione: una sola revision per cambio schema, nome descrittivo.
> Le revision sono reversibili dove possibile.
