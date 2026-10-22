# GreenMile

**Last-mile route planning with live re-planning.** GreenMile plans delivery routes for a fleet,
then lets you break a truck down or add a new order in the middle of the day and shows how
three recovery strategies cope, on a live map with moving trucks.

Built for the NEXAI 2026 hackathon, Track 4.3 (Last-Mile Route Planner with Live Re-planning),
on the Solomon VRPTW benchmark.

## Highlights

- **Real optimiser.** Capacity and time-window routing with Google OR-Tools. The static solve is within
  about 0.3% of the published best-known distances on C101, R101 and RC101 (25 customers).
- **Live disruptions.** Take a truck offline or add an order at any minute. Completed deliveries stay
  locked, a broken truck gets no new work, and the rest of the day is re-planned from where every truck
  actually is.
- **Three strategies compared on the same event.** Do nothing, cheapest insertion and full OR-Tools
  re-plan each run on their own copy of the fleet, with before/after KPIs.
- **Road distances.** Plan on real driving distances (OSRM) around any point on the map, with trucks and
  customers snapped to the nearest road. Times are in minutes at a chosen average speed.
- **Fleet view.** Every truck listed with what it is doing, its ETA and load, and status-coloured truck
  badges that move along the roads while the simulation plays.
- **Honest benchmark mode.** Straight-line mode keeps the original Solomon metric so results stay
  comparable with the literature.

## Quick start

Requires a recent Python 3 (developed and tested on 3.13).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt    # Windows; on Linux/macOS use .venv/bin/python
.venv/Scripts/python -m uvicorn api:app --port 8000
```

Open http://127.0.0.1:8000 (on Windows, `start_dashboard.ps1` does the same). The street map and road
routing need an internet connection; the plain x/y map and straight-line mode work offline.

## Using the dashboard

1. Pick a dataset (R101, C101 or RC101), leave **Distance: Road** and **Avg speed: 25 km/h**, and press
   **Load & solve**. The first plan takes a few seconds.
2. Press **Play**. Time runs at 1 simulated minute per real second by default (2, 4, 10 and 25 min/s
   are available). Drag the slider to jump around the day.
3. Watch the **Fleet** panel and the truck badges: moving trucks are green-yellow, servicing violet,
   waiting amber, broken red. Idle trucks wait at the depot and are summarised as "N idle". Click a truck
   or a fleet row for its card, or open the vehicle view for its cargo and orders.
4. Under the map, **Change the day**: take a vehicle offline, or add a new order (use the ⌖ button to
   click its location on the map). All three strategies re-plan.
5. Open the **strategy comparison** (third sidebar icon) for delivered, unassigned, late, distance and
   recovery time per strategy.
6. The 📍 button places the depot at an exact point on the street map and re-plans around it. The
   default depot is on Jimmy Carter Road at VIT Vellore.

Pages (sidebar, and the logo opens Home): **Home** is the landing page with a live briefing (delivery
progress, fleet status, next deadlines and which strategy is winning). **Map** is the dashboard: map with
moving trucks, **Change the day** under it, and the Fleet list on the side. **Orders** lists every order
with status, truck, window, planned arrival, slack and parcel weight/volume (filter, search, sort).
**Book** is a three-step booking page (search or pin a place, parcel size, delivery time) that shows how
each strategy handles the new order and how much space each truck has. **Trucks** shows one truck's cargo
and orders. **Analytics** charts orders delivered, orders left unassigned, fleet activity and delivery per
strategy, with the event history. **Compare** shows the three strategies side by side.

Trucks light up by state: broken = light red, idle = dim white, in use = bright yellow headlight.
Parcel sizes: one load unit is 5 kg and 0.03 m3, so Small = 25 kg / 0.15 m3, Medium = 50 kg / 0.30 m3,
Large = 100 kg / 0.60 m3, and a 200-unit truck carries 1000 kg / 6 m3. A truck's load is the parcels still
on board (assigned, not yet delivered).

Switch **Distance** to Straight-line to reproduce the benchmark metric exactly.

## How it works

```
Solomon file ──► parser ──► Problem (+ distance model)
                               │
                  OR-Tools VRPTW (capacity + time windows, drop penalties)
                               │
                       Comparison: 3 independent fleets
                               │  event: breakdown / new order
          ┌────────────────────┼─────────────────────┐
     do nothing        cheapest insertion       OR-Tools re-plan
          └────────────────────┼─────────────────────┘
                               │  KPIs (before / after / delta)
                           FastAPI ──► dashboard (Leaflet + Plotly)
```

| Strategy | What it does |
|---|---|
| Do nothing | Applies the event and leaves healthy routes alone. Stranded and new orders stay unassigned. |
| Cheapest insertion | Tries every position in every healthy truck's remaining route and picks the feasible insertion that adds the least distance. Fast, but never reshuffles existing stops. |
| OR-Tools re-plan | Re-solves the remaining orders from each truck's current position and remaining capacity (2 second search limit). |

Rules that apply throughout: delivered stops are frozen, service already in progress is preserved,
solver costs are integers (tenths of a unit), and each strategy keeps its own history so results never
overwrite each other.

## Distance and time model

- **Straight-line (benchmark) mode.** Euclidean distance between Solomon points; travel time equals
  distance.
- **Road mode (default in the dashboard).** The Solomon grid is placed on a real map at 80 m per unit
  around the depot. OSRM supplies driving distances, and every customer, truck and the depot is snapped to
  the nearest drivable road (so nothing sits in a lake). One time unit is read as **one minute of driving at
  the chosen average speed**, so a 4 km leg at 25 km/h takes about 10 minutes. Distances show in km.
- Trucks can still show a late "back at depot" time: Solomon customer windows are spread across the whole
  230-minute day and trucks wait for them. The fleet panel therefore also shows each truck's total driving
  minutes.
- Only drivable roads are used, so rail tracks are crossed only at real crossings or bridges. Private
  campus roads may be avoided by the routing service.

## KPIs

`distance` (executed plus remaining planned, including depot returns), `late_deliveries`,
`dropped_orders` (currently unassigned), `served_orders` and `solve_time_sec` (recovery time for the latest
event). Lower distance is not automatically better: do nothing often drives less because it leaves orders
undelivered. Full definitions are in [docs/TECHNICAL_NOTES.md](docs/TECHNICAL_NOTES.md).

## API

| Endpoint | Purpose |
|---|---|
| `POST /load` | Load and solve a dataset. Body: `dataset` (C101, R101, RC101), `reset`, `routing` (`road` or `euclid`), `city` (`vit`, `vellore`, `chennai`, `bengaluru`), `speed_kmh` (5-80, road mode), optional `depot_lat` and `depot_lng`. |
| `GET /state?t=` | Full state at simulated minute `t` for all three strategies: trucks, customers, routes, KPIs and events. |
| `POST /event` | Apply an event at the current time: `{"event_type": "breakdown", "vehicle_id": 3, ...}` or `{"event_type": "new_order", "x": .., "y": .., "demand": .., "ready": .., "due": .., ...}`, plus `time`, `session_id`, `revision`. |
| `GET /kpis` | Before/after KPIs for every event. |
| `GET /road?lat1=&lng1=&lat2=&lng2=` | Cached road geometry between two points (used by the street map). |
| `GET /timeseries?step=` | Orders delivered, unassigned, late, distance and fleet activity per strategy, sampled every `step` minutes (used by Analytics). |
| `GET /geocode?q=&lat=&lng=` | Place search for the booking page (OpenStreetMap, searched on the server, cached). |

The app keeps one in-memory session per process, so everyone using the same server shares the same simulation.

## Project layout

```
api.py          FastAPI app and state responses
parser.py       Solomon file parser
solver.py       OR-Tools static and dynamic VRPTW solvers
sim.py          Fleet state, events, locked history, re-planning
baselines.py    Do-nothing and cheapest-insertion strategies
comparison.py   Runs the same event on three independent fleets
kpis.py         KPI calculation
metric.py       Distance models: straight-line and OSRM road distances
benchmark.py    Static solve vs best-known values
static/         Dashboard (HTML, CSS, JS, vendored Leaflet and Plotly)
data/solomon/   The 56 Solomon instances
tests/          pytest suite
docs/           Detailed technical notes
```

## Tests and benchmark

```bash
.venv/Scripts/python -m pytest -q          # 136 tests, about 90 seconds
.venv/Scripts/python benchmark.py          # static solve vs best-known values
```

| Dataset | Ours | Best-known | Gap | Vehicles |
|---|---|---|---|---|
| C101 | 191.7 | 191.3 | +0.21% | 3 / 3 |
| R101 | 619.0 | 617.1 | +0.31% | 8 / 8 |
| RC101 | 462.3 | 461.1 | +0.26% | 4 / 4 |

These are 25-customer instances. Our distances are rounded to tenths, so gaps are approximate, and the
best-known values should be confirmed against the SINTEF Solomon page before being quoted. Road mode
results are not comparable with published Solomon values.

## Deploy

- **Render:** New > Blueprint > select this repository (uses `render.yaml`).
- **Hugging Face Spaces:** New Space > Docker SDK, push this repository (uses `Dockerfile`, port 7860).
- Free tiers sleep when idle, so open the URL once before a live demo. The street map and road routing
  call OpenStreetMap tiles and the public OSRM demo server, which are meant for light use.

## Limitations

- The street map is the Solomon grid placed on a real location, not real delivery data, and route lines
  follow roads only in road mode.
- Road mode implies a modelling choice for speed (default 25 km/h); changing it changes the plans.
- The dashboard uses the first 25 customers of each instance.
- One shared in-memory session per server process; no accounts or persistence.
- Weather and traffic events are not implemented.

## Credits

Solomon VRPTW instances (Solomon, 1987) as distributed by SINTEF. Routing by Google OR-Tools. Road
distances from OSRM and map data from OpenStreetMap contributors. Maps with Leaflet, charts with Plotly.
The dashboard styling was inspired by the Haulix dashboard design by Phenomenon Studio on Dribbble; this
project is not affiliated with them.

A demo walkthrough script is in [DEMO.md](DEMO.md). Every change, in plain words, is in [CHANGELOG.md](CHANGELOG.md).
