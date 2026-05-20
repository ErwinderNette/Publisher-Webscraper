"""
Lädt und bereitet die Referenzdaten aus Google Sheets auf.
"""
import pandas as pd
from config.reference import (
    get_category_target_url,
    get_expected_code_for_category,
)
from config.settings import settings

class SheetLoader:
    def __init__(self):
        self.url = settings.sheet_export_url
        self.data = None
        self.last_error = None

    @property
    def has_reference_data(self) -> bool:
        """True, wenn gültige Referenzdaten aus dem Sheet vorliegen."""
        return self.data is not None and not self.data.empty

    def load(self):
        """Lädt die CSV-Daten aus dem Sheet."""
        self.last_error = None
        try:
            # Sheet laden
            self.data = pd.read_csv(self.url)
            # Spaltennamen säubern
            self.data.columns = [str(c).strip() for c in self.data.columns]
            # Werte säubern
            self.data = self.data.map(lambda x: str(x).strip() if pd.notna(x) else x)
            return self.data
        except Exception as e:
            self.last_error = str(e)
            # Fallback: leeres DataFrame, damit der QC-Lauf trotzdem starten kann.
            self.data = pd.DataFrame(columns=["Link", "Aktions-Code"])
            return self.data

    def get_expected_for_category(self, category_key: str):
        """Sucht den passenden Link und Code für eine Kategorie."""
        # Reise-Hits und Reisewelt werden dynamisch berechnet (wöchentlich/monatlich)
        computed_url = get_category_target_url(category_key)
        if category_key in ("reise_hits", "reisewelt") and computed_url:
            return {
                "url": computed_url.rstrip("/").lower(),
                "code": get_expected_code_for_category(category_key),
            }

        if self.data is None:
            return None

        # Mapping: Unser interner Key -> Name im Sheet (erste Spalte)
        sheet_mapping = {
            "busreisen": "Busreise",
            "flugreisen": "Flugreise",
            "reise_hits": "Reise-Hits",
            "reisewelt": "Reisewelt",
            "reiseziele": "Reiseziele und Länder",
            "homepage": "trendtours",  # Allgemeiner Code
        }

        target = sheet_mapping.get(category_key, "trendtours")

        # Filtere Zeilen, die den Zielnamen enthalten
        mask = self.data.iloc[:, 0].str.contains(target, case=False, na=False)
        rows = self.data[mask]

        url = None
        if not rows.empty:
            latest = rows.iloc[-1]
            url = str(latest.get("Link", "")).split("?")[0].lower().rstrip("/")

        if computed_url:
            url = computed_url.rstrip("/").lower()

        code = get_expected_code_for_category(category_key)
        if not url:
            return None
        return {"url": url, "code": code}

loader = SheetLoader()