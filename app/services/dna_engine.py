"""
Book DNA Engine — The Secret Sauce

Calculates a user's reading personality based on their emotional history.
Phase 1: Rule-based pattern matching with weighted scoring.
Phase 2 (future): Clustering from real user data.
Phase 3 (future): AI-powered with Claude API.
"""

import math
from collections import Counter
from datetime import datetime, timezone

from app.utils.emotions import VALID_EMOTION_IDS

# Maps the engine's internal personality ids → the 8 canonical "bible" slugs.
# The engine currently recognises 8 types; this mapping assigns each one a stable
# bible slug. Some matches are judgment calls — update if the product team
# clarifies the intent of a specific mapping.
DNA_TYPE_SLUG_MAP: dict[str, str] = {
    "grief_romantic":          "grief_romantic",
    "control_intellectual":    "chaos_cartographer",
    "soft_masochist":          "soft_masochist",
    "comfort_architect":       "comfort_architect",
    "midnight_arsonist":       "rage_archivist",
    "quiet_witness":           "tender_witness",
    "obsessive_romantic":      "two_am_scholar",
    "emotional_archaeologist": "awe_chaser",
}

BIBLE_DNA_SLUGS = frozenset({
    "grief_romantic", "chaos_cartographer", "soft_masochist", "awe_chaser",
    "comfort_architect", "rage_archivist", "tender_witness", "two_am_scholar",
})


def dna_type_slug_for(engine_id: str | None) -> str | None:
    if not engine_id:
        return None
    return DNA_TYPE_SLUG_MAP.get(engine_id)


PERSONALITY_TYPES = [
    {
        "id": "grief_romantic",
        "name": "The Grief Romantic",
        "description": "You seek books that break your heart because feeling deeply is how you know you're alive. Loss isn't your enemy — numbness is.",
        "primary_emotions": ["grief", "healing", "seen"],
        "anti_emotions": ["nothing", "wit"],
        "blind_spots": ["You avoid books with neat happy endings", "You mistake emotional pain for depth"],
        "comfort_tropes": ["Unrequited love", "Beautiful suffering", "Bittersweet endings"],
        "color": "#3A5A6B",
        "glyph": "◈",
    },
    {
        "id": "control_intellectual",
        "name": "The Control-Seeking Intellectual",
        "description": "You read to master the chaos. Understanding is your armor, and every book is a new piece of territory mapped.",
        "primary_emotions": ["wit", "dread", "nothing"],
        "anti_emotions": ["comfort", "healing"],
        "blind_spots": ["You intellectualize emotions instead of feeling them", "You abandon books that make you vulnerable"],
        "comfort_tropes": ["Unreliable narrators", "Philosophical fiction", "Systems and structures"],
        "color": "#5A5A8A",
        "glyph": "◇",
    },
    {
        "id": "soft_masochist",
        "name": "The Soft Masochist",
        "description": "You choose pain on purpose because you trust books that hurt you more than ones that comfort you.",
        "primary_emotions": ["rage", "grief", "obsession"],
        "anti_emotions": ["nothing", "comfort"],
        "blind_spots": ["You equate suffering with authenticity", "You distrust books that feel too safe"],
        "comfort_tropes": ["Tragic love", "Moral ambiguity", "Devastating plot twists"],
        "color": "#6B3A5D",
        "glyph": "◆",
    },
    {
        "id": "comfort_architect",
        "name": "The Comfort Architect",
        "description": "You build emotional safety through stories. Your bookshelf isn't a collection — it's a home you can always return to.",
        "primary_emotions": ["comfort", "nostalgia", "seen"],
        "anti_emotions": ["rage", "chaos", "dread"],
        "blind_spots": ["You avoid books that might destabilize you", "You re-read instead of risking new things"],
        "comfort_tropes": ["Found family", "Slow-burn romance", "Cozy settings"],
        "color": "#7A8B6F",
        "glyph": "○",
    },
    {
        "id": "midnight_arsonist",
        "name": "The Midnight Arsonist",
        "description": "You read like you're setting fire to your own beliefs. Comfort zones are for people who haven't found the right book yet.",
        "primary_emotions": ["chaos", "awe", "rage"],
        "anti_emotions": ["comfort", "nothing", "nostalgia"],
        "blind_spots": ["You conflate discomfort with growth", "You dismiss gentle books as boring"],
        "comfort_tropes": ["Boundary-pushing fiction", "Experimental structure", "Provocative themes"],
        "color": "#C47A3A",
        "glyph": "△",
    },
    {
        "id": "quiet_witness",
        "name": "The Quiet Witness",
        "description": "You absorb everything and process in silence. Books are your confessional — the only place you don't perform.",
        "primary_emotions": ["seen", "awe", "dread"],
        "anti_emotions": ["rage", "chaos"],
        "blind_spots": ["You observe more than you feel", "You use reading to avoid confrontation"],
        "comfort_tropes": ["Introspective narrators", "Literary fiction", "Quiet revelations"],
        "color": "#B8964E",
        "glyph": "□",
    },
    {
        "id": "obsessive_romantic",
        "name": "The Obsessive Romantic",
        "description": "You don't read books — you fall into them. Every story is a love affair, and you don't do casual.",
        "primary_emotions": ["obsession", "comfort", "2am"],
        "anti_emotions": ["nothing", "dread", "wit"],
        "blind_spots": ["You abandon books you can't fall in love with", "You chase the high of a new obsession"],
        "comfort_tropes": ["Consuming love stories", "Immersive worlds", "Characters you'd die for"],
        "color": "#C4553A",
        "glyph": "♡",
    },
    {
        "id": "emotional_archaeologist",
        "name": "The Emotional Archaeologist",
        "description": "You dig into stories looking for buried parts of yourself. Every book is an excavation site.",
        "primary_emotions": ["nostalgia", "seen", "healing"],
        "anti_emotions": ["nothing", "2am"],
        "blind_spots": ["You over-analyze what you read", "You search for meaning even when there's none"],
        "comfort_tropes": ["Psychological depth", "Identity exploration", "Hidden truths"],
        "color": "#7A5A9B",
        "glyph": "◎",
    },
]


def calculate_personality(entries: list[dict]) -> dict:
    """
    Calculate a user's reading personality from their book entries.

    Args:
        entries: List of dicts with keys:
            - emotions: list of emotion_id strings
            - intensity: int 1-10
            - created_at: datetime (for recency weighting)

    Returns:
        Dict with personality info, scores, and analytics.
    """
    if len(entries) < 3:
        return {
            "personality": None,
            "scores": {},
            "emotion_frequency": {},
            "emotion_intensity": {},
            "top_emotions": [],
            "blind_spots": [],
            "comfort_tropes": [],
            "avoided_emotions": [],
            "co_occurrence": {},
        }

    # === 1. Count emotion frequency ===
    emotion_freq = Counter()
    for entry in entries:
        for emo in entry["emotions"]:
            if emo in VALID_EMOTION_IDS:
                emotion_freq[emo] += 1

    # === 2. Calculate intensity-weighted emotions ===
    emotion_intensity = {}
    emotion_counts = {}
    for entry in entries:
        intensity = entry.get("intensity", 5)
        for emo in entry["emotions"]:
            if emo in VALID_EMOTION_IDS:
                emotion_intensity[emo] = emotion_intensity.get(emo, 0) + intensity
                emotion_counts[emo] = emotion_counts.get(emo, 0) + 1

    # Average intensity per emotion
    avg_intensity = {
        emo: emotion_intensity[emo] / emotion_counts[emo]
        for emo in emotion_intensity
    }

    # === 3. Recency weighting (recent books count more) ===
    recency_weights = {}
    now = datetime.now(timezone.utc)
    sorted_entries = sorted(entries, key=lambda e: e.get("created_at", now))
    for i, entry in enumerate(sorted_entries):
        weight = 0.5 + (i / max(len(entries) - 1, 1)) * 0.5  # 0.5 to 1.0
        for emo in entry["emotions"]:
            if emo in VALID_EMOTION_IDS:
                recency_weights[emo] = recency_weights.get(emo, 0) + weight

    # === 4. Build co-occurrence matrix ===
    co_occurrence = Counter()
    for entry in entries:
        # Filter for valid emotions only
        emos = sorted([e for e in entry["emotions"] if e in VALID_EMOTION_IDS])
        for i in range(len(emos)):
            for j in range(i + 1, len(emos)):
                co_occurrence[(emos[i], emos[j])] += 1

    # === 5. Score each personality type ===
    scores = {}
    for ptype in PERSONALITY_TYPES:
        score = 0.0

        # Frequency match (0-40 points)
        for emo in ptype["primary_emotions"]:
            freq = emotion_freq.get(emo, 0)
            score += freq * 8  # Each occurrence = 8 points

        # Intensity match (0-30 points)
        for emo in ptype["primary_emotions"]:
            avg_int = avg_intensity.get(emo, 0)
            score += avg_int * 1.5  # High intensity = more points

        # Recency bonus (0-15 points)
        for emo in ptype["primary_emotions"]:
            score += recency_weights.get(emo, 0) * 2

        # Co-occurrence bonus (0-10 points)
        primary = ptype["primary_emotions"]
        for i in range(len(primary)):
            for j in range(i + 1, len(primary)):
                pair = tuple(sorted([primary[i], primary[j]]))
                score += co_occurrence.get(pair, 0) * 5

        # Anti-emotion penalty
        for emo in ptype.get("anti_emotions", []):
            freq = emotion_freq.get(emo, 0)
            score -= freq * 3

        scores[ptype["id"]] = round(score, 2)

    # === 6. Find the winner ===
    best_id = max(scores, key=scores.get)
    personality = next(p for p in PERSONALITY_TYPES if p["id"] == best_id)

    # === 7. Top emotions ===
    top_emotions = emotion_freq.most_common(6)

    # === 8. Detect blind spots ===
    all_emotions = set(VALID_EMOTION_IDS)
    used_emotions = set(emotion_freq.keys())
    avoided_emotions = [
        emo for emo in all_emotions
        if emotion_freq.get(emo, 0) == 0
    ]

    return {
        "personality": {
            "id": personality["id"],
            "name": personality["name"],
            "description": personality["description"],
            "color": personality["color"],
            "glyph": personality["glyph"],
            "blind_spots": personality["blind_spots"],
            "comfort_tropes": personality["comfort_tropes"],
        },
        "dna_type_slug": dna_type_slug_for(personality["id"]),
        "scores": scores,
        "emotion_frequency": dict(emotion_freq),
        "emotion_intensity": {k: round(v, 1) for k, v in avg_intensity.items()},
        "top_emotions": [{"emotion_id": emo, "count": count} for emo, count in top_emotions],
        "avoided_emotions": sorted(avoided_emotions),
        "co_occurrence": {
            f"{a}+{b}": count
            for (a, b), count in co_occurrence.most_common(10)
        },
    }


def generate_stats(entries: list[dict]) -> dict:
    """
    Generate reading statistics from entries.
    """
    if not entries:
        return {
            "total_books": 0,
            "avg_intensity": 0,
            "highest_intensity_book": None,
            "most_common_emotion": None,
            "most_common_emotion_count": 0,
            "emotion_diversity": 0,
            "unique_emotions_used": 0,
            "total_emotions_possible": len(VALID_EMOTION_IDS),
            "books_per_month": 0,
        }

    total = len(entries)

    # Average intensity
    intensities = [e.get("intensity", 5) for e in entries]
    avg_intensity = sum(intensities) / len(intensities)

    # Highest intensity book
    max_entry = max(entries, key=lambda e: e.get("intensity", 0))

    # Most common emotion
    all_emotions = []
    for e in entries:
        # Only consider valid emotions for stats
        valid_entry_emotions = [em for em in e["emotions"] if em in VALID_EMOTION_IDS]
        all_emotions.extend(valid_entry_emotions)
    
    emotion_counter = Counter(all_emotions)
    most_common = emotion_counter.most_common(1)

    # Emotion diversity (unique emotions / total possible)
    unique_emotions = len(set(all_emotions))
    diversity = unique_emotions / len(VALID_EMOTION_IDS)

    dates = [e["created_at"] for e in entries if e.get("created_at")]
    
    if not dates:
        books_per_month = total
    else:
        real_span_days = (max(dates) - min(dates)).days        
        effective_days = max(real_span_days, 30)
        books_per_month = (total / effective_days) * 30

    return {
        "total_books": total,
        "avg_intensity": round(avg_intensity, 1),
        "highest_intensity_book": {
            "title": max_entry.get("title", ""),
            "intensity": max_entry.get("intensity", 0),
        },
        "most_common_emotion": most_common[0][0] if most_common else None,
        "most_common_emotion_count": most_common[0][1] if most_common else 0,
        "emotion_diversity": round(diversity * 100),
        "unique_emotions_used": unique_emotions,
        "total_emotions_possible": len(VALID_EMOTION_IDS),
        "books_per_month": round(books_per_month, 1),
    }


def build_emotion_vector(emotion_freq: dict[str, int]) -> list[float]:
    """
    Convert emotion frequency dict to a fixed-length vector.
    Order follows VALID_EMOTION_IDS for consistency across users.
    """
    sorted_ids = sorted(list(VALID_EMOTION_IDS))
    return [float(emotion_freq.get(emo, 0)) for emo in sorted_ids]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0.0-1.0."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def find_twins(
    user_emotion_freq: dict[str, int],
    candidates: list[dict],
    max_results: int = 5,
) -> list[dict]:
    """
    Find reading twins — users with the most similar emotion profiles.

    Args:
        user_emotion_freq: Current user's emotion frequency dict {emotion_id: count}
        candidates: List of dicts with keys:
            - username, display_name, personality_type
            - emotion_frequency: dict {emotion_id: count}
        max_results: How many twins to return

    Returns:
        List of twin matches sorted by similarity (highest first).
    """
    if not candidates:
        return []

    user_vec = build_emotion_vector(user_emotion_freq)
    user_emotions = set(e for e, c in user_emotion_freq.items() if c > 0)

    results = []
    for candidate in candidates:
        cand_freq = candidate.get("emotion_frequency", {})
        cand_vec = build_emotion_vector(cand_freq)

        sim = cosine_similarity(user_vec, cand_vec)
        if sim < 0.01:
            continue

        cand_emotions = set(e for e, c in cand_freq.items() if c > 0)
        shared = sorted(user_emotions & cand_emotions)

        results.append({
            "username": candidate["username"],
            "display_name": candidate.get("display_name"),
            "personality_type": candidate.get("personality_type"),
            "similarity": round(sim, 3),
            "shared_emotions": shared,
            "shared_count": len(shared),
        })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:max_results]


def build_heatmap_data(entries: list[dict]) -> dict:
    """
    Build the emotion x book heatmap matrix for the frontend.

    Returns:
        Dict with books (columns), emotions (rows), and cells (intersections).
    """
    books = []
    for e in entries:
        books.append({
            "entry_id": e["id"],
            "title": e.get("title", ""),
            "author": e.get("author", ""),
            "intensity": e.get("intensity", 5),
        })

    # Build matrix
    cells = []
    active_emotions = set()
    for e in entries:
        for emo in e["emotions"]:
            if emo in VALID_EMOTION_IDS:
                active_emotions.add(emo)
                cells.append({
                    "entry_id": e["id"],
                    "emotion_id": emo,
                    "intensity": e.get("intensity", 5),
                })

    return {
        "books": books,
        "active_emotions": sorted(active_emotions),
        "cells": cells,
        "total_books": len(books),
        "total_emotions": len(active_emotions),
    }

def generate_recap(
    month_entries: list[dict],
    prior_entries: list[dict],
    current_personality: str | None,
) -> dict:
    """
    Generate a monthly recap from entries logged in that month.

    Args:
        month_entries: Entries created during the target month.
        prior_entries: All entries BEFORE the target month (for shift detection).
        current_personality: User's current personality_type.

    Returns:
        Dict with recap data.
    """
    if not month_entries:
        return {
            "books_logged": 0,
            "avg_intensity": 0.0,
            "top_emotions": [],
            "most_intense_book": None,
            "dominant_emotion": None,
            "new_emotions": [],
            "personality_shift": {
                "previous_type": None,
                "current_type": current_personality,
                "shifted": False,
            },
            "books": [],
        }

    # Books list
    books = [
        {
            "title": e.get("title", ""),
            "author": e.get("author"),
            "intensity": e.get("intensity", 5),
            "emotions": e.get("emotions", []),
        }
        for e in month_entries
    ]

    # Average intensity
    intensities = [e.get("intensity", 5) for e in month_entries]
    avg_intensity = sum(intensities) / len(intensities)

    # Most intense book
    most_intense = max(month_entries, key=lambda e: e.get("intensity", 0))

    # Emotion frequency for this month
    month_freq = Counter()
    for e in month_entries:
        for emo in e["emotions"]:
            if emo in VALID_EMOTION_IDS:
                month_freq[emo] += 1

    top_emotions = [
        {"emotion_id": emo, "count": count}
        for emo, count in month_freq.most_common(5)
    ]

    dominant = month_freq.most_common(1)[0][0] if month_freq else None

    # New emotions — tagged this month but never before
    prior_emotions = set()
    for e in prior_entries:
        for emo in e["emotions"]:
            if emo in VALID_EMOTION_IDS:
                prior_emotions.add(emo)

    month_emotions = set(month_freq.keys())
    new_emotions = sorted(month_emotions - prior_emotions)

    # Personality shift detection
    prior_personality = None
    if len(prior_entries) >= 3:
        prior_result = calculate_personality(prior_entries)
        if prior_result.get("personality"):
            prior_personality = prior_result["personality"]["name"]

    shifted = (
        prior_personality is not None
        and current_personality is not None
        and prior_personality != current_personality
    )

    return {
        "books_logged": len(month_entries),
        "avg_intensity": round(avg_intensity, 1),
        "top_emotions": top_emotions,
        "most_intense_book": {
            "title": most_intense.get("title", ""),
            "author": most_intense.get("author"),
            "intensity": most_intense.get("intensity", 0),
            "emotions": most_intense.get("emotions", []),
        },
        "dominant_emotion": dominant,
        "new_emotions": new_emotions,
        "personality_shift": {
            "previous_type": prior_personality,
            "current_type": current_personality,
            "shifted": shifted,
        },
        "books": books,
    }