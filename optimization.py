"""
optimization.py — "Where should we actually meet?"

V1: geographic_midpoint() — naive average of coordinates.
V3: generate_candidates() + rank_candidates() — search a grid of candidate
    points around the group and score each one by:

        cost = (1 - w) * mean(travel_times) + w * max(travel_times)

    where w is the fairness slider (0 = optimize the average only, i.e.
    "fastest overall"; 1 = optimize the worst-case leg, i.e. "fairest for
    everyone"). This is deliberately simple — two terms, one weight — so
    the slider has a real, explainable mathematical meaning instead of
    being decoration on top of an opaque score.
"""

import math
from typing import Callable, Dict, List, Tuple

import numpy as np
from scipy.optimize import minimize


def geographic_midpoint(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """V1 baseline: plain average lat/lon. Kept for comparison/demo purposes."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def generate_candidates(
    points: List[Tuple[float, float]],
    grid_size: int = 7,
    padding_ratio: float = 0.15,
) -> List[Tuple[float, float]]:
    """
    Lay a grid_size x grid_size grid of candidate meeting points across the
    bounding box of everyone's location, padded outward a bit so the winner
    isn't artificially forced onto the exact center of the raw bounding box.

    These are still raw coordinates, not verified places — routing.py and
    places.py are what turn the winning grid cell into an actual named
    area with real venues in it (see app.py's pipeline).
    """
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]

    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)

    lat_pad = max((lat_max - lat_min) * padding_ratio, 0.01)
    lon_pad = max((lon_max - lon_min) * padding_ratio, 0.01)

    lat_min, lat_max = lat_min - lat_pad, lat_max + lat_pad
    lon_min, lon_max = lon_min - lon_pad, lon_max + lon_pad

    candidates = []
    denom = max(grid_size - 1, 1)
    for i in range(grid_size):
        for j in range(grid_size):
            lat = lat_min + (lat_max - lat_min) * i / denom
            lon = lon_min + (lon_max - lon_min) * j / denom
            candidates.append((lat, lon))
    return candidates


def haversine_km(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Great-circle distance, used only by the straight-line fallback below."""
    R = 6371.0
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def straight_line_time_minutes(
    p1: Tuple[float, float], p2: Tuple[float, float], speed_kmh: float = 30.0
) -> float:
    """
    Straight-line ETA estimate. Used for Public transport, since no free
    transit router exists — routing.py flags any matrix built this way as
    estimated, and app.py must surface that to the user rather than
    presenting it as a real travel time.
    """
    return (haversine_km(p1, p2) / speed_kmh) * 60.0


def fairness_stats(times: List[float], slider: float) -> Dict[str, float]:
    """
    cost = (1 - slider) * mean(times) + slider * max(times)

    slider=0.0: pure average — minimizes total travel time across the group,
                indifferent to how unevenly it's distributed.
    slider=1.0: pure worst-case — minimizes the single longest journey,
                indifferent to everyone else's time.
    Values in between blend the two continuously.
    """
    arr = np.array([t for t in times if t is not None], dtype=float)
    if len(arr) == 0:
        return {"average": None, "max": None, "std": None, "cost": None}

    average = float(np.mean(arr))
    maximum = float(np.max(arr))
    std = float(np.std(arr))  # not part of the cost — kept only for an optional "see the numbers" view

    cost = (1.0 - slider) * average + slider * maximum

    return {"average": average, "max": maximum, "std": std, "cost": cost}


def rank_candidates(
    candidates: List[Tuple[float, float]],
    matrix: List[List[float]],
    slider: float,
) -> List[Dict]:
    """
    matrix[person_index][candidate_index] = minutes.
    Returns every candidate with its stats, sorted best-first (lowest cost
    first). Pure function of already-fetched travel times, so re-ranking
    when the slider moves needs zero network calls.
    """
    results = []
    for c_idx, candidate in enumerate(candidates):
        times = [matrix[p_idx][c_idx] for p_idx in range(len(matrix))]
        stats = fairness_stats(times, slider)
        results.append({"location": candidate, "times": times, **stats})

    results.sort(key=lambda r: (r["cost"] if r["cost"] is not None else float("inf")))
    return results


def best_meeting_point(
    matrix: List[List[float]],
    candidates: List[Tuple[float, float]],
    slider: float,
) -> Dict:
    """Convenience wrapper: rank everything, return the single winner."""
    ranked = rank_candidates(candidates, matrix, slider)
    return ranked[0] if ranked else {}


def refine_best_point(
    origins: List[Tuple[float, float]],
    starting_point: Tuple[float, float],
    slider: float,
    travel_time_fn: Callable[[List[Tuple[float, float]], Tuple[float, float]], List[float]],
    max_iter: int = 5,
) -> Dict:
    """
    Local continuous refinement around the grid-search winner using SciPy's
    Nelder-Mead simplex method. The grid search (rank_candidates) narrows
    the search to the right neighborhood cheaply; this step spends a small,
    capped number of extra routing calls (roughly 2-3x max_iter in 2D)
    nudging that point toward a lower-cost location the grid may have
    stepped over.

    travel_time_fn should be routing.get_travel_times, injected here rather
    than imported directly so optimization.py doesn't depend on routing.py.
    Each call this function makes hits that dependency, so max_iter directly
    controls how many extra network requests refinement costs.
    """

    def cost_fn(coords: np.ndarray) -> float:
        times = travel_time_fn(origins, (float(coords[0]), float(coords[1])))
        stats = fairness_stats(times, slider)
        return stats["cost"] if stats["cost"] is not None else 1e6

    result = minimize(
        cost_fn,
        x0=np.array(starting_point, dtype=float),
        method="Nelder-Mead",
        options={"maxiter": max_iter, "xatol": 1e-3, "fatol": 1e-2},
    )

    refined_point = (float(result.x[0]), float(result.x[1]))
    times = travel_time_fn(origins, refined_point)
    stats = fairness_stats(times, slider)
    return {"location": refined_point, "times": times, **stats}

