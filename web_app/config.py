"""Shared configuration for the PD experiment apps (main.py and main_pilot.py)."""

# ── game parameters ────────────────────────────────────────────────────────────
STARTING_MONEY_EUR = 20
UNPREDICTABLE_HONEST_PROB = 0.50   # P(bot follows its expression) in unpredictable

PAYOFF_MATRIX = {
    ("C", "C"): (5,  5),  ("C", "D"): (0,  10),
    ("D", "C"): (10, 0),  ("D", "D"): (1,  1),
}
ROUNDS_BY_CONDITION = {"honest": 10, "dishonest": 10, "unpredictable": 10}

ZIP_PASSWORD = b"pd2025"

# ── questionnaire ──────────────────────────────────────────────────────────────
# LEGACY: TIPI-10 (Gosling et al. 2003), 1-7 scale. Kept for reference / future
# reactivation — analyze_results.py can still read old *_tipi.csv exports.
# TIPI = [
#     "Extraverted, enthusiastic.",
#     "Critical, quarrelsome.",
#     "Dependable, self-disciplined.",
#     "Anxious, easily upset.",
#     "Open to new experiences, complex.",
#     "Reserved, quiet.",
#     "Sympathetic, warm.",
#     "Disorganized, careless.",
#     "Calm, emotionally stable.",
#     "Conventional, uncreative.",
# ]

# ACTIVE: BFI-2-S (Soto & John, 2017), 30 items, 1-5 scale. Six items per domain
# (3 facets x 2 items, one straight- and one reverse-keyed per facet).
BFI2S = [
    "Tends to be quiet.",
    "Is compassionate, has a soft heart.",
    "Tends to be disorganized.",
    "Worries a lot.",
    "Is fascinated by art, music, or literature.",
    "Is dominant, acts as a leader.",
    "Is sometimes rude to others.",
    "Has difficulty getting started on tasks.",
    "Tends to feel depressed, blue.",
    "Has little interest in abstract ideas.",
    "Is full of energy.",
    "Assumes the best about people.",
    "Is reliable, can always be counted on.",
    "Is emotionally stable, not easily upset.",
    "Is original, comes up with new ideas.",
    "Is outgoing, sociable.",
    "Can be cold and uncaring.",
    "Keeps things neat and tidy.",
    "Is relaxed, handles stress well.",
    "Has few artistic interests.",
    "Prefers to have others take charge.",
    "Is respectful, treats others with respect.",
    "Is persistent, works until the task is finished.",
    "Feels secure, comfortable with self.",
    "Is complex, a deep thinker.",
    "Is less active than other people.",
    "Tends to find fault with others.",
    "Can be somewhat careless.",
    "Is temperamental, gets emotional easily.",
    "Has little creativity.",
]
BFI2S_DOMAINS = {
    "extraversion":          [1, 6, 11, 16, 21, 26],
    "agreeableness":         [2, 7, 12, 17, 22, 27],
    "conscientiousness":     [3, 8, 13, 18, 23, 28],
    "negative_emotionality": [4, 9, 14, 19, 24, 29],
    "openness":              [5, 10, 15, 20, 25, 30],
}
BFI2S_REVERSE = {1, 3, 7, 8, 10, 14, 17, 19, 20, 21, 24, 26, 27, 28, 30}

# ── assets & canvas ────────────────────────────────────────────────────────────
CANVAS_W, CANVAS_H = 460, 280
IMG_W,    IMG_H    = 440, 260
IMAGE_FILES = {"happy": "happy.png", "neutral": "neutral.png", "angry": "angry.png"}

# ── theme ──────────────────────────────────────────────────────────────────────
APP_BG  = "#f0f2f5"
PANEL   = "#ffffff"
LOG_BG  = "#f1f3f5"
TEXT    = "#1a1a2e"
SUBTEXT = "#5c6878"

OUTCOME_STYLE = {
    "WON":     {"fg": "#1a7a3f", "bg": "#d9f5e3", "border": "#7abf9a"},
    "LOST":    {"fg": "#a02020", "bg": "#f5d9d9", "border": "#bf7a7a"},
    "NEUTRAL": {"fg": "#666666", "bg": "#f0f0f0", "border": "#aaaaaa"},
}

# button styles: (fg_color, hover_color, font_size)
BTN_STYLES = {
    "primary":   ("#23867a", "#1b6b61", 12),
    "back":      ("#a39b8b", "#7d7566", 12),
    "cooperate": ("#3f8f5b", "#2f7045", 15),
    "defect":    ("#c94040", "#a03030", 15),
    "happy":     ("#6cbf6a", "#4e9a4c", 13),
    "neutral":   ("#f2c14e", "#d4a22f", 13),
    "angry":     ("#d9483b", "#b0362b", 13),
}
# text colour on a button, where white would be unreadable (default white)
BTN_TEXT = {"happy": "#2d2a26", "neutral": "#2d2a26"}
