"""
Zusätzliche Seitenprüfungen: Logo und abgelaufene Aktionen.
"""
import re
from datetime import datetime

from bs4 import BeautifulSoup

EXPIRED_KEYWORDS = (
    "abgelaufen",
    "expired",
    "verfallen",
    "nicht mehr gültig",
    "nicht mehr gueltig",
    "outdated",
)


def validate_logo(soup: BeautifulSoup) -> dict:
    """Prüft, ob ein Trendtours-Logo auf der Seite referenziert wird."""
    for img in soup.find_all("img"):
        src = (img.get("src") or "").lower()
        alt = (img.get("alt") or "").lower()
        title = (img.get("title") or "").lower()
        combined = f"{src} {alt} {title}"
        if "trendtours" in combined:
            return {"is_valid": True, "error": None, "action": None}

    return {
        "is_valid": False,
        "error": "Kein Trendtours-Logo auf der Seite gefunden",
        "action": "Aktuelles Trendtours-Logo gemäß Vorgabe einbinden",
    }


def validate_expired_deals(soup: BeautifulSoup, scraper_profile: str) -> dict:
    """Prüft ShopClever-Coupons: alter Monatscode ohne Abgelaufen-Kennzeichnung."""
    if scraper_profile == "focus_gsg":
        return _validate_focus_active_widget(soup)
    if scraper_profile != "shopclever":
        return {"is_valid": True, "error": None, "action": None}

    now = datetime.now()
    current = f"aff{now.month:02d}{now.year % 100:02d}"
    unmarked = []

    for item in soup.find_all("div", class_="coupon-listing-item"):
        if item.find("input", id="subscribe-store-email"):
            continue

        text = item.get_text(separator=" ", strip=True).lower()
        classes = " ".join(item.get("class", [])).lower()
        has_expired = any(k in text or k in classes for k in EXPIRED_KEYWORDS)

        codes = re.findall(r"aff\d{4}", text)
        old_codes = [c for c in codes if c != current]

        if old_codes and not has_expired:
            title_tag = item.find("h3", class_="coupon-title")
            label = title_tag.get_text(strip=True)[:80] if title_tag else text[:80]
            unmarked.append(label)

    if unmarked:
        return {
            "is_valid": False,
            "error": f"{len(unmarked)} Aktion(en) mit altem Code, nicht als abgelaufen markiert",
            "action": "Abgelaufene Aktionen kennzeichnen oder entfernen",
            "details": unmarked[:5],
        }

    return {"is_valid": True, "error": None, "action": None}


def _validate_focus_active_widget(soup: BeautifulSoup) -> dict:
    """Im aktiven Widget dürfen keine als abgelaufen markierten Angebote stehen."""
    widget = soup.select_one('[data-testid="active-vouchers-widget"]')
    if not widget:
        return {
            "is_valid": False,
            "error": "Aktiver Gutschein-Bereich (active-vouchers-widget) nicht gefunden",
            "action": "Seitenstruktur prüfen",
        }

    text = widget.get_text(separator=" ", strip=True).lower()
    classes = " ".join(widget.get("class", [])).lower()
    if any(k in text or k in classes for k in EXPIRED_KEYWORDS):
        return {
            "is_valid": False,
            "error": "Im aktiven Bereich sind abgelaufene Angebote sichtbar",
            "action": "Abgelaufene Deals in den Bereich „abgelaufene Gutscheine“ verschieben",
        }

    return {"is_valid": True, "error": None, "action": None}
