"""Emotion definitions — the core vocabulary of Book DNA."""

EMOTIONS = {
    "rage": {
        "label": "Rage",
        "description": "Righteous fury. You want justice, or you want to burn it all down.",
        "color": "#C4553A",
        "icon": "🔥"
    },
    "dread": {
        "label": "Dread",
        "description": "A knot in your stomach. You know something is coming.",
        "color": "#5A5A8A",
        "icon": "🌑"
    },
    "chaos": {
        "label": "Chaos",
        "description": "Unhinged energy. The rules don't apply here.",
        "color": "#C47A3A",
        "icon": "⚡"
    },
    "obsession": {
        "label": "Desire",
        "description": "Yearning, pining, and the ache of wanting.",
        "color": "#6B3A5D",
        "icon": "🩸"
    },

    "comfort": {
        "label": "Comfort",
        "description": "Safety. A warm light in a dark room.",
        "color": "#7A8B6F",
        "icon": "🍵"
    },
    "healing": {
        "label": "Catharsis",
        "description": "The release after the pain. You feel lighter now.",
        "color": "#5A8B6F",
        "icon": "✨"
    },
    "seen": {
        "label": "Seen",
        "description": "Validation. The author put your quietest thoughts on the page.",
        "color": "#B8964E",
        "icon": "👁️"
    },
    "grief": {
        "label": "Melancholy",
        "description": "The beautiful sadness. It hurts, but you don't want it to stop.",
        "color": "#3A5A6B",
        "icon": "💧"
    },
    "wit": {
        "label": "Wit",
        "description": "Sharp, clever, and intellectually playful. It feels like a game.",
        "color": "#8D5A9B",
        "icon": "🧠"
    },
    "awe": {
        "label": "Awe",
        "description": "The Sublime. You feel small in a vast, magnificent world.",
        "color": "#4E8CB8",
        "icon": "🌌"
    },
    "nostalgia": {
        "label": "Nostalgia",
        "description": "A bittersweet return to a past that may not be yours.",
        "color": "#B87A4E",
        "icon": "🍂"
    },    
    "nothing": {
        "label": "Empty",
        "description": "No connection. You read words, but felt nothing.",
        "color": "#6A6A6A",
        "icon": "🌫️"
    },    
    "2am": {
        "label": "2AM",
        "description": "Unputdownable. Sleep is for the weak.",
        "color": "#4B4B6A",
        "icon": "🌙"
    }
}

VALID_EMOTION_IDS = set(EMOTIONS.keys())