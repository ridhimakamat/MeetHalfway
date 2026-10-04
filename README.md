# MeetHalfway

**Four people need to meet. Find somewhere everyone can actually reach.**

Two screens. Everyone gets a name, their own travel mode, and a location
picked from a map of Goa (search or click — no typed address field). Get
back one recommended area, everyone's travel time to it, and a short list
of real places to go — with a link that saves and reopens the exact same
plan.

Runs entirely on free, open infrastructure — no API keys, no billing
account, no Google Cloud project.

## The stack

| Layer | Tool | Use |
|---|---|---|
| UI | Streamlit | Two-screen flow in `app.py` |
| Geocoding | Nominatim (OpenStreetMap) | Search box on the map picker, bounded to Goa; reverse-geocoding the winning area and map clicks |
| Travel times | OSRM Table service | Time matrix, grouped per person's own travel mode |
| Places | Overpass API (OpenStreetMap) | Cafés, restaurants, study spots, etc. |
| Map | Folium + OpenStreetMap tiles | Location picker (setup) and result map |
| Calculations | NumPy | Fairness stats |
| Optimization | SciPy (`optimize.minimize`) | Local refinement around the grid winner |
| Persistence | SQLite (standard library) | Saved plans, reopened via a link |

## Setup

```bash
cd MeetHalfway
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env   # optional — defaults work out of the box
```

`.env` is entirely optional. Fill it in only if you want to point at a
self-hosted OSRM/Overpass instance, set a proper Nominatim User-Agent
before real-world use, or move the SQLite file (see "Persistence and
shareable links" below).

## Run

```bash
streamlit run app.py
```

## The flow

```
                  app.py
                    │
     ┌──────────┬───┴────┬──────────┐
     ↓          ↓        ↓          ↓
 routing   optimization places   storage
     │          │        │          │
     └──────────┴───┬────┴──────────┘
                    ↓
             One result screen
```

**Screen 1 — Setup.** Each person gets a row: name, their own travel mode,
and a location set via the map picker below the list (search a place in
Goa, or click the map directly). Pick what you're meeting for.
**Screen 2 — Result.** One area name (reverse-geocoded, not raw
coordinates), each person's time next to their own mode and location, a
map, a short list of real venues, and a link to save or send.

| File | Responsibility |
|---|---|
| `app.py` | The two screens, including the Goa-bounded map location picker |
| `routing.py` | Geocoding (general and Goa-bounded), reverse-geocoding, the per-person-mode travel-time matrix |
| `optimization.py` | Generate candidate points, score by a fixed fairness formula, refine with SciPy |
| `places.py` | Search real venues around the winning area, widening the radius when needed |
| `storage.py` | Save/load a finished plan by id (SQLite) |

### Picking a location on the map

Locations aren't typed — they're set by searching or clicking:

- **Search** (`routing.geocode_in_goa`) uses Nominatim's `viewbox` +
  `bounded=True` parameters, which exclude results outside Goa entirely
  rather than merely preferring them — searching "Panaji" won't return a
  same-named place in another state.
- **Click** reads `st_folium`'s `last_clicked` coordinates, clamps them
  defensively into Goa's bounding box (belt-and-braces alongside the map's
  own `max_bounds`), and reverse-geocodes them into a readable label.
- The map itself (`folium.Map(..., max_bounds=True, min_lat=..., ...)`) is
  constrained to Goa, so panning or zooming out doesn't wander off into
  the Arabian Sea or Karnataka.
- A "Setting location for" selector controls which person the next search
  or click applies to; placed pins for everyone else stay visible in blue,
  the active person's pin in red.

The bounding box (`GOA_BOUNDS` in `routing.py`) is an approximate
rectangle covering the state with a small margin — not an exact
administrative polygon. Close enough to keep results on-topic; a precise
boundary would need an actual Goa GeoJSON shape and a point-in-polygon
check.

### Per-person travel modes

Each person picks Car, Walking, or Public transport independently.
`routing.get_travel_time_matrix` groups people by mode so everyone on the
same mode still shares one batched OSRM call, then reassembles a single
matrix in the original order — one person driving and another walking
costs two OSRM calls total, not one per person. Verified directly: two
people on Car and one on Walking produces exactly one `car` call and one
`foot` call, with each row landing back in its original position.

Public transport has no free routing equivalent, so it falls back to a
straight-line distance estimate. This is disclosed with a banner on the
result screen and an "(estimated)" tag next to that person's time — never
presented as a real travel time.

### The fairness formula

For each candidate location:

```
cost = (1 - w) * mean(travel_times) + w * max(travel_times)
```

`w` is fixed at `0.5` (`FAIRNESS_WEIGHT` in `app.py`) — the group's average
and the single longest journey count equally. No slider: the formula is
simple enough to state outright, and a fixed default keeps the result
screen to one clear number per person. Visible under "See the numbers" on
the result screen; change `FAIRNESS_WEIGHT` in `app.py` for a different
default.

### Two-stage optimization

1. **Grid search** (`generate_candidates` + `rank_candidates`): lay a 7x7
   grid across the group's bounding box, score every point in one batched
   OSRM call per mode, keep the best.
2. **Local refinement** (`refine_best_point`): SciPy's Nelder-Mead simplex
   method nudges that winning point within its neighborhood, spending a
   handful of extra OSRM calls (capped by `max_iter`) to find a lower-cost
   spot the grid resolution might have stepped over. Only kept if it beats
   the grid winner.

### Venue results

`places.py` fetches a pool of matching venues, drops duplicates (a cafe is
often mapped as both a point and a building), and sorts them by distance
from the meeting point. The result screen names the closest one, lists the
nearest six with distances, and pins them on the map in green. If Overpass
fails outright the screen says the lookup service didn't respond, rather
than claiming nothing exists nearby; the primary instance falls back to a
mirror before giving up.

### Persistence and shareable links

Every finished search is saved to a local SQLite file and its id is
written into the page URL as `?plan=<id>`. That URL is the shareable
link — open it anywhere and the exact same result loads, with no new
geocoding or routing calls.

## Known limitations

- **No free transit routing.** Public transport mode falls back to a
  straight-line distance estimate — flagged clearly in the UI.
- **OSM place data is patchier than Google's.** No ratings or review
  counts in most areas, and opening-hours coverage is inconsistent.
- **Public demo servers can be slow or rate-limit you** under heavy use —
  fine for a personal project, not for scaling without self-hosting.
- **Goa's boundary is an approximate rectangle**, not an exact polygon —
  a search or click right at the state's edge may behave slightly
  differently than a strict administrative boundary would suggest.
- **SQLite is a single file on one machine.** Fine for a personal
  deployment; multiple instances behind a load balancer need shared
  storage or a real hosted database.
- **The clipboard "Copy" button needs a secure context** (HTTPS, or
  `localhost` during development).

