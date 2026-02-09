"""
Book DNA Engine — The Secret Sauce

Calculates a user's reading personality based on their emotional history.
Phase 1: Rule-based pattern matching with weighted scoring.
Phase 2 (future): Clustering from real user data.
Phase 3 (future): AI-powered with Claude API.
"""

from collections import Counter
from datetime import datetime, timezone

from app.utils.emotions import VALID_EMOTION_IDS

PERSONALITY_TYPES = [
    {
        "id": "grief_romantic",
        "name": "The Grief Romantic",
        "description": "You seek books that break your heart because feeling deeply is how you know you're alive. Loss isn't your enemy — numbness is.",
        "primary_emotions": ["grief", "healing", "seen"],
        "anti_emotions": ["nothing", "chaos"],
        "blind_spots": ["You avoid books with neat happy endings", "You mistake emotional pain for depth"],
        "comfort_tropes": ["Unrequited love", "Beautiful suffering", "Bittersweet endings"],
        "color": "#3A5A6B",
        "glyph": "◈",
    },
    {
        "id": "control_intellectual",
        "name": "The Control-Seeking Intellectual",
        "description": "You read to master the chaos. Understanding is your armor, and every book is a new piece of territory mapped.",
        "primary_emotions": ["dread", "nothing", "chaos"],
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
        "primary_emotions": ["comfort", "healing", "seen"],
        "anti_emotions": ["rage", "chaos"],
        "blind_spots": ["You avoid books that might destabilize you", "You re-read instead of risking new things"],
        "comfort_tropes": ["Found family", "Slow-burn romance", "Cozy settings"],
        "color": "#7A8B6F",
        "glyph": "○",
    },
    {
        "id": "midnight_arsonist",
        "name": "The Midnight Arsonist",
        "description": "You read like you're setting fire to your own beliefs. Comfort zones are for people who haven't found the right book yet.",
        "primary_emotions": ["chaos", "2am", "rage"],
        "anti_emotions": ["comfort", "nothing"],
        "blind_spots": ["You conflate discomfort with growth", "You dismiss gentle books as boring"],
        "comfort_tropes": ["Boundary-pushing fiction", "Experimental structure", "Provocative themes"],
        "color": "#C47A3A",
        "glyph": "△",
    },
    {
        "id": "quiet_witness",
        "name": "The Quiet Witness",
        "description": "You absorb everything and process in silence. Books are your confessional — the only place you don't perform.",
        "primary_emotions": ["seen", "healing", "dread"],
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
        "anti_emotions": ["nothing", "dread"],
        "blind_spots": ["You abandon books you can't fall in love with", "You chase the high of a new obsession"],
        "comfort_tropes": ["Consuming love stories", "Immersive worlds", "Characters you'd die for"],
        "color": "#C4553A",
        "glyph": "♡",
    },
    {
        "id": "emotional_archaeologist",
        "name": "The Emotional Archaeologist",
        "description": "You dig into stories looking for buried parts of yourself. Every book is an excavation site.",
        "primary_emotions": ["dread", "seen", "healing"],
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
    if not entries:
        return {
            "personality": None,
            "scores": {},
            "emotion_frequency": {},
            "emotion_intensity": {},
            "top_emotions": [],
            "blind_spots": [],
            "comfort_tropes": [],
        }

    # === 1. Count emotion frequency ===
    emotion_freq = Counter()
    for entry in entries:
        for emo in entry["emotions"]:
            emotion_freq[emo] += 1

    # === 2. Calculate intensity-weighted emotions ===
    emotion_intensity = {}
    emotion_counts = {}
    for entry in entries:
        intensity = entry.get("intensity", 5)
        for emo in entry["emotions"]:
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
            recency_weights[emo] = recency_weights.get(emo, 0) + weight

    # === 4. Build co-occurrence matrix ===
    co_occurrence = Counter()
    for entry in entries:
        emos = sorted(entry["emotions"])
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
    missing_emotions = all_emotions - used_emotions
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

    Args:
        entries: List of dicts with emotions, intensity, created_at, finished_at.

    Returns:
        Dict with reading stats.
    """
    if not entries:
        return {
            "total_books": 0,
            "avg_intensity": 0,
            "highest_intensity_book": None,
            "most_common_emotion": None,
            "emotion_diversity": 0,
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
        all_emotions.extend(e["emotions"])
    emotion_counter = Counter(all_emotions)
    most_common = emotion_counter.most_common(1)

    # Emotion diversity (unique emotions / total possible)
    unique_emotions = len(set(all_emotions))
    diversity = unique_emotions / len(VALID_EMOTION_IDS)

    # Books per month
    dates = [e["created_at"] for e in entries if e.get("created_at")]
    if len(dates) >= 2:
        span = (max(dates) - min(dates)).days or 1
        books_per_month = total / (span / 30)
    else:
        books_per_month = total

    return {
        "total_books": total,
        "avg_intensity": round(avg_intensity, 1),
        "highest_intensity_book": {
            "title": max_entry.get("title", ""),
            "intensity": max_entry.get("intensity", 0),
        },
        "most_common_emotion": most_common[0][0] if most_common else None,
        "most_common_emotion_count": most_common[0][1] if most_common else 0,
        "emotion_diversity": round(diversity, 2),
        "unique_emotions_used": unique_emotions,
        "total_emotions_possible": len(VALID_EMOTION_IDS),
        "books_per_month": round(books_per_month, 1),
    }


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
