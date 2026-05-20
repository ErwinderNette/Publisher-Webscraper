"""
Erkennung von Reisekategorien basierend auf Keywords.
"""
from typing import Optional

def detect_category_from_text(text: str) -> str:
    """Erkennt Kategorie-Keys aus Button- oder Kontexttext."""
    t = text.lower().replace("€", "").replace(".", "")

    if "woche" in t or "hits" in t or "top-angebote" in t or "angebote der woche" in t:
        return "reise_hits"
    if "reisewelt" in t or "monat" in t or "bestpreis" in t:
        return "reisewelt"
    if "500" in t and "rabattcode" in t and "flug" not in t:
        return "homepage"
    if "alle reisen" in t or "last minute" in t:
        return "homepage"
    if "auf flugreisen" in t or ("1000" in t and "flugreisen" in t and "fernreisen" not in t):
        return "flugreisen_listing"
    # Kategorie-Navigation (z. B. ShopClever „Flugreise Angebote“) – vor Kampagnen-Deal
    if (
        "flugreise angebote" in t
        or ("flugreise" in t and "angebote" in t and "500" not in t and "last minute" not in t)
    ):
        return "flugreisen_listing"
    if "flugreisen-2026" in t or (
        "500" in t
        and ("flug" in t or "last minute" in t or "gutscheincode" in t)
    ):
        return "flugreisen"
    if "fernreisen" in t:
        return "homepage"
    if "flug" in t:
        return "flugreisen"
    if "bus" in t:
        return "busreisen"
    if "länder" in t or "ziele" in t:
        return "reiseziele"
    if "1000" in t and ("deine reise" in t or "pro person" in t or "person" in t or "gutschein" in t):
        return "homepage"

    return "homepage"