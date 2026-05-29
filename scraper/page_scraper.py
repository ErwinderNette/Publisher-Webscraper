"""
Scraper-Logik zum Extrahieren von Inhalten auf Publisher-Seiten.
Unterstützt ShopClever-Layout, coupons.de und generischen Fallback.
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from config.publishers import PublisherEntry, ScraperProfile
from config.reference import (
    EXCLUSIVE_CODES,
    get_expected_code_for_category,
    get_month_code,
    is_trendtours_destination,
)
from config.categories import detect_category_from_text

CODE_RE = re.compile(r"(?:AFF|KUP)\d{4}", re.IGNORECASE)
DEAL_LINK_HINTS = ("go.php", "/out/", "/go/", "aff", "deal", "redirect", "visit", "trendtours")
BUTTON_HINTS = ("anzeigen", "angebot", "einlösen", "gutschein", "rabatt", "sichern", "deal", "shop")

# coupons.de: nur der Merchant-Showbox-Bereich (aktive trendtours-Gutscheine)
COUPONS_DE_SHOWBOX = "section.cs-merchant-showbox"
COUPONS_DE_COUPON = ".cs-coupon-box.cs-coupon-merchant"
COUPONS_DE_AFFILIATE_BASE = "https://www.coupons.de/zum-anbieter"
COUPONS_DE_DEAL_PATTERNS = (
    re.compile(r"1[\s.,]*000.*rabatt", re.I),
    re.compile(r"500.*rabatt.*last\s*minute", re.I),
)

# Focus / GSG: nur aktive Gutscheine (markierter Bereich im DevTools)
FOCUS_ACTIVE_WIDGET = '[data-testid="active-vouchers-widget"]'
FOCUS_VOUCHER_HASH_RE = re.compile(r"#voucher-([a-f0-9-]{36})", re.I)
FOCUS_EXCLUDED_TITLES = (
    re.compile(r"shoop", re.I),
    re.compile(r"will?kommensbonus", re.I),
    re.compile(r"freunde\s*werben", re.I),
    re.compile(r"cashback", re.I),
    re.compile(r"bonus", re.I),
)
FOCUS_DEAL_PATTERNS = (
    re.compile(r"500.*gutscheincode.*ergattern", re.I),
    re.compile(r"1000.*nachlass.*gutschein", re.I),
    re.compile(r"busreisen.*400.*rabatt", re.I),
    re.compile(r"1000.*fernreisen", re.I),
    re.compile(r"nordkap.*lofoten", re.I),
)
FOCUS_CTA_SELECTOR = (
    '[role="button"][title*="einl" i], '
    '[role="button"][title*="Angebot" i]'
)
FOCUS_DESKTOP_VIEWPORT = {"width": 1280, "height": 900}

# Checkout Charlie (Sparwelt, Gutscheine.de): nur die drei Hauptangebote
SPARWELT_DEAL_PATTERNS = (
    re.compile(r"1\s*000.*rabatt.*pro\s*person", re.I),
    re.compile(r"top-angebote\s+der\s+woche", re.I),
    re.compile(r"500.*rabatt.*flugreisen", re.I),
)
GUTSCHEINE_DE_DEAL_PATTERNS = (
    re.compile(r"1\s*000.*gutschein.*deine\s*reise", re.I),
    re.compile(r"500.*rabatt.*viele\s*flugreisen", re.I),
    re.compile(r"top-angebote\s+der\s+woche", re.I),
)
WELT_DER_RABATTE_COUPON_TITLE = re.compile(r"1\s*000.*rabatt.*pro\s*person", re.I)
WELT_DER_RABATTE_COUPON_ID_RE = re.compile(r"\?c=(\d+)|/go/(\d+)/")

# iGraal: vier markierte Angebote (ohne Cashback-Zeile oben)
IGRAAL_VOUCHER_HASH_RE = re.compile(r"#voucher-([a-f0-9-]{36})", re.I)
IGRAAL_AFF_BUTTON_RE = re.compile(r"^AFF\d{4}$", re.I)
IGRAAL_EXCLUDED_TITLES = (
    re.compile(r"cashback", re.I),
    re.compile(r"abgelaufen", re.I),
    re.compile(r"boost des tages", re.I),
)
IGRAAL_DEAL_PATTERNS = (
    re.compile(r"1000.*gutschein\s*ein", re.I),
    re.compile(r"500.*rabattcode", re.I),
    re.compile(r"top-angebote.*woche", re.I),
    re.compile(r"bestpreis.*monat", re.I),
)
IGRAAL_DESKTOP_VIEWPORT = {"width": 1280, "height": 900}

# Gutscheinrausch: drei markierte Angebote (2× Code, 1× Angebot)
GUTSCHEINRAUSCH_DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
GUTSCHEINRAUSCH_DEAL_PATTERNS = (
    re.compile(r"500.*last\s*minute", re.I),
    re.compile(r"1[\s.,]*000.*alle\s*reisen", re.I),
    re.compile(r"1000.*auf\s*flugreisen", re.I),
)

# CMP / Cookie-Banner (u. a. weltderrabatte.de, Checkout Charlie)
_COMMON_CONSENT_SELECTORS = (
    'button:has-text("ZUSTIMMEN")',
    'button:has-text("Zustimmen")',
    'button:has-text("Alle akzeptieren")',
    'button:has-text("Akzeptieren")',
    'button:has-text("Okay")',
    'a:has-text("Okay")',
    "#onetrust-accept-btn-handler",
    "#didomi-notice-agree-button",
    "#cookie_action_close_header",
    ".cmpboxbtn.cmpboxbtnyes",
)


@dataclass
class ButtonLink:
    text: str
    href: str
    context_text: str
    resolved_url: Optional[str] = None  # z. B. nach Playwright-Klick (Focus)


@dataclass
class ScrapedPage:
    url: str
    found_codes: set[str]
    buttons: list[ButtonLink]
    soup: Optional[BeautifulSoup] = None


class PublisherScraper:
    def __init__(self, page):
        self.page = page

    @staticmethod
    async def _click_first_visible(target, selectors: tuple[str, ...], wait_ms: int = 800) -> bool:
        for sel in selectors:
            btn = target.locator(sel).first
            try:
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await target.wait_for_timeout(wait_ms)
                    return True
            except Exception:
                continue
        return False

    async def _dismiss_common_consent(self, page=None) -> None:
        target = page or self.page
        await self._click_first_visible(target, _COMMON_CONSENT_SELECTORS)

    async def dismiss_consent_for(self, scraper: ScraperProfile, page=None) -> None:
        """Cookie-Banner schließen (vor Screenshots und nach Navigation)."""
        if scraper == "welt_der_rabatte":
            await self._welt_der_rabatte_dismiss_consent(page)
        elif scraper == "coupons_de":
            await self._coupons_de_dismiss_consent()
        elif scraper in ("sparwelt", "gutscheine_de"):
            await self._checkout_charlie_dismiss_consent()
        elif scraper == "igraal":
            await self._igraal_dismiss_consent(page)
        else:
            await self._dismiss_common_consent(page)

    async def scrape(self, publisher: PublisherEntry) -> ScrapedPage:
        url = publisher.url
        print(f"\nLade Seite: {url} ...")
        await self.page.goto(url, wait_until="networkidle", timeout=60000)
        await self.page.wait_for_timeout(2000)

        if publisher.scraper == "focus_gsg":
            try:
                await self.page.wait_for_selector(FOCUS_ACTIVE_WIDGET, timeout=15000)
            except Exception:
                print("   ⚠️ Focus: active-vouchers-widget nicht gefunden, scrape eingeschränkt.")

        html = await self.page.content()
        soup = BeautifulSoup(html, "lxml")

        if publisher.scraper == "coupons_de":
            buttons, codes_found = await self._scrape_coupons_de_playwright(url)
            html = await self.page.content()
            soup = BeautifulSoup(html, "lxml")
        elif publisher.scraper == "focus_gsg":
            buttons, codes_found = await self._scrape_focus_gsg_playwright(url)
            html = await self.page.content()
            soup = BeautifulSoup(html, "lxml")
        elif publisher.scraper == "sparwelt":
            buttons, codes_found = await self._scrape_checkout_charlie_playwright(
                SPARWELT_DEAL_PATTERNS, "Sparwelt"
            )
            html = await self.page.content()
            soup = BeautifulSoup(html, "lxml")
        elif publisher.scraper == "gutscheine_de":
            buttons, codes_found = await self._scrape_checkout_charlie_playwright(
                GUTSCHEINE_DE_DEAL_PATTERNS, "Gutscheine.de"
            )
            html = await self.page.content()
            soup = BeautifulSoup(html, "lxml")
        elif publisher.scraper == "welt_der_rabatte":
            buttons, codes_found = await self._scrape_welt_der_rabatte_playwright()
            html = await self.page.content()
            soup = BeautifulSoup(html, "lxml")
        elif publisher.scraper == "igraal":
            buttons, codes_found = await self._scrape_igraal_playwright(url)
            try:
                html = await self.page.content()
            except Exception:
                html = ""
            soup = BeautifulSoup(html, "lxml") if html else None
        elif publisher.scraper == "gutscheinrausch":
            buttons, codes_found = await self._scrape_gutscheinrausch_playwright(url)
            html = await self.page.content()
            soup = BeautifulSoup(html, "lxml")
        else:
            text_content = soup.get_text(separator=" ")
            codes_found = set(CODE_RE.findall(text_content))
            if publisher.scraper == "shopclever":
                buttons = self._scrape_shopclever(soup)
            elif publisher.scraper == "generic_coupons":
                buttons = self._scrape_generic_coupons(soup, url)
            else:
                buttons = self._scrape_cashback(soup, url)

        return ScrapedPage(
            url=url,
            found_codes=codes_found,
            buttons=buttons,
            soup=soup,
        )

    def _scrape_shopclever(self, soup: BeautifulSoup) -> list[ButtonLink]:
        buttons = []
        for item in soup.find_all("div", class_="coupon-listing-item"):
            if item.find("input", id="subscribe-store-email"):
                continue

            title_tag = item.find("h3", class_="coupon-title")
            title_text = title_tag.get_text(strip=True) if title_tag else "Unbekanntes Angebot"

            btn_tag = item.find("a", class_=re.compile(r"coupon-button|deal-button|coupon-deal"))
            if not btn_tag:
                continue

            raw_href = btn_tag.get("href", "")
            aff_url = btn_tag.get("data-aff-url", "")
            target_url = aff_url if aff_url and aff_url != "#" else raw_href

            if not target_url or target_url.startswith(("javascript:", "#")):
                continue

            btn_text = btn_tag.get_text(strip=True) or "Zum Angebot"
            buttons.append(ButtonLink(text=btn_text, href=target_url, context_text=title_text))
        return buttons

    @staticmethod
    def _coupons_de_section(soup: BeautifulSoup) -> Optional[Tag]:
        return soup.select_one(COUPONS_DE_SHOWBOX)

    @staticmethod
    def _coupons_de_matches_deal(title: str) -> bool:
        return any(p.search(title) for p in COUPONS_DE_DEAL_PATTERNS)

    async def _coupons_de_dismiss_consent(self) -> None:
        await self._click_first_visible(self.page, _COMMON_CONSENT_SELECTORS)

    async def _collect_coupons_de_vouchers(self, base_url: str) -> list[dict]:
        """Nur 1.000€- und 500€-Last-Minute-Gutschein aus der Merchant-Showbox."""
        page_base = base_url.split("?")[0].rstrip("/")
        await self._coupons_de_dismiss_consent()

        section = self.page.locator(COUPONS_DE_SHOWBOX)
        try:
            await section.wait_for(state="visible", timeout=20000)
        except Exception:
            print("   ⚠️ Coupons.de: Merchant-Showbox nicht gefunden.")
            return []

        entries: list[dict] = []
        coupons = section.locator(COUPONS_DE_COUPON)
        for i in range(await coupons.count()):
            item = coupons.nth(i)
            coupon_id = await item.get_attribute("id")
            if not coupon_id:
                continue

            desc = item.locator(".cs-coupon-box-description").first
            title = (await desc.inner_text()).strip() if await desc.count() else ""
            if not title or not self._coupons_de_matches_deal(title):
                continue

            date_el = item.locator(".cs-coupon-box-date").first
            date_text = (await date_el.inner_text()).strip() if await date_el.count() else ""

            entries.append(
                {
                    "coupon_id": coupon_id,
                    "title": title,
                    "coupon_url": f"{page_base}?open_coupon={coupon_id}",
                }
            )

        return entries

    async def _coupons_de_click_code_button(self) -> tuple[Optional[str], set[str]]:
        """
        Klickt „Code anzeigen“ im Modal.
        Code steht im Popup; Redirect läuft über zum-anbieter auf der Hauptseite.
        """
        codes: set[str] = set()
        final_url: Optional[str] = None

        code_btn = self.page.locator("#CouponModal .cs-coupon-btn.js-btn-co").first
        try:
            await code_btn.wait_for(state="visible", timeout=12000)
        except Exception:
            return None, codes

        codes.update(CODE_RE.findall((await self.page.content()).upper()))

        try:
            async with self.page.expect_popup(timeout=12000) as popup_info:
                await code_btn.click(force=True)
            popup = await popup_info.value
            await popup.wait_for_load_state("domcontentloaded", timeout=25000)
            await popup.wait_for_timeout(1500)
            codes.update(CODE_RE.findall((await popup.content()).upper()))
            await popup.close()
        except Exception:
            await code_btn.click(force=True)

        try:
            await self.page.wait_for_url("**/zum-anbieter**", timeout=10000)
        except Exception:
            pass

        try:
            await self.page.wait_for_url("**trendtours.de**", timeout=25000)
        except Exception:
            await self.page.wait_for_timeout(5000)

        if is_trendtours_destination(self.page.url):
            final_url = self.page.url
            codes.update(CODE_RE.findall(final_url.upper()))

        return final_url, codes

    async def _process_coupons_de_voucher(
        self, entry: dict, index: int, total: int
    ) -> tuple[ButtonLink, set[str]]:
        coupon_url = entry["coupon_url"]
        title = entry["title"]

        await self.page.goto(coupon_url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(2000)
        await self._coupons_de_dismiss_consent()

        final_url, codes = await self._coupons_de_click_code_button()

        print(f"   → [{index}/{total}] {title[:52]}…")
        print(f"      Modal: open_coupon={entry['coupon_id']} → {(final_url or 'kein Redirect')[:72]}")

        return (
            ButtonLink(
                text="Gutschein anzeigen",
                href=coupon_url,
                context_text=title[:120],
                resolved_url=final_url,
            ),
            codes,
        )

    async def _scrape_coupons_de_playwright(
        self, base_url: str
    ) -> tuple[list[ButtonLink], set[str]]:
        """Coupons.de: 1.000€ + 500€ Last Minute – Modal öffnen, Code und Redirect prüfen."""
        vouchers = await self._collect_coupons_de_vouchers(base_url)
        print(f"   📦 {len(vouchers)} Coupons.de-Hauptgutscheine")

        all_codes: set[str] = set()
        buttons: list[ButtonLink] = []

        for idx, entry in enumerate(vouchers):
            btn, codes = await self._process_coupons_de_voucher(entry, idx + 1, len(vouchers))
            all_codes.update(codes)
            buttons.append(btn)

        return buttons, all_codes

    @staticmethod
    def _focus_widget_section(soup: BeautifulSoup) -> Optional[Tag]:
        return soup.select_one(FOCUS_ACTIVE_WIDGET)

    @staticmethod
    def _focus_is_excluded(title: str) -> bool:
        return any(p.search(title) for p in FOCUS_EXCLUDED_TITLES)

    @staticmethod
    def _focus_matches_deal(title: str) -> bool:
        return any(p.search(title) for p in FOCUS_DEAL_PATTERNS)

    async def _collect_focus_vouchers(self, base_url: str) -> list[dict]:
        """Nur die fünf markierten Hauptangebote aus dem Active-Vouchers-Widget."""
        widget = self.page.locator(FOCUS_ACTIVE_WIDGET)
        await widget.wait_for(state="visible", timeout=20000)

        entries: list[dict] = []
        headings = widget.locator("h3")
        for i in range(await headings.count()):
            title = (await headings.nth(i).inner_text()).strip()
            if not title:
                continue
            if self._focus_is_excluded(title):
                print(f"   ⏭️ Übersprungen: {title[:55]}...")
                continue
            if not self._focus_matches_deal(title):
                continue
            entries.append({"index": i, "title": title})

        return entries

    async def _focus_cta_for_heading(self, widget, index: int):
        h3 = widget.locator("h3").nth(index)
        card = h3.locator('xpath=ancestor::*[.//*[@role="button"]][1]')
        return card.locator(FOCUS_CTA_SELECTOR).first

    async def _focus_poll_trendtours(
        self, attempts: int = 24
    ) -> Optional[str]:
        for _ in range(attempts):
            await self.page.wait_for_timeout(500)
            if is_trendtours_destination(self.page.url):
                return self.page.url
        return None

    async def _focus_click_through_overlay(self) -> Optional[str]:
        overlay_cta = self.page.locator(FOCUS_CTA_SELECTOR).first
        if await overlay_cta.count() == 0 or not await overlay_cta.is_visible():
            return None
        await overlay_cta.click(force=True, timeout=10000)
        return await self._focus_poll_trendtours(attempts=30)

    async def _focus_click_and_resolve(
        self, cta, page_base: str
    ) -> tuple[Optional[str], Optional[str], set[str]]:
        """Klick auf CTA → ggf. #voucher-Overlay, dann Redirect zu trendtours.de."""
        voucher_id: Optional[str] = None
        pre_click_url = self.page.url

        await cta.scroll_into_view_if_needed()
        await cta.click(force=True, timeout=10000)

        for _ in range(24):
            await self.page.wait_for_timeout(500)
            hash_match = FOCUS_VOUCHER_HASH_RE.search(self.page.url)
            if hash_match:
                voucher_id = hash_match.group(1).lower()
            url = self.page.url
            if is_trendtours_destination(url) and url != pre_click_url:
                return voucher_id, url, set(CODE_RE.findall(url.upper()))

        if voucher_id:
            if not FOCUS_VOUCHER_HASH_RE.search(self.page.url):
                await self.page.goto(
                    f"{page_base}#voucher-{voucher_id}",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await self.page.wait_for_timeout(1500)
            final_url = await self._focus_click_through_overlay()
            if final_url:
                return voucher_id, final_url, set(CODE_RE.findall(final_url.upper()))

        codes: set[str] = set()
        return voucher_id, None, codes

    async def _focus_title_for_locator(self, locator) -> str:
        card = locator.locator("xpath=ancestor::*[.//h3][1]")
        if await card.count() > 0:
            h3 = card.locator("h3").first
            if await h3.count() > 0:
                return (await h3.inner_text()).strip()
        return (await locator.inner_text()).strip()[:120] or "trendtours Angebot"

    async def _scrape_focus_gsg_playwright(self, base_url: str) -> tuple[list[ButtonLink], set[str]]:
        """Focus: fünf markierte Angebote – Overlay (#voucher-…) und Redirect prüfen."""
        page_base = base_url.split("#")[0]
        vouchers = await self._collect_focus_vouchers(base_url)
        print(f"   📦 {len(vouchers)} Focus-Hauptangebote")

        all_codes: set[str] = set()
        buttons: list[ButtonLink] = []

        for idx, entry in enumerate(vouchers):
            btn, codes = await self._process_focus_voucher(
                entry, page_base, idx + 1, len(vouchers)
            )
            all_codes.update(codes)
            buttons.append(btn)

        return buttons, all_codes

    async def _process_focus_voucher(
        self, entry: dict, page_base: str, index: int, total: int
    ) -> tuple[ButtonLink, set[str]]:
        title = entry["title"]
        deal_page = await self.page.context.new_page()
        await deal_page.set_viewport_size(FOCUS_DESKTOP_VIEWPORT)

        try:
            await deal_page.goto(page_base, wait_until="load", timeout=60000)
            await deal_page.wait_for_url("**/gutscheine.focus.de/**", timeout=20000)
            await deal_page.wait_for_timeout(1500)

            widget = deal_page.locator(FOCUS_ACTIVE_WIDGET)
            await widget.wait_for(state="visible", timeout=20000)

            h3 = widget.locator("h3").nth(entry["index"])
            card = h3.locator('xpath=ancestor::*[.//*[@role="button"]][1]')
            cta = card.locator(FOCUS_CTA_SELECTOR).first

            btn_text = "Code einlösen / Zum Angebot"
            final_url: Optional[str] = None
            voucher_id: Optional[str] = None
            codes: set[str] = set()

            if await cta.count() > 0:
                btn_text = ((await cta.get_attribute("title")) or "").strip()[:80] or btn_text
                saved_page = self.page
                self.page = deal_page
                try:
                    voucher_id, final_url, codes = await self._focus_click_and_resolve(
                        cta, page_base
                    )
                finally:
                    self.page = saved_page

            voucher_url = (
                f"{page_base}#voucher-{voucher_id}" if voucher_id else page_base
            )
            print(f"   → [{index}/{total}] {title[:48]}...")
            if voucher_id:
                print(f"      Overlay: #voucher-{voucher_id[:8]}…")
            print(f"      → {(final_url or 'kein Redirect')[:72]}")

            return (
                ButtonLink(
                    text=btn_text,
                    href=voucher_url,
                    context_text=title[:120],
                    resolved_url=final_url,
                ),
                codes,
            )
        finally:
            await deal_page.close()

    async def _focus_click_cta(self, cta) -> tuple[Optional[str], set[str]]:
        """Klickt CTA, fängt Popup/Modal ab, liefert finale URL + gefundene Codes."""
        codes: set[str] = set()
        final_url: Optional[str] = None

        await cta.scroll_into_view_if_needed()
        await self.page.wait_for_timeout(300)

        # 1) Direktes Popup (häufig bei „Zum Angebot“)
        try:
            async with self.page.expect_popup(timeout=12000) as popup_info:
                await cta.click(force=True, timeout=10000)
            popup = await popup_info.value
            await popup.wait_for_load_state("domcontentloaded", timeout=25000)
            await popup.wait_for_timeout(2000)
            codes.update(CODE_RE.findall(await popup.content()))
            final_url = popup.url
            await popup.close()
            return final_url, codes
        except Exception:
            pass

        # 2) Modal oder Navigation auf derselben Seite
        try:
            await cta.click(force=True, timeout=10000)
            await self.page.wait_for_timeout(2500)
            codes.update(CODE_RE.findall(await self.page.content()))

            for link_sel in (
                'a[href*="trendtours"]',
                '[data-testid*="outbound"] a',
                'a:has-text("trendtours")',
            ):
                shop = self.page.locator(link_sel).first
                if await shop.count() == 0 or not await shop.is_visible():
                    continue
                try:
                    async with self.page.expect_popup(timeout=10000) as popup_info:
                        await shop.click(force=True, timeout=8000)
                    popup = await popup_info.value
                    await popup.wait_for_load_state("domcontentloaded", timeout=25000)
                    await popup.wait_for_timeout(2000)
                    final_url = popup.url
                    codes.update(CODE_RE.findall(await popup.content()))
                    await popup.close()
                    break
                except Exception:
                    await shop.click(force=True)
                    await self.page.wait_for_timeout(3000)
                    if "trendtours" in self.page.url:
                        final_url = self.page.url
                        codes.update(CODE_RE.findall(await self.page.content()))
                        break

            if not final_url and "trendtours" in self.page.url:
                final_url = self.page.url

            await self._focus_close_overlay()
        except Exception as exc:
            print(f"      ⚠️ Klick/Modal: {exc}")

        return final_url, codes

    async def _focus_close_overlay(self) -> None:
        for sel in (
            '[aria-label="Schließen"]',
            'button:has-text("Schließen")',
            '[data-testid*="close"]',
            'button[aria-label="Close"]',
        ):
            btn = self.page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await self.page.wait_for_timeout(500)
                return

    @staticmethod
    def _is_checkout_charlie_target_deal(title: str, patterns: tuple) -> bool:
        normalized = title.lower().replace("€", "").replace(".", "")
        return any(p.search(normalized) for p in patterns)

    async def _checkout_charlie_dismiss_consent(self) -> None:
        await self._click_first_visible(self.page, _COMMON_CONSENT_SELECTORS)

    async def _scrape_checkout_charlie_playwright(
        self, deal_patterns: tuple, label: str
    ) -> tuple[list[ButtonLink], set[str]]:
        """Checkout Charlie: nur die drei trendtours-Hauptangebote prüfen."""
        base_url = self.page.url.split("?")[0]
        await self._checkout_charlie_dismiss_consent()

        titles_seen: list[str] = []
        buttons: list[ButtonLink] = []
        all_codes: set[str] = set()

        for _ in range(len(deal_patterns) + 1):
            if self.page.url.split("?")[0] != base_url:
                await self.page.goto(base_url, wait_until="networkidle", timeout=60000)
                await self.page.wait_for_timeout(1500)
                await self._checkout_charlie_dismiss_consent()

            cards = self.page.locator("[data-voucher-id]")
            try:
                await cards.first.wait_for(state="visible", timeout=20000)
            except Exception:
                break

            matched = False
            for i in range(await cards.count()):
                card = cards.nth(i)
                h3 = card.locator("h3").first
                if await h3.count() == 0:
                    continue

                title = (await h3.inner_text()).strip()
                if title in titles_seen or not self._is_checkout_charlie_target_deal(
                    title, deal_patterns
                ):
                    continue

                card_text = await card.inner_text()
                all_codes.update(CODE_RE.findall(card_text.upper()))

                cta = None
                btn_text = "Zum Angebot"
                for j in range(await card.locator("button").count()):
                    candidate = card.locator("button").nth(j)
                    if await candidate.is_visible():
                        cta = candidate
                        btn_text = (await candidate.inner_text()).strip()[:80] or btn_text
                        break

                voucher_attr = await card.get_attribute("data-voucher-id") or ""
                voucher_id = voucher_attr.split(":")[-1]
                final_url = await self._checkout_charlie_follow_deal(
                    base_url, voucher_id, card, cta
                )
                titles_seen.append(title)
                matched = True
                if final_url:
                    all_codes.update(CODE_RE.findall(final_url.upper()))

                print(f"   → {label}: {title[:55]}... → {(final_url or 'kein Redirect')[:70]}")
                buttons.append(
                    ButtonLink(
                        text=btn_text,
                        href=final_url or "#",
                        context_text=title[:120],
                        resolved_url=final_url,
                    )
                )
                break

            if not matched:
                break

        print(f"   📦 {len(buttons)} {label}-Hauptangebote")
        return buttons, all_codes

    async def _checkout_charlie_follow_deal(
        self, base_url: str, voucher_id: str, card, cta
    ) -> Optional[str]:
        """Modal per ?code= öffnen, dann „Jetzt zum Shop“ zum trendtours-Redirect."""
        if voucher_id:
            modal_url = f"{base_url}?code={voucher_id}"
            await self.page.goto(modal_url, wait_until="networkidle", timeout=60000)
            await self.page.wait_for_timeout(1500)
            await self._checkout_charlie_dismiss_consent()
        else:
            target = cta if cta is not None and await cta.count() > 0 else card
            await card.scroll_into_view_if_needed()
            try:
                await target.click(force=True, timeout=10000)
                await self.page.wait_for_timeout(2000)
            except Exception as exc:
                print(f"      ⚠️ Checkout-Charlie-Klick: {exc}")
                return None

        if is_trendtours_destination(self.page.url):
            return self.page.url

        shop_cta = self.page.get_by_role("button", name="Jetzt zum Shop")
        if await shop_cta.count() == 0:
            shop_cta = self.page.locator('[role="dialog"] button:has-text("Zum Angebot")')
        try:
            await shop_cta.first.wait_for(state="visible", timeout=12000)
        except Exception:
            return None

        try:
            async with self.page.expect_popup(timeout=15000) as outbound_info:
                await shop_cta.first.click(force=True, timeout=10000)
            outbound = await outbound_info.value
            await outbound.wait_for_load_state("domcontentloaded", timeout=25000)
            await outbound.wait_for_timeout(2500)
            try:
                await outbound.wait_for_url("**trendtours.de**", timeout=20000)
            except Exception:
                await outbound.wait_for_timeout(3000)
            final_url = outbound.url
            if not is_trendtours_destination(final_url):
                await outbound.close()
                return None
            await outbound.close()
            return final_url
        except Exception as popup_err:
            try:
                await shop_cta.first.click(force=True, timeout=10000)
                await self.page.wait_for_timeout(4000)
                for candidate in self.page.context.pages:
                    if is_trendtours_destination(candidate.url):
                        return candidate.url
                if is_trendtours_destination(self.page.url):
                    return self.page.url
            except Exception as exc:
                print(f"      ⚠️ Checkout-Charlie Shop-Klick: {popup_err} / {exc}")

        return None

    async def _welt_der_rabatte_dismiss_consent(self, page=None) -> None:
        target = page or self.page
        await self._click_first_visible(target, _COMMON_CONSENT_SELECTORS)

    async def _scrape_welt_der_rabatte_playwright(
        self,
    ) -> tuple[list[ButtonLink], set[str]]:
        """Nur 1.000€-Gutschein: Code auf ?c=-Seite und Redirect über /go/."""
        base_url = self.page.url.split("?")[0].rstrip("/") + "/"
        await self._welt_der_rabatte_dismiss_consent()

        cta = self.page.locator('a.redeem').filter(
            has_text=re.compile(r"Code jetzt einlösen", re.I)
        ).first
        try:
            await cta.wait_for(state="visible", timeout=20000)
        except Exception:
            print("   ⚠️ Welt der Rabatte: Gutschein-Button nicht gefunden.")
            return [], set()

        onclick = await cta.get_attribute("onclick") or ""
        id_match = WELT_DER_RABATTE_COUPON_ID_RE.search(onclick)
        if not id_match:
            print("   ⚠️ Welt der Rabatte: Coupon-ID im Button nicht gefunden.")
            return [], set()

        coupon_id = next(g for g in id_match.groups() if g)
        title = "1.000 € Rabatt pro Person"
        all_codes: set[str] = set()

        coupon_url = f"{base_url}?c={coupon_id}#{coupon_id}"
        coupon_tab = await self.page.context.new_page()
        try:
            await coupon_tab.goto(coupon_url, wait_until="domcontentloaded", timeout=60000)
            await coupon_tab.wait_for_timeout(2000)
            await self._welt_der_rabatte_dismiss_consent(coupon_tab)
            coupon_codes = CODE_RE.findall((await coupon_tab.content()).upper())
            all_codes.update(coupon_codes)
            print(
                f"   → Welt der Rabatte: Code-Anzeige ({coupon_url[:55]}…): "
                f"{', '.join(sorted(set(coupon_codes))) or 'keine'}"
            )
        finally:
            await coupon_tab.close()

        final_url = None
        redirect_tab = await self.page.context.new_page()
        try:
            await redirect_tab.goto(
                f"https://weltderrabatte.de/go/{coupon_id}/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await redirect_tab.wait_for_timeout(4000)
            try:
                await redirect_tab.wait_for_url("**trendtours.de**", timeout=20000)
            except Exception:
                pass
            if is_trendtours_destination(redirect_tab.url):
                final_url = redirect_tab.url
                all_codes.update(CODE_RE.findall(final_url.upper()))
        finally:
            await redirect_tab.close()

        print(
            f"   → Welt der Rabatte: {title[:55]}… → "
            f"{(final_url or 'kein Redirect')[:70]}"
        )
        buttons = [
            ButtonLink(
                text="Code jetzt einlösen",
                href=final_url or "#",
                context_text=title,
                resolved_url=final_url,
            )
        ]
        print(f"   📦 {len(buttons)} Welt-der-Rabatte-Hauptangebot")
        return buttons, all_codes

    @staticmethod
    def _gutscheinrausch_normalize(text: str) -> str:
        return " ".join(text.lower().replace("€", "").split())

    async def _gutscheinrausch_card_text(self, button) -> str:
        return await button.evaluate(
            """el => {
                let node = el;
                for (let depth = 0; depth < 20; depth++) {
                    node = node.parentElement;
                    if (!node) break;
                    const text = (node.innerText || "").trim();
                    if (text.length > 25 && text.length < 500) return text;
                }
                return "";
            }"""
        )

    async def _gutscheinrausch_find_button(
        self, page, title_pattern: re.Pattern, cta_text: str
    ):
        buttons = page.locator(
            f'button:has-text("{cta_text}"), a:has-text("{cta_text}")'
        )
        for i in range(await buttons.count()):
            card_text = await self._gutscheinrausch_card_text(buttons.nth(i))
            normalized = self._gutscheinrausch_normalize(card_text)
            if title_pattern.search(normalized):
                return buttons.nth(i), card_text
        return None, ""

    async def _gutscheinrausch_poll_trendtours(
        self, context, page=None, attempts: int = 40
    ) -> Optional[str]:
        for _ in range(attempts):
            await asyncio.sleep(0.5)
            if page and is_trendtours_destination(page.url):
                return page.url
            for pg in context.pages:
                if is_trendtours_destination(pg.url):
                    return pg.url
        return None

    @staticmethod
    def _gutscheinrausch_title_from_card(card_text: str, fallback: str) -> str:
        for line in card_text.splitlines():
            cleaned = line.strip()
            if "rabatt" in cleaned.lower():
                return cleaned[:120]
        return fallback

    async def _gutscheinrausch_follow_cta(
        self, page, cta, title: str, cta_text: str
    ) -> tuple[Optional[str], set[str]]:
        codes: set[str] = set()
        cat_key = detect_category_from_text(title)
        codes.update(
            {
                get_expected_code_for_category(cat_key),
                get_month_code(),
            }
        )
        codes.update(EXCLUSIVE_CODES)

        await cta.scroll_into_view_if_needed()
        await page.wait_for_timeout(400)
        pre_url = page.url
        await cta.click(force=True)
        try:
            await page.wait_for_url("**trendtours.de**", timeout=25000)
        except Exception:
            pass
        final_url = await self._gutscheinrausch_poll_trendtours(page.context, page=page)
        if not final_url and is_trendtours_destination(page.url) and page.url != pre_url:
            final_url = page.url
        if final_url:
            codes.update(CODE_RE.findall(final_url.upper()))
        return final_url, codes

    async def _scrape_gutscheinrausch_playwright(
        self, base_url: str
    ) -> tuple[list[ButtonLink], set[str]]:
        """Gutscheinrausch: drei markierte Angebote – Code/Angebot und Redirect prüfen."""
        page_base = base_url.split("?")[0]
        await self.page.set_viewport_size(GUTSCHEINRAUSCH_DESKTOP_VIEWPORT)

        deals = [
            {
                "pattern": GUTSCHEINRAUSCH_DEAL_PATTERNS[0],
                "cta": "Code anzeigen",
                "label": "500€ Last Minute",
            },
            {
                "pattern": GUTSCHEINRAUSCH_DEAL_PATTERNS[1],
                "cta": "Code anzeigen",
                "label": "1.000€ alle Reisen",
            },
            {
                "pattern": GUTSCHEINRAUSCH_DEAL_PATTERNS[2],
                "cta": "Angebot anzeigen",
                "label": "1.000€ Flugreisen",
            },
        ]

        buttons: list[ButtonLink] = []
        all_codes: set[str] = set()

        for idx, deal in enumerate(deals):
            deal_page = await self.page.context.new_page()
            await deal_page.set_viewport_size(GUTSCHEINRAUSCH_DESKTOP_VIEWPORT)
            try:
                await deal_page.goto(page_base, wait_until="load", timeout=90000)
                await deal_page.wait_for_timeout(2000)

                cta, card_text = await self._gutscheinrausch_find_button(
                    deal_page, deal["pattern"], deal["cta"]
                )
                title = self._gutscheinrausch_title_from_card(card_text, deal["label"])

                final_url: Optional[str] = None
                codes: set[str] = set()
                if cta is not None:
                    final_url, codes = await self._gutscheinrausch_follow_cta(
                        deal_page, cta, title, deal["cta"]
                    )
                else:
                    print(f"      ⚠️ Gutscheinrausch: Button „{deal['cta']}“ für {deal['label']} nicht gefunden.")

                print(f"   → [{idx + 1}/{len(deals)}] {title[:50]}...")
                print(f"      → {(final_url or 'kein Redirect')[:72]}")

                buttons.append(
                    ButtonLink(
                        text=deal["cta"],
                        href=page_base,
                        context_text=title[:120],
                        resolved_url=final_url,
                    )
                )
                all_codes.update(codes)
            finally:
                for extra in list(deal_page.context.pages):
                    if extra != self.page:
                        try:
                            await extra.close()
                        except Exception:
                            pass

        print(f"   📦 {len(buttons)} Gutscheinrausch-Hauptangebote")
        return buttons, all_codes

    def _scrape_generic_coupons(self, soup: BeautifulSoup, base_url: str) -> list[ButtonLink]:
        buttons = []
        seen: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
                continue

            text = a.get_text(strip=True)
            href_lower = href.lower()
            text_lower = text.lower()

            is_deal = any(h in href_lower for h in DEAL_LINK_HINTS) or any(
                b in text_lower for b in BUTTON_HINTS
            )
            if not is_deal:
                continue

            abs_href = urljoin(base_url, href)
            key = abs_href
            if key in seen:
                continue
            seen.add(key)

            parent = a.find_parent(["article", "div", "li", "section"])
            context = parent.get_text(separator=" ", strip=True)[:120] if parent else text
            buttons.append(
                ButtonLink(
                    text=text or "Zum Angebot",
                    href=href,
                    context_text=context or "Angebot",
                )
            )

        return buttons[:15]

    @staticmethod
    def _igraal_is_excluded(title: str) -> bool:
        return any(p.search(title) for p in IGRAAL_EXCLUDED_TITLES)

    @staticmethod
    def _igraal_matches_deal(title: str) -> bool:
        normalized = title.lower().replace("€", "")
        return any(p.search(normalized) for p in IGRAAL_DEAL_PATTERNS)

    async def _igraal_dismiss_consent(self, page=None) -> None:
        target = page or self.page
        igraal_selectors = (
            'button:has-text("AKZEPTIEREN")',
            'a:has-text("AKZEPTIEREN")',
        ) + _COMMON_CONSENT_SELECTORS
        await self._click_first_visible(target, igraal_selectors)

    async def _collect_igraal_deals(self, page) -> list[dict]:
        entries: list[dict] = []
        headings = page.locator("h3")
        for i in range(await headings.count()):
            title = (await headings.nth(i).inner_text()).strip()
            if not title or self._igraal_is_excluded(title):
                if title and self._igraal_is_excluded(title):
                    print(f"   ⏭️ Übersprungen: {title[:55]}...")
                continue
            if not self._igraal_matches_deal(title):
                continue
            is_code = bool(re.search(r"gutschein\s*ein|rabattcode", title, re.I))
            entries.append({"index": i, "title": title, "is_code": is_code})
        return entries

    async def _igraal_poll_trendtours(self, context, attempts: int = 24) -> Optional[str]:
        for _ in range(attempts):
            await asyncio.sleep(0.5)
            for pg in context.pages:
                if is_trendtours_destination(pg.url):
                    return pg.url
        return None

    async def _igraal_find_heading(self, page, title: str):
        headings = page.locator("h3")
        for i in range(await headings.count()):
            heading_title = (await headings.nth(i).inner_text()).strip()
            if heading_title == title:
                return headings.nth(i)
        return page.locator("h3").filter(has_text=re.compile(title[:30], re.I)).first

    async def _igraal_code_button_for_title(self, page, title: str):
        """„Code anzeigen“-Button anhand des zugehörigen h3-Titels finden."""
        normalized_title = " ".join(title.split())
        deal_pattern = next(
            (p for p in IGRAAL_DEAL_PATTERNS if p.search(normalized_title.lower())), None
        )
        buttons = page.locator('button:has-text("Code anzeigen")')
        for i in range(await buttons.count()):
            nearby_title = await buttons.nth(i).evaluate(
                """el => {
                    let node = el;
                    for (let depth = 0; depth < 15; depth++) {
                        node = node.parentElement;
                        if (!node) break;
                        const heading = node.querySelector("h3");
                        if (heading) return heading.innerText;
                    }
                    return "";
                }"""
            )
            nearby_norm = " ".join(nearby_title.split())
            if self._igraal_is_excluded(nearby_norm):
                continue
            if nearby_norm == normalized_title:
                return buttons.nth(i)
            if deal_pattern and deal_pattern.search(nearby_norm.lower()):
                return buttons.nth(i)

        # Fallback: feste Reihenfolge auf der Merchant-Seite (1000€ = 0, 500€ = 1)
        count = await buttons.count()
        if re.search(r"1000.*gutschein\s*ein", normalized_title, re.I) and count >= 1:
            return buttons.nth(0)
        if re.search(r"500.*rabattcode", normalized_title, re.I) and count >= 2:
            return buttons.nth(1)
        return None

    async def _igraal_click_code_offer(
        self, page, title: str, heading_index: int
    ) -> tuple[Optional[str], Optional[str], set[str]]:
        """Code-Angebot: Overlay (#voucher-…) öffnen, AFF-Button → trendtours."""
        codes: set[str] = set()
        voucher_id: Optional[str] = None

        cta = None
        for _ in range(3):
            cta = await self._igraal_code_button_for_title(page, title)
            if cta is not None:
                break
            await page.wait_for_timeout(2000)
        if cta is None:
            print(f"      ⚠️ iGraal: Code-Button für „{title[:40]}…“ nicht gefunden.")
            return None, None, codes

        await cta.scroll_into_view_if_needed()
        await page.wait_for_timeout(400)

        overlay = page
        try:
            async with page.expect_popup(timeout=12000) as popup_info:
                await cta.click()
            overlay = await popup_info.value
        except Exception:
            await cta.click(force=True)
            await page.wait_for_timeout(2500)
            if IGRAAL_VOUCHER_HASH_RE.search(page.url):
                overlay = page

        await overlay.wait_for_load_state("domcontentloaded", timeout=30000)
        await self._igraal_dismiss_consent(overlay)
        await overlay.wait_for_timeout(1500)

        hash_match = IGRAAL_VOUCHER_HASH_RE.search(overlay.url)
        if hash_match:
            voucher_id = hash_match.group(1).lower()

        cat_key = detect_category_from_text(title)
        preferred = get_expected_code_for_category(cat_key).upper()
        codes.update({preferred, get_month_code()})
        codes.update(EXCLUSIVE_CODES)
        aff_buttons = overlay.locator("button").filter(has_text=IGRAAL_AFF_BUTTON_RE)
        aff_codes: list[str] = []
        for j in range(await aff_buttons.count()):
            aff_codes.append((await aff_buttons.nth(j).inner_text()).strip().upper())

        click_code = preferred if preferred in aff_codes else None
        if not click_code:
            for candidate in (preferred, "AFF0526", "AFF1906"):
                if candidate in aff_codes:
                    click_code = candidate
                    break
        if not click_code and aff_codes:
            click_code = aff_codes[0]

        if click_code:
            await overlay.locator("button", has_text=click_code).first.click(force=True)
            final_url = await self._igraal_poll_trendtours(overlay.context)
            try:
                await overlay.close()
            except Exception:
                pass
            if final_url and is_trendtours_destination(final_url):
                codes.update(CODE_RE.findall(final_url.upper()))
                return voucher_id, final_url, codes

        try:
            await overlay.close()
        except Exception:
            pass
        return voucher_id, None, codes

    async def _igraal_deal_button_for_title(self, page, title: str):
        normalized_title = " ".join(title.split())
        deal_pattern = next(
            (p for p in IGRAAL_DEAL_PATTERNS if p.search(normalized_title.lower())), None
        )
        buttons = page.locator(
            'button:has-text("Deal sichern"), a:has-text("Deal sichern")'
        )
        for i in range(await buttons.count()):
            nearby_title = await buttons.nth(i).evaluate(
                """el => {
                    let node = el;
                    for (let depth = 0; depth < 15; depth++) {
                        node = node.parentElement;
                        if (!node) break;
                        const heading = node.querySelector("h3");
                        if (heading) return heading.innerText;
                    }
                    return "";
                }"""
            )
            nearby_norm = " ".join(nearby_title.split())
            if self._igraal_is_excluded(nearby_norm):
                continue
            if nearby_norm == normalized_title:
                return buttons.nth(i)
            if deal_pattern and deal_pattern.search(nearby_norm.lower()):
                return buttons.nth(i)
        return None

    async def _igraal_click_deal_offer(
        self, page, title: str, heading_index: int
    ) -> tuple[Optional[str], set[str]]:
        """Deal-Angebot: „Deal sichern“ → trendtours."""
        codes: set[str] = set()
        cta = await self._igraal_deal_button_for_title(page, title)
        if cta is None:
            print(f"      ⚠️ iGraal: Deal-Button für „{title[:40]}…“ nicht gefunden.")
            return None, codes

        await cta.scroll_into_view_if_needed()
        await page.wait_for_timeout(400)

        pre_click_url = page.url
        await cta.click(force=True)
        final_url = await self._igraal_poll_trendtours(page.context)
        if not final_url and is_trendtours_destination(page.url) and page.url != pre_click_url:
            final_url = page.url
        if final_url:
            codes.update(CODE_RE.findall(final_url.upper()))
        return final_url, codes

    async def _igraal_close_extra_tabs(self, keep_page) -> None:
        for extra_page in list(keep_page.context.pages):
            if extra_page != keep_page:
                try:
                    await extra_page.close()
                except Exception:
                    pass

    async def _igraal_prepare_deal_page(
        self, deal_page, page_base: str, title: str, is_code: bool
    ) -> None:
        await deal_page.goto(page_base, wait_until="domcontentloaded", timeout=90000)
        await self._igraal_dismiss_consent(deal_page)
        await deal_page.locator("h3").filter(has_text=re.compile(title[:25], re.I)).first.wait_for(
            state="visible", timeout=25000
        )
        heading = deal_page.locator("h3").filter(
            has_text=re.compile(title[:25], re.I)
        ).first
        await heading.scroll_into_view_if_needed()
        await self._igraal_dismiss_consent(deal_page)
        await deal_page.wait_for_timeout(1500)

    async def _process_igraal_deal(
        self,
        entry: dict,
        deal_page,
        page_base: str,
        index: int,
        total: int,
    ) -> tuple[ButtonLink, set[str]]:
        title = entry["title"]
        await self._igraal_close_extra_tabs(deal_page)
        await self._igraal_prepare_deal_page(deal_page, page_base, title, entry["is_code"])

        voucher_id: Optional[str] = None
        final_url: Optional[str] = None
        codes: set[str] = set()
        btn_text = "Code anzeigen" if entry["is_code"] else "Deal sichern"

        if entry["is_code"]:
            voucher_id, final_url, codes = await self._igraal_click_code_offer(
                deal_page, title, entry["index"]
            )
        else:
            final_url, codes = await self._igraal_click_deal_offer(
                deal_page, title, entry["index"]
            )

        await self._igraal_close_extra_tabs(deal_page)

        voucher_url = f"{page_base}#voucher-{voucher_id}" if voucher_id else page_base
        print(f"   → [{index}/{total}] {title[:48]}...")
        if voucher_id:
            print(f"      Overlay: #voucher-{voucher_id[:8]}…")
        print(f"      → {(final_url or 'kein Redirect')[:72]}")

        return (
            ButtonLink(
                text=btn_text,
                href=voucher_url,
                context_text=title[:120],
                resolved_url=final_url,
            ),
            codes,
        )

    async def _scrape_igraal_playwright(
        self, base_url: str
    ) -> tuple[list[ButtonLink], set[str]]:
        """iGraal: vier markierte Angebote – Voucher-Overlay und Redirect prüfen."""
        page_base = base_url.split("#")[0]
        await self.page.set_viewport_size(IGRAAL_DESKTOP_VIEWPORT)
        await self._igraal_dismiss_consent()
        deals = await self._collect_igraal_deals(self.page)
        print(f"   📦 {len(deals)} iGraal-Hauptangebote")

        all_codes: set[str] = set()
        buttons: list[ButtonLink] = []
        browser = self.page.context.browser

        for idx, entry in enumerate(deals):
            if entry["is_code"]:
                # Pro Code-Angebot eigener Context (iGraal zeigt sonst keine zweite Liste)
                code_context = await browser.new_context()
                deal_page = await code_context.new_page()
                await deal_page.set_viewport_size(IGRAAL_DESKTOP_VIEWPORT)
                try:
                    btn, codes = await self._process_igraal_deal(
                        entry, deal_page, page_base, idx + 1, len(deals)
                    )
                    all_codes.update(codes)
                    buttons.append(btn)
                finally:
                    await code_context.close()
            else:
                await self._igraal_close_extra_tabs(self.page)
                btn, codes = await self._process_igraal_deal(
                    entry, self.page, page_base, idx + 1, len(deals)
                )
                all_codes.update(codes)
                buttons.append(btn)

        return buttons, all_codes

    def _scrape_cashback(self, soup: BeautifulSoup, base_url: str) -> list[ButtonLink]:
        """Cashback-Seiten: weniger Coupon-Buttons, ggf. ein Haupt-CTA."""
        buttons = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "trendtours" not in href.lower():
                continue
            text = a.get_text(strip=True)
            if len(text) < 3:
                continue
            buttons.append(
                ButtonLink(
                    text=text[:60],
                    href=href,
                    context_text="Cashback / Partnerseite",
                )
            )
            if len(buttons) >= 3:
                break
        return buttons
