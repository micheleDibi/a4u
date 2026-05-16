from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from app.schemas.common import ORMModel
from app.services import storage_service


class AvatarClipOut(ORMModel):
    id: uuid.UUID
    position: int
    prompt_text: str
    status: str  # pending|processing|ready|failed
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # `video_path` resta interno (path opaco). Esponiamo solo `video_url`.
    video_path: str | None = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def video_url(self) -> str | None:
        return storage_service.public_url(self.video_path) if self.video_path else None


class AvatarOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    audio_lang: str | None = None
    clips_status: str
    # Stato del pre-training XTTS latents (Fase 6 §9 rifinitura).
    # pending → processing → ready | failed. Quando `ready`, il backend
    # carica i latents da `tts_latents_path` per ogni job video saltando
    # ~5-15s di re-estrazione.
    tts_latents_status: str = "pending"
    tts_latents_generated_at: datetime | None = None
    tts_latents_error: str | None = None
    created_at: datetime
    updated_at: datetime
    clips: list[AvatarClipOut] = Field(default_factory=list)

    image_path: str = Field(exclude=True)
    audio_path: str | None = Field(default=None, exclude=True)
    # `tts_latents_path` resta interno: il file `.pt` non è esposto al FE.
    tts_latents_path: str | None = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def image_url(self) -> str:
        return storage_service.public_url(self.image_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audio_url(self) -> str | None:
        return (
            storage_service.public_url(self.audio_path)
            if self.audio_path
            else None
        )


class AvatarClipPromptOut(ORMModel):
    id: uuid.UUID
    position: int
    prompt: str
    label_it: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AvatarClipPromptCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    label_it: str | None = Field(default=None, max_length=120)
    is_active: bool = True


class AvatarClipPromptUpdate(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    label_it: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class AvatarClipPromptReorder(BaseModel):
    ordered_ids: list[uuid.UUID]


class AvatarVoiceScriptOut(ORMModel):
    language_code: str
    text: str
    created_at: datetime
    updated_at: datetime


class AvatarVoiceScriptUpsert(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
