"""
routing.py — "How long does everyone take to get there?"

Free-stack version: geocoding via Nominatim (OpenStreetMap), travel times
via OSRM's Table service. No API key required.

Each person can travel by their own mode (Car / Walking / Public
transport) — get_travel_time_matrix groups people by mode so each distinct
mode still costs just one batched OSRM call, then reassembles a single
matrix in the original person order.

Trade-offs worth knowing before relying on this for anything real:

- Nominatim's public usage policy caps requests at ~1/second and requires
  a real identifying User-Agent — geocoding and reverse-geocoding are
  rate-limited to respect that.
- The public OSRM demo server (router.project-osrm.org) only serves the
  "car" (driving) profile. Foot routing is served by a separate community
  demo (routing.openstreetmap.de) — both are shared, best-effort
  infrastructure with no uptime guarantee. Self-host OSRM if you need
  reliability.
- There is no free, open equivalent of real transit routing. "Public
  transport" is approximated as a straight-line distance at a fixed
  average speed. Callers must check is_estimated(mode) and disclose that
  plainly — this module never silently passes an estimate off as real.
"""

import os
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

import optimization

load_dotenv()

NOMINATIM_USER_AGENT = os.getenv(
    "NOMINATIM_USER_AGENT", "meethalfway_app (set NOMINATIM_USER_AGENT in .env)"
)

# Public community-run OSRM demo servers, one per profile. Override via .env
# if you're self-hosting or using a different provider.
OSRM_BASE_URLS = {
    "car": os.getenv("OSRM_BASE_URL_CAR", "https://router.project-osrm.org"),
    "foot": os.getenv("OSRM_BASE_URL_FOOT", "https://routing.openstreetmap.de/routed-foot"),
}

MODE_TO_PROFILE = {
    "Car": "car",
    "Walking": "foot",
    # "Public transport" is handled separately — see is_estimated().
}

PUBLIC_TRANSPORT_SPEED_KMH = 20.0  # crude average incl. waiting/stops

# Approximate bounding box covering all of Goa state with a small margin —
# not an exact administrative polygon, just enough to keep location search
# and the map picker confined to Goa. geopy's viewbox format is a pair of
# (lat, lon) points, diagonal corners of the box.
GOA_BOUNDS = {"lat_min": 14.88, "lat_max": 15.82, "lon_min": 73.66, "lon_max": 74.35}
GOA_VIEWBOX = [
    (GOA_BOUNDS["lat_max"], GOA_BOUNDS["lon_min"]),
    (GOA_BOUNDS["lat_min"], GOA_BOUNDS["lon_max"]),
]

_geolocator = Nominatim(user_agent=NOMINATIM_USER_AGENT)
# Nominatim's usage policy: max 1 request/second. Geocode and reverse share
# one rate limiter since both hit the same Nominatim instance.
# swallow_exceptions=False: geopy's default quietly returns None on any error
# (blocked, rate-limited, timed out), which the UI then mistook for "place not
# found" and for "no area name". Errors now raise so we can fall back to Photon.
_rate_limited_geocode = RateLimiter(
    _geolocator.geocode, min_delay_seconds=1.0, max_retries=1, error_wait_seconds=2.0,
    swallow_exceptions=False,
)
_rate_limited_reverse = RateLimiter(
    _geolocator.reverse, min_delay_seconds=1.0, max_retries=1, error_wait_seconds=2.0,
    swallow_exceptions=False,
)

# Photon (photon.komoot.io) is a free OpenStreetMap-based geocoder with no key.
# Used when Nominatim fails: shared cloud hosts (e.g. Streamlit Community
# Cloud) are often blocked or rate-limited by Nominatim even though it works
# fine from a home connection.
PHOTON_URL = os.getenv("PHOTON_URL", "https://photon.komoot.io")
_PHOTON_HEADERS = {"User-Agent": NOMINATIM_USER_AGENT}


class RoutingError(Exception):
    """Raised for any geocoding/routing failure the UI should surface."""


def is_estimated(mode: str) -> bool:
    """
    True when travel times for this mode are a straight-line approximation
    rather than real routing — currently just Public transport, since no
    free transit router exists. The UI uses this to show a clear, upfront
    disclaimer instead of quietly presenting an estimate as a real time.
    """
    return mode == "Public transport"


def _in_goa(lat: float, lon: float) -> bool:
    return (GOA_BOUNDS["lat_min"] <= lat <= GOA_BOUNDS["lat_max"]
            and GOA_BOUNDS["lon_min"] <= lon <= GOA_BOUNDS["lon_max"])


def _photon_label(props: dict) -> str:
    parts = []
    for key in ("name", "locality", "suburb", "city", "district", "state"):
        v = props.get(key)
        if v and v not in parts:
            parts.append(v)
    return ", ".join(parts)


def _photon_search_in_goa(query: str) -> Optional[Tuple[float, float, str]]:
    bbox = f"{GOA_BOUNDS['lon_min']},{GOA_BOUNDS['lat_min']},{GOA_BOUNDS['lon_max']},{GOA_BOUNDS['lat_max']}"
    resp = requests.get(
        f"{PHOTON_URL}/api/",
        params={"q": query, "bbox": bbox, "limit": 3, "lang": "en"},
        headers=_PHOTON_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    for feat in resp.json().get("features", []):
        lon, lat = feat["geometry"]["coordinates"][:2]
        if _in_goa(lat, lon):
            return lat, lon, _photon_label(feat.get("properties", {}))
    return None


def geocode_in_goa(query: str) -> Tuple[float, float, str]:
    """
    Geocode restricted to Goa, India. Tries Nominatim first (viewbox +
    bounded=True, so anything outside the box is excluded outright), then
    falls back to Photon if Nominatim errors out or finds nothing. Returns
    (lat, lon, display_label).
    """
    nominatim_error = None
    try:
        location = _rate_limited_geocode(query, viewbox=GOA_VIEWBOX, bounded=True)
        if location is not None:
            return location.latitude, location.longitude, location.address
    except Exception as exc:
        nominatim_error = exc

    try:
        found = _photon_search_in_goa(query)
        if found:
            return found
    except Exception as exc:
        if nominatim_error is not None:
            raise RoutingError(
                f"The location search services aren't responding right now ({type(exc).__name__}). "
                f"Please try again in a minute."
            ) from exc

    raise RoutingError(f"Couldn't find '{query}' within Goa. Try a more specific search.")


def geocode(address: str) -> Tuple[float, float]:
    """Convert a free-text address/place name into (lat, lon) via Nominatim."""
    try:
        location = _rate_limited_geocode(address)
    except Exception as exc:  # geopy raises various network/service errors
        raise RoutingError(f"Geocoding service error for '{address}': {exc}") from exc

    if location is None:
        raise RoutingError(
            f"Could not find '{address}'. Try adding more detail, e.g. "
            f"'{address}, Goa, India'."
        )
    return location.latitude, location.longitude


def _photon_reverse_name(point: Tuple[float, float]) -> Optional[str]:
    lat, lon = point
    resp = requests.get(
        f"{PHOTON_URL}/reverse",
        params={"lat": lat, "lon": lon, "lang": "en"},
        headers=_PHOTON_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    feats = resp.json().get("features", [])
    if not feats:
        return None
    props = feats[0].get("properties", {})
    return (
        props.get("suburb") or props.get("locality") or props.get("city")
        or props.get("district") or props.get("county") or props.get("name")
    )


def reverse_geocode(point: Tuple[float, float]) -> str:
    """
    Turn coordinates back into a human-readable area name (e.g. "Bambolim")
    so the result screen can say where to meet, not just show a pin. Tries
    Nominatim, then Photon; only falls back to a lat/lon string if both fail.
    """
    lat, lon = point
    try:
        location = _rate_limited_reverse((lat, lon), zoom=14)
        if location is not None:
            addr = location.raw.get("address", {})
            name = (
                addr.get("suburb")
                or addr.get("neighbourhood")
                or addr.get("village")
                or addr.get("town")
                or addr.get("city_district")
                or addr.get("city")
            )
            if name:
                return name
    except Exception:
        pass

    try:
        name = _photon_reverse_name((lat, lon))
        if name:
            return name
    except Exception:
        pass

    return f"{lat:.4f}, {lon:.4f}"


def _osrm_table(
    sub_origins: List[Tuple[float, float]],
    candidates: List[Tuple[float, float]],
    profile: str,
) -> List[List[Optional[float]]]:
    """One batched OSRM Table call: len(sub_origins) x len(candidates), minutes."""
    base_url = OSRM_BASE_URLS[profile]
    all_points = list(sub_origins) + list(candidates)
    # OSRM coordinates are lon,lat (opposite of how we store lat,lon everywhere else).
    coords_str = ";".join(f"{lon},{lat}" for lat, lon in all_points)

    n_sub = len(sub_origins)
    n_candidates = len(candidates)
    sources = ";".join(str(i) for i in range(n_sub))
    destinations = ";".join(str(i) for i in range(n_sub, n_sub + n_candidates))

    url = f"{base_url}/table/v1/{profile}/{coords_str}"
    params = {"sources": sources, "destinations": destinations, "annotations": "duration"}

    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise RoutingError(f"OSRM request failed: {exc}") from exc

    if data.get("code") != "Ok":
        raise RoutingError(f"OSRM table request failed: {data.get('code')} — {data.get('message', '')}")

    durations = data["durations"]  # seconds; shape n_sub x n_candidates
    return [[d / 60.0 if d is not None else None for d in row] for row in durations]


def get_travel_time_matrix(
    origins: List[Tuple[float, float]],
    candidates: List[Tuple[float, float]],
    modes: List[str],
) -> List[List[Optional[float]]]:
    """
    Full matrix of travel times: matrix[person_index][candidate_index] =
    minutes. Each person (origin) can use a different mode — people are
    grouped by mode so every distinct mode still costs just one batched
    OSRM call (or the straight-line fallback), then results are
    reassembled in the original person order.
    """
    if len(modes) != len(origins):
        raise ValueError("modes must have exactly one entry per origin")

    n_candidates = len(candidates)
    matrix: List[List[Optional[float]]] = [[None] * n_candidates for _ in origins]

    groups: Dict[str, List[int]] = {}
    for idx, mode in enumerate(modes):
        groups.setdefault(mode, []).append(idx)

    for mode, indices in groups.items():
        if is_estimated(mode):
            for idx in indices:
                origin = origins[idx]
                matrix[idx] = [
                    optimization.straight_line_time_minutes(origin, c, speed_kmh=PUBLIC_TRANSPORT_SPEED_KMH)
                    for c in candidates
                ]
            continue

        profile = MODE_TO_PROFILE.get(mode, "car")
        sub_origins = [origins[i] for i in indices]
        sub_matrix = _osrm_table(sub_origins, candidates, profile)
        for local_i, global_i in enumerate(indices):
            matrix[global_i] = sub_matrix[local_i]

    return matrix


def get_travel_times(
    origins: List[Tuple[float, float]],
    destination: Tuple[float, float],
    modes: List[str],
) -> List[float]:
    """Per-person-mode travel time in minutes from each origin to a single destination."""
    matrix = get_travel_time_matrix(origins, [destination], modes)
    return [row[0] for row in matrix]
