import uuid
from datetime import datetime

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
    room_unlocks_new: list[str] = Field(default_factory=list)


class StatsResponse(BaseModel):
    total_books: int
    avg_intensity: float
    highest_intensity_book: dict | None
    most_common_emotion: str | None
    most_common_emotion_count: int
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