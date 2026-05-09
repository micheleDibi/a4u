# Backend 07 — `app/services/`

Logica di dominio. I services ricevono `AsyncSession` (dal dep `get_db`),
parametri dominio, e ritornano modelli ORM o tuple. Mai aprono sessioni
proprie. Ogni operazione mutating chiama `core.audit.write_audit`.

---

## `app/services/__init__.py`

Vuoto.

---

## `app/services/file_service.py`

**Scopo**: validare e salvare upload immagini/audio sul filesystem locale.
Ri-encoding tramite Pillow per le immagini (strip EXIF). Subdir accettano
path nidificati come stringa (es. `"avatars/<user_id>"`).

### Costanti

- `ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}`.
- `ALLOWED_EXT_BY_FORMAT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}`.
- `ALLOWED_AUDIO_MIME = {"audio/webm", "audio/ogg", "audio/mpeg",
  "audio/mp4", "audio/wav", "audio/x-wav", "audio/m4a", "audio/x-m4a",
  "audio/aac"}`.
- `log = get_logger("app.files")`.

> `subdir` è una stringa: oltre ai valori "piatti" (`organizations`,
> `templates`) accetta forme nested come `f"avatars/{user_id}"`. Il path
> finale è sempre validato da `_ensure_within`.

### Funzioni

#### `_ensure_within(root: Path, target: Path) -> None`

Solleva `ValidationAppError(code="invalid_path")` se `target` (resolved) non
è dentro `root`. Difesa contro path traversal.

#### `save_upload_image(upload, *, subdir, filename_stem=None, max_dimension=4096) -> str`

`async`. Validazione + ri-encoding + salvataggio.

1. Verifica `Content-Type` ∈ `ALLOWED_MIME_TYPES` (se presente).
2. `await upload.read()`. Verifica:
   - non vuoto → `code=empty_file`;
   - dimensione ≤ `UPLOAD_MAX_MB` → `code=file_too_large`.
3. `Image.open(BytesIO(raw))`:
   - `ImageOps.exif_transpose` rispetta orientamento e strip EXIF;
   - se formato non riconosciuto, fallback PNG (alpha) o JPEG;
   - `thumbnail` se `max(w,h) > max_dimension`;
   - per JPEG converte in `RGB` e usa `quality=85`;
   - salva in `BytesIO` con `optimize=True`.
4. Crea `target_dir = upload_root / subdir` (anche se nested,
   `mkdir(parents=True)`).
5. `filename = f"{filename_stem or uuid4().hex}{ext}"`.
6. `_ensure_within(upload_root, target_path)`.
7. `target_path.write_bytes(payload)`.
8. Log `file_saved` con subdir, filename, size.
9. Restituisce `f"/uploads/{subdir}/{filename}"` (path pubblico).

`filename_stem` è opzionale; serve quando si vuole nomenclatura
deterministica (es. `image` per l'avatar utente, dove ogni utente ha la
propria cartella e quindi non c'è collisione).

In caso di immagine non valida: `UnidentifiedImageError → ValidationAppError(code="invalid_image")`.

#### `save_upload_audio(upload, *, subdir, filename_stem=None) -> str`

`async`. Validazione e salvataggio della traccia audio dell'avatar.

1. Verifica `Content-Type` ∈ `ALLOWED_AUDIO_MIME`.
2. `await upload.read()`. Verifica non vuoto e dimensione ≤
   `AVATAR_AUDIO_MAX_MB` → altrimenti `code=file_too_large`.
3. Estensione derivata dal MIME (mai dal client).
4. Salva sotto `upload_root / subdir / filename`. `_ensure_within` come
   sopra.
5. Restituisce il path pubblico `/uploads/<subdir>/<filename>`.

Non c'è ri-encoding: il file audio è scritto raw.

#### `delete_upload(path: str | None) -> None`

`async`. Cancella un file su filesystem.
- Se `path` è None o non inizia con `/uploads/` → no-op.
- Calcola path assoluto, valida con `_ensure_within`.
- Se esiste, `unlink()`. Errori di OS sono loggati ma non rilanciati.

---

## `app/services/storage_service.py`

**Scopo**: wrapper sottile sopra il filesystem per dare un'astrazione di
storage swappable (oggi local, domani S3-compatibile). Tutti i metodi sono
sincroni o `async` semplici e non aprono sessioni DB.

### Funzioni

- `save_bytes(path: str, data: bytes) -> None`: scrive dati raw a
  `<UPLOAD_DIR>/<path>` creando le cartelle padre. Validato da
  `_ensure_within`.
- `read_bytes(path: str) -> bytes`: legge il file a `<UPLOAD_DIR>/<path>`.
- `delete(path: str) -> None`: cancella un singolo file (no-op se
  inesistente).
- `delete_directory(path: str) -> None`: rimuove ricorsivamente una
  sotto-cartella di `UPLOAD_DIR` (usato dal delete avatar per pulire
  `avatars/<user_id>/`).
- `public_url(path: str) -> str`: ritorna `f"{settings.PUBLIC_BASE_URL}{path}"`
  (path già normalizzato a `/uploads/...`). Usato per produrre l'URL
  pubblico passato a MiniMax.

---

## `app/services/minimax_service.py`

**Scopo**: client del provider MiniMax (modello `MiniMax-Hailuo-02`).
Wrapping di `httpx.AsyncClient` con header `Authorization: Bearer
<MINIMAX_API_KEY>`. Le chiamate sono `async` e non toccano DB.

### Funzioni

- `start_video_generation(*, image_url: str, prompt: str) -> str`:
  POST `{MINIMAX_BASE_URL}/v1/video_generation` con body che include
  modello, prompt, `first_frame_image=image_url`,
  `last_frame_image=image_url` (per ottenere clip in **loop** chiuso),
  `duration=MINIMAX_CLIP_DURATION`,
  `resolution=MINIMAX_CLIP_RESOLUTION`. Ritorna `task_id`.
- `query_task_status(task_id: str) -> dict`: GET sullo status del task.
  Risposta normalizzata: `{status: "Queueing"|"Processing"|"Success"|
  "Fail", file_id?, error?}`.
- `download_file(file_id: str) -> bytes`: chiama l'endpoint
  `/v1/files/retrieve` per ottenere l'URL del `.mp4` e lo scarica via
  `httpx.AsyncClient.stream`. Ritorna i bytes.

Errori HTTP/timeout sono propagati come eccezioni; il chiamante (worker)
li intercetta e marca la clip `failed` con `error_message`.

---

## `app/services/avatar_clip_worker.py`

**Scopo**: worker singleton in-process che processa le clip
`pending`/`processing`. Lanciato dal `lifespan` di `app/main.py`.

### Esporta

- `start(app: FastAPI) -> None`: crea un `asyncio.Task` e lo memorizza in
  `app.state.avatar_clip_worker_task`. Idempotente: se già attivo, no-op.
- `stop(app: FastAPI) -> None`: cancella il task e `await` il join.

### Loop principale

```python
while not stop_event.is_set():
    async with AsyncSessionLocal() as db:
        await _tick(db)
    await asyncio.sleep(MINIMAX_POLL_INTERVAL_SECONDS)
```

`_tick`:

1. Se `MINIMAX_API_KEY` è vuoto → return (clip restano `pending`).
2. Carica clip in `pending` senza `minimax_task_id`: per ognuna chiama
   `start_video_generation`, salva `minimax_task_id`, marca
   `status=processing`, `started_at=now`.
3. Carica clip in `processing` con `minimax_task_id`: chiama
   `query_task_status`. Se `Success`, scarica il file via
   `download_file`, lo scrive con `storage_service.save_bytes` sotto
   `avatars/<user_id>/clip_<position>.mp4`, marca `status=ready`,
   `completed_at`, `video_path`. Se `Fail`, marca `status=failed` con
   `error_message`.
4. Aggiorna `avatars.clips_status` aggregato (`ready` se tutte 5 ready,
   `partial` se mix ready/failed, `failed` se tutte failed,
   `processing` se almeno una in processing, altrimenti `pending`).
5. Commit. Eccezioni dentro il tick sono loggate e non interrompono il
   loop.

### Recovery al restart

Tutto lo stato è in DB: al riavvio, le clip in `processing` con
`minimax_task_id` continuano dal polling, quelle `pending` ripartono.

---

## `app/services/auth_service.py`

**Scopo**: login, refresh con rotation, revoca refresh token. Lockout/audit/
rate per fallimenti.

### Costanti

- `log = get_logger("app.auth")`.

### Helper privati

#### `_now() -> datetime`

`datetime.now(tz=UTC)`.

#### `_record_login_attempt(db, *, email, ip, success) -> None`

Inserisce una riga in `login_attempts`. Email lowercased.

### Funzioni pubbliche

#### `login(db, *, email, password, ip, user_agent) -> tuple[User, str, str]`

`async`. Esegue il login; restituisce `(user, access_token, refresh_raw)`.

1. Normalizza email lowercase.
2. Carica user con `func.lower(User.email) == email_norm` (CITEXT lo gestisce
   già, ma resta idempotente).
3. Se utente lockato (`locked_until > now`): `RateLimitedError(code=
   "account_locked")`.
4. Se utente assente, inattivo o password errata:
   - registra tentativo fallito;
   - incrementa `failed_login_count`;
   - se raggiunge `LOGIN_LOCKOUT_THRESHOLD`:
     - setta `locked_until = now + LOGIN_LOCKOUT_MINUTES`;
     - resetta `failed_login_count`;
     - audit `auth.login.locked`.
   - audit `auth.login.failure`;
   - `await db.commit()` (cruciale: deve persistere anche se la richiesta
     fallisce);
   - `AuthenticationError(code="invalid_credentials")`.
5. Reset `failed_login_count = 0`, `locked_until = None`,
   `last_login_at = now`.
6. Registra tentativo OK.
7. Crea access token + refresh token. Salva `RefreshToken(id=jti,
   token_hash=hash_secret(raw), expires_at, user_agent, ip)`.
8. Audit `auth.login.success`.
9. Restituisce.

#### `rotate_refresh(db, *, refresh_token, ip, user_agent) -> tuple[User, str, str]`

`async`. Implementa rotation + reuse-detection.

1. `decode_token(refresh_token, expected_type="refresh")`.
2. Estrae `user_id`, `jti` (UUID).
3. `db.get(RefreshToken, jti)`:
   - `None` o `user_id` mismatch → `AuthenticationError(code="token_unknown")`.
4. Verifica `token_hash == hash_secret(refresh_token)` → mismatch =
   `token_invalid`.
5. Se `revoked_at != None` → **reuse detection**:
   - revoca tutti i refresh dell'utente non ancora revocati;
   - audit `auth.refresh.reuse_detected`;
   - `AuthenticationError(code="token_reused")`.
6. Se `expires_at <= now` → `code="token_expired"`.
7. Verifica `User` ancora attivo.
8. Marca il vecchio refresh `revoked_at = now`.
9. Genera nuovo (access, refresh, expires_at).
10. Salva nuovo `RefreshToken`. Setta `replaced_by_id` sul vecchio.
11. Audit `auth.refresh.success`.
12. Restituisce.

#### `revoke_refresh_token(db, *, refresh_token, ip) -> None`

`async`. Logout. Se token mancante o invalido, no-op silenzioso. Altrimenti
marca `revoked_at = now` e audit `auth.logout`.

---

## `app/services/org_service.py`

**Scopo**: CRUD organizzazioni con soft-delete e audit.

### Funzioni

#### `list_organizations(db, *, page, page_size, q=None) -> tuple[list[Organization], int]`

`async`. Lista paginata con filtro `q` opzionale (LIKE su `name`). Filtra
`deleted_at IS NULL`. Ritorna `(items, total)`.

#### `get_organization(db, org_id) -> Organization`

`async`. Carica per id; 404 `NotFoundError(code="organization_not_found")`
se assente o soft-deleted.

#### `create_organization(db, *, payload, logo_path, actor_id) -> Organization`

`async`. Crea `Organization(**payload, logo_path, created_by_user_id)`.
Subito dopo `db.add(org)` e prima del commit, crea anche un record
`OrganizationCourseSettings(organization_id=org.id)` con i default
business (`modules_per_cfu=1`, `lessons_per_module=8`,
`lesson_duration_minutes=15`, `assessment_lesson_enabled=true`,
`multiple_choice_questions_count=30`, `open_questions_count=6`), così
ogni nuova org nasce già con la sua configurazione corsi. Audit
`organization.create` con metadata `{name, email}`.

#### `update_organization(db, *, org, payload, actor_id, new_logo_path=None) -> Organization`

`async`. Aggiorna campi anagrafica. Se `new_logo_path` è valorizzato (anche
`None` esplicito), aggiorna il logo. Audit `organization.update`.

#### `soft_delete_organization(db, *, org, actor_id) -> None`

`async`. Setta `org.deleted_at = now`. Audit `organization.delete`.

---

## `app/services/organization_course_settings_service.py`

**Scopo**: lettura/aggiornamento idempotente dei parametri di
configurazione dei corsi per una organizzazione (1:1 con `Organization`).

### Funzioni

#### `get_or_create_settings(db, organization_id: UUID) -> OrganizationCourseSettings`

`async`. Cerca la riga per `organization_id`. Se assente (org creata
prima della migrazione `0007`) la crea **lazy** con i default business
e la restituisce. Garantisce quindi che ogni org abbia sempre una
configurazione consultabile.

#### `update_settings(db, settings, payload, *, actor_id) -> OrganizationCourseSettings`

`async`. Applica i campi di `OrganizationCourseSettingsUpdate` alla
riga, esegue `db.flush()` e `db.refresh(settings)` per riallinearsi al
DB. Scrive audit `organization.course_settings.update` con metadata
`{"changes": <diff>}`.

---

## `app/services/membership_service.py`

**Scopo**: iscrizioni, change role, transfer creator. Vincoli rank/creator
unico.

### Funzioni

#### `get_role_by_code(db, code) -> OrganizationRole`

`async`. Carica per codice. 404 se assente.

#### `enroll_user(db, *, user_id, organization_id, role_code, actor_id) -> Membership`

`async`. Iscrive un utente esistente come membro.

1. Carica `User` (404 altrimenti).
2. Carica ruolo target.
3. Verifica che l'utente non sia già membro (`uq_memberships_user_organization`).
   Se sì → `ConflictError(code="already_member")`.
4. Se `role_code == creator` e l'org ha già un creator → `ConflictError(
   code="creator_exists")`. (Use case: non si crea un secondo creator
   tramite enroll.)
5. Crea `Membership(user_id, organization_id, role_id, joined_by_user_id=actor_id)`.
6. Audit `membership.create` con `metadata.role`.
7. Restituisce.

#### `change_role(db, *, membership, new_role_code, actor_user, actor_membership) -> Membership`

`async`. Cambia ruolo di un membro applicando i vincoli rank.

1. Carica nuovo ruolo. Se `creator` → `PermissionDeniedError(code=
   "creator_via_transfer")` (usare l'endpoint dedicato).
2. Se attore non è platform admin:
   - richiede `actor_membership` (404 altrimenti);
   - se attore **non** è creator:
     - `ROLE_RANK[new_role.code] >= ROLE_RANK[actor_role.code]` (cioè non
       puoi promuovere a un ruolo superiore al tuo);
     - non puoi modificare un membro con rank inferiore al tuo (= ruolo
       superiore al tuo).
3. Aggiorna `membership.role_id`. Audit `membership.role_change`.
4. Restituisce.

#### `remove_membership(db, *, membership, actor_user) -> None`

`async`. Rimuove un membro.
- Se il target è `creator` → `ConflictError(code="cannot_remove_creator")`
  (richiede transfer prima).
- `db.delete(membership)`. Audit `membership.remove`.

#### `transfer_creator(db, *, organization_id, actor_user, actor_membership, target_user_id) -> tuple[Membership, Membership]`

`async`. Atomico nella stessa sessione.

1. Carica ruoli `creator` e `org_admin`.
2. Verifica `actor_membership.role_id == creator.id` (chi chiama deve essere
   il creator dell'org). Altrimenti `PermissionDeniedError(code=
   "not_creator")`. *Eccezione*: nel router, se l'attore è platform admin
   e non è membro, il router carica come `actor_membership` la membership
   del creator corrente.
3. Verifica `target_user_id != actor_user.id` (no self-transfer).
4. Carica membership del target (404 se non membro).
5. Scambia: `actor.role_id = org_admin.id`, `target.role_id = creator.id`.
6. Audit `organization.transfer_creator`.
7. Restituisce `(actor_membership_aggiornata, target_membership_aggiornata)`.

---

## `app/services/invitation_service.py`

**Scopo**: creazione/accettazione inviti.

### Costanti

- `INVITATION_TTL_DAYS = 7`.

### Funzioni

#### `_now() -> datetime`

#### `create_invitation(db, *, organization_id, email, role_code, actor_id) -> tuple[Invitation, str]`

`async`. Crea l'invito.

1. Se `role_code == creator` → `ValidationAppError(code=
   "cannot_invite_creator")`.
2. Carica ruolo target (404 altrimenti).
3. `raw_token = generate_url_safe_token()`.
4. Crea `Invitation(token_hash=hash_secret(raw_token), expires_at=now+7gg)`.
5. Audit `invitation.create` con `email`, `role`.
6. Restituisce `(invitation, raw_token)`. Il chiamante usa `raw_token` per
   costruire `accept_url`.

#### `accept_invitation(db, *, token, full_name, password, ip, user_agent) -> tuple[User, Membership]`

`async`. Accetta un invito.

1. Carica per `token_hash = hash_secret(token)`. 404 altrimenti.
2. 409 se `accepted_at`/`revoked_at` settati o `expires_at <= now`.
3. Cerca utente con quell'email.
   - Se assente: richiede `full_name` + `password` validi (valida con
     `is_password_strong`); altrimenti `ValidationAppError`.
   - Crea `User(...)`.
4. Se l'utente è già membro dell'org → marca invitation accepted_at e
   restituisce existing.
5. Crea `Membership(role_id=invitation.role_id, joined_by_user_id=
   invitation.created_by_user_id)`.
6. Setta `invitation.accepted_at = now`.
7. Audit `invitation.accept`.
8. Restituisce `(user, membership)`.

---

## `app/services/permission_service.py`

**Scopo**: modificare default globali, override per organizzazione, override
per membership.

### Funzioni

#### `_ensure_codes_exist(db, codes) -> dict[str, UUID]`

`async`. Verifica che tutti i codici siano in `ALL_PERMISSION_CODES`. Se
no, `ValidationAppError(code="unknown_permissions",
meta={"unknown": [...]})`. Restituisce `{code: permission_id}`.

#### `update_role_default_permissions(db, *, payload: RolePermissionDefaultUpdate, actor_id) -> None`

`async`. Modifica `role_permissions` (livello globale).

1. Carica ruolo per codice. 404 altrimenti.
2. Se ruolo è `creator`, verifica che `payload.permissions` contenga
   `CREATOR_REQUIRED_PERMISSIONS`. Altrimenti `ConflictError(code=
   "creator_required_permissions")`.
3. `_ensure_codes_exist(payload.permissions)`.
4. DELETE righe di `role_permissions` per quel ruolo.
5. INSERT nuove righe per ogni `permission_id`.
6. Audit `permission.role_defaults.update`.

#### `upsert_org_role_permissions(db, *, organization_id, role_code, overrides, actor_id) -> None`

`async`. Modifica `organization_role_permissions` per l'org+ruolo.

1. Carica ruolo. 404 altrimenti.
2. Se `creator` e overrides revocano `CREATOR_REQUIRED_PERMISSIONS` →
   `ConflictError`.
3. `_ensure_codes_exist(overrides.codes)`.
4. DELETE righe esistenti per `(org, role)`.
5. INSERT nuove con `granted` come specificato.
6. Audit `permission.org_role.update`.

#### `upsert_membership_permissions(db, *, membership, overrides, actor_id) -> None`

`async`. Modifica `membership_permission_overrides` per il singolo membro.

1. Se il membership è del creator, vincolo come sopra.
2. `_ensure_codes_exist`.
3. DELETE/INSERT analoghi.
4. Audit `permission.membership.update`.

#### `get_role_default_permissions(db, *, role_code) -> list[str]`

`async`. Restituisce i codici default per il ruolo (legge `role_permissions`).

#### `list_organization_role_overrides(db, *, organization_id, role_code) -> list[PermissionOverrideEntry]`

`async`. Restituisce gli override per (org, ruolo) come lista di `(code, granted)`.

#### `list_membership_overrides(db, *, membership_id) -> list[PermissionOverrideEntry]`

`async`. Restituisce gli override per il membership.

---

## `app/services/template_service.py`

**Scopo**: list/get/create/delete template slide e PDF. L'**update** è
implementato direttamente nei router perché coinvolge multipart con
flag di rimozione (più chiaro inline). Restano qui list/get/create/delete.

### Funzioni

#### `list_slide_templates(db, organization_id) -> list[SlideTemplate]`

`async`. Ordina per nome.

#### `list_pdf_templates(db, organization_id) -> list[PdfTemplate]`

`async`. Idem.

#### `get_slide_template(db, organization_id, template_id) -> SlideTemplate`

`async`. 404 se assente o di altra org.

#### `get_pdf_template(db, organization_id, template_id) -> PdfTemplate`

`async`. Idem.

#### `create_slide_template(db, *, organization_id, payload, background_image_path, logo_left_path, logo_right_path, actor_id) -> SlideTemplate`

`async`. Crea, audit `template.slide.create` con metadata `name`.

#### `delete_slide_template(db, *, tpl, actor_id) -> None`

`async`. Cancella i 3 file (background + 2 loghi) via `delete_upload`,
poi `db.delete(tpl)`. Audit `template.slide.delete`.

#### `create_pdf_template(...)` / `delete_pdf_template(...)`

Analoghi per PDF.

---

## `app/services/avatar_service.py`

**Scopo**: gestione dell'avatar utente (1:1 con `User`). L'avatar è
globale (cross-org), quindi le funzioni operano su `user_id` e non su
`organization_id`. Carica/salva immagine + audio, avvia generazione
delle 5 clip.

### Funzioni

#### `get_avatar_for_user(db, *, user_id) -> Avatar | None`

`async`. Carica l'avatar dell'utente con eager-load delle `clips`
ordinate per `position`. Ritorna `None` se assente.

#### `upsert_my_avatar(db, *, user_id, image, audio, audio_lang, actor_id) -> Avatar`

`async`. Crea l'avatar se non esiste, altrimenti aggiorna i campi
forniti. La firma non accetta più `audio_text` (rimosso).

1. Carica avatar esistente (o ne crea uno nuovo `Avatar(user_id=...)`).
2. Se `image` è fornito: salva via `file_service.save_upload_image(
   image, subdir=f"avatars/{user_id}", filename_stem="image")`. Se
   l'immagine cambia, cancella le clip vecchie (e i loro video) e
   ricrea 5 righe `pending`.
3. Se `audio` è fornito: salva via `file_service.save_upload_audio(
   audio, subdir=f"avatars/{user_id}", filename_stem="audio")`.
   Aggiorna `audio_lang` se fornito.
4. Setta `clips_status="pending"` se sono state ricreate le clip.
5. Audit `avatar.create` o `avatar.update`.
6. Restituisce l'avatar con `clips`.

#### `regenerate_clips(db, *, user_id, actor_id) -> Avatar`

`async`. Cancella tutte le righe `avatar_clips` esistenti (e i video su
disco) e le ricrea in `pending` partendo dai prompt admin attivi
(snapshot `prompt_text`). Setta `clips_status="pending"`. Audit
`avatar.clips.regenerate`. **202**.

#### `delete_avatar_for_user(db, *, user_id, actor_id) -> None`

`async`. Carica l'avatar dell'utente. Cancella la riga (CASCADE rimuove
le clip) e poi rimuove ricorsivamente la cartella
`avatars/<user_id>/` via `storage_service.delete_directory`. Audit
`avatar.delete`.

---

## `app/services/avatar_config_service.py`

**Scopo**: CRUD + reorder dei `avatar_clip_prompts` e CRUD dei
`avatar_voice_scripts` (entrambi config admin di piattaforma).

### Funzioni — prompt clip

#### `list_prompts(db) -> list[AvatarClipPrompt]`

`async`. Ordina per `position`.

#### `create_prompt(db, *, payload, actor_id) -> AvatarClipPrompt`

`async`. Assegna `position = max(position) + 1`. Audit
`avatar_config.prompt.create`.

#### `update_prompt(db, *, prompt_id, payload, actor_id) -> AvatarClipPrompt`

`async`. 404 se non esiste. Aggiorna i campi forniti. Audit
`avatar_config.prompt.update`.

#### `delete_prompt(db, *, prompt_id, actor_id) -> None`

`async`. 404 se non esiste. Cancella la riga (FK `avatar_clips.prompt_id`
SET NULL preserva le clip storiche). Audit
`avatar_config.prompt.delete`.

#### `reorder_prompts(db, *, ordered_ids, actor_id) -> list[AvatarClipPrompt]`

`async`. Validazione: `ordered_ids` deve coincidere col set di id
esistenti. Riassegna `position` in base all'ordine. Audit
`avatar_config.prompt.reorder`.

### Funzioni — voice scripts

#### `list_voice_scripts(db) -> list[AvatarVoiceScript]`

`async`. Restituisce tutte le righe di `avatar_voice_scripts` ordinate
per `language_code`.

#### `get_voice_script(db, *, language_code) -> AvatarVoiceScript | None`

`async`. Carica per chiave esatta. Nessun fallback.

#### `get_voice_script_with_fallback(db, *, language_code: str | None) -> AvatarVoiceScript | None`

`async`. Risolve lo script da mostrare all'utente con la cascata:
1. lingua richiesta (`language_code` valorizzato),
2. lingua di default piattaforma (es. `it`),
3. qualsiasi script disponibile (prima riga in ordine alfabetico),
4. `None` se la tabella è vuota.

#### `upsert_voice_script(db, *, language_code, text, actor_id) -> AvatarVoiceScript`

`async`. Crea la riga se assente, altrimenti aggiorna il `text`. Audit
`avatar.config.voice_script.upsert`.

#### `delete_voice_script(db, *, language_code, actor_id) -> None`

`async`. 404 se assente. Cancella la riga. Audit
`avatar.config.voice_script.delete`.

---

## `app/services/openai_translate_service.py`

**Scopo**: client OpenAI per la traduzione automatica i18n. Wrapping di
`httpx.AsyncClient` con header `Authorization: Bearer <OPENAI_API_KEY>`
e `base_url` configurabile. Tutte le chiamate sono `async` e non toccano
il DB.

### Eccezioni

- `class OpenAITranslateError(Exception)` con attributi `status: int |
  None`, `message: str`, `payload: Any`. Errore base.
- `class OpenAINotConfiguredError(OpenAITranslateError)` — sollevata
  quando `OPENAI_API_KEY` è vuota.

### Helper privati

#### `_client(timeout: float = 120.0) -> httpx.AsyncClient`

Crea il client `httpx.AsyncClient` con `Authorization: Bearer ...` e
`base_url=settings.openai_base_url`. Se la key è vuota, solleva
`OpenAINotConfiguredError`.

#### `_system_prompt(source_lang, target_lang_code, target_lang_name) -> str`

Costruisce il system prompt strict con queste regole:

- preservare i placeholder i18next (`{{name}}`, `{{count}}`, ecc.) e i
  segnaposto HTML;
- mantenere identiche le **keys** del JSON, tradurre solo i **values**;
- tono UI professionale, naturale e idiomatico, non letterale;
- mantenere intatti termini tecnici (PDF, JSON, API, MiniMax, ecc.) e
  simboli;
- brand `a4u` lowercase;
- output **only** JSON object.

### Funzione pubblica

#### `translate_batch(*, items, source_lang_code, source_lang_name, target_lang_code, target_lang_name) -> dict[str, str]`

`async`. Invoca `POST /chat/completions` con `model=settings.openai_model`,
`response_format={"type": "json_object"}`, `temperature=0.2`. Skip delle
chiavi con value vuoto o non-stringa. Ritorna il dict `{key: translated}`.

---

## `app/services/i18n_service.py`

**Scopo**: gestione delle traduzioni i18n a DB e completamento via
OpenAI delle voci mancanti.

### Costanti

- `DEFAULT_LANG_CODE = "it"`.

### Helper privati

#### `_missing_or_fallback_keys(*, reference, target) -> dict[str, str]`

Restituisce solo le chiavi **mancanti o vuote** nel `target`
(rispetto al `reference`). **Non** include le chiavi il cui valore
coincide col reference: i valori identici sono spesso traduzioni
legittime — brand name, prestiti linguistici, stringhe tecniche.

#### `_chunk(items, size) -> list[dict[str, str]]`

Spezza il dict di chiavi in liste di sotto-dict di dimensione `size`
(usato per costruire i batch da inviare a OpenAI).

### Funzioni pubbliche

#### `count_untranslated_per_language(db, *, default_code='it') -> dict[str, int]`

`async`. Singola query che raggruppa in memoria per ogni lingua il
numero di chiavi mancanti o vuote rispetto al default. Usata da
`GET /admin/i18n/languages` per popolare `untranslated_count` di tutti
gli elementi della lista.

#### `count_untranslated_for_language(db, *, code, default_code='it') -> int`

`async`. Versione singola lingua, usata dagli endpoint puntuali.

#### `auto_translate_missing(db, *, language, actor_id, default_code='it', default_name='Italian') -> dict`

`async`. Pipeline completa di completamento via OpenAI:

1. Identifica le chiavi mancanti via `_missing_or_fallback_keys`.
2. Le batcha in chunk da `settings.openai_translate_batch_size`
   (default 80).
3. Per ogni batch chiama
   `openai_translate_service.translate_batch()`. Una
   `OpenAINotConfiguredError` interrompe immediatamente con
   `ValidationAppError(code="openai_not_configured")` (fail-fast: non ha
   senso ritentare i batch successivi). Altri `OpenAITranslateError`
   vengono raccolti in `errors` ma non interrompono il flusso.
4. Esegue upsert dei risultati via la `upsert_translations` esistente.
5. Audit `i18n.translations.auto_translate` con metadata
   `{requested, translated, upserted, skipped, errors[:5]}`.
6. Ritorna `{requested, translated, skipped, errors}`.

---

## Servizi del dominio Corsi

I services del dominio Corso sono documentati nella sezione dedicata
(sono ~25 file, troppo per inlinarli qui senza disorientare). Mappatura
sintetica:

### Foundation + Pre-processing (Iterazioni A-B)

| Service | Documentato in | Scopo |
|---|---|---|
| `course_service.py` | [Courses 01](../courses/01-data-model.md), [Courses 03](../courses/03-architecture-generation.md) | CRUD corso, list, dettaglio eager-loaded, upload documenti |
| `course_taxonomy_service.py` | [Courses 01](../courses/01-data-model.md) | CRUD term tassonomie + auto-create on demand |
| `course_document_worker.py` | [Courses 02](../courses/02-document-preprocessing.md) | Worker async pre-processing documenti (lifespan) |
| `document_extraction_service.py` | [Courses 02](../courses/02-document-preprocessing.md) | Estrazione testo da PDF/DOCX/DOC/RTF/TXT/MD via `asyncio.to_thread` |
| `openai_summarize_service.py` | [Courses 02](../courses/02-document-preprocessing.md) | Wrapper OpenAI summarize (Appendice A) con `response_format: json_schema` |
| `openai_client.py` | [Courses 02](../courses/02-document-preprocessing.md) | Modulo condiviso: `OpenAIError`, `OpenAINotConfiguredError`, `get_client()`, `apply_reasoning_effort()` |

### Fase 1 — Architettura

| Service | Documentato in | Scopo |
|---|---|---|
| `course_architecture_service.py` | [Courses 03](../courses/03-architecture-generation.md) | Trigger generazione architettura, approve, rigenerazione |
| `course_architecture_crud.py` | [Courses 04](../courses/04-manual-editing.md) | CRUD manuale moduli/lezioni con renumber + AI generate-lessons |
| `course_architecture_worker.py` | [Courses 03](../courses/03-architecture-generation.md) | Worker async Fase 1 architettura (lifespan + ticker progress) |
| `openai_architecture_service.py` | [Courses 03](../courses/03-architecture-generation.md) | Wrapper OpenAI architettura (Fase 1) — gpt-5.5 |
| `openai_module_lessons_service.py` | [Courses 04](../courses/04-manual-editing.md) | Wrapper OpenAI lezioni di un modulo singolo (sync, ~20-30s) |

### Fase 2 — Struttura lezioni

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_structure_service.py` | [Courses 07](../courses/07-lesson-structure.md) | Orchestrazione + materializzazione + approve |
| `course_lesson_structure_crud.py` | [Courses 07](../courses/07-lesson-structure.md) | Edit manuale dei 4 campi Fase 2 |
| `course_lesson_structure_worker.py` | [Courses 07](../courses/07-lesson-structure.md) | Worker parallelo (cap=5 default) per modulo |
| `openai_lesson_structure_service.py` | [Courses 07](../courses/07-lesson-structure.md) | Wrapper OpenAI Fase 2 con JSON schema strict |

### Fase 3 — Contenuto + Glossario

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_content_service.py` | [Courses 08](../courses/08-lesson-content.md) | Orchestrazione + 10 validazioni §6.4 + materializzazione + approve |
| `course_lesson_content_crud.py` | [Courses 08](../courses/08-lesson-content.md) | Edit manuale `content_raw` + sync ref per asset rinominati |
| `course_lesson_content_worker.py` | [Courses 08](../courses/08-lesson-content.md) | Worker parallelo (cap=3 default) per lezione + auto-trigger glossario |
| `course_glossary_service.py` | [Courses 08](../courses/08-lesson-content.md) | Glossario corso (§10.1) — sync + ensure_glossary_ready |
| `openai_lesson_content_service.py` | [Courses 08](../courses/08-lesson-content.md) | Wrapper OpenAI Fase 3 + addendum §9.3 in rigenerazione |
| `openai_glossary_service.py` | [Courses 08](../courses/08-lesson-content.md) | Wrapper OpenAI glossario (10-30 termini) |

### §7 — PDF lezione testo

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_pdf_service.py` | [Courses 09](../courses/09-pdf-export.md) | Render HTML + Playwright pre-render mermaid + WeasyPrint + materialize |
| `course_lesson_pdf_worker.py` | [Courses 09](../courses/09-pdf-export.md) | Worker parallelo (cap=2 default) + cancel-check post-render |

### Fase 4 — Slide + PDF slide

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_slides_service.py` | [Courses 10](../courses/10-lesson-slides.md) | Orchestrazione + 8 validazioni §7.4 + materializzazione + approve + reset PDF su rigenerazione |
| `course_lesson_slides_crud.py` | [Courses 10](../courses/10-lesson-slides.md) | Edit manuale `slides_raw` + validazione allentata |
| `course_lesson_slides_worker.py` | [Courses 10](../courses/10-lesson-slides.md) | Worker parallelo (cap=3 default) + auto-retry trasparente + atomic claim |
| `openai_lesson_slides_service.py` | [Courses 10](../courses/10-lesson-slides.md) | Wrapper OpenAI Fase 4 con JSON schema + REGENERATION_SUFFIX §9.4 |
| `course_lesson_slides_pdf_service.py` | [Courses 09](../courses/09-pdf-export.md) | Render PDF slide A4 portrait + slide split + Mermaid base64 + slide_template |
| `course_lesson_slides_pdf_worker.py` | [Courses 09](../courses/09-pdf-export.md) | Worker parallelo (cap=2, riusa env `course_lesson_pdf_*`) |

### Fase 5 — Discorso + PDF discorso

| Service | Documentato in | Scopo |
|---|---|---|
| `course_lesson_speech_service.py` | [Courses 11](../courses/11-lesson-speech.md) | Orchestrazione + 8 validazioni §8.5 (incl. TTS-safety) + materializzazione + approve |
| `course_lesson_speech_crud.py` | [Courses 11](../courses/11-lesson-speech.md) | Edit manuale `speech_raw` + auto-ricalcolo durata + TTS-safety |
| `course_lesson_speech_worker.py` | [Courses 11](../courses/11-lesson-speech.md) | Worker parallelo (cap=3 default) + pre-check slides ready |
| `openai_lesson_speech_service.py` | [Courses 11](../courses/11-lesson-speech.md) | Wrapper OpenAI Fase 5 + JSON schema + REGENERATION_SUFFIX §9.5 + `WORDS_PER_MINUTE` (130 IT / 150 EN) |
| `course_lesson_speech_pdf_service.py` | [Courses 09](../courses/09-pdf-export.md) | Render PDF discorso A4 portrait per-slide grouping + format_timeline cumulativa |
| `course_lesson_speech_pdf_worker.py` | [Courses 09](../courses/09-pdf-export.md) | Worker parallelo (cap=2, riusa env `course_lesson_pdf_*`) |

### Pattern condivisi (tutti i worker AI)

- **Lifecycle**: registrati in `app/main.py` lifespan; ognuno espone `start_worker()` (idempotente) + `async stop_worker()` (gracefully attende task in flight con timeout 15s).
- **Concorrenza**: `asyncio.Semaphore(N)` con N da env `COURSE_LESSON_*_MAX_CONCURRENCY`. Cap separati per ogni fase (5 struttura, 3 content/slides/speech, 2 PDF).
- **Atomic claim** (anti-double-dispatch): `_inflight: set[UUID]` + `_inflight_lock: asyncio.Lock` con claim **PRIMA** del semaforo (pattern fix `87fbf70`). Evita che task in coda dietro al semaforo vengano ri-dispatched dal tick successivo.
- **Auto-retry trasparente**: helper `_apply_failure(lesson, *, error, recoverable, auto_retry_max)`. Errori recuperabili (rate-limit OpenAI, validazione, materializzazione) tornano a `pending` finché `attempts < auto_retry_max` (default 5). La UI vede solo "in elaborazione" finché passa.
- **Cancel-check post-OpenAI**: dopo la chiamata OpenAI/render PDF, refresh dello status dal DB; se `!= 'processing'` (utente ha cancellato), scarta il risultato senza scrivere.
- **JSON schema strict**: tutte le chiamate OpenAI usano `response_format: {type: 'json_schema', json_schema: {strict: true, schema: {...}}}`. Validazione Pydantic post-call per ulteriore safety.
- **Audit log** per ogni azione mutating: `course.created`, `course.document.summary.ready`, `course.architecture.generated`, `course.lesson.content.generated`, `course.lesson.slides.generated`, `course.lesson.speech.generated`, `course.lesson.{slides,speech}_pdf.generated`, ecc.
- **Stale-detection setter**: i CRUD manuali settano `*_modified_at = now()` per la cascata staleness; i worker AI non lo toccano.
