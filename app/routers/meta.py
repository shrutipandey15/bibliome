"""Served reference data — the single source of truth the frontend consumes so
the emotion vocabulary can't drift between client and server (B2.10 / P2-9)."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings
from app.utils.emotions import EMOTIONS

router = APIRouter(tags=["meta"])

settings = get_settings()


class EmotionVocabItem(BaseModel):
    slug: str
    family: str          # UI-only grouping ("It hurt", "It held me", …)
    name: str            # the plain word ("confusion")
    phrase: str          # the first-person line the UI shows ("I lost the plot")
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


class VersionResponse(BaseModel):
    app: str
    version: str
    git_sha: str
    environment: str


@router.get("/meta/version", response_model=VersionResponse)
async def get_version():
    """What is actually running. `git_sha` is stamped into the env at deploy time;
    it reads "unknown" on a dev box or when the deploy forgot to set it.
    """
    return VersionResponse(
        app=settings.APP_NAME,
        version="0.1.0",
        git_sha=settings.GIT_SHA,
        environment=settings.ENVIRONMENT,
    )
