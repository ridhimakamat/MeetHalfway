"""
places.py — "What's actually there?"

Free-stack version: venue search via the Overpass API, which queries
OpenStreetMap's tagged data directly. No API key required, but Overpass's
public instance is shared community infrastructure — be gentle with it
(one query per search, sensible radius, don't poll it in a loop).

Results are sorted nearest-first from the meeting point and carry a
distance_m field, so the UI can say "closest cafe is 250 m away" rather
than listing venues in arbitrary order.

OSM has patchy coverage of ratings/reviews compared to Google Places, so
results here have a name and address but usually no rating.
"""

import os
import time
from typing import Dict, List, Tuple

import requests
from dotenv import load_dotenv

import optimization

load_dotenv()

OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
# Tried only if the primary instance fails (busy or timing out).
OVERPASS_FALLBACK_URL = "https://overpass.kumi.systems/api/interpreter"

# Maps the UI's purpose buttons to (OSM key, OSM value) tag pairs.
PURPOSE_TO_OSM_TAGS = {
    "Café": [("amenity", "cafe")],
    "Food": [("amenity", "restaurant"), ("amenity", "fast_food")],
    "Outdoors": [
        ("leisure", "park"),
        ("leisure", "garden"),
        ("leisure", "nature_reserve"),
        ("natural", "beach"),
        ("tourism", "viewpoint"),
    ],
}

# Search radii per purpose. Outdoor spots are sparser than cafes, so widen
# further before giving up.
PURPOSE_RADII_M = {
    "Outdoors": (3000, 8000, 15000),
}
DEFAULT_RADII_M = (1500, 3000, 5000)

# Waterfalls are rare and usually far from any given point, so a plain
# "nearest eight" list would never include one next to a handful of parks.
# For these purposes they are searched separately over a wide radius, and
# the nearest few are added to the list.
PURPOSE_FEATURED_TAGS = {
    "Outdoors": [("waterway", "waterfall"), ("natural", "waterfall")],
}
FEATURED_RADIUS_M = 20000
FEATURED_MAX = 2


class PlacesError(Exception):
    """Raised for any Overpass API failure the UI should surface."""


def _build_query(location: Tuple[float, float], radius_m: int, tags: List[Tuple[str, str]]) -> str:
    lat, lon = location
    clauses = []
    for key, value in tags:
        clauses.append(f'node["{key}"="{value}"](around:{radius_m},{lat},{lon});')
        clauses.append(f'way["{key}"="{value}"](around:{radius_m},{lat},{lon});')
    body = "\n      ".join(clauses)
    # Fetch a generous pool, then sort by distance ourselves: Overpass's own
    # result order is arbitrary, so a small cap here would drop the nearest.
    return f"""
    [out:json][timeout:25];
    (
      {body}
    );
    out center 120;
    """


def _post_overpass(query: str) -> Dict:
    urls = [OVERPASS_URL]
    if OVERPASS_FALLBACK_URL not in urls:
        urls.append(OVERPASS_FALLBACK_URL)

    # Overpass instances commonly reject requests with a generic library
    # User-Agent (HTTP 406/429), so identify the app explicitly.
    headers = {
        "User-Agent": os.getenv("NOMINATIM_USER_AGENT", "meethalfway_app (personal project)"),
        "Accept": "application/json",
    }

    last_error = "unknown error"
    for url in urls:
        host = url.split("/")[2]
        for attempt in range(2):  # one short retry for "server busy" responses
            try:
                resp = requests.post(url, data={"data": query}, headers=headers, timeout=35)
                if resp.status_code in (429, 502, 503, 504) and attempt == 0:
                    last_error = f"{host} returned HTTP {resp.status_code}"
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.HTTPError as exc:
                last_error = f"{host} returned HTTP {exc.response.status_code}"
                break
            except requests.RequestException as exc:
                last_error = f"{host}: {type(exc).__name__}"
                break
            except ValueError:
                last_error = f"{host} sent an unreadable response"
                break
    raise PlacesError(last_error)


def search_places(
    location: Tuple[float, float],
    purpose: str,
    radius_m: int = 1500,
    max_results: int = 8,
    tags: List[Tuple[str, str]] = None,
) -> List[Dict]:
    """Find real venues near the meeting point matching the purpose, nearest first."""
    if tags is None:
        tags = PURPOSE_TO_OSM_TAGS.get(purpose, PURPOSE_TO_OSM_TAGS["Café"])
    data = _post_overpass(_build_query(location, radius_m, tags))

    places: List[Dict] = []
    for element in data.get("elements", []):
        tags_dict = element.get("tags", {})
        name = tags_dict.get("name")
        if not name:
            continue  # skip unnamed OSM features — not useful to recommend

        if element["type"] == "node":
            lat, lon = element.get("lat"), element.get("lon")
        else:  # "way" — Overpass gives us a computed center via `out center`
            center = element.get("center", {})
            lat, lon = center.get("lat"), center.get("lon")

        if lat is None or lon is None:
            continue

        distance_m = optimization.haversine_km(location, (lat, lon)) * 1000.0

        # A venue is often mapped as both a node and a building outline;
        # keep only one entry per name within 150 m.
        if any(p["name"] == name and abs(p["distance_m"] - distance_m) < 150 for p in places):
            continue

        address_parts = [
            tags_dict.get("addr:housenumber"),
            tags_dict.get("addr:street"),
            tags_dict.get("addr:suburb") or tags_dict.get("addr:city"),
        ]
        address = ", ".join(p for p in address_parts if p) or None

        places.append(
            {
                "name": name,
                "rating": None,  # OSM doesn't carry ratings
                "user_ratings_total": None,
                "address": address,
                "lat": lat,
                "lon": lon,
                "distance_m": distance_m,
                "open_now": None,  # opening_hours exists but needs parsing to evaluate "now"
            }
        )

    places.sort(key=lambda p: p["distance_m"])
    return places[:max_results]


def search_places_with_fallback(
    location: Tuple[float, float],
    purpose: str,
    radii_m: Tuple[int, ...] = None,
    max_results: int = 8,
) -> Dict:
    """
    Try increasingly wide search radii until something is found, instead of
    silently returning an empty list or presenting a match found 8 km away
    as if it were right next to the meeting point.

    Returns:
        {
            "places": [...],              # nearest first, possibly empty
            "radius_used_m": int or None, # the radius that produced results
            "expanded": bool,              # True if the first radius came up empty
            "error": bool,                 # True if the venue service itself failed
        }                                  # (so "none found" isn't confused with "couldn't ask")
    """
    if radii_m is None:
        radii_m = PURPOSE_RADII_M.get(purpose, DEFAULT_RADII_M)

    failures = 0
    last_error = None
    found: List[Dict] = []
    radius_used = None
    expanded = False
    for i, radius_m in enumerate(radii_m):
        try:
            results = search_places(location, purpose, radius_m=radius_m, max_results=max_results)
        except PlacesError as exc:
            failures += 1
            last_error = str(exc)
            continue
        if results:
            found, radius_used, expanded = results, radius_m, i > 0
            break

    # Rare-but-wanted features (e.g. waterfalls for Outdoors), searched wide.
    featured_tags = PURPOSE_FEATURED_TAGS.get(purpose)
    if featured_tags:
        try:
            extras = search_places(
                location, purpose, radius_m=FEATURED_RADIUS_M, max_results=FEATURED_MAX, tags=featured_tags
            )
        except PlacesError:
            extras = []
        for e in extras:
            e["featured"] = True
        known = {p_["name"] for p_ in found}
        found = found + [e for e in extras if e["name"] not in known]
        found.sort(key=lambda p_: p_["distance_m"])
        if found and radius_used is None:
            radius_used, expanded = FEATURED_RADIUS_M, True

    if found:
        return {"places": found, "radius_used_m": radius_used, "expanded": expanded,
                "error": False, "error_detail": None}

    all_failed = failures == len(radii_m)
    return {
        "places": [],
        "radius_used_m": None,
        "expanded": True,
        "error": all_failed,
        "error_detail": last_error if all_failed else None,
    }
