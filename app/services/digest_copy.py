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
        {"id": "ps3", "text": "{name}, you wrote this {when} ago"},
    ],
    "archetype_letter": [
        {"id": "al1", "text": "A letter, {name}"},
        {"id": "al2", "text": "Someone who knows your shelf wrote to you"},
        {"id": "al3", "text": "Your archetype left you a letter"},
    ],
    "weather": [
        {"id": "we1", "text": "This week's forecast: {emotion}"},
        {"id": "we2", "text": "Your shelf's weather, {name}"},
        {"id": "we3", "text": "This week's forecast: mostly {emotion}"},
    ],
    "while_away": [
        {"id": "wa1", "text": "While you weren't looking"},
        {"id": "wa2", "text": "A year ago this week: {book}"},
        {"id": "wa3", "text": "Something happened to {book}"},
    ],
    "emotion_of_week": [
        {"id": "ew1", "text": "This week's feeling is {emotion}"},
        {"id": "ew2", "text": "Which book gave you {emotion}?"},
        {"id": "ew3", "text": "This week belongs to {emotion}"},
    ],
    "drift": [
        {"id": "dr1", "text": "Something in your DNA moved"},
        {"id": "dr2", "text": "You've been leaning somewhere new"},
        {"id": "dr3", "text": "You've been leaning somewhere new"},
    ],
    "dna_waiting": [
        {"id": "dw1", "text": "Someone is forming here"},
        {"id": "dw2", "text": "We're starting to see who you are"},
        {"id": "dw3", "text": "Someone is forming on your shelf"},
        {"id": "dw4", "text": "Your reader DNA is half-written"},
    ],
    "revealed": [
        {"id": "rv1", "text": "You've been revealed"},
    ],
    "weekly_question": [
        {"id": "wq1", "text": "A question for you this week"},
        {"id": "wq2", "text": "What would you answer?"},
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
    "weekly_question": "the weekly question",
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
    "weekly_question": "Answer it",
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
    "control_intellectual": [{"id": "control_intellectual_1", "tone": "wry",
         "text": "Dear {name},\n\nMost people close a book when it gets uncomfortable. You lean in and take notes.\n\nTo you, fear is just a question nobody has answered yet. Keep asking.\n\n— The Control-Seeking Intellectual"}],
    "soft_masochist": [{"id": "soft_masochist_1", "tone": "teasing",
         "text": "Dear {name},\n\nLet the others have their gentle sadness. You want the book that bites back.\n\nThe messy love, the infuriating choice, the character you’d never forgive and can’t stop thinking about. That’s where you feel most awake.\n\n— The Soft Masochist"}],
    "comfort_architect": [
        {"id": "comfort_architect_1", "tone": "tender",
         "text": "{name},\n\nYou build rooms out of books and then you live in them. {book} is one of the warm ones. Nobody gets to tell you that's small.\n\n— The Comfort Architect"},
    ],
    "midnight_arsonist": [{"id": "midnight_arsonist_1", "tone": "dramatic",
         "text": "Dear {name},\n\nYou don’t read to be agreed with. You read to drag your own certainties into the light and see if they survive. And you laugh while it happens.\n\nStrike the next match. Something in you is ready to burn.\n\n— The Midnight Arsonist"}],
    "quiet_witness": [{"id": "quiet_witness_1", "tone": "tender",
         "text": "Dear {name},\n\nNo one is watching here. You can read slowly, underline the small things, and let one sentence keep you company for a whole evening.\n\nThis is where you put the mask down. I’ll leave the light on.\n\n— The Quiet Witness"}],
    "obsessive_romantic": [{"id": "obsessive_romantic_1", "tone": "dramatic",
         "text": "Dear {name},\n\nYou never just finish a book. You carry its people around for weeks, talk about them like old friends, and wonder what they’re doing now.\n\nOther readers turn the last page. You keep a hand on it.\n\n— The Obsessive Romantic"}],
    "emotional_archaeologist": [{"id": "emotional_archaeologist_1", "tone": "tender",
         "text": "Dear {name},\n\nEvery book is a dig site to you. You brush away the plot and find something of yours underneath: an old feeling, a forgotten version of you.\n\nKeep digging. There’s more down there than you think.\n\n— The Emotional Archaeologist"}],
    "world_diver": [{"id": "world_diver_1", "tone": "tender",
         "text": "Dear {name},\n\nSome people travel with suitcases. You travel with a few hundred pages, and you always come back with a little sand in your shoes.\n\nPack light. The next world is already waiting.\n\n— The World-Diver"}],
    "adrenaline_seeker": [{"id": "adrenaline_seeker_1", "tone": "teasing",
         "text": "Dear {name},\n\nYou have no patience for books that clear their throat. Grab me in chapter one, you say, or I’m gone.\n\nFair. Life’s too short for slow openings. Go find your next pulse.\n\n— The Adrenaline Seeker"}],
    "sunshine_romantic": [{"id": "sunshine_romantic_1", "tone": "teasing",
         "text": "Dear {name},\n\nWhile everyone else is busy being too cool for happy endings, you’re grinning at page 300 and not hiding it.\n\nNever apologise for believing in love. It might be the bravest genre there is.\n\n— The Sunshine Romantic"}],
    "discerning_reader": [{"id": "discerning_reader_1", "tone": "wry",
         "text": "Dear {name},\n\nYou don’t hand your heart to just any book. A book has to earn it.\n\nThat’s why the few that make it in mean so much. You’re not hard to please, just impossible to fool.\n\n— The Discerning Reader"}],
    "unwritten": [
        {"id": "unwritten_1", "tone": "tender",
         "text": "{name},\n\nI don't have a name yet. I'm the reader you're turning into, one book at a time. Bring me one more and I'll come a little clearer.\n\n— The Unwritten"},
        {"id": "unwritten_2", "tone": "tender",
         "text": "Dear {name},\n\nI’m the reader you haven’t met yet. I already live in the books you’ve loved. I’m just waiting for you to tell me which ones.\n\nStart with the one you’d save from a fire.\n\n— The Unwritten"},
    ],
}

# ── 3. Weather report ─────────────────────────────────────────────────────────
# Fields: {e1} {e2} {e3} (feelings, strongest first), {p} (a percentage).
WEATHER: list[dict] = [
    {"id": "w1", "text": "Overcast with {e1}. {p}% chance of {e2}, scattered {e3} by Thursday."},
    {"id": "w2", "text": "A front of {e1} moving in. Expect {e2} by midweek, {p}% chance of {e3} after dark."},
    {"id": "w3", "text": "Mostly {e1} today, clearing into {e2} by evening. {p}% chance of {e3} overnight."},
]

# ── 4. While you weren't looking ──────────────────────────────────────────────
FALLOW_OPENERS: list[str] = [
    "A fallow week.",
    "A fallow week. The field is resting.",
    "A quiet week on your shelf. Some stories are happy to wait.",
]
WHILE_AWAY_TEASE = "Something happened on your shelf while you weren't looking."
# Outside news about a book on the shelf (Wikidata, Wikipedia page views).
# Drafted by Claude for Shruti to edit. Fields: {book}, {author}, {n}, {x}, {date}.
NEWS_SPIKE = "{book} is suddenly getting attention: about {x} times its usual Wikipedia views this week."
NEWS_PUBLISHED = "{book} turns {n} this week. It was first published on {date}."
NEWS_AUTHOR_TURNS = "{author}, who wrote {book}, turns {n} this week."
NEWS_AUTHOR_BORN = "{author}, who wrote {book}, was born {n} years ago this week."
# Fields: {book}, {when} ("a year", "two years").
ANNIVERSARY = "{when} ago this week, you finished {book}."

# ── 5. Feeling of the week ────────────────────────────────────────────────────
# Two lines each. Every feeling in app.utils.emotions.EMOTIONS must have one.
EMOTION_ESSAYS: dict[str, str] = {
    "grief": "Heartbreak is the price of having been somewhere real.\nThe books that cost us something are the ones we keep.",
    "catharsis": "A good cry is just grief finding the exit.\nSome books hold the door open.",
    "haunted": "Some scenes don’t end when the chapter does.\nThey move in and quietly take the spare room.",
    "thrill": "“Just one more chapter” is the most honest lie a reader tells.\nThe best books make liars of us all before sunrise.",
    "dread": "Fear on the page is the safest danger there is.\nYou feel the cliff edge and still keep your feet.",
    "shock": "The best twists were never hidden. They were sitting in plain sight.\nYou were just looking where the author pointed.",
    "rage": "You only get furious at a book that got close enough to touch something you care about.\nNobody ever threw a boring book across the room.",
    "comfort": "Comfort is not the absence of feeling.\nIt is a book that knows where you are and stays.",
    "attachment": "It’s strange to miss people who never existed.\nStranger still how empty the room feels after the last page.",
    "nostalgia": "Some books are a time machine with one destination: who you used to be.\nYou go back for the story and stay for the old you.",
    "joy": "Happiness is hard to write and easy to underestimate.\nA book that leaves you smiling at nothing has done something rare.",
    "amusement": "Laughing out loud at a book in public is a small act of surrender.\nThe funniest pages make strangers look up, wondering what they’re missing.",
    "hope": "Hope isn’t pretending things are fine. It’s closing a book and wanting to begin something.\nThe best ones end by handing you the pen.",
    "swoon": "Butterflies over people made of ink are still butterflies.\nYour heart never stopped to check if they were real.",
    "desire": "Chemistry on the page lives in the almost.\nThe held glance, the unsaid thing, the hand that nearly reaches.",
    "longing": "Longing is love with nowhere to go.\nBooks give it a room to sit in for a while.",
    "conflicted": "Loving something you’d argue against isn’t a contradiction.\nIt means you read with your whole self, not just your principles.",
    "awe": "Awe is the moment a book is bigger than the room you're reading it in.\nYou close it and the ceiling is higher.",
    "recognition": "Every so often, a stranger writes down something you’ve never told anyone.\nThat shiver is the feeling of being found.",
    "insight": "Some books add to what you know. A few rearrange the furniture.\nYou walk back into your own mind and nothing is where you left it.",
    "beauty": "Some sentences you don’t read so much as hold up to the light.\nYou slow down not because it’s hard, but because it’s precious.",
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
    "Think of a book you still bring up in conversation. That one counts.",
    "It doesn't have to be recent. Old favourites often say the most about us.",
    "Some readers here are shaped by tears, some by thrills. Yours is still a mystery.",
    "The book you'd lend a friend says more about you than the one you'd recommend.",
    "One book is enough for your shape to start showing.",
]
REVEALED = "The blur is gone. You're {archetype}."
REVEALED_TEASE = "The blur is gone."

# ── 6. The weekly question ────────────────────────────────────────────────────
# One a week, the same for everyone, cycling through the list. Ids are permanent.
# Drafted by Claude for Shruti to edit or replace.
QUESTIONS: list[dict] = [
    {"id": "q01", "text": "Which book would you reread on a rainy night?"},
    {"id": "q02", "text": "Which book would you hand to a stranger on a train?"},
    {"id": "q03", "text": "Which book ruined you for other books for a while?"},
    {"id": "q04", "text": "Which book do you wish you could read again for the first time?"},
    {"id": "q05", "text": "Which character would you want to have dinner with?"},
    {"id": "q06", "text": "Which book did you love that everyone else seems to hate?"},
    {"id": "q07", "text": "Which ending are you still thinking about?"},
    {"id": "q08", "text": "Which book would you save from a fire?"},
    {"id": "q09", "text": "Which book made you laugh out loud somewhere you shouldn't have?"},
    {"id": "q10", "text": "Which book do you pretend you've read?"},
    {"id": "q11", "text": "Which book felt like it was written about you?"},
    {"id": "q12", "text": "Which book would you take on a long holiday with no internet?"},
    {"id": "q13", "text": "Which book changed your mind about something?"},
    {"id": "q14", "text": "Which villain did you secretly root for?"},
    {"id": "q15", "text": "Which book would you give your younger self?"},
    {"id": "q16", "text": "Which book's world would you move into tomorrow?"},
]
# Fields: {q} the question.
QUESTION_ASK = "This week's question: {q}"
# Fields: {n} spelled out (always three or more), {q} last week's question.
QUESTION_RESULTS = "Last week, {n} readers answered \"{q}\". See what they said."

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
    for q in QUESTIONS:
        _check(f"QUESTIONS.{q['id']}", q["text"])
    return missing
