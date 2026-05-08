# Backend 11 — `tests/`

Test pytest async. Usano `httpx.AsyncClient` con `ASGITransport(app)` e una
sessione SQLAlchemy isolata per fixture.

---

## `tests/__init__.py`

Vuoto.

---

## `tests/conftest.py`

**Scopo**: definire fixture comuni e impostare le env vars di test.

### Setup env

```python
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("JWT_SECRET", "test-secret-with-at-least-32-bytes-padding-here")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://a4u:a4u_dev_password@localhost:5432/a4u_test",
)
```

In CI (GitHub Actions) `DATABASE_URL` viene sovrascritta dal workflow.

### Fixture

#### `event_loop` (session-scope)

Crea un event loop dedicato per la sessione di test (richiesta da
`pytest-asyncio` in modo `auto`).

#### `_engine` (session-scope, async)

Crea un engine SQLAlchemy async, abilita `citext`, droppa+ricrea tutto lo
schema all'inizio della sessione, droppa al teardown. **Riutilizzato** da
tutti i test.

#### `db` (function-scope, async)

Apre una `AsyncSession` e fa `rollback()` al termine. Usata per test che
toccano direttamente il DB senza HTTP.

#### `seeded_db` (function-scope, async)

Come `db` ma esegue `ensure_seed` prima di yieldare. Usata per i test
permission resolver che richiedono ruoli/permessi seedati.

#### `client` (function-scope, async)

Crea un `AsyncClient` collegato all'app.
- Monkeypatcha `app.db.session.async_session_factory` e `engine` con quelli
  di test.
- Esegue `ensure_seed` una volta.
- Usa `create_app()` (factory) e fa `app.dependency_overrides[get_db]`
  con una sessione di test che fa commit/rollback corretto.
- Imposta `headers={"Origin": "http://localhost:5173"}` (per il CSRF
  middleware).
- `base_url="http://testserver"` (placeholder ASGI).

#### `random_email`

Stringa email randomica per ciascun test che ne ha bisogno
(`f"user-{uuid.uuid4().hex[:8]}@a4u.local"`).

---

## `tests/test_health.py`

Smoke test minimi:

- `test_health(client)`: `GET /api/v1/system/health` ritorna 200 e
  `status: "ok"`.
- `test_ready(client)`: `GET /api/v1/system/ready` ritorna 200 e
  `db: "ok"`.

---

## `tests/test_permissions.py`

Test del resolver di permessi.

### Helper

#### `_setup_user_membership(db, *, role_code) -> tuple[User, Organization, Membership]`

Crea utente + organizzazione + membership con il ruolo richiesto. Ritorna
i tre oggetti.

### Test

- `test_default_permissions_match_seed(seeded_db)`: setup membership con
  ruolo `manager`; verifica che `resolve_permissions` ritorni esattamente
  `ROLE_DEFAULT_PERMISSIONS[R.MANAGER]`.
- `test_org_role_override_grants_permission(seeded_db)`: aggiunge
  un override `(org, role=manager, perm=member:invite, granted=true)`;
  verifica che il permesso sia ora nel set.
- `test_membership_override_revokes_default(seeded_db)`: setup ruolo
  `org_admin`; aggiunge override `(membership, perm=template:slide:manage,
  granted=false)`; verifica che il permesso sia rimosso ma gli altri
  default restino.
- `test_platform_admin_has_all(seeded_db)`: utente platform admin riceve
  `ALL_PERMISSION_CODES` indipendentemente dall'org.

---

## `tests/test_auth_flow.py`

Test end-to-end via HTTP.

- `test_login_logout_me(client, _engine, random_email)`:
  1. Inserisce un utente `is_platform_admin=true` direttamente nel DB.
  2. `POST /auth/login` → 200, cookie `access_token` settato.
  3. `GET /auth/me` → 200, body coerente, `is_platform_admin=true`.
  4. `POST /auth/logout` → 200.
  5. `GET /auth/me` → 401 (cookie cancellato/refresh revocato).
- `test_login_invalid_credentials(client, random_email)`:
  - `POST /auth/login` con utente inesistente e password sbagliata → 401.

---

## Strategie di estensione

Per aggiungere test sui prossimi domini:

1. **Organizzazioni**: creare un fixture `as_admin` che logga il
   bootstrap admin via `/auth/login` e restituisce il client autenticato.
2. **Memberships/inviti**: usare `enroll_user` o invitations API; verificare
   che gli audit log siano scritti (`SELECT * FROM audit_logs ...`).
3. **Template**: testare upload con `httpx.AsyncClient.post(..., files=
   {"background": (filename, payload, "image/png")})`.

> I test sono lievi per scelta in questa iterazione. La copertura più
> ampia (rotation refresh, lockout, transfer creator, soft delete) è da
> aggiungere quando il modello dei corsi e i comportamenti di dominio
> saranno definiti.
