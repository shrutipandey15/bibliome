"""
Bibliome Engine — The Secret Sauce

Calculates a user's reading personality based on their emotional history.
Phase 1: Rule-based pattern matching with weighted scoring.
Phase 2 (future): Clustering from real user data.
Phase 3 (future): AI-powered with Claude API.
"""

from collections import Counter
from datetime import datetime, timezone

from app.utils.emotions import VALID_EMOTION_IDS, canonicalize


def _canonical_emotions(emotions) -> list[str]:
    """Map a list of raw emotion slugs to canonical slugs, dropping unknowns.

    Legacy pre-cutover slugs (e.g. ``healing``, ``2am``) are remapped via
    ``canonicalize`` so historical rows still count toward DNA scoring instead of
    being silently dropped — this is what keeps the engine in agreement with the
    Mirror/calendar services that already canonicalize.
    """
    out = []
    for emo in emotions or []:
        canon = canonicalize(emo)
        if canon is not None:
            out.append(canon)
    return out

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


# Personality "fingerprints" over the canonical 18-emotion vocabulary.
#
# INVARIANT: every slug in primary_emotions / anti_emotions MUST be a canonical
# VALID_SLUGS value (see tests/test_dna_engine.py::test_personality_slugs_are_canonical).
# Fingerprints were migrated to the 18-emotion vocabulary:
#   wit→amusement, chaos→confusion, two_am→longing (the old removed slugs).
# Archetypes are anchored only on *experiential* emotions; the "It lost me" family
# (boredom/revulsion/confusion/indifference) appears solely as anti_emotions —
# they describe a book failing you, not a reading identity. Across the 8 types,
# every experiential emotion is used as a primary at least once
# (test_every_experiential_emotion_is_used_somewhere).
#
# Two further invariants, both added after simulation found them violated (P1-5):
#   - No two types may share more than ONE primary. Sharing two makes a tie that
#     list order silently resolves, which is not a decision anyone made.
#   - Every type carries exactly TWO anti_emotions. A third is a permanent
#     handicap that shows up as that type under-winning at population scale.
PERSONALITY_TYPES = [
    {
        "id": "grief_romantic",
        "name": "The Grief Romantic",
        "description": "You seek books that break your heart because feeling deeply is how you know you're alive. Loss isn't your enemy — numbness is.",
        "primary_emotions": ["grief", "catharsis", "devastation"],
        "anti_emotions": ["comfort", "amusement"],  # they reject safe comfort and the merely clever
        "blind_spots": ["You avoid books with neat happy endings", "You mistake emotional pain for depth"],
        "comfort_tropes": ["Unrequited love", "Beautiful suffering", "Bittersweet endings"],
        "color": "#3A5A6B",
        "glyph": "◈",
    },
    {
        "id": "control_intellectual",
        "name": "The Control-Seeking Intellectual",
        "description": "You read to master what unsettles you. Understanding is your armor, and every book is a new piece of territory mapped.",
        "primary_emotions": ["recognition", "dread", "awe"],
        "anti_emotions": ["confusion", "catharsis"],  # they resist being lost, and being emotionally undone
        "blind_spots": ["You intellectualize emotions instead of feeling them", "You abandon books that make you vulnerable"],
        "comfort_tropes": ["Unreliable narrators", "Philosophical fiction", "Systems and structures"],
        "color": "#5A5A8A",
        "glyph": "◇",
    },
    {
        "id": "soft_masochist",
        "name": "The Soft Masochist",
        # Re-anchored (P1-5). This used to be grief + devastation, which is two of
        # The Grief Romantic's three primaries — a reader tagging only those two
        # scored an exact 1.0 tie between the pair, resolved by list order. The
        # difference between them was never sorrow anyway: it's whether the book
        # is doing the hurting on purpose. Anchored on rage and dread, it is.
        "description": "You choose pain on purpose. Not sorrow — teeth. You trust the book that comes at you over the one that holds you.",
        "primary_emotions": ["rage", "dread", "devastation"],
        "anti_emotions": ["comfort", "joy"],
        "blind_spots": ["You equate suffering with authenticity", "You distrust books that feel too safe"],
        "comfort_tropes": ["Tragic love", "Moral ambiguity", "Devastating plot twists"],
        "color": "#6B3A5D",
        "glyph": "◆",
    },
    {
        "id": "comfort_architect",
        "name": "The Comfort Architect",
        "description": "You build emotional safety through stories. Your bookshelf isn't a collection — it's a home you can always return to.",
        "primary_emotions": ["comfort", "longing", "tenderness"],
        # Two, not three (P1-5). Carrying a third penalty made this the only type
        # paying an extra subtraction on every scoring pass: it won 5.4% of 5,000
        # simulated readers against the ~12.5% an unbiased eight-way split gives.
        # The handicap was in the data, not in the readers.
        "anti_emotions": ["rage", "dread"],
        "blind_spots": ["You avoid books that might destabilize you", "You re-read instead of risking new things"],
        "comfort_tropes": ["Found family", "Slow-burn romance", "Cozy settings"],
        "color": "#7A8B6F",
        "glyph": "○",
    },
    {
        "id": "midnight_arsonist",
        "name": "The Midnight Arsonist",
        "description": "You read like you're setting fire to your own beliefs. Comfort zones are for people who haven't found the right book yet.",
        "primary_emotions": ["amusement", "awe", "rage"],
        "anti_emotions": ["comfort", "boredom"],
        "blind_spots": ["You conflate discomfort with growth", "You dismiss gentle books as boring"],
        "comfort_tropes": ["Boundary-pushing fiction", "Experimental structure", "Provocative themes"],
        "color": "#C47A3A",
        "glyph": "△",
    },
    {
        "id": "quiet_witness",
        "name": "The Quiet Witness",
        "description": "You absorb everything and process in silence. Books are your confessional — the only place you don't perform.",
        "primary_emotions": ["tenderness", "awe", "nostalgia"],
        "anti_emotions": ["rage", "revulsion"],
        "blind_spots": ["You observe more than you feel", "You use reading to avoid confrontation"],
        "comfort_tropes": ["Introspective narrators", "Literary fiction", "Quiet revelations"],
        "color": "#B8964E",
        "glyph": "□",
    },
    {
        "id": "obsessive_romantic",
        "name": "The Obsessive Romantic",
        "description": "You don't read books — you fall into them. Every story is a love affair, and you don't do casual.",
        "primary_emotions": ["desire", "comfort", "longing"],
        "anti_emotions": ["dread", "indifference"],  # they cannot do casual or detached
        "blind_spots": ["You abandon books you can't fall in love with", "You chase the high of a new obsession"],
        "comfort_tropes": ["Consuming love stories", "Immersive worlds", "Characters you'd die for"],
        "color": "#C4553A",
        "glyph": "♡",
    },
    {
        "id": "emotional_archaeologist",
        "name": "The Emotional Archaeologist",
        "description": "You dig into stories looking for buried parts of yourself. Every book is an excavation site.",
        "primary_emotions": ["longing", "joy", "catharsis"],
        "anti_emotions": ["amusement", "indifference"],
        "blind_spots": ["You over-analyze what you read", "You search for meaning even when there's none"],
        "comfort_tropes": ["Psychological depth", "Identity exploration", "Hidden truths"],
        "color": "#7A5A9B",
        "glyph": "◎",
    },
]


def calculate_personality(entries: list[dict]) -> dict:
    """
    Calculate a user's reading personality from their book entries.

    INTERNAL ONLY. Not the archetype source. ``dna_signals.score_archetype`` is the
    single headline authority; this exists for recap shift detection. Do not wire
    this to any user-visible surface — it gates at 3 books where the real engine
    gates at 5, and on simulated readers the two disagreed 42.7% of the time.

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
        for emo in _canonical_emotions(entry["emotions"]):
            emotion_freq[emo] += 1

    # === 2. Calculate intensity-weighted emotions ===
    emotion_intensity = {}
    emotion_counts = {}
    for entry in entries:
        intensity = entry.get("intensity", 5)
        for emo in _canonical_emotions(entry["emotions"]):
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
        for emo in _canonical_emotions(entry["emotions"]):
            recency_weights[emo] = recency_weights.get(emo, 0) + weight

    # === 4. Build co-occurrence matrix ===
    co_occurrence = Counter()
    for entry in entries:
        # Canonical, de-duplicated emotions per entry
        emos = sorted(set(_canonical_emotions(entry["emotions"])))
        for i in range(len(emos)):
            for j in range(i + 1, len(emos)):
                co_occurrence[(emos[i], emos[j])] += 1

    # === 5. Score each personality type ===
    # None of these terms is bounded, and they do not sum to 100 — the comments
    # here used to claim ranges like "0-40 points", which was never true. The
    # frequency term alone passes 400 for a 50-book reader. Scores are comparable
    # between types for one reader and meaningless between readers, which is
    # another reason this is not the headline engine.
    scores = {}
    for ptype in PERSONALITY_TYPES:
        score = 0.0

        # Frequency: 8 per tagged occurrence, unbounded, grows with library size.
        for emo in ptype["primary_emotions"]:
            freq = emotion_freq.get(emo, 0)
            score += freq * 8

        # Intensity: 1.5 x the mean rating of each primary (so ≤15 per primary).
        for emo in ptype["primary_emotions"]:
            avg_int = avg_intensity.get(emo, 0)
            score += avg_int * 1.5  # High intensity = more points

        # Recency: 2 x the summed 0.5–1.0 position weights, unbounded.
        for emo in ptype["primary_emotions"]:
            score += recency_weights.get(emo, 0) * 2

        # Co-occurrence: 5 per book pairing two primaries, unbounded.
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
            "emotion_counts": {},
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
        # Canonicalize so legacy slugs still count toward stats
        all_emotions.extend(_canonical_emotions(e["emotions"]))
    
    emotion_counter = Counter(all_emotions)
    most_common = emotion_counter.most_common(1)

    # Books tagged with each emotion (deduped per book) — the full ledger the
    # Stats page renders (B5.3). Keys are canonical slugs.
    emotion_book_counts: Counter = Counter()
    for e in entries:
        for slug in set(_canonical_emotions(e["emotions"])):
            emotion_book_counts[slug] += 1

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
        "emotion_counts": dict(emotion_book_counts),
        "emotion_diversity": round(diversity * 100),
        "unique_emotions_used": unique_emotions,
        "total_emotions_possible": len(VALID_EMOTION_IDS),
        "books_per_month": round(books_per_month, 1),
    }


# REMOVED (Phase 5 B5.6): find_twins / build_emotion_vector / cosine_similarity.
# Twin (reader-matching) is parked; its endpoint was O(all public users × entries)
# per request. When Twin is reopened it must use precomputed emotion vectors from
# cached_dna_profile + an offline candidate pipeline (blueprint §Feature 4), not a
# per-request scan. The design notes live in blueprint.md.


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
        for emo in _canonical_emotions(e["emotions"]):
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
        for emo in _canonical_emotions(e["emotions"]):
            month_freq[emo] += 1

    top_emotions = [
        {"emotion_id": emo, "count": count}
        for emo, count in month_freq.most_common(5)
    ]

    dominant = month_freq.most_common(1)[0][0] if month_freq else None

    # New emotions — tagged this month but never before
    prior_emotions = set()
    for e in prior_entries:
        for emo in _canonical_emotions(e["emotions"]):
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