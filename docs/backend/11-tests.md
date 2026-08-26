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

> **Nota (pytest-asyncio ≥ 1.0)**: la vecchia fixture override
> `event_loop` non esiste più — è stata rimossa. Il loop condiviso di
> sessione (necessario all'engine session-scope) si dichiara in
> `pyproject.toml`: `asyncio_default_fixture_loop_scope = "session"` +
> `asyncio_default_test_loop_scope = "session"`.

#### `_engine` (session-scope, async)

Crea un engine SQLAlchemy async, abilita `citext`, droppa+ricrea tutto lo
schema all'inizio della sessione, droppa al teardown. **Riutilizzato** da
tutti i test. Importa `app.models` come modulo (`import app.models`) per
registrare i metadata di tutti i modelli — il vecchio
`from app.models import *` era illegale a livello di funzione
(`SyntaxError`).

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
(`f"user-{uuid.uuid4().hex[:8]}@a4u-tests.it"`). Il dominio è
`@a4u-tests.it` (non più `.local`): `email-validator` 2.3 rifiuta i TLD
speciali come `.local`.

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

## `tests/course_builders.py`

Builder condiviso per i test del dominio corsi: `build_course(db, ...)`
crea org + corso + moduli + lezioni in un colpo solo, con stati
configurabili per fase (`status`, `module_status`, `content_status`,
`slides_status`, `speech_status`, `with_structure`, `with_assessment`,
…) + helper `find_lesson`. Più i builder di payload AI:
`build_document_summary` / `build_course_document` (Appendice A) e
`build_lesson_content_output` (output §6.3 minimo valido, con sezioni e
`coverage_check` parametrizzabili).

---

## `tests/test_lesson_coverage_resolver.py`

**Risoluzione dei riferimenti di contabilità §6.4** (puro, senza DB):
codici `O1`/`[O2]`/`o3` → testo canonico e indice fuori range irrisolto;
testo verbatim (retro-compatibilità); varianti tipografiche del guasto di
produzione (accento sciolto, apostrofo, NBSP, punto finale); troncamento
via contenimento e frammento troppo corto irrisolto; **anti-falso
positivo**: parafrasi di un fratello («segnale aperiodico» fra
«periodico» e «non periodico») e frammento contenuto in due obiettivi →
irrisolti; temi case/parentesi-insensitive e per titolo; dedup e ordine
delle liste; `normalize` allineata alla copia di `document_citation_guard`.

---

## `tests/test_lesson_content_objective_ids.py`

**Codici obiettivo end-to-end** (`seeded_db`): prompt utente con
`- [O1] …`; system prompt che ordina il codice, lo esenta dalla regola di
LINGUA e lo vieta nella prosa; `build_lesson_content_json_schema` che
inietta l'`enum` senza mutare la costante e non produce mai `enum` vuoto;
il **guasto di produzione riprodotto** che ora materializza `ready`;
codici risolti e mai persistiti; riferimento inventato scartato con audit
`course.lesson.content.coverage_refs_dropped`; `coverage_check` derivato
invece che confrontato; hard fail solo su obiettivo/tema realmente
scoperto (con il nome nel messaggio); lezione senza obiettivi che non va
più in loop; output perfetto persistito byte-identico; reset di
`content_attempts` su `failed`/`empty` e non su `processing`/cancel.

---

## `tests/test_course_status_model.py`

Coerenza del modello status: la tuple `COURSE_STATUSES` di
`models/course.py` è 1:1 con `COURSE_STATUS_RANK` e il CHECK
`ck_course_status_valid` accetta tutti i **22** valori (regressione del
drift 18→22 sanato).

---

## `tests/test_course_pipeline_gates.py`

Gate **per-unità** delle Fasi 2-5 (P3: rigenerazione oltre la fase,
corso terminale, gate sul SOLO modulo della lezione, struttura mancante,
generate-all che filtra; P2: modulo vergine generabile a corso avanzato,
`module_has_content`, reset di `regenerate_module_lessons`; P4/P5: gate
`approved` secco, filtri bulk, assessment escluse; monotonia dei
recompute).

---

## `tests/test_course_collateral_gates.py`

Gate collaterali: glossario (rank ≥ `architecture_approved`, `archived`
bloccato), `_ensure_editable` data-based del CRUD architettura
(editabile a `content_pending`, bloccato con generazioni in volo o
`published`), approve-all tolleranti (content e moduli),
`update_course` status (publish/archive, valori arbitrari → 409
`invalid_status_transition`, riattivazione ricalcolata) e
`normalize_course_status_from_data`.

---

## `tests/test_migration_0034_normalization.py`

**Equivalenza logica della migrazione 0034**: carica il modulo della
migrazione via `importlib` ed esegue i suoi UPDATE su corsi costruiti
con `course_builders`, verificando che il risultato coincida con
`normalize_course_status_from_data` (corso regredito → milestone
derivata; corso di sole assessment non promosso vacuamente; parziali
fermi; target di duplicazione skippati; `published`/`archived` intatti;
idempotenza).

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
