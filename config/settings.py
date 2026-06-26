"""
Zentrale Konfiguration für den Trendtours QC Scraper.
"""
from datetime import datetime
from pydantic_settings import BaseSettings
from pydantic import computed_field

class Settings(BaseSettings):
    """Anwendungseinstellungen."""

    # === Google Sheet Source of Truth ===
    # Monatliche Referenz: Ziel-URLs und Aktions-Codes pro Kategorie
    sheet_id: str = "1QLWxjyjSu1el9tEjiBaT3wxSPcbkhVXEqybzE8Tk33Y"

    @property
    def sheet_export_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/export?format=csv"

    # === Browser Settings ===
    headless: bool = True
    slow_mo: int = 100
    timeout: int = 30000

    user_agent: str = (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    )

    viewport_width: int = 412
    viewport_height: int = 915

    report_dir: str = "reports"
    screenshot_dir: str = "reports/screenshots"

    # === Gmail-Entwürfe (Google Workspace) ===
    gmail_enabled: bool = True
    gmail_sender: str = "es@uppr.de"
    gmail_drafts_mailbox: str = "js@uppr.de"
    gmail_credentials_dir: str = "config/gmail"
    qc_report_recipient: str = "js@uppr.de"
    qc_notify_name: str = "Janine"

    # === Monday.com Status-Update ===
    # Token optional per Umgebungsvariable MONDAY_API_TOKEN überschreiben
    monday_api_token: str = (
        "eyJhbGciOiJIUzI1NiJ9.eyJ0aWQiOjY3MzY1Nzg5MSwiYWFpIjoxMSwidWlkIjo3Nzg1Mzc5OCwiaWFkIjoiMjAyNi0wNi0yMlQwOTozNzowNC4wNjJaIiwicGVyIjoibWU6d3JpdGUiLCJhY3RpZCI6MjgyODA5NjQsInJnbiI6ImV1YzEifQ.M2TtpipPGK776CFN6qfVR_QL5zWcIXR36zoJPFaOKEU"
    )
    monday_board_id: str = "5098797869"
    monday_item_id: str = "3007938816"
    monday_status_column: str = "color_mm4fzfbf"
    monday_date_column: str = "date_mm4ffbac"
    monday_timezone: str = "Europe/Berlin"

    @computed_field
    @property
    def current_month_code(self) -> str:
        now = datetime.now()
        return f"AFF{now.month:02d}{now.year % 100:02d}"

    @computed_field
    @property
    def expected_codes(self) -> list[str]:
        return [self.current_month_code]

settings = Settings()
