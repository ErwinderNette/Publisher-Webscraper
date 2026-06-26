"""
Erzeugt E-Mail-Entwürfe (HTML, EML, Plain) pro Publisher mit Handlungsbedarf.
"""
import email.utils
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from config.contacts import PublisherContact, get_contact_by_url
from config.settings import settings

CODE_RE = re.compile(r"(?:AFF|KUP)\d{4}", re.IGNORECASE)

_GENERIC_MAILBOX = frozenset(
    {
        "info",
        "mail",
        "kontakt",
        "contact",
        "office",
        "team",
        "hello",
        "support",
        "admin",
        "service",
        "post",
        "sales",
        "help",
        "noreply",
        "no-reply",
        "buero",
    }
)

_MONTHS_DE = (
    "",
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


@dataclass
class EmailIssue:
    label: str
    instruction: str


@dataclass
class PublisherEmailDraft:
    publisher: str
    contact: PublisherContact
    subject: str
    plain_body: str
    html_body: str
    issues: list[EmailIssue]
    partner_url: str


@dataclass
class InternalReportDraft:
    subject: str
    plain_body: str
    html_body: str
    recipient: str


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _monat_label(dt: Optional[datetime] = None) -> tuple[str, int]:
    dt = dt or datetime.now()
    return _MONTHS_DE[dt.month], dt.year


def _parse_pruefdatum(value: str) -> Optional[datetime]:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def _capitalize_name(part: str) -> str:
    return part[:1].upper() + part[1:].lower() if part else ""


def _first_name_from_publisher_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name == "-":
        return ""
    first = name.split()[0]
    if len(first) < 2 or not first.replace("-", "").isalpha():
        return ""
    return _capitalize_name(first)


def _first_name_from_email(email: str) -> str:
    local = (email or "").split("@")[0].lower()
    if not local or local in _GENERIC_MAILBOX:
        return ""
    if "." in local:
        first = local.split(".")[0]
        if first.isalpha() and len(first) >= 2:
            return _capitalize_name(first)
    return ""


def greeting_from_contact(contact: PublisherContact) -> str:
    """Vorname aus publisher_name, sonst aus E-Mail, sonst „zusammen“."""
    first = _first_name_from_publisher_name(contact.publisher_name)
    if not first:
        first = _first_name_from_email(contact.publisher_email)
    return first if first else "zusammen"


def _build_instruction(row: dict, expected_code: str = "") -> str:
    action = str(row.get("Empfohlene_Maßnahme", "") or "").strip()
    if action and action != "-":
        base = action
    else:
        base = str(row.get("Fehlergrund", "") or "Bitte die genannten Punkte korrigieren")

    pruefpunkt = str(row.get("Prüfpunkt", ""))
    soll = str(row.get("Soll", ""))
    ist = str(row.get("Ist", ""))

    codes_soll = CODE_RE.findall(soll.upper())
    codes_expected = CODE_RE.findall((expected_code or "").upper())
    target_code = codes_soll[0] if codes_soll else (codes_expected[0] if codes_expected else "")

    if pruefpunkt == "Gutscheincode" and target_code:
        dt = _parse_pruefdatum(str(row.get("Prüfdatum", "")))
        monat = _MONTHS_DE[dt.month] if dt else _MONTHS_DE[datetime.now().month]
        outdated = CODE_RE.findall(ist.upper())
        if outdated:
            return f"Bitte den korrekten {target_code} für {monat} hinterlegen (aktuell z. B. {outdated[0]})"
        return f"Bitte den korrekten {target_code} für {monat} hinterlegen"

    if "Code" in base or pruefpunkt == "Affiliate-Link":
        if target_code and "hinterlegen" not in base.lower():
            return f"{base} (Zielcode: {target_code})"
    return base


def _issue_label(row: dict) -> str:
    label = str(row.get("Angebot", "") or "").strip()
    if label and label != "-":
        return label
    pruefpunkt = str(row.get("Prüfpunkt", "") or "").strip()
    return pruefpunkt or "Unbekanntes Angebot"


def _build_issues(issue_rows: pd.DataFrame, expected_code: str) -> list[EmailIssue]:
    issues: list[EmailIssue] = []
    for _, row in issue_rows.iterrows():
        row_dict = row.to_dict()
        issues.append(
            EmailIssue(
                label=_issue_label(row_dict),
                instruction=_build_instruction(row_dict, expected_code),
            )
        )
    return issues


def build_publisher_drafts(
    results: list[dict],
    expected_code: str = "",
) -> list[PublisherEmailDraft]:
    df = pd.DataFrame(results)
    if df.empty:
        return []

    action_df = df[df["Status"] == "Handlungsbedarf"]
    if action_df.empty:
        return []

    drafts: list[PublisherEmailDraft] = []
    for publisher, group in action_df.groupby("Publisher", sort=False):
        partner_url = str(group["Partner_URL"].iloc[0])
        contact = get_contact_by_url(partner_url)
        if not contact:
            print(
                f"⚠️ Kein Kontakt für Publisher '{publisher}' ({partner_url}) – keine E-Mail erzeugt."
            )
            continue

        issues = _build_issues(group, expected_code)
        if not issues:
            continue

        pruefdatum = _parse_pruefdatum(str(group["Prüfdatum"].iloc[0]))
        monat_label, jahr = _monat_label(pruefdatum)

        ctx = {
            "greeting": greeting_from_contact(contact),
            "page_url": contact.page_url,
            "monat_label": monat_label,
            "jahr": jahr,
            "issues": issues,
        }
        plain = _jinja.get_template("publisher_email.txt.j2").render(**ctx)
        html = _jinja.get_template("publisher_email.html.j2").render(**ctx)
        subject = f"trendtours - Unstimmigkeiten - {monat_label} {jahr}"

        drafts.append(
            PublisherEmailDraft(
                publisher=publisher,
                contact=contact,
                subject=subject,
                plain_body=plain,
                html_body=html,
                issues=issues,
                partner_url=partner_url,
            )
        )
    return drafts


def _attach_csv(msg: MIMEMultipart, csv_path: str) -> None:
    if not csv_path or not os.path.isfile(csv_path):
        return
    with open(csv_path, "rb") as f:
        part = MIMEBase("text", "csv")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        "attachment",
        filename=os.path.basename(csv_path),
    )
    msg.attach(part)


def build_internal_report_draft(
    publisher_draft_count: int,
    pruefdatum: Optional[datetime] = None,
) -> InternalReportDraft:
    monat_label, jahr = _monat_label(pruefdatum)
    ctx = {
        "notify_name": settings.qc_notify_name,
        "monat_label": monat_label,
        "jahr": jahr,
        "publisher_draft_count": publisher_draft_count,
    }
    plain = _jinja.get_template("internal_report_email.txt.j2").render(**ctx)
    html = _jinja.get_template("internal_report_email.html.j2").render(**ctx)
    subject = f"trendtours QC – Report {monat_label} {jahr}"
    return InternalReportDraft(
        subject=subject,
        plain_body=plain,
        html_body=html,
        recipient=settings.qc_report_recipient,
    )


def _build_publisher_mime_message(
    draft: PublisherEmailDraft,
    from_addr: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = draft.subject
    msg["From"] = from_addr
    msg["To"] = draft.contact.publisher_email
    msg["Date"] = email.utils.formatdate(localtime=True)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(draft.plain_body, "plain", "utf-8"))
    alt.attach(MIMEText(draft.html_body, "html", "utf-8"))
    msg.attach(alt)
    return msg


def _build_internal_mime_message(
    draft: InternalReportDraft,
    csv_path: str,
    from_addr: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = draft.subject
    msg["From"] = from_addr
    msg["To"] = draft.recipient
    msg["Date"] = email.utils.formatdate(localtime=True)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(draft.plain_body, "plain", "utf-8"))
    alt.attach(MIMEText(draft.html_body, "html", "utf-8"))
    msg.attach(alt)
    _attach_csv(msg, csv_path)
    return msg


def _email_out_dir(timestamp: str) -> str:
    out_dir = os.path.join(settings.report_dir, "emails", timestamp)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def write_email_files(
    drafts: list[PublisherEmailDraft],
    internal_draft: InternalReportDraft,
    csv_path: str,
    timestamp: str,
    from_addr: str,
) -> str:
    """Schreibt HTML, EML und README nach reports/emails/{timestamp}/."""
    out_dir = _email_out_dir(timestamp)

    if csv_path and os.path.isfile(csv_path):
        shutil.copy2(csv_path, os.path.join(out_dir, os.path.basename(csv_path)))

    for draft in drafts:
        slug = _slug(draft.publisher)
        html_path = os.path.join(out_dir, f"{slug}.html")
        eml_path = os.path.join(out_dir, f"{slug}.eml")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(draft.html_body)

        msg = _build_publisher_mime_message(draft, from_addr)
        with open(eml_path, "wb") as f:
            f.write(msg.as_bytes())

    with open(os.path.join(out_dir, "internal_report.html"), "w", encoding="utf-8") as f:
        f.write(internal_draft.html_body)

    internal_msg = _build_internal_mime_message(internal_draft, csv_path, from_addr)
    with open(os.path.join(out_dir, "internal_report.eml"), "wb") as f:
        f.write(internal_msg.as_bytes())

    readme = """Trendtours QC – E-Mail-Entwürfe
================================

Publisher (Handlungsbedarf):
- {slug}.html / {slug}.eml – Entwurf pro Publisher (ohne Report-Anhang)

Interner Report:
- internal_report.html / internal_report.eml – Vorschau der Benachrichtigung an {recipient}
- QC_Report_*.csv – Kopie des Reports im Ordner

Gmail (falls konfiguriert):
- Publisher-Entwürfe an die jeweiligen Kontakte
- QC-Benachrichtigung mit CSV wird direkt an {recipient} gesendet

Publisher-Ansprache: Hey [Vorname] oder Hey zusammen (aus publisher_name in contacts.yaml)
""".replace("{slug}", "publisher_slug").replace(
        "{recipient}", internal_draft.recipient
    )
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(readme)

    return out_dir


def generate_emails(
    results: list[dict],
    csv_path: str,
    timestamp: str,
    expected_code: str = "",
) -> list[PublisherEmailDraft]:
    """Hauptfunktion: Drafts bauen, Dateien schreiben, optional Gmail."""
    drafts = build_publisher_drafts(results, expected_code)

    pruefdatum = None
    if results:
        pruefdatum = _parse_pruefdatum(str(results[0].get("Prüfdatum", "")))

    internal_draft = build_internal_report_draft(len(drafts), pruefdatum)
    from_addr = settings.gmail_sender
    out_dir = write_email_files(drafts, internal_draft, csv_path, timestamp, from_addr)

    if drafts:
        print(f"\n📧 Publisher-E-Mail-Entwürfe: {len(drafts)}")
        for d in drafts:
            print(f"    • {d.publisher} → {d.contact.publisher_email} ({d.subject})")
    else:
        print("\n📧 Keine Publisher-E-Mail-Entwürfe (kein Handlungsbedarf oder fehlende Kontakte).")

    print(f" -> Ordner: {out_dir}")
    print(
        f" 📋 Interne QC-Benachrichtigung: {internal_draft.recipient} "
        f"({internal_draft.subject})"
    )

    if settings.gmail_enabled:
        try:
            from reporting.gmail_drafts import (
                create_gmail_drafts,
                send_internal_report_email,
            )

            if drafts:
                create_gmail_drafts(drafts, from_addr)
            send_internal_report_email(internal_draft, csv_path, from_addr)
        except Exception as e:
            print(f"⚠️ Gmail-Versand fehlgeschlagen: {e}")
            if "Gmail API" in str(e) or "accessNotConfigured" in str(e):
                print(
                    "   → Google Cloud: Gmail API im Projekt des Desktop-Clients aktivieren "
                    "(nicht nur ein anderes/n8n-Projekt)."
                )
            print("   Fallback: .eml/.html im Ordner oben nutzen.")

    return drafts
