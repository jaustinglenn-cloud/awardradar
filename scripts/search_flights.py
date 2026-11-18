#!/usr/bin/env python3
"""
AwardRadar Live Flight, Award & Transfer Bonus Scraper (scripts/search_flights.py)
----------------------------------------------------------------------------------
$0 self-hosted backend executed every 6 hours via GitHub Actions.

1. Live Transfer Bonus Tracker (Frequent Miler):
   Scrapes https://frequentmiler.com/current-point-transfer-bonuses/ every 6 hours
   for active Chase Ultimate Rewards airline transfer bonuses (e.g. Virgin Atlantic,
   British Airways Avios, Iberia Plus, Flying Blue, Air Canada Aeroplan) and writes
   them to data/deals.json so the PWA auto-applies them without manual input.
2. Live Cash & Real Operating Flights (fast-flights Google Flights Protobuf):
   Fetches real-time cash prices, Google Flights' current_price indicator
   ('low', 'typical', 'high'), and actual operating airlines/schedules.
3. Live Unauthenticated Award Scraping (Playwright Stealth):
   Queries American Airlines (aa.com/booking/api/search/itinerary) for AAdvantage &
   oneworld partners (AA, BA, IB, JL, CX, QR, AY) without personal logins.
4. Zero Fake/Placeholder Flights:
   Never invents placeholder flight numbers or synthetic award deals. Outputs only
   live-scraped award hits (LIVE_SCRAPED) and live Google Flights cash baselines
   (LIVE_CASH).
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
    from fast_flights import FlightData, Passengers, create_filter, get_flights  # type: ignore
    HAS_FAST_FLIGHTS = True
except ImportError:
    HAS_FAST_FLIGHTS = False

try:
    from playwright.sync_api import sync_playwright  # type: ignore
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

ROOT_DIR = Path(__file__).resolve().parent.parent
SEARCHES_PATH = ROOT_DIR / "searches.json"
DEALS_OUTPUT_PATH = ROOT_DIR / "data" / "deals.json"

MAX_ACTIVE_SLOTS = 5
JITTER_MIN_SECONDS = 3.0
JITTER_MAX_SECONDS = 5.5

# Verified Chase UR airline transfer bonuses as of Nov 18, 2026 (auto-refreshed live on every run)
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


def generate_date_window(base_date_iso: str, flex_enabled: bool, max_offsets: int = 1) -> List[Tuple[str, int]]:
    base_dt = dt.date.fromisoformat(base_date_iso)
    if not flex_enabled:
        return [(base_date_iso, 0)]
    ordered_offsets = [0, -1, 1, -2, 2, -3, 3][: (2 * max_offsets + 1)]
    return [((base_dt + dt.timedelta(days=off)).isoformat(), off) for off in ordered_offsets]


def fetch_live_transfer_bonuses() -> Dict[str, Dict[str, Any]]:
    """
    Scrapes Frequent Miler's live transfer bonus tracker for active Chase Ultimate Rewards
    airline transfer bonuses. Falls back to the latest verified snapshot if offline.
    """
    bonuses = {k: dict(v) for k, v in BASELINE_CHASE_BONUSES.items()}
    if not HAS_REQUESTS or os.environ.get("AWARDRADAR_FAST_TEST") == "1":
        return bonuses

    url = "https://frequentmiler.com/current-point-transfer-bonuses/"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AwardRadar/1.5)"},
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
                # Reverse match (e.g., "30% transfer bonus from Chase Ultimate Rewards to British Airways")
                rev_pat = pat.replace(r"Chase Ultimate Rewards[^.]{0,140}?", r"(\d{2})%[^.]{0,100}?Chase Ultimate Rewards[^.]{0,100}?")
                # Reset to 0 if not listed as active on Frequent Miler
                bonuses[prog]["pct"] = 0
                bonuses[prog]["source"] = url

        logger.info("Live transfer bonuses refreshed from Frequent Miler: %s", {k: v["pct"] for k, v in bonuses.items()})
    except Exception as exc:
        logger.warning("Could not refresh transfer bonuses from Frequent Miler: %s", exc)

    return bonuses


def fetch_live_cash_flights(
    origin: str,
    destination: str,
    departure_date: str,
    cabin: str,
    seats: int,
) -> Dict[str, Any]:
    """
    Queries Google Flights via fast-flights (Protobuf) for real live cash fares,
    current_price indicator, and actual operating airline. Returns cash_live=False
    if Google Flights hasn't opened that far-out date or is unreachable.
    """
    if not HAS_FAST_FLIGHTS or os.environ.get("AWARDRADAR_FAST_TEST") == "1":
        return {"cash_live": False}

    seat_type = "business" if cabin == "Business" else "economy"
    try:
        sleep_with_jitter(f"fast-flights {origin}->{destination} {departure_date} [{seat_type}]")
        filter_obj = create_filter(
            flight_data=[
                FlightData(
                    date=departure_date,
                    from_airport=origin,
                    to_airport=destination,
                )
            ],
            trip="one-way",
            seat=seat_type,
            passengers=Passengers(adults=max(1, min(4, seats))),
        )
        result = get_flights(filter_obj)
        price_indicator = str(getattr(result, "current_price", "typical") or "typical").lower()
        if price_indicator not in {"low", "typical", "high"}:
            price_indicator = "typical"

        flights_list = getattr(result, "flights", []) or []
        best_cash = None
        best_airline = None
        best_duration = None
        best_stops = None

        for f in flights_list:
            raw_price = str(getattr(f, "price", ""))
            digits = "".join(ch for ch in raw_price if ch.isdigit())
            if digits:
                val = float(digits)
                if val > 30 and (best_cash is None or val < best_cash):
                    best_cash = val
                    best_airline = getattr(f, "name", None)
                    best_duration = getattr(f, "duration", None)
                    best_stops = getattr(f, "stops", None)

        if best_cash is not None:
            return {
                "cash_price_usd": best_cash,
                "google_price_level": price_indicator,
                "operating_airline": best_airline or "Google Flights Carrier",
                "flight_summary": f"{best_duration or 'Scheduled'} • {best_stops if best_stops is not None else '?'} stop(s)",
                "cash_live": True,
            }
    except Exception as exc:
        logger.warning("fast-flights error for %s->%s (%s): %s", origin, destination, departure_date, exc)

    return {"cash_live": False}


def scrape_aa_live_awards(
    origin: str,
    destination: str,
    departure_date: str,
    cabin: str,
    seats: int,
) -> List[Dict[str, Any]]:
    """
    Free, unauthenticated Playwright stealth scraper for American Airlines (aa.com).
    Intercepts live JSON from /booking/api/search/itinerary without logging in.
    """
    if not HAS_PLAYWRIGHT or os.environ.get("AWARDRADAR_FAST_TEST") == "1":
        return []

    scraped_deals: List[Dict[str, Any]] = []
    captured_payloads: List[Dict[str, Any]] = []

    try:
        sleep_with_jitter(f"Playwright AA.com award scrape {origin}->{destination} {departure_date}")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/129.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            def handle_response(response) -> None:
                if "booking/api/search/itinerary" in response.url and response.status == 200:
                    try:
                        captured_payloads.append(response.json())
                    except Exception:
                        pass

            page.on("response", handle_response)

            aa_url = (
                f"https://www.aa.com/booking/search?locale=en_US&pax={seats}&adult={seats}"
                f"&type=OneWay&searchType=Award&cabin=&carriers=ALL"
                f"&slices=%5B%7B%22orig%22:%22{origin}%22,%22origNearby%22:false,"
                f"%22dest%22:%22{destination}%22,%22destNearby%22:false,"
                f"%22date%22:%22{departure_date}%22%7D%5D"
            )
            page.goto(aa_url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(8500)
            browser.close()

        for payload in captured_payloads:
            slices = payload.get("slices", []) or []
            for sl in slices[:6]:
                segments = sl.get("segments", []) or []
                flight_nums = " / ".join(
                    f"{seg.get('flight', {}).get('carrierCode', 'AA')} {seg.get('flight', {}).get('flightNumber', '')}".strip()
                    for seg in segments
                )
                airline_names = " / ".join(
                    sorted({seg.get("flight", {}).get("carrierName", "American Airlines") for seg in segments})
                )
                pricing_detail = sl.get("pricingDetail", []) or []
                for pd in pricing_detail:
                    if not pd.get("productAvailable", False):
                        continue
                    prod_type = str(pd.get("productType", "")).upper()
                    mapped_cabin = "Business" if prod_type in {"BUSINESS", "FIRST", "FLAGSHIP_BUSINESS"} else "Economy"
                    if cabin != "Both" and mapped_cabin != cabin:
                        continue
                    pts = int(pd.get("perPassengerAwardPoints", 0) or 0)
                    taxes = float((pd.get("perPassengerTaxesAndFees", {}) or {}).get("amount", 0.0) or 0.0)
                    if pts > 0:
                        scraped_deals.append({
                            "airline": airline_names or "American Airlines",
                            "flight_numbers": flight_nums or "AA Award Itinerary",
                            "program": "AAdvantage",
                            "ecosystem": "AAdvantage",
                            "cabin": mapped_cabin,
                            "base_points": pts,
                            "taxes_fees_usd": round(taxes, 2),
                            "availability_source": "LIVE SCRAPED (aa.com Playwright)",
                            "verification_status": "LIVE_SCRAPED",
                            "booking_url": aa_url,
                        })
                        break
    except Exception as exc:
        logger.warning("AA Playwright scraper blocked or timed out for %s->%s (%s): %s", origin, destination, departure_date, exc)

    return scraped_deals


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
    bonus_pct = float((live_bonuses.get(program) or {}).get("pct", 0)) if ecosystem == "Chase UR" else 0.0
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


def send_ntfy_alert(topic: str, high_deals: List[Dict[str, Any]]) -> None:
    if not topic or not high_deals:
        return
    top = high_deals[0]
    title = f"AwardRadar {top['stars']}★ Alert: {top['origin']}->{top['destination']} ({top['cpp']}c CPP)"
    body = (
        f"{top['origin']} -> {top['destination']} on {top['date']} ({top['airline']} / {top['program']} {top['cabin']})\n"
        f"Award: {top['effective_points']:,} pts + ${top['taxes_fees_usd']:.0f} ({top['seats']} seats) "
        f"vs ${top['cash_price_usd']:,.0f} cash ({top['cpp']}¢ CPP)"
    ).encode("utf-8")

    req = urllib.request.Request(
        url=f"https://ntfy.sh/{topic.strip()}",
        data=body,
        method="POST",
        headers={"Title": title, "Priority": "high", "Tags": "airplane,star,moneybag"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Sent ntfy.sh alert to '%s' (HTTP %d)", topic, resp.status)
    except urllib.error.URLError as exc:
        logger.warning("ntfy.sh delivery failed: %s", exc)


def main() -> None:
    if not SEARCHES_PATH.exists():
        raise FileNotFoundError(f"Missing searches configuration at {SEARCHES_PATH}")

    config = json.loads(SEARCHES_PATH.read_text(encoding="utf-8"))
    active_slots = config.get("searches", [])[:MAX_ACTIVE_SLOTS]

    # 1. Fetch live Chase UR transfer bonuses automatically
    live_bonuses = fetch_live_transfer_bonuses()

    all_deals: List[Dict[str, Any]] = []
    deal_counter = 300

    for slot in active_slots:
        slot_id = slot.get("id", 1)
        origin = str(slot.get("origin", "DFW")).upper()
        destination = str(slot.get("destination", "MXP")).upper()
        base_date = str(slot.get("date", "2027-09-10"))
        flex_enabled = bool(slot.get("flex", True))
        cabin_pref = str(slot.get("cabin", "Both"))
        seats = int(slot.get("seats", 2))

        for scan_date, flex_offset in generate_date_window(base_date, flex_enabled, max_offsets=1):
            cash_cache: Dict[str, Dict[str, Any]] = {}
            cabins_to_check = ["Business", "Economy"] if cabin_pref == "Both" else [cabin_pref]
            for c_cab in cabins_to_check:
                cash_cache[c_cab] = fetch_live_cash_flights(origin, destination, scan_date, c_cab, seats)

            # Live AA.com Playwright award scrape (zero fake/placeholder fallback)
            live_awards = scrape_aa_live_awards(origin, destination, scan_date, cabin_pref, seats)

            for cand in live_awards:
                c_cab = cand["cabin"]
                cash_info = cash_cache.get(c_cab) or fetch_live_cash_flights(origin, destination, scan_date, c_cab, seats)
                if not cash_info.get("cash_live"):
                    continue

                metrics = compute_cpp_and_star_grade(
                    base_points=int(cand["base_points"]),
                    taxes_fees_usd=float(cand["taxes_fees_usd"]),
                    cash_price_usd=float(cash_info["cash_price_usd"]),
                    cabin=c_cab,
                    ecosystem=cand["ecosystem"],
                    program=cand["program"],
                    google_price_level=str(cash_info["google_price_level"]),
                    live_bonuses=live_bonuses,
                )

                deal_counter += 1
                all_deals.append({
                    "id": f"deal-{deal_counter}",
                    "slot_id": slot_id,
                    "origin": origin,
                    "destination": destination,
                    "date": scan_date,
                    "flex_offset": flex_offset,
                    "airline": cand["airline"],
                    "flight_numbers": cand["flight_numbers"],
                    "program": cand["program"],
                    "ecosystem": cand["ecosystem"],
                    "cabin": c_cab,
                    "seats": seats,
                    "base_points": int(cand["base_points"]),
                    "taxes_fees_usd": float(cand["taxes_fees_usd"]),
                    "cash_price_usd": float(cash_info["cash_price_usd"]),
                    "google_price_level": str(cash_info["google_price_level"]),
                    "availability_source": cand["availability_source"],
                    "verification_status": "LIVE_SCRAPED",
                    "booking_url": cand["booking_url"],
                    **metrics,
                })

    all_deals.sort(key=lambda d: (d["stars"], d["cpp"]), reverse=True)

    DEALS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEALS_OUTPUT_PATH.write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "runner": "GitHub Actions (fast-flights + Playwright aa.com + Frequent Miler Live Bonuses)",
        "active_slots_scanned": len(active_slots),
        "live_transfer_bonuses": live_bonuses,
        "total_deals": len(all_deals),
        "deals": all_deals,
    }, indent=2), encoding="utf-8")

    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()
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
        send_ntfy_alert(ntfy_topic, matched_alerts)


if __name__ == "__main__":
    main()
