# API reference

Reference completa di tutti gli endpoint REST sotto il prefisso `/api/v1`.

Convenzioni:

- Autenticazione via cookie HttpOnly (`access_token` per la maggior parte;
  `refresh_token` solo per `/auth/refresh`).
- Errori in formato `{ code, message, request_id?, meta? }` (vedi
  [05 — Security](05-security.md) e
  [Backend 08 — API](backend/08-api.md)).
- Tutti i body JSON sono `Content-Type: application/json` salvo i casi
  multipart annotati.

## Indice

- [System](#system)
- [Auth](#auth)
- [Admin: organizations](#admin-organizations)
- [Admin: users](#admin-users)
- [Admin: permissions](#admin-permissions)
- [Memberships & permessi](#memberships)
- [Invitations](#invitations)
- [Slide templates](#slide-templates)
- [PDF templates](#pdf-templates)
- [Course settings](#course-settings)
- [**Corsi** (CRUD + pipeline AI)](courses/05-api-reference.md) — endpoint sotto `/orgs/{org_id}/courses` (lista, CRUD, documenti, architettura, moduli, lezioni, contenuti, slide, discorso, export PDF, video MP4 — Fase 6 — e "Video con avatar" — Fase 6b — più la verifica delle competenze). Documentati in file dedicato.
- [Mio avatar](#mio-avatar)
- [Admin: avatar config](#admin-avatar-config)
- [Admin: i18n](#admin-i18n)

---

## System

### `GET /system/health`

Risposta `200`: `{ "status": "ok" }`. Non tocca DB.

### `GET /system/ready`

Risposta `200`: `{ "status": "ok", "db": "ok" }` se Postgres risponde.

### `POST /system/log-client`

Body JSON arbitrario. Logga lato server. Rate-limit `60/minute`.

---

## Auth

### `POST /auth/login`

Body:
```json
{ "email": "user@example.com", "password": "Password123!" }
```

Risposta `200`: `{ "status": "ok", "user_id": "<uuid>" }`. Imposta i
cookie `access_token` (path `/`) e `refresh_token` (path
`/api/v1/auth/refresh`).

Rate-limit: 5/min/IP. Lockout: 10 fallimenti in 15 min.

Errori:
- `401 invalid_credentials`.
- `429 account_locked` se l'utente è in lockout.
- `429 rate_limited` per limite IP.

### `POST /auth/refresh`

Cookie: `refresh_token`. Risposta `200` setta nuovi cookie. Errori:
- `401 missing_refresh` se il cookie manca.
- `401 token_expired` / `token_invalid` / `token_unknown` / `token_reused`
  (in caso di reuse detection: tutti i refresh dell'utente vengono
  revocati).

Rate-limit: 30/min.

### `POST /auth/logout`

Revoca il refresh token corrente, cancella i cookie. Risposta `200`.

### `GET /auth/me`

Risposta `200`:
```json
{
  "user": { ... UserOut ... },
  "organizations": [
    { "organization_id", "organization_name", "role_code",
      "role_name_it", "permissions": ["member:view", ...] }
  ],
  "is_platform_admin": false
}
```

`401 not_authenticated` se manca/scaduto access token.

---

## Admin: organizations

> Tutti richiedono `is_platform_admin`. 403 `platform_admin_required` altrimenti.

### `GET /admin/organizations`

Query: `page` (≥1), `page_size` (1..200), `q` (filtro nome).

Risposta `200`:
```json
{ "items": [OrganizationOut, ...], "meta": {"page", "page_size", "total"} }
```

### `POST /admin/organizations` (multipart)

Form fields:
- `name` (obbligatorio), `email` (obbligatorio EmailStr),
- `phone`, `website`, `vat_number`, `fiscal_code`, `country`, `address`,
  `city`, `province`, `postal_code` (opzionali),
- `logo: file` opzionale (image/png|jpeg|webp, max `UPLOAD_MAX_MB`).

Risposta `201`: `OrganizationOut`.

### `GET /admin/organizations/{org_id}`

Risposta `200`: `OrganizationOut`. `404 organization_not_found`.

### `PUT /admin/organizations/{org_id}` (multipart)

Stessi field di create + opzionali:
- `logo: file` per sostituire.
- `remove_logo: bool` per cancellarlo.

Se nessuno dei due è valorizzato, mantiene il logo esistente.

### `DELETE /admin/organizations/{org_id}` → `204`

Soft delete (`deleted_at = now`).

---

## Admin: users

### `GET /admin/users`

Query: `page`, `page_size`, `q` (LIKE su email o full_name).
Risposta `200`: `Page<UserOut>`.

### `POST /admin/users` → `201`

Body `UserCreateAdmin { email, full_name, password, is_platform_admin? }`.
`409 email_in_use`.

### `PUT /admin/users/{user_id}`

Body `UserUpdateAdmin { full_name?, is_platform_admin?, is_active? }`.
`404 user_not_found`.

### `POST /admin/organizations/{org_id}/memberships` → `201`

Body `EnrollUserRequest { user_id, role_code }`.
Iscrive un utente esistente. `404 user_not_found` se l'utente manca.
`409 already_member` se già membro.
`409 creator_exists` se ruolo `creator` e l'org ne ha già uno.

Risposta: `MembershipOut`.

---

## Admin: permissions

### `GET /admin/permissions/permissions`

Risposta:
```json
{
  "permissions": ["member:view", "member:invite", ...],
  "roles": [{"code": "creator", "name_it": "Creatore"}, ...]
}
```

### `GET /admin/permissions/role-defaults?role_code=org_admin`

Risposta: `{ "role_code": "...", "permissions": ["..."] }`.

### `PUT /admin/permissions/role-defaults`

Body: `{ "role_code": "<code>", "permissions": ["<code>", ...] }`.

Errori:
- `404 role_not_found`.
- `400 unknown_permissions` (con `meta.unknown`).
- `409 creator_required_permissions` se si tenta di rimuovere
  `permission:manage` o `org:transfer_creator` dal `creator`.

---

## Memberships

> `org_id` come path. Prefisso completo: `/api/v1/orgs/{org_id}/...`

### `GET /orgs/{org_id}/members`

Permesso: `member:view`. Risposta: `MembershipOut[]`.

### `PUT /orgs/{org_id}/members/{user_id}/role`

Permesso: `member:assign_role`. Body: `{ "role_code": "..." }`.

Errori:
- `403 creator_via_transfer` se si prova ad assegnare `creator`.
- `403 rank_violation`.
- `404 membership_not_found`.

Restituisce `MembershipOut` aggiornato.

### `DELETE /orgs/{org_id}/members/{user_id}` → `204`

Permesso: `member:remove`. `409 cannot_remove_creator`.

### `GET /orgs/{org_id}/members/{user_id}/permissions`

Permesso: `permission:manage`. Risposta:
```json
{ "membership_id": "...", "overrides": [{"code", "granted"}] }
```

### `PUT /orgs/{org_id}/members/{user_id}/permissions`

Permesso: `permission:manage`. Body:
```json
{ "overrides": [{"code": "member:invite", "granted": true}, ...] }
```

Sostituisce gli override esistenti.

### `GET /orgs/{org_id}/permissions/role/{role_code}`

Permesso: `permission:manage`. Risposta:
```json
{ "role_code", "defaults": ["..."], "overrides": [{"code","granted"}] }
```

### `PUT /orgs/{org_id}/permissions/role/{role_code}`

Permesso: `permission:manage`. Body `{ "overrides": [...] }`.

### `POST /orgs/{org_id}/transfer-creator`

Permesso: `org:transfer_creator`. Body: `{ "target_user_id": "..." }`.

Errori:
- `403 not_creator`.
- `409 self_transfer`.
- `404 not_a_member`.

Effetto atomico: caller→`org_admin`, target→`creator`.

---

## Invitations

### `POST /orgs/{org_id}/invitations` → `201`

Permesso: `member:invite`. Body: `{ "email", "role_code" }`.

Errori:
- `400 cannot_invite_creator` (non si invita come creator: usare transfer).

Risposta:
```json
{
  "invitation": {"id","organization_id","email","role_code","expires_at","accepted_at"},
  "token": "<chiaro: salva e condividi>",
  "accept_url": "<PUBLIC_BASE_URL>/invitations/<token>"
}
```

### `GET /invitations/{token}/preview`

Pubblico. Risposta:
```json
{ "valid": true, "organization_name", "email", "role_name_it",
  "user_exists", "expires_at" }
```
oppure `{ "valid": false }`.

### `POST /invitations/{token}/accept`

Pubblico. Body `InvitationAcceptRequest { full_name?, password? }`.

Per nuovi utenti `full_name` + `password` obbligatori; password validata
(≥10, una maiuscola, una cifra). Errori:
- `404 invitation_not_found`,
- `409 invitation_used` / `invitation_revoked` / `invitation_expired`,
- `400 missing_signup_fields` / `weak_password`.

Risposta: `{ "status": "ok", "user_id", "membership_id", "organization_id" }`.

---

## Slide templates

> Permesso: `template:slide:manage`. Path: `/orgs/{org_id}/templates/slide`.

### `GET .../templates/slide`

Risposta: `SlideTemplateOut[]`.

### `POST .../templates/slide` (multipart) → `201`

Form fields:
- `name` (obbligatorio max 120),
- `text_color`, `primary_color`, `secondary_color` (`#RRGGBB`),
- `font_family`, `slide_size` (`16:9` | `4:3`),
- `background`, `logo_left`, `logo_right` file opzionali.

### `GET .../templates/slide/{id}`

Risposta: `SlideTemplateOut`. `404 template_not_found`.

### `PUT .../templates/slide/{id}` (multipart)

Form fields come create + opzionali:
- file `background` / `logo_left` / `logo_right` per sostituire.
- flag bool `remove_background` / `remove_logo_left` / `remove_logo_right`
  per cancellare.

I file vecchi su disco vengono rimossi se sostituiti.

### `DELETE .../templates/slide/{id}` → `204`

---

## PDF templates

> Permesso: `template:pdf:manage`. Path: `/orgs/{org_id}/templates/pdf`.

Stessi metodi di slide templates. Form fields specifici:

- `page_size` (`A4` | `Letter`),
- `header_height_mm` (0..80, default 20),
- `footer_height_mm` (0..80, default 15),
- `margin_mm` (0..60, default 20),
- `background_opacity_pct` (0..100, default 15) — opacità della filigrana
  di sfondo applicata in preview e in fase di rendering.

---

## Course settings

> Permesso: `course_config:manage`. Path:
> `/orgs/{org_id}/course-settings`. Relazione 1:1 con
> `organizations`: ogni org ha un solo record di settings.

### `GET /orgs/{org_id}/course-settings`

Restituisce `OrganizationCourseSettingsOut`. Se la riga è assente
(organizzazione creata prima della migrazione `0007`), viene creata
**lazy** con i default; l'endpoint è quindi idempotente.

### `PUT /orgs/{org_id}/course-settings`

Body `OrganizationCourseSettingsUpdate` (tutti i 6 campi richiesti):

```json
{
  "modules_per_cfu": 1,
  "lessons_per_module": 8,
  "lesson_duration_minutes": 15,
  "assessment_lesson_enabled": true,
  "multiple_choice_questions_count": 30,
  "open_questions_count": 6
}
```

Validazione:
- `modules_per_cfu`, `lessons_per_module`, `lesson_duration_minutes` ≥ 1.
- `multiple_choice_questions_count`, `open_questions_count` ≥ 0.

Restituisce `OrganizationCourseSettingsOut`. Audit
`organization.course_settings.update` con metadata `{changes: {...}}`.

---

## Mio avatar

> Solo autenticazione richiesta (no permesso RBAC). Tutti gli endpoint
> operano sull'utente corrente. Path: `/me/avatar`.

### `GET /me/avatar`

Risposta `200`: `AvatarOut | null`. Restituisce `null` se l'utente non ha
ancora creato un avatar.

### `PUT /me/avatar` (multipart)

Crea o aggiorna l'avatar (1:1 per utente, idempotente).

Form fields:
- `image` file (richiesto al primo create — JPEG quadrato 1024×1024
  prodotto dal frontend dopo crop 1:1, max `UPLOAD_MAX_MB`).
- `audio` file (richiesto al primo create — MIME audio whitelisted, max
  `AVATAR_AUDIO_MAX_MB`).
- `audio_lang: str` (es. `it`, `en`).

In update sono tutti opzionali; se viene caricata una nuova immagine, le 5
clip esistenti vengono cancellate e ricreate in `pending`.

> Il form non accetta più `audio_text`: il testo da leggere è gestito
> centralmente dall'admin via `avatar_voice_scripts` e mostrato in
> sola lettura all'utente durante la registrazione.

Risposta: `AvatarOut`.

### `GET /me/avatar/voice-script?lang=...`

Risposta `200`: `AvatarVoiceScriptOut | null`. Restituisce lo script da
leggere per la lingua richiesta. Se `lang` non è specificato o non ha
script, applica il fallback `lang richiesto → lingua di default
piattaforma → qualsiasi script disponibile → null`.

### `DELETE /me/avatar` → `204`

Cancella l'avatar e la relativa cartella `uploads/avatars/<user_id>/`.

### `POST /me/avatar/clips/regenerate` → `202`

Ricrea le 5 righe `avatar_clips` in `pending` (il worker le processerà).
Utile in caso di `failed`/`partial` o se sono cambiati i prompt admin.

Risposta: `AvatarOut` con `clips_status="pending"`.

### `PATCH /me/avatar/musetalk-params`

Aggiorna i tre parametri MuseTalk per-avatar usati dalla generazione del
"Video con avatar" delle lezioni (Fase 6b, lip-sync dell'avatar
sovrapposto al video — vedi
[Courses 13 — Avatar video](courses/13-avatar-video.md)).

Body `AvatarMusetalkParamsUpdate` (tutti e tre i campi obbligatori, la UI
invia sempre i valori correnti):

```json
{
  "musetalk_extra_margin": 15,
  "musetalk_left_cheek_width": 110,
  "musetalk_right_cheek_width": 110
}
```

Range: `musetalk_extra_margin` 0..200; `musetalk_left_cheek_width` e
`musetalk_right_cheek_width` 0..400.

Risposta `200`: `AvatarOut`. `404 avatar_not_found` se l'utente corrente
non ha ancora un avatar.

---

## Admin: avatar config

> Richiede `is_platform_admin`. Path: `/admin/avatar-config/...`. Gestisce
> i prompt EN passati a MiniMax al momento della generazione e gli script
> di lettura per la registrazione audio (uno per lingua).

### `GET /admin/avatar-config/prompts`

Risposta: `AvatarClipPromptOut[]` ordinata per `position`.

### `POST /admin/avatar-config/prompts` → `201`

Body JSON:
```json
{ "prompt": "subtle nod...", "label_it": "Cenno del capo", "is_active": true }
```

`position` è assegnata automaticamente come ultima.

### `PUT /admin/avatar-config/prompts/{id}`

Body JSON con campi opzionali `prompt`, `label_it`, `is_active`.

### `DELETE /admin/avatar-config/prompts/{id}` → `204`

### `PUT /admin/avatar-config/prompts/reorder`

Body JSON:
```json
{ "ordered_ids": ["<uuid>", "<uuid>", ...] }
```

Riassegna `position` in base all'ordine fornito.

### `GET /admin/avatar-config/voice-scripts`

Risposta `200`: `AvatarVoiceScriptOut[]` (una riga per lingua presente in
`avatar_voice_scripts`).

### `PUT /admin/avatar-config/voice-scripts/{language_code}`

Upsert dello script per quella lingua. `language_code` deve esistere in
`languages`.

Body JSON `AvatarVoiceScriptUpsert { text: string }` (1..4000 char).

Risposta: `AvatarVoiceScriptOut`. Audit `avatar.config.voice_script.upsert`.

### `DELETE /admin/avatar-config/voice-scripts/{language_code}` → `204`

Cancella lo script di quella lingua. Audit
`avatar.config.voice_script.delete`.

---

## Admin: i18n

> Richiede `is_platform_admin`. Path: `/admin/i18n/...`. Gestisce le
> lingue supportate e le relative traduzioni.

### `GET /admin/i18n/languages`

Restituisce `LanguageOut[]`. Ogni voce include il campo
`untranslated_count: int` (numero di chiavi mancanti o vuote rispetto
alla lingua di default).

### `GET /admin/i18n/languages/{code}`

Restituisce `LanguageOut` con `untranslated_count` popolato.

### `POST /admin/i18n/languages` / `PATCH /admin/i18n/languages/{code}`

Create/update di una lingua. La risposta è un `LanguageOut` con
`untranslated_count`.

### `GET /admin/i18n/languages/{code}/translations`

Restituisce `{ language: LanguageOut, translations: [...] }`. Anche qui
il `language` esposto include `untranslated_count`.

### `POST /admin/i18n/languages/{code}/auto-translate`

Completa via OpenAI le chiavi mancanti o vuote del target. Body vuoto.
Permission: platform admin.

Risposta `200`: `AutoTranslateResponse`:

```json
{
  "code": "<lang>",
  "requested": 120,
  "translated": 118,
  "skipped": 0,
  "errors": ["..."]
}
```

Errori:
- `422 openai_not_configured` se `OPENAI_API_KEY` non è valorizzata.
- `403 platform_admin_required` per non-admin.

Internamente: `i18n_service.auto_translate_missing()` batcha le chiavi
da `OPENAI_TRANSLATE_BATCH_SIZE` per richiesta a `/chat/completions` e
fa upsert dei risultati. Audit `i18n.translations.auto_translate` con
`{requested, translated, upserted, skipped, errors[:5]}`.

`LanguageOut` include sempre il nuovo campo `untranslated_count: int`.

---

## Tipi DTO

| Tipo | Campi |
|---|---|
| `OrganizationOut` | id, name, email, phone, website, vat_number, fiscal_code, country, address, city, province, postal_code, logo_path, created_at, updated_at |
| `UserOut` | id, email, full_name, is_platform_admin, is_active, last_login_at, created_at |
| `MembershipOut` | id, user_id, user_email, user_full_name, organization_id, role_id, role_code, role_name_it, joined_at |
| `MeOut` | user, organizations[{organization_id, organization_name, role_code, role_name_it, permissions[]}], is_platform_admin |
| `SlideTemplateOut` | id, organization_id, name, background_image_path, logo_left_path, logo_right_path, text_color, primary_color, secondary_color, font_family, slide_size, created_at, updated_at |
| `PdfTemplateOut` | + page_size, header_height_mm, footer_height_mm, margin_mm, background_opacity_pct |
| `AvatarOut` | id, user_id, audio_lang, clips_status, musetalk_extra_margin, musetalk_left_cheek_width, musetalk_right_cheek_width, image_url, audio_url, clips: AvatarClipOut[], created_at, updated_at (`image_path`/`audio_path` esclusi: esposti come `image_url`/`audio_url`) |
| `AvatarMusetalkParamsUpdate` | musetalk_extra_margin (0..200), musetalk_left_cheek_width (0..400), musetalk_right_cheek_width (0..400) |
| `AvatarClipOut` | id, avatar_id, position, prompt_text, status, video_path, error_message, started_at, completed_at, created_at, updated_at |
| `AvatarClipPromptOut` | id, position, prompt, label_it, is_active, created_at, updated_at |
| `AvatarVoiceScriptOut` | language_code, text, created_at, updated_at |
| `AvatarVoiceScriptUpsert` | text (1..4000) |
| `Page<T>` | items: T[], meta: { page, page_size, total } |
| `PermissionOverrideEntry` | code, granted (bool) |
| `OrganizationCourseSettingsOut` | id, organization_id, modules_per_cfu, lessons_per_module, lesson_duration_minutes, assessment_lesson_enabled, multiple_choice_questions_count, open_questions_count, created_at, updated_at |
| `OrganizationCourseSettingsUpdate` | modules_per_cfu (≥1), lessons_per_module (≥1), lesson_duration_minutes (≥1), assessment_lesson_enabled, multiple_choice_questions_count (≥0), open_questions_count (≥0) |
| `LanguageOut` | code, name, native_name, is_default, is_active, untranslated_count, created_at, updated_at |
| `AutoTranslateResponse` | code, requested, translated, skipped, errors[] |

## Codici errore canonici (machine-readable)

| Code | Status | Quando |
|---|---|---|
| `not_authenticated` | 401 | Manca cookie/Bearer |
| `token_expired` | 401 | JWT scaduto |
| `token_invalid` | 401 | JWT firmato male / tipo errato |
| `token_unknown` | 401 | Refresh non in DB |
| `token_reused` | 401 | Refresh già revocato → chain-revoke |
| `account_locked` | 429 | Lockout login |
| `invalid_credentials` | 401 | Password sbagliata |
| `csrf_origin_invalid` | 403 | Origin/Referer mismatch |
| `permission_denied` | 403 | Permesso mancante (`meta.missing`) |
| `platform_admin_required` | 403 | Non admin di piattaforma |
| `not_a_member` | 403 | Non membro dell'org richiesta |
| `not_creator` | 403 | Non sei creator (transfer-creator) |
| `not_found` | 404 | Risorsa generica non trovata |
| `organization_not_found` / `user_not_found` / etc. | 404 | Risorse specifiche |
| `avatar_not_found` | 404 | Operazioni `/me/avatar` (delete, regenerate clip, `PATCH .../musetalk-params`) quando l'utente non ha un avatar |
| `conflict` | 409 | IntegrityError DB |
| `already_member` | 409 | Utente già iscritto all'org |
| `creator_exists` | 409 | Org ha già un creator |
| `cannot_remove_creator` | 409 | Tentativo rimuovere il creator |
| `creator_required_permissions` | 409 | Override revoca permessi obbligatori del creator |
| `creator_via_transfer` | 403 | Cambio ruolo a creator non permesso |
| `rank_violation` | 403 | Vincolo rank (assign role) |
| `self_transfer` | 409 | Transfer creator a se stessi |
| `invitation_not_found` / `invitation_used` / `invitation_revoked` / `invitation_expired` | 404/409 | Stato invito |
| `cannot_invite_creator` | 400 | Inviti non producono creator |
| `missing_signup_fields` / `weak_password` | 400 | Accept invito |
| `validation_error` | 422 | Pydantic body |
| `invalid_mime` / `invalid_image` / `empty_file` / `file_too_large` / `invalid_path` | 400 | Upload |
| `unknown_permissions` | 400 | Codici permesso non noti (admin permissions) |
| `rate_limited` | 429 | slowapi |
| `internal_error` | 500 | catch-all (mai dettagli) |
