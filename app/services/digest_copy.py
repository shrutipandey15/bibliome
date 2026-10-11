"""Every word the weekly digest says, in one file, for Shruti to write.

Nothing here is generated. Placeholders are marked ``TODO(shruti)``; the digest
refuses to send while any remain (see ``missing_copy``), so a half-written digest
can never reach a reader. The admin preview shows them as they are, so drafts
can be read in place.

Template fields, filled by digest_service. Only the ones listed for a string are
available to it:
  {name}     the reader's display name (or username)
  {book}     a book title from their shelf
  {emotion}  a feeling's display name ("heartbreak", "awe")
  {when}     "six weeks", "three months", "a year"

Rules for anything written here (the tests check the first one):
  - Never count zero, never "didn't read", never "miss you". A quiet week is a
    fallow week.
  - Ids are permanent. A sent letter or subject is remembered by its id, so
    editing the text is fine, but renaming an id makes it "new" again.
"""

# ── Subject lines, per lead section ───────────────────────────────────────────
# The notification's headline and the digest page's title. No subject repeats for a reader within 8 weeks, so each lead needs several.
SUBJECTS: dict[str, list[dict]] = {
    "past_self": [
        {"id": "ps1", "text": "Something you wrote {when} ago"},
        {"id": "ps2", "text": "You left yourself a note about {book}"},
        {"id": "ps3", "text": "TODO(shruti): past-self subject"},
    ],
    "archetype_letter": [
        {"id": "al1", "text": "A letter, {name}"},
        {"id": "al2", "text": "Someone who knows your shelf wrote to you"},
        {"id": "al3", "text": "TODO(shruti): archetype letter subject"},
    ],
    "weather": [
        {"id": "we1", "text": "This week's forecast: {emotion}"},
        {"id": "we2", "text": "Your shelf's weather, {name}"},
        {"id": "we3", "text": "TODO(shruti): weather subject"},
    ],
    "while_away": [
        {"id": "wa1", "text": "While you weren't looking"},
        {"id": "wa2", "text": "A year ago this week: {book}"},
        {"id": "wa3", "text": "TODO(shruti): while-you-weren't-looking subject"},
    ],
    "emotion_of_week": [
        {"id": "ew1", "text": "This week's feeling is {emotion}"},
        {"id": "ew2", "text": "Which book gave you {emotion}?"},
        {"id": "ew3", "text": "TODO(shruti): emotion-of-the-week subject"},
    ],
    "drift": [
        {"id": "dr1", "text": "Something in your DNA moved"},
        {"id": "dr2", "text": "You've been leaning somewhere new"},
        {"id": "dr3", "text": "TODO(shruti): drift subject"},
    ],
    "dna_waiting": [
        {"id": "dw1", "text": "Someone is forming here"},
        {"id": "dw2", "text": "We're starting to see who you are"},
        {"id": "dw3", "text": "TODO(shruti): DNA-waiting subject"},
        {"id": "dw4", "text": "TODO(shruti): DNA-waiting subject"},
    ],
    "revealed": [
        {"id": "rv1", "text": "You've been revealed"},
    ],
}

# ── Section titles (small caps label above each section) ──────────────────────
TITLES: dict[str, str] = {
    "past_self": "a line from your past self",
    "archetype_letter": "a letter from your archetype",
    "weather": "your shelf's weather",
    "while_away": "while you weren't looking",
    "emotion_of_week": "feeling of the week",
    "drift": "the drift",
    "dna_waiting": "your DNA is waiting",
    "revealed": "you've been revealed",
}

# ── Main button label, per lead section ───────────────────────────────────────
BUTTONS: dict[str, str] = {
    "past_self": "Read it again",
    "archetype_letter": "Open your letter",
    "weather": "See the full forecast",
    "while_away": "Open the book",
    "emotion_of_week": "See your closest book",
    "drift": "See where you're leaning",
    "dna_waiting": "Add a book",
    "revealed": "Meet yourself",
}

# ── 1. A line from your past self ─────────────────────────────────────────────
PAST_SELF_TEASE = "{when} ago you wrote something about {book}."
PAST_SELF_PROMPT = "How does it feel now?"

# ── 2. Archetype letters ──────────────────────────────────────────────────────
# Keyed by archetype id (dna_engine.PERSONALITY_TYPES + DISCERNING_READER), plus
# "unwritten" for readers without an archetype yet. Each reader gets any one
# letter at most once, so more letters per archetype = more weeks of letters.
# Fields: {name}, {book}, {emotion}. tone is one of teasing/tender/dramatic/wry.
ARCHETYPE_LETTERS: dict[str, list[dict]] = {
    "grief_romantic": [
        {"id": "grief_romantic_1", "tone": "tender",
         "text": "Dear {name},\n\nI saw you go back to {book}. You always do this: you find the ache and sit beside it like it's a fire. "
                 "I'm not going to tell you to stop. I'm only going to say that the heart you keep breaking on purpose is the same one that keeps you reading.\n\n"
                 "— The Grief Romantic"},
        {"id": "grief_romantic_2", "tone": "wry",
         "text": "{name},\n\nAnother week, another book that left a mark. At this point {emotion} should be paying rent.\n\n— The Grief Romantic"},
    ],
    "control_intellectual": [{"id": "control_intellectual_1", "tone": "wry", "text": "TODO(shruti): letter"}],
    "soft_masochist": [{"id": "soft_masochist_1", "tone": "teasing", "text": "TODO(shruti): letter"}],
    "comfort_architect": [
        {"id": "comfort_architect_1", "tone": "tender",
         "text": "{name},\n\nYou build rooms out of books and then you live in them. {book} is one of the warm ones. Nobody gets to tell you that's small.\n\n— The Comfort Architect"},
    ],
    "midnight_arsonist": [{"id": "midnight_arsonist_1", "tone": "dramatic", "text": "TODO(shruti): letter"}],
    "quiet_witness": [{"id": "quiet_witness_1", "tone": "tender", "text": "TODO(shruti): letter"}],
    "obsessive_romantic": [{"id": "obsessive_romantic_1", "tone": "dramatic", "text": "TODO(shruti): letter"}],
    "emotional_archaeologist": [{"id": "emotional_archaeologist_1", "tone": "wry", "text": "TODO(shruti): letter"}],
    "world_diver": [{"id": "world_diver_1", "tone": "dramatic", "text": "TODO(shruti): letter"}],
    "adrenaline_seeker": [{"id": "adrenaline_seeker_1", "tone": "teasing", "text": "TODO(shruti): letter"}],
    "sunshine_romantic": [{"id": "sunshine_romantic_1", "tone": "teasing", "text": "TODO(shruti): letter"}],
    "discerning_reader": [{"id": "discerning_reader_1", "tone": "wry", "text": "TODO(shruti): letter"}],
    "unwritten": [
        {"id": "unwritten_1", "tone": "tender",
         "text": "{name},\n\nI don't have a name yet. I'm the reader you're turning into, one book at a time. Bring me one more and I'll come a little clearer.\n\n— The Unwritten"},
        {"id": "unwritten_2", "tone": "teasing", "text": "TODO(shruti): letter from the Unwritten"},
    ],
}

# ── 3. Weather report ─────────────────────────────────────────────────────────
# Fields: {e1} {e2} {e3} (feelings, strongest first), {p} (a percentage).
WEATHER: list[dict] = [
    {"id": "w1", "text": "Overcast with {e1}. {p}% chance of {e2}, scattered {e3} by Thursday."},
    {"id": "w2", "text": "A front of {e1} moving in. Expect {e2} by midweek, {p}% chance of {e3} after dark."},
    {"id": "w3", "text": "TODO(shruti): weather template using {e1} {e2} {e3} {p}"},
]

# ── 4. While you weren't looking ──────────────────────────────────────────────
FALLOW_OPENERS: list[str] = [
    "A fallow week.",
    "A fallow week. The field is resting.",
    "TODO(shruti): fallow-week opener",
]
WHILE_AWAY_TEASE = "Something happened on your shelf while you weren't looking."
# Fields: {book}, {when} ("a year", "two years").
ANNIVERSARY = "{when} ago this week, you finished {book}."

# ── 5. Feeling of the week ────────────────────────────────────────────────────
# Two lines each. Every feeling in app.utils.emotions.EMOTIONS must have one.
EMOTION_ESSAYS: dict[str, str] = {
    "grief": "Heartbreak is the price of having been somewhere real.\nThe books that cost us something are the ones we keep.",
    "catharsis": "TODO(shruti): two lines on catharsis",
    "haunted": "TODO(shruti): two lines on haunted",
    "thrill": "TODO(shruti): two lines on page-turner",
    "dread": "TODO(shruti): two lines on tension",
    "shock": "TODO(shruti): two lines on shock",
    "rage": "TODO(shruti): two lines on anger",
    "comfort": "Comfort is not the absence of feeling.\nIt is a book that knows where you are and stays.",
    "attachment": "TODO(shruti): two lines on attachment",
    "nostalgia": "TODO(shruti): two lines on nostalgia",
    "joy": "TODO(shruti): two lines on joy",
    "amusement": "TODO(shruti): two lines on laughter",
    "hope": "TODO(shruti): two lines on hope",
    "swoon": "TODO(shruti): two lines on swoon",
    "desire": "TODO(shruti): two lines on desire",
    "longing": "TODO(shruti): two lines on longing",
    "conflicted": "TODO(shruti): two lines on conflicted",
    "awe": "Awe is the moment a book is bigger than the room you're reading it in.\nYou close it and the ceiling is higher.",
    "recognition": "TODO(shruti): two lines on recognition",
    "insight": "TODO(shruti): two lines on insight",
    "beauty": "TODO(shruti): two lines on beauty",
}
EMOTION_CLOSEST = "The closest book on your shelf to this feeling: {book}."
EMOTION_CLOSEST_TEASE = "One book on your shelf is closest to it."
EMOTION_ASK = "Which book on your shelf gave you this?"

# ── 7. The drift ──────────────────────────────────────────────────────────────
# Fields: {degree} (one of DRIFT_DEGREES), {emotion}.
DRIFT_MOVED = "You've been leaning {degree} toward {emotion}."
DRIFT_DEGREES = ("slightly more", "noticeably more", "much more")
DRIFT_TEASE = "Something in your DNA moved."
# Fields: {emotion} (the nearest archetype's leading feeling), {archetype}.
DRIFT_STILL = "Your DNA held still. One more book full of {emotion} would tip you toward {archetype}."

# ── 9. Your DNA is waiting ────────────────────────────────────────────────────
WAITING_HEADLINE = "Someone is forming here."
# Fields: {n} spelled out ("two"), {s} plural suffix.
WAITING_PROGRESS = "{n} more book{s} and we'll know who you are as a reader."
# Fields: {emotion}, {k} spelled out, {total} spelled out.
WAITING_HINT = "So far your shelf leans toward {emotion}. That narrows it to {k} of {total} readers."
# One more clue is revealed (in the app) for each week the shelf stays empty.
WAITING_CLUE_TEASE = "A new clue about the reader you'll be is waiting for you."
WAITING_CLUES: list[str] = [
    "Every reader here started with one book. Usually the one they can't stop thinking about.",
    "TODO(shruti): second clue",
    "TODO(shruti): third clue",
    "TODO(shruti): fourth clue",
    "TODO(shruti): fifth clue",
    "TODO(shruti): sixth clue",
]
REVEALED = "The blur is gone. You're {archetype}."
REVEALED_TEASE = "The blur is gone."

# ── The one easy action (under 10 seconds) ────────────────────────────────────
ACTIONS: dict[str, str] = {
    "tag_emotion": "Tag one book with {emotion}",
    "retag": "Re-tag {book} in one tap",
    "add_book": "Log the book you're reading now",
}

NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven", "twelve"]


def missing_copy(emotion_slugs, archetype_ids) -> list[str]:
    """Everything still unwritten. Empty means the digest may send."""
    missing = []

    def _check(where: str, text: str | None):
        if not text or "TODO(shruti)" in text:
            missing.append(where)

    for slug in emotion_slugs:
        _check(f"EMOTION_ESSAYS[{slug}]", EMOTION_ESSAYS.get(slug))
    for aid in [*archetype_ids, "unwritten"]:
        letters = ARCHETYPE_LETTERS.get(aid) or [{"id": f"{aid}?", "text": None}]
        for letter in letters:
            _check(f"ARCHETYPE_LETTERS[{aid}].{letter['id']}", letter["text"])
    for lead, subjects in SUBJECTS.items():
        for s in subjects:
            _check(f"SUBJECTS[{lead}].{s['id']}", s["text"])
    for w in WEATHER:
        _check(f"WEATHER.{w['id']}", w["text"])
    for i, line in enumerate(FALLOW_OPENERS):
        _check(f"FALLOW_OPENERS[{i}]", line)
    for i, line in enumerate(WAITING_CLUES):
        _check(f"WAITING_CLUES[{i}]", line)
    return missing
