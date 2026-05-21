# XTTS — Worker RunPod Serverless (TTS voce per le video-lezioni)

Questo worker esegue la sintesi vocale XTTS-v2 (voice cloning) su GPU NVIDIA
tramite [RunPod Serverless](https://www.runpod.io/serverless-gpu). Sostituisce
il TTS che girava sulla CPU della VM (~64 min per un video da 14 min → ~2-4 min
su GPU).

È un porting **fedele** dello script di riferimento `batch_generate.py`:
stessi parametri (`temperature=0.65`, `enable_text_splitting=False`), stesso
chunking (180 caratteri, silenzio 250 ms), nessuna modifica al modello.

## Contenuto

| File | Ruolo |
|---|---|
| `handler.py` | Handler RunPod (generator): un `yield` per segment audio. |
| `Dockerfile` | Immagine GPU; il modello XTTS-v2 (~1.8 GB) è cotto dentro. |
| `requirements.txt` | Dipendenze Python (torch è installato a parte dal Dockerfile). |
| `download_model.py` | Scarica il modello al build time. |
| `test_local.py` | Smoke test (richiede una GPU + un `sample.wav`). |

## 1 — Build dell'immagine

Serve solo Docker (non una GPU per **costruire** l'immagine).

```bash
cd XTTS
docker build --platform linux/amd64 -t <registry>/a4u-xtts:latest .
```

`--platform linux/amd64` è obbligatorio (anche su Mac ARM): RunPod gira su x86.

## 2 — Push su un registry

GHCR (consigliato, il repo è già su GitHub) oppure Docker Hub:

```bash
# GitHub Container Registry
echo $GHCR_TOKEN | docker login ghcr.io -u <github-user> --password-stdin
docker tag a4u-xtts:latest ghcr.io/<github-user>/a4u-xtts:latest
docker push ghcr.io/<github-user>/a4u-xtts:latest
```

## 3 — Creare l'endpoint RunPod Serverless

Console RunPod → **Serverless** → **New Endpoint** → **Import from Docker Registry**:

- **Container Image**: l'URL del push (es. `ghcr.io/<user>/a4u-xtts:latest`).
  Se il registry è privato, aggiungere le credenziali in *Container Registry Auth*.
- **GPU**: **RTX 4090** o **L40S** (XTTS-v2 è latency-bound: una H100 costa
  2.5-3x senza vantaggi reali).
- **Active Workers**: `0` → scale-to-zero, si paga solo durante i job.
- **Max Workers**: `1-2`.
- **Container Disk**: ≥ `15 GB` (il modello è cotto nell'immagine).
- **Idle Timeout**: `5-10 s` (default va bene).
- **Execution Timeout**: ≥ `900 s`.

Deploy. Annotare l'**Endpoint ID** dalla pagina dell'endpoint.

## 4 — API key

Console RunPod → **Settings** → **API Keys** → crea una key con permesso
sugli endpoint serverless.

## 5 — Configurare l'app

Nel `.env` del backend a4u (e in `docker-compose.prod.yml` se si deploya in
produzione):

```ini
RUNPOD_API_KEY=<la-api-key>
RUNPOD_TTS_ENDPOINT_ID=<endpoint-id>
RUNPOD_BASE_URL=https://api.runpod.ai
RUNPOD_TTS_TIMEOUT_SECONDS=1800
RUNPOD_TTS_POLL_INTERVAL_SECONDS=3
```

Riavviare il backend. La generazione video userà automaticamente RunPod per
la fase audio.

## Contratto I/O

**Input** (`POST https://api.runpod.ai/v2/{endpoint_id}/run`):

```json
{
  "input": {
    "language_code": "it",
    "voice_sample_url": "https://.../uploads/avatars/<user>/audio.webm",
    "segments": [
      { "segment_id": "seg-1", "text": "Testo del primo segmento." }
    ]
  }
}
```

Il worker scarica il `voice_sample_url` (deve essere pubblicamente
raggiungibile dai worker RunPod — è la stessa URL `/uploads/...` che usa
MiniMax). `voice_sample_b64` inline resta accettato come fallback.

**Output** — un elemento per **chunk** audio (~12s), consumato via
`GET /v2/{endpoint_id}/stream/{job_id}`. L'audio è inviato per chunk e
non per intero segmento: un segmento lungo come blob unico sforerebbe i
limiti dello stream di RunPod e andrebbe perso. Il client ricompone i
chunk per `segment_id`, ordinati per `chunk_index`:

```json
{ "segment_id": "seg-1", "chunk_index": 0, "chunk_total": 4,
  "audio_b64": "<FLAC base64>", "sample_rate": 24000 }
```

Errore fatale: `{ "error": "..." }`.

## Test locale

Su una macchina con GPU NVIDIA, con un file `sample.wav` (6-10 s di voce):

```bash
pip install -r requirements.txt
pip install torch torchaudio
python test_local.py sample.wav
```

## Costi (indicativi)

Scale-to-zero: **€0 da fermo**. Per un video da ~14 min, ~2-4 min di GPU →
~€0.05-0.10 con RTX 4090. Primo job dopo un periodo di inattività: +~10-30 s
di cold start (il modello è già nell'immagine, si carica solo in VRAM).
