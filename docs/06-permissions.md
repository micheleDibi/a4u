# 06 — Permission model

Modello RBAC con override a 2 livelli. Codice di riferimento:
`backend/app/core/permissions.py` (mirror in `frontend/src/lib/permissions.ts`).

## Codici permessi

Definiti come costanti nella classe `P`. Tutti i codici sono `scope=organization`
in questa iterazione (i codici platform-only sono enforced via `is_platform_admin`).

| Codice | Significato |
|---|---|
| `member:view` | Visualizzare i membri dell'org |
| `member:invite` | Creare un invito via email/token |
| `member:assign_role` | Cambiare ruolo a un membro (con vincoli di rank) |
| `member:remove` | Rimuovere un membro dall'org |
| `template:slide:manage` | CRUD template slide |
| `template:pdf:manage` | CRUD template PDF |
| `permission:manage` | Modificare gli override permessi (ruolo+org, membership) |
| `org:transfer_creator` | Trasferire il ruolo `creator` a un altro membro |
| `org:update` | Modificare i dati anagrafici dell'org |
| `course_config:manage` | Gestire i parametri di configurazione dei corsi (moduli per CFU, lezioni, durata, verifica finale) a livello di organizzazione |
| `course:view` | Visualizzare i corsi dell'org. I `member` vedono solo quelli assegnati. |
| `course:create` | Creare un nuovo corso |
| `course:edit` | Modificare metadati corso, documenti, moduli, lezioni (CRUD manuale) |
| `course:delete` | Eliminare un corso (cascade su documenti, moduli, lezioni) |
| `course:assign` | Cambiare il `assignee_user_id` di un corso |
| `course:generate` | Triggerare la pipeline AI (architettura, lezioni del modulo, fasi successive) e approvare l'architettura |

> **Avatar**: la gestione dell'avatar **non passa** dal modello RBAC. Ogni
> utente può creare/modificare/eliminare il **proprio** avatar
> (auth-based: gli endpoint `/me/avatar` richiedono solo l'autenticazione e
> operano sull'utente corrente). La configurazione globale dei prompt usati
> per generare le clip è invece riservata al platform admin
> (`requirePlatformAdmin` sui router `/admin/avatar-config/*`). Non esiste
> più un permesso `avatar:manage` nel sistema.

## Ruoli interni

Definiti in `R` (codice) e seedati in `organization_roles`. Ogni ruolo ha un
`rank` SMALLINT: più basso = più potente (creator < org_admin < manager < member).

| Code | rank | Default permissions |
|---|---|---|
| `creator` | 10 | **TUTTI** i codici di `ALL_PERMISSION_CODES` |
| `org_admin` | 20 | `member:view`, `member:invite`, `member:assign_role`, `member:remove`, `template:slide:manage`, `template:pdf:manage`, `org:update`, `course_config:manage`, `course:view`, `course:create`, `course:edit`, `course:delete`, `course:assign`, `course:generate` |
| `manager` | 30 | `member:view`, `course:view` |
| `member` | 40 | `course:view` (filtrato server-side: solo corsi assegnati) |

I default sono caricati in `role_permissions` da `seed.ensure_seed` la prima
volta. L'admin di piattaforma può modificarli via
`PUT /admin/permissions/role-defaults`.

## Tre tabelle di policy

```
role_permissions (role_id, permission_id)                                  ← default globali
organization_role_permissions (org_id, role_id, permission_id, granted)    ← override per org
membership_permission_overrides (membership_id, permission_id, granted)    ← override per persona
```

`granted` è un boolean: `true` aggiunge il permesso, `false` lo rimuove
rispetto al livello precedente.

## Algoritmo di risoluzione

Funzione: `resolve_permissions(db, user, organization_id) -> set[str]`.

1. Se `user.is_platform_admin` → restituisce `set(ALL_PERMISSION_CODES)`.
2. Carica `Membership` per `(user.id, org_id)`. Se manca → 403 `not_a_member`.
3. `base = role_permissions[membership.role_id]` (set di codici).
4. Per ogni `(code, granted)` in `organization_role_permissions[(org_id, role_id)]`:
   - `granted=true` → `base.add(code)`
   - `granted=false` → `base.discard(code)`
5. Per ogni `(code, granted)` in `membership_permission_overrides[membership.id]`:
   - applica `add`/`discard` come sopra.
6. Restituisce `base`.

## Dependency `require(*codes)`

In FastAPI:

```python
from app.core.permissions import P, require

@router.get("/orgs/{org_id}/templates/slide", dependencies=[require(P.TEMPLATE_SLIDE_MANAGE)])
async def list_templates(org_id: UUID, ...):
    ...
```

Internamente:
1. Estrae `org_id` dalla path.
2. Risolve `permissions = resolve_permissions(...)`.
3. Verifica che TUTTI i `codes` richiesti siano presenti.
4. Se mancano: 403 `permission_denied` con `meta.missing = [...]`.

`require_membership()` è una variante che richiede solo l'appartenenza
all'organizzazione, senza permessi specifici.

## Vincoli server-side (oltre il resolver)

- **Creator immutabile su 2 permessi**: `permission_service` rifiuta
  override (sia globali sia membership) che rimuovano `permission:manage` o
  `org:transfer_creator` al ruolo `creator`. Questo evita che il creator si
  auto-bricchi.
- **Rank constraint** in `membership_service.change_role`:
  - non è permesso assegnare il ruolo `creator` (solo via `transfer-creator`);
  - se l'attore non è platform admin né creator, non può promuovere a un
    rank inferiore al proprio (= ruolo superiore);
  - non può modificare un membro con rank inferiore al proprio.
- **Single creator**: un'organizzazione ha esattamente un creator.
  - `enroll_user` rifiuta se esiste già un creator nell'org.
  - `transfer_creator` è atomico in transazione: caller→`org_admin`,
    target→`creator`.
- **No remove creator**: `remove_membership` rifiuta se il target è creator.
  Bisogna prima fare `transfer-creator`, poi rimuovere.

## Endpoint che modificano i permessi

| Endpoint | Chi può | Effetto |
|---|---|---|
| `PUT /api/v1/admin/permissions/role-defaults` | platform admin | Modifica `role_permissions` (default globali per ruolo) |
| `PUT /api/v1/orgs/{org_id}/permissions/role/{role_code}` | `permission:manage` o platform admin | Modifica `organization_role_permissions` per quel ruolo nell'org |
| `PUT /api/v1/orgs/{org_id}/members/{user_id}/permissions` | `permission:manage` o platform admin | Modifica `membership_permission_overrides` per quel singolo utente |
| `POST /api/v1/orgs/{org_id}/transfer-creator` | `org:transfer_creator` (= creator di default) | Scambio atomico creator↔org_admin |

## Frontend mirror

In `frontend/src/lib/permissions.ts`:

- `P` è un oggetto `as const` con gli stessi codici del backend.
- `ALL_PERMISSIONS: PermissionCode[]` per UI list.
- `PERMISSION_LABELS_IT` e `ROLE_LABELS_IT` per i testi UI.
- `useHasPermission(code, orgId?)` legge i permessi risolti dal `me` (caricato
  da `/auth/me` all'avvio) e ritorna boolean.
- `<PermissionGate code="...">{children}</PermissionGate>` rendering condizionale.

> Le permissions in `me.organizations[i].permissions` sono **già risolte**
> lato server: non occorre rifare la cascata in client.

## Descrizioni e i18n

Ogni codice permesso ha due livelli di stringhe nel frontend:

- `t("permissions.<code>")` — label breve (es. "Cambia ruolo").
- `t("permissionDescriptions.<code>")` — descrizione di 1-2 frasi che
  spiega cosa concede e quali vincoli si applicano. Esempio:
  `"Cambiare il ruolo di un membro esistente, ma solo fino al proprio
  livello di rank: un Org Admin non può promuovere a Creator…"`.

Lato backend le descrizioni vivono in
`app/db/seed.py::PERMISSION_DESCRIPTIONS` e sono **scritte
esplicitamente per ogni codice** (1-2 frasi). Il seed ora aggiorna anche
`Permission.description` su righe esistenti (in precedenza saltava
silenziosamente se la riga esisteva), così è sufficiente modificare il
dict per riallineare il DB al prossimo `ensure_seed`.

> La colonna `permissions.description` è stata convertita da
> `VARCHAR(255)` a `TEXT` (migrazione `0008_permission_description_text`)
> per accomodare descrizioni più articolate (>255 caratteri). La nuova
> descrizione di `course_config:manage` è il caso che ha reso necessaria
> la conversione.

Lato frontend il blocco `permissionDescriptions` in
`src/i18n/locales/it.json` contiene la stessa lista (chiavi = codici
permesso). Le pagine `PermissionsManagerPage` (admin globale) e
`MemberPermissionsPage` (override per membro) mostrano:

- come **label**: `t("permissions.<code>")`,
- come **descrizione** sotto la label: `t("permissionDescriptions.<code>")`,
- il codice raw (es. `member:view`) appare come **tooltip** sull'hover
  della label, non più scritto in piccolo sotto.

> Per consentire le chiavi i18n con `:` (codici permessi tipo
> `member:view`), `src/i18n/index.ts` inizializza i18next con
> `nsSeparator: false`. Vedi `docs/frontend/09-i18n.md`.

## Permessi del dominio Corsi

Tutti e 6 i permessi `course:*` + `course_config:manage` sono **seedati e
attivi**. Pattern di gating:

- `course:view` filtra la list/dettaglio. Per i `member`, il service
  `course_service.list_courses` aggiunge `WHERE assignee_user_id = me.id`.
- `course:edit` gating per: PATCH corso (auto-save), upload/delete documento,
  reprocess documento, CRUD manuale moduli/lezioni, reorder.
- `course:create` gating sul pulsante "Nuovo corso" e POST `/courses`.
- `course:delete` gating su DELETE corso.
- `course:assign` gating su PATCH `/courses/{id}/assignee` (separato da edit
  perché spesso lo deve poter fare un manager senza dargli edit).
- `course:generate` gating su: trigger architettura, approve, regenerate,
  generate-lessons del modulo singolo. Stato del corso oltre il permesso
  (es. il bottone "Genera" è abilitato solo se `status='draft'`/`architecture_failed`).

Vedi [Courses API reference](courses/05-api-reference.md) per la mappa
permission ↔ endpoint completa.

### Note di implementazione

- Migrazione `0009_course_permissions` aggiunge le 6 righe in `permissions`
  e i mapping default in `role_permissions` per `creator`, `org_admin`,
  `manager`, `member`.
- `frontend/src/lib/permissions.ts` espone i codici via `P.COURSE_*` e li
  include in `ALL_PERMISSIONS` per le pagine di gestione permessi.
- Le `<PermissionGate code="course:...">` controllano la visibilità dei
  pulsanti CRUD nell'editor corso.
