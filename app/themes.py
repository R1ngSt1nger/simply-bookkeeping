THEMES = {
    "coastal_lagoon": {
        "label": "Coastal Lagoon",
        "ink": "#123B52", "inkdeep": "#0A2536",
        "paper": "#F6F1E7", "papercard": "#FFFFFF",
        "accent": "#0F9DBF", "accentdeep": "#0B7A91", "accentlight": "#D6F1F6",
        "line": "#E2DDD0",
    },
    "slate_harbour": {
        "label": "Slate Harbour",
        "ink": "#1E3A4C", "inkdeep": "#12242F",
        "paper": "#EEF1F4", "papercard": "#FFFFFF",
        "accent": "#2E6E99", "accentdeep": "#235579", "accentlight": "#D8E6EE",
        "line": "#DCE3E8",
    },
    "desert_clay": {
        "label": "Desert Clay",
        "ink": "#3A2E22", "inkdeep": "#241B13",
        "paper": "#F5F0E6", "papercard": "#FFFCF6",
        "accent": "#C1673A", "accentdeep": "#9C4F28", "accentlight": "#F0DAC8",
        "line": "#E4DAC8",
    },
    "dusk_violet": {
        "label": "Dusk Violet",
        "ink": "#332A42", "inkdeep": "#201A2B",
        "paper": "#F1EEF3", "papercard": "#FFFFFF",
        "accent": "#6B4E8E", "accentdeep": "#543C70", "accentlight": "#E7DFF0",
        "line": "#E1DCE8",
    },
    "charcoal_gold": {
        "label": "Charcoal Gold",
        "ink": "#2B2924", "inkdeep": "#17160F",
        "paper": "#F1EFEA", "papercard": "#FFFFFF",
        "accent": "#B08A2E", "accentdeep": "#8C6D22", "accentlight": "#F0E6C9",
        "line": "#E2DFD5",
    },
}

DEFAULT_THEME = "slate_harbour"

# Fixed across every theme — positive/income is always green, negative/expense
# is always red/coral. This is a financial-meaning convention, not a decorative
# choice, so it doesn't change with the theme.
MONEY_POSITIVE = "#1E9E6B"
MONEY_POSITIVE_LIGHT = "#CDEEDC"
MONEY_NEGATIVE = "#D1493C"
MONEY_NEGATIVE_LIGHT = "#F6D9D2"


def get_theme(key: str) -> dict:
    theme = dict(THEMES.get(key) or THEMES[DEFAULT_THEME])
    theme["brass"] = MONEY_POSITIVE
    theme["brasslight"] = MONEY_POSITIVE_LIGHT
    theme["rust"] = MONEY_NEGATIVE
    theme["rustlight"] = MONEY_NEGATIVE_LIGHT
    return theme
