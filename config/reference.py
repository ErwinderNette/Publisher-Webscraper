"""
Lokale Referenz-Overrides – ergänzen das Google Sheet.

Kampagnen-Codes (z. B. AFF1906) und feste Ziel-URLs für wiederkehrende Deals.
Reise-Hits und Reisewelt werden dynamisch aus dem Datum berechnet.
"""
import re
from datetime import date, timedelta
from typing import Optional, Set

from config.settings import settings

# Exklusive Aktionscodes: dürfen auf Publisher-Seiten stehen, ohne „veraltet“-Meldung
EXCLUSIVE_CODES: frozenset[str] = frozenset({"AFF1906"})

# Kategorie → abweichender Aktionscode (sonst Monatscode AFF0526 o. ä.)
CATEGORY_ACTION_CODES: dict[str, str] = {
    "flugreisen": "AFF1906",
}

# Erwartete Ziel-URLs pro Kategorie (Pfad ohne Query)
# reise_hits und reisewelt werden dynamisch berechnet – siehe get_category_target_url()
CATEGORY_TARGET_URLS: dict[str, str] = {
    "homepage": "https://www.trendtours.de",
    "flugreisen": "https://www.trendtours.de/kampagne/flugreisen-2026",
    "flugreisen_listing": "https://www.trendtours.de/flugreisen",
    "busreisen": "https://www.trendtours.de/busreisen",
    "reiseziele": "https://www.trendtours.de/reiseziele",
}

REISE_HITS_SLUG_RE = re.compile(r"/reise-hits/(reise-hits\d{4})", re.IGNORECASE)
REISEWELT_SLUG_RE = re.compile(r"/reisewelt/(reisewelt-[a-z]+)", re.IGNORECASE)

REISE_HITS_GRACE_DAYS = 3

MONTH_NAMES_DE: dict[int, str] = {
    1: "januar",
    2: "februar",
    3: "maerz",
    4: "april",
    5: "mai",
    6: "juni",
    7: "juli",
    8: "august",
    9: "september",
    10: "oktober",
    11: "november",
    12: "dezember",
}


def get_month_code(reference_date: Optional[date] = None) -> str:
    d = reference_date or date.today()
    return f"AFF{d.month:02d}{d.year % 100:02d}"


def get_reise_hits_reference_wednesday(reference_date: date) -> date:
    """Letzter Mittwoch (einschließlich heute, wenn Mittwoch)."""
    weekday = reference_date.weekday()  # Mo=0 … So=6
    if weekday >= 2:
        days_back = weekday - 2
    else:
        days_back = weekday + 4
    return reference_date - timedelta(days=days_back)


def get_reise_hits_slug(reference_date: Optional[date] = None) -> str:
    """Slug z. B. reise-hits2126 aus ISO-KW des Referenz-Mittwochs."""
    d = reference_date or date.today()
    ref_wed = get_reise_hits_reference_wednesday(d)
    iso_year, iso_week, _ = ref_wed.isocalendar()
    return f"reise-hits{iso_week:02d}{iso_year % 100:02d}"


def get_current_reise_hits_url(reference_date: Optional[date] = None) -> str:
    slug = get_reise_hits_slug(reference_date)
    return f"https://www.trendtours.de/reise-hits/{slug}"


def get_previous_reise_hits_slug(reference_date: Optional[date] = None) -> str:
    d = reference_date or date.today()
    prev_wed = get_reise_hits_reference_wednesday(d) - timedelta(days=7)
    return get_reise_hits_slug(prev_wed)


def slug_to_activation_wednesday(slug: str) -> Optional[date]:
    """Mittwoch, an dem ein Reise-Hits-Slug aktiviert wurde."""
    match = re.fullmatch(r"reise-hits(\d{2})(\d{2})", slug.lower())
    if not match:
        return None
    iso_week = int(match.group(1))
    full_year = 2000 + int(match.group(2))
    jan4 = date(full_year, 1, 4)
    week1_monday = jan4 - timedelta(days=jan4.weekday())
    target_monday = week1_monday + timedelta(weeks=iso_week - 1)
    return target_monday + timedelta(days=2)


def extract_reise_hits_slug(path: str) -> Optional[str]:
    match = REISE_HITS_SLUG_RE.search(path.lower())
    return match.group(1).lower() if match else None


def evaluate_reise_hits_url(final_path: str, reference_date: Optional[date] = None) -> dict:
    """
    Prüft Reise-Hits-URLs:
    - Aktueller Slug: OK
    - Vorheriger Slug: OK am Wechsel-Mittwoch und bis 3 Tage danach
    - Älter: Handlungsbedarf
    """
    today = reference_date or date.today()
    url_slug = extract_reise_hits_slug(final_path)
    if not url_slug:
        current = get_current_reise_hits_url(today)
        return {
            "matches_category": False,
            "is_valid": False,
            "expected_url": current.rstrip("/").lower(),
            "found_slug": None,
            "days_outdated": None,
            "error": f"Kein Reise-Hits-Slug in URL – erwartet {get_reise_hits_slug(today)}",
            "action": f"Link auf {current} aktualisieren",
        }

    current_slug = get_reise_hits_slug(today)
    previous_slug = get_previous_reise_hits_slug(today)
    expected_url = get_current_reise_hits_url(today).rstrip("/").lower()
    current_ref_wed = get_reise_hits_reference_wednesday(today)
    days_since_switch = (today - current_ref_wed).days

    if url_slug == current_slug:
        return {
            "matches_category": True,
            "is_valid": True,
            "expected_url": expected_url,
            "found_slug": url_slug,
            "days_outdated": 0,
            "error": None,
            "action": None,
        }

    if url_slug == previous_slug and days_since_switch <= REISE_HITS_GRACE_DAYS:
        return {
            "matches_category": True,
            "is_valid": True,
            "expected_url": expected_url,
            "found_slug": url_slug,
            "days_outdated": days_since_switch,
            "error": None,
            "action": None,
        }

    activation_wed = slug_to_activation_wednesday(url_slug)
    if activation_wed:
        deactivated_on = activation_wed + timedelta(days=7)
        days_outdated = max(0, (today - deactivated_on).days)
    else:
        days_outdated = REISE_HITS_GRACE_DAYS + 1

    return {
        "matches_category": True,
        "is_valid": False,
        "expected_url": expected_url,
        "found_slug": url_slug,
        "days_outdated": days_outdated,
        "error": (
            f"Veralteter Reise-Hits-Link ({url_slug}) – "
            f"aktuell erwartet: {current_slug} "
            f"({days_outdated} Tag(e) überholt)"
        ),
        "action": f"Wochenangebot-Link auf {get_current_reise_hits_url(today)} aktualisieren",
    }


def get_reisewelt_slug(reference_date: Optional[date] = None) -> str:
    d = reference_date or date.today()
    return f"reisewelt-{MONTH_NAMES_DE[d.month]}"


def get_reisewelt_url(reference_date: Optional[date] = None) -> str:
    slug = get_reisewelt_slug(reference_date)
    return f"https://www.trendtours.de/reisewelt/{slug}"


def get_previous_reisewelt_slug(reference_date: Optional[date] = None) -> str:
    d = reference_date or date.today()
    first_of_month = d.replace(day=1)
    last_month = first_of_month - timedelta(days=1)
    return get_reisewelt_slug(last_month)


def extract_reisewelt_slug(path: str) -> Optional[str]:
    match = REISEWELT_SLUG_RE.search(path.lower())
    return match.group(1).lower() if match else None


def evaluate_reisewelt_url(final_path: str, reference_date: Optional[date] = None) -> dict:
    """Prüft Reisewelt-URLs – aktueller Monat, Vor-Monat bis 3 Tage nach Monatswechsel OK."""
    today = reference_date or date.today()
    url_slug = extract_reisewelt_slug(final_path)
    current_slug = get_reisewelt_slug(today)
    previous_slug = get_previous_reisewelt_slug(today)
    expected_url = get_reisewelt_url(today).rstrip("/").lower()
    days_since_month_start = today.day - 1

    if not url_slug:
        return {
            "matches_category": False,
            "is_valid": False,
            "expected_url": expected_url,
            "found_slug": None,
            "days_outdated": None,
            "error": f"Kein Reisewelt-Slug in URL – erwartet {current_slug}",
            "action": f"Link auf {get_reisewelt_url(today)} aktualisieren",
        }

    if url_slug == current_slug:
        return {
            "matches_category": True,
            "is_valid": True,
            "expected_url": expected_url,
            "found_slug": url_slug,
            "days_outdated": 0,
            "error": None,
            "action": None,
        }

    if url_slug == previous_slug and days_since_month_start <= REISE_HITS_GRACE_DAYS:
        return {
            "matches_category": True,
            "is_valid": True,
            "expected_url": expected_url,
            "found_slug": url_slug,
            "days_outdated": days_since_month_start,
            "error": None,
            "action": None,
        }

    return {
        "matches_category": True,
        "is_valid": False,
        "expected_url": expected_url,
        "found_slug": url_slug,
        "days_outdated": days_since_month_start if url_slug == previous_slug else None,
        "error": (
            f"Veralteter Reisewelt-Link ({url_slug}) – "
            f"aktuell erwartet: {current_slug}"
        ),
        "action": f"Monatsangebot-Link auf {get_reisewelt_url(today)} aktualisieren",
    }


def get_expected_code_for_category(category_key: str) -> str:
    """Aktionscode für Redirect-Prüfung einer Kategorie."""
    return CATEGORY_ACTION_CODES.get(category_key, get_month_code()).upper()


def get_allowed_codes() -> set[str]:
    """Aktuelle Codes: Monatscode (Sheet/Settings) + exklusive Kampagnen."""
    allowed = (
        {get_month_code()}
        | {c.upper() for c in EXCLUSIVE_CODES}
        | {code.upper() for code in CATEGORY_ACTION_CODES.values()}
    )
    try:
        from config.sheet_loader import loader

        ref = loader.get_expected_for_category("homepage")
        if ref and ref.get("code") and str(ref["code"]) not in ("-", "nan", ""):
            allowed.add(str(ref["code"]).upper())
    except Exception:
        pass
    return allowed


def get_allowed_page_codes() -> set[str]:
    """Alias für Seiten- und Redirect-Code-Prüfung."""
    return get_allowed_codes()


def is_outdated_aff_code(code: str, allowed: Optional[Set[str]] = None) -> bool:
    """True, wenn AFF/KUP-Code nicht mehr gültig ist (z. B. Vormonat)."""
    c = code.upper()
    if c in (allowed or get_allowed_codes()):
        return False
    if c.startswith("AFF") or c.startswith("KUP"):
        return len(c) >= 7 and c[3:7].isdigit()
    return False


def url_path_matches_category(final_path: str, category_key: str, reference_date: Optional[date] = None) -> bool:
    """Prüft Ziel-Pfad gegen die erwartete Kategorie."""
    path = final_path.split("?")[0].lower().rstrip("/")

    if category_key == "reise_hits":
        return evaluate_reise_hits_url(path, reference_date)["is_valid"]

    if category_key == "reisewelt":
        return evaluate_reisewelt_url(path, reference_date)["is_valid"]

    primary = get_category_target_url(category_key, reference_date)
    if primary:
        expected = primary.rstrip("/").lower()
        if expected in path or path in expected:
            return True

    prefix_rules: dict[str, tuple[str, ...]] = {
        "flugreisen": ("/kampagne/flugreisen",),
        "flugreisen_listing": ("/flugreisen",),
        "busreisen": ("/busreisen",),
        "reiseziele": ("/reiseziele",),
        "homepage": ("https://www.trendtours.de",),
    }
    for fragment in prefix_rules.get(category_key, ()):
        if fragment in path or path.endswith(fragment.rstrip("/")):
            return True
    return False


def get_category_target_url(category_key: str, reference_date: Optional[date] = None) -> Optional[str]:
    if category_key == "reise_hits":
        return get_current_reise_hits_url(reference_date)
    if category_key == "reisewelt":
        return get_reisewelt_url(reference_date)
    return CATEGORY_TARGET_URLS.get(category_key)


def is_trendtours_destination(url: str) -> bool:
    """True nur bei echter Ziel-Domain trendtours.de (nicht Publisher-Pfade mit „trendtours“ im Slug)."""
    if not url or url in ("ERROR", "#"):
        return False
    return "trendtours.de" in url.lower()
