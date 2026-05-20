"""Smoke test locale del handler XTTS — richiede una GPU NVIDIA e un
file audio di riferimento.

Uso:
    python test_local.py [path/al/sample.wav]

Importare `handler` carica il modello su GPU (~1.8 GB): eseguire solo
su una macchina con GPU. Non incluso nell'immagine Docker (.dockerignore).
"""
import base64
import io
import sys

import soundfile as sf


def main() -> None:
    sample = sys.argv[1] if len(sys.argv) > 1 else "sample.wav"
    with open(sample, "rb") as f:
        voice_b64 = base64.b64encode(f.read()).decode("ascii")

    import handler as h

    job = {
        "id": "local-test",
        "input": {
            "language_code": "it",
            "voice_sample_b64": voice_b64,
            "voice_sample_format": sample.rsplit(".", 1)[-1],
            "segments": [
                {"segment_id": "s1", "text": "Ciao, questo e' un test di sintesi."},
                {"segment_id": "s2", "text": "Secondo segmento, un'altra frase di prova."},
            ],
        },
    }

    count = 0
    for result in h.handler(job):
        if "error" in result:
            print("ERRORE:", result["error"])
            sys.exit(1)
        data, sr = sf.read(io.BytesIO(base64.b64decode(result["audio_b64"])))
        print(f"  segment {result['segment_id']}: {len(data)} campioni @ {sr} Hz")
        assert sr == 24000, f"sample rate inatteso: {sr}"
        assert len(data) > 0, "audio vuoto"
        count += 1

    assert count == 2, f"attesi 2 segment, ricevuti {count}"
    print("OK — handler funzionante.")


if __name__ == "__main__":
    main()
