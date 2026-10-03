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

# Maps the engine's internal personality ids → the public "bible" slugs stored on
# DNA snapshots. The eight original types keep the slugs they shipped with so
# existing snapshots stay continuous, with one fix: `awe_chaser` moves to the
# World-Diver, the type that actually anchors on awe (the Emotional Archaeologist
# held no awe at all, so "The Awe Chaser" could never be triggered by wonder).
DNA_TYPE_SLUG_MAP: dict[str, str] = {
    "grief_romantic":          "grief_romantic",
    "control_intellectual":    "chaos_cartographer",
    "soft_masochist":          "soft_masochist",
    "comfort_architect":       "comfort_architect",
    "midnight_arsonist":       "rage_archivist",
    "quiet_witness":           "tender_witness",
    "obsessive_romantic":      "two_am_scholar",
    "emotional_archaeologist": "emotional_archaeologist",
    "world_diver":             "awe_chaser",
    "adrenaline_seeker":       "adrenaline_seeker",
    "sunshine_romantic":       "sunshine_romantic",
    "discerning_reader":       "discerning_reader",
}

BIBLE_DNA_SLUGS = frozenset(DNA_TYPE_SLUG_MAP.values())


def dna_type_slug_for(engine_id: str | None) -> str | None:
    if not engine_id:
        return None
    return DNA_TYPE_SLUG_MAP.get(engine_id)


# Personality "fingerprints" over the canonical 21-feeling vocabulary (v3).
#
# INVARIANT: every slug in primary_emotions / anti_emotions MUST be a canonical
# VALID_SLUGS value (see tests/test_dna_engine.py::test_personality_slugs_are_canonical).
# Every one of the 21 feelings anchors at least one type
# (test_every_experiential_emotion_is_used_somewhere).
#
# Rebuilt in Stage 3 of the Emotion & DNA rework. Tested before shipping against
# 58 reading histories written blind from the descriptions below (89.7% labelled
# as expected: clear readers 11/11, messy 21/22, between-two 9/10) and a 12,000-
# reader simulated population with a calibrated baseline (every type 4.6–13.1%).
#
# The invariants all still hold, and still matter:
#   - No two types share more than ONE primary (P1-5).
#   - Every type carries exactly TWO anti_emotions (P1-5).
#   - NO FREE_ANTI (DNA2): every anti must clear ANTI_FLOOR_RATE in BASELINE_VECTOR.
#     `comfort` was the anti for four types in the first draft and failed this;
#     the antis below are spread across well-tagged feelings instead.
#
# The Discerning Reader is not in this list. It is assigned from verdicts, not
# feelings (see dna_signals.classify_reader), so it takes no part in the feeling
# fingerprints or the fairness maths.
PERSONALITY_TYPES = [
    {
        "id": "grief_romantic",
        "name": "The Grief Romantic",
        # The shareable card speaks as the reader (first person, under 60
        # characters): what they'd say out loud, and the blind spot admitted.
        "article": "a",
        "share_line": "Loss isn't my enemy. Numbness is.",
        "red_flag": "I avoid neat happy endings",
        "description": "You seek books that break your heart because feeling deeply is how you know you're alive. Loss isn't your enemy — numbness is.",
        "primary_emotions": ["grief", "catharsis", "haunted"],
        # Bittersweet, never breezy: joy and laughter are what this reader passes on.
        "anti_emotions": ["joy", "amusement"],
        "blind_spots": ["You avoid books with neat happy endings", "You mistake emotional pain for depth"],
        "comfort_tropes": ["Unrequited love", "Beautiful suffering", "Bittersweet endings"],
        "color": "#3A5A6B",
        "glyph": "◈",
    },
    {
        "id": "control_intellectual",
        "name": "The Control-Seeking Intellectual",
        "article": "a",
        "share_line": "If it scares me, I read until it doesn't.",
        "red_flag": "I'd rather analyse a feeling than have it",
        "description": "You read to master what unsettles you. Understanding is your armor, and every book is a new piece of territory mapped.",
        "primary_emotions": ["insight", "dread", "awe"],
        "anti_emotions": ["grief", "catharsis"],  # they resist vulnerability, and being emotionally undone
        "blind_spots": ["You intellectualize emotions instead of feeling them", "You abandon books that make you vulnerable"],
        "comfort_tropes": ["Unreliable narrators", "Philosophical fiction", "Systems and structures"],
        "color": "#5A5A8A",
        "glyph": "◇",
    },
    {
        "id": "soft_masochist",
        "name": "The Soft Masochist",
        "article": "a",
        "share_line": "I choose pain on purpose. Not sorrow — teeth.",
        "red_flag": "I don't trust a book that feels too safe",
        # `conflicted` ("I shouldn't love this but I do") is this reader in one
        # phrase: drawn to the book that comes at them, and aware of it.
        "description": "You choose pain on purpose. Not sorrow — teeth. You're drawn to the book that comes at you over the one that holds you.",
        "primary_emotions": ["rage", "conflicted", "desire"],
        "anti_emotions": ["joy", "hope"],
        "blind_spots": ["You equate suffering with authenticity", "You distrust books that feel too safe"],
        "comfort_tropes": ["Dark romance", "Moral ambiguity", "Villains you shouldn't root for"],
        "color": "#6B3A5D",
        "glyph": "◆",
    },
    {
        "id": "comfort_architect",
        "name": "The Comfort Architect",
        "article": "a",
        "share_line": "My bookshelf isn't a collection. It's a home.",
        "red_flag": "I re-read instead of risking something new",
        "description": "You build emotional safety through stories. Your bookshelf isn't a collection — it's a home you can always return to.",
        # Found family and the book that leaves you hopeful: comfort + attachment + hope.
        "primary_emotions": ["comfort", "attachment", "hope"],
        "anti_emotions": ["rage", "dread"],
        "blind_spots": ["You avoid books that might destabilize you", "You re-read instead of risking new things"],
        "comfort_tropes": ["Found family", "Slow-burn romance", "Cozy settings"],
        "color": "#7A8B6F",
        "glyph": "○",
    },
    {
        "id": "midnight_arsonist",
        "name": "The Midnight Arsonist",
        "article": "a",
        "share_line": "I read to set fire to my own beliefs.",
        "red_flag": "I call gentle books boring",
        "description": "You read like you're setting fire to your own beliefs. Comfort zones are for people who haven't found the right book yet.",
        "primary_emotions": ["amusement", "rage", "insight"],
        "anti_emotions": ["attachment", "swoon"],  # dismisses the gentle and the sweet
        "blind_spots": ["You conflate discomfort with growth", "You dismiss gentle books as boring"],
        "comfort_tropes": ["Boundary-pushing fiction", "Satire", "Provocative themes"],
        "color": "#C47A3A",
        "glyph": "△",
    },
    {
        "id": "quiet_witness",
        "name": "The Quiet Witness",
        "article": "a",
        "share_line": "Books are where I stop performing.",
        "red_flag": "I observe more than I feel",
        "description": "You absorb everything and process in silence. Books are your confessional — the only place you don't perform.",
        # Tenderness merged into comfort, so the introspective triple is now
        # being seen + memory + the sentence itself.
        "primary_emotions": ["recognition", "nostalgia", "beauty"],
        "anti_emotions": ["thrill", "amusement"],  # not here for pace, not here for laughs
        "blind_spots": ["You observe more than you feel", "You use reading to avoid confrontation"],
        "comfort_tropes": ["Introspective narrators", "Literary fiction", "Quiet revelations"],
        "color": "#B8964E",
        "glyph": "□",
    },
    {
        "id": "obsessive_romantic",
        "name": "The Obsessive Romantic",
        "article": "an",
        "share_line": "I don't read books. I fall into them.",
        "red_flag": "I quit books I can't fall in love with",
        "description": "You don't read books — you fall into them. Every story is a love affair, and you don't do casual.",
        "primary_emotions": ["desire", "longing", "attachment"],
        "anti_emotions": ["amusement", "joy"],  # they cannot do casual
        "blind_spots": ["You abandon books you can't fall in love with", "You chase the high of a new obsession"],
        "comfort_tropes": ["Consuming love stories", "Immersive worlds", "Characters you'd die for"],
        "color": "#C4553A",
        "glyph": "♡",
    },
    {
        "id": "emotional_archaeologist",
        "name": "The Emotional Archaeologist",
        "article": "an",
        "share_line": "Every book is a dig for a buried part of me.",
        "red_flag": "I find meaning even where there's none",
        "description": "You dig into stories looking for buried parts of yourself. Every book is an excavation site.",
        # Being seen, the ache underneath, and what it teaches you about yourself.
        # (It shared catharsis with the Grief Romantic in the first draft and stole
        # grief readers; insight separates the dig from the wound.)
        "primary_emotions": ["recognition", "longing", "insight"],
        "anti_emotions": ["amusement", "rage"],
        "blind_spots": ["You over-analyze what you read", "You search for meaning even when there's none"],
        "comfort_tropes": ["Psychological depth", "Identity exploration", "Hidden truths"],
        "color": "#7A5A9B",
        "glyph": "◎",
    },
    {
        "id": "world_diver",
        "name": "The World-Diver",
        "article": "a",
        "share_line": "I read to live somewhere else.",
        "red_flag": "I skip quiet books that don't take me anywhere",
        "description": "You read to live somewhere else. Vast worlds, long histories, maps in the front pages — you want to be swallowed whole and come back glowing.",
        "primary_emotions": ["awe", "thrill", "joy"],
        "anti_emotions": ["recognition", "grief"],  # not here to be mirrored, not here to mourn
        "blind_spots": ["You escape into worlds instead of looking at your own", "You skip the quiet books that don't transport you"],
        "comfort_tropes": ["Epic fantasy", "Deep worldbuilding", "Maps and histories"],
        "color": "#3A7A8C",
        "glyph": "✦",
    },
    {
        "id": "adrenaline_seeker",
        "name": "The Adrenaline Seeker",
        "article": "an",
        "share_line": "If it doesn't grab me by the collar, I'm out.",
        "red_flag": "I rush past the quiet parts",
        "description": "You read for the pulse. Twists, fear, the 3am chapter — a book has to grab you by the collar and not let go.",
        "primary_emotions": ["thrill", "dread", "shock"],
        "anti_emotions": ["recognition", "beauty"],  # plot over prose, story over self
        "blind_spots": ["You rush past the quiet parts", "You trust a twist more than a feeling"],
        "comfort_tropes": ["Thrillers", "Horror", "Twists you didn't see coming"],
        "color": "#8C3A3A",
        "glyph": "✶",
    },
    {
        "id": "sunshine_romantic",
        "name": "The Sunshine Romantic",
        "article": "a",
        "share_line": "I believe in love stories. Not embarrassed about it.",
        "red_flag": "I bail when a story turns dark",
        "description": "You read for the swoon. Banter, butterflies and the happy ending you were promised — you believe in love stories and you're not embarrassed about it.",
        "primary_emotions": ["swoon", "joy", "amusement"],
        "anti_emotions": ["grief", "dread"],
        "blind_spots": ["You bail when a story turns dark", "You read for the ending you already know"],
        "comfort_tropes": ["Romcoms", "Banter", "Happily ever after"],
        "color": "#D4876B",
        "glyph": "☼",
    },
]

# Assigned from verdicts, never from feelings (dna_signals.classify_reader).
# Shaped like a personality type so every consumer of `archetype_dict` works
# unchanged; its feeling lists are empty on purpose.
DISCERNING_READER = {
    "id": "discerning_reader",
    "name": "The Discerning Reader",
    # The shareable card speaks as the reader (first person, under 60
    # characters): what they'd say out loud, and the blind spot admitted.
    "article": "a",
    "share_line": "Most books don't reach me. The ones that do, matter.",
    "red_flag": "I decide a book has failed before it's finished",
    "description": "You have high standards. Most books don't reach you — and when one does, it matters.",
    "primary_emotions": [],
    "anti_emotions": [],
    "blind_spots": ["You decide a book has failed you before it has finished", "You might be reading the wrong shelf, not the wrong books"],
    "comfort_tropes": ["Books that earn it", "Hidden gems", "Writers who never waste a page"],
    "color": "#6E6E6E",
    "glyph": "◌",
}


def _archetype_table_rev() -> str:
    """A short hash of every type's id + primaries + antis.

    Changes the moment anyone edits the fingerprint of any archetype. Stored on
    each DNA snapshot so the "your DNA shifted" notification can tell an actual
    reader change from an engine re-anchor: if the previous snapshot was written
    under a different rev, a new label is at least partly our doing, not theirs,
    and the notification is suppressed (the snapshot is still taken).
    """
    import hashlib

    payload = ";".join(
        f"{t['id']}:{','.join(t['primary_emotions'])}|{','.join(t['anti_emotions'])}"
        for t in PERSONALITY_TYPES
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


ARCHETYPE_TABLE_REV = _archetype_table_rev()


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
    shift: dict | None = None,
) -> dict:
    """
    Generate a monthly recap from entries logged in that month.

    Args:
        month_entries: Entries read (finished_at, else logged) during the target month.
        prior_entries: All entries BEFORE the target month (for shift detection).
        current_personality: User's current personality_type.
        shift: The archetype change dated in this month, from the replayed DNA
            ({previous_type, current_type, shifted}), or None. The caller owns
            this; it used to be re-derived here from the legacy engine, which
            disagreed with the live one 42.7% of the time.

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
            "personality_shift": shift or {
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

    shift = shift or {"previous_type": None, "current_type": current_personality, "shifted": False}

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
        "personality_shift": shift,
        "books": books,
    }