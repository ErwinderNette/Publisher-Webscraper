"""
Gmail API: Entwürfe in js@uppr.de, Versand von es@uppr.de (OAuth Desktop Flow).
"""
import base64
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, build_from_document
from googleapiclient.errors import HttpError, UnknownApiNameOrVersion

from reporting.email_generator import (
    InternalReportDraft,
    PublisherEmailDraft,
    _build_internal_mime_message,
    _build_publisher_mime_message,
)

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def _validate_credentials_file(creds_file: Path) -> None:
    import json

    with open(creds_file, encoding="utf-8") as f:
        data = json.load(f)
    if "installed" in data:
        return
    if "web" in data:
        raise ValueError(
            "credentials.json ist ein Web-Client (z. B. n8n), nicht Desktop. "
            "In der Google Cloud Console einen neuen OAuth-Client vom Typ "
            "'Desktop app' anlegen, JSON herunterladen und als "
            "config/gmail/credentials.json speichern. "
            "Sonst: Fehler redirect_uri_mismatch."
        )
    raise ValueError(
        "credentials.json hat weder 'installed' noch 'web'. "
        "Bitte OAuth-Client 'Desktop app' neu anlegen."
    )


def _credentials_path() -> Path:
    from config.settings import settings

    return Path(settings.gmail_credentials_dir) / "credentials.json"


def _token_path_for(account: str) -> Path:
    from config.settings import settings

    local = account.split("@", 1)[0]
    return Path(settings.gmail_credentials_dir) / f"token_{local}.json"


def _resolve_token_path(account: str) -> Path:
    """Pro Konto token_{local}.json; Legacy token.json nur für gmail_sender."""
    from config.settings import settings

    token_file = _token_path_for(account)
    if token_file.is_file():
        return token_file
    if account == settings.gmail_sender:
        legacy = Path(settings.gmail_credentials_dir) / "token.json"
        if legacy.is_file():
            return legacy
    return token_file


def _find_gmail_discovery_json() -> Optional[Path]:
    """Sucht gmail.v1.json (kaputte venv-Installation: Ordner 'discovery_cache 2')."""
    import googleapiclient

    base = Path(googleapiclient.__file__).parent
    for sub in ("discovery_cache/documents", "discovery_cache 2/documents"):
        candidate = base / sub / "gmail.v1.json"
        if candidate.is_file():
            return candidate
    bundled = Path(__file__).parent.parent / "config" / "gmail" / "gmail.v1.json"
    if bundled.is_file():
        return bundled
    return None


def _build_gmail_service(creds):
    try:
        return build(
            "gmail",
            "v1",
            credentials=creds,
            cache_discovery=False,
            static_discovery=True,
        )
    except UnknownApiNameOrVersion:
        pass

    doc_path = _find_gmail_discovery_json()
    if doc_path:
        content = doc_path.read_text(encoding="utf-8")
        return build_from_document(content, credentials=creds)

    return build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=True,
        static_discovery=False,
    )


def get_gmail_service(for_account: str):
    creds = None
    token_file = _resolve_token_path(for_account)
    creds_file = _credentials_path()

    if token_file.is_file():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_file.is_file():
                raise FileNotFoundError(
                    f"Gmail OAuth: {creds_file} fehlt. Siehe README (Gmail-Setup)."
                )
            _validate_credentials_file(creds_file)
            print(f"\n🔐 Gmail OAuth: Bitte mit **{for_account}** anmelden …")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)
        canonical = _token_path_for(for_account)
        canonical.parent.mkdir(parents=True, exist_ok=True)
        with open(canonical, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    try:
        return _build_gmail_service(creds)
    except HttpError as e:
        if e.resp.status == 403 and "accessNotConfigured" in str(e):
            raise RuntimeError(
                "Gmail API ist im Google-Cloud-Projekt deines Desktop-OAuth-Clients "
                "nicht aktiviert. In der Cloud Console das gleiche Projekt wählen wie "
                "bei credentials.json → APIs & Services → Bibliothek → Gmail API → "
                "Aktivieren, 2–5 Min. warten, dann QC erneut starten."
            ) from e
        raise


def _message_to_raw(msg) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def _create_gmail_draft(service, mime) -> str:
    body = {"message": {"raw": _message_to_raw(mime)}}
    created = service.users().drafts().create(userId="me", body=body).execute()
    return created.get("id", "")


def create_gmail_drafts(
    drafts: list[PublisherEmailDraft],
    from_addr: str,
) -> list[str]:
    """Legt pro Publisher einen Gmail-Entwurf im Entwürfe-Postfach an."""
    from config.settings import settings

    mailbox = settings.gmail_drafts_mailbox
    service = get_gmail_service(mailbox)
    draft_ids: list[str] = []

    for draft in drafts:
        mime = _build_publisher_mime_message(draft, from_addr)
        draft_id = _create_gmail_draft(service, mime)
        draft_ids.append(draft_id)
        print(
            f"   ✓ Gmail-Entwurf: {draft.publisher} → {draft.contact.publisher_email} "
            f"(Draft-ID: {draft_id})"
        )

    print(f"\n📬 {len(draft_ids)} Publisher-Gmail-Entwürfe in {mailbox} angelegt.")
    return draft_ids


def send_internal_report_email(
    draft: InternalReportDraft,
    csv_path: str,
    from_addr: str,
) -> str:
    """Sendet die interne QC-Benachrichtigung mit CSV-Anhang (kein Entwurf)."""
    from config.settings import settings

    service = get_gmail_service(settings.gmail_sender)
    mime = _build_internal_mime_message(draft, csv_path, from_addr)
    sent = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": _message_to_raw(mime)})
        .execute()
    )
    message_id = sent.get("id", "")
    print(
        f"   ✓ E-Mail gesendet: {from_addr} → {draft.recipient} "
        f"(Message-ID: {message_id})"
    )
    return message_id
