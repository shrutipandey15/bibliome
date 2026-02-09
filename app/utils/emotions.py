"""Emotion definitions — the core vocabulary of Book DNA."""

EMOTIONS = {
    "rage": {
        "label": "Rage",
        "description": "The book made you want to throw it. Or throw something else.",
        "color": "#C4553A",
    },
    "comfort": {
        "label": "Comfort",
        "description": "Warm blanket energy. You felt held.",
        "color": "#7A8B6F",
    },
    "dread": {
        "label": "Existential Dread",
        "description": "Stared at the ceiling for 20 minutes after reading.",
        "color": "#5A5A8A",
    },
    "healing": {
        "label": "Healing",
        "description": "Something shifted inside you. Quietly, permanently.",
        "color": "#5A8B6F",
    },
    "obsession": {
        "label": "Obsession",
        "description": "Could not stop reading. Cancelled plans. Lost sleep.",
        "color": "#6B3A5D",
    },
    "grief": {
        "label": "Grief",
        "description": "Mourned a fictional person like they were real.",
        "color": "#3A5A6B",
    },
    "seen": {
        "label": "Seen",
        "description": "The author wrote your exact inner experience.",
        "color": "#B8964E",
    },
    "chaos": {
        "label": "Chaos",
        "description": "The book broke your brain in a good way.",
        "color": "#C47A3A",
    },
    "nothing": {
        "label": "Nothing",
        "description": "You felt absolutely nothing. That's data too.",
        "color": "#6A6A6A",
    },
    "2am": {
        "label": "2AM Energy",
        "description": "Why did you read this at 2am? You don't know either.",
        "color": "#7A5A9B",
    },
}

VALID_EMOTION_IDS = set(EMOTIONS.keys())
