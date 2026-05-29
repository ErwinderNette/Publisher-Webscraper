"""
Fehler-Screenshots: Element-Ausschnitt wenn möglich, sonst Fallback; roter Rahmen (Pillow).
"""
import os
import re
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from config.settings import settings

CODE_RE = re.compile(r"(?:AFF|KUP)\d{4}", re.IGNORECASE)
BOX_COLOR = (220, 38, 38)
BOX_WIDTH = 3
PADDING = 8


def _annotate_image(path: str) -> str:
    """Zeichnet roten Rahmen um das gesamte Bild (Fehlerbereich)."""
    with Image.open(path) as img:
        draw = ImageDraw.Draw(img)
        w, h = img.size
        for i in range(BOX_WIDTH):
            draw.rectangle(
                [i, i, w - 1 - i, h - 1 - i],
                outline=BOX_COLOR,
            )
        img.save(path)
    return path


def _extract_code_hints(*texts: Optional[str]) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for t in texts:
        if not t:
            continue
        for m in CODE_RE.findall(str(t)):
            key = m.upper()
            if key not in seen:
                seen.add(key)
                hints.append(m)
    return hints


async def _try_locator_screenshot(page, locator, path: str) -> bool:
    try:
        if await locator.count() == 0:
            return False
        first = locator.first
        await first.scroll_into_view_if_needed(timeout=5000)
        await first.screenshot(path=path, timeout=10000)
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:
        return False


async def capture_issue_screenshot(
    page,
    prefix: str,
    *,
    hint_texts: Optional[list[str]] = None,
    ist: str = "",
    soll: str = "",
    voucher_href: str = "",
    pruefpunkt: str = "",
    fallback_relative: str = "",
) -> str:
    """
    Speichert annotierten Fehler-Screenshot unter reports/screenshots/.
    Gibt relativen Pfad zurück (screenshots/...) oder '-'.
    """
    os.makedirs(settings.screenshot_dir, exist_ok=True)
    name = f"issue_{prefix}_{datetime.now().strftime('%H%M%S_%f')}.png"
    abs_path = os.path.join(settings.screenshot_dir, name)
    rel_path = f"screenshots/{name}"

    hints = list(hint_texts or [])
    hints.extend(_extract_code_hints(ist, soll, voucher_href, (hint_texts or [""])[0] if hint_texts else ""))

    captured = False

    # Focus / Igraal: Voucher-Anker
    if voucher_href and "#voucher-" in voucher_href:
        vid = voucher_href.split("#voucher-")[-1].split("?")[0].strip()
        if vid:
            for sel in (
                f'[data-testid="voucher-item"][href*="{vid}"]',
                f'a[href*="#voucher-{vid}"]',
                f'[id*="voucher-{vid}"]',
            ):
                if await _try_locator_screenshot(page, page.locator(sel), abs_path):
                    captured = True
                    break

    # Code-Text auf der Seite
    if not captured:
        for code in hints:
            for sel in (
                f"text={code}",
                f"text=/.*{re.escape(code)}.*/i",
            ):
                try:
                    loc = page.locator(sel)
                    if await _try_locator_screenshot(page, loc, abs_path):
                        captured = True
                        break
                except Exception:
                    continue
            if captured:
                break

    # Kontext aus Angebot / Prüfpunkt (erste ~40 Zeichen)
    if not captured and hint_texts:
        snippet = (hint_texts[0] or "")[:50].strip()
        if len(snippet) >= 8:
            try:
                loc = page.get_by_text(snippet, exact=False)
                if await _try_locator_screenshot(page, loc, abs_path):
                    captured = True
            except Exception:
                pass

    # Coupon-Widgets / Listings
    if not captured:
        widget_selectors = [
            '[data-testid="active-vouchers-widget"]',
            ".coupon-listing-item",
            ".coupon-item",
            ".voucher",
            "article",
        ]
        for sel in widget_selectors:
            if await _try_locator_screenshot(page, page.locator(sel).first, abs_path):
                captured = True
                break

    # Fallback: vorhandener Screenshot kopieren oder Viewport
    if not captured:
        if fallback_relative and fallback_relative != "-":
            fb_abs = os.path.join(settings.report_dir, fallback_relative)
            if os.path.isfile(fb_abs):
                import shutil

                shutil.copy2(fb_abs, abs_path)
                captured = True
        if not captured:
            try:
                await page.screenshot(path=abs_path, full_page=False)
                captured = True
            except Exception:
                return "-"

    if captured:
        _annotate_image(abs_path)
        return rel_path
    return "-"
