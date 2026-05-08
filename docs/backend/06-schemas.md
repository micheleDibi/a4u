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
- `image_path: str | None`, `audio_path: str | None`,
- `audio_lang: str | None`,
- `clips_status: AvatarClipsAggregateStatus`,
- `clips: list[AvatarClipOut]` (ordinate per `position`),
- `created_at`, `updated_at`.

> Il campo `audio_text` è stato rimosso. Lo script da leggere è ora
> esposto via `AvatarVoiceScriptOut`.

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
