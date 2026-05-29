"""
Generiert CSV und Excel-Reports mit Übersicht, Vollständig und Handlungsbedarf.
"""
import os
from datetime import datetime

import pandas as pd

from config.settings import settings
from validators.coupon_validator import get_expected_month_code

DETAIL_COLUMNS = [
    "Prüfdatum",
    "Publisher",
    "Gruppe",
    "Partner_URL",
    "Prüfpunkt",
    "Angebot",
    "Ist",
    "Soll",
    "Status",
    "Fehlergrund",
    "Empfohlene_Maßnahme",
    "Screenshot",
    "Übersicht_Screenshot",
]

OVERVIEW_COLUMNS = [
    "Prüfdatum",
    "Publisher",
    "Gruppe",
    "Partner_URL",
    "Gesamtstatus",
    "Anzahl_Prüfungen",
    "Anzahl_Handlungsbedarf",
    "Betroffene_Gutscheine",
    "Kurzfassung",
    "Übersicht_Screenshot",
]

# Angebots-Labels ohne konkreten Gutscheintitel (seitenweite Prüfungen)
_GENERIC_ANGEBOT = frozenset(
    {
        "-",
        "coupon-bereich",
        "gesamtseite",
        "coupon-listings",
        "partnerseite",
    }
)


def _is_concrete_offer(angebot: str) -> bool:
    label = (angebot or "").strip()
    if not label or label == "-":
        return False
    return label.lower() not in _GENERIC_ANGEBOT


def _issue_label(row) -> str:
    angebot = str(row.get("Angebot", "") or "").strip()
    if _is_concrete_offer(angebot):
        return f"«{angebot}»"
    return str(row.get("Prüfpunkt", "") or "Prüfung")


def _betroffene_gutscheine(issues: pd.DataFrame) -> str:
    titles: list[str] = []
    seen: set[str] = set()
    for _, r in issues.iterrows():
        angebot = str(r.get("Angebot", "") or "").strip()
        if not _is_concrete_offer(angebot):
            continue
        key = angebot.lower()
        if key in seen:
            continue
        seen.add(key)
        titles.append(angebot)
    return " | ".join(titles) if titles else "-"


def _build_overview(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for publisher, group in df.groupby(["Publisher", "Gruppe"], sort=False):
        pub_name, pub_group = publisher
        sub = group
        issues = sub[sub["Status"] == "Handlungsbedarf"]
        kurz_parts = [
            f"{_issue_label(r)}: {r['Fehlergrund']}"
            for _, r in issues.iterrows()
            if r["Fehlergrund"] and r["Fehlergrund"] != "-"
        ]
        kurz = "; ".join(kurz_parts) if kurz_parts else "Keine Auffälligkeiten"
        rows.append(
            {
                "Prüfdatum": sub["Prüfdatum"].iloc[0],
                "Publisher": pub_name,
                "Gruppe": pub_group,
                "Partner_URL": sub["Partner_URL"].iloc[0],
                "Gesamtstatus": "Handlungsbedarf" if len(issues) else "OK",
                "Anzahl_Prüfungen": len(sub),
                "Anzahl_Handlungsbedarf": len(issues),
                "Betroffene_Gutscheine": _betroffene_gutscheine(issues),
                "Kurzfassung": kurz[:500],
                "Übersicht_Screenshot": sub["Übersicht_Screenshot"].iloc[0],
            }
        )
    return pd.DataFrame(rows, columns=OVERVIEW_COLUMNS)


def generate_report(results: list[dict]):
    if not results:
        print("⚠️ Keine Ergebnisse zum Reporten vorhanden.")
        return

    os.makedirs(settings.report_dir, exist_ok=True)

    df = pd.DataFrame(results)
    for col in DETAIL_COLUMNS:
        if col not in df.columns:
            df[col] = "-"

    df = df[DETAIL_COLUMNS]
    overview_df = _build_overview(df)
    action_df = df[df["Status"] == "Handlungsbedarf"].copy()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(settings.report_dir, f"QC_Report_{timestamp}.csv")
    xlsx_path = os.path.join(settings.report_dir, f"QC_Report_{timestamp}.xlsx")

    df.to_csv(csv_path, index=False, sep=";", encoding="utf-8-sig")

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        overview_df.to_excel(writer, sheet_name="Übersicht", index=False)
        df.to_excel(writer, sheet_name="Vollständig", index=False)
        action_df.to_excel(writer, sheet_name="Handlungsbedarf", index=False)

    print(f"\n📊 Reports erfolgreich generiert!")
    print(f" -> CSV:   {csv_path}")
    print(f" -> Excel: {xlsx_path}")
    print(f"    Blätter: Übersicht ({len(overview_df)}), Vollständig ({len(df)}), Handlungsbedarf ({len(action_df)})")

    from reporting.email_generator import generate_emails

    generate_emails(
        results,
        csv_path=csv_path,
        timestamp=timestamp,
        expected_code=get_expected_month_code(),
    )
