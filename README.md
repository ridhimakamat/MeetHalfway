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

### The searching screen

Pressing "Find our spot" no longer runs the slow network work inside the
button click. It validates, switches to a dedicated "searching" stage that
renders no map component, and runs the search there. The setup screen's
map component can trigger its own reruns, and a rerun mid-search aborts it
and loses the result; the searching screen removes that possibility. Any
unexpected error returns to setup with a message instead of hanging.

### Colours and theme

The page colours assume a white background, so `.streamlit/config.toml`
pins Streamlit's light theme and `app.py` pins the main text colours
explicitly. (This file only sets the theme. It is unrelated to the removed
`runOnSave` setting.) Secondary greys were darkened for contrast.

### Persistence and shareable links

Every finished search is saved to a local SQLite file and its id is
written into the page URL as `?plan=<id>`. That URL is the shareable
link — open it anywhere and the exact same result loads, with no new
geocoding or routing calls.

### Why results were disappearing, and what changed

Three concrete, compounding fixes, in order of how much they likely
mattered:

1. **Forward-geocoding is gone from the search path entirely.** The old
   text-entry flow geocoded every person's address inside `run_search()`,
   serially, rate-limited to ~1/second by Nominatim's usage policy — for
   four people that alone was several seconds of blocking network calls
   before routing even started. Now locations are resolved interactively
   during setup (search or click, one at a time), so by the time "Find our
   spot" is pressed, every person already has coordinates. `run_search()`
   does zero forward-geocoding. This is the single biggest reduction in
   how long the app sits blocked on external network calls — and the
   likeliest actual cause of a slow request getting dropped mid-flight,
   since Streamlit's `session_state` lives only in server memory: a
   dropped connection or server restart during a long blocking call wipes
   it, landing back on a blank setup screen with nothing saved yet.
2. **The database file moved out of the watched directory.**
   `storage.py` now defaults to `~/.meethalfway/meethalfway.db` instead of
   sitting next to `app.py`. Streamlit's dev-server file watcher monitors
   the app's own directory for source changes to trigger hot-reloads;
   every finished search used to write to a file in that same directory.
   Whether or not that was actually triggering reloads in your setup,
   it's a known footgun and now costs nothing to avoid.

   (An earlier version of this fix also shipped a `.streamlit/config.toml`
   setting `runOnSave = false` as extra defense. That's been removed — it
   caused its own problem, silently disabling auto-reload-on-save for
   anyone who had it enabled, which looked like "my file changes aren't
   taking effect" when testing. If you ever replace these files while the
   app is already running, fully stop (Ctrl+C) and restart
   `streamlit run app.py` rather than relying on hot-reload, to guarantee
   there's no stale code left in memory.)
3. **The result map no longer triggers its own reruns.** `st_folium` on
   the result screen now passes `returned_objects=[]`, since that map is
   informational only. Without it, streamlit-folium can fire an
   automatic extra rerun right after the map's first paint — harmless
   on its own since session state would normally survive it, but one
   less moving part during the exact moment right after a result renders.

None of these can be proven as *the* cause without reproducing the exact
failure live, but all three are real, and the combination should make
results substantially more robust. If it still happens, the most useful
next step is to open the browser's developer console (Network tab) right
when it happens and check whether the WebSocket connection to Streamlit
actually drops — that would confirm it's a connection issue rather than
something in the app logic.

Override where the database file lives with `MEETHALFWAY_DB_PATH` in
`.env` — useful for a mounted volume on hosting with an otherwise
ephemeral filesystem.

## Playing nice with public infrastructure

Nominatim, the OSRM demo server, and the Overpass API are shared,
volunteer-run community infrastructure, not a product with an SLA:

- **Nominatim**: max ~1 request/second, and requires a real identifying
  User-Agent (set `NOMINATIM_USER_AGENT` in `.env` with actual contact
  info before deploying anywhere). `routing.py` rate-limits geocoding,
  reverse-geocoding, and the Goa-bounded search to comply.
- **OSRM demo server**: driving-only on `router.project-osrm.org`; walking
  routes go to a separate community demo. Neither is meant for production
  traffic — self-host OSRM with your own OSM extract if you need
  reliability or higher volume.
- **Overpass API**: one query per search (with up to two extra only if the
  radius needs to widen) — avoid polling it in a loop.
- **Attribution**: OpenStreetMap's license (ODbL) requires crediting
  "OpenStreetMap contributors" wherever the map or data is shown — already
  included as a caption under the map in `app.py`.

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

## Ideas for a V6

- Snap grid candidates to real places (via Overpass) *before* scoring.
- Let each person set their own "acceptable max travel time" as a hard
  constraint.
- An expiry or cleanup policy for old saved plans in `storage.py`.
- A real Goa administrative boundary (GeoJSON + point-in-polygon) instead
  of the approximate bounding rectangle.
- Self-host OSRM + Nominatim + an Overpass mirror for a version you'd
  actually put in front of real users.
