"""
Stealth Browser Setup mit Playwright.
Simuliert ein Android-Gerät um Bot-Detection zu umgehen.
"""

import asyncio
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import stealth_async

from config.settings import settings


class StealthBrowser:
    """Verwaltet eine Stealth-Browser-Instanz."""
    
    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
    
    async def start(self) -> None:
        """Startet den Browser."""
        self._playwright = await async_playwright().start()
        
        self._browser = await self._playwright.chromium.launch(
            headless=settings.headless,
            slow_mo=settings.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
                "--ignore-certificate-errors-spki-list",
            ],
        )
    
    async def stop(self) -> None:
        """Stoppt den Browser und räumt auf."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
    
    async def new_context(self) -> BrowserContext:
        """
        Erstellt einen neuen Browser-Context mit Mobile-Emulation.
        """
        if not self._browser:
            raise RuntimeError("Browser nicht gestartet. Erst start() aufrufen.")
        
        context = await self._browser.new_context(
            user_agent=settings.user_agent,
            viewport={
                "width": settings.viewport_width,
                "height": settings.viewport_height,
            },
            device_scale_factor=2.5,
            is_mobile=True,
            has_touch=True,
            locale="de-DE",
            timezone_id="Europe/Berlin",
            geolocation={"latitude": 52.52, "longitude": 13.405},  # Berlin
            permissions=["geolocation"],
        )
        
        return context
    
    async def new_stealth_page(self, context: BrowserContext) -> Page:
        """
        Erstellt eine neue Seite mit Stealth-Einstellungen.
        """
        page = await context.new_page()
        
        # Stealth-Skripte injizieren
        await stealth_async(page)
        
        # Zusätzliche Evasion-Techniken
        await page.add_init_script("""
            // WebDriver Property verstecken
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Chrome Runtime simulieren
            window.chrome = {
                runtime: {}
            };
            
            // Permissions API anpassen
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // Plugin-Array füllen
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            // Languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['de-DE', 'de', 'en-US', 'en']
            });
        """)
        
        # Standard-Timeout setzen
        page.set_default_timeout(settings.timeout)
        
        return page


@asynccontextmanager
async def get_stealth_browser():
    """
    Context Manager für einfache Browser-Nutzung.
    
    Usage:
        async with get_stealth_browser() as (browser, context, page):
            await page.goto("[example.com](https://example.com)")
    """
    browser = StealthBrowser()
    await browser.start()
    
    try:
        context = await browser.new_context()
        page = await browser.new_stealth_page(context)
        yield browser, context, page
    finally:
        await browser.stop()


async def test_stealth():
    """Testet ob der Stealth-Browser funktioniert."""
    async with get_stealth_browser() as (browser, context, page):
        # Bot-Detection Test
        await page.goto("https://bot.sannysoft.com/")
        await page.wait_for_timeout(3000)
        
        # Screenshot speichern
        await page.screenshot(path="stealth_test.png", full_page=True)
        print("Screenshot gespeichert: stealth_test.png")
        
        # Webdriver-Check
        webdriver_value = await page.evaluate("() => navigator.webdriver")
        print(f"navigator.webdriver = {webdriver_value}")
        
        if webdriver_value is None or webdriver_value is False:
            print("✅ Stealth-Modus aktiv!")
        else:
            print("❌ Bot wurde erkannt!")


if __name__ == "__main__":
    asyncio.run(test_stealth())
