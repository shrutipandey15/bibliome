"""Served reference data — the single source of truth the frontend consumes so
the emotion vocabulary can't drift between client and server (B2.10 / P2-9)."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.utils.emotions import EMOTIONS

router = APIRouter(tags=["meta"])


class EmotionVocabItem(BaseModel):
    slug: str
    family: str          # UI-only grouping ("It hurt", "It held me", …)
    name: str            # display label
    symbol: str
    color: str
    description: str


class EmotionVocabResponse(BaseModel):
    version: int
    count: int
    emotions: list[EmotionVocabItem]


# Bump when the vocabulary changes so clients can cache-bust.
# v2: the 18-emotion vocabulary with families (replaced the 13-emotion set).
EMOTION_VOCAB_VERSION = 2


@router.get("/emotions", response_model=EmotionVocabResponse)
async def get_emotion_vocabulary():
    """The canonical 18-emotion vocabulary (slug, family, name, symbol, color, description).

    Public + unauthenticated: it's static reference data. Frontends should consume
    this instead of hardcoding labels/colors (which is how the P2-9 drift happened).
    The ``family`` groups emotions for display only — the stored value is the slug.
    """
    return EmotionVocabResponse(
        version=EMOTION_VOCAB_VERSION,
        count=len(EMOTIONS),
        emotions=EMOTIONS,
    )
