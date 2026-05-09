# Backend 05 — `app/models/`

Modelli SQLAlchemy 2 (ORM dichiarativo con `Mapped[T]`). Tutti ereditano
da `Base` e (la maggior parte) da `UUIDPKMixin` + `TimestampMixin` di
`app/db/base.py`.

## `app/models/__init__.py`

Re-export centralizzato: importa tutti i modelli e li espone in `__all__`.
Serve a:
- registrare il `metadata` con tutte le tabelle prima dell'autogenerate
  Alembic;
- consentire `from app.models import *  # noqa: F401,F403` in `alembic/env.py`.

`__all__`: `AuditLog, Avatar, AvatarClip, AvatarClipPrompt,
AvatarVoiceScript, Course, CourseDocument, CourseLesson, CourseModule,
CourseTaxonomyTerm, Invitation, Language, LoginAttempt, Membership,
MembershipPermissionOverride, Organization, OrganizationCourseSettings,
OrganizationRole, OrganizationRolePermission, PdfTemplate, Permission,
RefreshToken, RolePermission, SlideTemplate, User`.

---

## `User` — `app/models/user.py`

Tabella: `users`.

| Campo | Tipo | Vincoli | Note |
|---|---|---|---|
| `id` | UUID | PK | da `UUIDPKMixin` |
| `email` | CITEXT | UNIQUE, NOT NULL, INDEX | case-insensitive (estensione `citext`) |
| `password_hash` | str(255) | NOT NULL | bcrypt |
| `full_name` | str(255) | NOT NULL | |
| `is_platform_admin` | bool | NOT NULL, default `false` | |
| `is_active` | bool | NOT NULL, default `true` | |
| `last_login_at` | datetime tz | nullable | aggiornato su login OK |
| `failed_login_count` | int | NOT NULL, default 0 | reset su login OK |
| `locked_until` | datetime tz | nullable | scadenza lockout |
| `created_at`, `updated_at` | datetime tz | NOT NULL, server_default | da `TimestampMixin` |

---

## `Organization` — `app/models/organization.py`

Tabella: `organizations`.

| Campo | Tipo | Vincoli | Note |
|---|---|---|---|
| `id` | UUID | PK | |
| `name` | str(255) | NOT NULL, INDEX | denominazione |
| `email` | str(255) | NOT NULL | obbligatoria per spec |
| `phone`, `website`, `vat_number`, `fiscal_code`, `country`, `address`, `city`, `province`, `postal_code` | str | nullable | anagrafica completa |
| `logo_path` | str(500) | nullable | path tipo `/uploads/organizations/<uuid>.jpg` |
| `created_by_user_id` | UUID | FK `users.id` ON DELETE SET NULL | |
| `deleted_at` | datetime tz | nullable, INDEX | soft-delete |
| timestamps | | | |

Le query di list filtrano `deleted_at IS NULL`.

Relationship:

```python
course_settings: Mapped["OrganizationCourseSettings"] = relationship(
    back_populates="organization",
    uselist=False,
    cascade="all, delete-orphan",
)
```

(1:1 con `OrganizationCourseSettings`; cascade pulisce la riga settings
quando l'org è hard-deleted.)

---

## `OrganizationCourseSettings` — `app/models/organization_course_settings.py`

Tabella: `organization_course_settings`. Parametri di configurazione dei
corsi a livello di organizzazione (1:1 con `Organization`). Permission
gating: `course_config:manage`.

| Campo | Tipo | Vincoli | Note |
|---|---|---|---|
| `id` | UUID | PK | da `UUIDPKMixin` |
| `organization_id` | UUID | FK `organizations.id` ON DELETE CASCADE, **UNIQUE** | 1:1 |
| `modules_per_cfu` | smallint | NOT NULL, CHECK `>= 1` | default 1 |
| `lessons_per_module` | smallint | NOT NULL, CHECK `>= 1` | default 8 |
| `lesson_duration_minutes` | smallint | NOT NULL, CHECK `>= 1` | default 15 |
| `assessment_lesson_enabled` | bool | NOT NULL | default `true` |
| `multiple_choice_questions_count` | smallint | NOT NULL, CHECK `>= 0` | default 30 |
| `open_questions_count` | smallint | NOT NULL, CHECK `>= 0` | default 6 |
| `created_at`, `updated_at` | datetime tz | NOT NULL, server_default | da `TimestampMixin` |

Relationship `organization` back-populated a
`Organization.course_settings`.

> Tutte le organizzazioni esistenti al momento della migrazione `0007`
> sono backfillate con un record di settings ai default. Le nuove org
> create dopo la migrazione ricevono il record direttamente da
> `org_service.create_organization`. La logica di lettura del service
> `get_or_create_settings` resta come safety net (resilienza per org
> precedenti la migrazione).

---

## `OrganizationRole` — `app/models/role.py`

Tabella: `organization_roles`. Lookup statico, popolato dal seed.

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `code` | str(40) | UNIQUE, NOT NULL, INDEX |
| `name_it` | str(80) | NOT NULL |
| `description` | str(255) | nullable |
| `rank` | smallint | NOT NULL, default 100 |

`code` ∈ {`creator`, `org_admin`, `manager`, `member`}.

---

## `Permission` & `RolePermission` & `OrganizationRolePermission` — `app/models/permission.py`

### `permissions`

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `code` | str(80) | UNIQUE, NOT NULL, INDEX |
| `description` | Text | nullable |
| `scope` | str(20) | NOT NULL, default `organization` |

> `description` era originariamente `String(255)`. È stata convertita
> in `Text()` dalla migrazione `0008_permission_description_text` per
> accomodare descrizioni i18n articolate (>255 caratteri), come quella
> del permesso `course_config:manage`.

### `role_permissions`

PK composta `(role_id, permission_id)`. FK CASCADE su entrambe.
Memorizza i default globali per ogni ruolo.

### `organization_role_permissions`

PK composta `(organization_id, role_id, permission_id)`.

| Campo | Tipo | Vincoli |
|---|---|---|
| `organization_id` | UUID | FK `organizations.id` CASCADE |
| `role_id` | UUID | FK `organization_roles.id` CASCADE |
| `permission_id` | UUID | FK `permissions.id` CASCADE |
| `granted` | bool | NOT NULL, default `true` |

`granted=true` aggiunge un permesso al ruolo per quell'org;
`granted=false` lo rimuove (override negativo del default).

---

## `Membership` & `MembershipPermissionOverride` — `app/models/membership.py`

### `memberships`

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID | FK `users.id` CASCADE, INDEX |
| `organization_id` | UUID | FK `organizations.id` CASCADE, INDEX |
| `role_id` | UUID | FK `organization_roles.id` RESTRICT |
| `joined_by_user_id` | UUID | FK `users.id` SET NULL, nullable |
| `joined_at` | datetime tz | NOT NULL, server_default `now()` |

UNIQUE su `(user_id, organization_id)`: un utente al massimo una membership
per organizzazione.

### `membership_permission_overrides`

PK composta `(membership_id, permission_id)`. FK CASCADE.

| Campo | Tipo | Vincoli |
|---|---|---|
| `membership_id` | UUID | FK `memberships.id` CASCADE |
| `permission_id` | UUID | FK `permissions.id` CASCADE |
| `granted` | bool | NOT NULL, default `true` |

Override per singolo membership (livello più granulare).

---

## `Invitation` — `app/models/invitation.py`

Tabella: `invitations`.

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `organization_id` | UUID | FK CASCADE |
| `email` | str(255) | NOT NULL |
| `role_id` | UUID | FK RESTRICT |
| `token_hash` | str(128) | UNIQUE, NOT NULL, INDEX |
| `created_by_user_id` | UUID | FK SET NULL, nullable |
| `expires_at` | datetime tz | NOT NULL |
| `accepted_at` | datetime tz | nullable |
| `revoked_at` | datetime tz | nullable |
| timestamps | | |

Indici extra: `(token_hash)`, `(organization_id, email)`.

`token_hash` è SHA256 del token in chiaro (mai memorizzato il token).

---

## `RefreshToken` — `app/models/refresh_token.py`

Tabella: `refresh_tokens`. Memorizza i refresh attivi per rotation +
reuse-detection.

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK (corrisponde al claim `jti` del JWT) |
| `user_id` | UUID | FK `users.id` CASCADE, INDEX |
| `token_hash` | str(128) | UNIQUE, NOT NULL, INDEX |
| `expires_at` | datetime tz | NOT NULL |
| `revoked_at` | datetime tz | nullable |
| `replaced_by_id` | UUID | FK self, SET NULL, nullable |
| `user_agent` | str(500) | nullable |
| `ip` | str(64) | nullable |
| timestamps | | |

`replaced_by_id` punta al successore nella catena di rotation.

---

## `LoginAttempt` — `app/models/login_attempt.py`

Tabella: `login_attempts`. Ogni tentativo di login (success/fail) lascia
una riga.

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `email` | str(255) | NOT NULL |
| `ip` | str(64) | nullable |
| `success` | bool | NOT NULL |
| `created_at` | datetime tz | NOT NULL, server_default `now()` |

Indici: `(email, created_at)`, `(ip, created_at)` per query di analisi.

---

## `SlideTemplate` — `app/models/slide_template.py`

Tabella: `slide_templates`.

| Campo | Tipo | Default |
|---|---|---|
| `id` | UUID | |
| `organization_id` | UUID FK CASCADE INDEX | |
| `name` | str(120) NOT NULL | |
| `background_image_path` | str(500) | nullable |
| `logo_left_path` | str(500) | nullable |
| `logo_right_path` | str(500) | nullable |
| `text_color` | CHAR(7) NOT NULL | `#1F1F1F` |
| `primary_color` | CHAR(7) NOT NULL | `#1976D2` |
| `secondary_color` | CHAR(7) NOT NULL | `#9C27B0` |
| `font_family` | str(120) NOT NULL | `Roboto` |
| `slide_size` | str(8) NOT NULL | `16:9` |
| `created_by_user_id` | UUID FK SET NULL | |
| timestamps | | |

`slide_size` ∈ {`16:9`, `4:3`}.

---

## `PdfTemplate` — `app/models/pdf_template.py`

Tabella: `pdf_templates`.

Stessi campi di `SlideTemplate` (immagini, colori, font) +
specifici PDF:

| Campo | Tipo | Default |
|---|---|---|
| `page_size` | str(8) NOT NULL | `A4` (alternativa: `Letter`) |
| `header_height_mm` | smallint NOT NULL | `20` |
| `footer_height_mm` | smallint NOT NULL | `15` |
| `margin_mm` | smallint NOT NULL | `20` |
| `background_opacity_pct` | smallint NOT NULL | `15` (0..100) — opacità della filigrana di sfondo |

---

## `Avatar` — `app/models/avatar.py`

Tabella: `avatars`. Avatar globale per utente (1:1, cross-org).

| Campo | Tipo | Note |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID FK `users.id` CASCADE | UNIQUE NOT NULL (1:1 con `User`) |
| `image_path` | str(500) | nullable, frame sorgente per le clip (JPEG quadrato 1024×1024) |
| `audio_path` | str(500) | nullable, traccia audio dell'utente |
| `audio_lang` | str(8) | nullable (`it`, `en`, ...) |
| `clips_status` | str(16) NOT NULL default `pending` | aggregato (∈ `pending`, `processing`, `ready`, `partial`, `failed`) |
| timestamps | | |

Relazione `clips: list[AvatarClip]` con cascade delete.

> Il vecchio campo `audio_text` (testo libero scritto dall'utente) è stato
> rimosso: lo script da leggere durante la registrazione viene ora
> dall'admin via `AvatarVoiceScript`.

---

## `AvatarVoiceScript` — `app/models/avatar_voice_script.py`

Tabella: `avatar_voice_scripts`. Testo che l'utente deve leggere durante
la registrazione audio dell'avatar, per ottenere un campione
foneticamente vario adatto al voice cloning. Una riga per lingua.

| Campo | Tipo | Vincoli |
|---|---|---|
| `language_code` | str(10) | PK, FK `languages.code` ON DELETE CASCADE |
| `text` | text | NOT NULL |
| timestamps | | |

Seedata con IT/EN da `db.seed._seed_avatar_voice_scripts` (dict
`AVATAR_VOICE_SCRIPTS_SEED`).

---

## `AvatarClipPrompt` — `app/models/avatar_clip_prompt.py`

Tabella: `avatar_clip_prompts`. Configurazione admin: i prompt EN passati
a MiniMax. Seedata con 5 voci da `db.seed._seed_avatar_clip_prompts`.

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `position` | smallint | UNIQUE NOT NULL |
| `prompt` | text | NOT NULL (in inglese) |
| `label_it` | str(120) | NOT NULL (etichetta UI italiana) |
| `is_active` | bool | NOT NULL default `true` |
| timestamps | | |

---

## `AvatarClip` — `app/models/avatar_clip.py`

Tabella: `avatar_clips`. Una riga per ogni clip generata (5 per avatar).

| Campo | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `avatar_id` | UUID | FK `avatars.id` CASCADE, INDEX |
| `prompt_id` | UUID | FK `avatar_clip_prompts.id` SET NULL, nullable |
| `position` | smallint | NOT NULL |
| `prompt_text` | text | NOT NULL — **snapshot** del prompt al momento del lancio (resta valido se il prompt admin viene poi modificato) |
| `status` | str(16) | NOT NULL default `pending` (∈ `pending`, `processing`, `ready`, `failed`) |
| `minimax_task_id` | str(128) | nullable, INDEX |
| `minimax_file_id` | str(128) | nullable |
| `video_path` | str(500) | nullable, path al `.mp4` finale |
| `error_message` | text | nullable |
| `started_at` | datetime tz | nullable |
| `completed_at` | datetime tz | nullable |
| timestamps | | |

Indici extra: `(avatar_id, position)`, `minimax_task_id`.

---

## `AuditLog` — `app/models/audit_log.py`

Tabella: `audit_logs`. Append-only per convenzione applicativa.

| Campo | Tipo | Note |
|---|---|---|
| `id` | UUID | |
| `created_at` | datetime tz NOT NULL server_default `now()` | |
| `actor_user_id` | UUID FK `users.id` SET NULL, nullable | |
| `organization_id` | UUID FK `organizations.id` SET NULL, nullable | |
| `action` | str(80) NOT NULL | es. `auth.login.success`, `organization.create` |
| `target_type` | str(80) | nullable |
| `target_id` | str(80) | nullable, può essere UUID stringificato o codice |
| `payload` | JSONB NOT NULL default `{}` | dettagli azione |
| `request_id` | str(64) | popolato dal middleware |
| `ip` | str(64) | |
| `user_agent` | str(500) | |

Indici: `(organization_id, created_at)`, `(actor_user_id, created_at)`,
`(action, created_at)`. Query tipiche: ultimi N eventi per org/utente/azione.

---

## Modelli del dominio Corsi

I modelli `Course`, `CourseDocument`, `CourseModule`, `CourseLesson`,
`CourseTaxonomyTerm` e `Language` sono documentati separatamente in
[Courses 01 — Data model](../courses/01-data-model.md): il dettaglio di
colonne, vincoli, indici, relazioni e state machine è troppo articolato
per replicarlo qui.

In sintesi:

- `Course` ha uno state machine a 17 valori che copre l'intera pipeline AI a 5 fasi: `draft → architecture_* → lessons_structure_* → content_* → slides_* → speech_* → published / archived`. Ogni `*_pending/_ready/_approved` è derivato dagli stati per-modulo (Fase 2) o per-lezione (Fasi 3-5).
- `CourseDocument` ha il proprio state machine `pending → processing → ready/failed` per il pre-processing AI (Appendice A).
- `CourseModule` ha 10 colonne `lessons_structure_*` per Fase 2 + `architecture_modified_at` per stale-detection.
- `CourseLesson` è la tabella più ampia: oltre ai campi base e Fase 2 (`learning_objectives`, `mandatory_topics`, `prerequisites`, `section_outline`), ha 4 blocchi di colonne per le pipeline AI/PDF:
  - **Fase 3 — Contenuto** (11 colonne `content_*` + `content_modified_at`, migration 0015)
  - **§7 — PDF lezione testo** (8 colonne `pdf_*`, migration 0016, FK `pdf_templates`)
  - **Fase 4 — Slide** (11 colonne `slides_*` + `slides_modified_at`, migration 0019)
  - **Fase 4 — PDF slide** (8 colonne `slides_pdf_*`, migration 0020, FK `slide_templates` dopo unification 0022)
  - **Fase 5 — Discorso** (11 colonne `speech_*` + `speech_modified_at`, migration 0023)
  - **Fase 5 — PDF discorso** (8 colonne `speech_pdf_*`, migration 0024, FK `pdf_templates`)
  
  Costanti tuple esposte dal modello: `LESSON_CONTENT_STATUSES`, `LESSON_PDF_STATUSES`, `LESSON_SLIDES_STATUSES`, `LESSON_SPEECH_STATUSES` (`empty | pending | processing | ready | approved | failed` per quelli con `approved`; `empty | pending | processing | ready | failed` per i PDF).
- `CourseModule` e `CourseLesson` hanno UNIQUE su codici `M{N}`/`M{N}.L{K}` scoped al corso (vedi nota sul renumber in [Manual editing](../courses/04-manual-editing.md)).
- `CourseTaxonomyTerm` modella i 8 tassonomi (categoria, stile insegnamento, profondità contenuto, ruolo docente, dimensione pubblico, livello conoscenza, destinatari, livello EQF) con righe seedate e custom per organizzazione.
- `Language` è una piccola lookup table (`code`, `name_native`, `name_en`).
- `SlideTemplate` (modello a parte) è il template **unificato** per avatar video + export PDF slide (Fase 4) — vedi [migration 0022](10-alembic.md). Aggiunge `margin_mm` + `background_opacity_pct` rispetto alla forma originale.

Tutti i `*_modified_at` sono settati **solo dai CRUD manuali**, mai dai worker AI: la cascata di stale-detection (vedi `frontend/src/lib/staleness.ts`) si basa su questa proprietà per non auto-segnalare le rigenerazioni AI come stale.
