"""
Aktive Top-Publisher (laut QC-Vorgabe, ohne durchgestrichene Partner).
"""
from dataclasses import dataclass
from typing import Literal, Optional

ScraperProfile = Literal[
    "shopclever",
    "generic_coupons",
    "coupons_de",
    "focus_gsg",
    "cashback",
    "igraal",
    "sparwelt",
    "gutscheine_de",
    "welt_der_rabatte",
    "gutscheinrausch",
]
CheckType = Literal["codes", "redirects", "expired", "logo"]


@dataclass(frozen=True)
class PublisherEntry:
    name: str
    url: str
    group: str
    scraper: ScraperProfile
    checks: tuple[CheckType, ...]


# Prüfpunkte je Profil (nicht jeder Publisher braucht alle)
_CHECKS_COUPON = ("codes", "redirects", "expired", "logo")
_CHECKS_GENERIC = ("codes", "redirects", "expired")
_CHECKS_CASHBACK = ("codes", "logo")

PUBLISHERS: list[PublisherEntry] = [
    PublisherEntry(
        name="Welt der Rabatte",
        url="https://weltderrabatte.de/shop/trendtours-touristik/",
        group="Welt der Rabatte",
        scraper="welt_der_rabatte",
        checks=_CHECKS_GENERIC,
    ),
     # Global Savings Group
    PublisherEntry(
        name="Focus Gutscheine",
        url="https://gutscheine.focus.de/gutscheine/trendtours",
        group="Global Savings Group",
        scraper="focus_gsg",
        checks=_CHECKS_GENERIC,
    ),

    PublisherEntry(
        name="ShopClever",
        url="https://www.shopclever.de/trendtours-gutschein",
        group="ShopClever",
        scraper="shopclever",
        checks=_CHECKS_COUPON,
    ),
    PublisherEntry(
        name="Coupons.de",
        url="https://www.coupons.de/gutscheine/trendtours",
        group="Coupons",
        scraper="coupons_de",
        checks=_CHECKS_GENERIC,
    ),
   
   # PublisherEntry(
    #    name="Shoop",
     #   url="https://www.shoop.de/cashback/trendtours_touristik",
      #  group="Global Savings Group",
       # scraper="cashback",
        #checks=_CHECKS_CASHBACK,
    #),
    PublisherEntry(
        name="Igraal",
        url="https://de.igraal.com/gutschein/trendtours",
        group="Global Savings Group",
        scraper="igraal",
        checks=_CHECKS_GENERIC,
    ),
    PublisherEntry(
        name="Gutscheinrausch",
        url="https://www.gutscheinrausch.de/gutscheine/trendtours/",
        group="Gutscheinrausch",
        scraper="gutscheinrausch",
        checks=_CHECKS_GENERIC,
    ),
   # PublisherEntry(
   #     name="Gutscheine.Codes",
    #    url="https://gutscheine.codes/gutscheine/trendtours.de",
     #   group="Gutscheine.Codes",
      #  scraper="generic_coupons",
       # checks=_CHECKS_GENERIC,
  #  ),
   
    # Checkout Charlie
    PublisherEntry(
        name="Sparwelt",
        url="https://www.sparwelt.de/gutscheine/trendtours-touristik",
        group="Checkout Charlie",
        scraper="sparwelt",
        checks=_CHECKS_GENERIC,
    ),
    PublisherEntry(
        name="Gutscheine.de",
        url="https://www.gutscheine.de/trendtours-touristik",
        group="Checkout Charlie",
        scraper="gutscheine_de",
        checks=_CHECKS_GENERIC,
    ),
    #PublisherEntry(
     #   name="Spiegel Gutscheine",
      #  url="https://gutscheine.spiegel.de/trendtours-touristik",
       # group="Checkout Charlie",
        #scraper="generic_coupons",
        #checks=_CHECKS_GENERIC,
    #),
]


def get_publisher_by_url(url: str) -> Optional[PublisherEntry]:
    for p in PUBLISHERS:
        if p.url.rstrip("/") == url.rstrip("/"):
            return p
    return None
