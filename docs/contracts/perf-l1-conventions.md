# Contratto — Convenzioni del Livello 1 prestazioni

Proprietario: lead (team-lead). Modifiche solo via richiesta `CONTRATTO:`.

## 1. Regola dell'event loop
In produzione API e 16 worker condividono **un solo** event loop (uvicorn senza `--workers`). Ogni
chiamata sincrona dentro una funzione `async` ferma tutte le request di tutti gli utenti.

- Vietato dentro `async def` (sia nei router sia nei servizi e nei worker):
  - chiamate allo storage (`remote_storage.get_storage()` → `download_bytes`, `upload_bytes`,
    `exists`, `delete`, …): con `STORAGE_BACKEND=ovh_sftp` ogni chiamata apre una connessione SSH
    nuova (p50 533 ms misurati) e i retry usano `time.sleep`;
  - PIL (`Image.open`, `exif_transpose`, `thumbnail`, `save`), pypdf (merge/append/write),
    `zipfile`, `soundfile` (`sf.read`/`sf.write`), decode audio;
  - parse markdown-it, Jinja render, misure geometriche SVG, `json.dumps`/`json.loads` di
    payload grandi (> 100 KB).
- Si spostano con `await asyncio.to_thread(fn, *args)` (nessun executor nuovo, nessuna nuova
  dipendenza). Se una funzione sincrona fa più passi bloccanti in sequenza, la si avvolge **una
  volta** al livello più alto possibile invece di avvolgere ogni passo.
- **Non** cambiare firme pubbliche se non serve: preferire un helper sincrono privato chiamato via
  `to_thread` dalla funzione async esistente. I nomi già fissati dai test (`*_sync`,
  `block_external_requests`, ecc.) restano.
- Le `AsyncSession` **non** passano mai a un thread. Gli oggetti ORM nemmeno, di norma: al thread
  si passano valori (str, bytes, dict, dataclass o uno snapshot dei campi letti). Eccezione
  ammessa solo se motivata nel `PIANO:`: una funzione sincrona esistente che legge attributi già
  caricati di un oggetto ORM (es. `pdf_template`) senza modificarli e senza toccare relazioni non
  caricate.
- Le cache in memoria (LRU delle figure, ecc.) si leggono e scrivono sul loop, non dai thread.
- Ogni spostamento ha un test che lo dimostra: storage/funzione finta lenta (es. `time.sleep(0.3)`)
  e, in parallelo sullo stesso loop, una coroutine sonda che deve completare in < 100 ms.

## 2. Log
- `event_loop_lag` (WARNING): emesso dal monitor quando il ritardo di un `asyncio.sleep(0.5)`
  supera 100 ms. Campi: `lag_ms` (int), `threshold_ms` (int).
- `event_loop_lag_summary` (INFO), ogni 60 s: `samples`, `p50_ms`, `p99_ms`, `max_ms`.
- `http_request` (esistente) guadagna il campo `response_bytes` (int | null, da `Content-Length`
  della risposta; `null` se assente, es. streaming).
- L'access log di uvicorn (`uvicorn.access`) va a WARNING: resta un solo log per request.

## 3. Vincoli
- Nessuna nuova dipendenza (Python o npm), nessuna nuova variabile d'ambiente, nessuna migrazione.
- Costanti di tuning (soglie del monitor, durate dei ticker, TTL di cache) come costanti di modulo
  con commento in italiano.
- Connessioni al DB dell'app con `jit=off` (asyncpg `server_settings`), anche nei test.
- Commenti e docstring in italiano, nomi in inglese, ruff a 100 colonne (CLAUDE.md).
- Nuove stringhe UI solo in `frontend/src/i18n/locales/it.json` e `en.json`
  (proprietario: `frontend`; `frontend-b` le chiede a `frontend`).

## 4. Porte di sviluppo
- Backend: 8000. Frontend (Vite): 5173.
- Non lasciare server in esecuzione a fine task.

## 5. Test
- Singolo file: `cd backend && env -u OPENAI_API_KEY DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python3.12 -m pytest tests/<file> -q`
  (la chiave OpenAI della shell farebbe partire chiamate reali a pagamento).
- La suite completa la lancia solo il lead, a fine lavoro.
- Frontend: `npm --prefix frontend run lint`, `run type-check`, `run build` (non c'è test runner).
