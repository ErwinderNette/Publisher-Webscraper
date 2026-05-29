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
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from config.contacts import PublisherContact, get_contact_by_url
from config.settings import settings

CODE_RE = re.compile(r"(?:AFF|KUP)\d{4}", re.IGNORECASE)

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
    instruction: str
    screenshot_rel: str
    screenshot_abs: str
    cid: str
    image_filename: str


@dataclass
class PublisherEmailDraft:
    publisher: str
    contact: PublisherContact
    subject: str
    plain_body: str
    html_body: str
    issues: list[EmailIssue]
    partner_url: str


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


def _screenshot_abs(relative: str) -> str:
    if not relative or relative == "-":
        return ""
    return os.path.join(settings.report_dir, relative)


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


def _pick_screenshot(row: dict) -> str:
    ss = str(row.get("Screenshot", "") or "").strip()
    if ss and ss != "-":
        return ss
    overview = str(row.get("Übersicht_Screenshot", "") or "").strip()
    return overview if overview != "-" else "-"


def _build_issues(
    issue_rows: pd.DataFrame,
    expected_code: str,
    publisher_slug: str,
) -> list[EmailIssue]:
    issues: list[EmailIssue] = []
    for idx, (_, row) in enumerate(issue_rows.iterrows()):
        rel = _pick_screenshot(row.to_dict())
        abs_path = _screenshot_abs(rel)
        if not abs_path or not os.path.isfile(abs_path):
            continue
        cid = f"issue{idx}@{publisher_slug}"
        issues.append(
            EmailIssue(
                instruction=_build_instruction(row.to_dict(), expected_code),
                screenshot_rel=rel,
                screenshot_abs=abs_path,
                cid=cid,
                image_filename=os.path.basename(abs_path),
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

        slug = _slug(publisher)
        issues = _build_issues(group, expected_code, slug)
        if not issues:
            print(f"⚠️ Keine Screenshots für '{publisher}' – E-Mail übersprungen.")
            continue

        pruefdatum = _parse_pruefdatum(str(group["Prüfdatum"].iloc[0]))
        monat_label, jahr = _monat_label(pruefdatum)

        ctx = {
            "publisher_email": contact.publisher_email,
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


def _build_mime_message(
    draft: PublisherEmailDraft,
    csv_path: str,
    from_addr: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = draft.subject
    msg["From"] = from_addr
    msg["To"] = draft.contact.publisher_email
    msg["Date"] = email.utils.formatdate(localtime=True)

    related = MIMEMultipart("related")
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(draft.plain_body, "plain", "utf-8"))
    alt.attach(MIMEText(draft.html_body, "html", "utf-8"))
    related.attach(alt)

    for issue in draft.issues:
        with open(issue.screenshot_abs, "rb") as f:
            img = MIMEImage(f.read(), _subtype="png")
        img.add_header("Content-ID", f"<{issue.cid}>")
        img.add_header("Content-Disposition", "inline", filename=issue.image_filename)
        related.attach(img)

    msg.attach(related)

    if csv_path and os.path.isfile(csv_path):
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

    return msg


def write_email_files(
    drafts: list[PublisherEmailDraft],
    csv_path: str,
    timestamp: str,
    from_addr: str,
) -> str:
    """Schreibt HTML, EML und README nach reports/emails/{timestamp}/."""
    out_dir = os.path.join(settings.report_dir, "emails", timestamp)
    os.makedirs(out_dir, exist_ok=True)

    if csv_path and os.path.isfile(csv_path):
        shutil.copy2(csv_path, os.path.join(out_dir, os.path.basename(csv_path)))

    for draft in drafts:
        slug = _slug(draft.publisher)
        html_path = os.path.join(out_dir, f"{slug}.html")
        eml_path = os.path.join(out_dir, f"{slug}.eml")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(draft.html_body)

        msg = _build_mime_message(draft, csv_path, from_addr)
        with open(eml_path, "wb") as f:
            f.write(msg.as_bytes())

    readme = """Trendtours QC – E-Mail-Entwürfe
================================

Pro Publisher mit Handlungsbedarf liegt hier:
- {slug}.html  – Vorschau / Copy-Paste
- {slug}.eml   – Import in Mail-Client (Outlook, Apple Mail)
- QC_Report_*.csv – vollständiger QC-Report als Anhang (in .eml enthalten)

Gmail-Entwürfe (falls konfiguriert) liegen zusätzlich im Postfach es@uppr.de.

An: jeweilige publisher_email aus contacts.yaml
Ansprache im Text: Hey [E-Mailadresse]

Vor dem Versand bitte Text und Screenshots kurz prüfen.
""".replace(
        "{slug}", "publisher_slug"
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
    if not drafts:
        print("\n📧 Keine E-Mail-Entwürfe (kein Handlungsbedarf oder fehlende Kontakte).")
        return []

    from_addr = settings.gmail_sender
    out_dir = write_email_files(drafts, csv_path, timestamp, from_addr)

    print(f"\n📧 E-Mail-Entwürfe: {len(drafts)} Publisher")
    print(f" -> Ordner: {out_dir}")
    for d in drafts:
        print(f"    • {d.publisher} → {d.contact.publisher_email} ({d.subject})")

    if settings.gmail_enabled:
        try:
            from reporting.gmail_drafts import create_gmail_drafts

            create_gmail_drafts(drafts, csv_path, from_addr)
        except Exception as e:
            print(f"⚠️ Gmail-Entwürfe fehlgeschlagen: {e}")
            if "Gmail API" in str(e) or "accessNotConfigured" in str(e):
                print(
                    "   → Google Cloud: Gmail API im Projekt des Desktop-Clients aktivieren "
                    "(nicht nur ein anderes/n8n-Projekt)."
                )
            print("   Fallback: .eml/.html im Ordner oben nutzen.")

    return drafts
