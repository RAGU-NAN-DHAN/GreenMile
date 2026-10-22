"""Forward event simulation with immutable execution segments and historical views."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import math
from time import perf_counter

from models import Customer, Plan, Problem, VehicleRoute, VehicleStart
from solver import SolveError, solve_dynamic

EPS = 1e-8


@dataclass(frozen=True)
class Segment:
    kind: str
    start: float
    end: float
    x1: float
    y1: float
    x2: float
    y2: float
    distance: float = 0
    customer_id: int | None = None

    def fraction(self, t):
        return 1.0 if self.end == self.start else max(0., min(1., (t-self.start)/(self.end-self.start)))

    def position(self, t):
        fraction = self.fraction(t)
        return self.x1+(self.x2-self.x1)*fraction, self.y1+(self.y2-self.y1)*fraction


@dataclass
class VehicleState:
    vehicle_id: int
    capacity: int
    x: float
    y: float
    status: str = "idle"
    served: list[int] = field(default_factory=list)
    used_capacity: int = 0
    distance_travelled: float = 0
    segments: list[Segment] = field(default_factory=list)


class FleetState:
    def __init__(self, problem: Problem, plan: Plan):
        if plan.problem_name != problem.name:
            raise ValueError("Plan does not belong to this problem")
        self.problem = problem
        self.current_time = 0.0
        self.nodes_data = {c.id: c for c in problem.nodes}
        self.vehicles = {i: VehicleState(i, problem.vehicle_capacity, problem.depot.x, problem.depot.y)
                         for i in range(problem.vehicle_count)}
        self.broken_vehicles: set[int] = set()
        self.node_status = {c.id: "pending" for c in problem.customers}
        self.unassigned_pool: set[int] = set(plan.dropped_customer_ids)
        self.events: list[dict] = []
        self.last_solve = {"status": plan.status, "seconds": plan.solve_time_seconds}
        self._versions = []
        assigned = []
        for route in plan.routes:
            if route.vehicle_id not in self.vehicles:
                raise ValueError("Unknown vehicle in initial plan")
            assigned.extend(s.customer_id for s in route.stops[1:-1])
            self.vehicles[route.vehicle_id].segments = self._route_segments(route, problem.depot.x, problem.depot.y)
        if (len(assigned) != len(set(assigned)) or set(assigned) & self.unassigned_pool
                or set(assigned) | self.unassigned_pool != set(self.node_status)):
            raise ValueError("Initial plan must account for every customer exactly once")
        self.advance_time(0)
        self._save_version()

    def _route_segments(self, route: VehicleRoute, x: float, y: float) -> list[Segment]:
        result = []
        previous = route.stops[0]
        for stop in route.stops[1:]:
            c = self.nodes_data[stop.customer_id]
            # Leg travel duration equals rounded distance in this model.
            distance = stop.arrival-previous.departure
            if distance < -EPS or stop.service_start < stop.arrival-EPS or stop.departure < stop.service_start-EPS:
                raise ValueError("Invalid route timing")
            result.append(Segment("travelling", previous.departure, stop.arrival, x, y, c.x, c.y,
                                  max(0., distance), c.id))
            if stop.service_start > stop.arrival:
                result.append(Segment("waiting", stop.arrival, stop.service_start, c.x, c.y, c.x, c.y, customer_id=c.id))
            if c.id:
                result.append(Segment("servicing", stop.service_start, stop.departure, c.x, c.y, c.x, c.y, customer_id=c.id))
            previous, x, y = stop, c.x, c.y
        return result

    def advance_time(self, new_time: float):
        if not math.isfinite(new_time) or new_time < self.current_time:
            raise ValueError("Event time must be finite and cannot move backward")
        self.current_time = float(new_time)
        for v in self.vehicles.values():
            v.status = "broken" if v.vehicle_id in self.broken_vehicles else "idle"
            v.x, v.y = self.problem.depot.x, self.problem.depot.y
            v.distance_travelled = 0.
            for segment in v.segments:
                if segment.start > new_time:
                    break
                v.x, v.y = segment.position(new_time)
                v.distance_travelled += segment.distance*segment.fraction(new_time)
                if segment.kind == "servicing" and segment.end <= new_time and segment.customer_id not in v.served:
                    v.served.append(segment.customer_id)
                if segment.start <= new_time < segment.end:
                    if v.vehicle_id not in self.broken_vehicles:
                        v.status = segment.kind
                    break
            v.used_capacity = sum(self.nodes_data[i].demand for i in v.served)
        served = {i for v in self.vehicles.values() for i in v.served}
        servicing = {s.customer_id for v in self.vehicles.values() for s in v.segments
                     if s.kind == "servicing" and s.start <= new_time < s.end}
        self.node_status = {i: "served" if i in served else "servicing" if i in servicing else "pending"
                            for i in self.nodes_data if i != 0}
        assigned = {s.customer_id for v in self.vehicles.values() for s in v.segments
                    if s.kind == "servicing" and s.customer_id not in served}
        self.unassigned_pool = set(self.node_status)-served-assigned
        return self

    def _prefix(self, v, keep_service=True):
        prefix = []
        for segment in v.segments:
            if segment.end <= self.current_time:
                prefix.append(segment)
            elif segment.start <= self.current_time < segment.end:
                if keep_service and segment.kind == "servicing":
                    prefix.append(segment)
                elif segment.start < self.current_time:
                    x, y = segment.position(self.current_time)
                    prefix.append(Segment("interrupted_service" if segment.kind == "servicing" else segment.kind,
                                          segment.start, self.current_time, segment.x1, segment.y1, x, y,
                                          segment.distance*segment.fraction(self.current_time), segment.customer_id))
                break
            else:
                break
        return prefix

    def prep_for_ortools(self):
        starts, prefixes, locked = [], {}, set()
        for vehicle_id, v in self.vehicles.items():
            if vehicle_id in self.broken_vehicles:
                continue
            prefix = self._prefix(v)
            prefixes[vehicle_id] = prefix
            current = next((s for s in prefix if s.kind == "servicing" and s.end > self.current_time), None)
            available = max(self.current_time, self.problem.depot.ready)
            reserved = 0
            if current:
                locked.add(current.customer_id)
                reserved = self.nodes_data[current.customer_id].demand
                available = current.end
            starts.append(VehicleStart(vehicle_id, v.x, v.y, available,
                                       v.capacity-v.used_capacity-reserved))
        pending = tuple(c for i, c in self.nodes_data.items()
                        if i and self.node_status[i] != "served" and i not in locked)
        problem = Problem(self.problem.name, len(starts), self.problem.vehicle_capacity, self.problem.depot, pending)
        return problem, tuple(starts), prefixes

    def _replan(self, seconds):
        problem, starts, prefixes = self.prep_for_ortools()
        began = perf_counter()
        try:
            plan = solve_dynamic(problem, starts, self.current_time, seconds)
        except SolveError as exc:
            # Keep healthy itineraries; new/stranded orders remain unassigned.
            self.last_solve = {"status": "SOLVE_FAILED", "error": str(exc), "seconds": perf_counter()-began}
            self.advance_time(self.current_time)
            return
        replacements = {}
        by_id = {s.vehicle_id: s for s in starts}
        for route in plan.routes:
            start = by_id[route.vehicle_id]
            replacements[route.vehicle_id] = prefixes[route.vehicle_id] + self._route_segments(route, start.x, start.y)
        for vehicle_id, prefix in prefixes.items():
            self.vehicles[vehicle_id].segments = replacements.get(vehicle_id, prefix)
        self.last_solve = {"status": plan.status, "seconds": plan.solve_time_seconds,
                           "search_limit_seconds": plan.search_limit_seconds,
                           "dropped_customer_ids": list(plan.dropped_customer_ids)}
        self.advance_time(self.current_time)

    def _validate_event(self, t, seconds):
        if not math.isfinite(t) or t < self.current_time:
            raise ValueError("Event time must be finite and cannot move backward")
        if isinstance(seconds, bool) or not math.isfinite(seconds) or not 0 < seconds <= 2:
            raise ValueError("Search limit must be greater than zero and at most 2")

    def _commit_event(self, t, kind, payload, seconds, mutation, strategy="replan"):
        self._validate_event(t, seconds)
        from baselines import STRATEGIES, apply_strategy
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy}")
        candidate = self._clone()
        candidate.advance_time(t)
        before = candidate._snapshot()
        mutation(candidate)
        candidate.advance_time(t)
        apply_strategy(candidate, strategy, seconds)
        candidate.events.append({"type": kind, "time": t, "payload": payload, "strategy": strategy,
                                 "before": before, "after": candidate._snapshot()})
        versions = self._versions
        self.__dict__.update(candidate.__dict__)
        self._versions = versions
        self._save_version()
        return deepcopy(self.events[-1])

    def replan(self, t, time_limit_seconds=2):
        return self._commit_event(t, "replan", {}, time_limit_seconds, lambda state: None)

    def break_vehicle(self, t, vehicle_id, time_limit_seconds=2, *, strategy="replan"):
        if vehicle_id not in self.vehicles:
            raise ValueError("Unknown vehicle")
        if vehicle_id in self.broken_vehicles:
            raise ValueError("Vehicle is already broken")
        def mutation(state):
            v = state.vehicles[vehicle_id]
            v.segments = state._prefix(v, keep_service=False)
            state.broken_vehicles.add(vehicle_id)
        return self._commit_event(t, "break_vehicle", {"vehicle_id": vehicle_id}, time_limit_seconds, mutation, strategy)

    def add_order(self, t, x, y, demand, window, service=10, customer_id=None, time_limit_seconds=2, *, strategy="replan"):
        if (len(window) != 2 or not all(math.isfinite(v) for v in (x, y, *window, service))
                or window[0] < 0 or window[0] > window[1] or service < 0
                or isinstance(demand, bool) or not isinstance(demand, int) or demand < 0):
            raise ValueError("Invalid order coordinates, demand, service, or window")
        if customer_id is None:
            customer_id = max(self.nodes_data)+1
        if isinstance(customer_id, bool) or not isinstance(customer_id, int) or customer_id <= 0 or customer_id in self.nodes_data:
            raise ValueError("New order ID must be a unique positive integer")
        customer = Customer(customer_id, x, y, demand, window[0], window[1], service)
        return self._commit_event(t, "add_order", asdict(customer), time_limit_seconds,
                                  lambda state: state.nodes_data.update({customer_id: customer}), strategy)

    def _snapshot(self):
        travelled = sum(v.distance_travelled for v in self.vehicles.values())
        planned = sum(s.distance for v in self.vehicles.values() for s in v.segments)
        return {"t": self.current_time, "vehicles": {str(i): asdict(v) for i, v in self.vehicles.items()},
                "customers": {str(i): asdict(c) for i, c in self.nodes_data.items()},
                "node_status": dict(self.node_status), "unassigned_pool": sorted(self.unassigned_pool),
                "broken_vehicles": sorted(self.broken_vehicles),
                "distance_travelled": travelled, "remaining_distance": max(0., planned-travelled),
                "total_distance": planned, "last_solve": deepcopy(self.last_solve)}

    def _clone(self):
        clone = object.__new__(FleetState)
        clone.__dict__ = deepcopy({k: v for k, v in self.__dict__.items() if k not in ("_versions", "events")})
        clone.events = list(self.events)
        clone._versions = []
        return clone

    def _save_version(self):
        self._versions.append((self.current_time, self._clone()))

    def fork(self):
        """Independent mutable fleet with the same immutable historical versions."""
        clone = self._clone()
        clone._versions = list(self._versions)
        clone.events = deepcopy(self.events)
        return clone

    def snapshot(self, t=None):
        """Read-only view. Past queries use the plan version in effect then."""
        if t is None:
            return self._snapshot()
        if not math.isfinite(t) or t < 0:
            raise ValueError("Snapshot time must be finite and nonnegative")
        candidates = [state for at, state in self._versions if at <= t]
        view = candidates[-1]._clone()
        view.advance_time(t)
        return view._snapshot()


State = FleetState


def replan(state: FleetState, t: float, time_limit_seconds=2):
    return state.replan(t, time_limit_seconds)
