# musetalk_client — client MuseTalk lip-sync (VENDORED)

Copia **verbatim** del client RunPod del progetto `MuseTalk-API`
(`scripts/client/`). Genera un video di avatar con lip-sync da un set di
clip + una traccia audio, usando l'endpoint RunPod Serverless MuseTalk e
Cloudflare R2 come storage di transito.

## ⚠️ NON MODIFICARE

Questi file sono una copia esatta di `MuseTalk-API/scripts/client/` e
**non vanno mai modificati**. MuseTalk è già testato e funzionante: ogni
modifica rischia di romperlo. Per aggiornarli, ri-copiare i file dal
progetto sorgente — mai editarli a mano qui.

`a4u` resta così disaccoppiata da `MuseTalk-API`: il client gira come
**subprocess isolato** (`python -m scripts.client.synth_random_lipsync`,
con `cwd` su questa cartella) e legge la configurazione solo da variabili
d'ambiente, che il backend gli passa esplicitamente.

## Come viene usato

`app/services/course_lesson_avatar_video_worker.py` lancia:

```
python -m scripts.client.synth_random_lipsync \
    --clips-dir <clip avatar MiniMax> \
    --audio     <audio della lezione> \
    --output    <video lip-sync> \
    --extra-margin/--left-cheek-width/--right-cheek-width <da Avatar>
```

con `cwd` = questa cartella (così `import scripts.client...` si risolve)
e l'environment popolato da `Settings`:
`RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`, `R2_ENDPOINT`, `R2_BUCKET`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.

## Dipendenze

`boto3` (R2 S3-compatible) e `requests` (HTTP RunPod) — dichiarate in
`backend/pyproject.toml`. `ffmpeg`/`ffprobe` devono essere nel PATH.
