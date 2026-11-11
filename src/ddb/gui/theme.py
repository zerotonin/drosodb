# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — gui.theme                                                 ║
# ║  « one source of truth for GUI colours »                         ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Wong (2011) colourblind-safe palette plus the semantic colours  ║
# ║  used by the lineage chart, the modal scrim and status notes.    ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Colour constants for the GUI. Import these instead of hardcoding hex."""

from __future__ import annotations

WONG: dict[str, str] = {
    "black": "#000000",
    "orange": "#E69F00",
    "sky_blue": "#56B4E9",
    "bluish_green": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "vermilion": "#D55E00",
    "reddish_purple": "#CC79A7",
}

ACCENT = WONG["blue"]

# Lineage chart: the selected vial, live vials, retired vials.
LINEAGE_FOCUS = WONG["orange"]
LINEAGE_FOCUS_FILL = "#FDF1DA"
LINEAGE_ACTIVE = WONG["bluish_green"]
LINEAGE_RETIRED = "#9A9A9A"
LINEAGE_RETIRED_FILL = "#F2F2F2"
LINEAGE_CARD_FILL = "#FFFFFF"
LINEAGE_TEXT = "#1F1F1F"
LINEAGE_MUTED_TEXT = "#6B6B6B"
LINEAGE_EDGE = "#7C8693"
LINEAGE_CROSS_EDGE = WONG["vermilion"]
LINEAGE_BACKGROUND = "#FAFBFC"

# Modal backdrop: dark slate at ~40 % opacity (alpha 0-255).
SCRIM_RGB: tuple[int, int, int] = (20, 24, 33)
SCRIM_ALPHA = 102
SCRIM_FADE_MS = 140
