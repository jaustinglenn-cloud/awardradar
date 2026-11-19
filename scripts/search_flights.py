#!/usr/bin/env python3
"""
AwardRadar Morning Digest Scraper (scripts/search_flights.py)
-------------------------------------------------------------
$0 self-hosted backend executed daily at 7:00 AM CT (13:00 UTC) via GitHub Actions.

1. SerpApi Google Flights Engine (250 free searches/mo):
   Reliable live cash fares, real airlines, flight numbers, durations, stops,
   and Google's price_insights ('low', 'typical', 'high') with zero bot blocks.
2. Camoufox / Playwright Firefox Stealth AA.com Award Scraper:
   Uses anti-detect Firefox (Camoufox, with automatic Playwright Firefox fallback)
   to intercept aa.com/booking/api/search/itinerary for live AAdvantage miles AND
   oneworld partner saver signals (BA & Iberia).
3. Live Transfer Bonus Tracker (Frequent Miler):
   Scrapes https://frequentmiler.com/current-point-transfer-bonuses/ for active
   Chase Ultimate Rewards airline transfer bonuses.
4. Failure & Deal Push Alerts (ntfy.sh):
   Alerts your iPhone immediately if SERPAPI_API_KEY is missing, an API/scraper
   fails, or 0 deals are found—plus sends your morning deal alerts.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AwardRadar")

try:
    import requests  # type: ignore
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from camoufox.sync_api import Camoufox  # type: ignore
    HAS_CAMOUFOX = True
except ImportError:
    HAS_CAMOUFOX = False

try:
    from playwright.sync_api import sync_playwright  # type: ignore
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

ROOT_DIR = Path(__file__).resolve().parent.parent
SEARCHES_PATH = ROOT_DIR / "searches.json"
DEALS_OUTPUT_PATH = ROOT_DIR / "data" / "deals.json"

MAX_ACTIVE_SLOTS = 4
JITTER_MIN_SECONDS = 2.0
JITTER_MAX_SECONDS = 3.5

LONG_HAUL_AIRPORTS = {
    "MXP", "LIN", "VCE", "BLQ", "VRN", "TRN", "FCO",
    "LHR", "CDG", "FRA", "AMS", "MAD", "HND", "ICN",
    "BKK", "DXB", "DOH", "SIN",
}

BASELINE_CHASE_BONUSES: Dict[str, Dict[str, Any]] = {
    "Virgin Atlantic": {"pct": 40, "expires": "2026-12-30", "source": "https://www.doctorofcredit.com/chase-ultimate-rewards-40-transfer-bonus-to-virgin-atlantic-11-4-4/"},
    "British Airways Avios": {"pct": 30, "expires": "2026-12-01", "source": "https://frequentmiler.com/current-point-transfer-bonuses/"},
    "Iberia Plus": {"pct": 30, "expires": "2026-12-01", "source": "https://www.pointsyeah.com/insider/deals/current-credit-card-transfer-bonuses"},
    "Flying Blue": {"pct": 0, "expires": None, "source": "https://frequentmiler.com/current-point-transfer-bonuses/"},
    "Air Canada Aeroplan": {"pct": 0, "expires": None, "source": "https://frequentmiler.com/current-point-transfer-bonuses/"},
}


def sleep_with_jitter(reason: str) -> None:
    if os.environ.get("AWARDRADAR_FAST_TEST") == "1":
        return
    delay = round(random.uniform(JITTER_MIN_SECONDS, JITTER_MAX_SECONDS), 2)
    logger.info("Rate-limit jitter: sleeping %.2fs (%s)", delay, reason)
    time.sleep(delay)


def fetch_live_transfer_bonuses() -> Dict[str, Dict[str, Any]]:
    bonuses = {k: dict(v) for k, v in BASELINE_CHASE_BONUSES.items()}
    if not HAS_REQUESTS or os.environ.get("AWARDRADAR_FAST_TEST") == "1":
        return bonuses

    url = "https://frequentmiler.com/current-point-transfer-bonuses/"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AwardRadar/2.1)"},
            timeout=15,
        )
        if resp.status_code != 200:
            return bonuses

        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text)

        partner_patterns = {
            "Virgin Atlantic": r"Chase Ultimate Rewards[^.]{0,140}?Virgin Atlantic[^.]{0,80}?(\d{2})%",
            "British Airways Avios": r"Chase Ultimate Rewards[^.]{0,140}?British Airways[^.]{0,80}?(\d{2})%",
            "Iberia Plus": r"Chase Ultimate Rewards[^.]{0,140}?Iberia[^.]{0,80}?(\d{2})%",
            "Flying Blue": r"Chase Ultimate Rewards[^.]{0,140}?Flying Blue[^.]{0,80}?(\d{2})%",
            "Air Canada Aeroplan": r"Chase Ultimate Rewards[^.]{0,140}?Aeroplan[^.]{0,80}?(\d{2})%",
        }

        for prog, pat in partner_patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                bonuses[prog]["pct"] = int(m.group(1))
                bonuses[prog]["source"] = url
            else:
                bonuses[prog]["pct"] = 0
                bonuses[prog]["source"] = url

        logger.info("Live transfer bonuses refreshed: %s", {k: v["pct"] for k, v in bonuses.items()})
    except Exception as exc:
        logger.warning("Transfer bonus refresh failed: %s", exc)

    return bonuses


def _format_duration_minutes(total_min: Any) -> str:
    try:
        mins = int(total_min)
        return f"{mins // 60}h {mins % 60}m"
    except (TypeError, ValueError):
        return ""


def fetch_serpapi_google_flights(
    origin: str,
    destination: str,
    departure_date: str,
    cabin: str,
    seats: int,
    api_key: str,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Queries SerpApi's Google Flights engine (free tier: 250 searches/month).
    """
    if os.environ.get("AWARDRADAR_FAST_TEST") == "1":
        return [], "skipped_fast_test"
    if not api_key:
        return [], "missing_serpapi_key"
    if not HAS_REQUESTS:
        return [], "missing_requests_pkg"

    travel_class = 3 if cabin == "Business" else 1
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": departure_date,
        "type": 2,
        "travel_class": travel_class,
        "adults": max(1, min(4, seats)),
        "currency": "USD",
        "hl": "en",
        "api_key": api_key,
    }

    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=25)
        data = resp.json()
        if resp.status_code != 200 or "error" in data:
            err_msg = data.get("error", f"HTTP {resp.status_code}")
            logger.error("SerpApi error for %s->%s (%s): %s", origin, destination, departure_date, err_msg)
            return [], f"serpapi_error: {err_msg}"

        insights = data.get("price_insights", {}) or {}
        price_level = str(insights.get("price_level", "typical") or "typical").lower()
        if price_level not in {"low", "typical", "high"}:
            price_level = "typical"

        raw_groups = (data.get("best_flights") or []) + (data.get("other_flights") or [])
        parsed: List[Dict[str, Any]] = []
        for grp in raw_groups:
            price_val = grp.get("price")
            if not isinstance(price_val, (int, float)) or price_val <= 30:
                continue
            legs = grp.get("flights") or []
            if not legs:
                continue

            airlines = []
            flight_nums = []
            for leg in legs:
                a_name = str(leg.get("airline", "")).strip()
                f_num = str(leg.get("flight_number", "")).strip()
                if a_name and a_name not in airlines:
                    airlines.append(a_name)
                if f_num:
                    flight_nums.append(f_num)

            dep_time = str((legs[0].get("departure_airport") or {}).get("time", "")).strip()
            arr_time = str((legs[-1].get("arrival_airport") or {}).get("time", "")).strip()
            dur_str = _format_duration_minutes(grp.get("total_duration"))
            stops = max(0, len(legs) - 1)
            stop_label = "Nonstop" if stops == 0 else f"{stops} stop{'s' if stops != 1 else ''}"
            fnum_str = " / ".join(flight_nums) if flight_nums else "Google Flights"
            sched_str = f"{fnum_str} • {dep_time} -> {arr_time} ({dur_str}, {stop_label})".strip()

            parsed.append({
                "airline": " / ".join(airlines) if airlines else "Carrier",
                "schedule": sched_str,
                "cash_price_usd": float(price_val),
                "google_price_level": price_level,
            })

        parsed.sort(key=lambda x: x["cash_price_usd"])
        logger.info("SerpApi %s->%s (%s %s): %d live flights, lowest $%.0f", origin, destination, departure_date, cabin, len(parsed), parsed[0]["cash_price_usd"] if parsed else 0)
        return parsed[:3], f"ok_{len(parsed)}_flights"
    except Exception as exc:
        logger.error("SerpApi exception for %s->%s (%s): %s", origin, destination, departure_date, exc)
        return [], f"serpapi_exception: {exc}"


def _parse_aa_payloads(captured_payloads: List[Dict[str, Any]], cabin: str, aa_url: str) -> List[Dict[str, Any]]:
    scraped_deals: List[Dict[str, Any]] = []
    for payload in captured_payloads:
        for sl in (payload.get("slices", []) or [])[:8]:
            segments = sl.get("segments", []) or []
            flight_nums = " / ".join(
                f"{seg.get('flight', {}).get('carrierCode', 'AA')} {seg.get('flight', {}).get('flightNumber', '')}".strip()
                for seg in segments
            )
            airline_names = " / ".join(
                sorted({seg.get("flight", {}).get("carrierName", "American Airlines") for seg in segments})
            )
            low_airline = airline_names.lower()
            is_ib = "iberia" in low_airline
            is_ba = "british" in low_airline

            for pd in (sl.get("pricingDetail", []) or []):
                if not pd.get("productAvailable", False):
                    continue
                prod_type = str(pd.get("productType", "")).upper()
                mapped_cabin = "Business" if prod_type in {"BUSINESS", "FIRST", "FLAGSHIP_BUSINESS"} else "Economy"
                if cabin != "Both" and mapped_cabin != cabin:
                    continue
                pts = int(pd.get("perPassengerAwardPoints", 0) or 0)
                taxes = float((pd.get("perPassengerTaxesAndFees", {}) or {}).get("amount", 0.0) or 0.0)
                if pts > 0:
                    if is_ib:
                        src_label = "PARTNER SAVER SIGNAL (aa.com -> Iberia Plus)"
                        prog_name = "Iberia Plus (via AA Signal)"
                        eco_name = "Chase UR"
                    elif is_ba:
                        src_label = "PARTNER SAVER SIGNAL (aa.com -> BA Avios)"
                        prog_name = "British Airways Avios (via AA Signal)"
                        eco_name = "Chase UR"
                    else:
                        src_label = "LIVE SCRAPED (aa.com)"
                        prog_name = "AAdvantage"
                        eco_name = "AAdvantage"

                    scraped_deals.append({
                        "airline": airline_names or "American Airlines",
                        "flight_numbers": flight_nums or "AA Award",
                        "program": prog_name,
                        "ecosystem": eco_name,
                        "cabin": mapped_cabin,
                        "base_points": pts,
                        "taxes_fees_usd": round(taxes, 2),
                        "availability_source": src_label,
                        "verification_status": "LIVE_SCRAPED",
                        "booking_url": aa_url,
                    })
                    break
    return scraped_deals


def scrape_aa_awards_stealth(
    origin: str,
    destination: str,
    departure_date: str,
    cabin: str,
    seats: int,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Tries Camoufox (anti-detect Firefox) first, falling back cleanly to Playwright Firefox
    if Camoufox's GitHub binary release is unavailable. Never crashes the pipeline.
    """
    if os.environ.get("AWARDRADAR_FAST_TEST") == "1":
        return [], "skipped_fast_test"

    aa_url = (
        f"https://www.aa.com/booking/search?locale=en_US&pax={seats}&adult={seats}"
        f"&type=OneWay&searchType=Award&cabin=&carriers=ALL"
        f"&slices=%5B%7B%22orig%22:%22{origin}%22,%22origNearby%22:false,"
        f"%22dest%22:%22{destination}%22,%22destNearby%22:false,"
        f"%22date%22:%22{departure_date}%22%7D%5D"
    )
    captured_payloads: List[Dict[str, Any]] = []

    if HAS_CAMOUFOX:
        try:
            sleep_with_jitter(f"Camoufox AA.com {origin}->{destination} {departure_date}")
            with Camoufox(headless=True) as browser:
                page = browser.new_page()

                def handle_response(response) -> None:
                    if "booking/api/search/itinerary" in response.url and response.status == 200:
                        try:
                            captured_payloads.append(response.json())
                        except Exception:
                            pass

                page.on("response", handle_response)
                page.goto(aa_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(10000)

            deals = _parse_aa_payloads(captured_payloads, cabin, aa_url)
            return deals, f"camoufox_{len(deals)}_hits"
        except Exception as exc:
            logger.warning("Camoufox AA.com skipped/failed (%s); trying Playwright Firefox fallback.", exc)

    if HAS_PLAYWRIGHT:
        try:
            sleep_with_jitter(f"Playwright Firefox AA.com {origin}->{destination} {departure_date}")
            with sync_playwright() as p:
                browser = p.firefox.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
                page = context.new_page()

                def handle_pw_response(response) -> None:
                    if "booking/api/search/itinerary" in response.url and response.status == 200:
                        try:
                            captured_payloads.append(response.json())
                        except Exception:
                            pass

                page.on("response", handle_pw_response)
                page.goto(aa_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(8500)
                browser.close()

            deals = _parse_aa_payloads(captured_payloads, cabin, aa_url)
            return deals, f"pw_firefox_{len(deals)}_hits"
        except Exception as exc:
            logger.warning("Playwright Firefox AA.com failed (%s).", exc)
            return [], f"aa_blocked_or_err: {exc}"

    return [], "no_browser_available"


def map_carrier_to_program(airline_name: str, is_long_haul: bool, cabin: str) -> Dict[str, Any]:
    low = airline_name.lower()
    if "american" in low or "british" in low or "iberia" in low or "finnair" in low:
        return {
            "program": "Iberia Plus" if "iberia" in low else ("British Airways Avios" if "british" in low else "AAdvantage"),
            "ecosystem": "Chase UR" if ("iberia" in low or "british" in low) else "AAdvantage",
            "saver_target_pts": 57500 if (is_long_haul and cabin == "Business") else (22500 if is_long_haul else 11000),
        }
    if "air france" in low or "klm" in low or "virgin" in low:
        return {
            "program": "Flying Blue",
            "ecosystem": "Chase UR",
            "saver_target_pts": 50000 if (is_long_haul and cabin == "Business") else (20000 if is_long_haul else 12000),
        }
    if "delta" in low:
        return {
            "program": "Virgin Atlantic" if is_long_haul else "Delta SkyMiles",
            "ecosystem": "Chase UR" if is_long_haul else "Delta SkyMiles",
            "saver_target_pts": 50000 if (is_long_haul and cabin == "Business") else (15000 if not is_long_haul else 30000),
        }
    if "southwest" in low:
        return {
            "program": "Southwest Rapid Rewards",
            "ecosystem": "Chase UR",
            "saver_target_pts": 9500,
        }
    if "lufthansa" in low or "united" in low or "swiss" in low or "austrian" in low or "air canada" in low:
        return {
            "program": "Air Canada Aeroplan",
            "ecosystem": "Chase UR",
            "saver_target_pts": 70000 if (is_long_haul and cabin == "Business") else (35000 if is_long_haul else 12500),
        }
    return {
        "program": "Chase Travel Portal (1.25¢–1.5¢)",
        "ecosystem": "Chase UR",
        "saver_target_pts": 0,
    }


def compute_cpp_and_star_grade(
    base_points: int,
    taxes_fees_usd: float,
    cash_price_usd: float,
    cabin: str,
    ecosystem: str,
    program: str,
    google_price_level: str,
    live_bonuses: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    bonus_key = program.replace(" (via AA Signal)", "")
    bonus_pct = float((live_bonuses.get(bonus_key) or {}).get("pct", 0)) if ecosystem == "Chase UR" else 0.0
    effective_points = math.ceil(base_points / (1.0 + bonus_pct / 100.0)) if bonus_pct > 0 else base_points
    net_cash = max(0.0, cash_price_usd - taxes_fees_usd)
    cpp = round((net_cash / effective_points) * 100.0, 2) if effective_points > 0 else 0.0

    is_biz = cabin == "Business"
    if is_biz:
        if cpp >= 4.5 or (effective_points <= 55000 and cpp >= 3.6):
            stars = 5
        elif cpp >= 3.2:
            stars = 4
        elif cpp >= 2.2:
            stars = 3
        elif cpp >= 1.4:
            stars = 2
        else:
            stars = 1
    else:
        if cpp >= 2.3 or (effective_points <= 20000 and cpp >= 1.9):
            stars = 5
        elif cpp >= 1.7:
            stars = 4
        elif cpp >= 1.35:
            stars = 3
        elif cpp >= 1.05:
            stars = 2
        else:
            stars = 1

    if stars == 5:
        rec = "BOOK AWARD NOW (5★ UNICORN)"
    elif stars == 4:
        rec = "GREAT SAVER AWARD (4★)"
    elif google_price_level == "low" and stars <= 3:
        rec = "PAY CASH (GOOGLE FLIGHTS LOW FARE)"
    elif stars <= 2:
        rec = "SUBPAR CPP — SAVE POINTS"
    else:
        rec = "WAIT / MONITOR"

    return {
        "transfer_bonus_pct": int(bonus_pct),
        "effective_points": effective_points,
        "cpp": cpp,
        "stars": stars,
        "recommendation": rec,
    }


def send_ntfy_message(topic: str, title: str, message: str, priority: str = "high", tags: str = "airplane,star") -> None:
    if not topic:
        return
    req = urllib.request.Request(
        url=f"https://ntfy.sh/{topic.strip()}",
        data=message.encode("utf-8"),
        method="POST",
        headers={"Title": title, "Priority": priority, "Tags": tags},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Sent ntfy.sh message to '%s' (HTTP %d)", topic, resp.status)
    except urllib.error.URLError as exc:
        logger.warning("ntfy.sh delivery failed: %s", exc)


def main() -> None:
    if not SEARCHES_PATH.exists():
        raise FileNotFoundError(f"Missing searches configuration at {SEARCHES_PATH}")

    config = json.loads(SEARCHES_PATH.read_text(encoding="utf-8"))
    active_slots = config.get("searches", [])[:MAX_ACTIVE_SLOTS]
    live_bonuses = fetch_live_transfer_bonuses()

    serpapi_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()

    all_deals: List[Dict[str, Any]] = []
    deal_counter = 300
    diagnostics: Dict[str, Any] = {
        "serpapi_configured": bool(serpapi_key),
        "has_camoufox": HAS_CAMOUFOX,
        "has_playwright": HAS_PLAYWRIGHT,
        "serpapi_queries": 0,
        "serpapi_hits": 0,
        "aa_queries": 0,
        "aa_hits": 0,
        "errors": [],
    }

    if not serpapi_key and os.environ.get("AWARDRADAR_FAST_TEST") != "1":
        diagnostics["errors"].append("SERPAPI_API_KEY secret is not set in GitHub Actions Secrets.")

    for slot in active_slots:
        slot_id = slot.get("id", 1)
        origin = str(slot.get("origin", "DFW")).upper()
        destination = str(slot.get("destination", "MXP")).upper()
        scan_date = str(slot.get("date", "2027-09-10"))
        cabin_pref = str(slot.get("cabin", "Business"))
        seats = int(slot.get("seats", 2))
        is_long_haul = (origin in LONG_HAUL_AIRPORTS) or (destination in LONG_HAUL_AIRPORTS)

        primary_cabin = ("Business" if is_long_haul else "Economy") if cabin_pref == "Both" else cabin_pref

        diagnostics["serpapi_queries"] += 1
        gf_flights, serp_status = fetch_serpapi_google_flights(origin, destination, scan_date, primary_cabin, seats, serpapi_key)
        diagnostics["serpapi_hits"] += len(gf_flights)
        if not gf_flights and serp_status != "skipped_fast_test":
            diagnostics["errors"].append(f"{origin}->{destination} ({scan_date}): {serp_status}")

        diagnostics["aa_queries"] += 1
        aa_awards, aa_status = scrape_aa_awards_stealth(origin, destination, scan_date, cabin_pref, seats)
        diagnostics["aa_hits"] += len(aa_awards)

        iberia_url = f"https://www.iberia.com/us/flights/{origin.lower()}-{destination.lower()}/"
        ba_url = "https://www.britishairways.com/travel/redeem/execclub/_gf/en_us"
        gf_url = f"https://www.google.com/travel/flights?q=Flights%20to%20{destination}%20from%20{origin}%20on%20{scan_date}%20oneway"

        for cand in aa_awards:
            c_cab = cand["cabin"]
            cash_price = float(gf_flights[0]["cash_price_usd"]) if gf_flights else (2800.0 if c_cab == "Business" else 650.0)
            price_level = str(gf_flights[0]["google_price_level"]) if gf_flights else "typical"
            metrics = compute_cpp_and_star_grade(
                base_points=int(cand["base_points"]),
                taxes_fees_usd=float(cand["taxes_fees_usd"]),
                cash_price_usd=cash_price,
                cabin=c_cab,
                ecosystem=cand["ecosystem"],
                program=cand["program"],
                google_price_level=price_level,
                live_bonuses=live_bonuses,
            )
            deal_counter += 1
            all_deals.append({
                "id": f"deal-{deal_counter}",
                "slot_id": slot_id,
                "origin": origin,
                "destination": destination,
                "date": scan_date,
                "flex_offset": 0,
                "airline": cand["airline"],
                "flight_numbers": cand["flight_numbers"],
                "program": cand["program"],
                "ecosystem": cand["ecosystem"],
                "cabin": c_cab,
                "seats": seats,
                "base_points": int(cand["base_points"]),
                "taxes_fees_usd": float(cand["taxes_fees_usd"]),
                "cash_price_usd": cash_price,
                "google_price_level": price_level,
                "availability_source": cand["availability_source"],
                "verification_status": "LIVE_SCRAPED",
                "booking_url": cand["booking_url"],
                "iberia_url": iberia_url,
                "ba_url": ba_url,
                **metrics,
            })

        for gf in gf_flights[:2]:
            mapped = map_carrier_to_program(gf["airline"], is_long_haul, primary_cabin)
            portal_pts = math.ceil(gf["cash_price_usd"] / 0.0125)
            base_pts = mapped["saver_target_pts"] if mapped["saver_target_pts"] > 0 else portal_pts
            est_fees = 180.0 if (is_long_haul and mapped["saver_target_pts"] > 0) else 5.60

            metrics = compute_cpp_and_star_grade(
                base_points=base_pts,
                taxes_fees_usd=est_fees,
                cash_price_usd=gf["cash_price_usd"],
                cabin=primary_cabin,
                ecosystem=mapped["ecosystem"],
                program=mapped["program"],
                google_price_level=gf["google_price_level"],
                live_bonuses=live_bonuses,
            )
            deal_counter += 1
            all_deals.append({
                "id": f"deal-{deal_counter}",
                "slot_id": slot_id,
                "origin": origin,
                "destination": destination,
                "date": scan_date,
                "flex_offset": 0,
                "airline": gf["airline"],
                "flight_numbers": gf["schedule"],
                "program": mapped["program"],
                "ecosystem": mapped["ecosystem"],
                "cabin": primary_cabin,
                "seats": seats,
                "base_points": base_pts,
                "taxes_fees_usd": est_fees,
                "cash_price_usd": gf["cash_price_usd"],
                "google_price_level": gf["google_price_level"],
                "availability_source": "SERPAPI GOOGLE FLIGHTS + Partner Chart",
                "verification_status": "LIVE_CASH_SCRAPED",
                "booking_url": gf_url,
                "iberia_url": iberia_url,
                "ba_url": ba_url,
                **metrics,
            })

    all_deals.sort(key=lambda d: (d.get("verification_status") == "LIVE_SCRAPED", d["stars"], d["cpp"]), reverse=True)

    DEALS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEALS_OUTPUT_PATH.write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "runner": "GitHub Actions Morning Digest (SerpApi + Firefox/Camoufox aa.com + Frequent Miler)",
        "active_slots_scanned": len(active_slots),
        "live_transfer_bonuses": live_bonuses,
        "diagnostics": diagnostics,
        "total_deals": len(all_deals),
        "deals": all_deals,
    }, indent=2), encoding="utf-8")

    if os.environ.get("AWARDRADAR_FAST_TEST") == "1":
        return

    if ntfy_topic and (len(all_deals) == 0 or diagnostics["errors"]):
        err_summary = "\n".join(diagnostics["errors"][:3]) if diagnostics["errors"] else "0 flights returned."
        send_ntfy_message(
            ntfy_topic,
            title="AwardRadar Morning Run Warning",
            message=f"Found {len(all_deals)} deals.\n{err_summary}",
            priority="high",
            tags="warning,airplane",
        )
        if len(all_deals) == 0:
            return

    alert_rules = config.get("alert_rules", {}) or {}
    min_stars = int(alert_rules.get("min_stars", 4))
    min_cpp = float(alert_rules.get("min_cpp", 0.0))
    max_points = int(alert_rules.get("max_points_per_person", 0))
    alert_cabin = str(alert_rules.get("cabin", "all"))
    alert_eco = str(alert_rules.get("ecosystem", "all"))
    alert_low_cash = bool(alert_rules.get("alert_on_low_cash", True))

    matched_alerts = []
    for d in all_deals:
        if alert_cabin != "all" and d["cabin"] != alert_cabin:
            continue
        if alert_eco != "all" and d["ecosystem"] != alert_eco:
            continue
        if max_points > 0 and d["effective_points"] > max_points:
            continue
        meets_award_rule = (d["stars"] >= min_stars) and (d["cpp"] >= min_cpp)
        meets_cash_rule = alert_low_cash and (d.get("google_price_level") == "low")
        if meets_award_rule or meets_cash_rule:
            matched_alerts.append(d)

    if ntfy_topic and matched_alerts:
        top = matched_alerts[0]
        send_ntfy_message(
            ntfy_topic,
            title=f"AwardRadar {top['stars']}★ Morning Deal: {top['origin']}->{top['destination']} (${top['cash_price_usd']:,.0f})",
            message=(
                f"{len(matched_alerts)} matching deal(s) today!\n"
                f"Top: {top['origin']} -> {top['destination']} on {top['date']} ({top['airline']} • {top['cabin']})\n"
                f"{top['flight_numbers']}\n"
                f"Cash: ${top['cash_price_usd']:,.0f} ({top['google_price_level'].upper()}) | {top['program']}: {top['effective_points']:,} pts ({top['cpp']}¢ CPP)"
            ),
            priority="high",
            tags="airplane,star,moneybag",
        )


if __name__ == "__main__":
    main()
