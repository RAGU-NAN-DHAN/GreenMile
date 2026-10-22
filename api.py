"""S5 dashboard: one process and one in-memory comparison session."""
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal
from uuid import uuid4
import httpx
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from comparison import Comparison
from kpis import calculate_kpis
from parser import find_dataset, load_solomon
from solver import SolveError, solve_static

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Last-Mile Router", version="5.0")
lock = RLock()
current: Comparison | None = None
session_id = ""
revision = 0
dataset_name = ""

# Road geometry for the street-map view. Cosmetic: the solver still uses Solomon x/y distances.
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lng1},{lat1};{lng2},{lat2}?overview=full&geometries=geojson"
road_cache: dict[tuple, dict] = {}
road_lock = RLock()


def fetch_road(lat1, lng1, lat2, lng2):
    """Ask the public OSRM demo server for the driving route between two points."""
    response = httpx.get(OSRM_URL.format(lat1=lat1, lng1=lng1, lat2=lat2, lng2=lng2), timeout=10,
                         headers={"User-Agent": "GreenMile-student-demo"})
    response.raise_for_status()
    route = response.json()["routes"][0]
    return {"distance_m": route["distance"],
            "coords": [[lat, lng] for lng, lat in route["geometry"]["coordinates"]]}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LoadPayload(StrictModel):
    dataset: Literal["C101", "R101", "RC101"] = "R101"
    reset: bool = False


class EventBase(StrictModel):
    time: float = Field(ge=0)
    session_id: str
    revision: int = Field(ge=0, strict=True)


class Breakdown(EventBase):
    event_type: Literal["breakdown"]
    vehicle_id: int = Field(ge=0, strict=True)


class NewOrder(EventBase):
    event_type: Literal["new_order"]
    x: float
    y: float
    demand: int = Field(ge=0, strict=True)
    ready: float = Field(ge=0)
    due: float = Field(ge=0)
    service: float = Field(default=10, ge=0)

    @model_validator(mode="after")
    def valid_window(self):
        if self.ready > self.due:
            raise ValueError("Ready time must not exceed due time")
        return self


EventPayload = Annotated[Breakdown | NewOrder, Body(discriminator="event_type")]


def require_loaded():
    if current is None:
        raise HTTPException(409, "Load a dataset first")
    return current


def metadata():
    comparison = require_loaded()
    state = comparison.states["replan"]
    return {"dataset": dataset_name, "session_id": session_id, "revision": revision,
            "max_time": state.problem.depot.due, "committed_time": state.current_time,
            "event_count": len(comparison.events)}


def validate_time(t):
    if t > require_loaded().states["replan"].problem.depot.due:
        raise HTTPException(422, "Time exceeds the depot closing time")


def event_summary(event):
    return {key: event[key] for key in ("type", "time", "payload", "strategies")}


def chart_state(snapshot):
    t = snapshot["t"]
    routes, vehicles, assignment = [], [], {}
    for key, vehicle in snapshot["vehicles"].items():
        future_count = 0
        past, future, legs = [], [], []
        for segment in vehicle["segments"]:
            if segment["kind"] == "servicing":
                assignment[segment["customer_id"]] = int(key)
                future_count += segment["end"] > t
            if segment["kind"] != "travelling":
                continue
            a, b = [segment["x1"], segment["y1"]], [segment["x2"], segment["y2"]]
            span = segment["end"]-segment["start"]
            progress = 1.0 if segment["end"] <= t else 0.0 if segment["start"] >= t or span <= 0 else (t-segment["start"])/span
            legs.append({"a": a, "b": b, "p": progress})
            if segment["end"] <= t:
                past.append([a, b])
            elif segment["start"] >= t:
                future.append([a, b])
            else:
                f = (t-segment["start"])/(segment["end"]-segment["start"])
                middle = [a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f]
                past.append([a, middle]); future.append([middle, b])
        routes.append({"vehicle_id": int(key), "past": past, "future": future, "legs": legs})
        vehicles.append({"id": int(key), "x": vehicle["x"], "y": vehicle["y"],
                         "status": vehicle["status"], "used_capacity": vehicle["used_capacity"],
                         "capacity": vehicle["capacity"], "pending_count": future_count,
                         "served_count": len(vehicle["served"]), "distance_travelled": vehicle["distance_travelled"]})
    unassigned = set(snapshot["unassigned_pool"])
    customers = [{**customer, "status": "unassigned" if int(i) in unassigned else snapshot["node_status"][int(i)],
                  "vehicle_id": assignment.get(int(i))}
                 for i, customer in snapshot["customers"].items() if int(i) != 0]
    return {"depot": snapshot["customers"]["0"], "customers": customers,
            "vehicles": vehicles, "routes": routes, "last_solve": snapshot["last_solve"]}


def state_response(t):
    comparison = require_loaded()
    validate_time(t)
    snapshots = comparison.snapshot(t)
    events = [event_summary(e) for e in comparison.events if e["time"] <= t]
    return {**metadata(), "time": t,
            "strategies": {name: chart_state(s) for name, s in snapshots.items()},
            "kpis": {name: calculate_kpis(s) for name, s in snapshots.items()},
            "events": events, "latest_event": events[-1] if events else None}


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/load")
def load(payload: LoadPayload):
    global current, session_id, revision, dataset_name
    with lock:
        if current is not None and current.events and not payload.reset:
            raise HTTPException(409, "Reset confirmation is required to discard existing events")
        try:
            problem = load_solomon(find_dataset(ROOT / "data" / "solomon", payload.dataset))
            candidate = Comparison(problem, solve_static(problem, time_limit_seconds=2))
        except SolveError as exc:
            raise HTTPException(503, str(exc)) from exc
        current = candidate
        dataset_name, session_id, revision = payload.dataset, uuid4().hex, 0
        return state_response(0)


@app.get("/state")
def get_state(t: Annotated[float, Query(ge=0, allow_inf_nan=False)] = 0):
    with lock:
        return state_response(t)


@app.post("/event")
def event(payload: EventPayload):
    global revision
    with lock:
        comparison = require_loaded()
        if payload.session_id != session_id or payload.revision != revision:
            raise HTTPException(409, "This session changed. Refresh the view before applying an event.")
        validate_time(payload.time)
        if payload.time < comparison.states["replan"].current_time:
            raise HTTPException(409, "Historical view: events must be at or after the last committed event")
        try:
            if isinstance(payload, Breakdown):
                comparison.break_vehicle(payload.time, payload.vehicle_id)
            else:
                comparison.add_order(payload.time, payload.x, payload.y, payload.demand,
                                     (payload.ready, payload.due), service=payload.service)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        revision += 1
        return state_response(payload.time)


@app.get("/kpis")
def get_kpis(t: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None):
    with lock:
        comparison = require_loaded()
        t = comparison.states["replan"].current_time if t is None else t
        validate_time(t)
        events = [event_summary(e) for e in comparison.events if e["time"] <= t]
        return {**metadata(), "time": t, "kpis": comparison.kpis(t), "events": events,
                "latest_event": events[-1] if events else None}


@app.get("/road")
def road(lat1: Annotated[float, Query(ge=-90, le=90)], lng1: Annotated[float, Query(ge=-180, le=180)],
         lat2: Annotated[float, Query(ge=-90, le=90)], lng2: Annotated[float, Query(ge=-180, le=180)]):
    """Cached road route between two points; 502 when the routing service is unreachable."""
    key = tuple(round(v, 5) for v in (lat1, lng1, lat2, lng2))
    with road_lock:
        if key in road_cache:
            return road_cache[key]
    try:
        result = fetch_road(*key)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise HTTPException(502, "Road routing service unavailable") from exc
    with road_lock:
        road_cache[key] = result
    return result


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
