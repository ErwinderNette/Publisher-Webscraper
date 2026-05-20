"""
Validiert Ziel-URLs gegen berechnete Referenz (Reise-Hits, Reisewelt) bzw. Sheet.
"""
from config.categories import detect_category_from_text
from config.reference import (
    evaluate_reise_hits_url,
    evaluate_reisewelt_url,
    get_category_target_url,
    get_expected_code_for_category,
    url_path_matches_category,
)
from config.sheet_loader import loader


def _evaluate_category_url(cat_key: str, clean_final: str) -> dict:
    if cat_key == "reise_hits":
        return evaluate_reise_hits_url(clean_final)
    if cat_key == "reisewelt":
        return evaluate_reisewelt_url(clean_final)
    is_valid = url_path_matches_category(clean_final, cat_key)
    expected_url = (get_category_target_url(cat_key) or "").rstrip("/").lower()
    return {
        "is_valid": is_valid,
        "expected_url": expected_url,
        "error": (
            f"Falsches Ziel – erwartet Kategorie „{cat_key}“ (z. B. {expected_url})"
            if not is_valid
            else None
        ),
        "action": (
            f"Button/Link auf passendes Kategorie-Ziel ausrichten (z. B. {expected_url})"
            if not is_valid
            else None
        ),
    }


def validate_redirect(button_text: str, context_text: str, final_url: str) -> dict:
    """Prüft, ob der Klick auf trendtours.de in der erkannten Kategorie landet."""
    full_text = f"{button_text} {context_text}"
    cat_key = detect_category_from_text(full_text)
    expected = loader.get_expected_for_category(cat_key)

    if not expected:
        fallback_url = get_category_target_url(cat_key)
        if fallback_url:
            expected = {
                "url": fallback_url.rstrip("/").lower(),
                "code": get_expected_code_for_category(cat_key),
            }
        else:
            return {
                "is_valid": True,
                "error": None,
                "detected_category": cat_key,
                "final_url": final_url,
                "expected_code": "-",
                "expected_url": "-",
                "action": None,
            }

    clean_final = final_url.split("?")[0].lower().rstrip("/")
    evaluation = _evaluate_category_url(cat_key, clean_final)
    is_valid = evaluation["is_valid"]
    clean_expected = evaluation.get("expected_url") or expected["url"].rstrip("/")

    return {
        "is_valid": is_valid,
        "error": evaluation.get("error"),
        "detected_category": cat_key,
        "final_url": final_url,
        "expected_code": expected["code"],
        "expected_url": clean_expected,
        "action": evaluation.get("action"),
    }
