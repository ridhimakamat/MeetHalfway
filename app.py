"""
app.py — MeetHalfway's UI.

Two screens:
  1. Setup — everyone's name, their own travel mode, and a location picked
     from a Goa-bounded map (search or click — no free-text address field,
     so every location is a real point, not an ambiguous string).
  2. Result — one recommended area, everyone's time to get there, a map,
     and real places to go.

Every finished search is saved to SQLite (storage.py) and its id is
written into the page URL (?plan=...). That URL is the shareable link, and
it's also what makes results survive a page refresh or a dropped
connection: Streamlit's session_state lives only in server memory, so if
that session is lost, reloading the page with the plan id still in the URL
restores the exact same result instead of losing it.

Two additional, concrete fixes for results disappearing mid-session:
  - storage.py keeps the database file outside this directory, so writing
    it can't be mistaken by Streamlit's dev-server file watcher for a
    source-code change.
  - The result screen's map passes returned_objects=[] to st_folium, since
    it's informational only — without this, streamlit-folium can fire an
    extra automatic rerun right after the map's first paint.
Neither of these can be proven as *the* cause without reproducing it live,
but both are real, documented footguns that match the symptom, and fixing
them costs nothing.
"""

import html

import folium
import streamlit as st
from streamlit_folium import st_folium

import optimization
import places as places_module
import routing
import storage

st.set_page_config(page_title="MeetHalfway", page_icon=None, layout="centered")

MODE_OPTIONS = ["Car", "Walking", "Public transport"]
PURPOSE_OPTIONS = ["Café", "Food", "Study", "Shopping", "Outdoors"]

GOA_BOUNDS = routing.GOA_BOUNDS
GOA_CENTER = [15.35, 74.00]

# Fixed blend of average and worst-case travel time (see optimization.fairness_stats).
# 0.5 means the group's average and the single longest journey count equally —
# no slider exposed in the UI, this is just the formula's one tunable knob.
FAIRNESS_WEIGHT = 0.5

DEFAULT_PEOPLE = [
    {"name": "You", "lat": 15.4909, "lon": 73.8278, "label": "Panaji, North Goa", "mode": "Car"},
    {"name": "Friend 1", "lat": 15.2832, "lon": 73.9862, "label": "Margao, South Goa", "mode": "Car"},
    {"name": "Friend 2", "lat": 15.4028, "lon": 74.0120, "label": "Ponda, North Goa", "mode": "Car"},
    {"name": "Friend 3", "lat": 15.3955, "lon": 73.8157, "label": "Vasco da Gama, South Goa", "mode": "Car"},
]

storage.init_db()

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap');

#MainMenu, header, footer {visibility: hidden;}
html, body, [class*="css"] { font-family: 'Manrope', -apple-system, sans-serif; }

.block-container { max-width: 600px; padding-top: 2.5rem; padding-bottom: 3rem; }

/* Light palette pinned explicitly: the page colours below assume a white
   background, and in a dark browser/OS theme Streamlit would otherwise
   draw its own light text on top of it. */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: #ffffff; color: #18181b; }
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { color: #27272a; }
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label { color: #27272a; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: #52525b; }
[data-testid="stExpander"] summary p, [data-testid="stExpander"] summary span { color: #18181b; }
div.stButton > button[kind="secondary"] { background: #ffffff; }
div.stButton > button[kind="secondary"] p { color: #18181b; }
div.stButton > button[kind="primary"] p { color: #18181b !important; font-weight: 700; }
div[data-testid="stFormSubmitButton"] button p { color: #18181b !important; }

/* Dropdown menu (opens in a floating layer): white, thin border, light-grey hover. */
[data-testid="stSelectboxVirtualDropdown"], [data-testid="stSelectboxVirtualDropdown"] > div,
[role="listbox"], ul[role="listbox"] { background: #ffffff !important; color: #18181b !important; }
div:has(> [data-testid="stSelectboxVirtualDropdown"]),
div:has(> div > [data-testid="stSelectboxVirtualDropdown"]),
div:has(> [role="listbox"]), div:has(> ul[role="listbox"]) {
    background: #ffffff !important; border: 1px solid #e4e4e7 !important; border-radius: 10px !important;
    box-shadow: 0 4px 14px rgba(24,24,27,0.08) !important;
}
[role="option"], [role="option"] * { background-color: transparent !important; color: #18181b !important; }
[role="option"]:hover, [role="option"][aria-selected="true"] { background-color: #f4f4f5 !important; }

/* Inputs and dropdowns: white, soft border, grey-black border when clicked. */
[data-testid="stTextInputRootElement"],
[data-testid="stSelectbox"] div:has(> input),
div[data-baseweb="input"], div[data-baseweb="select"] > div {
    background: #ffffff !important; border: 1px solid #e4e4e7 !important; border-radius: 10px !important;
    box-shadow: none !important;
}
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stSelectbox"] div:has(> input):focus-within,
div[data-baseweb="input"]:focus-within, div[data-baseweb="select"] > div:focus-within {
    border-color: #52525b !important;
}
div[data-baseweb="base-input"] { background: #ffffff !important; border: none !important; }
[data-testid="stTextInputRootElement"] input, [data-testid="stSelectbox"] input,
[data-testid="stSelectbox"] [data-baseweb="select"] div, [data-testid="stSelectbox"] [data-baseweb="select"] span {
    color: #18181b !important; -webkit-text-fill-color: #18181b; background: transparent !important;
}
[data-testid="stTextInputRootElement"] input:disabled, [data-testid="stSelectbox"] input:disabled {
    color: #3f3f46 !important; -webkit-text-fill-color: #3f3f46 !important; opacity: 1 !important;
}
[data-testid="stTextInputRootElement"] input::placeholder { color: #71717a !important; -webkit-text-fill-color: #71717a; }
[data-testid="stSelectbox"] svg { fill: #18181b; }
div[data-baseweb="popover"] ul, div[data-baseweb="popover"] [role="listbox"] { background: #ffffff !important; }
div[data-baseweb="popover"] li, div[data-baseweb="popover"] [role="option"],
div[data-baseweb="popover"] li *, div[data-baseweb="popover"] [role="option"] * {
    color: #18181b !important; background-color: transparent;
}
div[data-baseweb="popover"] li:hover, div[data-baseweb="popover"] [role="option"]:hover { background-color: #f4f4f5 !important; }

.mh-title {
    text-align: center; font-size: 2.3rem; font-weight: 800;
    letter-spacing: -0.03em; margin-bottom: 0.2rem; color: #18181b;
}
.mh-subtitle { text-align: center; color: #52525b; font-size: 1.05rem; margin-bottom: 2.2rem; }

.mh-card {
    background: #ffffff; border: 1px solid #ececef; border-radius: 18px;
    padding: 1rem 1.25rem; margin-bottom: 1.4rem;
    box-shadow: 0 2px 10px rgba(24,24,27,0.05);
}

.mh-row-header {
    font-size: 0.72rem; font-weight: 700; color: #52525b;
    text-transform: uppercase; letter-spacing: 0.06em; padding: 0.4rem 0 0.3rem 0.2rem;
}

.mh-avatar {
    width: 34px; height: 34px; border-radius: 50%;
    background: #e4e4e7; border: 1px solid #a1a1aa;
    color: #18181b; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 0.95rem; margin-top: 0.35rem;
}

.mh-location-set { padding-top: 0.5rem; font-size: 0.92rem; color: #18181b; }
.mh-location-unset { padding-top: 0.5rem; font-size: 0.92rem; color: #52525b; font-style: italic; }

.mh-section-label {
    font-weight: 700; font-size: 0.95rem; color: #27272a; margin: 1.5rem 0 0.6rem 0;
}
.mh-section-hint { color: #52525b; font-size: 0.85rem; margin: -0.3rem 0 0.7rem 0; }

div.stButton > button {
    border-radius: 999px; font-weight: 700; border: 1px solid #e4e4e7;
    transition: all 0.15s ease;
}
div.stButton > button[kind="primary"] {
    background: #e4e4e7; border: 1px solid #52525b; color: #18181b;
}
div.stButton > button[kind="primary"]:hover { background: #d4d4d8; }
div[data-testid="stFormSubmitButton"] button {
    border-radius: 12px; font-weight: 700; border: 1px solid #52525b;
    background: #e4e4e7; color: #18181b;
}

.mh-eyebrow {
    text-align: center; color: #52525b; text-transform: uppercase;
    letter-spacing: 0.1em; font-size: 0.8rem; font-weight: 700; margin-top: 0.5rem;
}
.mh-area-name {
    text-align: center; font-size: 2.5rem; font-weight: 800;
    letter-spacing: -0.02em; margin: 0.2rem 0 1.2rem 0; color: #18181b;
}

.mh-person-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.6rem 0; border-bottom: 1px solid #f4f4f5; font-size: 1.02rem;
}
.mh-person-row:last-child { border-bottom: none; }
.mh-person-main { display: flex; flex-direction: column; }
.mh-person-location { font-size: 0.8rem; color: #52525b; margin-top: 0.15rem; font-weight: 400; }
.mh-time { font-variant-numeric: tabular-nums; color: #18181b; font-weight: 700; white-space: nowrap; }
.mh-mode-badge {
    font-size: 0.72rem; font-weight: 600; color: #3f3f46; background: #f4f4f5;
    border-radius: 999px; padding: 0.15rem 0.55rem; margin-left: 0.5rem;
}

.mh-summary { text-align: center; color: #3f3f46; margin: 0.7rem 0 1.5rem 0; font-weight: 500; }

.mh-warning-banner {
    background: #fef9e7; border: 1px solid #fde68a; color: #92400e;
    border-radius: 14px; padding: 0.8rem 1.1rem; font-size: 0.9rem; margin: 0 0 1.3rem 0;
}

.mh-place-row { padding: 0.55rem 0; border-bottom: 1px solid #f4f4f5; }
.mh-place-row:last-child { border-bottom: none; }
.mh-place-address { color: #52525b; font-size: 0.88rem; }
</style>
""",
    unsafe_allow_html=True,
)


def _get_query_param(name: str):
    try:
        value = st.query_params.get(name)
        return value[0] if isinstance(value, list) else value
    except Exception:
        try:
            values = st.experimental_get_query_params().get(name)
            return values[0] if values else None
        except Exception:
            return None


def _set_query_param(name: str, value: str) -> None:
    try:
        st.query_params[name] = value
    except Exception:
        try:
            st.experimental_set_query_params(**{name: value})
        except Exception:
            pass


def _clear_query_params() -> None:
    try:
        st.query_params.clear()
    except Exception:
        try:
            st.experimental_set_query_params()
        except Exception:
            pass


def init_state() -> None:
    defaults = {
        "stage": "setup",
        "people": [dict(p) for p in DEFAULT_PEOPLE],
        "purpose": "Café",
        "result": None,
        "restore_failed": False,
        "last_processed_click": None,
        "pending_people": None,
        "search_error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def maybe_restore_from_url() -> None:
    """If this session has no result in memory but the URL names a saved
    plan, reload it from SQLite — restores after a refresh or a dropped
    session, and is also exactly how a shared link opens for someone else."""
    if st.session_state.get("result"):
        return
    plan_id = _get_query_param("plan")
    if not plan_id:
        return
    plan = storage.load_plan(plan_id)
    if plan is None:
        st.session_state.restore_failed = True
        return
    st.session_state.result = plan
    st.session_state.stage = "result"


def _embed_html(markup: str, height: int) -> None:
    """st.iframe on current Streamlit; components.html (removed in newer
    releases) only as a fallback for older installs."""
    if hasattr(st, "iframe"):
        st.iframe(markup, height=height)
    else:
        import streamlit.components.v1 as components
        components.html(markup, height=height)


def render_copy_link_box() -> None:
    """A small HTML/JS widget that reads the browser's own current URL
    (which already includes ?plan=... once a search has run) and offers a
    one-click copy. Runs inside an iframe, so it reads window.parent's
    location, not its own — otherwise it would copy the iframe's address
    instead of the actual app page."""
    _embed_html(
        """
        <div style="display:flex; gap:8px; align-items:center; font-family:'Manrope',sans-serif;">
          <input id="mh-link" readonly
                 style="flex:1; padding:11px 14px; border-radius:12px; border:1px solid #e4e4e7;
                        font-size:0.88rem; color:#3f3f46; background:#fafafa; outline:none;">
          <button id="mh-copy-btn"
                  style="padding:11px 18px; border-radius:12px;
                         background:#e4e4e7; border:1px solid #52525b; color:#18181b;
                         font-weight:700; font-size:0.88rem; cursor:pointer;">Copy</button>
        </div>
        <script>
          const link = window.parent.location.href;
          document.getElementById('mh-link').value = link;
          const btn = document.getElementById('mh-copy-btn');
          btn.onclick = function () {
            navigator.clipboard.writeText(link);
            btn.innerText = 'Copied';
            setTimeout(() => { btn.innerText = 'Copy'; }, 1500);
          };
        </script>
        """,
        height=64,
    )


def start_search() -> None:
    """Validate the setup form, then hand off to the dedicated 'searching'
    screen. The slow network work deliberately does NOT run inside the
    button click on the setup screen: that screen contains a live map
    component, and a component firing its own rerun mid-search would abort
    the search and drop the result. The searching screen renders no map."""
    valid = []
    missing = []
    for i, p in enumerate(st.session_state.people):
        name = (p.get("name") or "").strip() or f"Person {i + 1}"
        if p.get("lat") is None or p.get("lon") is None:
            missing.append(name)
            continue
        valid.append({
            "name": name,
            "lat": p["lat"],
            "lon": p["lon"],
            "label": p.get("label") or f"{p['lat']:.4f}, {p['lon']:.4f}",
            "mode": p.get("mode") or "Car",
        })

    if missing:
        st.error(f"Set a location for: {', '.join(missing)}")
        return
    if len(valid) < 2:
        st.error("Add at least two people with a location set.")
        return

    st.session_state.pending_people = valid
    st.session_state.search_error = None
    st.session_state.stage = "searching"
    st.rerun()


def compute_plan(valid: list, purpose: str) -> dict:
    points = [(p["lat"], p["lon"]) for p in valid]
    modes = [p["mode"] for p in valid]

    candidates = optimization.generate_candidates(points, grid_size=7)
    matrix = routing.get_travel_time_matrix(points, candidates, modes=modes)

    best = optimization.best_meeting_point(matrix, candidates, FAIRNESS_WEIGHT)
    if not best:
        raise routing.RoutingError("Couldn't find a viable meeting point with these locations. Try different ones.")

    try:
        refined = optimization.refine_best_point(
            points,
            best["location"],
            FAIRNESS_WEIGHT,
            lambda o, c: routing.get_travel_times(o, c, modes),
            max_iter=5,
        )
        if refined.get("cost") is not None and (best.get("cost") is None or refined["cost"] < best["cost"]):
            best = refined
    except routing.RoutingError:
        pass  # refinement is a bonus; the grid winner is still a valid answer

    area_name = routing.reverse_geocode(best["location"])

    try:
        place_result = places_module.search_places_with_fallback(best["location"], purpose)
    except places_module.PlacesError:
        place_result = {"places": [], "radius_used_m": None, "expanded": True, "error": True, "error_detail": "request failed"}

    return {
        "people": valid,
        "points": [list(pt) for pt in points],
        "best": {
            "location": list(best["location"]),
            "times": best["times"],
            "average": best["average"],
            "max": best["max"],
            "std": best["std"],
        },
        "area_name": area_name,
        "purpose": purpose,
        "places": place_result,
        "fairness_weight": FAIRNESS_WEIGHT,
    }


def _fail_search(message: str) -> None:
    st.session_state.search_error = message
    st.session_state.pending_people = None
    st.session_state.stage = "setup"
    st.rerun()


def run_search() -> None:
    valid = st.session_state.get("pending_people")
    if not valid:
        st.session_state.stage = "setup"
        st.rerun()
        return

    try:
        payload = compute_plan(valid, st.session_state.purpose)
    except routing.RoutingError as e:
        _fail_search(str(e))
        return
    except Exception as e:  # never leave the user stuck on the searching screen
        _fail_search(f"Something went wrong while searching ({type(e).__name__}). Please try again.")
        return

    plan_id = storage.save_plan(payload)

    st.session_state.result = payload
    st.session_state.pending_people = None
    st.session_state.stage = "result"
    _set_query_param("plan", plan_id)
    st.rerun()


def render_location_picker() -> None:
    st.markdown('<div class="mh-section-label">Where is everyone?</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="mh-section-hint">Search for a place in Goa, or click directly on the map.</div>',
        unsafe_allow_html=True,
    )

    names = [(p.get("name") or "").strip() or f"Person {i + 1}" for i, p in enumerate(st.session_state.people)]
    active_idx = st.selectbox(
        "Setting location for", options=list(range(len(names))),
        format_func=lambda i: names[i], key="active_person_idx",
    )

    with st.form("goa_search_form", clear_on_submit=True):
        col_q, col_btn = st.columns([4, 1])
        query = col_q.text_input(
            "Search", placeholder="Search a place in Goa", label_visibility="collapsed",
        )
        submitted = col_btn.form_submit_button("Search", use_container_width=True)

    if submitted and query.strip():
        try:
            lat, lon, label = routing.geocode_in_goa(query.strip())
            st.session_state.people[active_idx]["lat"] = lat
            st.session_state.people[active_idx]["lon"] = lon
            st.session_state.people[active_idx]["label"] = label
            st.rerun()
        except routing.RoutingError as e:
            st.error(str(e))

    m = folium.Map(
        location=GOA_CENTER, zoom_start=10, min_zoom=9,
        max_bounds=True,
        min_lat=GOA_BOUNDS["lat_min"], max_lat=GOA_BOUNDS["lat_max"],
        min_lon=GOA_BOUNDS["lon_min"], max_lon=GOA_BOUNDS["lon_max"],
    )
    for i, p in enumerate(st.session_state.people):
        if p.get("lat") is not None:
            color = "red" if i == active_idx else "blue"
            folium.Marker(
                [p["lat"], p["lon"]], tooltip=names[i],
                icon=folium.Icon(color=color, icon="user"),
            ).add_to(m)

    map_state = st_folium(
        m, use_container_width=True, height=420,
        key="goa_picker_map", returned_objects=["last_clicked"],
    )

    clicked = map_state.get("last_clicked") if map_state else None
    if clicked:
        click_key = (round(clicked["lat"], 5), round(clicked["lng"], 5))
        if st.session_state.get("last_processed_click") != click_key:
            lat = min(max(clicked["lat"], GOA_BOUNDS["lat_min"]), GOA_BOUNDS["lat_max"])
            lon = min(max(clicked["lng"], GOA_BOUNDS["lon_min"]), GOA_BOUNDS["lon_max"])
            with st.spinner("Looking up that location..."):
                label = routing.reverse_geocode((lat, lon))
            st.session_state.people[active_idx]["lat"] = lat
            st.session_state.people[active_idx]["lon"] = lon
            st.session_state.people[active_idx]["label"] = label
            st.session_state.last_processed_click = click_key
            st.rerun()


def render_setup(searching: bool = False) -> None:
    if not searching and st.session_state.get("search_error"):
        st.error(st.session_state.search_error)
        st.session_state.search_error = None

    if st.session_state.get("restore_failed"):
        st.warning("That link has expired or doesn't exist anymore. Start a new plan below.")
        st.session_state.restore_failed = False

    st.markdown('<div class="mh-title">MeetHalfway</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="mh-subtitle">Find a place everyone can actually reach.</div>',
        unsafe_allow_html=True,
    )

    header_cols = st.columns([0.5, 1.6, 2.3, 1.7, 0.45])
    header_cols[1].markdown('<div class="mh-row-header">Name</div>', unsafe_allow_html=True)
    header_cols[2].markdown('<div class="mh-row-header">Location</div>', unsafe_allow_html=True)
    header_cols[3].markdown('<div class="mh-row-header">Travel mode</div>', unsafe_allow_html=True)

    remove_index = None
    for i, person in enumerate(st.session_state.people):
        initial = (person.get("name") or "?").strip()[:1].upper() or "?"
        col_avatar, col_name, col_location, col_mode, col_remove = st.columns(
            [0.5, 1.6, 2.3, 1.7, 0.45]
        )
        col_avatar.markdown(f'<div class="mh-avatar">{initial}</div>', unsafe_allow_html=True)
        person["name"] = col_name.text_input(
            "Name", value=person.get("name", ""), key=f"name_{i}",
            placeholder="Name", label_visibility="collapsed", disabled=searching,
        )
        if person.get("lat") is not None:
            col_location.markdown(
                f'<div class="mh-location-set">{html.escape(person.get("label") or "")}</div>', unsafe_allow_html=True,
            )
        else:
            col_location.markdown('<div class="mh-location-unset">Pick below</div>', unsafe_allow_html=True)
        current_mode = person.get("mode", "Car")
        person["mode"] = col_mode.selectbox(
            "Mode", MODE_OPTIONS,
            index=MODE_OPTIONS.index(current_mode) if current_mode in MODE_OPTIONS else 0,
            key=f"mode_{i}", label_visibility="collapsed", disabled=searching,
        )
        if len(st.session_state.people) > 2 and col_remove.button("×", key=f"remove_{i}", disabled=searching):
            remove_index = i

    if remove_index is not None:
        st.session_state.people.pop(remove_index)
        st.session_state.pop("active_person_idx", None)  # indices shifted; let it reset to 0
        st.rerun()

    if st.button("Add person", disabled=searching):
        st.session_state.people.append({"name": "", "lat": None, "lon": None, "label": None, "mode": "Car"})
        st.session_state.active_person_idx = len(st.session_state.people) - 1
        st.rerun()

    if any(p.get("mode") == "Public transport" for p in st.session_state.people):
        st.caption(
            "Note: no free transit routing service is available, so travel times for "
            "anyone using public transport will be a straight-line estimate, not a real schedule."
        )

    if not searching:
        render_location_picker()

    if not searching:
        st.markdown('<div class="mh-section-label">What are you meeting for?</div>', unsafe_allow_html=True)
        purpose_cols = st.columns(len(PURPOSE_OPTIONS))
        for col, opt in zip(purpose_cols, PURPOSE_OPTIONS):
            is_selected = st.session_state.purpose == opt
            if col.button(
                opt, key=f"purpose_{opt}",
                type="primary" if is_selected else "secondary",
                use_container_width=True, disabled=searching,
            ):
                st.session_state.purpose = opt
                st.rerun()

    st.write("")
    if st.button("Find our spot", type="primary", use_container_width=True, disabled=searching):
        start_search()

    if searching:
        st.caption("Finding the fairest spot. This can take up to a minute on the free map servers.")
        with st.spinner("Checking travel times and looking for places..."):
            run_search()


def _fmt_distance(meters: float) -> str:
    if meters < 1000:
        return f"{int(round(meters / 10.0) * 10)} m"
    return f"{meters / 1000.0:.1f} km"


def render_result() -> None:
    res = st.session_state.result
    best = res["best"]
    people = res["people"]
    purpose = res["purpose"]

    st.markdown('<div class="mh-eyebrow">Your meeting spot</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="mh-area-name">{res["area_name"]}</div>', unsafe_allow_html=True)

    if any(p["mode"] == "Public transport" for p in people):
        st.markdown(
            '<div class="mh-warning-banner">No free transit routing service is available. '
            'Times marked "estimated" below are a straight-line approximation, not a real schedule.</div>',
            unsafe_allow_html=True,
        )

    rows_html = ""
    for person, t in zip(people, best["times"]):
        t_str = f"{round(t)} min" if t is not None else "N/A"
        if person["mode"] == "Public transport":
            t_str += " (estimated)"
        location_label = html.escape(person.get("label") or "")
        rows_html += (
            '<div class="mh-person-row">'
            '<div class="mh-person-main">'
            f'<span>{html.escape(person["name"])}<span class="mh-mode-badge">{person["mode"]}</span></span>'
            f'<span class="mh-person-location">{location_label}</span>'
            "</div>"
            f'<span class="mh-time">{t_str}</span>'
            "</div>"
        )
    st.markdown(f'<div class="mh-card">{rows_html}</div>', unsafe_allow_html=True)

    valid_times = [t for t in best["times"] if t is not None]
    if valid_times:
        st.markdown(
            f'<div class="mh-summary">Everyone {round(max(valid_times))} minutes or less</div>',
            unsafe_allow_html=True,
        )

    with st.expander("See the numbers"):
        st.write(f"Average: {best['average']:.1f} min")
        st.write(f"Worst case: {best['max']:.1f} min")
        st.write(f"Spread (std dev): {best['std']:.1f} min")
        st.caption(
            f"Balances average and worst-case travel time equally "
            f"(fixed weighting, w={res.get('fairness_weight', FAIRNESS_WEIGHT)})."
        )

    place_result = res["places"]
    meet_point = tuple(best["location"])

    def _dist_m(p):
        return optimization.haversine_km(meet_point, (p["lat"], p["lon"])) * 1000.0

    shown = sorted(place_result.get("places") or [], key=_dist_m)[:6]

    m = folium.Map(location=best["location"], zoom_start=13)
    for person, pt in zip(people, res["points"]):
        folium.Marker(pt, tooltip=person["name"], icon=folium.Icon(color="blue", icon="user")).add_to(m)
    folium.Marker(
        best["location"], tooltip=res["area_name"], icon=folium.Icon(color="red", icon="star"),
    ).add_to(m)
    for p in shown:
        folium.Marker(
            [p["lat"], p["lon"]],
            tooltip=f'{p["name"]} ({_fmt_distance(_dist_m(p))})',
            icon=folium.Icon(color="green", icon="info-sign"),
        ).add_to(m)
    m.fit_bounds([list(pt) for pt in res["points"]] + [list(best["location"])] + [[p["lat"], p["lon"]] for p in shown],
                 padding=(30, 30))
    # This map is informational only — returned_objects=[] stops it from
    # firing any automatic rerun on its own (e.g. right after first paint),
    # which would otherwise be an unnecessary source of instability here.
    st_folium(m, use_container_width=True, height=380, key="result_map", returned_objects=[])
    st.caption("Map data (c) OpenStreetMap contributors")

    st.markdown(
        f'<div class="mh-section-label">{html.escape(purpose)} near {html.escape(res["area_name"])}</div>',
        unsafe_allow_html=True,
    )

    if shown:
        nearest = shown[0]
        st.markdown(
            f'Closest to the meeting point: **{nearest["name"]}**, '
            f'{_fmt_distance(_dist_m(nearest))} away.'
        )
        if place_result.get("expanded"):
            st.caption(
                f"Nothing turned up right at the meeting point, so we widened the search to "
                f"{place_result['radius_used_m'] / 1000:.0f} km."
            )
        places_html = ""
        for p in shown:
            rating = f" - {p['rating']} rating" if p.get("rating") else ""
            address = html.escape(p.get("address") or "")
            places_html += (
                f'<div class="mh-place-row"><b>{html.escape(p["name"])}</b>{rating} '
                f'<span class="mh-place-address">({_fmt_distance(_dist_m(p))} away)</span><br>'
                f'<span class="mh-place-address">{address}</span></div>'
            )
        st.markdown(f'<div class="mh-card">{places_html}</div>', unsafe_allow_html=True)
    elif place_result.get("error"):
        st.warning(
            "The venue lookup service didn't respond, so we can't list places right now. "
            "Your meeting spot above is still correct. Press Edit locations and search again in a minute."
        )
        if place_result.get("error_detail"):
            st.caption(f"Details: {place_result['error_detail']}")
    else:
        st.info(
            f"We couldn't find any {purpose.lower()} spots near {res['area_name']}, "
            f"even after widening the search. Try a different purpose, or different locations."
        )

    st.markdown('<div class="mh-section-label">Share this plan</div>', unsafe_allow_html=True)
    render_copy_link_box()
    st.caption("This link saves and reopens the plan — refreshing the page or sending it to someone else both just work.")

    st.write("")
    if st.button("Edit locations", use_container_width=True):
        st.session_state.people = [dict(p) for p in people]
        st.session_state.purpose = purpose
        st.session_state.stage = "setup"
        st.session_state.pop("active_person_idx", None)
        _clear_query_params()
        st.rerun()


init_state()
maybe_restore_from_url()

# Everything is drawn inside one placeholder container. When the screen
# changes (setup -> searching -> result) the old screen is replaced as a
# whole, instead of lingering as faded leftovers below the new one.
_page = st.empty()
with _page.container():
    if st.session_state.stage == "setup":
        render_setup()
    elif st.session_state.stage == "searching":
        render_setup(searching=True)
    else:
        render_result()
