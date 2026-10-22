# GreenMile changelog

All changes, in plain words, newest first. Dates are 2026-10-22 unless noted. Commit ids are in brackets.

## Latest: documentation, selectors and click fixes
- **Docs updated** to cover every page, endpoint, parcel unit and truck light [a822bcd, 1302b36].
- **Fixed: selecting a truck and taking it offline while the simulation plays.** The page redrew the truck card,
  the Fleet list and the breakdown dropdown four times a second, which swallowed clicks. Redraws now wait until a
  click is handled, and the dropdown is only rebuilt when the list of trucks changes [f3becc3].
- **Stepper arrows** are centred in their circles (SVG chevrons) [f3becc3] and have a thin green border [681124c].
- **Deep links:** an explicit `?view=` page now wins over `?t=` and `?truck=` [f3becc3].

## Home, parcels, truck lights and layout
- **Home page** (the briefing design) is the first page when the link opens, and the logo returns to it. It shows
  delivery progress, fleet status, the next deadlines and which strategy is winning [6e23358].
- **Truck lights:** broken = light red with a glow, idle = dim white, in use = bright yellow headlight and beam, on
  the map, the Fleet list and the big truck on the Trucks page [6e23358].
- **Parcel weight and volume:** one load unit is 5 kg and 0.03 m³ (a 200-unit truck holds 1000 kg and 6 m³).
  Sizes: Small 25 kg / 0.15 m³, Medium 50 kg / 0.30 m³, Large 100 kg / 0.60 m³ [6e23358].
- **Correct truck loads:** a truck's load is the parcels still on board (assigned, not yet delivered), shown in kg
  and m³ on the Fleet list, truck card, Trucks page, order tiles and Orders table [6e23358].
- **Book page** now fills the width: a live order summary, a parcel-size guide, and the six trucks with the most
  free space [6e23358].
- **Layout:** Fleet list on the side panel, "Change the day" wide and short under the map, the header on one row
  with "Load & solve" at the end, and City, Speed and Strategy in one aligned group [6e23358, c38abe3].
- **Stepper selectors** (arrows with a dot indicator, keyboard left and right) replace the native dropdowns [e3238eb].
- **Working place search** for booking: it now runs on the server (`/geocode`) with caching and clear error
  messages, because searching from the browser was blocked [c38abe3].
- **Book is its own page**, separate from Orders [c38abe3].

## New pages and branding
- **Orders page:** every order with status, truck, time window, planned arrival, slack and parcel size; filters,
  search, sorting, at-risk and late flags [0feb82c].
- **Order booking** in three steps (place, parcel size, delivery time) that shows how each strategy handles the new
  order [0feb82c].
- **Analytics page:** KPI cards, delivered and unassigned charts, fleet activity, delivery per strategy and the
  event history, with event times marked [0feb82c, 35e2792]. Backend: planned arrival per order and `GET /timeseries`.
- **Logo:** the route pin (pin with a dotted route) is the site icon and the favicon [35e2792]. Sidebar icons got text
  labels [0feb82c].
- **Analytics chart rewritten** to be slimmer and easier to read: delivered versus not delivered per strategy [35e2792].

## Docs and demo material
- **README rewritten**, step-by-step notes moved to `docs/TECHNICAL_NOTES.md` [2970350].
- **Project guide** (`docs/PROJECT_GUIDE.md`), **Flow prompt pack** with real 1920x1080 frames, and **URL options** to
  open the dashboard in a given state, for example `?t=60&truck=1&view=compare&clean=1` [2ca2868].
- **Deploy files and demo script:** `render.yaml`, `Dockerfile`, `DEMO.md` [9e6926d].

## Look and feel of trucks and units
- **Cargo truck drawing:** cab window and headlight, larger wheels [ff713ff]; cargo fills from the trailer floor up and
  the truck sits on a road with a chassis [66f778a].
- **Dropdown lists** readable on the dark theme [7032503].
- **Realistic speed:** one time unit is one minute of driving at a chosen average speed (default 25 km/h); the fleet
  panel shows each truck's driving minutes; the status message is a plain line; one brand plus [f85e8fc].
- **Units:** times in minutes, road distances in km [d67ad3d].
- **Real-time play** (1 simulated minute per second, up to 25), GreenMile branding, and truck badges that shrink to
  numbered dots when zoomed out [f9bae31].
- **Fleet panel** with side-view truck icons that move along the roads, plus per-truck activity and times [408c9fb].

## Road distances and the street map
- **Customers, trucks and the depot are snapped to the nearest road**, so nothing is drawn in water [9de20b8].
- **Default depot:** the "amazon" point on Jimmy Carter Road, VIT Vellore [f76ccea].
- **Planning on road distances** (OSRM) in the solver, simulator and KPIs, with a pin tool to place the depot on an
  exact map point; straight-line mode kept for the benchmark [f682e97].
- **Routes drawn along real roads**, vehicles drawn on the road, and a Road km figure [0953e63].
- **Street map** (Leaflet, OpenStreetMap) with a Plain/Street toggle [971c435].

## Dashboard and benchmark
- **Dashboard rebuilt** in the Haulix-inspired dark style, and `benchmark.py` comparing static solves with the
  best-known values (within about 0.3 %) [19f1f6e].

## Foundation (steps S1 to S5)
- Solomon data parser, OR-Tools solver (capacity, time windows, drop penalties), the event simulator (breakdowns,
  new orders, re-planning with locked history), the three recovery strategies (do nothing, cheapest insertion,
  OR-Tools re-plan) with KPIs, and the first web dashboard [72a6985].
