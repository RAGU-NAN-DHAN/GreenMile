# Last-Mile Router — Steps 1–5

Solomon dataset loading for a 25-customer routing demo. This step includes the
Python environment, input models, parser, source datasets, and parser tests.
The static solver, live event simulation, strategy comparisons, and browser
dashboard are implemented. Deployment and a backup video remain future work.

## Open the visual simulation (S5)

From the project folder, run:

```powershell
.\.venv\Scripts\python.exe api.py
```

Alternatively run `start_dashboard.ps1` from any working directory. Open
<http://127.0.0.1:8000> in a browser. Keep the server terminal open; Ctrl+C stops it.
If another instance is already running on port 8000, open that URL rather than
launching a second instance. The server binds only to the local computer.

1. Select R101 and click **Load & solve**. C101 and RC101 are also available.
2. Press **Play** or move the slider to **90**. Speed controls advance playback.
3. Choose an active vehicle (for example vehicle 14 if active) and click
   **Break selected vehicle**. Playback pauses while all three strategies recover.
4. Move to **95**, click **Choose location on map**, then click within the plot.
   Edit demand, service duration, and delivery window, then click **Add order & compare**.
5. Switch **Map strategy** to compare route plans. KPI cards show the current
   projection and latest event's immediate before/after deltas; the table compares
   all three methods. The event log records the same disruptions across fleets.
6. Move backward to inspect history. Event controls disable before the latest
   committed event; **Return to latest event** returns to an editable time.

Solid route lines show remaining travel; faint dotted lines preserve travelled
paths. Customer colours distinguish pending, servicing, served, and unassigned.
Triangle markers show vehicles; red crosses show broken vehicles. Idle vehicles at
the depot remain selectable in the dropdown but are not drawn over the depot.
Plotly uses equal x/y units; these are synthetic coordinates, not a geographic map.
The layout adapts to narrow windows by putting the controls below the map.

All plot assets are local (`static/vendor/plotly.min.js`). No CDN connection is
needed after setup. Reloading the page restores the current session. Loading a
dataset after events requires confirmation and resets all histories. Closing the
server loses the session; this is an in-memory demo, not persistent dispatch storage.

### API contract

| Endpoint | Request | Result |
|---|---|---|
| `POST /load` | `{"dataset":"R101","reset":false}` | Initial state, map data, KPIs, session ID, and revision |
| `GET /state?t=90` | Read-only time query | All strategy maps, time-specific KPIs, and events visible at that time |
| `POST /event` | Typed event, time, session ID, revision | Updated state for all three strategies |
| `GET /kpis?t=95` | Optional read-only time | Strategy KPIs and event before/after comparisons |

Example event bodies (replace session/revision with the latest response):

```json
{"event_type":"breakdown","time":90,"vehicle_id":14,"session_id":"...","revision":0}
```

```json
{"event_type":"new_order","time":95,"x":40,"y":40,"demand":10,"ready":100,"due":180,"service":10,"session_id":"...","revision":1}
```

The API validates event types, required fields, finite values, vehicle IDs (including
zero), time bounds, and delivery windows. Stale sessions/revisions and historical
events return 409; invalid inputs return 422. `GET` requests never advance live
state. A lock serializes access to the one global session. Use one server worker;
multiple workers would hold separate states. Solver failures remain visible in the
event results and UI; they are not silently reported as optimal routes.

The frontend checks HTTP responses, cancels outdated slider requests, pauses
playback for mutations, and disables controls during recovery. Fractional times
are displayed explicitly. The backend caps initial search and re-planning at two
seconds; total request time also includes all-strategy processing and serialization.

### Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

S5 adds API tests for actual datasets, read-only history, real events, reset
confirmation, stale revisions, vehicle zero, malformed inputs, and failed-solve
reporting. FastAPI's test client uses a local loopback socket internally; a sandbox
that blocks loopback must permit it or run these tests in a normal terminal.

## Run the S4 strategy comparison

```powershell
.\.venv\Scripts\python.exe demo_s4.py
.\.venv\Scripts\python.exe -m pytest -q
```

This runs the same R101 breakdown and new-order events on independent fleets using
do nothing, cheapest insertion, and OR-Tools re-planning. It writes a readable
`results/s4-comparison.md` report and detailed `results/s4-comparison.json` with
per-strategy before/after snapshots, every KPI delta, statuses, and final results.

```python
from comparison import Comparison
from parser import find_dataset, load_solomon
from solver import solve_static

problem = load_solomon(find_dataset("data/solomon", "R101"))
comparison = Comparison(problem, solve_static(problem))
event = comparison.break_vehicle(90, vehicle_id=14)
event = comparison.add_order(95, 40, 40, 10, (100, 180))
print(event["strategies"])
print(comparison.kpis(230))  # Read-only projection to end of day.
```

Choose the breakdown vehicle from the actual initial plan; the demo chooses an
active vehicle automatically. Events validate and commit across all three fleets
atomically. Each strategy evolves separately after the first event, so subsequent
before snapshots belong to that strategy rather than a shared baseline.

### Strategies

- **Do nothing:** apply the event, freeze a broken vehicle, and leave all healthy
  itineraries unchanged. New and stranded orders stay unassigned. No solve runs.
- **Cheapest insertion:** process unassigned orders in ascending ID order. Try
  every position in every healthy vehicle's editable suffix, including idle
  vehicles. Keep the existing customer order, completed history, and current
  service locked. Check capacity, waiting, service-start windows, and depot return
  for the entire candidate schedule. Choose the feasible insertion adding the
  least projected distance, with deterministic ties. Only vehicles receiving an
  insertion change. Previously unassigned orders can be retried on later events.
  The heuristic has a two-second budget and reports `INSERTION_TIME_LIMIT` if it
  stops early, keeping only valid insertions.
- **Re-plan:** use S3's pending-order solver with a two-second search limit and
  preserved history. `SOLVE_FAILED` remains visible and retains healthy routes.

`baselines.py` adapts the structure of the supplied prototype; it no longer uses
the obsolete route/schedule dictionaries. Its `run_do_nothing`,
`run_cheapest_insertion`, and `run_ortools_replan` wrappers return independent
copies, but require an event-only state. Prefer `Comparison` or event methods with
`strategy="do_nothing"` / `strategy="cheapest_insertion"` / `strategy="replan"`
so no strategy accidentally starts from an already re-planned event result.

### KPI definitions

`kpis.py` reads actual/planned segments and rejects missing service schedules.
It exposes the following values without rounding away precision:

- `distance`: executed travel plus remaining planned travel, including depot returns.
- `distance_travelled` and `remaining_distance`: the two parts of that total.
- `late_deliveries`: completed or planned deliveries whose physical arrival is
  after due; `actual_late_deliveries` counts only completed ones. Aborted approaches
  and interrupted service are not counted as deliveries.
- `dropped_orders`: currently unassigned orders, not permanently cancelled orders.
- `served_orders`, `assigned_pending_orders`, and `total_orders`: delivery accounting.
- `solve_time_sec`: recovery execution time for the latest event, measured with a
  monotonic clock for all methods; excludes cloning and advancing to the event.
  Re-planning includes model preparation/extraction, so it may slightly exceed
  the two-second search budget. Before-event recovery time is zero for that event.

Every event has a numeric delta for every KPI. Reporting rounds only for display.
Lower distance is not automatically better: do nothing often travels less because
it leaves orders undelivered. Feasible strategies should normally report no late
deliveries. A time-limited re-plan is not guaranteed to beat cheapest insertion.

## Run the S3 demonstration

```powershell
.\.venv\Scripts\python.exe demo_s3.py
.\.venv\Scripts\python.exe -m pytest -q
```

The demo loads R101 with 25 customers, solves initial routes, advances to time 90,
breaks an active vehicle, adds an order at time 95, then advances to depot closing.
Each re-plan has a 2-second search budget. It checks completed-delivery locking,
vehicle positions, travelled distance, capacity, delivery windows, and order
accounting. `results/s3-demo.json` contains initial, mid-day, post-event, and final
snapshots plus before/after snapshots for each event. The added order brings the
demo to 26 customers; the initial dataset remains capped at 25.

### Simulation interface

```python
from parser import find_dataset, load_solomon
from solver import solve_static
from sim import FleetState

problem = load_solomon(find_dataset("data/solomon", "R101"))
state = FleetState(problem, solve_static(problem))
preview = state.snapshot(90)  # Read-only; does not change the clock.
state.advance_time(90)       # Commit forward progress.
vehicle_id = next(i for i, v in state.vehicles.items()
                  if v.status in ("travelling", "waiting", "servicing"))
breakdown = state.break_vehicle(90, vehicle_id)
new_order = state.add_order(95, 40, 40, 10, (100, 180), service=10)
past = state.snapshot(45)    # Uses the plan that existed at time 45.
current = state.snapshot()
```

`State` is an alias for `FleetState`; `replan(state, t)` is also available as a
module-level function. Event times must move forward or remain at the current time.
Events at the same time are processed in call order. Read-only historical and
future snapshots never change the active state. A snapshot at an event timestamp
shows the latest event applied at that timestamp.

### Execution and re-planning rules

- All available vehicles are tracked, including vehicles unused in the S2 plan.
- A vehicle can be idle, travelling, waiting, servicing, or broken. Position changes
  only on travel segments. The depot is never treated as a delivered order.
- A delivery is served when its service ends, including zero-duration service at
  its scheduled instant. Served IDs and completed segments remain locked forever.
- Healthy vehicles finish service already in progress. That order is reserved to
  its vehicle; its remaining duration and demand are respected by the new plan.
- A breakdown freezes the vehicle at the event position. Interrupted service does
  not count as delivered; its order becomes available for full service elsewhere.
- Re-planning considers all unfinished, unlocked orders, including originally
  dropped orders, not just newly stranded orders. Broken vehicles are excluded.
- The solver uses synthetic start nodes at current positions, time windows
  relative to the event, and remaining capacities. Returned schedules use absolute
  times. Original vehicle IDs are preserved. Vehicles return to the original
  depot even when assigned no new deliveries.
- Capacity is the agreed simplified budget: capacity minus served demand, with
  in-progress service demand reserved. Cargo transfer/reloading is assumed rather
  than simulated. Completed capacity does not reset when a vehicle returns home.
- Full execution paths are lists of immutable travel/wait/service segments.
  Partial travel is retained at an event. Total projected distance equals distance
  already travelled plus remaining planned distance, including healthy depot returns.
- Unassigned orders remain explicit in `unassigned_pool`, including expired orders.
  Pending is a delivery status; assignment is tracked separately. After depot
  closing, unserved orders stay unassigned.
- If no vehicles remain, the result is `NO_AVAILABLE_VEHICLES`. If a solve fails,
  the event stays applied, healthy vehicles retain their previous itineraries,
  and new/stranded orders remain unassigned with `SOLVE_FAILED` and an error detail.
  Invalid events or unexpected exceptions do not partially mutate the live state.

The simulation keeps history and events in memory. S4 adds baseline strategies and
comparison data; persistence, APIs, Plotly visualization, and KPI cards are later work.

## Solve initial routes

```powershell
.\.venv\Scripts\python.exe solver.py --json results/initial-plans.json
.\.venv\Scripts\python.exe solver.py data/solomon/r101.txt --seconds 2
```

The default runs C101, R101, and RC101 with 25 customers each. Output includes
distance, used vehicles, dropped IDs, elapsed time, and routes with service-start
times. JSON additionally includes arrival, departure, cumulative demand, objective,
penalty, search status, and the time limit. Vehicle IDs are zero-based; unused
vehicles are omitted from the routes.

`solve_static(problem, time_limit_seconds=2)` returns a `Plan`. The search uses
PATH_CHEAPEST_ARC followed by guided local search. Limits above 3 seconds are
rejected. Recorded elapsed time includes model construction and extraction, so it
can slightly exceed the search limit. A missing solution raises `SolveError` and
is not reported as an all-dropped plan.

Distances are Euclidean, rounded to the nearest tenth (half up), then stored as
integer costs. Travel time equals this rounded distance. Windows are rounded
inward to tenths; service duration is rounded upward. Reported values are converted
back to source units. This quantization can differ from full-precision benchmark
distance conventions.

Vehicles start at depot opening time and must return by closing time. Service
must start within each customer's window; it may finish after that customer's due
time. Arrival can be earlier than service start because of waiting. Route stops
include both depot endpoints. Schedules are reconstructed at the earliest feasible
times for the selected route order, with waiting at the destination.

The objective is distance plus a large integer penalty per dropped customer. The
penalty exceeds an upper bound on route distance, prioritizing service count over
distance. Vehicle count is reported but is not minimized as a separate objective.
The time-limited heuristic does not prove optimality. `benchmark.py` compares
25-customer solves with commonly cited best-known values (verify them against the
SINTEF Solomon page before quoting):

| Dataset | Ours | Best-known | Gap | Vehicles |
|---|---|---|---|---|
| C101 | 191.7 | 191.3 | +0.21% | 3 / 3 |
| R101 | 619.0 | 617.1 | +0.31% | 8 / 8 |
| RC101 | 462.3 | 461.1 | +0.26% | 4 / 4 |

Run `python benchmark.py`; `tests/test_benchmark.py` checks no drops and a gap under 2%.

## Run locally

Open PowerShell in this project folder. No environment activation is necessary:

```powershell
.\.venv\Scripts\python.exe parser.py
.\.venv\Scripts\python.exe -m pytest -q
```

The first command summarizes C101, R101, and RC101. Each should show 25 customers
plus the depot, 25 available vehicles, and capacity 200. Depot windows are
0–1236, 0–230, and 0–240 respectively.

To read one file:

```powershell
.\.venv\Scripts\python.exe parser.py data/solomon/r101.txt --limit 25
```

## Recreate the environment

Use Python 3.13 (the version used for this build):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` records the installed dependency versions, including transitive
dependencies. The main packages are OR-Tools, FastAPI, Uvicorn, and pytest.

## Parser interface

```python
from parser import find_dataset, load_solomon

path = find_dataset("data/solomon", "R101")
problem = load_solomon(path, customer_limit=25)
print(problem.depot)
print(problem.customers[0])
```

- `Problem` contains the name, available vehicle count, vehicle capacity, depot,
  and a tuple of non-depot customers. `problem.nodes` includes the depot first.
- `Customer` contains `id`, `x`, `y`, `demand`, `ready`, `due`, and `service`.
- IDs and demands are integers. Coordinates and times preserve source units;
  integer scaling belongs in the future solver.
- The parser validates every row, then takes the first 25 non-depot customers in
  file order. It does not assume customers have consecutive IDs.
- A limit of zero returns only the depot. If fewer customers exist than requested,
  all available customers are returned.
- `SolomonParseError` identifies invalid input and includes line numbers for row
  errors. File access errors retain their normal Python exception types.
- Dataset lookup is case-insensitive and rejects ambiguous matches.

## Data and validation

The 56 original text datasets were copied from the supplied `solomon-100.zip`.
`data/source_manifest.json` records archive and file checksums. The source files
retain all 100 customers; the parser selects the demo subset in memory.

Tests cover real benchmark inputs, original units, irregular whitespace, subset
selection, duplicate IDs, missing depots, malformed values, invalid time windows,
negative demand/service, and case-insensitive dataset lookup.

When changing the parser or models, run the tests above. Keep the default at 25
customers until the complete demo works. Published 100-customer routing results
must not be treated as directly comparable to this 25-customer subset.

## Street map view (illustrative)

The map button (🗺) switches from the plain x/y plot to a Leaflet street map. Solomon
data has no real coordinates, so the x/y grid is placed on a chosen city (Vellore,
Chennai or Bengaluru) at 80 m per unit. This is cosmetic: the solver, distances and
time windows still use Solomon x/y units, and route lines are straight, not roads.
Tiles come from OpenStreetMap (attribution shown; internet needed), darkened with a
CSS filter. Leaflet 1.9.4 is bundled in `static/vendor/leaflet/`. The plain map stays
available as an offline fallback.

### Road routes (street map)

In street-map view every route leg is drawn along real roads. The backend `/road`
endpoint asks the public OSRM demo server (router.project-osrm.org) for the driving
route between two points and caches the answer; the page shows straight lines while
a leg loads, and keeps a straight line if the service is unreachable. The "Road" chip
sums the real road length of all legs. Vehicles are drawn at their progress along the
road. The solver still plans on Solomon x/y distances, so road kilometres are
informational and do not change the KPIs. Points that fall off-road (rivers, forest)
snap to the nearest road. The OSRM demo server is for light use only; self-host OSRM
or use another routing service for anything heavier.

## Deploy

- **Render:** New > Blueprint > select this repo (uses `render.yaml`).
- **Hugging Face Spaces:** New Space > Docker SDK, push this repo (uses `Dockerfile`, port 7860).
- The app keeps one in-memory session per process, so everyone on the same URL shares the same simulation.
- The first load of each leg on the street map calls the public OSRM server; the free tier may sleep, so open the URL once before a live demo.

## Road-distance planning (default in the dashboard)

Choose **Distance: Road** (default) or **Straight-line** in the header. In road mode
(`metric.py`) the Solomon grid is placed on a map location (the "amazon" point on Jimmy Carter Road, VIT, by default, or
any city/point you pick), OSRM driving distances are fetched, and the solver, simulator,
baselines and KPIs all use them: travel time equals road distance, so plans, late
deliveries and re-planning are road-based. One "unit" is calibrated so the average road
leg equals the average straight leg, which keeps the Solomon time windows meaningful;
real kilometres = distance x metres-per-unit / 1000 (shown in the Road chip).

- Click the pin button on the map, then click the exact spot, to put the depot there;
  the customers are laid out around it and everything is re-planned.
- Straight-line mode is the original Solomon benchmark metric (used by `benchmark.py`
  and most tests). Road results are not comparable with published Solomon values.
- Only drivable roads are used (OSRM car profile), so rail tracks are never routed
  along; they are crossed only at real crossings or bridges. Private campus roads may
  be avoided by the routing service, so routes inside a campus can detour via public roads.
- If the routing service is unreachable, loading in road mode fails with a clear message
  (use Straight-line); distances that cannot be fetched mid-session fall back to estimates.
