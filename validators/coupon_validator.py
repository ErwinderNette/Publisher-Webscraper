"""
Validiert Gutscheincodes auf Publisher-Seiten gegen die Sheet-Referenz.
"""
import re
from config.reference import get_allowed_codes, is_outdated_aff_code
from config.sheet_loader import loader

CODE_PATTERN = re.compile(r"(?:AFF|KUP)\d{4}", re.IGNORECASE)


def get_expected_month_code() -> str:
    ref = loader.get_expected_for_category("homepage")
    if ref and ref.get("code") and ref["code"] not in ("-", "nan", ""):
        return str(ref["code"]).upper()
    from config.reference import get_month_code

    return get_month_code()


def _extract_url_codes(final_url: str) -> set[str]:
    return {m.upper() for m in CODE_PATTERN.findall(final_url or "")}


def validate_page_codes(found_codes: set[str]) -> dict:
    """Seitenweiter Code-Check: aktueller Monatscode vorhanden, keine veralteten Codes."""
    expected = get_expected_month_code()
    found_upper = {c.upper() for c in found_codes}
    allowed = get_allowed_codes()

    missing = {expected} - found_upper
    outdated = {c for c in found_upper if is_outdated_aff_code(c, allowed)}

    is_valid = len(missing) == 0 and len(outdated) == 0
    issues = []
    if missing:
        issues.append(f"Aktueller Code {expected} fehlt auf der Seite")
    if outdated:
        issues.append(f"Veraltete Codes gefunden: {', '.join(sorted(outdated))}")

    return {
        "is_valid": is_valid,
        "expected": expected,
        "found": sorted(found_upper),
        "missing": sorted(missing),
        "outdated": sorted(outdated),
        "error": "; ".join(issues) if issues else None,
        "action": (
            f"Gutscheincode auf {expected} aktualisieren und alte Codes entfernen"
            if not is_valid
            else None
        ),
    }


def validate_url_code(final_url: str, expected_code: str = "") -> dict:
    """
    Prüft Affiliate-Codes in der Ziel-URL dynamisch:
    - Erlaubt: aktueller Monatscode (z. B. AFF0526, ab Juni AFF0626) + exklusive Kampagnen (AFF1906)
    - Fehler: veraltete AFF/KUP-Codes (z. B. AFF0426 im Mai)
    """
    if not final_url or final_url == "ERROR":
        return {
            "is_valid": False,
            "error": "Redirect konnte nicht aufgelöst werden",
            "action": "Affiliate-Link prüfen und korrigieren",
        }

    codes_in_url = _extract_url_codes(final_url)
    allowed = get_allowed_codes()

    if not codes_in_url:
        return {
            "is_valid": False,
            "error": "Finale URL enthält keinen Aktionscode (AFF/KUP)",
            "action": f"Affiliate-Link mit gültigem Code versehen (z. B. {get_expected_month_code()})",
        }

    outdated = {c for c in codes_in_url if is_outdated_aff_code(c, allowed)}
    has_allowed = bool(codes_in_url & allowed)

    is_valid = has_allowed and not outdated
    issues = []
    if outdated:
        issues.append(f"Veralteter Code in URL: {', '.join(sorted(outdated))}")
    if not has_allowed:
        issues.append(
            f"Kein gültiger Code in URL (erlaubt: {', '.join(sorted(allowed))})"
        )

    return {
        "is_valid": is_valid,
        "error": "; ".join(issues) if issues else None,
        "action": (
            None
            if is_valid
            else f"Link auf aktuellen Code {get_expected_month_code()} oder gültige Kampagne aktualisieren"
        ),
    }
