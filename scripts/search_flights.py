#!/usr/bin/env python3
"""
AwardRadar — 5-Layer Zero-Cost Award Flight Waterfall Engine
Runs daily via GitHub Actions (or on-demand via workflow_dispatch).

Layers:
  1. PointsYeah Insider Deals & Flying Blue Promo Rewards scrapers
  2. Curated Award Travel RSS Feeds (Frequent Miler, OMAAT, VFTW, Thrifty Traveler, DoC)
  3. Real-time r/awardtravel JSON feed parser (new + hot)
  4. Optional Seats.aero Partner API hook (activated if SEATS_AERO_API_KEY is set)
  5. Live Cash-Price Benchmarking via fast-flights (Google Flights Protobuf) + Scoring & Alerts
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

# Optional fast-flights cash benchmarking
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
    "User-Agent": "AwardRadarBot/2.0 (by /u/awardradar_app; zero-cost personal tracker)"
}

# Common city/country -> IATA mapping for text parsing
CITY_TO_IATA = {
    "dallas": "DFW", "dfw": "DFW", "austin": "AUS", "houston": "IAH",
    "new york": "JFK", "nyc": "JFK", "jfk": "JFK", "ewr": "EWR",
    "chicago": "ORD", "ord": "ORD", "san francisco": "SFO", "sfo": "SFO",
    "los angeles": "LAX", "lax": "LAX", "miami": "MIA", "mia": "MIA",
    "seattle": "SEA", "boston": "BOS", "atlanta": "ATL", "washington": "IAD",
    "zurich": "ZRH", "milan": "MXP", "rome": "FCO", "paris": "CDG",
    "london": "LHR", "amsterdam": "AMS", "frankfurt": "FRA", "munich": "MUC",
    "madrid": "MAD", "barcelona": "BCN", "lisbon": "LIS", "dublin": "DUB",
    "tokyo": "HND", "haneda": "HND", "narita": "NRT", "osaka": "KIX",
    "seoul": "ICN", "singapore": "SIN", "doha": "DOH", "dubai": "DXB",
    "sydney": "SYD", "auckland": "AKL", "cancun": "CUN", "honolulu": "HNL",
}

REGION_MAP = {
    "EUR": {"ZRH", "MXP", "FCO", "CDG", "LHR", "AMS", "FRA", "MUC", "MAD", "BCN", "LIS", "DUB", "VIE", "CPH", "ARN", "ATH"},
    "TYO": {"HND", "NRT"},
    "ASIA": {"HND", "NRT", "KIX", "ICN", "SIN", "HKG", "TPE", "BKK"},
    "US": {"DFW", "AUS", "IAH", "JFK", "EWR", "ORD", "SFO", "LAX", "MIA", "SEA", "BOS", "ATL", "IAD", "DEN"},
}

AIRPORT_NAMES = {
    "DFW": "Dallas/Fort Worth", "AUS": "Austin", "IAH": "Houston",
    "JFK": "New York JFK", "EWR": "Newark", "ORD": "Chicago O'Hare",
    "SFO": "San Francisco", "LAX": "Los Angeles", "MIA": "Miami",
    "SEA": "Seattle", "BOS": "Boston", "ATL": "Atlanta", "IAD": "Washington Dulles",
    "ZRH": "Zurich", "MXP": "Milan Malpensa", "FCO": "Rome",
    "CDG": "Paris CDG", "LHR": "London Heathrow", "AMS": "Amsterdam",
    "FRA": "Frankfurt", "MUC": "Munich", "MAD": "Madrid", "BCN": "Barcelona",
    "LIS": "Lisbon", "DUB": "Dublin", "HND": "Tokyo Haneda", "NRT": "Tokyo Narita",
    "ICN": "Seoul Incheon", "SIN": "Singapore", "DOH": "Doha", "DXB": "Dubai",
    "SYD": "Sydney",
}

PROGRAM_CURRENCIES = {
    "Flying Blue": ["Chase UR", "Amex MR", "Capital One", "Citi TYP", "Bilt"],
    "Air France/KLM Flying Blue": ["Chase UR", "Amex MR", "Capital One", "Citi TYP", "Bilt"],
    "Virgin Atlantic": ["Chase UR", "Amex MR", "Capital One", "Citi TYP", "Bilt"],
    "Air Canada Aeroplan": ["Chase UR", "Amex MR", "Capital One", "Bilt"],
    "British Airways Avios": ["Chase UR", "Amex MR", "Capital One", "Bilt"],
    "Qatar Privilege Club": ["Chase UR", "Amex MR", "Citi TYP", "Bilt"],
    "United MileagePlus": ["Chase UR", "Bilt"],
    "American AAdvantage": ["Bilt"],
    "ANA Mileage Club": ["Amex MR"],
    "Singapore KrisFlyer": ["Chase UR", "Amex MR", "Capital One", "Citi TYP"],
    "Emirates Skywards": ["Chase UR", "Amex MR", "Capital One", "Citi TYP", "Bilt"],
    "Avianca LifeMiles": ["Amex MR", "Capital One", "Citi TYP", "Bilt"],
    "Turkish Miles&Smiles": ["Capital One", "Citi TYP", "Bilt"],
}

AWARD_KEYWORDS = [
    "miles", "points", "award", "business class", "first class", "lie-flat",
    "promo rewards", "transfer bonus", "sweet spot", "avios", "flying blue",
    "aeroplan", "qsuites", "ana ", "jal ", "virgin atlantic", "lifemiles",
    "redemption", "availability", "saver", "wide open", "partner space"
]

RSS_FEEDS = [
    ("Frequent Miler", "https://frequentmiler.com/feed/"),
    ("One Mile at a Time", "https://onemileatatime.com/feed/"),
    ("View from the Wing", "https://viewfromthewing.com/feed/"),
    ("Thrifty Traveler", "https://thriftytraveler.com/feed/"),
    ("Doctor of Credit", "https://www.doctorofcredit.com/feed/"),
]

# Heuristic cash benchmarks when Google Flights rate-limits datacenter IPs
HEURISTIC_CASH = {
    "First": 8500,
    "Business": 3600,
    "Premium Economy": 1650,
    "Economy": 750,
}


def load_searches():
    if os.path.exists(SEARCHES_FILE):
        try:
            with open(SEARCHES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[WARN] Could not parse {SEARCHES_FILE}: {e}")
    return []


def extract_miles(text):
    """Extract one-way points/miles cost from free text."""
    # Pattern 1: 55,000 miles/pts
    m = re.search(r"\b(\d{2,3}),(\d{3})\s*(?:k\s*)?(?:miles|points|pts|avios)\b", text, re.I)
    if m:
        val = int(m.group(1) + m.group(2))
        if 5000 <= val <= 300000:
            return val
    # Pattern 2: 55k or 57.5k
    m2 = re.search(r"\b(\d{2,3}(?:\.\d)?)\s*[kK]\b(?:\s*(?:miles|points|pts|avios|each|ow|one[- ]way))?", text)
    if m2:
        val = int(float(m2.group(1)) * 1000)
        if 7500 <= val <= 250000:
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
        ("flying blue", "Air France/KLM Flying Blue", "Air France / KLM"),
        ("air france", "Air France/KLM Flying Blue", "Air France"),
        ("klm", "Air France/KLM Flying Blue", "KLM"),
        ("virgin atlantic", "Virgin Atlantic", "Virgin Atlantic"),
        ("aeroplan", "Air Canada Aeroplan", "Air Canada / Partners"),
        ("qsuite", "Qatar Privilege Club", "Qatar Airways"),
        ("qatar", "Qatar Privilege Club", "Qatar Airways"),
        ("avios", "British Airways Avios", "British Airways / Iberia"),
        ("iberia", "British Airways Avios", "Iberia"),
        ("ana", "Virgin Atlantic", "ANA"),
        ("jal", "American AAdvantage", "Japan Airlines"),
        ("japan airlines", "American AAdvantage", "Japan Airlines"),
        ("united", "United MileagePlus", "United / Star Alliance"),
        ("american", "American AAdvantage", "American / Oneworld"),
        ("emirates", "Emirates Skywards", "Emirates"),
        ("singapore", "Singapore KrisFlyer", "Singapore Airlines"),
        ("lifemiles", "Avianca LifeMiles", "Star Alliance"),
        ("swiss", "Air Canada Aeroplan", "SWISS"),
        ("lufthansa", "Air Canada Aeroplan", "Lufthansa"),
    ]
    for key, prog, airline in mapping:
        if key in low:
            return prog, airline
    return "Partner Award Program", "Major Alliance Carrier"


def extract_route(text):
    """Extract (origin, destination) IATA pair from text."""
    # Direct route pattern: DFW-ZRH, DFW -> ZRH, DFW to ZRH
    m = re.search(r"\b([A-Z]{3})\s*(?:->|→|–|-|to|/)\s*([A-Z]{3})\b", text)
    if m:
        o, d = m.group(1), m.group(2)
        if o in AIRPORT_NAMES and d in AIRPORT_NAMES and o != d:
            return o, d

    # Fallback: scan known cities/IATA in order of appearance
    low = text.lower()
    found = []
    for key, code in CITY_TO_IATA.items():
        idx = low.find(key)
        if idx != -1:
            if code not in [c for _, c in found]:
                found.append((idx, code))
    found.sort(key=lambda x: x[0])
    if len(found) >= 2:
        return found[0][1], found[1][1]
    if len(found) == 1:
        code = found[0][1]
        if code in REGION_MAP["US"]:
            return code, "EUR"
        return "US", code
    return "US", "EUR"


def build_links(origin, dest, source_url):
    o = origin if len(origin) == 3 else "DFW"
    d = dest if len(dest) == 3 else "CDG"
    return {
        "book_url": source_url,
        "pointsyeah_url": f"https://www.pointsyeah.com/",
        "seatsaero_url": f"https://seats.aero/search?origins={o}&destinations={d}",
        "google_flights_url": f"https://www.google.com/travel/flights?q={quote_plus(f'Flights to {d} from {o}')}",
    }


def make_deal(title, source, url, origin=None, dest=None, cabin=None, miles=None, taxes=195, dates="Select Dates (Next 90 Days)", program=None, airline=None):
    o, d = (origin, dest) if (origin and dest) else extract_route(title)
    cab = cabin or extract_cabin(title)
    pts = miles or extract_miles(title) or (55000 if cab == "Business" else 75000 if cab == "First" else 22000)
    prog, air = (program, airline) if (program and airline) else extract_program_and_airline(title)
    currencies = PROGRAM_CURRENCIES.get(prog, ["Chase UR", "Amex MR", "Capital One"])
    links = build_links(o, d, url)
    uid = hashlib.md5(f"{o}-{d}-{cab}-{pts}-{title[:45]}".encode("utf-8")).hexdigest()[:12]

    return {
        "id": uid,
        "title": title.strip()[:140],
        "origin": o,
        "origin_name": AIRPORT_NAMES.get(o, o),
        "destination": d,
        "destination_name": AIRPORT_NAMES.get(d, d),
        "airline": air,
        "program": prog,
        "cabin": cab,
        "miles": pts,
        "taxes": taxes,
        "cash_benchmark": None,
        "cpp": None,
        "verified": False,
        "dates": dates,
        "transfer_currencies": currencies,
        "source": source,
        "url": url,
        **links,
        "matched_search": False,
        "found_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# LAYER 1: PointsYeah Insider Deals & Flying Blue Promo Scraper
# ---------------------------------------------------------------------------
def layer1_pointsyeah_and_promos():
    deals = []
    # 1A: PointsYeah Insider Deals
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

    # 1B: Flying Blue Promo Rewards page
    try:
        resp = requests.get("https://www.flyingblue.us/en/spend/flights/promo-rewards", headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(" ", strip=True)
            if "promo rewards" in text.lower():
                deals.append(make_deal(
                    title="Flying Blue Monthly Promo Rewards — 25% Off Europe Awards (US to CDG/AMS)",
                    source="Flying Blue Promo",
                    url="https://www.flyingblue.us/en/spend/flights/promo-rewards",
                    origin="ORD", dest="CDG", cabin="Business", miles=45000, taxes=215,
                    dates="Book this month • Travel next 5 months",
                    program="Air France/KLM Flying Blue", airline="Air France / KLM"
                ))
    except Exception as e:
        print(f"[Layer 1B] Flying Blue skipped: {e}")

    return deals


# ---------------------------------------------------------------------------
# LAYER 2: Curated Award Travel RSS Feeds
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# LAYER 3: r/awardtravel Live JSON Parser
# ---------------------------------------------------------------------------
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
                # Filter out pure beginner questions without deals/availability
                if title.strip().endswith("?") and "availability" not in combined.lower() and "alert" not in combined.lower():
                    continue
                miles = extract_miles(combined)
                has_deal_signal = any(k in combined.lower() for k in [
                    "availability", "wide open", "alert", "drop", "release",
                    "booked", "promo", "sweet spot", "qsuite", "ana ", "jal ", "flying blue"
                ])
                if miles or has_deal_signal:
                    seen_ids.add(pid)
                    permalink = f"https://www.reddit.com{post.get('permalink', '')}"
                    deals.append(make_deal(title, "r/awardtravel", permalink, miles=miles))
        except Exception as e:
            print(f"[Layer 3] Reddit error ({url}): {e}")
    print(f"[Layer 3] r/awardtravel extracted: {len(deals)} deals")
    return deals


# ---------------------------------------------------------------------------
# LAYER 4: Optional Seats.aero Partner API Hook
# ---------------------------------------------------------------------------
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
            params = {
                "origin_airport": ",".join(origins[:3]),
                "destination_airport": ",".join(dests[:3]),
                "take": 10,
            }
            resp = requests.get("https://seats.aero/partnerapi/search", headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                for row in resp.json().get("data", [])[:5]:
                    route = row.get("Route", {})
                    o = route.get("OriginAirport", origins[0])
                    d = route.get("DestinationAirport", dests[0])
                    miles = row.get("JMileageCost") or row.get("FMileageCost") or row.get("YMileageCost")
                    if miles:
                        miles = int(miles)
                        cabin = "Business" if row.get("JAvailable") else ("First" if row.get("FAvailable") else "Economy")
                        src_prog = route.get("Source", "Seats.aero Partner")
                        deals.append(make_deal(
                            title=f"{o} to {d} {cabin} Award via {src_prog}",
                            source="Seats.aero Live",
                            url=f"https://seats.aero/search?origins={o}&destinations={d}",
                            origin=o, dest=d, cabin=cabin, miles=miles,
                            dates=row.get("Date", "Next 60 Days"),
                            program=src_prog, airline=row.get("JAirlines") or src_prog
                        ))
        except Exception as e:
            print(f"[Layer 4] Seats.aero error: {e}")
    print(f"[Layer 4] Seats.aero returned: {len(deals)} deals")
    return deals


# ---------------------------------------------------------------------------
# LAYER 5: Live Cash-Price Verification (fast-flights) & Watchlist Scoring
# ---------------------------------------------------------------------------
def verify_cash_price(origin, dest, cabin):
    """Query live cash price via fast-flights (Google Flights Protobuf) with safe fallback."""
    if HAS_FAST_FLIGHTS and len(origin) == 3 and len(dest) == 3:
        try:
            target_date = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
            seat_map = {
                "First": "first",
                "Business": "business",
                "Premium Economy": "premium-economy",
                "Economy": "economy",
            }
            result = get_flights(
                flight_data=[FlightData(date=target_date, from_airport=origin, to_airport=dest)],
                trip="one-way",
                seat=seat_map.get(cabin, "business"),
                passengers=Passengers(adults=1),
                fetch_mode="fallback",
            )
            prices = []
            for fl in getattr(result, "flights", [])[:5]:
                raw = str(getattr(fl, "price", ""))
                digits = re.sub(r"[^\d]", "", raw)
                if digits:
                    val = int(digits)
                    if 150 <= val <= 25000:
                        prices.append(val)
            if prices:
                return min(prices), True
        except Exception:
            pass
    return HEURISTIC_CASH.get(cabin, 3200), False


def matches_watchlist(deal, searches):
    for cfg in searches:
        if not cfg.get("active", True):
            continue
        origins = [x.strip().upper() for x in cfg.get("origin", "").split(",") if x.strip()]
        dests = [x.strip().upper() for x in cfg.get("destination", "").split(",") if x.strip()]
        # Expand regions
        expanded_dests = set()
        for d in dests:
            if d in REGION_MAP:
                expanded_dests.update(REGION_MAP[d])
            else:
                expanded_dests.add(d)

        origin_ok = ("ANY" in origins) or (deal["origin"] in origins) or (deal["origin"] == "US")
        dest_ok = ("ANY" in expanded_dests) or (deal["destination"] in expanded_dests)
        cabin_ok = (cfg.get("cabin", "Any") == "Any") or (cfg.get("cabin", "").lower() in deal["cabin"].lower())
        pts_ok = deal["miles"] <= int(cfg.get("max_points", 150000))
        if origin_ok and dest_ok and cabin_ok and pts_ok:
            return True
    return False


def send_notifications(top_deals, matched_deals):
    alert_list = matched_deals[:3] if matched_deals else top_deals[:3]
    if not alert_list:
        return

    summary_lines = [
        f"• {d['origin']}→{d['destination']} ({d['cabin']}): {d['miles']:,} pts + ${d['taxes']} (~{d['cpp']} CPP) via {d['program']} — {d['url']}"
        for d in alert_list
    ]
    body = "\n".join(summary_lines)
    title = f"AwardRadar: {len(matched_deals)} Watchlist Matches / {len(top_deals)} Daily Deals"

    # 1. ntfy.sh Instant Push
    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()
    if ntfy_topic:
        try:
            requests.post(
                f"https://ntfy.sh/{ntfy_topic}",
                data=body.encode("utf-8"),
                headers={"Title": title, "Priority": "high" if matched_deals else "default", "Tags": "airplane,sparkles"},
                timeout=10,
            )
            print("[Alerts] Sent ntfy.sh push notification.")
        except Exception as e:
            print(f"[Alerts] ntfy.sh failed: {e}")

    # 2. Discord Webhook
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if discord_url:
        try:
            payload = {"content": f"**{title}**\n{body}"}
            requests.post(discord_url, json=payload, timeout=10)
            print("[Alerts] Sent Discord webhook notification.")
        except Exception as e:
            print(f"[Alerts] Discord failed: {e}")

    # 3. Optional Gmail SMTP
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
    searches = load_searches()
    print(f"Loaded {len(searches)} watchlist configurations.")

    raw_deals = []
    raw_deals.extend(layer1_pointsyeah_and_promos())
    raw_deals.extend(layer2_rss_feeds())
    raw_deals.extend(layer3_reddit_awardtravel())
    raw_deals.extend(layer4_seats_aero(searches))

    # Deduplicate by normalized key
    deduped = {}
    for d in raw_deals:
        key = f"{d['origin']}-{d['destination']}-{d['cabin']}-{d['title'][:38].lower()}"
        if key not in deduped:
            deduped[key] = d

    ranked = []
    fast_flights_calls = 0
    for d in deduped.values():
        d["matched_search"] = matches_watchlist(d, searches)
        # Limit live fast-flights lookups to top 8 candidates to keep runtime fast
        if fast_flights_calls < 8 and len(d["origin"]) == 3 and len(d["destination"]) == 3:
            cash, is_verified = verify_cash_price(d["origin"], d["destination"], d["cabin"])
            fast_flights_calls += 1
        else:
            cash, is_verified = HEURISTIC_CASH.get(d["cabin"], 3200), False

        d["cash_benchmark"] = cash
        d["verified"] = is_verified
        d["cpp"] = round(max(0.5, ((cash - d["taxes"]) / max(d["miles"], 1000)) * 100), 2)

        score = (50 if d["matched_search"] else 0) + (20 if is_verified else 0) + min(25, d["cpp"] * 3)
        ranked.append((score, d))

    ranked.sort(key=lambda x: x[0], reverse=True)
    final_deals = [item[1] for item in ranked[:25]]

    # Preserve existing deals if a transient outage returns fewer than 5
    if len(final_deals) < 5 and os.path.exists(DEALS_FILE):
        try:
            with open(DEALS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
                old_list = existing.get("deals", []) if isinstance(existing, dict) else existing
                seen_ids = {d["id"] for d in final_deals}
                for old in old_list:
                    if old.get("id") not in seen_ids:
                        final_deals.append(old)
                    if len(final_deals) >= 8:
                        break
        except Exception:
            pass

    os.makedirs(os.path.dirname(DEALS_FILE), exist_ok=True)
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_count": len(final_deals),
        "deals": final_deals,
    }
    with open(DEALS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    matched = [d for d in final_deals if d.get("matched_search")]
    print(f"=== Saved {len(final_deals)} deals ({len(matched)} watchlist matches) to {DEALS_FILE} ===")
    send_notifications(final_deals, matched)


if __name__ == "__main__":
    main()
