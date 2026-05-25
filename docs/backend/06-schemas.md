# Backend 06 — `app/schemas/`

DTO Pydantic v2 usati per input/output dei router. Le classi che leggono dai
modelli ORM derivano da `ORMModel` (con `from_attributes=True`).

## `app/schemas/__init__.py`

Vuoto.

---

## `app/schemas/common.py`

### `class ORMModel(BaseModel)`

Base con `model_config = ConfigDict(from_attributes=True)`. Ogni schema di
output ne deriva.

### `class PageMeta(BaseModel)`

Campi: `page` (≥1), `page_size` (1..200), `total` (≥0).

### `class Page(BaseModel, Generic[T])`

Campi: `items: list[T]`, `meta: PageMeta`. Usato come `Page[OrganizationOut]`,
ecc.

---

## `app/schemas/user.py`

### `class UserOut(ORMModel)`

Output utente.
- `id: UUID`
- `email: EmailStr`
- `full_name: str`
- `is_platform_admin: bool`
- `is_active: bool`
- `last_login_at: datetime | None`
- `created_at: datetime`

### `class UserCreateAdmin(BaseModel)`

Input creazione utente da admin piattaforma.
- `email: EmailStr`
- `full_name: str` (1..255)
- `password: str` (10..128)
- `is_platform_admin: bool = False`

Validatore `_validate_password`: chiama `is_password_strong(v)`. Se fallisce,
`ValueError("Password debole...")`.

### `class UserUpdateAdmin(BaseModel)`

Tutti opzionali: `full_name`, `is_platform_admin`, `is_active`.

### `class MeOrganizationOut(ORMModel)`

Output di un'organizzazione di cui l'utente corrente è membro:
- `organization_id`, `organization_name`, `role_code`, `role_name_it`,
  `permissions: list[str]` (già risolti).

### `class MeOut(BaseModel)`

Output `/auth/me`:
- `user: UserOut`
- `organizations: list[MeOrganizationOut]`
- `is_platform_admin: bool`

---

## `app/schemas/auth.py`

### `class LoginRequest(BaseModel)`

- `email: EmailStr`
- `password: str` (1..200)

### `class InvitationAcceptRequest(BaseModel)`

Per nuovi utenti che accettano l'invito serve nome+password; per utenti
esistenti basta accettare. Quindi entrambi opzionali:
- `full_name: str | None` (1..255)
- `password: str | None` (10..128)

---

## `app/schemas/organization.py`

### `class OrganizationBase(BaseModel)`

Tutti i campi anagrafica:
- `name` (NOT NULL), `email` (EmailStr).
- Optional: `phone`, `website`, `vat_number`, `fiscal_code`, `country`,
  `address`, `city`, `province`, `postal_code` (con i max length corretti).

### `class OrganizationOut(OrganizationBase, ORMModel)`

Aggiunge `id`, `logo_path: str | None`, `created_at`, `updated_at`.

---

## `app/schemas/organization_course_settings.py`

DTO per i parametri di configurazione corsi (1:1 con `Organization`).

### `class OrganizationCourseSettingsBase(BaseModel)`

I 6 campi business validati con `Field(ge=...)`:

- `modules_per_cfu: int = Field(ge=1)`,
- `lessons_per_module: int = Field(ge=1)`,
- `lesson_duration_minutes: int = Field(ge=1)`,
- `assessment_lesson_enabled: bool`,
- `multiple_choice_questions_count: int = Field(ge=0)`,
- `open_questions_count: int = Field(ge=0)`.

### `class OrganizationCourseSettingsUpdate(OrganizationCourseSettingsBase)`

Body PUT idempotente: tutti i 6 campi sono richiesti (sostituisce
completamente la configurazione corrente).

### `class OrganizationCourseSettingsOut(OrganizationCourseSettingsBase, ORMModel)`

Aggiunge `id: UUID`, `organization_id: UUID`, `created_at`,
`updated_at`.

---

## `app/schemas/i18n.py`

DTO per le lingue e le traduzioni admin.

### `class LanguageOut(...)`

Output di una lingua. Include il nuovo campo `untranslated_count: int =
0` (numero di chiavi mancanti o vuote rispetto alla lingua di default,
calcolato server-side via `i18n_service.count_untranslated_for_language`).

### `class AutoTranslateResponse(BaseModel)`

Response del nuovo endpoint
`POST /admin/i18n/languages/{code}/auto-translate`:

- `code: str` — codice lingua target,
- `requested: int` — numero di chiavi candidate,
- `translated: int` — numero di chiavi effettivamente tradotte e
  upserted,
- `skipped: int` — chiavi saltate (value vuoto/non-stringa),
- `errors: list[str]` — eventuali errori di batch (max 5 propagati al
  client).

---

## `app/schemas/membership.py`

### `class MembershipOut(ORMModel)`

Composta a partire dal join (vedi router `memberships.py`):
- `id`, `user_id`, `user_email`, `user_full_name`, `organization_id`,
  `role_id`, `role_code`, `role_name_it`, `joined_at`.

### `class EnrollUserRequest(BaseModel)`

- `user_id: UUID`, `role_code: str`.

### `class ChangeRoleRequest(BaseModel)`

- `role_code: str`.

### `class InvitationCreateRequest(BaseModel)`

- `email: EmailStr`, `role_code: str`.

### `class InvitationOut(ORMModel)`

- `id`, `organization_id`, `email`, `role_code`, `expires_at`, `accepted_at`.

### `class InvitationCreateResponse(BaseModel)`

- `invitation: InvitationOut`
- `token: str` (in chiaro, solo nella risposta della creazione).
- `accept_url: str`.

### `class TransferCreatorRequest(BaseModel)`

- `target_user_id: UUID`.

### `class PermissionOverrideEntry(BaseModel)`

- `code: str` (1..80), `granted: bool`.

### `class PermissionOverridesUpdate(BaseModel)`

- `overrides: list[PermissionOverrideEntry]`.

### `class RolePermissionDefaultUpdate(BaseModel)`

- `role_code: str`, `permissions: list[str]` (lista esatta dei codici da
  associare al ruolo a livello globale).

---

## `app/schemas/template.py`

### `HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"`

### `_normalize_hex(value: str | None) -> str | None`

Normalizza stringhe come `"1976D2"` o `"#1976d2"` in `"#1976D2"`. Restituisce
`None` se input è `None`. Lancia `ValueError` se la lunghezza non è valida.

### `class _TemplateColors(BaseModel)`

Mixin per i 3 colori e il font:
- `text_color`, `primary_color`, `secondary_color` con pattern hex
  (default `#1F1F1F` / `#1976D2` / `#9C27B0`).
- `font_family: str` (1..120, default `Roboto`).
- Validator pre-mode `_norm` che invoca `_normalize_hex`.

### `class SlideTemplateBase(_TemplateColors)`

- `name: str` (1..120).
- `slide_size: Literal["16:9", "4:3"] = "16:9"`.

### `class SlideTemplateOut(SlideTemplateBase, ORMModel)`

Aggiunge `id`, `organization_id`, `background_image_path`, `logo_left_path`,
`logo_right_path`, `created_at`, `updated_at`.

### `class PdfTemplateBase(_TemplateColors)`

- `name: str` (1..120).
- `page_size: Literal["A4", "Letter"] = "A4"`.
- `header_height_mm: int` (0..80, default 20).
- `footer_height_mm: int` (0..80, default 15).
- `margin_mm: int` (0..60, default 20).
- `background_opacity_pct: int = Field(default=15, ge=0, le=100)`
  (opacità della filigrana di sfondo).

### `class PdfTemplateOut(PdfTemplateBase, ORMModel)`

Stesse aggiunte di `SlideTemplateOut`.

---

## `app/schemas/avatar.py`

### `AvatarClipStatus = Literal["pending", "processing", "ready", "failed"]`

### `AvatarClipsAggregateStatus = Literal["pending", "processing", "ready", "partial", "failed"]`

### `class AvatarClipOut(ORMModel)`

Output di una singola clip:
- `id: UUID`, `avatar_id: UUID`, `position: int`,
- `prompt_text: str` (snapshot),
- `status: AvatarClipStatus`,
- `video_path: str | None`,
- `error_message: str | None`,
- `started_at`, `completed_at`, `created_at`, `updated_at`.

### `class AvatarOut(ORMModel)`

Output dell'avatar utente (1:1 con `User`):
- `id: UUID`, `user_id: UUID`,
- `audio_lang: str | None`,
- `clips_status: str`,
- `musetalk_extra_margin: int`, `musetalk_left_cheek_width: int`,
  `musetalk_right_cheek_width: int` — parametri MuseTalk per la scheda
  "Video con Avatar" delle lezioni,
- `clips: list[AvatarClipOut]` (ordinate per `position`),
- `created_at`, `updated_at`.

`image_path` e `audio_path` sono campi `Field(exclude=True)`: non
compaiono nel JSON. Al loro posto due `@computed_field`:
- `image_url: str` — `storage_service.public_url(image_path)`,
- `audio_url: str | None` — `public_url(audio_path)` o `None`.

> Il campo `audio_text` è stato rimosso. Lo script da leggere è ora
> esposto via `AvatarVoiceScriptOut`.

### `class AvatarMusetalkParamsUpdate(BaseModel)`

Body per `PATCH /me/avatar/musetalk-params`. Tutti e tre i campi sono
obbligatori (la UI invia sempre i tre valori):
- `musetalk_extra_margin: int = Field(ge=0, le=200)`,
- `musetalk_left_cheek_width: int = Field(ge=0, le=400)`,
- `musetalk_right_cheek_width: int = Field(ge=0, le=400)`.

I default del modello ORM sono i valori del comando MuseTalk testato
manualmente (15 / 110 / 110).

### `class AvatarVoiceScriptOut(ORMModel)`

Output dello script standardizzato per la lingua `language_code`:
- `language_code: str`,
- `text: str`,
- `created_at: datetime`, `updated_at: datetime`.

### `class AvatarVoiceScriptUpsert(BaseModel)`

Body usato sia in admin (`PUT /admin/avatar-config/voice-scripts/{lang}`)
sia internamente dal service:
- `text: str = Field(min_length=1, max_length=4000)`.

### `class AvatarClipPromptOut(ORMModel)`

- `id: UUID`, `position: int`,
- `prompt: str`, `label_it: str`,
- `is_active: bool`,
- timestamps.

### `class AvatarClipPromptCreate(BaseModel)`

- `prompt: str` (1..4000),
- `label_it: str` (1..120),
- `is_active: bool = True`.

### `class AvatarClipPromptUpdate(BaseModel)`

Tutti opzionali: `prompt`, `label_it`, `is_active`.

### `class AvatarClipPromptReorder(BaseModel)`

- `ordered_ids: list[UUID]`.

---

## Schemi del dominio Corsi

La maggior parte degli schemi del dominio Corso (Fasi 1-5, PDF) è
documentata nella sezione dedicata. Sotto sono documentati gli schemi
delle feature recenti — verifica delle competenze (Fase 3) e generazione
video (Fasi 6 / 6b) — che hanno endpoint backend di riferimento.

---

## `app/schemas/course_lesson_content.py` — classi assessment

Oltre agli schemi della Fase 3 (contenuto didattico, `LessonContent*`),
questo file definisce gli schemi della **verifica delle competenze**: il
payload polimorfico che vive in `course_lesson.content_raw` quando
`lesson.is_assessment`.

### `class AssessmentMCOption(BaseModel)`

Una opzione di una domanda a scelta multipla. `extra="forbid"`.
- `option_id: str` (1..10) — es. `"A"`..`"D"`,
- `text: str` (1..1000).

### `class AssessmentMCQuestion(BaseModel)`

Domanda a scelta multipla. `extra="forbid"`.
- `question_id: str` (1..20),
- `text: str` (1..2000),
- `options: list[AssessmentMCOption]` (2..6 elementi),
- `correct_option_id: str` (1..10) — referenzia un `option_id` esistente.

### `class AssessmentOpenQuestion(BaseModel)`

Domanda aperta con traccia di risposta attesa (per la correzione).
`extra="forbid"`.
- `question_id: str` (1..20),
- `text: str` (1..2000),
- `expected_answer: str` (1..4000).

### `class LessonAssessmentOutput(BaseModel)`

Output AI per una lezione di verifica. Polimorfico con
`LessonContentOutput`: entrambi vivono in `content_raw`, il discriminante
è la chiave `is_assessment`. `extra="forbid"`.
- `lesson_id: str`, `lesson_title: str`,
- `is_assessment: Literal[True] = True`,
- `multiple_choice_questions: list[AssessmentMCQuestion]`,
- `open_questions: list[AssessmentOpenQuestion]`.

### `class LessonAssessmentUpdateInput(BaseModel)`

Body per `PATCH /lessons/{lid}/assessment` (CRUD manuale della verifica).
Entrambe le liste opzionali; l'edit non degrada lo status. Validazione
di consistenza (id univoci, una sola opzione corretta) nel service.
`extra="forbid"`.
- `multiple_choice_questions: list[AssessmentMCQuestion] | None`,
- `open_questions: list[AssessmentOpenQuestion] | None`.

---

## `app/schemas/course_lesson_video.py`

DTO della **Fase 6 — generazione video MP4 della lezione** (§9). Il
video è prodotto dal worker `course_lesson_video_worker`.

### `class LessonVideoGenerateInput(BaseModel)`

Body opzionale per `POST .../video/generate` (riservato a future opzioni
come override risoluzione/preset). **Attualmente vuoto**, `extra="forbid"`.

### `class LessonVideoStatusOut(ORMModel)`

Stato video di una lezione singola:
- `lesson_id: str`, `lesson_code: str`,
- `status: str` (`empty|pending|processing|ready|failed|cancelled`),
- `progress: int = 0`, `progress_phase: str | None`,
- `video_url: str | None` — path pubblico `/uploads/...` quando `ready`,
- `error: str | None`, `attempts: int = 0`,
- `generated_at: datetime | None`,
- `tokens: dict | None` — metadata della run (durate, device, num_*),
- `is_stale: bool` — `True` se il video è stato generato prima di un
  cambio a discorso o slide (`speech_modified_at`/`slides_modified_at`/
  `speech_approved_at`/`slides_approved_at`),
- pre-requisiti runtime: `speech_approved: bool`, `slides_approved: bool`,
  `voice_sample_available: bool` (il FE li usa per disabilitare "Genera").

### `class LessonVideoMetaOut(ORMModel)`

Meta video di lezione esposto in `CourseLessonOut` per le pagine indice:
`video_status`, `video_progress`, `video_progress_phase`, `video_path`,
`video_error`, `video_attempts`, `video_generated_at`, `video_tokens`.

### `class LessonVideoBatchOut(BaseModel)`

Snapshot batch a livello corso (`GET .../lessons-video/status`):
- `items: list[LessonVideoStatusOut]`,
- `total`, `ready_count`, `processing_count`, `pending_count`,
  `failed_count` (int),
- `eligible_count: int` — lezioni eleggibili (speech+slides approved AND
  voice sample disponibile); usato dal bottone "Genera tutti",
- `aggregate_progress: int` — 0-100, media sulle lezioni in flight.

---

## `app/schemas/course_lesson_avatar_video.py`

DTO della **Fase 6b — "Video con Avatar"** (§9b): il video MP4 della
lezione con l'avatar parlante (lip-sync MuseTalk) sovrapposto. Prodotto
dal worker `course_lesson_avatar_video_worker`.

### `class LessonAvatarVideoGenerateInput(BaseModel)`

Body opzionale per `POST .../avatar-video/generate` (riservato a future
opzioni). **Attualmente vuoto**, `extra="forbid"`.

### `class LessonAvatarVideoStatusOut(ORMModel)`

Stato del video con avatar di una lezione singola:
- `lesson_id: str`, `lesson_code: str`,
- `status: str` (`empty|pending|processing|ready|failed|cancelled`),
- `progress: int = 0`, `progress_phase: str | None`,
- `video_url: str | None` — path pubblico `/uploads/...` quando `ready`,
- `error: str | None`, `attempts: int = 0`,
- `generated_at: datetime | None`,
- `tokens: dict | None`,
- `is_stale: bool` — `True` se il video con avatar è più vecchio
  dell'ultima rigenerazione del video della lezione
  (`avatar_video_generated_at < video_generated_at`),
- pre-requisiti runtime: `lesson_video_ready: bool` (il video della
  lezione esiste), `avatar_clips_ready: bool` (l'avatar ha clip pronte).

### `class LessonAvatarVideoBatchOut(BaseModel)`

Snapshot batch a livello corso (`GET .../lessons-avatar-video/status`):
- `items: list[LessonAvatarVideoStatusOut]`,
- `total`, `ready_count`, `processing_count`, `pending_count`,
  `failed_count` (int),
- `eligible_count: int` — lezioni eleggibili (video lezione `ready` AND
  avatar con clip pronte),
- `aggregate_progress: int` — 0-100, media sulle lezioni in flight,
- `avatar_clips_ready: bool` — stato course-level dell'avatar
  dell'assegnatario: se `false` nessuna lezione è eleggibile.

---

## `app/schemas/admin_metrics.py`

DTO per `GET /admin/metrics` (dashboard pannello admin). I conteggi per
status (`by_status`) sono restituiti **raw**; il bucketing in macro-fasi
avviene lato frontend per mantenere localizzate le label e cambiabili
senza migrazione backend. Snapshot cached server-side TTL 60s in
`admin_metrics_service`.

### `class StatusCount(BaseModel)`

Conteggio per uno status. Riutilizzato in vari blocchi:
- `status: str`, `count: int`.

### `class UsersMetrics(BaseModel)`

- `total`, `active` (`is_active=true`),
- `active_last_30d` (`last_login_at >= now()-30d`).

### `class OrgsMetrics(BaseModel)`

- `total` — `WHERE deleted_at IS NULL`.

### `class CoursesMetrics(BaseModel)`

- `total: int`,
- `by_status: list[StatusCount]` — 17 valori possibili
  (vedi [Courses 01](../courses/01-data-model.md)).

### `class LessonsPhaseBreakdown(BaseModel)`

Conteggi per ciascuna delle 5 fasi pipeline per-lezione:
- `content`, `slides`, `speech`, `video`, `avatar_video` — ognuno
  `list[StatusCount]`.

### `class LessonsMetrics(BaseModel)`

- `total: int`, `phases: LessonsPhaseBreakdown`.

### `class CostByPhase(BaseModel)`

Costo OpenAI cumulato per una fase: `phase: str` (architecture |
structure | content | slides | speech), `cost_usd: float`.

Nota: `Course.glossary_tokens` usa lo schema vecchio senza `cost_usd` e
NON compare in `by_phase`.

### `class CostMetrics(BaseModel)`

- `total_usd`, `last_7d_usd`, `last_30d_usd: float`,
- `by_phase: list[CostByPhase]` (5 entries).

### `class AvatarClipsMetrics(BaseModel)`

- `by_status: list[StatusCount]` — `pending|processing|ready|failed`.

### `class LoginDayMetric(BaseModel)`

- `date: str` (YYYY-MM-DD UTC), `success: int`, `failure: int`.

### `class LoginActivityMetrics(BaseModel)`

- `last_7d: list[LoginDayMetric]` — sempre 7 entries (zero-fill),
- `success_total_7d`, `failure_total_7d: int`.

### `class AuditRecentEntry(BaseModel)`

- `id: UUID`, `created_at: datetime`, `action: str`,
- `actor_user_name: str | None`, `organization_name: str | None`,
- `target_type: str | None`, `target_id: str | None`.

### `class AdminMetricsOut(BaseModel)`

Top-level DTO. Contiene tutti i blocchi sopra + `generated_at:
datetime`.

---

## `app/schemas/org_metrics.py`

DTO per `GET /orgs/{org_id}/metrics` (dashboard organizzazione). Importa
`StatusCount` e `LessonsPhaseBreakdown` da `admin_metrics` per non
duplicare le stesse shape. **Niente costi AI nel payload** (scelta di
prodotto — vedi memoria `feedback_no_api_costs_in_org_views`).

### `class AssigneeWorkload(BaseModel)`

Carico di lavoro di un docente. Restituiti i **top 10** per numero di
corsi.
- `user_id: UUID`, `name: str | None`, `course_count: int`.

### `class RoleCount(BaseModel)`

- `role_code: str` (creator | org_admin | manager | member),
- `role_name_it: str | None`, `count: int`.

### `class AvatarReadiness(BaseModel)`

Stato avatar dei docenti assegnati a qualche corso dell'org:
- `total_assignees: int`,
- `ready: int` — ha `audio_path` AND ≥1 clip MiniMax `ready`,
- `partial: int` — solo uno dei due,
- `not_ready: int` — nessun avatar oppure niente audio né clip pronte.

### `class CoursesMetrics(BaseModel)`

- `total: int`, `by_status: list[StatusCount]`,
- `by_assignee: list[AssigneeWorkload]` — top 10.

(Diverso da `admin_metrics.CoursesMetrics`: ha in più `by_assignee`.)

### `class LessonsMetrics(BaseModel)`

- `total: int`, `phases: LessonsPhaseBreakdown`.

### `class MembersMetrics(BaseModel)`

- `total: int`, `by_role: list[RoleCount]`,
- `pending_invitations: int` (`accepted_at IS NULL AND revoked_at IS
  NULL AND expires_at > now()`).

### `class AuditRecentEntry(BaseModel)`

Come `admin_metrics.AuditRecentEntry` ma **senza `organization_name`**
(ridondante: tutte le entry sono già filtrate per `organization_id`).

### `class OrgMetricsOut(BaseModel)`

Top-level DTO. Contiene `courses / lessons / modules_total / members /
avatar_readiness / audit_recent` + `generated_at`. **Nessun campo
`cost`** né campi `cost_usd`/totali token.
