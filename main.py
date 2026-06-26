import argparse
import asyncio
import os
import re
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from rich.console import Console

from config.publishers import PUBLISHERS, PublisherEntry
from config.reference import is_trendtours_destination
from config.settings import settings
from config.sheet_loader import loader
from scraper.browser import get_stealth_browser
from scraper.page_scraper import PublisherScraper
from scraper.redirect_checker import RedirectChecker
from validators.coupon_validator import (
    get_expected_month_code,
    validate_page_codes,
    validate_url_code,
)
from validators.url_validator import validate_redirect
from validators.page_checks import validate_logo, validate_expired_deals
from reporting.report_generator import generate_report
from reporting.screenshot_annotator import capture_issue_screenshot

console = Console()
CODE_RE = re.compile(r"(?:AFF|KUP)\d{4}", re.IGNORECASE)

MONDAY_STATUS_OK = "✅ Erfolgreich"
MONDAY_STATUS_ERROR = "🚨 Fehler"
MONDAY_STATUS_LABELS = frozenset({MONDAY_STATUS_OK, MONDAY_STATUS_ERROR})


def update_monday_status(status_label: str) -> bool:
    """Aktualisiert Status und Zeitstempel im Monday-Board. Gibt True bei Erfolg zurück."""
    if status_label not in MONDAY_STATUS_LABELS:
        console.print(
            f"[red]❌ Ungültiger Monday-Status: {status_label!r} "
            f"(erlaubt: {MONDAY_STATUS_OK!r}, {MONDAY_STATUS_ERROR!r})[/red]"
        )
        return False

    if not settings.monday_api_token:
        console.print("[red]❌ Monday API-Token fehlt (settings.monday_api_token / MONDAY_API_TOKEN).[/red]")
        return False

    console.print(f"[bold cyan]Sende Status an monday.com: {status_label}[/bold cyan]")

    berlin_tz = ZoneInfo(settings.monday_timezone)
    berlin_now = datetime.now(berlin_tz)
    utc_now = berlin_now.astimezone(ZoneInfo("UTC"))
    date_str = berlin_now.strftime("%Y-%m-%d")
    time_str = utc_now.strftime("%H:%M:%S")

    console.print(
        f"[dim]   Datum (DE): {date_str} {berlin_now.strftime('%H:%M:%S')} "
        f"→ API-Zeit (UTC): {time_str}[/dim]"
    )

    column_values = json.dumps({
        settings.monday_status_column: {"label": status_label},
        settings.monday_date_column: {"date": date_str, "time": time_str},
    })

    graphql_query = """
    mutation ($boardId: ID!, $itemId: ID!, $columnValues: JSON!) {
        change_multiple_column_values(
            board_id: $boardId,
            item_id: $itemId,
            column_values: $columnValues
        ) { id }
    }
    """

    payload = {
        "query": graphql_query,
        "variables": {
            "boardId": settings.monday_board_id,
            "itemId": settings.monday_item_id,
            "columnValues": column_values,
        },
    }

    headers = {
        "Authorization": settings.monday_api_token,
        "Content-Type": "application/json",
        "API-Version": "2024-01",
    }

    try:
        response = requests.post(
            "https://api.monday.com/v2",
            json=payload,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()

        if body.get("errors"):
            for err in body["errors"]:
                console.print(f"[red]❌ Monday API-Fehler: {err.get('message', err)}[/red]")
            console.print(f"[dim]   Vollständige Antwort: {json.dumps(body, ensure_ascii=False)}[/dim]")
            return False

        item_id = (body.get("data") or {}).get("change_multiple_column_values", {}).get("id")
        if not item_id:
            console.print("[red]❌ Monday-Antwort ohne Item-ID – Update vermutlich fehlgeschlagen.[/red]")
            console.print(f"[dim]   Vollständige Antwort: {json.dumps(body, ensure_ascii=False)}[/dim]")
            return False

        console.print(
            f"[green]✓ Monday-Board aktualisiert (Item {item_id}, Board {settings.monday_board_id}).[/green]"
        )
        return True
    except requests.RequestException as e:
        console.print(f"[red]❌ Netzwerkfehler beim Monday-Update: {e}[/red]")
        if getattr(e, "response", None) is not None:
            console.print(f"[dim]   Antwort: {e.response.text[:500]}[/dim]")
        return False
    except Exception as e:
        console.print(f"[red]❌ Fehler beim Update von monday.com: {e}[/red]")
        return False


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _result_row(
    publisher: PublisherEntry,
    pruefdatum: str,
    pruefpunkt: str,
    angebot: str,
    ist: str,
    soll: str,
    is_ok: bool,
    fehlergrund,
    action,
    screenshot: str,
    overview_screenshot: str,
) -> dict:
    return {
        "Prüfdatum": pruefdatum,
        "Publisher": publisher.name,
        "Gruppe": publisher.group,
        "Partner_URL": publisher.url,
        "Prüfpunkt": pruefpunkt,
        "Angebot": angebot,
        "Ist": ist,
        "Soll": soll,
        "Status": "OK" if is_ok else "Handlungsbedarf",
        "Fehlergrund": fehlergrund or "-",
        "Empfohlene_Maßnahme": action or "-",
        "Screenshot": screenshot,
        "Übersicht_Screenshot": overview_screenshot,
    }


async def _take_screenshot(scraper: PublisherScraper, publisher: PublisherEntry, prefix: str) -> str:
    await scraper.dismiss_consent_for(publisher.scraper)
    screenshot_name = f"{prefix}_{datetime.now().strftime('%H%M%S_%f')}.png"
    screenshot_path = os.path.join(settings.screenshot_dir, screenshot_name)
    await scraper.page.screenshot(path=screenshot_path, full_page=True)
    return f"screenshots/{screenshot_name}"


async def _issue_screenshot(
    scraper: PublisherScraper,
    publisher: PublisherEntry,
    prefix: str,
    overview_ss: str,
    *,
    hint_texts=None,
    ist: str = "",
    soll: str = "",
    voucher_href: str = "",
) -> str:
    await scraper.dismiss_consent_for(publisher.scraper)
    return await capture_issue_screenshot(
        scraper.page,
        prefix,
        hint_texts=hint_texts,
        ist=ist,
        soll=soll,
        voucher_href=voucher_href,
        fallback_relative=overview_ss,
    )


async def run_qc_checks():
    console.print("[bold blue]🚀 Starte Trendtours QC...[/bold blue]\n")

    loader.load()
    if not loader.has_reference_data:
        console.print(
            "[yellow]⚠️ Google Sheet nicht erreichbar – Fallback-Codes werden genutzt.[/yellow]"
        )
        if loader.last_error:
            console.print(f"[yellow]   Ursache: {loader.last_error}[/yellow]")
    else:
        console.print("[green]✓ Google Sheet als Referenz geladen.[/green]")

    expected_code = get_expected_month_code()
    console.print(f"[cyan]Erwarteter Monatscode: {expected_code}[/cyan]\n")

    os.makedirs(settings.screenshot_dir, exist_ok=True)
    all_results: list[dict] = []
    pruefdatum = datetime.now().strftime("%d.%m.%Y")

    async with get_stealth_browser() as (browser, context, page):
        scraper = PublisherScraper(page)
        checker = RedirectChecker(context)

        for publisher in PUBLISHERS:
            console.print(f"🔍 [bold]{publisher.name}[/bold] ({publisher.group})")
            overview_ss = "-"

            try:
                scraped = await scraper.scrape(publisher)
            except Exception as e:
                console.print(f"   [red]Seite nicht ladbar: {e}[/red]")
                all_results.append(
                    _result_row(
                        publisher,
                        pruefdatum,
                        "Seitenzugriff",
                        "-",
                        "Fehler",
                        publisher.url,
                        False,
                        str(e),
                        "Publisher-Seite manuell prüfen",
                        "-",
                        "-",
                    )
                )
                console.print("-" * 50)
                continue

            slug = _slug(publisher.name)
            overview_ss = await _take_screenshot(scraper, publisher, f"overview_{slug}")
            console.print(f"   📷 Übersichts-Screenshot: {overview_ss}")

            # --- Gutscheincode (Seite) ---
            if "codes" in publisher.checks:
                code_val = validate_page_codes(scraped.found_codes)
                ss = "-"
                if not code_val["is_valid"]:
                    outdated = ", ".join(code_val.get("outdated") or [])
                    ss = await _issue_screenshot(
                        scraper,
                        publisher,
                        f"code_{slug}",
                        overview_ss,
                        hint_texts=[outdated] if outdated else None,
                        ist=", ".join(code_val["found"]) or "keine",
                        soll=code_val["expected"],
                    )
                all_results.append(
                    _result_row(
                        publisher,
                        pruefdatum,
                        "Gutscheincode",
                        "Coupon-Bereich"
                        if publisher.scraper
                        in (
                            "coupons_de",
                            "focus_gsg",
                            "welt_der_rabatte",
                            "igraal",
                            "gutscheinrausch",
                        )
                        else "Gesamtseite",
                        ", ".join(code_val["found"]) or "keine",
                        code_val["expected"],
                        code_val["is_valid"],
                        code_val.get("error"),
                        code_val.get("action"),
                        ss,
                        overview_ss,
                    )
                )
                if not code_val["is_valid"]:
                    console.print(f"   [red]Code: {code_val['error']}[/red]")

            # --- Logo ---
            if "logo" in publisher.checks and scraped.soup:
                logo_val = validate_logo(scraped.soup)
                ss = "-"
                if not logo_val["is_valid"]:
                    ss = await _issue_screenshot(
                        scraper,
                        publisher,
                        f"logo_{slug}",
                        overview_ss,
                        hint_texts=["Trendtours"],
                    )
                all_results.append(
                    _result_row(
                        publisher,
                        pruefdatum,
                        "Logo",
                        "Partnerseite",
                        "Kein Logo gefunden" if not logo_val["is_valid"] else "Trendtours-Logo vorhanden",
                        "Trendtours-Logo sichtbar",
                        logo_val["is_valid"],
                        logo_val.get("error"),
                        logo_val.get("action"),
                        ss,
                        overview_ss,
                    )
                )

            # --- Abgelaufene Aktionen ---
            if "expired" in publisher.checks and scraped.soup:
                exp_val = validate_expired_deals(scraped.soup, publisher.scraper)
                ss = "-"
                if not exp_val["is_valid"]:
                    ss = await _issue_screenshot(
                        scraper,
                        publisher,
                        f"expired_{slug}",
                        overview_ss,
                        hint_texts=["Gutschein", "Coupon"],
                    )
                ist = exp_val.get("error") or "OK"
                if exp_val.get("details"):
                    ist += " – " + "; ".join(exp_val["details"][:3])
                all_results.append(
                    _result_row(
                        publisher,
                        pruefdatum,
                        "Abgelaufene Aktionen",
                        "Coupon-Listings",
                        ist,
                        "Abgelaufene Deals klar gekennzeichnet",
                        exp_val["is_valid"],
                        exp_val.get("error"),
                        exp_val.get("action"),
                        ss,
                        overview_ss,
                    )
                )

            # --- Redirects / Links ---
            if "redirects" in publisher.checks:
                buttons = scraped.buttons
                console.print(f"   [cyan]🔗 {len(buttons)} Deal-Links gefunden[/cyan]")

                if not buttons:
                    all_results.append(
                        _result_row(
                            publisher,
                            pruefdatum,
                            "Affiliate-Links",
                            "-",
                            "Keine klickbaren Deals gefunden",
                            "Mindestens ein Trendtours-Angebot mit Link",
                            False,
                            "Keine Deal-Buttons/Links extrahiert",
                            "Seite prüfen oder Scraper-Profil anpassen",
                            overview_ss,
                            overview_ss,
                        )
                    )
                else:
                    for btn in buttons:
                        if btn.resolved_url:
                            final_url = btn.resolved_url
                        elif btn.href.startswith("#") or btn.href == "#":
                            final_url = "ERROR"
                        else:
                            final_url = await checker.get_final_url(publisher.url, btn.href)

                        redirect_ok = is_trendtours_destination(final_url)
                        redirect_val = validate_redirect(btn.text, btn.context_text, final_url)
                        expected_btn_code = redirect_val.get("expected_code") or expected_code
                        if expected_btn_code in ("-", "nan", ""):
                            expected_btn_code = expected_code

                        url_code_val = validate_url_code(final_url, str(expected_btn_code))
                        path_ok = redirect_val["is_valid"]
                        code_ok = url_code_val["is_valid"]
                        is_ok = redirect_ok and path_ok and code_ok

                        if not redirect_ok and final_url not in ("ERROR", "#"):
                            errors_pre = [f"Redirect landet nicht auf trendtours.de: {final_url[:80]}"]
                        else:
                            errors_pre = []
                        if not redirect_ok and final_url in ("ERROR", "#"):
                            errors_pre = ["Redirect konnte nicht aufgelöst werden (Klick/Popup)"]

                        errors = list(errors_pre)
                        actions = []
                        if not redirect_ok:
                            actions.append("Affiliate-Button prüfen – Link muss auf trendtours.de führen")
                        if redirect_val.get("error"):
                            errors.append(redirect_val["error"])
                        if redirect_val.get("action"):
                            actions.append(redirect_val["action"])
                        if url_code_val.get("error"):
                            errors.append(url_code_val["error"])
                        if url_code_val.get("action"):
                            actions.append(url_code_val["action"])

                        codes_in_url = CODE_RE.findall(final_url.upper())
                        redirect_label = "OK" if redirect_ok else "FEHLER"
                        ist_parts = [f"Redirect {redirect_label}: {final_url[:100]}"]
                        if publisher.scraper in ("focus_gsg", "igraal") and btn.href.startswith(
                            "http"
                        ):
                            ist_parts.insert(0, f"Voucher: {btn.href}")
                        ist_parts.append(f"Code in URL: {', '.join(codes_in_url) or '–'}")

                        ss = "-"
                        if not is_ok:
                            ss = await _issue_screenshot(
                                scraper,
                                publisher,
                                f"link_{slug}",
                                overview_ss,
                                hint_texts=[btn.context_text],
                                voucher_href=btn.href,
                                ist=" | ".join(ist_parts),
                                soll=str(expected_btn_code),
                            )

                        all_results.append(
                            _result_row(
                                publisher,
                                pruefdatum,
                                "Affiliate-Link",
                                btn.context_text[:100],
                                " | ".join(ist_parts),
                                f"Ziel: {redirect_val.get('expected_url', '-')} | Code: {expected_btn_code}",
                                is_ok,
                                " | ".join(errors) if errors else None,
                                " | ".join(dict.fromkeys(actions)) if actions else None,
                                ss,
                                overview_ss,
                            )
                        )
                        mark = "✅" if is_ok else "❌"
                        if publisher.scraper == "focus_gsg":
                            vid = btn.href.split("#voucher-")[-1][:8] if "#voucher-" in btn.href else "?"
                            console.print(
                                f"      {mark} #{vid}… → Redirect {redirect_label}: {(final_url or '–')[:50]}"
                            )
                        else:
                            console.print(
                                f"      {mark} Redirect {redirect_label}: {btn.context_text[:38]}... → {(final_url or '–')[:55]}"
                            )

            console.print("-" * 50)

    if all_results:
        generate_report(all_results)
    console.print("\n[bold blue]🎉 QC beendet![/bold blue]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trendtours QC Scraper")
    parser.add_argument(
        "--monday-only",
        action="store_true",
        help="Nur Monday-Board-Update testen (ohne QC-Lauf)",
    )
    parser.add_argument(
        "--monday-status",
        choices=["erfolg", "fehler"],
        default="erfolg",
        help="Status für --monday-only (Standard: erfolg)",
    )
    args = parser.parse_args()

    monday_status_map = {
        "erfolg": MONDAY_STATUS_OK,
        "fehler": MONDAY_STATUS_ERROR,
    }

    if args.monday_only:
        ok = update_monday_status(monday_status_map[args.monday_status])
        raise SystemExit(0 if ok else 1)

    try:
        asyncio.run(run_qc_checks())
        if not update_monday_status(MONDAY_STATUS_OK):
            raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"\n[bold red]Kritischer Fehler im Hauptprogramm: {e}[/bold red]")
        update_monday_status(MONDAY_STATUS_ERROR)
        raise SystemExit(1) from e