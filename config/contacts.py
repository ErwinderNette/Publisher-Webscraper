"""
Publisher-Kontakte für E-Mail-Entwürfe (Lookup per normalisierter page_url).
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml

_CONTACTS_PATH = Path(__file__).parent / "contacts.yaml"


@dataclass(frozen=True)
class PublisherContact:
    publisher_id: int
    publisher_group: str
    publisher_company: str
    page_url: str
    publisher_name: str
    publisher_email: str


def normalize_url(url: str) -> str:
    """Vergleichsfähige URL ohne trailing slash, Host lowercase."""
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u if "://" in u else f"https://{u}")
    host = (parsed.netloc or parsed.path.split("/")[0]).lower()
    path = parsed.path.rstrip("/") or ""
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return f"{host}{path}"


@lru_cache(maxsize=1)
def load_contacts() -> list[PublisherContact]:
    with open(_CONTACTS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rows = data.get("contacts") or []
    return [
        PublisherContact(
            publisher_id=int(row["publisher_id"]),
            publisher_group=str(row["publisher_group"]),
            publisher_company=str(row["publisher_company"]),
            page_url=str(row["page_url"]),
            publisher_name=str(row["publisher_name"]),
            publisher_email=str(row["publisher_email"]),
        )
        for row in rows
    ]


@lru_cache(maxsize=1)
def _url_index() -> dict[str, PublisherContact]:
    return {normalize_url(c.page_url): c for c in load_contacts()}


def get_contact_by_url(url: str) -> Optional[PublisherContact]:
    return _url_index().get(normalize_url(url))


def get_contact_by_company(company: str) -> Optional[PublisherContact]:
    key = (company or "").strip().lower()
    for c in load_contacts():
        if c.publisher_company.lower() == key:
            return c
    return None
