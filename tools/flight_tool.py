import os
import re
import certifi
import airportsdata
import pycountry
import requests
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

API_KEY = os.getenv("AVIATIONSTACK_API_KEY")

# Default origin when user says only destination, e.g. "Japan trip"
# Change this if your default location is not Bangladesh/Dhaka.
DEFAULT_ORIGIN_IATA = os.getenv("DEFAULT_ORIGIN_IATA", "DAC")

BASE_URL = "https://api.aviationstack.com/v1/flights"

AIRPORTS = airportsdata.load("IATA")


COUNTRY_ALIASES = {
    "usa": "US",
    "u.s.a": "US",
    "u.s.": "US",
    "america": "US",
    "united states": "US",
    "uk": "GB",
    "u.k.": "GB",
    "britain": "GB",
    "england": "GB",
    "uae": "AE",
    "dubai": "AE",
    "south korea": "KR",
    "korea": "KR",
    "russia": "RU",
    "vietnam": "VN",
    "bangladesh": "BD",
    "india": "IN",
    "japan": "JP",
    "china": "CN",
    "singapore": "SG",
    "malaysia": "MY",
    "thailand": "TH",
    "indonesia": "ID",
    "nepal": "NP",
    "qatar": "QA",
    "saudi arabia": "SA",
    "turkey": "TR",
    "canada": "CA",
    "australia": "AU",
    "germany": "DE",
    "france": "FR",
    "italy": "IT",
    "spain": "ES",
}


# Preferred main airport for country-level search
COUNTRY_MAIN_AIRPORT = {
    "BD": "DAC",
    "IN": "DEL",
    "JP": "NRT",
    "US": "JFK",
    "GB": "LHR",
    "AE": "DXB",
    "SG": "SIN",
    "MY": "KUL",
    "TH": "BKK",
    "ID": "CGK",
    "CN": "PEK",
    "KR": "ICN",
    "NP": "KTM",
    "QA": "DOH",
    "SA": "JED",
    "TR": "IST",
    "CA": "YYZ",
    "AU": "SYD",
    "DE": "FRA",
    "FR": "CDG",
    "IT": "FCO",
    "ES": "MAD",
}


CITY_MAIN_AIRPORT = {
    "dhaka": "DAC",
    "delhi": "DEL",
    "new delhi": "DEL",
    "mumbai": "BOM",
    "kolkata": "CCU",
    "chennai": "MAA",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "tokyo": "NRT",
    "osaka": "KIX",
    "kyoto": "KIX",
    "new york": "JFK",
    "london": "LHR",
    "dubai": "DXB",
    "singapore": "SIN",
    "kuala lumpur": "KUL",
    "bangkok": "BKK",
    "doha": "DOH",
    "istanbul": "IST",
    "toronto": "YYZ",
    "sydney": "SYD",
    "paris": "CDG",
    "rome": "FCO",
    "madrid": "MAD",
    "frankfurt": "FRA",
}

STOP_WORDS = {
    "flight", "flights", "ticket", "tickets", "trip", "travel",
    "plan", "complete", "days", "day", "including", "hotel",
    "hotels", "sightseeing", "under", "budget", "info", "information"
}


# ---------------------------------------------------------------------------
# Precomputed indexes (built once at import time, not per-request)
# ---------------------------------------------------------------------------

def _build_country_to_airports_index():
    """country_code -> list of (score, iata), pre-sorted best-first."""
    index = {}

    for iata, airport in AIRPORTS.items():
        if not iata:
            continue

        airport_country = str(airport.get("country", "")).upper().strip()
        if not airport_country:
            continue

        name = str(airport.get("name", "")).lower()
        city = str(airport.get("city", "")).lower()

        score = 0
        if "international" in name:
            score += 50
        if "intl" in name:
            score += 40
        if "capital" in name:
            score += 20
        if city:
            score += 5

        index.setdefault(airport_country, []).append((score, iata))

    for code in index:
        index[code].sort(reverse=True)

    return index


def _build_city_to_airports_index():
    """lowercase city -> list of (score, iata), pre-sorted best-first."""
    index = {}

    for iata, airport in AIRPORTS.items():
        if not iata:
            continue

        city = str(airport.get("city", "")).lower().strip()
        if not city:
            continue

        name = str(airport.get("name", "")).lower()
        score = 0
        if "international" in name:
            score += 10

        index.setdefault(city, []).append((score, iata))

    for city in index:
        index[city].sort(reverse=True)

    return index


def _build_name_search_index():
    """
    List of (lowercase_name, iata, score_bonus) for substring matching
    against airport names, used as a last-resort fallback.
    """
    entries = []

    for iata, airport in AIRPORTS.items():
        if not iata:
            continue

        name = str(airport.get("name", "")).lower().strip()
        if not name:
            continue

        bonus = 10 if "international" in name else 0
        entries.append((name, iata, bonus))

    return entries


def _build_country_alpha2_to_name():
    return {country.alpha_2: country.name.lower() for country in pycountry.countries}


def _build_country_name_pattern():
    """Single compiled regex matching any known country name (len >= 4)."""
    names = sorted(
        {country.name.lower() for country in pycountry.countries if len(country.name) >= 4},
        key=len,
        reverse=True,
    )
    if not names:
        return None
    pattern = r"\b(" + "|".join(re.escape(n) for n in names) + r")\b"
    return re.compile(pattern)


def _build_city_pattern():
    names = sorted(CITY_MAIN_AIRPORT.keys(), key=len, reverse=True)
    if not names:
        return None
    pattern = r"\b(" + "|".join(re.escape(n) for n in names) + r")\b"
    return re.compile(pattern)


def _build_country_alias_pattern():
    names = sorted(COUNTRY_ALIASES.keys(), key=len, reverse=True)
    if not names:
        return None
    pattern = r"\b(" + "|".join(re.escape(n) for n in names) + r")\b"
    return re.compile(pattern)


_COUNTRY_TO_AIRPORTS = _build_country_to_airports_index()
_CITY_TO_AIRPORTS = _build_city_to_airports_index()
_AIRPORT_NAME_ENTRIES = _build_name_search_index()
_COUNTRY_ALPHA2_TO_NAME = _build_country_alpha2_to_name()
_COUNTRY_NAME_PATTERN = _build_country_name_pattern()
_CITY_PATTERN = _build_city_pattern()
_COUNTRY_ALIAS_PATTERN = _build_country_alias_pattern()
_VALID_IATA_CODES = set(AIRPORTS.keys())

_FROM_TO_PATTERN = re.compile(
    r"\bfrom\s+(.+?)\s+\bto\s+(.+?)(?:\s+(?:on|for|under|including|with|in|at)\b|[.!?]|$)"
)
_TO_FROM_PATTERN = re.compile(
    r"\bto\s+(.+?)\s+\bfrom\s+(.+?)(?:\s+(?:on|for|under|including|with|in|at)\b|[.!?]|$)"
)
_FROM_ONLY_PATTERN = re.compile(
    r"\bfrom\s+(.+?)(?:\s+(?:on|for|under|including|with|in|at)\b|[.!?]|$)"
)
_TO_ONLY_PATTERN = re.compile(
    r"\bto\s+(.+?)(?:\s+(?:on|for|under|including|with|in|at)\b|[.!?]|$)"
)
_IATA_CANDIDATE_PATTERN = re.compile(r"\b[A-Z]{3}\b")

_GLOBAL_KEYWORDS = (
    "all country",
    "all countries",
    "global flight",
    "global flights",
    "all flight",
    "all flights",
    "worldwide flight",
    "worldwide flights",
)


def clean_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    words = [w for w in text.split() if w not in STOP_WORDS]
    return " ".join(words).strip()


@lru_cache(maxsize=512)
def country_name_to_code(text: str):
    text = clean_text(text)

    if not text:
        return None

    if text in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[text]

    try:
        country = pycountry.countries.lookup(text)
        return country.alpha_2
    except LookupError:
        pass

    # Detect country name inside longer text
    if _COUNTRY_NAME_PATTERN:
        match = _COUNTRY_NAME_PATTERN.search(text)
        if match:
            matched_name = match.group(1)
            for alpha2, name in _COUNTRY_ALPHA2_TO_NAME.items():
                if name == matched_name:
                    return alpha2

    if _COUNTRY_ALIAS_PATTERN:
        match = _COUNTRY_ALIAS_PATTERN.search(text)
        if match:
            return COUNTRY_ALIASES.get(match.group(1))

    return None


@lru_cache(maxsize=256)
def get_best_airport_for_country(country_code: str):
    preferred = COUNTRY_MAIN_AIRPORT.get(country_code)

    if preferred and preferred in AIRPORTS:
        return preferred

    candidates = _COUNTRY_TO_AIRPORTS.get(country_code)

    if not candidates:
        # Fall back to matching by full country name, in case the dataset's
        # `country` field uses full names instead of alpha-2 codes for some
        # entries (kept for parity with the original behaviour).
        country = pycountry.countries.get(alpha_2=country_code)
        if country:
            candidates = _COUNTRY_TO_AIRPORTS.get(country.name.upper())

    if not candidates:
        return None

    return candidates[0][1]


@lru_cache(maxsize=1024)
def resolve_location_to_iata(location: str):
    """
    Converts country/city/airport/IATA into IATA code.

    Examples:
    Bangladesh -> DAC
    Japan -> NRT
    Dhaka -> DAC
    Tokyo -> NRT
    DAC -> DAC
    """

    if not location:
        return None

    raw_location = location.strip()

    # Direct IATA code
    if re.fullmatch(r"[A-Za-z]{3}", raw_location):
        code = raw_location.upper()
        if code in AIRPORTS:
            return code

    location_clean = clean_text(raw_location)

    if not location_clean:
        return None

    # City preferred airport
    if location_clean in CITY_MAIN_AIRPORT:
        return CITY_MAIN_AIRPORT[location_clean]

    # Country preferred airport
    country_code = country_name_to_code(location_clean)
    if country_code:
        airport = get_best_airport_for_country(country_code)
        if airport:
            return airport

    # Exact / partial city match via precomputed index
    best_score = 0
    best_iata = None

    exact = _CITY_TO_AIRPORTS.get(location_clean)
    if exact:
        return exact[0][1]

    for city, entries in _CITY_TO_AIRPORTS.items():
        if location_clean in city:
            score = 70 + entries[0][0]
            if score > best_score:
                best_score = score
                best_iata = entries[0][1]

    if best_iata:
        return best_iata

    # Last resort: substring match against full airport names
    for name, iata, bonus in _AIRPORT_NAME_ENTRIES:
        if location_clean in name:
            score = 50 + bonus
            if score > best_score:
                best_score = score
                best_iata = iata

    return best_iata


def find_location_mentions(query: str):
    """
    Finds country or city names inside a natural language query.
    """

    q = query.lower()
    mentions = []

    if _COUNTRY_ALIAS_PATTERN:
        for match in _COUNTRY_ALIAS_PATTERN.finditer(q):
            mentions.append(match.group(1))

    if _COUNTRY_NAME_PATTERN:
        for match in _COUNTRY_NAME_PATTERN.finditer(q):
            mentions.append(match.group(1))

    if _CITY_PATTERN:
        for match in _CITY_PATTERN.finditer(q):
            mentions.append(match.group(1))

    # Remove duplicates while keeping order
    unique_mentions = []
    for item in mentions:
        if item not in unique_mentions:
            unique_mentions.append(item)

    return unique_mentions


def _extract_valid_iata_codes(text: str):
    """
    Returns 3-letter uppercase codes from text that are actually valid
    IATA airport codes (filters out incidental all-caps words like
    'USA' or 'ASAP' that would otherwise be misread as airports).
    """
    candidates = _IATA_CANDIDATE_PATTERN.findall(text)
    return [c for c in candidates if c.upper() in _VALID_IATA_CODES]


def parse_route(query: str):
    """
    Returns:
    dep_iata, arr_iata

    Can return:
    None, None  -> global live flights
    DAC, NRT    -> filtered route
    DAC, None   -> all flights from DAC
    None, NRT   -> all flights to NRT
    """

    q = query.strip()
    q_lower = q.lower()

    # Global / all-country query
    if any(keyword in q_lower for keyword in _GLOBAL_KEYWORDS):
        return None, None

    # Direct IATA code route: DAC to NRT (validated against real airports)
    codes = _extract_valid_iata_codes(q)

    if len(codes) >= 2:
        dep = codes[0].upper()
        arr = codes[1].upper()
        return dep, arr

    # Pattern: from X to Y
    match = _FROM_TO_PATTERN.search(q_lower)

    if match:
        origin_text = match.group(1)
        dest_text = match.group(2)

        dep_iata = resolve_location_to_iata(origin_text)
        arr_iata = resolve_location_to_iata(dest_text)

        return dep_iata, arr_iata

    # Pattern: to Y from X
    match = _TO_FROM_PATTERN.search(q_lower)

    if match:
        dest_text = match.group(1)
        origin_text = match.group(2)

        dep_iata = resolve_location_to_iata(origin_text)
        arr_iata = resolve_location_to_iata(dest_text)

        return dep_iata, arr_iata

    # Pattern: flights from X
    match = _FROM_ONLY_PATTERN.search(q_lower)

    if match:
        origin_text = match.group(1)
        dep_iata = resolve_location_to_iata(origin_text)
        return dep_iata, None

    # Pattern: flights to X
    match = _TO_ONLY_PATTERN.search(q_lower)

    if match:
        dest_text = match.group(1)
        arr_iata = resolve_location_to_iata(dest_text)
        return None, arr_iata

    # Fallback: find country/city mentions
    mentions = find_location_mentions(q)

    if len(mentions) >= 2:
        dep_iata = resolve_location_to_iata(mentions[0])
        arr_iata = resolve_location_to_iata(mentions[1])
        return dep_iata, arr_iata

    if len(mentions) == 1:
        arr_iata = resolve_location_to_iata(mentions[0])
        return DEFAULT_ORIGIN_IATA, arr_iata

    return None, None


def format_flight(flight: dict):
    airline = flight.get("airline", {}).get("name") or "Unknown airline"
    flight_number = flight.get("flight", {}).get("iata") or "Unknown flight number"
    status = flight.get("flight_status") or "Unknown"

    dep = flight.get("departure", {}) or {}
    arr = flight.get("arrival", {}) or {}

    dep_airport = dep.get("airport") or "Unknown departure airport"
    dep_iata = dep.get("iata") or "Unknown"
    dep_terminal = dep.get("terminal") or "N/A"
    dep_gate = dep.get("gate") or "N/A"
    dep_scheduled = dep.get("scheduled") or "Unknown"
    dep_delay = dep.get("delay")
    dep_delay_text = f"{dep_delay} minutes" if dep_delay is not None else "N/A"

    arr_airport = arr.get("airport") or "Unknown arrival airport"
    arr_iata = arr.get("iata") or "Unknown"
    arr_terminal = arr.get("terminal") or "N/A"
    arr_gate = arr.get("gate") or "N/A"
    arr_scheduled = arr.get("scheduled") or "Unknown"
    arr_delay = arr.get("delay")
    arr_delay_text = f"{arr_delay} minutes" if arr_delay is not None else "N/A"

    return f"""
Airline: {airline}
Flight: {flight_number}
Status: {status}

Departure:
- Airport: {dep_airport}
- IATA: {dep_iata}
- Terminal: {dep_terminal}
- Gate: {dep_gate}
- Scheduled: {dep_scheduled}
- Delay: {dep_delay_text}

Arrival:
- Airport: {arr_airport}
- IATA: {arr_iata}
- Terminal: {arr_terminal}
- Gate: {arr_gate}
- Scheduled: {arr_scheduled}
- Delay: {arr_delay_text}
""".strip()


def search_flights(query: str, limit: int = 10, max_retries: int = 1):
    if not API_KEY:
        return (
            "Flight API error: AVIATIONSTACK_API_KEY is missing.\n"
            "Please add this in your .env file:\n"
            "AVIATIONSTACK_API_KEY=your_api_key_here"
        )

    dep_iata, arr_iata = parse_route(query)

    params = {
        "access_key": API_KEY,
        "limit": min(limit, 100),
    }

    if dep_iata:
        params["dep_iata"] = dep_iata

    if arr_iata:
        params["arr_iata"] = arr_iata

    response = None
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.get(BASE_URL, params=params, timeout=30)
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            response = None

    if response is None:
        return f"Flight API request failed: {last_error}"

    if response.status_code >= 400:
        return (
            "Flight API error:\n"
            f"HTTP status: {response.status_code}\n"
            f"Response: {response.text[:300]}"
        )

    try:
        data = response.json()
    except ValueError:
        return "Flight API returned invalid JSON."

    if "error" in data:
        error = data["error"]
        return (
            "Flight API error:\n"
            f"Code: {error.get('code', 'Unknown')}\n"
            f"Message: {error.get('message', 'Unknown error')}"
        )

    flight_data = data.get("data", [])

    if not flight_data:
        route_text = ""

        if dep_iata and arr_iata:
            route_text = f" for route {dep_iata} to {arr_iata}"
        elif dep_iata:
            route_text = f" from {dep_iata}"
        elif arr_iata:
            route_text = f" to {arr_iata}"

        return (
            f"No live flight data found{route_text}.\n\n"
            "Note: AviationStack provides live/status flight data, not ticket prices. "
            "For actual fare prices, use a flight-pricing API such as Amadeus."
        )

    route_info = "Global live flights"

    if dep_iata and arr_iata:
        route_info = f"Live flights from {dep_iata} to {arr_iata}"
    elif dep_iata:
        route_info = f"Live flights from {dep_iata}"
    elif arr_iata:
        route_info = f"Live flights to {arr_iata}"

    formatted_flights = [format_flight(flight) for flight in flight_data[:limit]]

    return f"{route_info}\n\n" + "\n\n---\n\n".join(formatted_flights)


if __name__ == "__main__":
    print(search_flights("Plan a 7 days Japan trip from Bangladesh"))
    print("\n" + "=" * 80 + "\n")
    print(search_flights("all country flight info"))