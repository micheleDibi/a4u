from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.permissions import (
    ALL_PERMISSION_CODES,
    R,
    ROLE_DEFAULT_PERMISSIONS,
    ROLE_NAME_IT,
    ROLE_RANK,
)
from app.core.security import hash_password
from app.models.avatar_clip_prompt import AvatarClipPrompt
from app.models.avatar_voice_script import AvatarVoiceScript
from app.models.language import Language
from app.models.permission import Permission, RolePermission
from app.models.role import OrganizationRole
from app.models.translation import Translation
from app.models.user import User

log = get_logger("app.seed")

ROLE_DESCRIPTIONS: dict[str, str] = {
    R.CREATOR: "Massimo livello dell'organizzazione, può trasferirne la titolarità.",
    R.ORG_ADMIN: "Amministra l'organizzazione, gestisce membri e contenuti.",
    R.MANAGER: "Gestisce i corsi e visualizza i membri.",
    R.MEMBER: "Membro standard con accesso ai corsi assegnati.",
}

PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "member:view": (
        "Vedere l'elenco dei membri dell'organizzazione con nome, email, "
        "ruolo e data di iscrizione. Necessario per quasi tutte le altre "
        "azioni sui membri."
    ),
    "member:invite": (
        "Creare inviti via email per nuovi membri scegliendo il ruolo "
        "iniziale. Ogni invito è un link monouso con scadenza a 7 giorni."
    ),
    "member:assign_role": (
        "Cambiare il ruolo di un membro esistente, ma solo fino al proprio "
        "livello di rank: un Org Admin non può promuovere qualcuno a "
        "Creator né modificare un membro con ruolo superiore."
    ),
    "member:remove": (
        "Rimuovere un membro dall'organizzazione. Il Creator non è "
        "rimovibile direttamente: prima va trasferito ad un altro membro."
    ),
    "template:slide:manage": (
        "Creare, modificare, eliminare e scegliere come default i template "
        "per le slide dei corsi (formato 16:9 / 4:3, colori, font, logo, "
        "immagine di sfondo)."
    ),
    "template:pdf:manage": (
        "Creare, modificare, eliminare e scegliere come default i template "
        "per i documenti PDF (formato A4/Letter, header/footer/margini in "
        "mm, colori, logo, sfondo)."
    ),
    "permission:manage": (
        "Modificare i permessi dell'organizzazione: override per ruolo "
        "(es. concedere a tutti i Manager un permesso non default) e "
        "override per singolo membro (concedi/revoca puntuale)."
    ),
    "org:transfer_creator": (
        "Trasferire il ruolo di Creator a un altro membro. Chi era Creator "
        "diventa Org Admin. Azione riservata al Creator e irreversibile "
        "senza un nuovo trasferimento."
    ),
    "org:update": (
        "Modificare i dati anagrafici dell'organizzazione: nome, partita "
        "IVA, codice fiscale, indirizzo, contatti, logo."
    ),
    "course_config:manage": (
        "Configurare i parametri di default per la generazione dei corsi: "
        "numero di moduli per CFU, numero di lezioni per modulo, durata "
        "delle lezioni e attivazione della verifica di apprendimento "
        "finale (con il numero di domande a scelta multipla e aperte da "
        "generare). Questi valori vengono usati per pre-popolare la "
        "struttura di un nuovo corso."
    ),
    "course:view": (
        "Vedere i corsi a sé assegnati. Per vedere anche i corsi assegnati "
        "ad altri membri dell'organizzazione serve il permesso "
        "'course:view_all'."
    ),
    "course:view_all": (
        "Vedere TUTTI i corsi dell'organizzazione, inclusi quelli assegnati "
        "ad altri membri. Senza questo permesso l'utente vede solo i corsi "
        "a sé assegnati (anche se ha 'course:edit')."
    ),
    "course:create": (
        "Creare nuovi corsi nell'organizzazione, scegliendo titolo, "
        "obiettivi, tassonomie didattiche, numero di CFU e caricando i "
        "documenti di riferimento."
    ),
    "course:assign": (
        "Cambiare l'assegnatario di un corso esistente, scegliendo tra i "
        "membri attivi dell'organizzazione."
    ),
    "course:edit": (
        "Modificare i parametri di un corso (titolo, obiettivi, "
        "tassonomie, CFU, argomenti chiave, lingua) e gestire i documenti "
        "di riferimento. Non implica la visibilità sui corsi degli altri "
        "membri: per quella serve 'course:view_all'."
    ),
    "course:delete": (
        "Eliminare un corso e tutti i suoi documenti di riferimento. "
        "Operazione irreversibile."
    ),
    "course:generate": (
        "Avviare la generazione AI dei contenuti del corso (architettura, "
        "lezioni, slide, discorso TTS) a partire dai parametri configurati."
    ),
    "course:save_draft": (
        "Salvare un corso come bozza dalle schede 'Informazioni di base' e "
        "'Inquadramento didattico' senza confermare il setup didattico. "
        "Utile quando un amministratore crea uno stub di corso (es. solo "
        "titolo e CFU) e lo assegna a un altro utente che lo completerà."
    ),
}

# Prompt seedati per la generazione clip MiniMax. Sono in inglese
# (MiniMax preferisce EN) e descrivono micro-movimenti naturali di un
# docente universitario. Vincolo enforced lato service:
# last_frame_image == first_frame_image, quindi i clip sono loopabili.
AVATAR_VOICE_SCRIPTS_SEED: dict[str, str] = {
    "it": (
        "Buongiorno, mi presento e leggo con voce naturale questo breve "
        "testo per registrare un campione vocale. La mia voce è chiara e "
        "il ritmo è disteso, perché un buon ascolto nasce da una lettura "
        "senza fretta. Pronuncio con cura ogni sillaba, anche le più "
        "delicate: gnocchi, zucchero, specchio, freschezza, qualunque, "
        "scivolare. Quando incontro una pausa la rispetto, e quando un "
        "concetto è importante alzo leggermente il tono. Grazie per "
        "l'attenzione: questa registrazione servirà a creare un modello "
        "vocale fedele al mio modo di parlare."
    ),
    "en": (
        "Good day, my name will appear in the lessons and today I am "
        "recording a short voice sample. I read at a natural pace, with "
        "clear pronunciation, because good listening starts with unhurried "
        "speaking. I take care of every sound: through, thought, weather, "
        "rhythm, schedule, measure. I respect short pauses, and I raise "
        "my tone slightly when a concept matters. Thank you for listening: "
        "this recording will help create a voice model that sounds like me."
    ),
}


AVATAR_CLIP_PROMPTS_SEED: list[dict[str, str]] = [
    {
        "label_it": "Cenno di pensiero",
        "prompt": (
            "Subtle thoughtful head nod with calm shoulders. A university lecturer "
            "pausing briefly to think between sentences. Stable pose, natural "
            "breathing, no sudden motion."
        ),
    },
    {
        "label_it": "Sorriso e ammiccamento",
        "prompt": (
            "Slight friendly smile with a single soft eye blink. Approachable "
            "professor making warm eye contact with the audience. Calm shoulders, "
            "stable head."
        ),
    },
    {
        "label_it": "Sguardo che scorre l'aula",
        "prompt": (
            "Slow head turn from frontal to a small angle and smoothly back, as if "
            "scanning a lecture room. Natural neck movement, eyes following the "
            "head, no sudden motion."
        ),
    },
    {
        "label_it": "Cenno di assenso",
        "prompt": (
            "Gentle nod of agreement with a brief small smile, encouraging the "
            "listener. Hands and shoulders calm, eyes on camera."
        ),
    },
    {
        "label_it": "Reazione calorosa",
        "prompt": (
            "Short warm chuckle, mouth opening slightly, eyes crinkling — a natural "
            "human reaction. Subtle head bob, then return to neutral pose."
        ),
    },
]

# Metadata per le 24 lingue UE seedate al primo avvio.
# Dopo il seed iniziale, l'admin di piattaforma può modificare/aggiungere/eliminare
# lingue e traduzioni via UI; il DB diventa la fonte di verità.
LANGUAGE_META: dict[str, dict[str, object]] = {
    "bg": {"name": "Български", "country": "BG", "default": False, "rtl": False},
    "cs": {"name": "Čeština", "country": "CZ", "default": False, "rtl": False},
    "da": {"name": "Dansk", "country": "DK", "default": False, "rtl": False},
    "de": {"name": "Deutsch", "country": "DE", "default": False, "rtl": False},
    "el": {"name": "Ελληνικά", "country": "GR", "default": False, "rtl": False},
    "en": {"name": "English", "country": "GB", "default": False, "rtl": False},
    "es": {"name": "Español", "country": "ES", "default": False, "rtl": False},
    "et": {"name": "Eesti", "country": "EE", "default": False, "rtl": False},
    "fi": {"name": "Suomi", "country": "FI", "default": False, "rtl": False},
    "fr": {"name": "Français", "country": "FR", "default": False, "rtl": False},
    "ga": {"name": "Gaeilge", "country": "IE", "default": False, "rtl": False},
    "hr": {"name": "Hrvatski", "country": "HR", "default": False, "rtl": False},
    "hu": {"name": "Magyar", "country": "HU", "default": False, "rtl": False},
    "it": {"name": "Italiano", "country": "IT", "default": True, "rtl": False},
    "lt": {"name": "Lietuvių", "country": "LT", "default": False, "rtl": False},
    "lv": {"name": "Latviešu", "country": "LV", "default": False, "rtl": False},
    "mt": {"name": "Malti", "country": "MT", "default": False, "rtl": False},
    "nl": {"name": "Nederlands", "country": "NL", "default": False, "rtl": False},
    "pl": {"name": "Polski", "country": "PL", "default": False, "rtl": False},
    "pt": {"name": "Português", "country": "PT", "default": False, "rtl": False},
    "ro": {"name": "Română", "country": "RO", "default": False, "rtl": False},
    "sk": {"name": "Slovenčina", "country": "SK", "default": False, "rtl": False},
    "sl": {"name": "Slovenščina", "country": "SI", "default": False, "rtl": False},
    "sv": {"name": "Svenska", "country": "SE", "default": False, "rtl": False},
}

SEED_LOCALES_DIR = Path(__file__).resolve().parents[1] / "i18n" / "seed_locales"

# Canoniche mantenute esplicitamente nel frontend (vedi memoria utente):
# le altre 22 lingue UE vengono completate dall'admin via "Completa con AI".
_CANONICAL_FRONTEND_LOCALES = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "i18n"
    / "locales"
)
_CANONICAL_LANG_CODES = ("it", "en")


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    """Appiattisce dict annidati: {a:{b:'x'}} → {'a.b':'x'}."""
    out: dict[str, str] = {}
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, full))
        elif isinstance(v, str):
            out[full] = v
    return out


async def ensure_seed(db: AsyncSession) -> None:
    """Idempotente: esegui sempre dopo `alembic upgrade head`."""
    await _seed_roles(db)
    await _seed_permissions(db)
    await _seed_role_permissions(db)
    await _seed_languages_and_translations(db)
    await _sync_canonical_translations(db)
    await _seed_avatar_clip_prompts(db)
    await _seed_avatar_voice_scripts(db)
    await _seed_bootstrap_admin(db)
    log.info("seed_complete")


async def _sync_canonical_translations(db: AsyncSession) -> None:
    """Sincronizza le traduzioni canoniche IT/EN dal frontend.

    Diversamente da `_seed_languages_and_translations` che è additivo (skip
    delle chiavi esistenti), questa funzione fa **upsert con overwrite**:
    se il valore frontend differisce dal DB, il DB viene aggiornato.

    Motivazione: il frontend `it.json` è il "single source of truth" per le
    UI string. Quando aggiungiamo nuove chiavi (es. `nav.courseSettings`,
    `i18n.autoTranslate`, `myAvatar.*`) o modifichiamo testi (es. tooltip
    della verifica corsi), il backend deve riallinearsi automaticamente al
    prossimo restart, altrimenti `auto_translate_missing` invia a OpenAI un
    reference italiano incompleto e le altre lingue rimangono parziali.

    Operativo solo se la cartella canonica è raggiungibile (dev/repo
    monorepo). In container backend-only è no-op silenzioso.
    """
    if not _CANONICAL_FRONTEND_LOCALES.exists():
        return
    for code in _CANONICAL_LANG_CODES:
        path = _CANONICAL_FRONTEND_LOCALES / f"{code}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover
            log.warning("canonical_locale_parse_failed", code=code, error=str(exc))
            continue
        flat = _flatten(data)
        existing_rows = (
            await db.execute(
                select(Translation).where(Translation.language_code == code)
            )
        ).scalars().all()
        existing_map = {r.key: r for r in existing_rows}
        added = 0
        updated = 0
        for key, value in flat.items():
            row = existing_map.get(key)
            if row is None:
                db.add(Translation(language_code=code, key=key, value=value))
                added += 1
            elif row.value != value:
                row.value = value
                updated += 1
        await db.flush()
        if added or updated:
            log.info(
                "canonical_translations_synced",
                code=code,
                added=added,
                updated=updated,
            )


async def _seed_avatar_voice_scripts(db: AsyncSession) -> None:
    """Seed degli script vocali default per IT/EN. Idempotente per chiave."""
    existing = {
        s.language_code
        for s in (await db.execute(select(AvatarVoiceScript))).scalars().all()
    }
    inserted = 0
    for lang, text in AVATAR_VOICE_SCRIPTS_SEED.items():
        if lang in existing:
            continue
        db.add(AvatarVoiceScript(language_code=lang, text=text))
        inserted += 1
    if inserted:
        await db.flush()
        log.info("seed_avatar_voice_scripts", inserted=inserted)


async def _seed_avatar_clip_prompts(db: AsyncSession) -> None:
    """Seed dei prompt default per la generazione clip MiniMax.

    Idempotente: se esiste già qualche prompt, non fa nulla (l'admin può
    averli modificati via UI).
    """
    existing_count = (
        await db.execute(select(AvatarClipPrompt))
    ).scalars().first()
    if existing_count is not None:
        return
    for position, item in enumerate(AVATAR_CLIP_PROMPTS_SEED):
        db.add(
            AvatarClipPrompt(
                position=position,
                prompt=item["prompt"],
                label_it=item["label_it"],
                is_active=True,
            )
        )
    await db.flush()
    log.info("seed_avatar_clip_prompts", count=len(AVATAR_CLIP_PROMPTS_SEED))


async def _seed_roles(db: AsyncSession) -> None:
    existing = {
        r.code for r in (await db.execute(select(OrganizationRole))).scalars().all()
    }
    for code, name in ROLE_NAME_IT.items():
        if code in existing:
            continue
        db.add(
            OrganizationRole(
                code=code,
                name_it=name,
                description=ROLE_DESCRIPTIONS.get(code),
                rank=ROLE_RANK[code],
            )
        )
    await db.flush()


async def _seed_permissions(db: AsyncSession) -> None:
    existing = {
        p.code: p
        for p in (await db.execute(select(Permission))).scalars().all()
    }
    for code in ALL_PERMISSION_CODES:
        desc = PERMISSION_DESCRIPTIONS.get(code)
        if code in existing:
            # Allinea la description se è cambiata in PERMISSION_DESCRIPTIONS.
            if desc and existing[code].description != desc:
                existing[code].description = desc
            continue
        db.add(Permission(code=code, description=desc, scope="organization"))
    await db.flush()


async def _seed_role_permissions(db: AsyncSession) -> None:
    roles = {
        r.code: r for r in (await db.execute(select(OrganizationRole))).scalars().all()
    }
    perms = {p.code: p for p in (await db.execute(select(Permission))).scalars().all()}
    existing_links = {
        (rp.role_id, rp.permission_id)
        for rp in (await db.execute(select(RolePermission))).scalars().all()
    }
    for role_code, permission_codes in ROLE_DEFAULT_PERMISSIONS.items():
        role = roles.get(role_code)
        if role is None:
            continue
        for code in permission_codes:
            perm = perms.get(code)
            if perm is None:
                continue
            if (role.id, perm.id) in existing_links:
                continue
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    await db.flush()


async def _seed_languages_and_translations(db: AsyncSession) -> None:
    """Seed delle 24 lingue UE + traduzioni dai JSON in app/i18n/seed_locales/.

    Idempotente: se la lingua esiste già non la sovrascrive (l'admin avrebbe
    potuto modificarla via UI). Se mancano traduzioni per chiavi, le aggiunge;
    non sovrascrive valori già presenti in DB.
    """
    existing_langs = {
        ll.code for ll in (await db.execute(select(Language))).scalars().all()
    }
    for code, meta in LANGUAGE_META.items():
        if code not in existing_langs:
            db.add(
                Language(
                    code=code,
                    name_native=str(meta["name"]),
                    flag_country_code=str(meta["country"]) if meta.get("country") else None,
                    is_default=bool(meta["default"]),
                    rtl=bool(meta["rtl"]),
                    is_active=True,
                )
            )
    await db.flush()

    if not SEED_LOCALES_DIR.exists():
        log.warning("seed_locales_missing", path=str(SEED_LOCALES_DIR))
        return

    inserted = 0
    for code in LANGUAGE_META:
        path = SEED_LOCALES_DIR / f"{code}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover
            log.warning("seed_locale_parse_failed", code=code, error=str(exc))
            continue
        flat = _flatten(data)
        existing_keys = set(
            (
                await db.execute(
                    select(Translation.key).where(Translation.language_code == code)
                )
            ).scalars().all()
        )
        for key, value in flat.items():
            if key in existing_keys:
                continue
            db.add(Translation(language_code=code, key=key, value=value))
            inserted += 1
    await db.flush()
    log.info("seed_translations", inserted=inserted)


async def _seed_bootstrap_admin(db: AsyncSession) -> None:
    settings = get_settings()
    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password
    if not email or not password:
        log.info("bootstrap_admin_skipped", reason="missing_env")
        return
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        if not existing.is_platform_admin:
            existing.is_platform_admin = True
            await db.flush()
            log.info("bootstrap_admin_promoted", email=email)
        return
    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=settings.bootstrap_admin_full_name,
        is_platform_admin=True,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    log.info("bootstrap_admin_created", email=email)
