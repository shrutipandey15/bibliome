import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CalendarEmotion(BaseModel):
    slug: str
    color: str


class CalendarSegment(BaseModel):
    emotion: CalendarEmotion
    weight: float


class CalendarMonth(BaseModel):
    month_key: str
    label: str
    segments: list[CalendarSegment]


class EmotionalCalendarResponse(BaseModel):
    months: list[CalendarMonth]


class BlindSpotEmotion(BaseModel):
    slug: str
    name: str
    symbol: str


class BlindSpotItem(BaseModel):
    emotion: BlindSpotEmotion
    observation: str
    prevalence: float


BlindSpotsResponse = list[BlindSpotItem]


class PersonalityInfo(BaseModel):
    id: str
    name: str
    description: str
    color: str
    glyph: str
    blind_spots: list[str]
    comfort_tropes: list[str]


class TopEmotion(BaseModel):
    emotion_id: str
    count: int


class DNAProfileResponse(BaseModel):
    personality: PersonalityInfo | None
    dna_type_slug: str | None = None
    scores: dict[str, float]
    emotion_frequency: dict[str, int]
    emotion_intensity: dict[str, float]
    top_emotions: list[TopEmotion]
    avoided_emotions: list[str]
    co_occurrence: dict[str, int]
    book_count: int


class DNASnapshotResponse(BaseModel):
    id: uuid.UUID
    personality_type: str
    emotion_data: dict
    book_count: int
    year: int | None
    generated_at: datetime

    model_config = {"from_attributes": True}


class DNAGenerateResponse(BaseModel):
    snapshot: DNASnapshotResponse
    personality: PersonalityInfo


class StatsResponse(BaseModel):
    total_books: int
    avg_intensity: float
    highest_intensity_book: dict | None
    most_common_emotion: str | None
    most_common_emotion_count: int
    # Books tagged with each canonical emotion — the full Stats ledger (B5.3).
    emotion_counts: dict[str, int] = {}
    emotion_diversity: float
    unique_emotions_used: int
    total_emotions_possible: int
    books_per_month: float


class HeatmapBook(BaseModel):
    entry_id: uuid.UUID
    title: str
    author: str | None
    intensity: int


class HeatmapCell(BaseModel):
    entry_id: uuid.UUID
    emotion_id: str
    intensity: int


class HeatmapResponse(BaseModel):
    books: list[HeatmapBook]
    active_emotions: list[str]
    cells: list[HeatmapCell]
    total_books: int
    total_emotions: int


class TwinMatch(BaseModel):
    username: str
    display_name: str | None
    personality_type: str | None
    similarity: float
    shared_emotions: list[str]
    shared_count: int


class TwinResponse(BaseModel):
    twins: list[TwinMatch]
    your_top_emotions: list[str]
    total_public_users_searched: int


class RecapBook(BaseModel):
    title: str
    author: str | None
    intensity: int
    emotions: list[str]


class RecapShift(BaseModel):
    previous_type: str | None
    current_type: str | None
    shifted: bool


class RecapResponse(BaseModel):
    month: str
    books_logged: int
    avg_intensity: float
    top_emotions: list[TopEmotion]
    most_intense_book: RecapBook | None
    dominant_emotion: str | None
    new_emotions: list[str]
    personality_shift: RecapShift
    books: list[RecapBook]

class DNAProfileNotEnough(BaseModel):
    enough: Literal[False]
    book_count: int
    tagged_count: int
    needed: int
    message: str
    snapshot_count: int
    has_two_snapshots: bool
    journal_entry_count: int


class DNAProfileReady(BaseModel):
    enough: Literal[True]
    book_count: int
    tagged_count: int
    insights: list[dict[str, Any]]
    locked: list[dict[str, Any]]
    earned: list[dict[str, Any]]
    archetype: PersonalityInfo | None
    archetype_scores: dict[str, float]
    margin: float
    runner_up: str | None
    basis: dict[str, Any] | None
    profiles: dict[str, dict[str, float]]
    drift: float
    reads_for: list[str]
    snapshot_count: int
    has_two_snapshots: bool
    journal_entry_count: int
    # Merged on top by the /profile route itself (population-wide, not cached
    # per-reader) — not part of build_dna's own return. Whole percent, or None
    # if too few readers share this archetype to say honestly (profile_service).
    archetype_share: int | None = None


DNAProfileV2Response = DNAProfileNotEnough | DNAProfileReady


class PatternsResponse(BaseModel):
    stats: StatsResponse
    heatmap: HeatmapResponse


class DNAEvolutionPoint(BaseModel):
    id: str
    date: datetime
    archetype: str | None
    dna_type_slug: str | None
    book_count: int
    # Legacy snapshots can carry a top_emotions row missing "emotion_id".
    top_emotions: list[str | None]
    drift_from_prev: float | None
    trigger: str | None