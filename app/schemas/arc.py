import uuid

from pydantic import BaseModel


class ArcBeat(BaseModel):
    slug: str
    symbol: str
    color: str
    label: str  # "Start" | "Check-in" | "Middle" | "End"


class ArcCardResponse(BaseModel):
    entry_id: uuid.UUID
    title: str
    author: str | None
    dna_type: str | None
    arc: list[ArcBeat]
    intensity: int
    thought: str | None
