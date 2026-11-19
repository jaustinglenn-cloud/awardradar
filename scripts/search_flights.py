#!/usr/bin/env python3
"""
AwardRadar — Zero-Cost Multi-Source Award & Live Itinerary Engine
Runs daily via GitHub Actions (or on-demand via workflow_dispatch).

Layers:
  0. Direct Active Itinerary Scanner: Actively queries EVERY configured itinerary in searches.json
     (Business/First prioritized + Economy) via SerpApi Google Flights (if SERPAPI_API_KEY is set)
     and/or fast-flights (free Google Flights Protobuf), mapping live flights to Chase UR,
     American AAdvantage, and Delta SkyMiles partner award charts.
  1. PointsYeah Insider Deals, Flying Blue Promo Rewards & MilesFeed Aggregator
  2. 13 Curated Award & Flight Deal RSS Feeds
  3. Real-time r/awardtravel & r/flights JSON feeds
  4. Optional Seats.aero Partner API hook (if SEATS_AERO_API_KEY is set)
  5. 1-5 Deal Grading & ntfy.sh iPhone Push Alerts (supports NTFY_TOPIC or NFTY_TOPIC)
"""

import os
import re
import json
import math
import hashlib
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
    "User-Agent": "AwardRadarBot/2.2 (by /u/awardradar_app; zero-cost personal tracker)"
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

LONG_HAUL_AIRPORTS = {
    "ZRH", "MXP", "VCE", "FCO", "CDG", "LHR", "AMS", "FRA", "MUC", "MAD", "BCN",
    "LIS", "DUB", "HND", "NRT", "KIX", "ICN", "SIN", "DOH", "DXB", "SYD", "AKL",
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
    "Chase Travel Portal": ["Chase UR"],
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
    ("Upgraded Points", "https://upgradedpoints.com/feed/"),
    ("AwardWallet Blog", "https://awardwallet.com/blog/feed/"),
    ("The Points Guy", "https://thepointsguy.com/feed/"),
    ("Miles to Memories", "https://milestomemories.com/feed/"),
    ("God Save The Points", "https://www.godsavethepoints.com/feed/"),
    ("The Flight Deal", "https://feeds.feedburner.com/TheFlightDeal"),
    ("Fly4free", "https://www.fly4free.com/feed/"),
    ("Secret Flying", "https://www.secretflying.com/feed/"),
]

HEURISTIC_CASH = {"First": 8500, "Business": 3600, "Premium Economy": 1650, "Economy": 680}
CABIN_PRIORITY = {"First": 4, "Business": 3, "Premium Economy": 2, "Economy": 1}


def load_config():
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


def extract_cabin(text, miles=None, is_long_haul=False):
    low = text.lower()
    # Check Economy / Premium Economy FIRST when explicitly mentioned alongside low miles
    has_econ = any(k in low for k in ["economy", "coach", "main cabin", "basic economy"])
    has_pe = ("premium economy" in low or "prem econ" in low)
    has_first = any(k in low for k in ["first class", "ana f ", "jal f ", "the suite", "la premiere", "lufthansa first", "first (f)"])
    has_biz = any(k in low for k in ["business class", "business", "qsuite", "polaris", "upper class", "mint", "delta one", "club suite", "the room", "lie-flat"])

    # Hard anti-hallucination sanity guard: long-haul < 35k miles is NEVER Business or First
    if is_long_haul and miles and miles < 35000:
        return "Premium Economy" if has_pe else "Economy"

    if has_pe:
        return "Premium Economy"
    if has_econ and not (has_biz or has_first):
        return "Economy"
    if has_first and (not miles or not is_long_haul or miles >= 55000):
        return "First"
    if has_biz and (not miles or not is_long_haul or miles >= 35000):
        return "Business"
    if has_econ:
        return "Economy"
    return None


def extract_taxes(text, default_tax=95):
    m = re.search(r"(?:\+|taxes?|fees?)\s*\$\s*(\d{1,4}(?:\.\d{2})?)", text, re.I)
    if m:
        val = float(m.group(1))
        if 5 <= val <= 1500:
            return round(val, 2)
    return default_tax


def extract_program_and_airline(text):
    low = text.lower()
    mapping = [
        ("iberia", "Iberia Plus", "Iberia"),
        ("flying blue", "Air France/KLM Flying Blue", "Air France / KLM"),
        ("air france", "Air France/KLM Flying Blue", "Air France"),
        ("klm", "Air France/KLM Flying Blue", "KLM"),
        ("virgin atlantic", "Virgin Atlantic", "Virgin Atlantic"),
        ("aeroplan", "Air Canada Aeroplan", "Air Canada"),
        ("qsuite", "Qatar Privilege Club", "Qatar Airways"),
        ("qatar", "Qatar Privilege Club", "Qatar Airways"),
        ("avios", "British Airways Avios", "British Airways"),
        ("british", "British Airways Avios", "British Airways"),
        ("ana", "Virgin Atlantic", "ANA"),
        ("jal", "American AAdvantage", "Japan Airlines"),
        ("japan airlines", "American AAdvantage", "Japan Airlines"),
        ("delta", "Delta SkyMiles", "Delta Air Lines"),
        ("southwest", "Southwest Rapid Rewards", "Southwest Airlines"),
        ("united", "United MileagePlus", "United Airlines"),
        ("american", "American AAdvantage", "American Airlines"),
        ("aadvantage", "American AAdvantage", "American Airlines"),
        ("lufthansa", "Air Canada Aeroplan", "Lufthansa"),
        ("swiss", "Air Canada Aeroplan", "SWISS"),
    ]
    for key, prog, airline in mapping:
        if key in low:
            return prog, airline
    return "Chase Travel Portal", "Major Carrier"


def map_carrier_award_estimate(airline_name, origin, dest, cabin, cash_price):
    is_lh = (origin in LONG_HAUL_AIRPORTS) or (dest in LONG_HAUL_AIRPORTS)
    low = (airline_name or "").lower()
    if "iberia" in low:
        pts = 50500 if (is_lh and cabin in ("Business", "First")) else (20000 if is_lh else 11000)
        return "Iberia Plus", pts, 185 if is_lh else 6
    if "british" in low:
        pts = 60000 if (is_lh and cabin in ("Business", "First")) else (22500 if is_lh else 11000)
        return "British Airways Avios", pts, 350 if is_lh else 6
    if "american" in low or "finnair" in low:
        pts = 57500 if (is_lh and cabin in ("Business", "First")) else (22500 if is_lh else 11000)
        return "American AAdvantage", pts, 120 if is_lh else 6
    if "air france" in low or "klm" in low:
        pts = 55000 if (is_lh and cabin in ("Business", "First")) else (20000 if is_lh else 12000)
        return "Air France/KLM Flying Blue", pts, 220 if is_lh else 6
    if "delta" in low:
        if is_lh:
            pts = 50000 if cabin in ("Business", "First") else 30000
            return "Virgin Atlantic", pts, 250
        return "Delta SkyMiles", 15000 if cabin == "Economy" else 35000, 6
    if "virgin" in low:
        pts = 47500 if (is_lh and cabin in ("Business", "First")) else 15000
        return "Virgin Atlantic", pts, 260 if is_lh else 6
    if "southwest" in low:
        pts = max(5500, int(cash_price / 0.014))
        return "Southwest Rapid Rewards", pts, 6
    if any(k in low for k in ("united", "lufthansa", "swiss", "air canada", "austrian")):
        pts = 70000 if (is_lh and cabin in ("Business", "First")) else (35000 if is_lh else 12500)
        return "Air Canada Aeroplan", pts, 95 if is_lh else 6
    portal_pts = max(5000, math.ceil(cash_price / 0.0125))
    return "Chase Travel Portal", portal_pts, 6


def extract_route(text):
    """Strictly requires BOTH an explicit origin and destination in the text. Never fabricates DFW."""
    m = re.search(r"\b([A-Z]{3})\s*(?:->|→|–|-|to|/)\s*([A-Z]{3})\b", text)
    if m:
        o, d = m.group(1), m.group(2)
        if o in AIRPORT_NAMES and d in AIRPORT_NAMES and o != d:
            return o, d
    low = text.lower()
    found = []
    for key, code in CITY_TO_IATA.items():
        # Word-boundary match prevents false hits like 'ord' inside 'affordable' or 'dal' inside 'modal'
        m_city = re.search(r"\b" + re.escape(key) + r"\b", low)
        if m_city and code not in [c for _, c in found]:
            found.append((m_city.start(), code))
    found.sort(key=lambda x: x[0])
    if len(found) >= 2:
        return found[0][1], found[1][1]
    return None, None


def compute_deal_grade(cpp, cabin, miles, price_level="typical"):
    is_premium = cabin in ("Business", "First")
    is_low = (price_level == "low")
    if is_premium:
        if (cabin == "First" and cpp >= 10.0) or (cabin == "Business" and (cpp >= 9.0 or (miles <= 40000 and cpp >= 7.5))):
            return 5, "Unicorn (<1x/yr)"
        if cpp > 4.0:
            return 4, "Tremendous Value (>4 CPP)"
        if cpp >= 2.5 or (is_low and cpp >= 2.0):
            return 3, "Solid Saver Deal"
        if is_low or cpp >= 1.5:
            return 2, "Google Flights Low"
        return 1, "Average Year-Round"
    else:
        if cpp >= 4.0 or (miles <= 10000 and cpp >= 3.5):
            return 5, "Unicorn (<1x/yr)"
        if cpp >= 2.0:
            return 4, "Tremendous Value"
        if cpp >= 1.5 or (is_low and cpp >= 1.3):
            return 3, "Solid Saver Deal"
        if is_low or cpp >= 1.2:
            return 2, "Google Flights Low"
        return 1, "Average Year-Round"


def build_airline_url(airline, origin, dest, travel_date):
    low = (airline or "").lower()
    o = origin if len(origin) == 3 else "DFW"
    d = dest if len(dest) == 3 else "MXP"
    dt_str = travel_date or (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    if "american" in low or "jal" in low or "japan" in low:
        return (
            f"https://www.aa.com/booking/search?locale=en_US&pax=1&adult=1&type=OneWay&searchType=Award"
            f"&slices=%5B%7B%22orig%22:%22{o}%22,%22dest%22:%22{d}%22,%22date%22:%22{dt_str}%22%7D%5D"
        )
    if "iberia" in low:
        return f"https://www.iberia.com/us/flights/{o.lower()}-{d.lower()}/"
    if "british" in low:
        return "https://www.britishairways.com/travel/redeem/execclub/_gf/en_us"
    if "air france" in low:
        return "https://wwws.airfrance.us/search/open-dates"
    if "klm" in low:
        return "https://www.klm.us/search/open-dates"
    if "delta" in low:
        return f"https://www.delta.com/flight-search/book-a-flight?origin={o}&destination={d}"
    if "virgin" in low or "ana" in low:
        return "https://flywith.virginatlantic.com/us/en/reward-flight-finder.html"
    if "southwest" in low:
        return f"https://www.southwest.com/air/booking/select.html?originationAirportCode={o}&destinationAirportCode={d}&departureDate={dt_str}&fareType=POINTS"
    if "united" in low:
        return f"https://www.united.com/en/us/fsr/choose-flights?f={o}&t={d}&d={dt_str}&tt=1&at=1&px=1&taxng=1"
    return f"https://www.google.com/travel/flights?q={quote_plus(f'Flights to {d} from {o} on {dt_str} oneway {airline}')}"


def build_links(origin, dest, source_url, cabin="Business", airline="", travel_date=""):
    o = origin if len(origin) == 3 else "DFW"
    d = dest if len(dest) == 3 else "MXP"
    dt_str = travel_date or (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    cab_q = "first class" if cabin == "First" else ("business class" if cabin == "Business" else ("premium economy" if cabin == "Premium Economy" else "economy"))
    air_q = f" {airline.split('/')[0].strip()}" if airline and "Carrier" not in airline else ""
    gf_query = f"Flights to {d} from {o} on {dt_str} oneway {cab_q}{air_q}"
    return {
        "book_url": build_airline_url(airline, o, d, dt_str),
        "airline_url": build_airline_url(airline, o, d, dt_str),
        "pointsyeah_url": "https://www.pointsyeah.com/",
        "seatsaero_url": f"https://seats.aero/search?origins={o}&destinations={d}",
        "google_flights_url": f"https://www.google.com/travel/flights?q={quote_plus(gf_query)}",
    }


def make_deal(title, source, url, origin=None, dest=None, cabin=None, miles=None, taxes=None,
              dates="Select Dates (Next 90 Days)", program=None, airline=None, flights_taken=None,
              flight_times="Check schedule", travel_date=None, cash_benchmark=None, price_level="typical",
              verified=False, matched_search=False, itinerary_name=None, parse_text=None):
    full_text = parse_text or title
    o, d = (origin, dest) if (origin and dest) else extract_route(full_text)
    if not o or not d or o == d:
        return None
    pts = miles if miles is not None else extract_miles(full_text)
    if not pts:
        return None
    is_lh = (o in LONG_HAUL_AIRPORTS) or (d in LONG_HAUL_AIRPORTS)
    cab = cabin or extract_cabin(full_text, miles=pts, is_long_haul=is_lh)
    if not cab:
        return None
    # Final long-haul cabin sanity guard
    if is_lh and pts < 35000 and cab in ("Business", "First"):
        cab = "Economy"
    tax_val = taxes if taxes is not None else extract_taxes(full_text, default_tax=150 if is_lh else 6)
    prog, air = (program, airline) if (program and airline) else extract_program_and_airline(full_text)
    currencies = PROGRAM_CURRENCIES.get(prog, ["Chase UR", "American AAdvantage", "Delta SkyMiles"])
    m_date = re.search(r"(\d{4}-\d{2}-\d{2})", str(travel_date or dates or ""))
    t_date = m_date.group(1) if m_date else (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    links = build_links(o, d, url, cabin=cab, airline=air, travel_date=t_date)
    uid = hashlib.md5(f"{o}-{d}-{cab}-{pts}-{t_date}-{air}-{title[:35]}".encode("utf-8")).hexdigest()[:12]

    return {
        "id": uid,
        "title": title.strip()[:140],
        "itinerary_name": itinerary_name or f"{o} → {d}",
        "origin": o,
        "origin_name": AIRPORT_NAMES.get(o, o),
        "destination": d,
        "destination_name": AIRPORT_NAMES.get(d, d),
        "airline": air,
        "flights_taken": flights_taken or f"{air} ({o} → {d})",
        "flight_times": flight_times or "Check schedule",
        "travel_date": t_date,
        "program": prog,
        "cabin": cab,
        "miles": pts,
        "taxes": tax_val,
        "cash_benchmark": cash_benchmark,
        "price_level": price_level,
        "cpp": None,
        "grade": 1,
        "grade_label": "Average Year-Round",
        "verified": verified,
        "dates": dates,
        "transfer_currencies": currencies,
        "source": source,
        "url": url,
        **links,
        "matched_search": matched_search,
        "is_unicorn_dfw_dal": False,
        "found_at": datetime.now(timezone.utc).isoformat(),
    }


def _query_serpapi_flights(origin, dest, travel_date, cabin, api_key):
    if not api_key:
        return []
    travel_class = 4 if cabin == "First" else (3 if cabin == "Business" else (2 if cabin == "Premium Economy" else 1))
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": travel_date,
        "type": 2,
        "travel_class": travel_class,
        "adults": 1,
        "currency": "USD",
        "hl": "en",
        "api_key": api_key,
    }
    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=25)
        data = resp.json()
        if resp.status_code != 200 or "error" in data:
            return []
        price_level = str((data.get("price_insights") or {}).get("price_level", "typical") or "typical").lower()
        if price_level not in ("low", "typical", "high"):
            price_level = "typical"
        rows = []
        for grp in ((data.get("best_flights") or []) + (data.get("other_flights") or []))[:3]:
            price = grp.get("price")
            legs = grp.get("flights") or []
            if not isinstance(price, (int, float)) or not legs:
                continue
            airlines = []
            fnums = []
            for leg in legs:
                a = str(leg.get("airline", "")).strip()
                fn = str(leg.get("flight_number", "")).strip()
                if a and a not in airlines:
                    airlines.append(a)
                if fn:
                    fnums.append(fn)
            dep_t = str((legs[0].get("departure_airport") or {}).get("time", "")).strip()
            arr_t = str((legs[-1].get("arrival_airport") or {}).get("time", "")).strip()
            mins = int(grp.get("total_duration") or 0)
            dur = f"{mins // 60}h {mins % 60}m" if mins else ""
            stops = max(0, len(legs) - 1)
            stop_lbl = "Nonstop" if stops == 0 else f"{stops} stop{'s' if stops != 1 else ''}"
            rows.append({
                "airline": " / ".join(airlines) or "Carrier",
                "flights_taken": f"{' / '.join(fnums) or 'Live Flight'} • {stop_lbl}{(', ' + dur) if dur else ''}",
                "flight_times": f"{dep_t} → {arr_t}" if dep_t else "Check schedule",
                "cash": int(price),
                "price_level": price_level,
                "source": "Google Flights (SerpApi)",
            })
        return rows
    except Exception as e:
        print(f"[Layer 0] SerpApi error {origin}->{dest}: {e}")
        return []


def _query_fast_flights(origin, dest, travel_date, cabin):
    if not HAS_FAST_FLIGHTS:
        return []
    seat_map = {"First": "first", "Business": "business", "Premium Economy": "premium-economy", "Economy": "economy"}
    try:
        res = get_flights(
            flight_data=[FlightData(date=travel_date, from_airport=origin, to_airport=dest)],
            trip="one-way",
            seat=seat_map.get(cabin, "business"),
            passengers=Passengers(adults=1),
            fetch_mode="fallback",
        )
        price_level = str(getattr(res, "current_price", "typical") or "typical").lower()
        if price_level not in ("low", "typical", "high"):
            price_level = "typical"
        rows = []
        for fl in getattr(res, "flights", [])[:3]:
            digits = re.sub(r"[^\d]", "", str(getattr(fl, "price", "")))
            if not digits:
                continue
            val = int(digits)
            if not (60 <= val <= 25000):
                continue
            airline = str(getattr(fl, "name", "Carrier") or "Carrier").strip()
            dep_t = str(getattr(fl, "departure", "") or "").strip()
            arr_t = str(getattr(fl, "arrival", "") or "").strip()
            dur = str(getattr(fl, "duration", "") or "").strip()
            stops = getattr(fl, "stops", "")
            stop_lbl = "Nonstop" if str(stops) == "0" else f"{stops} stop(s)"
            rows.append({
                "airline": airline,
                "flights_taken": f"{airline} • {stop_lbl}{(', ' + dur) if dur else ''}",
                "flight_times": f"{dep_t} → {arr_t}" if dep_t else "Check schedule",
                "cash": val,
                "price_level": price_level,
                "source": "Google Flights Live",
            })
        return rows
    except Exception as e:
        print(f"[Layer 0] fast-flights error {origin}->{dest}: {e}")
        return []


def layer0_direct_itineraries(searches):
    """Actively scans EVERY configured itinerary (Business/First + Economy) so user routes always appear."""
    deals = []
    serpapi_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    today = datetime.now(timezone.utc).date()

    for cfg in searches:
        if not cfg.get("active", True):
            continue
        itin_name = cfg.get("name") or f"{cfg.get('origin')} → {cfg.get('destination')}"
        origins = [x.strip().upper() for x in str(cfg.get("origin", "")).split(",") if len(x.strip()) == 3][:2]
        dests = [x.strip().upper() for x in str(cfg.get("destination", "")).split(",") if len(x.strip()) == 3][:2]
        if not origins or not dests:
            continue

        # Google Flights only indexes ~330 days out; clamp future dates if >325 days away
        raw_date = str(cfg.get("start_date") or "").strip()
        m_dt = re.search(r"(\d{4}-\d{2}-\d{2})", raw_date or str(cfg.get("dates") or ""))
        if m_dt:
            req_dt = datetime.strptime(m_dt.group(1), "%Y-%m-%d").date()
            if (req_dt - today).days > 325:
                query_date = (today + timedelta(days=300)).strftime("%Y-%m-%d")
                display_date = f"{m_dt.group(1)} (Benchmarked {query_date})"
            elif req_dt <= today:
                query_date = (today + timedelta(days=30)).strftime("%Y-%m-%d")
                display_date = query_date
            else:
                query_date = m_dt.group(1)
                display_date = query_date
        else:
            query_date = (today + timedelta(days=45)).strftime("%Y-%m-%d")
            display_date = str(cfg.get("dates") or query_date)

        pref_cab = cfg.get("cabin", "Any")
        cabins = ["Business", "Economy"] if pref_cab == "Any" else [pref_cab]

        for o in origins[:1]:
            for d in dests[:1]:
                for cab in cabins:
                    rows = _query_serpapi_flights(o, d, query_date, cab, serpapi_key)
                    if not rows:
                        rows = _query_fast_flights(o, d, query_date, cab)
                    for r in rows[:2]:
                        prog, est_pts, est_tax = map_carrier_award_estimate(r["airline"], o, d, cab, r["cash"])
                        gf_url = build_links(o, d, "", cabin=cab, airline=r["airline"], travel_date=query_date)["google_flights_url"]
                        deals.append(make_deal(
                            title=f"{o} → {d} {r['airline']} {cab} (Live Schedule + {prog} Partner Chart)",
                            source=r["source"],
                            url=gf_url,
                            origin=o, dest=d, cabin=cab, miles=est_pts, taxes=est_tax,
                            dates=display_date, program=prog, airline=r["airline"],
                            flights_taken=r["flights_taken"], flight_times=r["flight_times"],
                            travel_date=query_date, cash_benchmark=r["cash"],
                            price_level=r["price_level"], verified=True,
                            matched_search=True, itinerary_name=itin_name,
                        ))
    print(f"[Layer 0] Direct itinerary scans returned: {len(deals)} live flights")
    return deals


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
                    d_item = make_deal(txt, "PointsYeah Deals", full_url)
                    if d_item: deals.append(d_item)
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
                flights_taken="AF 159 (Nonstop DFW → CDG)",
            ))
    except Exception as e:
        print(f"[Layer 1B] Flying Blue skipped: {e}")

    try:
        resp = requests.get("https://milesfeed.com/", headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            seen_mf = set()
            for a in soup.find_all("a", href=True):
                txt = " ".join(a.get_text(" ", strip=True).split())
                href = a["href"]
                if len(txt) < 25 or txt in seen_mf or not href.startswith("http"):
                    continue
                if any(k in txt.lower() for k in AWARD_KEYWORDS):
                    seen_mf.add(txt)
                    d_item = make_deal(txt, "MilesFeed Aggregator", href)
                    if d_item: deals.append(d_item)
                    if len(seen_mf) >= 6:
                        break
            print(f"[Layer 1C] MilesFeed scraped: {len(seen_mf)} deals")
    except Exception as e:
        print(f"[Layer 1C] MilesFeed skipped: {e}")
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
                    d_item = make_deal(title, f"{source_name} RSS", link, parse_text=combined)
                    if d_item: deals.append(d_item)
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
        ("r/awardtravel", "https://www.reddit.com/r/awardtravel/new.json?limit=30"),
        ("r/awardtravel", "https://www.reddit.com/r/awardtravel/hot.json?limit=20"),
        ("r/flights", "https://www.reddit.com/r/flights/new.json?limit=20"),
    ]
    seen_ids = set()
    for sub_name, url in endpoints:
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
                    d_item = make_deal(title, sub_name, permalink, miles=miles, parse_text=combined)
                    if d_item: deals.append(d_item)
        except Exception as e:
            print(f"[Layer 3] Reddit error ({url}): {e}")
    print(f"[Layer 3] Reddit extracted: {len(deals)} deals")
    return deals


def layer4_seats_aero(searches):
    api_key = os.environ.get("SEATS_AERO_API_KEY", "").strip()
    if not api_key:
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
                                flights_taken=f"{row.get(air_key) or src_prog} • {o} → {d}",
                                matched_search=True, itinerary_name=cfg.get("name") or f"{o} → {d}",
                            ))
        except Exception as e:
            print(f"[Layer 4] Seats.aero error: {e}")
    return deals


def match_itinerary(deal, searches):
    if deal.get("matched_search"):
        return True, deal.get("itinerary_name")
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


def send_ntfy_notification(alert_deals, unicorns, total_count, matched_count):
    # Accept BOTH NTFY_TOPIC and NFTY_TOPIC and strip any accidental https://ntfy.sh/ prefix
    raw_topic = (os.environ.get("NTFY_TOPIC") or os.environ.get("NFTY_TOPIC") or "").strip()
    ntfy_topic = re.sub(r"^https?://ntfy\.sh/", "", raw_topic, flags=re.I).strip("/")
    if not ntfy_topic:
        print("[Alerts] No NTFY_TOPIC or NFTY_TOPIC secret configured — skipping push.")
        return

    is_manual_run = os.environ.get("GITHUB_EVENT_NAME", "") == "workflow_dispatch"
    if not alert_deals:
        if not is_manual_run:
            print("[Alerts] No deals met alert threshold today.")
            return
        title = f"AwardRadar Scan Complete ({total_count} Live Flights)"
        body = f"Scanned your itineraries ({matched_count} matches, {total_count} total live flights). No Grade 4-5 deals or DFW/DAL Unicorns crossed your alert threshold on this run."
        priority = "default"
    else:
        summary_lines = [
            f"- [Grade {d['grade']}/5{' UNICORN' if d.get('is_unicorn_dfw_dal') else ''}] {d['origin']}->{d['destination']} ({d['cabin']}): {d['miles']:,} pts + ${d['taxes']} ({d['cpp']}c CPP) | {d['flight_times']} | {d['google_flights_url']}"
            for d in alert_deals[:5]
        ]
        body = "\n".join(summary_lines)
        title = (
            f"AwardRadar: {len(unicorns)} DFW/DAL Unicorn(s) & {len(alert_deals)} Alerts"
            if unicorns else
            f"AwardRadar: {len(alert_deals)} Flight Alert(s) ({total_count} Live)"
        )
        priority = "urgent" if unicorns else "high"

    safe_title = title.encode("ascii", "ignore").decode("ascii")
    try:
        resp = requests.post(
            f"https://ntfy.sh/{ntfy_topic}",
            data=body.encode("utf-8"),
            headers={"Title": safe_title, "Priority": priority, "Tags": "airplane,star"},
            timeout=15,
        )
        print(f"[Alerts] Sent ntfy.sh push to '{ntfy_topic}' (HTTP {resp.status_code}, manual={is_manual_run}).")
    except Exception as e:
        print(f"[Alerts] ntfy.sh failed: {e}")


def main():
    print("=== AwardRadar Zero-Cost Engine Starting ===")
    cfg = load_config()
    searches = cfg.get("searches", [])
    alert_cfg = cfg.get("alert_config", {}) or {}
    min_grade = int(alert_cfg.get("min_grade", 4))
    alert_dfw_dal_unicorns = bool(alert_cfg.get("alert_dfw_dal_unicorns", True))

    raw_deals = []
    # Layer 0: Always actively search user's configured itineraries first!
    raw_deals.extend(layer0_direct_itineraries(searches))
    raw_deals.extend(layer1_pointsyeah_and_promos())
    raw_deals.extend(layer2_rss_feeds())
    raw_deals.extend(layer3_reddit_awardtravel())
    raw_deals.extend(layer4_seats_aero(searches))

    deduped = {}
    for d in raw_deals:
        key = f"{d['origin']}-{d['destination']}-{d['cabin']}-{d['airline'][:18].lower()}-{d['travel_date']}"
        if key not in deduped or d.get("matched_search"):
            deduped[key] = d

    enriched = []
    for d in deduped.values():
        matched, itin_name = match_itinerary(d, searches)
        d["matched_search"] = matched
        if itin_name:
            d["itinerary_name"] = itin_name

        cash = d.get("cash_benchmark") or HEURISTIC_CASH.get(d["cabin"], 3200)
        d["cash_benchmark"] = cash
        d["cpp"] = round(max(0.5, ((cash - d["taxes"]) / max(d["miles"], 1000)) * 100), 2)
        grade, grade_label = compute_deal_grade(d["cpp"], d["cabin"], d["miles"], d.get("price_level", "typical"))
        d["grade"] = grade
        d["grade_label"] = grade_label
        d["is_unicorn_dfw_dal"] = (grade == 5 and d["origin"] in ("DFW", "DAL") and not matched)
        if d["is_unicorn_dfw_dal"]:
            d["itinerary_name"] = f"DFW/DAL Unicorn ({d['origin']} → {d['destination']})"
        enriched.append(d)

    # Always keep ALL matched itinerary flights + top graded discovery/unicorn deals
    matched_list = [d for d in enriched if d["matched_search"]]
    other_list = [d for d in enriched if not d["matched_search"]]
    matched_list.sort(key=lambda d: (d["grade"], CABIN_PRIORITY.get(d["cabin"], 1), d["cpp"]), reverse=True)
    other_list.sort(key=lambda d: (d["is_unicorn_dfw_dal"], d["grade"], CABIN_PRIORITY.get(d["cabin"], 1), d["cpp"]), reverse=True)

    final_deals = (matched_list[:20] + other_list[:15])
    final_deals.sort(key=lambda d: (d["grade"], CABIN_PRIORITY.get(d["cabin"], 1), d["cpp"]), reverse=True)

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

    print(f"=== Saved {len(final_deals)} flights ({len(matched_list)} itinerary matches, {len(unicorns)} DFW/DAL unicorns) ===")
    send_ntfy_notification(alert_deals, unicorns, len(final_deals), len(matched_list))


if __name__ == "__main__":
    main()
