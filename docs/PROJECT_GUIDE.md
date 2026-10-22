# GreenMile: the complete project guide

A step-by-step explanation of the whole project, written so that every team member can understand,
demo and defend every part. Read it in order the first time. Later, use the contents list to jump.

**Contents**

0. How to use this guide
1. The problem we solve
2. Concepts and glossary
3. The big picture
4. Step S1: data and parser
5. Step S2: the solver
6. Step S3: the simulation and events
7. Step S4: recovery strategies and KPIs
8. Step S5: the API and the dashboard
9. Step S6: validation (tests, benchmark, demo scripts)
10. Step S7: publishing and the demo
11. Extensions beyond the original plan
12. One event, end to end
13. Design decisions and why
14. Limitations and future work
15. Questions you may be asked
16. Cheat sheet

---

## 0. How to use this guide

- **Five-minute version:** read sections 1, 3 and 12.
- **To present:** read 1, 3, 12, 13 and 15, then rehearse with [DEMO.md](../DEMO.md).
- **To change code:** read the step that owns the file (the file map in section 16 says which).
- Terms in **bold** are explained in the glossary (section 2).

---

## 1. The problem we solve

A delivery company plans its trucks' routes in the morning. During the day things go wrong: a truck
breaks down, a new urgent order arrives. A plan made in the morning is suddenly wrong. Dispatchers
need a system that:

1. makes a good plan to start with (short routes, every customer visited inside their time window,
   no truck overloaded);
2. reacts to a disruption **from the current state of the day**, not by starting over: parcels already
   delivered stay delivered, trucks keep moving from where they actually are;
3. shows how different reaction strategies compare, with numbers.

This is hackathon problem 4.3 (NEXAI 2026, Track 4: AI for Supply Chain and Logistics):
*Last-Mile Route Planner with Live Re-planning*. The required parts are a static solver checked against
benchmark data, an event simulator (new orders, breakdowns), and a dashboard with a map, a timeline,
event buttons and before/after KPIs.

**What GreenMile adds on top:** real road distances and a real street map, a live fleet panel with moving
trucks, minutes and kilometres as units, and three independent strategies compared on every event.

---

## 2. Concepts and glossary

| Term | Meaning in this project |
|---|---|
| **Depot** | Where all trucks start and finish. Customer 0 in the data. |
| **Customer / order / stop** | A place to deliver to. Has a location, a demand, a time window and a service time. |
| **Demand** | Load units the order needs. A truck cannot carry more than its **capacity** (200 for R101). |
| **Time window** | `[ready, due]`: service may only start inside it. Arriving early means waiting. |
| **Service time** | How long unloading takes at the stop (10 minutes in these instances). |
| **VRP** | Vehicle Routing Problem: choose routes for several vehicles to serve customers cheaply. |
| **VRPTW** | VRP with Time Windows: also respect each customer's window. This is our problem. |
| **Solomon benchmark** | Standard academic VRPTW instances. Families: C (clustered), R (random), RC (mixed). `1xx` have short horizons, `2xx` long. We use C101, R101, RC101. |
| **OR-Tools** | Google's optimisation library. Its routing solver builds an initial route and improves it. |
| **Dimension** | An OR-Tools quantity accumulated along a route (we use *Capacity* and *Time*). |
| **Disjunction / drop penalty** | Lets the solver skip a customer at a big cost, so a plan always exists and "dropped orders" can be reported instead of failing. |
| **Metaheuristic (guided local search)** | The improvement method: repeatedly tweak routes, penalising features that keep appearing, to escape poor local optima. |
| **Dynamic re-planning** | Solving again mid-day, from each truck's current position, for the orders still pending. |
| **Locked history** | Deliveries already done (and service in progress) are never changed. |
| **Segment** | A piece of a truck's timeline: travelling, waiting, servicing (or interrupted). |
| **Strategy** | A way of recovering from an event: do nothing, cheapest insertion, OR-Tools re-plan. |
| **KPI** | A number to compare plans: distance, late deliveries, dropped orders, recovery time. |
| **OSRM** | Open Source Routing Machine: gives real driving distances and routes on OpenStreetMap roads. |
| **Snapping** | Moving a point onto the nearest drivable road. |
| **Session / revision** | The server keeps one simulation. `revision` counts events so stale browsers are rejected. |

---

## 3. The big picture

```
 Solomon .txt file
        │  parser.py
        ▼
 Problem (depot, customers, vehicles, capacity, distance model)
        │  solver.py  (OR-Tools VRPTW, static)
        ▼
 Plan (routes with arrival and service times)
        │  comparison.py creates THREE copies of the fleet
        ▼
 ┌──────────────┬────────────────────┬───────────────────┐
 │ do_nothing   │ cheapest_insertion │ replan            │   each is a FleetState (sim.py)
 └──────┬───────┴─────────┬──────────┴─────────┬─────────┘
        │   event: breakdown / new order at minute t
        ▼
 Each strategy recovers in its own way (baselines.py, solver.py)
        │
        ▼
 kpis.py → before / after / delta for every strategy
        │
        ▼
 api.py (FastAPI)  ⇄  static/ dashboard (Leaflet, Plotly, JS)
```

**Two runtime loops**

1. *Simulation loop:* the browser asks `GET /state?t=…` many times while **Play** runs. The server
   reconstructs where every truck is at that minute from the stored plan. Nothing is recomputed.
2. *Event loop:* when you press "take offline" or "add order", `POST /event` applies it to all three
   strategies, recovers, and returns the new state.

**Files at a glance:** `parser.py`, `models.py` (data), `solver.py` (planning), `sim.py` (time and
events), `baselines.py`, `comparison.py`, `kpis.py` (strategies and numbers), `metric.py` (distance
models), `api.py` (server), `static/` (dashboard).

---

## 4. Step S1: data and parser

**Goal:** turn a Solomon text file into a validated Python object the solver can use.

### 4.1 The data
`data/solomon/` holds all 56 Solomon instances (9 C1, 8 C2, 12 R1, 11 R2, 8 RC1, 8 RC2).
`data/source_manifest.json` records the SHA-256 checksum of each file so we can prove the data was
not altered. The dashboard offers three: C101, R101, RC101.

### 4.2 A file looks like this
```
R101

VEHICLE
NUMBER     CAPACITY
  25         200

CUSTOMER
CUST NO.   XCOORD.   YCOORD.    DEMAND   READY TIME   DUE DATE   SERVICE TIME
    0          35      35           0       0         230           0     <- depot
    1          41      49          10     161         171          10     <- first customer
```
Customer 0 is the depot; its due date (230) is when the day ends.

### 4.3 What the code does
- `parser.load_solomon(path, customer_limit=25)` reads the table and keeps the depot plus the first 25
  customers. Malformed files raise `SolomonParseError` with the file and line number. Values stay in the
  original units; nothing is scaled here. The solver then validates the values it receives (finite
  numbers, ready ≤ due, non-negative integer demands, unique IDs, depot with ID 0 and zero demand).
- `parser.find_dataset(folder, "R101")` finds `r101.txt` safely (it rejects names like `../../secret`).
- `models.py` defines the dataclasses: `Customer`, `Problem` (name, vehicle count, capacity, depot,
  customers, optional distance `metric`), `RouteStop`, `VehicleRoute`, `Plan`, `VehicleStart`,
  `DynamicPlan`. They are frozen (immutable) so plans cannot be changed by accident.

**Why 25 customers?** It matches the 25-customer benchmark and keeps solves fast enough for live use.

**Tests:** `tests/test_parser.py` covers real files, odd whitespace, subsets and malformed input.

---

## 5. Step S2: the solver

**Goal:** from a `Problem`, produce the initial plan.

### 5.1 Model (in `solver.solve_static`)
- **Integer costs.** OR-Tools works with integers, so distances are stored in *tenths of a unit*
  (`SCALE = 10`) and rounded to the nearest tenth. Reported values are divided back.
- **Distance matrix.** `distance_matrix(problem)` gives the cost of every pair. In road mode this comes
  from OSRM (section 11); otherwise it is straight-line.
- **Capacity dimension.** Each customer's demand accumulates along a route and may not exceed the
  vehicle capacity.
- **Time dimension.** Travel time between stops equals their distance, plus service time at the stop.
  Each customer's window is imposed as a range on the time variable. Windows are rounded **inward**
  (ready rounded up, due rounded down) and service time rounded **up**, so rounding never relaxes a
  constraint.
- **Drop penalty.** Each customer is optional with a penalty bigger than any possible total route
  distance, so the solver always prefers serving a customer if it can. Dropped IDs are reported.
- **Search.** First route by *path cheapest arc*, then *guided local search* for a time limit (at most
  3 seconds in the static solve).
- **Extraction.** Routes are read back and a schedule is rebuilt at the earliest feasible times; waiting
  happens at the customer, so *arrival* and *service start* can differ. The code double-checks that every
  window holds and raises `SolveError` if not.

### 5.2 Dynamic solve (`solver.solve_dynamic`)
Used for re-planning mid-day. Differences:
- Each truck starts at its **current position**, represented by an extra "start" node.
- Each truck has its own **remaining capacity**, and an `available_at` time (when its current service
  ends).
- Times are shifted by `now`, so windows are measured from the event.
- Only the **pending** customers are included; delivered ones are history.
- Search limit is at most 2 seconds. Empty trucks still return to the depot.

### 5.3 Benchmark check
`benchmark.py` solves C101, R101 and RC101 and compares with the published best-known 25-customer values.
Our results are 0.2 to 0.3 % above (see section 9). Distances are rounded to tenths, so the comparison
is approximate.

**Tests:** `tests/test_solver.py` (constraints, drops, limits, errors), `tests/test_benchmark.py`.

---

## 6. Step S3: the simulation and events

**Goal:** keep track of the day as it unfolds, and let events change it without breaking history.

### 6.1 Truck timelines (`sim.Segment`)
A truck's day is a list of segments, each with a start and end minute:
- `travelling` (from A to B, with the distance),
- `waiting` (arrived before the window opens),
- `servicing` (unloading),
- `interrupted_service` (cut short by a breakdown).

From a segment and a time `t`, `Segment.position(t)` interpolates the position, and
`Segment.fraction(t)` says how far through it we are.

### 6.2 `FleetState`
Holds the problem, the current time, every truck (`VehicleState`: position, status, served stops,
load, distance travelled, segments), which trucks are broken, each order's status, and the pool of
unassigned orders.

- `advance_time(t)` walks every truck's segments up to `t`, computing position, status
  (`idle/travelling/waiting/servicing/broken`), distance travelled, served stops and load. It can only
  move forward.
- `snapshot(t)` returns a **read-only** view at any minute, including the past. Past queries use the
  saved plan version that was in effect then, so scrubbing the slider back is correct.

### 6.3 Events
- `break_vehicle(t, vehicle_id)`: the truck stops at its current position, any service in progress is
  interrupted, its unfinished orders return to the pool, and it never receives new work.
- `add_order(t, x, y, demand, window, service)`: a new customer ID is created and joins the pool.
- `replan(...)`: just re-solves.

### 6.4 How an event is applied safely (`_commit_event`)
1. Validate the time and search limit.
2. **Clone** the state, advance the clone to `t`, take a *before* snapshot.
3. Apply the change (break the truck or add the order), advance again.
4. Run the chosen strategy's recovery on the clone.
5. Take an *after* snapshot, store the event with both snapshots.
6. **Only now** replace the real state with the clone and save a version.
If anything fails, the real state is untouched (all-or-nothing).

### 6.5 Re-planning (`prep_for_ortools` and `_replan`)
- For each healthy truck, keep its **prefix** (segments finished or in progress; a service in progress is
  kept), and compute its start position, `available_at` and remaining capacity.
- Pending orders are those not served and not locked in a current service.
- Call `solve_dynamic`; append the new segments to each truck's prefix.
- If the solver fails (`SolveError`), keep the existing healthy routes and show status `SOLVE_FAILED`.

**Rules this enforces:** served stops stay frozen; a broken truck gets no new work; pending orders can be
reassigned; a truck is re-planned from where it really is.

**Tests:** `tests/test_sim.py` (invariants at each step, locking, breakdown, new order).

---

## 7. Step S4: recovery strategies and KPIs

### 7.1 Three strategies (`baselines.py`)
| Strategy | Behaviour | Strength / weakness |
|---|---|---|
| `do_nothing` | Apply the event; leave healthy routes alone. Stranded and new orders stay unassigned. | Instant, but leaves orders undelivered. |
| `cheapest_insertion` | For each unassigned order (in ID order), try every position in every healthy truck's remaining route; pick the feasible insertion that adds the least distance. Keeps existing order of stops. Has a 2-second budget. | Fast and stable; cannot reshuffle existing stops. |
| `replan` | Full dynamic OR-Tools solve from current positions. | Best quality; costs up to 2 seconds. |

`_candidate_route` builds a complete earliest-feasible schedule for a truck from its current position
(capacity, waiting, windows, depot return all checked).

### 7.2 Fair comparison (`comparison.py`)
`Comparison` owns **one `FleetState` per strategy**. For each event it forks each state, applies the
**same** event to every fork, recovers, and commits all three or none. Histories never mix, so one
strategy cannot benefit from another's work.

### 7.3 KPIs (`kpis.py`)
`calculate_kpis(state)` reads the actual and planned segments:
- `distance`: travel done plus remaining planned travel, including returns to the depot;
- `late_deliveries`: services whose arrival is after the due time;
- `dropped_orders`: orders currently unassigned (not permanently cancelled);
- `served_orders`, `assigned_pending_orders`, `total_orders`;
- `solve_time_sec`: recovery time of the latest event.

`compare_strategies` produces `before`, `after` and `delta` for each strategy. Remember: lower distance is
not automatically better; doing nothing often drives less because it delivers less.

**Tests:** `tests/test_baselines.py`, `tests/test_kpis.py`.

---

## 8. Step S5: the API and the dashboard

### 8.1 The API (`api.py`, FastAPI)
| Endpoint | What it does |
|---|---|
| `POST /load` | Load a dataset, build the distance model, solve, create the three fleets. Body: `dataset`, `reset`, `routing` (`road`/`euclid`), `city`, `speed_kmh`, optional `depot_lat`/`depot_lng`. |
| `GET /state?t=` | Full state at minute `t`: for each strategy the trucks, customers, routes (past, future and per-leg progress), KPIs; plus events. |
| `POST /event` | Apply `breakdown` or `new_order` at the current time. Needs `session_id` and `revision`. |
| `GET /kpis` | Before/after comparison for all events. |
| `GET /road` | Cached road geometry between two points for the street map. |
| `GET /timeseries?step=` | Per-strategy delivered, unassigned, late, distance and fleet-activity counts every `step` minutes, plus the events (used by Analytics). Cached per session revision. |
| `GET /geocode?q=&lat=&lng=` | Place search for the booking page, run on the server against OpenStreetMap (browsers get blocked), cached, `502` when unavailable. |

Safety features: request bodies are validated with strict models (unknown fields rejected); `409` if the
session changed (stale `revision`), if reset confirmation is missing, or if an event is earlier than the
last committed event; `422` for invalid values or a time after the depot closes; `502` if the routing
service is down; `503` if the solver fails. One process-wide lock protects the shared session.

`chart_state` turns a snapshot into what the page needs: trucks with their `activity` (what each is doing
now and until when) and `finish`, customers with status and vehicle, routes split into past and future,
and the per-leg progress used to draw trucks along roads.

### 8.2 The dashboard (`static/`)
Single page, no build step. `index.html` (layout), `style.css` (dark theme), `app.js` (logic).

**Header:** brand, KPI chips (active, delivered, late, unassigned, distance, solve time, road km), average
speed, distance mode, dataset and Load & solve.

**Operations view**
- **Map:** the street map (Leaflet, OpenStreetMap tiles) is the default; a plain x/y plot (Plotly) is the
  fallback. Customers are coloured by status; routes are drawn along roads (past dim, future bright).
- **Truck badges:** rounded square coloured by status (moving, servicing, waiting, broken) with a truck
  and its number. They glide while playing. Idle trucks are summarised as "N idle" at the depot. Badges
  shrink as you zoom out.
- **Fleet panel:** every truck with its activity, ETA, load, driving minutes and when it is back; click
  to select and fly to it.
- **Controls:** play/pause, a time slider, speed (1 to 25 minutes per real second), zoom, 📍 depot pin,
  ⌖ place a new order.
- **Change the day:** take a truck offline, add a new order.

**Vehicle view:** cargo layout (a truck drawing with the order tiles stacked from the floor up), orders
list, gauges, stats.

**Strategy comparison view:** three cards and a table.

**Home page (first page, and the logo):** a live briefing: what GreenMile does in three lines, then a "Right now"
panel with delivery progress, a fleet status bar (moving, servicing, waiting, idle, broken), the next order
deadlines, and a plain-English line on which strategy is winning. Opening the link without options shows Home.

**Orders page:** every order with status (pending, servicing, delivered, unassigned, at risk, late), truck,
time window, planned arrival, slack and parcel weight/volume. Filter tabs, search and sortable columns; a row opens
its truck on the map. Planned arrival comes from the backend (`arrival` and `service_start` per order).

**Book page:** a three-step booking flow. (1) Search a place (server-side `/geocode`) or drop a pin on a small map.
(2) Choose a parcel size: Small 25 kg / 0.15 m³, Medium 50 kg / 0.30 m³, Large 100 kg / 0.60 m³. (3) Choose the
delivery time: within 1 hour, within 2 hours or anytime today. A side panel shows a live order summary, the parcel
guide and the six trucks with the most free space. Booking sends a `new_order` event and then shows, per strategy,
which truck takes the order and when it arrives, or that it could not be scheduled.

**Analytics page:** KPI cards, charts of orders delivered over the day, orders left unassigned, fleet activity
(stacked area) and orders delivered per strategy, plus the event history. Event times are marked on the charts.

**Fleet panel and truck lights:** the Fleet list sits on the side of the map, with "Change the day" as a wide card
under the map. Trucks are lit by state: broken = light red with a glow, idle = dim white, in use = bright yellow
headlight and beam (the large truck on the Trucks page changes the same way).

**Loads and parcels:** one load unit is 5 kg and 0.03 m³, so a 200-unit truck carries 1000 kg and 6 m³. A truck's
load is the parcels still on board (assigned, not yet delivered), shown in kg and m³ on the Fleet list, truck card,
Trucks page, order tiles and Orders table.

**Selectors:** the native dropdowns for city, speed, strategy, distance and dataset are steppers (arrows with a dot
indicator, keyboard left and right). The page holds redraws while a press is in progress so that clicks and
breakdowns work while the simulation is playing.

**How the page works:** `accept(data)` stores the state; `render()` redraws everything from it. Playing
repeatedly calls `GET /state?t=…` (skipping a tick while a request is still in flight). Events call
`POST /event` and then show the returned state.

**Helpful URL options:** `?t=60&truck=4&view=compare&map=plain&clean=1` opens a particular moment,
truck or view, and `clean=1` hides everything but the map (used for screenshots).

**Tests:** `tests/test_api.py`, `tests/test_activity.py`, `tests/test_frontend_assets.py`.

---

## 9. Step S6: validation

### 9.1 Automated tests
```
.venv/Scripts/python -m pytest -q        # 136 tests, about 90 seconds
```
| File | What it protects |
|---|---|
| `test_parser.py` | Parsing and input validation |
| `test_solver.py` | Constraints, drops, limits, errors |
| `test_sim.py` | Time advance, locking, breakdowns, new orders, invariants |
| `test_baselines.py` | The strategies and their rules |
| `test_kpis.py` | KPI definitions and deltas |
| `test_api.py` | Endpoints, errors, conflicts |
| `test_activity.py` | Per-truck activity for the fleet panel |
| `test_road.py` | The `/road` endpoint and cache |
| `test_metric.py` | Road distances, calibration, snapping, depot anchor, speed (with a fake routing table, so no internet) |
| `test_benchmark.py` | Static solve is close to the best-known values |
| `test_frontend_assets.py` | Page assets are served and the script's element IDs exist |
| `test_orders_analytics.py` | Planned arrivals per order, the `/timeseries` shape and consistency, the `/geocode` endpoint |

### 9.2 Benchmark (25 customers)
| Dataset | Ours | Best-known | Gap | Vehicles |
|---|---|---|---|---|
| C101 | 191.7 | 191.3 | +0.21% | 3 / 3 |
| R101 | 619.0 | 617.1 | +0.31% | 8 / 8 |
| RC101 | 462.3 | 461.1 | +0.26% | 4 / 4 |

The best-known values come from memory of the usual published results and should be confirmed against
the SINTEF Solomon page before being quoted.

### 9.3 Demo scripts
`demo_s3.py` and `demo_s4.py` replay a fixed R101 scenario and check the invariants; `results/` holds the
saved output. Example outcome (straight-line mode, breakdown at minute 90): re-plan and insertion leave 1
order unassigned, doing nothing leaves 3.

---

## 10. Step S7: publishing and the demo

- **Deploy:** `render.yaml` (Render Blueprint) and `Dockerfile` (Hugging Face Spaces, port 7860). They have
  not been tested in a real deploy. Free tiers sleep, so open the link before presenting. One server holds
  one shared simulation.
- **Demo:** [DEMO.md](../DEMO.md) is a 90-second script. [FLOW_PROMPTS.md](FLOW_PROMPTS.md) is a prompt
  pack for AI intro/outro clips. Record the real dashboard for the main part.
- **Status:** deployment and the recorded video are the remaining items.

---

## 11. Extensions beyond the original plan

### 11.1 Road distances (`metric.py`)
- Two **distance models**: straight-line (benchmark) and road. `Problem.metric` is `None` for
  straight-line; in road mode it is a `RoadMetric`.
- **Placement:** the Solomon grid is placed on a map at 80 m per unit. The default location is the
  "amazon" point on Jimmy Carter Road at VIT Vellore; the 📍 tool or `depot_lat/depot_lng` change it.
- **Distances:** one OSRM *table* request returns the whole road-distance matrix; it is cached. New points
  (a new order, a truck's current position) trigger another request via `prefetch`.
- **Snapping:** OSRM also returns the nearest road point for each input; markers, legs and the depot use
  those, so nothing is drawn in a lake or inside a building.
- **Time unit:** one time unit is one minute of driving at the chosen average speed
  (default 25 km/h = 416.7 m per minute). Real kilometres = units × metres-per-unit ÷ 1000.
- **Fallback:** if OSRM is unreachable at load, `/load` returns 502 and you can use straight-line mode;
  mid-session failures fall back to estimates.

### 11.2 Why trucks can still show late "back at depot" times
Solomon's customer windows are spread over the whole 230-minute day, so trucks wait for late windows to
open. Driving time is small (24 to 40 minutes per truck for R101 at 25 km/h); the fleet panel shows both.

### 11.3 Street map and routes
`/road` returns road geometry for each leg; the page draws it, with a straight line while loading. Truck
position is interpolated along the road using the leg progress that `chart_state` provides.

### 11.4 Fleet panel and moving trucks
`activity` says what each truck is doing now and until when; the badges persist between updates so they
glide (CSS transition) while playing.

### 11.5 Interface polish
Dark theme, a plain status line, a single brand mark, readable dropdowns, a cargo drawing with a road
under the wheels and cargo stacked from the floor, units in minutes and kilometres.

### 11.6 Orders, booking, analytics and home
Added after the first demo: the Orders and Book pages (with parcel weight/volume), the Analytics page (`/timeseries`),
the Home page, truck lights and the stepper selectors. See section 8.2 for what each shows.

### 11.7 Not built
Weather and traffic events (they would need travel time to differ from distance).

---

## 12. One event, end to end

Scenario: R101, road mode, you take **Truck 3** offline at **minute 90**.

1. You drag the slider to 90. The page calls `GET /state?t=90` and shows the fleet at that minute.
2. You press *Take selected vehicle offline*. The page sends
   `POST /event {event_type: "breakdown", vehicle_id: 3, time: 90, session_id, revision}`.
3. The server checks the session and revision (reject if stale) and the time (not before the last event).
4. `Comparison._event` forks all three fleets, advances each fork to minute 90 and snapshots *before*.
5. In each fork, Truck 3's segments are cut at its position, its service in progress is interrupted, its
   unfinished orders go to the pool, and it is marked broken.
6. Recovery runs **per strategy**: do nothing leaves the pool; insertion places pool orders into other
   trucks' routes; re-plan calls `solve_dynamic` from every truck's current position.
7. KPIs are computed *after*; `before`, `after` and `delta` are stored.
8. All three forks replace the real fleets together, `revision` increases, and the new state returns.
9. The page redraws: Truck 3's badge turns red, the event log gets an entry, the comparison table shows
   how each strategy fared, and Play continues from minute 90 with the new plans.

---

## 13. Design decisions and why

| Decision | Reason |
|---|---|
| OR-Tools instead of writing our own optimiser | Mature VRPTW support (capacity, windows, drops), fast, and lets us spend time on re-planning and the dashboard. |
| Integer tenths for costs | OR-Tools requires integers; tenths keep precision without float errors. |
| Windows rounded inward, service rounded up | Rounding can never make a plan infeasible in reality. |
| Drop penalty instead of hard "serve everyone" | A plan always exists; unserved orders are reported, not crashes. |
| Clone, then commit | Failed events cannot corrupt the live state. |
| Three independent fleets | A fair comparison; no strategy inherits another's changes. |
| Keep a saved version per event | The timeline slider can show the past correctly. |
| Frozen dataclasses | Plans and problems cannot be modified by accident. |
| Straight-line mode kept | The benchmark and most tests stay valid and comparable with the literature. |
| Road mode via OSRM | Real roads and real kilometres without building routing ourselves. |
| Time unit = minute at a chosen speed | Makes times realistic and adjustable. |
| One shared in-memory session | Simple for a demo; no database needed. |
| Static dashboard, no framework | No build step; easy to deploy and explain. |

---

## 14. Limitations and future work

- Only the first 25 customers of each instance are used; larger sizes need longer solves.
- One shared session per server process; no accounts or saved sessions.
- Road mode depends on the public OSRM server and OpenStreetMap tiles (light use only).
- The street map is the Solomon grid placed on a real place, not real orders.
- Road results are not comparable with published Solomon values.
- No weather or traffic events; no multi-day planning; no driver shifts or breaks.
- Possible next steps: weather and traffic as time-dependent travel times, self-hosted routing, saving
  sessions, larger instances with a faster solver configuration, automated browser tests.

---

## 15. Questions you may be asked

1. **What problem do you solve?** Re-planning deliveries mid-day after disruptions (section 1).
2. **What is VRPTW?** Routing vehicles with capacities to serve customers inside time windows.
3. **Why OR-Tools?** Proven solver for exactly this problem; saves time (section 13).
4. **How do you know the solver is good?** It is within about 0.3 % of published best-known distances on
   three 25-customer instances (section 9.2).
5. **How does re-planning differ from solving again?** It keeps history, starts trucks from their current
   positions with remaining capacity, and only routes pending orders (section 6.5).
6. **What happens to a broken truck's orders?** They return to the pool and are reassigned (or stay
   unassigned under "do nothing").
7. **Why three strategies?** To show the trade-off: speed and stability against quality.
8. **Is cheapest insertion ever better than re-plan?** It can match it; it is faster and keeps routes
   stable, but cannot reshuffle existing stops.
9. **How are KPIs defined?** Section 7.3. Note that doing nothing has low distance because it delivers less.
10. **How do you make sure the comparison is fair?** Independent fleets, identical events (section 7.2).
11. **What if an event fails halfway?** The change is applied to a clone and committed only on success.
12. **How do you show the past on the slider?** Each event saves a plan version; past queries use it.
13. **What are the units?** Time in minutes, distance in kilometres in road mode (Solomon units in
    straight-line mode).
14. **Why are some back-at-depot times late if driving is short?** Trucks wait for customer windows.
15. **Where does the road data come from?** OSRM on OpenStreetMap; points are snapped to roads.
16. **Why not use real roads for the benchmark?** Published values assume straight-line distances.
17. **How are rail tracks handled?** The driving profile excludes them except at crossings.
18. **What does "snapped" mean?** The point is moved to the nearest drivable road (median 67 m for R101 at VIT).
19. **How many tests and what do they cover?** 130, across parser, solver, simulator, strategies, KPIs,
    API and frontend assets (section 9.1).
20. **What would you do next?** Section 14.

---

## 16. Cheat sheet

### Commands
```
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn api:app --port 8000        # run the app (or start_dashboard.ps1)
.venv/Scripts/python -m pytest -q                          # all tests
.venv/Scripts/python benchmark.py                          # solver vs best-known
.venv/Scripts/python demo_s4.py                            # replay the R101 scenario
```

### File map
| File | Owns |
|---|---|
| `parser.py`, `models.py` | S1 data |
| `solver.py`, `benchmark.py` | S2 planning |
| `sim.py` | S3 time, events, history |
| `baselines.py`, `comparison.py`, `kpis.py` | S4 strategies and numbers |
| `api.py`, `static/` | S5 server and dashboard |
| `tests/`, `demo_s3.py`, `demo_s4.py`, `results/` | S6 validation |
| `render.yaml`, `Dockerfile`, `DEMO.md`, `docs/` | S7 delivery and docs |
| `metric.py` | Road distances (extension) |

### Defaults to remember
25 customers · static search ≤ 3 s · re-plan search ≤ 2 s · costs in tenths of a unit · grid placed at
80 m per unit · average speed 25 km/h · play speed 1 minute per second · depot at the "amazon" point,
Jimmy Carter Road, VIT Vellore.
