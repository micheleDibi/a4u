# Backend 08 — `app/api/v1/`

Router REST. Tutti registrati sotto il prefisso `/api/v1` da
`app.api.v1.api_router`.

## `app/api/__init__.py` / `app/api/v1/__init__.py`

`api/__init__.py` è vuoto. `v1/__init__.py` importa tutti i sotto-router e
li include in un `APIRouter(prefix="/api/v1")`. L'ordine di inclusione non
influenza il routing (FastAPI usa la prima rotta che matcha; non ci sono
collisioni).

I router inclusi:
- `system` (health, ready, log-client)
- `auth` (login, refresh, logout, me)
- `admin_organizations` (`/admin/organizations`)
- `admin_users` (`/admin/users`, `/admin/organizations/{org_id}/memberships`)
- `admin_permissions` (`/admin/permissions/*`)
- `memberships` (`/orgs/{org_id}/members*`, `/orgs/{org_id}/permissions/*`,
  `/orgs/{org_id}/transfer-creator`)
- `invitations` (`/orgs/{org_id}/invitations`, `/invitations/{token}/*`)
- `slide_templates` (`/orgs/{org_id}/templates/slide*`)
- `pdf_templates` (`/orgs/{org_id}/templates/pdf*`)
- `organization_course_settings` (`/orgs/{org_id}/course-settings`)
- `me_avatar` (`/me/avatar*`)
- `admin_avatar_config` (`/admin/avatar-config/prompts*`,
  `/admin/avatar-config/voice-scripts*`)
- `admin_i18n` (`/admin/i18n/languages*`)
- `courses` (`/orgs/{org_id}/courses*` — dominio Corsi: Fasi 1-6,
  export PDF; il dettaglio Fasi 1-5 è nella sezione Courses)

---

## `app/api/v1/system.py`

`router = APIRouter(prefix="/system", tags=["system"])`.

### `GET /system/health`

Liveness probe (no DB). Restituisce `{ "status": "ok" }`.

### `GET /system/ready`

Readiness probe. Esegue `SELECT 1`. Restituisce `{ status, db: "ok" }`.

### `POST /system/log-client`

Endpoint per ricevere errori dal frontend e loggarli centralmente.
Rate-limit `60/minute`. Body: arbitrario (JSON). Logga `client_event`
con `payload` e `request_id`.

---

## `app/api/v1/auth.py`

`router = APIRouter(prefix="/auth", tags=["auth"])`.

### Helper

#### `_set_auth_cookies(response, *, access, refresh)`

Imposta i cookie `access_token` (path `/`) e `refresh_token` (path
`/api/v1/auth/refresh`) entrambi HttpOnly, SameSite=Lax,
Secure conforme a `COOKIE_SECURE`.

#### `_clear_auth_cookies(response)`

Cancella i due cookie.

### `POST /auth/login`

Body: `LoginRequest { email, password }`. Rate-limit `5/minute/IP`.
Chiama `auth_service.login`, setta cookie. Risposta:
`{ "status": "ok", "user_id": "<uuid>" }`.

### `POST /auth/refresh`

Cookie `refresh_token`. Rate-limit `30/minute`. Se manca cookie, ritorna
`401` con `{ "status": "missing_refresh" }`.
Altrimenti `auth_service.rotate_refresh`, setta nuovi cookie.

### `POST /auth/logout`

Cookie `refresh_token`. Chiama `auth_service.revoke_refresh_token`, clear
cookie. `{ "status": "ok" }`.

### `GET /auth/me`

Restituisce `MeOut`:

```json
{
  "user": { ... },
  "organizations": [
    { "organization_id", "organization_name", "role_code",
      "role_name_it", "permissions": ["member:view", ...] }
  ],
  "is_platform_admin": true
}
```

Se l'utente è platform admin, `organizations` è vuoto (l'admin opera
trasversalmente, non ha membership). Per ogni membership viene chiamato
`resolve_permissions` per produrre il set risolto.

### Self-service profilo personale

Tre rotte che operano sull'utente corrente (`CurrentUser`, solo
autenticazione). Tutte ritornano `MeOut` ricostruito da `_build_me` (helper
condiviso con `GET /auth/me`) e scrivono audit.

#### `PATCH /auth/me`

Body `ProfileUpdate { full_name }`. Modifica del proprio nome, **nessuna
re-auth** (azione a basso rischio). Audit `user.profile.update`.

#### `POST /auth/me/change-email`

Body `ChangeEmailRequest { current_password, new_email }`. Verifica la
password attuale (`401 invalid_current_password`); `422 email_unchanged` se
coincide con quella attuale; `409 email_in_use` se già usata da un altro
utente. Le sessioni **restano valide** (l'email è un identificatore, non una
credenziale). Audit `user.email.change` con `metadata {old, new}`.

#### `POST /auth/me/change-password`

Rate-limit `5/minute`. Body `ChangePasswordRequest { current_password,
new_password }` (la nuova è validata da `is_password_strong`). Verifica la
password attuale (`401 invalid_current_password`); `422 password_unchanged` se
identica all'attuale. **Non** revoca le proprie sessioni (UX amichevole,
asimmetria voluta rispetto al reset lato admin). Audit `user.password.change`.

---

## `app/api/v1/admin_organizations.py`

`router = APIRouter(prefix="/admin/organizations",
tags=["admin-organizations"])`. Tutti gli endpoint richiedono
`PlatformAdmin`.

### Helper

#### `_form_organization(...)` (Depends factory)

Espande i campi di `OrganizationBase` come `Form(...)` per accettare
multipart.

### `GET /admin/organizations`

Query `?page=1&page_size=25&q=...`. Restituisce `Page[OrganizationOut]`.

### `POST /admin/organizations`

Multipart con tutti i campi `OrganizationBase` + opzionale `logo: UploadFile`.
Salva il logo via `save_upload_image(logo, subdir="organizations")` e
chiama `org_service.create_organization`. **201**.

### `GET /admin/organizations/{org_id}`

Restituisce `OrganizationOut`.

### `PUT /admin/organizations/{org_id}`

Multipart con campi + `logo` opzionale + `remove_logo: bool` opzionale.
Logica:
- Se è stato caricato un nuovo `logo` → upload e usa il nuovo path.
- Altrimenti se `remove_logo=true` → `new_logo = None`.
- Altrimenti mantiene il path esistente.

Aggiorna l'org via `org_service.update_organization`.

### `DELETE /admin/organizations/{org_id}` → 204

Soft-delete via `org_service.soft_delete_organization`.

---

## `app/api/v1/admin_users.py`

`router = APIRouter(prefix="/admin", tags=["admin-users"])`. Tutti i
endpoint richiedono `PlatformAdmin`.

### `GET /admin/users`

Query: `page`, `page_size`, `q` (LIKE su email o full_name).
Restituisce `Page[UserOut]`.

### `POST /admin/users`

Body `UserCreateAdmin { email, full_name, password, is_platform_admin? }`.
Verifica email non in uso (else 409 `email_in_use`). Hash password e salva
con `is_active=True`. Audit `user.create`. **201** restituisce `UserOut`.

### `PUT /admin/users/{user_id}`

Body `UserUpdateAdmin` (campi opzionali: `full_name`, `email`,
`is_platform_admin`, `is_active`). Delega a
`user_admin_service.update_user_admin`, che applica le invarianti di
sicurezza: `404 user_not_found`; **self-guard** (`409
cannot_deactivate_self`, `409 cannot_demote_self`); **last-active-admin**
(`409 last_active_admin` se è l'unico platform admin attivo);
`409 email_in_use` su collisione email. Audit `user.update` con i campi
modificati. Restituisce `UserOut`.

> La rimozione di un account = disattivazione (`is_active=False`),
> reversibile. **Nessuna** delete fisica.

### `POST /admin/users/{user_id}/password`

Body `UserAdminSetPassword { password }` (validata da `is_password_strong`).
Reset password manuale lato admin (no SMTP). Delega a
`user_admin_service.set_user_password`: hash della nuova password e
**revoca di tutti i refresh token vivi** dell'utente target (forza il
re-login; gli access JWT restano validi fino a scadenza TTL). `404
user_not_found`. Audit `user.password_reset`. Restituisce `UserOut`.

### `POST /admin/organizations/{org_id}/memberships` → 201

Body `EnrollUserRequest { user_id, role_code }`. Chiama
`membership_service.enroll_user`. Restituisce `MembershipOut`.

> Solo platform admin può iscrivere utenti direttamente. Gli altri ruoli
> usano gli inviti.

---

## `app/api/v1/admin_permissions.py`

`router = APIRouter(prefix="/admin/permissions",
tags=["admin-permissions"])`. Richiede `PlatformAdmin`.

### `GET /admin/permissions/permissions`

Restituisce `{ permissions: list[str], roles: [{code, name_it}] }`.
Catalogo per UI.

### `GET /admin/permissions/role-defaults?role_code=org_admin`

Restituisce `{ role_code, permissions: list[str] }`.

### `PUT /admin/permissions/role-defaults`

Body `RolePermissionDefaultUpdate { role_code, permissions[] }`.
Chiama `permission_service.update_role_default_permissions`.

---

## `app/api/v1/memberships.py`

`router = APIRouter(prefix="/orgs/{org_id}", tags=["organizations"])`.

### `GET /orgs/{org_id}/members`

Permesso: `member:view`. Restituisce `list[MembershipOut]`. Join con
`User` e `OrganizationRole` per popolare nome/email/ruolo. Ordine: rank
ASC, nome ASC.

### Helper `_get_membership_or_404(db, *, org_id, user_id) -> Membership`

Carica la membership; 404 se assente.

### `PUT /orgs/{org_id}/members/{user_id}/role`

Permesso: `member:assign_role`. Body `ChangeRoleRequest`. Carica la
membership target e quella dell'attore (se non platform admin) e chiama
`membership_service.change_role`. Restituisce `MembershipOut` aggiornato.

### `DELETE /orgs/{org_id}/members/{user_id}` → 204

Permesso: `member:remove`. Carica la membership e chiama
`remove_membership`.

### `GET /orgs/{org_id}/members/{user_id}/permissions`

Permesso: `permission:manage`. Restituisce `{ membership_id, overrides:
[{code, granted}] }`.

### `PUT /orgs/{org_id}/members/{user_id}/permissions`

Permesso: `permission:manage`. Body `PermissionOverridesUpdate`. Chiama
`permission_service.upsert_membership_permissions`.

### `GET /orgs/{org_id}/permissions/role/{role_code}`

Permesso: `permission:manage`. Restituisce `{ role_code, defaults,
overrides: [{code, granted}] }`.

### `PUT /orgs/{org_id}/permissions/role/{role_code}`

Permesso: `permission:manage`. Body `PermissionOverridesUpdate`.

### `POST /orgs/{org_id}/transfer-creator`

Permesso: `org:transfer_creator`. Body `TransferCreatorRequest { target_user_id }`.
Carica `actor_membership`; se l'attore è platform admin senza membership,
carica come actor il creator corrente dell'org. Chiama
`membership_service.transfer_creator`.

---

## `app/api/v1/invitations.py`

`router = APIRouter(tags=["invitations"])`.

### `POST /orgs/{org_id}/invitations` → 201

Permesso: `member:invite`. Body `InvitationCreateRequest`. Crea l'invito,
costruisce `accept_url = f"{PUBLIC_BASE_URL}/invitations/{token}"`.
Restituisce `InvitationCreateResponse { invitation, token, accept_url }`.

> Il token è ritornato **una sola volta**: in DB ne è memorizzato solo
> l'hash.

### `GET /invitations/{token}/preview`

**Pubblico** (no auth richiesta). Restituisce informazioni minime per
mostrare la pagina di accept:
- `valid: bool`,
- `organization_name`, `email`, `role_name_it`,
- `user_exists: bool`,
- `expires_at`.

Se token non trovato, `{ valid: false }`.

### `POST /invitations/{token}/accept`

**Pubblico**. Body `InvitationAcceptRequest { full_name?, password? }`.
Per nuovi utenti `full_name+password` obbligatori. Per utenti esistenti
basta accettare. Restituisce `{ status, user_id, membership_id, organization_id }`.

---

## `app/api/v1/slide_templates.py`

`router = APIRouter(prefix="/orgs/{org_id}/templates/slide",
tags=["templates"])`.

### Helper `_form_slide_template(...)`

Espone i campi di `SlideTemplateBase` come Form fields.

### `GET /orgs/{org_id}/templates/slide`

Permesso: `template:slide:manage`. Restituisce `list[SlideTemplateOut]`.

### `POST /orgs/{org_id}/templates/slide` → 201

Permesso: `template:slide:manage`. Multipart con campi base + 3 file
opzionali (`background`, `logo_left`, `logo_right`). Per ognuno fa
`save_upload_image(..., subdir="templates")` se presente.

### `GET /orgs/{org_id}/templates/slide/{template_id}`

Permesso: `template:slide:manage`.

### `PUT /orgs/{org_id}/templates/slide/{template_id}`

Permesso: `template:slide:manage`. Multipart: campi base + file +
`remove_*` flag. Logica:
- per ciascun file (`background` / `logo_left` / `logo_right`):
  - se file caricato → upload nuovo;
  - altrimenti se `remove_*` true → `None`;
  - altrimenti mantiene esistente.
- Aggiorna i campi via `setattr`.
- Cancella i vecchi file su disco se sostituiti.
- Audit `template.slide.update`.

### `DELETE /orgs/{org_id}/templates/slide/{template_id}` → 204

Permesso: `template:slide:manage`. Cancella i 3 file e la riga DB.

---

## `app/api/v1/pdf_templates.py`

Identica struttura di `slide_templates.py` ma con campi specifici PDF
(`page_size`, `header_height_mm`, `footer_height_mm`, `margin_mm`).
Permesso: `template:pdf:manage`.

---

## `app/api/v1/organization_course_settings.py`

`router = APIRouter(prefix="/orgs/{org_id}/course-settings",
tags=["organizations"])`. Tutti gli endpoint richiedono il permesso
`P.COURSE_CONFIG_MANAGE` (`require(P.COURSE_CONFIG_MANAGE)`) e l'utente
corrente come `CurrentUser`.

### `GET /orgs/{org_id}/course-settings`

Permesso: `course_config:manage`. Carica via
`organization_course_settings_service.get_or_create_settings`. Se la
riga è assente (org pre-`0007`), viene creata lazy con i default e
restituita. Risposta: `OrganizationCourseSettingsOut`.

### `PUT /orgs/{org_id}/course-settings`

Permesso: `course_config:manage`. Body
`OrganizationCourseSettingsUpdate`. Carica la riga via
`get_or_create_settings`, applica i campi via `update_settings` e
restituisce `OrganizationCourseSettingsOut`. Audit
`organization.course_settings.update` con metadata
`{"changes": <diff>}`.

---

## `app/api/v1/me_avatar.py`

`router = APIRouter(prefix="/me/avatar", tags=["me-avatar"])`. Richiede
solo l'autenticazione (no permesso RBAC): tutti gli endpoint operano
sull'utente corrente (`current_user.id`).

### `GET /me/avatar`

Restituisce `AvatarOut | null`. Chiama `avatar_service.get_avatar_for_user`.

### `PUT /me/avatar`

Multipart. Form fields:
- `image: UploadFile | None` (al primo create è obbligatorio; il
  frontend invia un JPEG quadrato 1024×1024 prodotto dopo crop 1:1).
- `audio: UploadFile | None` (al primo create è obbligatorio).
- `audio_lang: str | None`.

Chiama `avatar_service.upsert_my_avatar`. Restituisce `AvatarOut`.

> Il form non accetta più `audio_text`: il testo da leggere è gestito
> centralmente dall'admin via `avatar_voice_scripts`.

### `DELETE /me/avatar` → 204

Chiama `avatar_service.delete_avatar_for_user`.

### `POST /me/avatar/clips/regenerate` → 202

Chiama `avatar_service.regenerate_clips`. Restituisce `AvatarOut` con
`clips_status="pending"`.

### `GET /me/avatar/voice-script?lang=...`

Restituisce `AvatarVoiceScriptOut | null` con il testo che l'utente
deve leggere durante la registrazione audio. Risoluzione tramite
`avatar_config_service.get_voice_script_with_fallback` (lang richiesta →
lingua di default piattaforma → qualsiasi script disponibile → null).

### `PATCH /me/avatar/musetalk-params`

Body `AvatarMusetalkParamsUpdate { musetalk_extra_margin,
musetalk_left_cheek_width, musetalk_right_cheek_width }` (tutti
obbligatori, con i range `Field(ge=..., le=...)`). Aggiorna i tre
parametri MuseTalk dell'avatar dell'utente corrente — usati dalla scheda
"Video con Avatar" delle lezioni per il lip-sync. 404 `avatar_not_found`
se l'utente non ha ancora un avatar. Chiama
`avatar_service.update_musetalk_params`, restituisce `AvatarOut`.

---

## `app/api/v1/admin_avatar_config.py`

`router = APIRouter(prefix="/admin/avatar-config", tags=["admin-avatar-config"])`.
Tutti gli endpoint richiedono `PlatformAdmin`.

### `GET /admin/avatar-config/prompts`

Restituisce `list[AvatarClipPromptOut]` ordinata per `position`.

### `POST /admin/avatar-config/prompts` → 201

Body `AvatarClipPromptCreate { prompt, label_it, is_active? }`.
`position` assegnata automaticamente.

### `PUT /admin/avatar-config/prompts/{prompt_id}`

Body `AvatarClipPromptUpdate` (tutti opzionali).

### `DELETE /admin/avatar-config/prompts/{prompt_id}` → 204

### `PUT /admin/avatar-config/prompts/reorder`

Body `AvatarClipPromptReorder { ordered_ids: list[UUID] }`. Riassegna le
`position` in base all'ordine fornito.

### `GET /admin/avatar-config/voice-scripts`

Restituisce `list[AvatarVoiceScriptOut]` (una riga per lingua presente).

### `PUT /admin/avatar-config/voice-scripts/{language_code}`

Body `AvatarVoiceScriptUpsert { text: str }` (1..4000 char). Upsert sulla
riga di quella lingua. Restituisce `AvatarVoiceScriptOut`.

### `DELETE /admin/avatar-config/voice-scripts/{language_code}` → 204

Cancella lo script di quella lingua.

> Anche per il PDF template, il form `_form_pdf_template(...)` accetta
> ora un parametro `background_opacity_pct: int = Form(default=15,
> ge=0, le=100)` propagato a `PdfTemplate`.

---

## `app/api/v1/admin_i18n.py`

`router = APIRouter(prefix="/admin/i18n", tags=["admin-i18n"])`. Tutti
gli endpoint richiedono `PlatformAdmin`.

### Helper privato `_to_language_out(lang, untranslated=0)`

Converte una `Language` ORM in `LanguageOut` valorizzando il nuovo
campo `untranslated_count`.

### `GET /admin/i18n/languages`

Restituisce `list[LanguageOut]`. Calcola i counts via
`i18n_service.count_untranslated_per_language` (singola query) e
popola `untranslated_count` su ogni elemento.

### `GET /admin/i18n/languages/{code}`

Restituisce `LanguageOut`. Popola `untranslated_count` via
`count_untranslated_for_language`.

### `POST /admin/i18n/languages`

Crea una nuova lingua. Risposta `LanguageOut` con
`untranslated_count` calcolato.

### `PATCH /admin/i18n/languages/{code}`

Aggiorna i campi della lingua. Risposta `LanguageOut` con
`untranslated_count` aggiornato.

### `GET /admin/i18n/languages/{code}/translations`

Restituisce `{ language: LanguageOut, translations: [...] }`. Anche
qui `language.untranslated_count` è popolato.

### `POST /admin/i18n/languages/{code}/auto-translate`

**Nuovo endpoint**. Chiama `i18n_service.auto_translate_missing()` e
risponde con `AutoTranslateResponse { code, requested, translated,
skipped, errors }`. Errore `422 openai_not_configured` se
`OPENAI_API_KEY` non è configurata.

---

## `app/api/v1/courses.py` — dominio Corsi

Il router del dominio Corso (`/orgs/{org_id}/courses`) è ampio: il
dettaglio completo degli endpoint delle Fasi 1-5 e degli export PDF è
documentato nella sezione Courses. Qui sono documentati gli endpoint
delle feature recenti.

### `PATCH /{course_id}/lessons/{lesson_id}/assessment`

Permesso: `course:edit`. Body `LessonAssessmentUpdateInput`. Patch
manuale della **verifica delle competenze** (`content_raw` di una
lezione `is_assessment`). Guard `lesson.is_assessment` (altrimenti
`409 lesson_not_assessment`); richiede lezione in `ready`/`approved`.
Chiama `course_lesson_content_crud.update_lesson_assessment`,
restituisce `CourseOut`.

### Fase 6 — Generazione video MP4 (§9)

Endpoint del router courses. `generate`/`generate-batch` rispondono
`202 Accepted`; lo status è polling-friendly.

| Metodo | Path | Permesso |
|---|---|---|
| POST | `/{course_id}/lessons/{lesson_id}/video/generate` | `course:generate` |
| POST | `/{course_id}/lessons-video/generate-batch` | `course:generate` |
| POST | `/{course_id}/lessons/{lesson_id}/video/cancel` | `course:generate` |
| POST | `/{course_id}/lessons-video/cancel-batch` | `course:generate` |
| GET | `/{course_id}/lessons/{lesson_id}/video/status` | `course:view` |
| GET | `/{course_id}/lessons-video/status` | `course:view` |

`generate` / `generate-batch` validano le pre-condizioni a monte e
rispondono `409` con `code` specifico se manca un pre-requisito
(`speech_not_approved`, `slides_not_approved`, `voice_sample_missing`,
`lesson_is_assessment_not_eligible`, `video_already_in_progress`,
`no_eligible_lessons_for_video`). Body opzionale `LessonVideoGenerateInput`
(attualmente vuoto). I DTO `LessonVideoStatusOut` / `LessonVideoBatchOut`
sono costruiti da `course_lesson_video_service`.

### Fase 6b — Video con Avatar (lip-sync MuseTalk) (§9b)

| Metodo | Path | Permesso |
|---|---|---|
| POST | `/{course_id}/lessons/{lesson_id}/avatar-video/generate` | `course:generate` |
| POST | `/{course_id}/lessons-avatar-video/generate-batch` | `course:generate` |
| POST | `/{course_id}/lessons/{lesson_id}/avatar-video/cancel` | `course:generate` |
| POST | `/{course_id}/lessons-avatar-video/cancel-batch` | `course:generate` |
| GET | `/{course_id}/lessons/{lesson_id}/avatar-video/status` | `course:view` |
| GET | `/{course_id}/lessons-avatar-video/status` | `course:view` |

`generate` / `generate-batch` rispondono `202 Accepted` e validano le
pre-condizioni; `409` con `code` specifico (`lesson_video_not_ready`,
`avatar_clips_not_ready`, `lesson_is_assessment_not_eligible`,
`avatar_video_already_in_progress`, `no_eligible_lessons_for_avatar_video`).
Body opzionale `LessonAvatarVideoGenerateInput` (attualmente vuoto). I DTO
`LessonAvatarVideoStatusOut` / `LessonAvatarVideoBatchOut` sono costruiti
da `course_lesson_avatar_video_service`.

### Duplicazione corso in altra lingua

| Metodo | Path | Permesso |
|---|---|---|
| POST | `/{course_id}/duplicate?target_language_code=X` | `course:duplicate` |
| GET  | `/{course_id}/duplications` | `course:view` |
| POST | `/duplication-jobs/{job_id}/cancel` | `course:duplicate` |

`POST /duplicate` risponde `202 Accepted` con `CourseDuplicationJobOut`.
Validazioni: lingua target ≠ lingua sorgente (`409
duplicate_same_language`), lingua esistente e attiva (`404
language_not_available`), nessun job già attivo per stessa coppia (`409
duplicate_already_in_progress`, garantito anche da un unique parziale
DB). Il worker `course_duplication_worker` prende in carico, clona la
shell del corso target e traduce via OpenAI tutti i contenuti
(architecture, content, slides, speech, glossary, document summaries).
Video MP4 e Video con Avatar resettati a `empty`. Vedi
[Courses 15](../courses/15-course-duplication.md).

### Ricerca paper scientifici

Ricerca, riassunto AI e import di paper accademici dentro la tab
Documenti di un corso. Multi-source: **OpenAlex** è la sorgente primaria
(discovery + paginazione cursor-based); **Semantic Scholar** e
**Crossref** sono usate solo per l'enrichment **on-demand** (mai durante
la search di lista). Tutti gli endpoint richiedono `course:edit`, previo
`_ensure_org` + `course_service.get_course`.

| Metodo | Path | Permesso |
|---|---|---|
| POST | `/{course_id}/papers/search` | `course:edit` |
| POST | `/{course_id}/papers/ai-summary` | `course:edit` |
| POST | `/{course_id}/papers/import` | `course:edit` |

#### `POST /{course_id}/papers/search`

Request `PaperSearchInput`, response `PaperSearchResultsOut`. Ricerca
**cursor-based** via OpenAlex (`backend/app/services/openalex_search_service.py`):
`PaperSearchInput { query (max 500), filters: PaperSearchFilters,
cursor: str|None, per_page (1..50, default 20) }`; la risposta riporta
`results: list[PaperOut], next_cursor: str|None, total_count`. Errore:
`502 BAD_GATEWAY` `{code: "openalex_error", message}` su `OpenAlexError`
(`backend/app/api/v1/courses.py:692`).

#### `POST /{course_id}/papers/ai-summary`

Request `PaperAISummaryInput { paper: PaperOut }`, response
`PaperAISummaryOut`. **Sincrono, no persistenza** (l'output è usato solo
per il render FE). Se `paper.doi` è presente esegue **enrichment
on-demand** costruendo un `OpenAlexWork` sintetico dal `PaperOut` e
chiamando `paper_enrichment_service.enrich_paper` (Semantic Scholar +
Crossref in parallelo via `asyncio.gather`, non bloccante;
`backend/app/api/v1/courses.py:741`); arricchisce abstract / TL;DR /
subjects nel `paper_context`, poi aggiunge il `course_context` (titolo +
lingua del corso) per generare il riassunto **nella lingua del corso**.
`PaperAISummaryOut { short_summary, technical_summary, keywords[],
study_limitations }` (validator `_clean_keywords`: trim, troncamento a 80
char, dedup case-insensitive). Errori: `409 openai_not_configured`
(`OpenAINotConfiguredError` → `ConflictError`,
`backend/app/api/v1/courses.py:804`); `502 BAD_GATEWAY`
`{code: "openai_error", message}` su `OpenAIPaperSummaryError`
(`backend/app/api/v1/courses.py:809`).

#### `POST /{course_id}/papers/import`

Request `PaperImportInput { papers: list[PaperOut] (1..50) }`, response
`PaperImportResultOut`. Itera i paper e chiama
`paper_import_service.import_paper`, che crea un `CourseDocument` per
ciascuno: **.pdf** se il paper è OA (scarica il binario via
`openalex_client.download_pdf`), **.md** con i metadata altrimenti (con
fallback graceful a metadata se il download PDF fallisce). Il documento
nasce con `summary_status="pending"`, così la pipeline esistente
`course_document_worker` ne prende in carico extract_text + summarize AI.
La risposta conta `pdf_count` / `metadata_count` e lista gli `imported`
(`PaperImportItemResultOut { document_id, filename, mode (pdf|metadata),
paper_id }`). `db.commit()` finale (`backend/app/api/v1/courses.py:863`).

Schemi in `backend/app/schemas/paper_search.py`
(`PaperSearchInput` / `PaperSearchResultsOut`, `PaperAISummaryInput`,
`PaperImportInput` / `PaperImportResultOut`, `PaperOut`) e
`backend/app/schemas/paper_ai_summary.py` (`PaperAISummaryOut`).
Deep-dive in [Courses 16](../courses/16-paper-search.md); riepilogo
endpoint anche in [Courses 05](../courses/05-api-reference.md).

---

## `app/api/v1/admin_metrics.py` — dashboard admin

Router `/admin/metrics`, tag `admin-metrics`. Gate: `PlatformAdmin` DI
(via dependency `_: PlatformAdmin` che richiede
`me.is_platform_admin=true`).

### `GET /admin/metrics`

Permesso: `is_platform_admin`. Restituisce `AdminMetricsOut` con snapshot
platform-wide (vedi [06 — Schemas](06-schemas.md)). Cache lato service
TTL 60s (vedi [07 — Services](07-services.md) `admin_metrics_service`).

Nessun parametro di query. Risposta `200` con i blocchi
`generated_at, users, orgs, courses, lessons, cost, login_activity`.
`403 platform_admin_required` se non admin.

Chiama `admin_metrics_service.get_admin_metrics(db)`.

---

## `app/api/v1/org_metrics.py` — dashboard organizzazione

Router `/orgs`, tag `org-metrics`. Gate: `require(P.COURSE_VIEW)`
(qualsiasi membership che possa vedere i corsi dell'org). Path
`/orgs/{org_id}/metrics`.

### `GET /orgs/{org_id}/metrics`

Permesso: `course:view`. Restituisce `OrgMetricsOut` filtrato per
`organization_id`. Niente cache server-side (org-scoped, traffico già
ridotto). Il payload **non** contiene costi AI per scelta di prodotto
(vedi memoria `feedback_no_api_costs_in_org_views`).

Risposta `200`: `{generated_at, courses, lessons, members}`. `403
permission_denied` se l'utente non ha `course:view` nell'org. `403
not_a_member` se non è membro dell'org (e non è platform admin).

Chiama `org_metrics_service.compute_org_metrics(db, org_id=org_id)`.

---

## Convenzioni risposte

Tutte le risposte di errore sono nel formato:

```json
{ "code": "<machine_code>", "message": "<msg>", "request_id": "<rid>", "meta": { ... } }
```

Dove:
- `code`: stringa snake-case identificativa per gestione client.
- `message`: testo italiano user-friendly.
- `request_id`: presente sempre se il middleware è attivo, utile per
  correlare ai log.
- `meta`: opzionale, dipende dall'errore (es. `{ "missing": ["..."] }`
  per `permission_denied`).

Status code mappati:
- `400`: `ValidationAppError` (form/file).
- `401`: `AuthenticationError`, mancanza access/refresh token.
- `403`: `PermissionDeniedError`, `csrf_origin_invalid`.
- `404`: `NotFoundError`.
- `409`: `ConflictError`, `IntegrityError`.
- `422`: validazione Pydantic (body JSON).
- `429`: rate-limit, lockout.
- `500`: catch-all.
