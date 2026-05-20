"""
Überprüft die Ziel-URLs von gefundenen Buttons durch aktives Anklicken/Navigieren.
"""
import asyncio
from urllib.parse import urljoin
from playwright.async_api import BrowserContext

# Typische Wörter in Affiliate-Links oder Button-Texten, 
# um Menü-Links von echten Angeboten zu unterscheiden.
AFFILIATE_KEYWORDS = ["go", "out", "deal", "visit", "redirect", "coupon"]
BUTTON_KEYWORDS = ["anzeigen", "angebot", "einlösen", "gutschein", "rabatt", "sichern"]

def is_likely_deal_link(href: str, text: str) -> bool:
    """Filtert unwichtige Links (wie Menüs oder Footer) heraus."""
    href_lower = href.lower()
    text_lower = text.lower()
    
    # 1. Check auf typische Affiliate-Pfade im Link
    if any(keyword in href_lower for keyword in AFFILIATE_KEYWORDS):
        return True
        
    # 2. Check auf typische Button-Texte
    if any(keyword in text_lower for keyword in BUTTON_KEYWORDS):
        return True
        
    # 3. Wenn "trendtours" direkt im Link steht
    if "trendtours" in href_lower and not href_lower.startswith("/"):
        return True
        
    return False

class RedirectChecker:
    def __init__(self, context: BrowserContext):
        self.context = context

    async def get_final_url(self, base_url: str, href: str) -> str:
        """
        Navigiert zur angegebenen URL, wartet alle Redirects ab
        und gibt die finale Ziel-URL zurück.
        """
        # Falls der Link relativ ist (z.B. /out/trendtours), machen wir ihn absolut
        absolute_url = urljoin(base_url, href)
        
        # Öffne einen neuen Tab für den Klick, um die Hauptseite nicht zu verlassen
        page = await self.context.new_page()
        final_url = ""
        
        try:
            # domcontentloaded reicht meistens, um den Redirect-Trigger zu feuern
            await page.goto(absolute_url, wait_until="domcontentloaded", timeout=15000)
            
            # Affiliate-Netzwerke nutzen oft noch JavaScript-Redirects, 
            # daher geben wir der Seite noch kurz Zeit zum "Ankommen".
            await page.wait_for_timeout(3000)
            
            final_url = page.url
        except Exception as e:
            print(f"⚠️ Fehler beim Auflösen von {absolute_url}: {e}")
            final_url = "ERROR"
        finally:
            await page.close()
            
        return final_url

if __name__ == "__main__":
    # Test-Block
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from scraper.browser import get_stealth_browser
    
    async def test_redirects():
        async with get_stealth_browser() as (browser, context, page):
            checker = RedirectChecker(context)
            
            # Test 1: Ein typischer relativer Affiliate-Link eines Publishers
            # (Wir simulieren hier, dass wir auf shopclever.de auf einen Button klicken)
            test_base = "https://www.shopclever.de"
            test_href = "/out/trendtours" # Fiktiver oder echter Pfad
            
            print(f"Teste Redirect für: {test_base}{test_href}")
            
            # WICHTIG: Da /out/trendtours hier nur als Platzhalter dient, 
            # testen wir zur Sicherheit mal direkt mit einem Dummy-Redirect
            final_url = await checker.get_final_url("https://httpbin.org", "/redirect-to?url=https://www.trendtours.de/busreisen")
            
            print(f"🏁 Finale URL lautet: {final_url}")

    asyncio.run(test_redirects())