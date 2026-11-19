#!/usr/bin/env python3
"""
AwardRadar — 5-Layer Zero-Cost Award Flight Waterfall Engine
Runs daily via GitHub Actions (or on-demand via workflow_dispatch).

Features:
  - Enforces max_itineraries quota cap from searches.json
  - Grades every flight 1-5:
      1 = Average deal found any time of year
      2 = Google Flights "Low" unusual price
      3 = Solid saver deal
      4 = Tremendous award value (>4.0 CPP Business/First or >=2.0 CPP Economy)
      5 = Unicorn deal (<1x/year — stupid not to take it)
  - Alerts on Grade 5 Unicorn deals departing DFW or DAL even outside configured itineraries
  - Sorts Live Flights by Grade (5 -> 1), prioritizing Business/First while keeping Economy deals
"""

import os
import re
import json
import hashlib
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup

try:
    from fast_flights import FlightData, Passengers, get_flights
    HAS_FAST_FLIGHTS = True
except Exception:
    HAS_FAST_FLIGHTS = False

SEARCHES_FILE = "searches.json"
DEALS_FILE = "data/deals.json"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REDDIT_HEADERS = {
    "User-Agent": "AwardRadarBot/2.1 (by /u/awardradar_app; zero-cost personal tracker)"
}

CITY_TO_IATA = {
    "dallas": "DFW", "dfw": "DFW", "love field": "DAL", "dal": "DAL",
    "austin": "AUS", "houston": "IAH", "new york": "JFK", "nyc": "JFK", "jfk": "JFK", "ewr": "EWR",
    "chicago": "ORD", "ord": "ORD", "san francisco": "SFO", "sfo": "SFO",
    "los angeles": "LAX", "lax": "LAX", "long beach": "LGB", "lgb": "LGB",
    "miami": "MIA", "mia": "MIA", "seattle": "SEA", "boston": "BOS", "atlanta": "ATL", "washington": "IAD",
    "zurich": "ZRH", "milan": "MXP", "venice": "VCE", "rome": "FCO", "paris": "CDG",
    "london": "LHR", "amsterdam": "AMS", "frankfurt": "FRA", "munich": "MUC",
    "madrid": "MAD", "barcelona": "BCN", "lisbon": "LIS", "dublin": "DUB",
    "tokyo": "HND", "haneda": "HND", "narita": "NRT", "osaka": "KIX",
    "seoul": "ICN", "singapore": "SIN", "doha": "DOH", "dubai": "DXB",
    "sydney": "SYD", "auckland": "AKL", "cancun": "CUN", "honolulu": "HNL",
}

REGION_MAP = {
    "EUR": {"ZRH", "MXP", "VCE", "FCO", "CDG", "LHR", "AMS", "FRA", "MUC", "MAD", "BCN", "LIS", "DUB", "VIE", "CPH", "ARN", "ATH"},
    "TYO": {"HND", "NRT"},
    "ASIA": {"HND", "NRT", "KIX", "ICN", "SIN", "HKG", "TPE", "BKK"},
    "US": {"DFW", "DAL", "AUS", "IAH", "JFK", "EWR", "ORD", "SFO", "LAX", "LGB", "MIA", "SEA", "BOS", "ATL", "IAD", "DEN"},
}

AIRPORT_NAMES = {
    "DFW": "Dallas/Fort Worth", "DAL": "Dallas Love Field", "AUS": "Austin", "IAH": "Houston",
    "JFK": "New York JFK", "EWR": "Newark", "ORD": "Chicago O'Hare",
    "SFO": "San Francisco", "LAX": "Los Angeles", "LGB": "Long Beach", "MIA": "Miami",
    "SEA": "Seattle", "BOS": "Boston", "ATL": "Atlanta", "IAD": "Washington Dulles",
    "ZRH": "Zurich", "MXP": "Milan Malpensa", "VCE": "Venice Marco Polo", "FCO": "Rome",
    "CDG": "Paris CDG", "LHR": "London Heathrow", "AMS": "Amsterdam",
    "FRA": "Frankfurt", "MUC": "Munich", "MAD": "Madrid", "BCN": "Barcelona",
    "LIS": "Lisbon", "DUB": "Dublin", "HND": "Tokyo Haneda", "NRT": "Tokyo Narita",
    "ICN": "Seoul Incheon", "SIN": "Singapore", "DOH": "Doha", "DXB": "Dubai",
    "SYD": "Sydney",
}

PROGRAM_CURRENCIES = {
    "Air France/KLM Flying Blue": ["Chase UR", "Delta SkyMiles", "Amex MR", "Capital One", "Citi TYP", "Bilt"],
    "Virgin Atlantic": ["Chase UR", "Delta SkyMiles", "Amex MR", "Capital One", "Citi TYP", "Bilt"],
    "Air Canada Aeroplan": ["Chase UR", "Amex MR", "Capital One", "Bilt"],
    "British Airways Avios": ["Chase UR", "American AAdvantage", "Amex MR", "Capital One", "Bilt"],
    "Iberia Plus": ["Chase UR", "American AAdvantage", "Amex MR", "Bilt"],
    "Qatar Privilege Club": ["Chase UR", "American AAdvantage", "Amex MR", "Citi TYP", "Bilt"],
    "United MileagePlus": ["Chase UR", "Bilt"],
    "American AAdvantage": ["American AAdvantage", "Bilt"],
    "Delta SkyMiles": ["Delta SkyMiles", "Amex MR"],
    "Southwest Rapid Rewards": ["Chase UR"],
    "ANA Mileage Club": ["Amex MR"],
    "Singapore KrisFlyer": ["Chase UR", "Amex MR", "Capital One", "Citi TYP"],
    "Emirates Skywards": ["Chase UR", "Amex MR", "Capital One", "Citi TYP", "Bilt"],
}

AWARD_KEYWORDS = [
    "miles", "points", "award", "business class", "first class", "lie-flat",
    "promo rewards", "transfer bonus", "sweet spot", "avios", "flying blue",
    "aeroplan", "qsuites", "ana ", "jal ", "virgin atlantic", "aadvantage",
    "skymiles", "iberia", "redemption", "availability", "saver", "wide open", "partner space"
]

RSS_FEEDS = [
    ("Frequent Miler", "https://frequentmiler.com/feed/"),
    ("One Mile at a Time", "https://onemileatatime.com/feed/"),
    ("View from the Wing", "https://viewfromthewing.com/feed/"),
    ("Thrifty Traveler", "https://thriftytraveler.com/feed/"),
    ("Doctor of Credit", "https://www.doctorofcredit.com/feed/"),
]

HEURISTIC_CASH = {
    "First": 8500,
    "Business": 3600,
    "Premium Economy": 1650,
    "Economy": 680,
}

CABIN_PRIORITY = {"First": 4, "Business": 3, "Premium Economy": 2, "Economy": 1}


def load_config():
    """Supports both v2 object schema and legacy array searches.json."""
    default_cfg = {
        "max_itineraries": 4,
        "global_partners": ["Chase UR", "American AAdvantage", "Delta SkyMiles"],
        "global_airlines": ["American Airlines", "Delta Air Lines", "Iberia", "British Airways", "Air France", "KLM", "Virgin Atlantic"],
        "alert_config": {"min_grade": 4, "alert_dfw_dal_unicorns": True},
        "searches": [],
    }
    if os.path.exists(SEARCHES_FILE):
        try:
            with open(SEARCHES_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                default_cfg["searches"] = raw
            elif isinstance(raw, dict):
                default_cfg.update(raw)
        except Exception as e:
            print(f"[WARN] Could not parse {SEARCHES_FILE}: {e}")
    max_cap = max(1, min(6, int(default_cfg.get("max_itineraries", 4))))
    default_cfg["searches"] = (default_cfg.get("searches") or [])[:max_cap]
    return default_cfg


def extract_miles(text):
    m = re.search(r"\b(\d{1,3}),(\d{3})\s*(?:k\s*)?(?:miles|points|pts|avios)\b", text, re.I)
    if m:
        val = int(m.group(1) + m.group(2))
        if 4000 <= val <= 300000:
            return val
    m2 = re.search(r"\b(\d{1,3}(?:\.\d)?)\s*[kK]\b(?:\s*(?:miles|points|pts|avios|each|ow|one[- ]way))?", text)
    if m2:
        val = int(float(m2.group(1)) * 1000)
        if 4000 <= val <= 250000:
            return val
    return None


def extract_cabin(text):
    low = text.lower()
    if any(k in low for k in ["first class", "ana f ", "jal f ", "the suite", "la premiere", "lufthansa first", "first (f)"]):
        return "First"
    if any(k in low for k in ["business", "qsuite", "polaris", "upper class", "mint", "delta one", "club suite", "the room", "lie-flat", " j "]):
        return "Business"
    if "premium economy" in low or "prem econ" in low:
        return "Premium Economy"
    if "economy" in low or "coach" in low or "main cabin" in low:
        return "Economy"
    return "Business"


def extract_program_and_airline(text):
    low = text.lower()
    mapping = [
        ("iberia", "Iberia Plus", "Iberia"),
        ("flying blue", "Air France/KLM Flying Blue", "Air France / KLM"),
        ("air france", "Air France/KLM Flying Blue", "Air France"),
        ("klm", "Air France/KLM Flying Blue", "KLM"),
        ("virgin atlantic", "Virgin Atlantic", "Virgin Atlantic"),
        ("aeroplan", "Air Canada Aeroplan", "Air Canada / Partners"),
        ("qsuite", "Qatar Privilege Club", "Qatar Airways"),
        ("qatar", "Qatar Privilege Club", "Qatar Airways"),
        ("avios", "British Airways Avios", "British Airways / Iberia"),
        ("british", "British Airways Avios", "British Airways"),
        ("ana", "Virgin Atlantic", "ANA"),
        ("jal", "American AAdvantage", "Japan Airlines"),
        ("japan airlines", "American AAdvantage", "Japan Airlines"),
        ("delta", "Delta SkyMiles", "Delta Air Lines"),
        ("southwest", "Southwest Rapid Rewards", "Southwest Airlines"),
        ("united", "United MileagePlus", "United Airlines"),
        ("american", "American AAdvantage", "American Airlines"),
        ("aadvantage", "American AAdvantage", "American Airlines"),
        ("emirates", "Emirates Skywards", "Emirates"),
        ("singapore", "Singapore KrisFlyer", "Singapore Airlines"),
    ]
    for key, prog, airline in mapping:
        if key in low:
            return prog, airline
    return "Partner Award Program", "Major Alliance Carrier"


def extract_route(text):
    m = re.search(r"\b([A-Z]{3})\s*(?:->|→|–|-|to|/)\s*([A-Z]{3})\b", text)
    if m:
        o, d = m.group(1), m.group(2)
        if o in AIRPORT_NAMES and d in AIRPORT_NAMES and o != d:
            return o, d
    low = text.lower()
    found = []
    for key, code in CITY_TO_IATA.items():
        idx = low.find(key)
        if idx != -1 and code not in [c for _, c in found]:
            found.append((idx, code))
    found.sort(key=lambda x: x[0])
    if len(found) >= 2:
        return found[0][1], found[1][1]
    if len(found) == 1:
        code = found[0][1]
        if code in REGION_MAP["US"]:
            return code, "EUR"
        return "DFW", code
    return "DFW", "EUR"


def compute_deal_grade(cpp, cabin, miles, price_level="typical"):
    """
    1-5 Deal Grading Scale:
      5: Unicorn deal (<1x/year, stupid not to take it)
      4: Tremendous award value (>4.0 CPP Business/First, >=2.0 CPP Economy)
      3: Solid above-average deal
      2: Equivalent to Google Flights "Low" unusual price
      1: Average deal found any time of year
    """
    is_premium = cabin in ("Business", "First")
    is_low = (price_level == "low")
    if is_premium:
        if cpp >= 6.5 or (cabin == "First" and miles <= 85000 and cpp >= 6.0) or (miles <= 50000 and cpp >= 5.0):
            return 5, "Unicorn (<1x/yr)"
        if cpp > 4.0:
            return 4, "Tremendous Value (>4 CPP)"
        if cpp >= 2.5 or (is_low and cpp >= 2.0):
            return 3, "Solid Saver Deal"
        if is_low or cpp >= 1.5:
            return 2, "Google Flights Low"
        return 1, "Average Year-Round"
    else:
        if cpp >= 3.0 or (miles <= 15000 and cpp >= 2.5):
            return 5, "Unicorn (<1x/yr)"
        if cpp >= 2.0:
            return 4, "Tremendous Value"
        if cpp >= 1.5 or (is_low and cpp >= 1.3):
            return 3, "Solid Saver Deal"
        if is_low or cpp >= 1.2:
            return 2, "Google Flights Low"
        return 1, "Average Year-Round"


def build_links(origin, dest, source_url):
    o = origin if len(origin) == 3 else "DFW"
    d = dest if len(dest) == 3 else "CDG"
    return {
        "book_url": source_url,
        "pointsyeah_url": "https://www.pointsyeah.com/",
        "seatsaero_url": f"https://seats.aero/search?origins={o}&destinations={d}",
        "google_flights_url": f"https://www.google.com/travel/flights?q={quote_plus(f'Flights to {d} from {o}')}",
    }


def make_deal(title, source, url, origin=None, dest=None, cabin=None, miles=None, taxes=195,
              dates="Select Dates (Next 90 Days)", program=None, airline=None, flights_taken=None):
    o, d = (origin, dest) if (origin and dest) else extract_route(title)
    cab = cabin or extract_cabin(title)
    pts = miles or extract_miles(title) or (55000 if cab == "Business" else 75000 if cab == "First" else 18000)
    prog, air = (program, airline) if (program and airline) else extract_program_and_airline(title)
    currencies = PROGRAM_CURRENCIES.get(prog, ["Chase UR", "American AAdvantage", "Delta SkyMiles"])
    links = build_links(o, d, url)
    uid = hashlib.md5(f"{o}-{d}-{cab}-{pts}-{title[:45]}".encode("utf-8")).hexdigest()[:12]

    return {
        "id": uid,
        "title": title.strip()[:140],
        "itinerary_name": f"{o} → {d}",
        "origin": o,
        "origin_name": AIRPORT_NAMES.get(o, o),
        "destination": d,
        "destination_name": AIRPORT_NAMES.get(d, d),
        "airline": air,
        "flights_taken": flights_taken or f"{air} ({o} → {d})",
        "program": prog,
        "cabin": cab,
        "miles": pts,
        "taxes": taxes,
        "cash_benchmark": None,
        "price_level": "typical",
        "cpp": None,
        "grade": 1,
        "grade_label": "Average Year-Round",
        "verified": False,
        "dates": dates,
        "transfer_currencies": currencies,
        "source": source,
        "url": url,
        **links,
        "matched_search": False,
        "is_unicorn_dfw_dal": False,
        "found_at": datetime.now(timezone.utc).isoformat(),
    }


def layer1_pointsyeah_and_promos():
    deals = []
    try:
        resp = requests.get("https://www.pointsyeah.com/insider/deals", headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            seen = set()
            for a in soup.find_all("a", href=True):
                txt = " ".join(a.get_text(" ", strip=True).split())
                if len(txt) < 22 or txt in seen:
                    continue
                if any(k in txt.lower() for k in AWARD_KEYWORDS):
                    seen.add(txt)
                    href = a["href"]
                    full_url = href if href.startswith("http") else f"https://www.pointsyeah.com{href}"
                    deals.append(make_deal(txt, "PointsYeah Deals", full_url))
                    if len(deals) >= 8:
                        break
        print(f"[Layer 1A] PointsYeah scraped: {len(deals)} deals")
    except Exception as e:
        print(f"[Layer 1A] PointsYeah skipped: {e}")

    try:
        resp = requests.get("https://www.flyingblue.us/en/spend/flights/promo-rewards", headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code == 200 and "promo rewards" in resp.text.lower():
            deals.append(make_deal(
                title="Flying Blue Monthly Promo Rewards — 25% Off Europe Awards (DFW to CDG/AMS)",
                source="Flying Blue Promo",
                url="https://www.flyingblue.us/en/spend/flights/promo-rewards",
                origin="DFW", dest="CDG", cabin="Business", miles=45000, taxes=215,
                dates="Book this month • Travel next 5 months",
                program="Air France/KLM Flying Blue", airline="Air France / KLM",
                flights_taken="AF 159 (Nonstop DFW → CDG)"
            ))
    except Exception as e:
        print(f"[Layer 1B] Flying Blue skipped: {e}")
    return deals


def layer2_rss_feeds():
    deals = []
    for source_name, feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=BROWSER_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            parsed = feedparser.parse(resp.content)
            count = 0
            for entry in parsed.entries[:15]:
                title = entry.get("title", "")
                summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
                combined = f"{title} {summary[:300]}"
                if any(k in combined.lower() for k in AWARD_KEYWORDS):
                    link = entry.get("link", feed_url)
                    deals.append(make_deal(title, f"{source_name} RSS", link))
                    count += 1
                    if count >= 4:
                        break
            print(f"[Layer 2] {source_name}: {count} matching posts")
        except Exception as e:
            print(f"[Layer 2] {source_name} error: {e}")
    return deals


def layer3_reddit_awardtravel():
    deals = []
    endpoints = [
        "https://www.reddit.com/r/awardtravel/new.json?limit=30",
        "https://www.reddit.com/r/awardtravel/hot.json?limit=20",
    ]
    seen_ids = set()
    for url in endpoints:
        try:
            resp = requests.get(url, headers=REDDIT_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            children = resp.json().get("data", {}).get("children", [])
            for item in children:
                post = item.get("data", {})
                pid = post.get("id")
                if not pid or pid in seen_ids:
                    continue
                title = post.get("title", "")
                selftext = post.get("selftext", "")[:350]
                combined = f"{title} {selftext}"
                if title.strip().endswith("?") and "availability" not in combined.lower() and "alert" not in combined.lower():
                    continue
                miles = extract_miles(combined)
                has_deal_signal = any(k in combined.lower() for k in [
                    "availability", "wide open", "alert", "drop", "release",
                    "booked", "promo", "sweet spot", "qsuite", "ana ", "jal ", "flying blue", "iberia", "aadvantage"
                ])
                if miles or has_deal_signal:
                    seen_ids.add(pid)
                    permalink = f"https://www.reddit.com{post.get('permalink', '')}"
                    deals.append(make_deal(title, "r/awardtravel", permalink, miles=miles))
        except Exception as e:
            print(f"[Layer 3] Reddit error ({url}): {e}")
    print(f"[Layer 3] r/awardtravel extracted: {len(deals)} deals")
    return deals


def layer4_seats_aero(searches):
    api_key = os.environ.get("SEATS_AERO_API_KEY", "").strip()
    if not api_key:
        print("[Layer 4] SEATS_AERO_API_KEY not set — skipping optional API hook.")
        return []
    deals = []
    headers = {"Partner-Authorization": api_key, "Accept": "application/json"}
    for cfg in searches:
        if not cfg.get("active", True):
            continue
        origins = [x.strip().upper() for x in cfg.get("origin", "").split(",") if len(x.strip()) == 3]
        dests = [x.strip().upper() for x in cfg.get("destination", "").split(",") if len(x.strip()) == 3]
        if not origins or not dests:
            continue
        try:
            params = {"origin_airport": ",".join(origins[:3]), "destination_airport": ",".join(dests[:3]), "take": 10}
            resp = requests.get("https://seats.aero/partnerapi/search", headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                for row in resp.json().get("data", [])[:5]:
                    route = row.get("Route", {})
                    o = route.get("OriginAirport", origins[0])
                    d = route.get("DestinationAirport", dests[0])
                    for cab, avail_key, cost_key, air_key in [
                        ("First", "FAvailable", "FMileageCost", "FAirlines"),
                        ("Business", "JAvailable", "JMileageCost", "JAirlines"),
                        ("Economy", "YAvailable", "YMileageCost", "YAirlines"),
                    ]:
                        if row.get(avail_key) and row.get(cost_key):
                            src_prog = route.get("Source", "Seats.aero Partner")
                            deals.append(make_deal(
                                title=f"{o} to {d} {cab} Award via {src_prog}",
                                source="Seats.aero Live",
                                url=f"https://seats.aero/search?origins={o}&destinations={d}",
                                origin=o, dest=d, cabin=cab, miles=int(row[cost_key]),
                                dates=row.get("Date", "Next 60 Days"),
                                program=src_prog, airline=row.get(air_key) or src_prog,
                                flights_taken=f"{row.get(air_key) or src_prog} • {o} → {d}"
                            ))
        except Exception as e:
            print(f"[Layer 4] Seats.aero error: {e}")
    return deals


def verify_cash_price(origin, dest, cabin):
    if HAS_FAST_FLIGHTS and len(origin) == 3 and len(dest) == 3:
        try:
            target_date = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
            seat_map = {"First": "first", "Business": "business", "Premium Economy": "premium-economy", "Economy": "economy"}
            result = get_flights(
                flight_data=[FlightData(date=target_date, from_airport=origin, to_airport=dest)],
                trip="one-way",
                seat=seat_map.get(cabin, "business"),
                passengers=Passengers(adults=1),
                fetch_mode="fallback",
            )
            price_level = str(getattr(result, "current_price", "typical") or "typical").lower()
            if price_level not in ("low", "typical", "high"):
                price_level = "typical"
            prices = []
            first_flight_desc = None
            for fl in getattr(result, "flights", [])[:5]:
                raw = str(getattr(fl, "price", ""))
                digits = re.sub(r"[^\d]", "", raw)
                if digits:
                    val = int(digits)
                    if 80 <= val <= 25000:
                        prices.append(val)
                        if not first_flight_desc:
                            fname = getattr(fl, "name", "")
                            dur = getattr(fl, "duration", "")
                            stops = getattr(fl, "stops", "")
                            first_flight_desc = f"{fname} • {dur} ({stops} stops)".strip()
            if prices:
                return min(prices), True, price_level, first_flight_desc
        except Exception:
            pass
    return HEURISTIC_CASH.get(cabin, 3200), False, "typical", None


def match_itinerary(deal, searches):
    for cfg in searches:
        if not cfg.get("active", True):
            continue
        origins = [x.strip().upper() for x in cfg.get("origin", "").split(",") if x.strip()]
        dests = [x.strip().upper() for x in cfg.get("destination", "").split(",") if x.strip()]
        expanded_dests = set()
        for d in dests:
            if d in REGION_MAP:
                expanded_dests.update(REGION_MAP[d])
            else:
                expanded_dests.add(d)
        origin_ok = ("ANY" in origins) or (deal["origin"] in origins)
        dest_ok = ("ANY" in expanded_dests) or (deal["destination"] in expanded_dests)
        cabin_ok = (cfg.get("cabin", "Any") == "Any") or (cfg.get("cabin", "").lower() in deal["cabin"].lower())
        pts_ok = deal["miles"] <= int(cfg.get("max_points", 150000))
        if origin_ok and dest_ok and cabin_ok and pts_ok:
            return True, cfg.get("name") or f"{cfg.get('origin')} → {cfg.get('destination')}"
    return False, None


def send_notifications(alert_deals, unicorns):
    if not alert_deals:
        return
    summary_lines = [
        f"• [Grade {d['grade']}/5{' 🦄 DFW/DAL UNICORN' if d.get('is_unicorn_dfw_dal') else ''}] {d['origin']}→{d['destination']} ({d['cabin']}): {d['miles']:,} pts + ${d['taxes']} ({d['cpp']}¢ CPP) via {d['program']} — {d['url']}"
        for d in alert_deals[:5]
    ]
    body = "\n".join(summary_lines)
    title = f"AwardRadar: {len(unicorns)} DFW/DAL Unicorn(s) & {len(alert_deals)} Graded Alerts" if unicorns else f"AwardRadar: {len(alert_deals)} Graded Flight Alerts"

    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()
    if ntfy_topic:
        try:
            requests.post(
                f"https://ntfy.sh/{ntfy_topic}",
                data=body.encode("utf-8"),
                headers={"Title": title, "Priority": "urgent" if unicorns else "high", "Tags": "airplane,star"},
                timeout=10,
            )
            print("[Alerts] Sent ntfy.sh push notification.")
        except Exception as e:
            print(f"[Alerts] ntfy.sh failed: {e}")

    discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if discord_url:
        try:
            requests.post(discord_url, json={"content": f"**{title}**\n{body}"}, timeout=10)
            print("[Alerts] Sent Discord webhook notification.")
        except Exception as e:
            print(f"[Alerts] Discord failed: {e}")

    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASS", "").strip()
    alert_email = os.environ.get("ALERT_EMAIL", "").strip()
    if smtp_user and smtp_pass and alert_email:
        try:
            msg = MIMEText(body)
            msg["Subject"] = title
            msg["From"] = smtp_user
            msg["To"] = alert_email
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [alert_email], msg.as_string())
            print("[Alerts] Sent Gmail SMTP email alert.")
        except Exception as e:
            print(f"[Alerts] SMTP failed: {e}")


def main():
    print("=== AwardRadar Zero-Cost Waterfall Engine Starting ===")
    cfg = load_config()
    searches = cfg.get("searches", [])
    alert_cfg = cfg.get("alert_config", {}) or {}
    min_grade = int(alert_cfg.get("min_grade", 4))
    alert_dfw_dal_unicorns = bool(alert_cfg.get("alert_dfw_dal_unicorns", True))
    print(f"Loaded {len(searches)} itineraries (cap={cfg.get('max_itineraries', 4)}), min_grade={min_grade}, dfw_dal_unicorns={alert_dfw_dal_unicorns}")

    raw_deals = []
    raw_deals.extend(layer1_pointsyeah_and_promos())
    raw_deals.extend(layer2_rss_feeds())
    raw_deals.extend(layer3_reddit_awardtravel())
    raw_deals.extend(layer4_seats_aero(searches))

    deduped = {}
    for d in raw_deals:
        key = f"{d['origin']}-{d['destination']}-{d['cabin']}-{d['title'][:38].lower()}"
        if key not in deduped:
            deduped[key] = d

    enriched = []
    fast_flights_calls = 0
    for d in deduped.values():
        matched, itin_name = match_itinerary(d, searches)
        d["matched_search"] = matched
        if itin_name:
            d["itinerary_name"] = itin_name

        if fast_flights_calls < 8 and len(d["origin"]) == 3 and len(d["destination"]) == 3:
            cash, is_verified, price_level, flight_desc = verify_cash_price(d["origin"], d["destination"], d["cabin"])
            fast_flights_calls += 1
        else:
            cash, is_verified, price_level, flight_desc = HEURISTIC_CASH.get(d["cabin"], 3200), False, "typical", None

        d["cash_benchmark"] = cash
        d["verified"] = is_verified
        d["price_level"] = price_level
        if flight_desc:
            d["flights_taken"] = flight_desc
        d["cpp"] = round(max(0.5, ((cash - d["taxes"]) / max(d["miles"], 1000)) * 100), 2)
        grade, grade_label = compute_deal_grade(d["cpp"], d["cabin"], d["miles"], price_level)
        d["grade"] = grade
        d["grade_label"] = grade_label
        d["is_unicorn_dfw_dal"] = (grade == 5 and d["origin"] in ("DFW", "DAL") and not matched)
        if d["is_unicorn_dfw_dal"]:
            d["itinerary_name"] = f"DFW/DAL Unicorn ({d['origin']} → {d['destination']})"
        enriched.append(d)

    # Sort by Grade (5 -> 1), then Business/First priority, then CPP descending
    enriched.sort(
        key=lambda d: (d["grade"], CABIN_PRIORITY.get(d["cabin"], 1), d["cpp"]),
        reverse=True,
    )
    final_deals = enriched[:30]

    os.makedirs(os.path.dirname(DEALS_FILE), exist_ok=True)
    with open(DEALS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_count": len(final_deals),
            "deals": final_deals,
        }, f, indent=2)

    alert_deals = []
    unicorns = []
    for d in final_deals:
        if alert_dfw_dal_unicorns and d["grade"] == 5 and d["origin"] in ("DFW", "DAL"):
            alert_deals.append(d)
            unicorns.append(d)
        elif d["matched_search"] and d["grade"] >= min_grade:
            alert_deals.append(d)

    print(f"=== Saved {len(final_deals)} flights ({len(alert_deals)} alert-worthy, {len(unicorns)} DFW/DAL unicorns) ===")
    send_notifications(alert_deals, unicorns)


if __name__ == "__main__":
    main()
