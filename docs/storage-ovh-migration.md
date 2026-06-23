# Storage file: migrazione su server OVH (FTP/SFTP)

Runbook per spostare la persistenza dei file (PDF dispense/slide/discorso,
video, video-avatar, upload utente) dal **filesystem locale** al **server OVH**
(`progettiersaf.com`) via FTP/FTPS oppure SFTP. I file sono poi serviti
pubblicamente via HTTP da OVH.

## Architettura

Tutto l'I/O file passa da `backend/app/services/remote_storage.py`, che astrae
il backend attivo via `settings.storage_backend`:

- `local` — filesystem locale (default, dev). Comportamento storico.
- `ovh_ftp` — server OVH via FTP/FTPS (`ftplib.FTP_TLS`, connessione
  per-operazione, MKD ricorsivo, upload atomico temp+rename, retry/backoff).
- `ovh_sftp` — server OVH via SFTP (Paramiko/SSH su porta `OVH_SFTP_PORT=22`).
  Stessa interfaccia e semantica di `ovh_ftp` (connessione per-operazione, MKD
  ricorsivo, upload atomico temp+rename, retry/backoff) ma su canale SSH
  cifrato: niente porte passive, niente riuso della sessione TLS sul canale
  dati, rename atomico via `posix_rename` (con fallback delete+rename per i
  server senza l'estensione). Più robusto sull'hosting condiviso. Riusa le
  stesse credenziali `OVH_FTP_*` (host/user/password/base path). Richiede
  `paramiko`.

Il DB **non cambia**: le colonne `*_path` continuano a contenere lo stesso path
logico opaco. Il layer mappa quel path a una *key* namespaced e la risolve sul
backend attivo:

| Contenuto | Key | URL pubblico (ovh) |
|---|---|---|
| Upload/media (`/uploads/...`, `lesson_videos/...`, `avatars/...`) | `uploads/<rel>` | `{OVH_PUBLIC_BASE_URL}/uploads/<rel>` |
| PDF generati (`{org}/{course}/{lesson}.pdf`) | `generated_pdfs/<rel>` | `{OVH_PUBLIC_BASE_URL}/generated_pdfs/<rel>` |

- **Scritture/cancellazioni** → backend attivo.
- **Letture lato server** (merge/zip PDF, estrazione documenti, embed immagini
  nei PDF, input pipeline video) → FTP RETR (deterministico).
- **Letture browser / servizi esterni** (player video, MiniMax, RunPod) →
  URL pubblico (`remote_storage.media_url` / `public_url`).

Restano **sempre locali** (cache/transitori, esclusi dalla migrazione):
`lesson_audio/` (cache TTS), `musetalk_manifests/`, `*/clips_musetalk_*/`,
work dir ffmpeg (`.tmp_work_*`, `.tmp_avatar_work_*`).

## Configurazione (`.env`)

```ini
STORAGE_BACKEND=ovh_ftp          # local | ovh_ftp | ovh_sftp
STORAGE_LOCAL_FALLBACK=true      # cutover: legge dal locale se manca su OVH
OVH_FTP_HOST=ftp.cluster023.hosting.ovh.net
OVH_FTP_PORT=21
OVH_FTP_USER=progetn
OVH_FTP_PASSWORD=...             # mai committare
OVH_FTP_BASE_PATH=/www/media     # cartella FTP mappata al docroot (confermare!)
OVH_FTP_USE_TLS=true             # FTPS; false solo per debug (ignorato da ovh_sftp)
OVH_SFTP_PORT=22                 # porta SFTP, usata solo con STORAGE_BACKEND=ovh_sftp
OVH_PUBLIC_BASE_URL=https://progettiersaf.com/media
# Frontend (build arg Vite): deve combaciare con OVH_PUBLIC_BASE_URL + /uploads
VITE_UPLOADS_BASE_URL=https://progettiersaf.com/media/uploads
```

`OVH_FTP_BASE_PATH` e `OVH_PUBLIC_BASE_URL` devono puntare alla **stessa**
cartella: i file scritti in `{BASE_PATH}/uploads/x` devono essere serviti da
`{PUBLIC_BASE_URL}/uploads/x`. Verificare il docroot dell'hosting (di norma
`/www`) e disattivare l'autoindex sul dominio.

## Procedura di cutover (a fasi)

La procedura vale identica per entrambi i backend remoti: dove sotto si legge
`ovh_ftp` si può sostituire `ovh_sftp` (stesse credenziali `OVH_FTP_*`, in più
`OVH_SFTP_PORT`). Gli script di migrazione/spike costruiscono il backend remoto
a prescindere da `STORAGE_BACKEND` e accettano `--protocol ftp|sftp` per
scegliere il canale.

0. **Spike connettività** (nessuna modifica all'app): con le `OVH_FTP_*` nel
   `.env`, dalla cartella `backend/`:
   ```
   python -m scripts.ftp_spike
   ```
   Verifica connect/STOR/RETR/SIZE/delete e che il file caricato sia subito
   raggiungibile via HTTP. Se sul backend FTPS fallisce il TLS data-channel, il
   workaround di riuso sessione è già in `_ReusedSslFTP_TLS`; in ultima istanza
   `OVH_FTP_USE_TLS=false` per isolare il problema, oppure passa al backend
   `ovh_sftp` (SSH, niente canale dati TLS).

1. **Deploy con `STORAGE_BACKEND=local`** — il layer è attivo ma il
   comportamento è identico a oggi. Nessun cambiamento osservabile.

2. **Mirror dei file esistenti** su OVH (solo upload, niente DB):
   ```
   python -m scripts.migrate_files_to_ovh --dry-run
   python -m scripts.migrate_files_to_ovh
   python -m scripts.migrate_files_to_ovh --verify
   ```
   Idempotente: salta i file già presenti con la stessa dimensione.

3. **Cutover**: `STORAGE_BACKEND=ovh_ftp` (o `ovh_sftp`) +
   `STORAGE_LOCAL_FALLBACK=true`. Rebuild del frontend con
   `VITE_UPLOADS_BASE_URL` → OVH. Le nuove scritture vanno su OVH; le letture
   mancanti ripiegano sul locale ancora montato.

4. **Finalizzazione**: a log puliti (nessun `storage_fallback_read`), si può
   mettere `STORAGE_LOCAL_FALLBACK=false` e dismettere i volumi/serving locali.

**Rollback**: in qualsiasi momento `STORAGE_BACKEND=local`. I file mirrorati
restano autoritativi in locale.

## Note

- I download dei singoli PDF passano ancora da un endpoint backend (preservano
  il nome file leggibile) ma leggono i bytes da OVH. I bundle "Scarica tutto"
  restano server-side. Video/immagini sono serviti come URL pubblici diretti.
- I file su OVH sono pubblici (path con UUID, non indovinabili): accettato come
  da decisione di progetto. Tenere l'autoindex disattivato.
- Test del layer: `pytest tests/test_remote_storage.py` (il backend OVH è
  testato contro un server FTP in-process se `pyftpdlib` è installato).
