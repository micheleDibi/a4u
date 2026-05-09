# Database schema (PostgreSQL 16)

Riferimento completo dello schema. Tutte le tabelle hanno PK UUID e
naming convention SQLAlchemy (`uq_`, `fk_`, `pk_`, `ix_`, `ck_`).
Estensioni richieste: `citext`, `uuid-ossp`.

ER diagramma testuale:

```
users ─┬─< memberships >─ organizations >─ slide_templates
       │      │                ├─ pdf_templates
       │      │                ├─ organization_course_settings (1:1)
       │      │                ├─ invitations >─ organization_roles
       │      │                ├─ organization_role_permissions
       │      │                ├─ course ─< course_document
       │      │                │       ├─< course_module ─< course_lesson
       │      │                │       └── course_taxonomy_term (org-scoped)
       │      │                └─ audit_logs
       │      ├─< membership_permission_overrides >─ permissions
       │      └─ organization_roles >─ role_permissions >─ permissions
       ├─< refresh_tokens (self-FK replaced_by)
       ├─< login_attempts
       ├── avatars (1:1) ─< avatar_clips >─ avatar_clip_prompts
       └─< audit_logs (actor_user_id)

languages ─┬─< avatar_voice_scripts
           └─< course (FK language_code)
```

> `avatar_voice_scripts` è seedata con i testi che gli utenti devono
> leggere durante la registrazione audio, una riga per `language_code`
> (PK = FK su `languages.code`). Vedi più sotto.

> Le tabelle del dominio Corsi (`course`, `course_document`,
> `course_module`, `course_lesson`, `course_taxonomy_term`) sono
> documentate in [Courses 01 — Data model](../courses/01-data-model.md)
> con tutti i campi, vincoli, indici e state machine. Lo schema è
> riassunto qui in apertura del diagramma; il dettaglio per-colonna è
> centralizzato nel doc dedicato per evitare duplicazioni.

---

## `users`

Identità della piattaforma.

| Colonna | Tipo | Vincoli | Descrizione |
|---|---|---|---|
| `id` | UUID | PK | |
| `email` | CITEXT | UNIQUE NOT NULL | Case-insensitive (estensione `citext`) |
| `password_hash` | varchar(255) | NOT NULL | bcrypt |
| `full_name` | varchar(255) | NOT NULL | |
| `is_platform_admin` | boolean | NOT NULL DEFAULT false | |
| `is_active` | boolean | NOT NULL DEFAULT true | Soft-disable |
| `last_login_at` | timestamptz | nullable | Aggiornato su login OK |
| `failed_login_count` | integer | NOT NULL DEFAULT 0 | Resettato su login OK |
| `locked_until` | timestamptz | nullable | Lockout temporaneo |
| `created_at` | timestamptz | NOT NULL DEFAULT now() | |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() | onupdate |

**Indici**: `ix_users_email`, `uq_users_email` (UNIQUE).

---

## `organizations`

| Colonna | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `name` | varchar(255) | NOT NULL |
| `email` | varchar(255) | NOT NULL |
| `phone` | varchar(50) | nullable |
| `website` | varchar(255) | nullable |
| `vat_number` | varchar(64) | nullable |
| `fiscal_code` | varchar(64) | nullable |
| `country` | varchar(100) | nullable |
| `address` | varchar(255) | nullable |
| `city` | varchar(120) | nullable |
| `province` | varchar(120) | nullable |
| `postal_code` | varchar(20) | nullable |
| `logo_path` | varchar(500) | nullable |
| `created_by_user_id` | UUID | FK `users.id` ON DELETE SET NULL |
| `deleted_at` | timestamptz | nullable, INDEX (soft-delete) |
| `created_at`, `updated_at` | timestamptz | server_default + onupdate |

**Indici**: `ix_organizations_name`, `ix_organizations_deleted_at`.

---

## `organization_course_settings`

Parametri di configurazione dei corsi per organizzazione (1:1 con
`organizations`). Creata dalla migrazione `0007` e backfillata per le
org esistenti.

| Colonna | Tipo | Default / Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `organization_id` | UUID | FK `organizations.id` ON DELETE CASCADE, **UNIQUE** (1:1) |
| `modules_per_cfu` | smallint NOT NULL | default `1`, CHECK `>= 1` |
| `lessons_per_module` | smallint NOT NULL | default `8`, CHECK `>= 1` |
| `lesson_duration_minutes` | smallint NOT NULL | default `15`, CHECK `>= 1` |
| `assessment_lesson_enabled` | boolean NOT NULL | default `true` |
| `multiple_choice_questions_count` | smallint NOT NULL | default `30`, CHECK `>= 0` |
| `open_questions_count` | smallint NOT NULL | default `6`, CHECK `>= 0` |
| `created_at`, `updated_at` | timestamptz | NOT NULL DEFAULT now() |

Permission gating lato API: `course_config:manage`.

---

## `organization_roles` (lookup)

Seedata. PK UUID; `code` UNIQUE.

| Colonna | Tipo |
|---|---|
| `id` | UUID PK |
| `code` | varchar(40) UNIQUE NOT NULL |
| `name_it` | varchar(80) NOT NULL |
| `description` | varchar(255) |
| `rank` | smallint NOT NULL DEFAULT 100 |

Valori seedati: `creator(rank=10)`, `org_admin(20)`, `manager(30)`,
`member(40)`.

---

## `permissions` (lookup)

| Colonna | Tipo |
|---|---|
| `id` | UUID PK |
| `code` | varchar(80) UNIQUE NOT NULL |
| `description` | text | nullable |
| `scope` | varchar(20) NOT NULL DEFAULT 'organization' |

> La colonna `description` era originariamente `VARCHAR(255)`. È stata
> convertita a `TEXT` dalla migrazione `0008_permission_description_text`
> per accomodare descrizioni i18n articolate (>255 caratteri).

Valori seedati: codici elencati in [06 — Permissions](../06-permissions.md)
(include `course_config:manage` aggiunto dalla migrazione `0007`).

---

## `role_permissions`

Default globali per ruolo.

| Colonna | Tipo | Vincoli |
|---|---|---|
| `role_id` | UUID | FK `organization_roles.id` ON DELETE CASCADE |
| `permission_id` | UUID | FK `permissions.id` ON DELETE CASCADE |

PK composta `(role_id, permission_id)`.

---

## `organization_role_permissions`

Override per organizzazione.

| Colonna | Tipo | Vincoli |
|---|---|---|
| `organization_id` | UUID | FK `organizations.id` CASCADE |
| `role_id` | UUID | FK `organization_roles.id` CASCADE |
| `permission_id` | UUID | FK `permissions.id` CASCADE |
| `granted` | boolean | NOT NULL DEFAULT true |

PK composta `(organization_id, role_id, permission_id)`.

---

## `memberships`

| Colonna | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `user_id` | UUID | FK `users.id` CASCADE, INDEX |
| `organization_id` | UUID | FK `organizations.id` CASCADE, INDEX |
| `role_id` | UUID | FK `organization_roles.id` RESTRICT |
| `joined_by_user_id` | UUID | FK `users.id` SET NULL |
| `joined_at` | timestamptz | NOT NULL DEFAULT now() |

**Indici**: `ix_memberships_user_id`, `ix_memberships_organization_id`,
`uq_memberships_user_organization (user_id, organization_id) UNIQUE`.

---

## `membership_permission_overrides`

Override per singolo membership.

| Colonna | Tipo | Vincoli |
|---|---|---|
| `membership_id` | UUID | FK `memberships.id` CASCADE |
| `permission_id` | UUID | FK `permissions.id` CASCADE |
| `granted` | boolean | NOT NULL DEFAULT true |

PK composta `(membership_id, permission_id)`.

---

## `invitations`

| Colonna | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `organization_id` | UUID | FK CASCADE |
| `email` | varchar(255) | NOT NULL |
| `role_id` | UUID | FK `organization_roles.id` RESTRICT |
| `token_hash` | varchar(128) | UNIQUE NOT NULL |
| `created_by_user_id` | UUID | FK `users.id` SET NULL |
| `expires_at` | timestamptz | NOT NULL |
| `accepted_at` | timestamptz | nullable |
| `revoked_at` | timestamptz | nullable |
| `created_at`, `updated_at` | timestamptz | |

**Indici**: `ix_invitations_token_hash`, `ix_invitations_org_email
(organization_id, email)`.

---

## `refresh_tokens`

| Colonna | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK (= claim `jti` del JWT) |
| `user_id` | UUID | FK `users.id` CASCADE, INDEX |
| `token_hash` | varchar(128) | UNIQUE NOT NULL, INDEX |
| `expires_at` | timestamptz | NOT NULL |
| `revoked_at` | timestamptz | nullable |
| `replaced_by_id` | UUID | FK self SET NULL (rotation chain) |
| `user_agent` | varchar(500) | nullable |
| `ip` | varchar(64) | nullable |
| `created_at`, `updated_at` | timestamptz | |

---

## `login_attempts`

| Colonna | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `email` | varchar(255) | NOT NULL |
| `ip` | varchar(64) | nullable |
| `success` | boolean | NOT NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

**Indici**: `ix_login_attempts_email_created_at`,
`ix_login_attempts_ip_created_at`.

---

## `slide_templates`

Template **unificato** per slide: avatar video + export PDF slide
(Fase 4). La migration 0022 ha unificato i template (rimosso `kind`
discriminator da `pdf_templates`, FK `course_lesson.slides_pdf_template_id`
puntata a `slide_templates`).

| Colonna | Tipo | Default |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID FK CASCADE INDEX | |
| `name` | varchar(120) NOT NULL | |
| `background_image_path` | varchar(500) | nullable |
| `logo_left_path` | varchar(500) | nullable |
| `logo_right_path` | varchar(500) | nullable |
| `text_color` | char(7) NOT NULL | `#1F1F1F` |
| `primary_color` | char(7) NOT NULL | `#1976D2` |
| `secondary_color` | char(7) NOT NULL | `#9C27B0` |
| `font_family` | varchar(120) NOT NULL | `Roboto` |
| `slide_size` | varchar(8) NOT NULL | `16:9` |
| `margin_mm` | smallint NOT NULL | `20` (CHECK 0..60, aggiunto in 0022) |
| `background_opacity_pct` | smallint NOT NULL | `15` (CHECK 0..100, aggiunto in 0022) |
| `is_default` | boolean NOT NULL | `false` |
| `created_by_user_id` | UUID FK SET NULL | |
| timestamps | | |

---

## `pdf_templates`

Template per PDF lezione testo (§7) e PDF discorso (Fase 5). Entrambi
A4 portrait single-column block-flow.

| Colonna | Tipo | Default |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID FK CASCADE INDEX | |
| `name` | varchar(120) NOT NULL | |
| `background_image_path` | varchar(500) | nullable |
| `logo_left_path` | varchar(500) | nullable |
| `logo_right_path` | varchar(500) | nullable |
| `text_color` | char(7) NOT NULL | `#1F1F1F` |
| `primary_color` | char(7) NOT NULL | `#1976D2` |
| `secondary_color` | char(7) NOT NULL | `#9C27B0` |
| `font_family` | varchar(120) NOT NULL | `Roboto` |
| `page_size` | varchar(8) NOT NULL | `A4` |
| `header_height_mm` | smallint NOT NULL | `20` |
| `footer_height_mm` | smallint NOT NULL | `15` |
| `margin_mm` | smallint NOT NULL | `20` |
| `background_opacity_pct` | smallint NOT NULL | `15` (0..100, opacità della filigrana di sfondo) |
| `is_default` | boolean NOT NULL | `false` |
| `created_by_user_id` | UUID FK SET NULL | |
| timestamps | | |

> **Nota storica**: la migration 0021 introduceva un campo `kind` (`lesson` | `slides`) come discriminatore tra template per il PDF lezione testo e per il PDF slide. La 0022 ha invertito la decisione: `kind` è stato rimosso e i template per le slide sono stati unificati con quelli dell'avatar (`slide_templates`). Da 0022 in poi, `pdf_templates` serve **solo** PDF lezione testo e PDF discorso (entrambi prosa A4 portrait), mentre `slide_templates` serve avatar e PDF slide.

---

## `avatars`

Avatar globale per utente (1:1, cross-org).

| Colonna | Tipo | Default / Vincoli |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK `users.id` CASCADE | UNIQUE NOT NULL |
| `image_path` | varchar(500) | nullable |
| `audio_path` | varchar(500) | nullable |
| `audio_lang` | varchar(8) | nullable |
| `clips_status` | varchar(16) NOT NULL | `pending` (∈ `pending`, `processing`, `ready`, `partial`, `failed`) |
| timestamps | timestamptz | |

**Indici**: `uq_avatars_user_id` (UNIQUE su `user_id`).

> Il vecchio campo `audio_text` (testo libero scritto dall'utente) è stato
> rimosso dalla migrazione `0005`. Lo script che l'utente legge durante la
> registrazione viene ora dal nuovo `avatar_voice_scripts` (vedi sotto).

---

## `avatar_voice_scripts`

Testo standardizzato che l'utente deve leggere durante la registrazione
audio dell'avatar, una riga per lingua. Seedata con IT/EN da
`db.seed._seed_avatar_voice_scripts` (testi foneticamente vari, ~25s di
lettura). Serve a ottenere un campione audio adatto al futuro voice
cloning.

| Colonna | Tipo | Vincoli |
|---|---|---|
| `language_code` | varchar(10) | PK, FK `languages.code` ON DELETE CASCADE |
| `text` | text | NOT NULL |
| timestamps | timestamptz | server_default + onupdate |

Una sola riga per lingua. La risoluzione lato API segue il fallback
"lingua richiesta → lingua di default piattaforma → qualsiasi script
disponibile → null" (vedi `avatar_config_service.get_voice_script_with_fallback`).

---

## `avatar_clip_prompts`

Configurazione admin: i prompt EN passati a MiniMax. Seedata con 5 voci.

| Colonna | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `position` | smallint | UNIQUE NOT NULL |
| `prompt` | text | NOT NULL (in inglese, passato al provider) |
| `label_it` | varchar(120) | NOT NULL (etichetta UI admin) |
| `is_active` | boolean | NOT NULL DEFAULT true |
| timestamps | timestamptz | |

---

## `avatar_clips`

Una riga per ogni clip generata (5 per avatar, una per ciascun prompt
attivo al momento della creazione). Stato gestito dal worker.

| Colonna | Tipo | Vincoli |
|---|---|---|
| `id` | UUID | PK |
| `avatar_id` | UUID | FK `avatars.id` CASCADE, INDEX |
| `prompt_id` | UUID | FK `avatar_clip_prompts.id` SET NULL, nullable |
| `position` | smallint | NOT NULL |
| `prompt_text` | text | NOT NULL (snapshot del prompt al momento del lancio) |
| `status` | varchar(16) | NOT NULL DEFAULT `pending` (∈ `pending`, `processing`, `ready`, `failed`) |
| `minimax_task_id` | varchar(128) | nullable, INDEX |
| `minimax_file_id` | varchar(128) | nullable |
| `video_path` | varchar(500) | nullable |
| `error_message` | text | nullable |
| `started_at` | timestamptz | nullable |
| `completed_at` | timestamptz | nullable |
| timestamps | timestamptz | |

**Indici**: `ix_avatar_clips_avatar_id`,
`ix_avatar_clips_avatar_id_position (avatar_id, position)`,
`ix_avatar_clips_minimax_task_id`.

---

## `audit_logs`

| Colonna | Tipo |
|---|---|
| `id` | UUID PK |
| `created_at` | timestamptz NOT NULL server_default now() |
| `actor_user_id` | UUID FK SET NULL nullable |
| `organization_id` | UUID FK SET NULL nullable |
| `action` | varchar(80) NOT NULL |
| `target_type` | varchar(80) nullable |
| `target_id` | varchar(80) nullable |
| `payload` | JSONB NOT NULL DEFAULT '{}' |
| `request_id` | varchar(64) nullable |
| `ip` | varchar(64) nullable |
| `user_agent` | varchar(500) nullable |

**Indici**:
- `ix_audit_logs_org_created (organization_id, created_at)`,
- `ix_audit_logs_actor_created (actor_user_id, created_at)`,
- `ix_audit_logs_action_created (action, created_at)`.

## Codici `audit_logs.action` usati

| Action | Significato |
|---|---|
| `auth.login.success` | login OK |
| `auth.login.failure` | login KO |
| `auth.login.locked` | account lockato dopo soglia |
| `auth.refresh.success` | refresh ruotato |
| `auth.refresh.reuse_detected` | reuse-detection scattata |
| `auth.logout` | logout |
| `organization.create` | nuova organizzazione |
| `organization.update` | aggiornamento anagrafica |
| `organization.delete` | soft delete |
| `organization.transfer_creator` | trasferimento creator |
| `organization.course_settings.update` | aggiornamento parametri configurazione corsi |
| `membership.create` | iscrizione utente |
| `membership.role_change` | cambio ruolo |
| `membership.remove` | rimozione membership |
| `permission.role_defaults.update` | default globali modificati |
| `permission.org_role.update` | override per ruolo+org |
| `permission.membership.update` | override per membership |
| `invitation.create` | invito creato |
| `invitation.accept` | invito accettato |
| `template.slide.create/update/delete` | CRUD slide template |
| `template.pdf.create/update/delete` | CRUD PDF template |
| `avatar.create/update/delete` | CRUD avatar utente |
| `avatar.clips.regenerate` | Re-lancio generazione clip avatar |
| `avatar_config.prompt.create/update/delete/reorder` | CRUD prompt config admin |
| `avatar.config.voice_script.upsert/delete` | CRUD voice script per lingua |
| `i18n.translations.auto_translate` | Completamento via OpenAI delle traduzioni mancanti per una lingua |
