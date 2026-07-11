"""Served reference data — the single source of truth the frontend consumes so
the emotion vocabulary can't drift between client and server (B2.10 / P2-9)."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.utils.emotions import EMOTIONS_13

router = APIRouter(tags=["meta"])


class EmotionVocabItem(BaseModel):
    slug: str
    name: str
    symbol: str
    color: str
    description: str


class EmotionVocabResponse(BaseModel):
    version: int
    count: int
    emotions: list[EmotionVocabItem]


# Bump when the vocabulary changes so clients can cache-bust.
EMOTION_VOCAB_VERSION = 1


@router.get("/emotions", response_model=EmotionVocabResponse)
async def get_emotion_vocabulary():
    """The canonical 13-emotion vocabulary (slug, name, symbol, color, description).

    Public + unauthenticated: it's static reference data. Frontends should consume
    this instead of hardcoding labels/colors (which is how the P2-9 drift happened).
    """
    return EmotionVocabResponse(
        version=EMOTION_VOCAB_VERSION,
        count=len(EMOTIONS_13),
        emotions=EMOTIONS_13,
    )
