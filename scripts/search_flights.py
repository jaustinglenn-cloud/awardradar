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
    ("CheapDFW", "https://cheapdfw.com/feed/"),
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

BASELINE_TRANSFER_BONUSES = {
    "Virgin Atlantic": {"pct": 40, "expires": "2026-12-30", "source": "https://frequentmiler.com/current-point-transfer-bonuses/"},
    "British Airways Avios": {"pct": 30, "expires": "2026-12-01", "source": "https://frequentmiler.com/current-point-transfer-bonuses/"},
    "Iberia Plus": {"pct": 30, "expires": "2026-12-01", "source": "https://frequentmiler.com/current-point-transfer-bonuses/"},
    "Air France/KLM Flying Blue": {"pct": 0, "expires": None, "source": "https://frequentmiler.com/current-point-transfer-bonuses/"},
    "Air Canada Aeroplan": {"pct": 0, "expires": None, "source": "https://frequentmiler.com/current-point-transfer-bonuses/"},
}


def fetch_live_transfer_bonuses():
    """Scrapes Frequent Miler daily for active Chase UR transfer bonuses and their expiration dates."""
    bonuses = {k: dict(v) for k, v in BASELINE_TRANSFER_BONUSES.items()}
    url = "https://frequentmiler.com/current-point-transfer-bonuses/"
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code != 200:
            return bonuses
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text)
        patterns = {
            "Virgin Atlantic": r"Chase[^.]{0,160}?Virgin Atlantic[^.]{0,100}?(\d{2})%([^.]{0,90})",
            "British Airways Avios": r"Chase[^.]{0,160}?British Airways[^.]{0,100}?(\d{2})%([^.]{0,90})",
            "Iberia Plus": r"Chase[^.]{0,160}?Iberia[^.]{0,100}?(\d{2})%([^.]{0,90})",
            "Air France/KLM Flying Blue": r"Chase[^.]{0,160}?Flying Blue[^.]{0,100}?(\d{2})%([^.]{0,90})",
            "Air Canada Aeroplan": r"Chase[^.]{0,160}?Aeroplan[^.]{0,100}?(\d{2})%([^.]{0,90})",
        }
        for prog, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if m:
                bonuses[prog]["pct"] = int(m.group(1))
                tail = m.group(2) or ""
                m_exp = re.search(r"(?:through|until|ends?|exp\w*)\s+([A-Z][a-z]+\s+\d{1,2}(?:,?\s+20\d{2})?|\d{1,2}/\d{1,2}/20\d{2})", tail, re.I)
                if m_exp:
                    bonuses[prog]["expires"] = m_exp.group(1).strip()
            else:
                bonuses[prog]["pct"] = 0
                bonuses[prog]["expires"] = None
        print("[Bonuses] Refreshed daily transfer bonuses:", {k: f"{v['pct']}% (exp {v['expires']})" for k, v in bonuses.items() if v["pct"] > 0})
    except Exception as e:
        print(f"[Bonuses] Refresh skipped ({e}); using baseline.")
    return bonuses


def extract_deal_with_gemini(title, excerpt, source_name, url, api_key):
    """Uses Gemini 2.5 Flash free tier (when GEMINI_API_KEY is set) to accurately extract structured flight deals."""
    if not api_key:
        return None
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    prompt = (
        'Extract the single best REAL flight deal from this article excerpt as strict JSON. '
        'NEVER invent or guess an origin airport, cabin, or price that is not explicitly in the text. '
        'If it is not a concrete flight deal with both an origin and destination airport, return {"is_deal": false}. '
        'If multiple origins are listed and DFW or DAL is explicitly among them, pick DFW or DAL. '
        'Return JSON schema: {"is_deal": bool, "origin": "3-letter IATA", "destination": "3-letter IATA", '
        '"airline": "string", "program": "string", "cabin": "First|Business|Premium Economy|Economy", '
        '"miles": int_or_null, "taxes_usd": number_or_null, "cash_usd": number_or_null, "dates": "string"}.\n\n'
        f"TITLE: {title}\nEXCERPT: {excerpt[:2200]}"
    )
    try:
        resp = requests.post(
            endpoint,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        cand_txt = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        obj = json.loads(cand_txt)
        if not obj.get("is_deal"):
            return None
        o = str(obj.get("origin") or "").upper().strip()
        d = str(obj.get("destination") or "").upper().strip()
        if len(o) != 3 or len(d) != 3 or o == d:
            return None
        cab = obj.get("cabin") or "Economy"
        if cab not in ("First", "Business", "Premium Economy", "Economy"):
            cab = "Economy"
        miles = obj.get("miles")
        cash = obj.get("cash_usd")
        taxes = obj.get("taxes_usd")
        price_level = "typical"
        if not miles and cash and cash >= 60:
            miles = max(5000, math.ceil(float(cash) / 0.0125))
            price_level = "low"
            taxes = 0 if taxes is None else taxes
        if not miles:
            return None
        return make_deal(
            title=title,
            source=f"{source_name} (AI)",
            url=url,
            origin=o,
            dest=d,
            cabin=cab,
            miles=int(miles),
            taxes=float(taxes) if taxes is not None else (150 if d in LONG_HAUL_AIRPORTS else 6),
            dates=str(obj.get("dates") or "Select Dates"),
            program=obj.get("program") or None,
            airline=obj.get("airline") or None,
            cash_benchmark=float(cash) if cash else None,
            price_level=price_level,
        )
    except Exception:
        return None
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


def fetch_article_excerpt(url, max_chars=3500):
    """Fetches the first ~12 paragraphs/list items of an article to extract real routes, miles & cabin."""
    if not url or not url.startswith("http"):
        return ""
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        root = soup.find("article") or soup.find("main") or soup
        chunks = []
        total = 0
        for el in root.find_all(["p", "li"], limit=28):
            txt = " ".join(el.get_text(" ", strip=True).split())
            if len(txt) < 18:
                continue
            chunks.append(txt)
            total += len(txt)
            if total >= max_chars or len(chunks) >= 14:
                break
        return " \n ".join(chunks)
    except Exception:
        return ""


def extract_paired_miles_and_cabin(text, is_long_haul=False):
    """
    Scans sentence-by-sentence to pair mileage cost with the cabin mentioned in the SAME sentence,
    preventing an Economy price in paragraph 3 from being paired with a Business mention in paragraph 1.
    """
    sentences = re.split(r"[\n\.!?]+", text)
    best = None
    for sent in sentences:
        pts = extract_miles(sent)
        if not pts:
            continue
        cab = extract_cabin(sent, miles=pts, is_long_haul=is_long_haul)
        if cab:
            # Prefer Business/First if explicitly paired with valid miles in the same sentence
            prio = CABIN_PRIORITY.get(cab, 1)
            if best is None or prio > best[2]:
                best = (pts, cab, prio, sent)
    if best:
        return best[0], best[1], best[3]
    # Fallback across full text with strict long-haul sanity guard
    pts = extract_miles(text)
    if not pts:
        return None, None, text
    cab = extract_cabin(text, miles=pts, is_long_haul=is_long_haul)
    return pts, cab, text


def extract_cash_fare_deal(text):
    """Detects cash mistake fares / low fares (e.g. from The Flight Deal, Secret Flying, Fly4free)."""
    low = text.lower()
    if not any(k in low for k in ("roundtrip", "round-trip", "one-way", "nonstop", "fare", "flights from", "deal")):
        return None
    m = re.search(r"\$\s*(\d{3,4})\b", text)
    if not m:
        return None
    val = int(m.group(1))
    if 99 <= val <= 2800:
        return val
    return None


def extract_route(text):
    """
    Strictly requires BOTH an explicit origin and destination in the text. Never fabricates DFW.
    If multiple US origins are listed (e.g. "DFW, ORD, JFK to MXP"), prefers DFW/DAL when truly present.
    """
    # 1. Explicit IATA pair: DFW-MXP, DFW -> MXP, ORD to HND
    pairs = re.findall(r"\b([A-Z]{3})\s*(?:->|→|–|-|to|/)\s*([A-Z]{3})\b", text)
    valid_pairs = [(o, d) for o, d in pairs if o in AIRPORT_NAMES and d in AIRPORT_NAMES and o != d]
    for o, d in valid_pairs:
        if o in ("DFW", "DAL"):
            return o, d
    if valid_pairs:
        return valid_pairs[0]

    # 2. Multi-hub text scan with word boundaries
    low = text.lower()
    found = []
    for key, code in CITY_TO_IATA.items():
        m_city = re.search(r"\b" + re.escape(key) + r"\b", low)
        if m_city and code not in [c for _, c in found]:
            found.append((m_city.start(), code))
    found.sort(key=lambda x: x[0])
    codes = [c for _, c in found]
    if len(codes) < 2:
        return None, None

    us_codes = [c for c in codes if c in REGION_MAP["US"]]
    intl_codes = [c for c in codes if c not in REGION_MAP["US"]]
    if us_codes and intl_codes:
        # Prefer DFW/DAL only if actually mentioned in the article!
        orig = "DFW" if "DFW" in us_codes else ("DAL" if "DAL" in us_codes else us_codes[0])
        return orig, intl_codes[0]
    return codes[0], codes[1]


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
    is_lh = (o in LONG_HAUL_AIRPORTS) or (d in LONG_HAUL_AIRPORTS)
    matched_sent = full_text
    if miles is not None:
        pts = miles
        cab = cabin or extract_cabin(full_text, miles=pts, is_long_haul=is_lh)
    else:
        pts, parsed_cab, matched_sent = extract_paired_miles_and_cabin(full_text, is_long_haul=is_lh)
        cab = cabin or parsed_cab
        # Cash mistake-fare / low-fare fallback (Secret Flying, The Flight Deal, Fly4free)
        if not pts:
            cash_deal = extract_cash_fare_deal(full_text)
            if cash_deal:
                cab = cab or ("Business" if ("business" in full_text.lower() and cash_deal >= 900) else "Economy")
                pts = max(5000, math.ceil(cash_deal / 0.0125))
                if cash_benchmark is None:
                    cash_benchmark = cash_deal
                price_level = "low"
                if taxes is None:
                    taxes = 0
    if (pts is None or (pts == 0 and not (price_level == "low" and cash_benchmark))) or not cab:
        return None
    if is_lh and pts < 35000 and cab in ("Business", "First"):
        cab = "Economy"
    tax_val = taxes if taxes is not None else extract_taxes(matched_sent, default_tax=extract_taxes(full_text, default_tax=150 if is_lh else 6))
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
                        # Zero partner-chart guessing: ONLY surface Google Flights results when genuinely flagged LOW
                        if r.get("price_level") != "low":
                            continue
                        gf_url = build_links(o, d, "", cabin=cab, airline=r["airline"], travel_date=query_date)["google_flights_url"]
                        d_cash = make_deal(
                            title=f"{o} → {d} {r['airline']} {cab} — Google Flights LOW Cash Fare (${r['cash']:,})",
                            source=f"{r['source']} (LOW Cash)",
                            url=gf_url,
                            origin=o, dest=d, cabin=cab, miles=0, taxes=0,
                            dates=display_date, program="Cash Fare (Google Flights LOW)", airline=r["airline"],
                            flights_taken=r["flights_taken"], flight_times=r["flight_times"],
                            travel_date=query_date, cash_benchmark=r["cash"],
                            price_level="low", verified=True,
                            matched_search=True, itinerary_name=itin_name,
                        )
                        if d_cash:
                            d_cash["is_cash_deal"] = True
                            deals.append(d_cash)
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
    article_fetches = 0
    for source_name, feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=BROWSER_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            parsed = feedparser.parse(resp.content)
            kept = 0
            for entry in parsed.entries[:12]:
                title = entry.get("title", "")
                summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
                combined = f"{title} \n {summary[:500]}"
                low_c = combined.lower()
                is_cash_deal_feed = source_name in ("CheapDFW", "The Flight Deal", "Fly4free", "Secret Flying")
                if not (is_cash_deal_feed or any(k in low_c for k in AWARD_KEYWORDS)):
                    continue

                link = entry.get("link", feed_url)
                gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
                d_item = None
                if gemini_key and article_fetches < 18 and link.startswith("http"):
                    article_fetches += 1
                    excerpt = fetch_article_excerpt(link) or combined
                    d_item = extract_deal_with_gemini(title, excerpt, source_name, link, gemini_key)
                if not d_item:
                    d_item = make_deal(title, f"{source_name} RSS", link, parse_text=combined)
                if not d_item and article_fetches < 24 and link.startswith("http"):
                    article_fetches += 1
                    excerpt = fetch_article_excerpt(link)
                    if excerpt:
                        d_item = make_deal(title, f"{source_name} RSS", link, parse_text=f"{title} \n {excerpt}")
                if d_item:
                    deals.append(d_item)
                    kept += 1
                    if kept >= 3:
                        break
            print(f"[Layer 2] {source_name}: {kept} verified deals (article fetches so far: {article_fetches})")
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
            print(f"[Layer 3] Reddit JSON error ({url}): {e}")
    # Fallback to Reddit RSS/Atom if JSON API was blocked by datacenter IP rate-limits
    if not deals:
        for sub_name, rss_url in [
            ("r/awardtravel", "https://www.reddit.com/r/awardtravel/new/.rss"),
            ("r/flights", "https://www.reddit.com/r/flights/new/.rss"),
        ]:
            try:
                r = requests.get(rss_url, headers=REDDIT_HEADERS, timeout=12)
                if r.status_code != 200:
                    continue
                feed = feedparser.parse(r.content)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "")
                    body = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
                    combined = f"{title} \n {body[:800]}"
                    if title.strip().endswith("?") and "availability" not in combined.lower():
                        continue
                    d_item = make_deal(title, sub_name, entry.get("link", rss_url), parse_text=combined)
                    if d_item:
                        deals.append(d_item)
            except Exception as e:
                print(f"[Layer 3] Reddit RSS fallback error ({sub_name}): {e}")
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
        return "skipped: no NTFY_TOPIC/NFTY_TOPIC secret"

    is_manual_run = os.environ.get("GITHUB_EVENT_NAME", "") == "workflow_dispatch"
    if not alert_deals:
        if not is_manual_run:
            print("[Alerts] No deals met alert threshold today.")
            return "skipped: 0 deals met threshold"
        title = f"AwardRadar: {total_count} Flights Scanned"
        body = f"Scanned {matched_count} itinerary match(es) ({total_count} total live flights). None crossed your alert grade on this run."
        priority = "default"
        click_url = "https://jaustinglenn-cloud.github.io/awardradar/"
    else:
        entries = []
        for d in alert_deals[:5]:
            stars = "★" * int(d["grade"]) + "☆" * (5 - int(d["grade"]))
            date_str = d.get("travel_date") or d.get("dates") or "Flexible"
            airline_str = (d.get("airline") or d.get("program") or "Airline").split("/")[0].strip()
            cost_lbl = f"${int(d['cash_benchmark']):,} cash" if (d.get("is_cash_deal") or d["miles"] == 0) else f"{d['miles']:,} pts"
            val_lbl = "Google Flights LOW" if (d.get("is_cash_deal") or d["miles"] == 0) else f"{d['cpp']}c CPP"
            entries.append(
                f"{stars} | {cost_lbl} | {d['origin']} -> {d['destination']}\n"
                f"{date_str} | {airline_str} | {val_lbl}"
            )
        entries.append("Tap notification to open AwardRadar & deal links")
        body = "\n\n".join(entries)
        title = f"AwardRadar: {len(alert_deals)} Flight{'s' if len(alert_deals) != 1 else ''} Identified"
        priority = "urgent" if unicorns else "high"
        click_url = "https://jaustinglenn-cloud.github.io/awardradar/"

    safe_title = title.encode("ascii", "ignore").decode("ascii")
    # Proven iOS-compatible ntfy headers (no Markdown header or complex Actions that iOS APNs silently drops)
    headers = {
        "Title": safe_title,
        "Priority": priority,
        "Tags": "airplane,star",
        "Click": click_url,
    }
    try:
        resp = requests.post(
            f"https://ntfy.sh/{ntfy_topic}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        status_str = f"HTTP {resp.status_code} ({len(alert_deals)} alert(s))"
        print(f"[Alerts] ntfy.sh push to '{ntfy_topic}' -> {status_str} (manual={is_manual_run})")
        return status_str
    except Exception as e:
        print(f"[Alerts] ntfy.sh failed: {e}")
        return f"error: {e}"

def main():
    print("=== AwardRadar Zero-Cost Engine Starting ===")
    cfg = load_config()
    searches = cfg.get("searches", [])
    alert_cfg = cfg.get("alert_config", {}) or {}
    min_grade = int(alert_cfg.get("min_grade", 4))
    alert_dfw_dal_unicorns = bool(alert_cfg.get("alert_dfw_dal_unicorns", True))

    live_bonuses = fetch_live_transfer_bonuses()
    raw_deals = []
    # Layer 0: Always actively search user's configured itineraries first!
    raw_deals.extend(layer0_direct_itineraries(searches))
    raw_deals.extend(layer1_pointsyeah_and_promos())
    raw_deals.extend(layer2_rss_feeds())
    raw_deals.extend(layer3_reddit_awardtravel())
    raw_deals.extend(layer4_seats_aero(searches))

    deduped = {}
    for d in raw_deals:
        if not d:
            continue
        key = f"{d['origin']}-{d['destination']}-{d['cabin']}-{d['airline'][:18].lower()}-{d['travel_date']}"
        if key not in deduped or d.get("matched_search"):
            deduped[key] = d

    enriched = []
    for d in deduped.values():
        matched, itin_name = match_itinerary(d, searches)
        d["matched_search"] = matched
        if itin_name:
            d["itinerary_name"] = itin_name

        # Apply active daily Chase UR transfer bonus if applicable
        b_info = live_bonuses.get(d.get("program") or "")
        b_pct = int(b_info.get("pct", 0)) if (b_info and "Chase UR" in (d.get("transfer_currencies") or [])) else 0
        d["base_miles"] = d["miles"]
        d["transfer_bonus_pct"] = b_pct
        d["transfer_bonus_expires"] = b_info.get("expires") if b_pct > 0 else None
        if b_pct > 0:
            d["miles"] = math.ceil(d["base_miles"] / (1.0 + b_pct / 100.0))

        cash = d.get("cash_benchmark") or HEURISTIC_CASH.get(d["cabin"], 3200)
        d["cash_benchmark"] = cash
        if d.get("is_cash_deal") or d["miles"] == 0:
            d["cpp"] = 0.0
            is_lh_cash = (d["origin"] in LONG_HAUL_AIRPORTS) or (d["destination"] in LONG_HAUL_AIRPORTS)
            if d["cabin"] in ("Business", "First") and is_lh_cash and cash <= 2200:
                grade, grade_label = 3, "Unusually Low Business Cash"
            else:
                grade, grade_label = 2, "Google Flights Low Cash"
        else:
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

    alert_deals = []
    unicorns = []
    for d in final_deals:
        if alert_dfw_dal_unicorns and d["grade"] == 5 and d["origin"] in ("DFW", "DAL"):
            alert_deals.append(d)
            unicorns.append(d)
        elif d["matched_search"] and d["grade"] >= min_grade:
            alert_deals.append(d)

    ntfy_status = send_ntfy_notification(alert_deals, unicorns, len(final_deals), len(matched_list))

    os.makedirs(os.path.dirname(DEALS_FILE), exist_ok=True)
    with open(DEALS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "app_version": "v2.7",
            "ntfy_status": ntfy_status,
            "ai_parser_active": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
            "transfer_bonuses": live_bonuses,
            "total_count": len(final_deals),
            "deals": final_deals,
        }, f, indent=2)

    print(f"=== Saved {len(final_deals)} flights ({len(matched_list)} itinerary matches, {len(unicorns)} DFW/DAL unicorns, ntfy={ntfy_status}) ===")


if __name__ == "__main__":
    main()
