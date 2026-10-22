"""Recovery strategies adapted from the user's baseline prototype to S3 segments.

Call after advancing the clock and applying the disruption, before any recovery.
Normal callers should use Comparison or FleetState's event methods.
"""
import math
from time import perf_counter

from models import RouteStop, VehicleRoute

STRATEGIES = ("do_nothing", "cheapest_insertion", "replan")
SCALE = 10
EPS = 1e-8


def _candidate_route(state, start, order_ids):
    """Earliest feasible schedule from the current position, including depot return."""
    if sum(state.nodes_data[i].demand for i in order_ids) > start.remaining_capacity:
        return None
    now = state.current_time
    departure = max(0, math.ceil((start.available_at-now)*SCALE-EPS))
    absolute = now+departure/SCALE
    stops = [RouteStop(-1-start.vehicle_id, absolute, absolute, absolute, 0)]
    x, y = start.x, start.y
    distance = load = 0
    for customer_id in [*order_ids, 0]:
        customer = state.nodes_data[customer_id]
        leg = int(math.floor(math.hypot(customer.x-x, customer.y-y)*SCALE+.5))
        arrival = departure+leg
        service_start = max(arrival, math.ceil((customer.ready-now)*SCALE-EPS), 0)
        if service_start > math.floor((customer.due-now)*SCALE+EPS):
            return None
        departure = service_start + (math.ceil(customer.service*SCALE) if customer_id else 0)
        distance += leg
        load += customer.demand
        stops.append(RouteStop(customer_id, now+arrival/SCALE, now+service_start/SCALE, now+departure/SCALE, load))
        x, y = customer.x, customer.y
    return VehicleRoute(start.vehicle_id, tuple(stops), distance/SCALE, load)


def _insert_pending(state, seconds):
    deadline = perf_counter()+seconds
    inserted = []
    timed_out = False
    # Stable ordering makes the greedy heuristic reproducible.
    for order_id in sorted(state.unassigned_pool):
        _, starts, prefixes = state.prep_for_ortools()
        best = None
        for start in starts:
            vehicle = state.vehicles[start.vehicle_id]
            prefix = prefixes[start.vehicle_id]
            locked = {s.customer_id for s in prefix if s.kind == "servicing" and s.end > state.current_time}
            suffix = [s.customer_id for s in vehicle.segments
                      if s.kind == "servicing" and s.end > state.current_time and s.customer_id not in locked]
            old_remaining = sum(s.distance for s in vehicle.segments)-vehicle.distance_travelled
            for position in range(len(suffix)+1):
                if perf_counter() >= deadline:
                    timed_out = True
                    break
                order = suffix[:position]+[order_id]+suffix[position:]
                route = _candidate_route(state, start, order)
                if route is None:
                    continue
                key = (route.distance-old_remaining, start.vehicle_id, position)
                if best is None or key < best[0]:
                    best = (key, start, prefix, route)
            if timed_out:
                break
        if best is not None:
            _, start, prefix, route = best
            state.vehicles[start.vehicle_id].segments = prefix+state._route_segments(route, start.x, start.y)
            state.advance_time(state.current_time)
            inserted.append(order_id)
        if timed_out:
            break
    state.last_solve = {"status": "INSERTION_TIME_LIMIT" if timed_out else "INSERTION_COMPLETE",
                       "inserted_customer_ids": inserted, "dropped_customer_ids": sorted(state.unassigned_pool)}


def apply_strategy(state, strategy, time_limit_seconds=2):
    """Mutate an event-only state. Time only recovery, consistently for all methods."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    if isinstance(time_limit_seconds, bool) or not math.isfinite(time_limit_seconds) or not 0 < time_limit_seconds <= 2:
        raise ValueError("Recovery budget must be greater than zero and at most 2 seconds")
    began = perf_counter()
    if strategy == "do_nothing":
        state.last_solve = {"status": "UNCHANGED", "dropped_customer_ids": sorted(state.unassigned_pool)}
    elif strategy == "cheapest_insertion":
        _insert_pending(state, time_limit_seconds)
    else:
        state._replan(time_limit_seconds)
        state.last_solve["solver_seconds"] = state.last_solve["seconds"]
    state.last_solve.update(strategy=strategy, seconds=perf_counter()-began,
                            recovery_limit_seconds=time_limit_seconds)


def _run_copy(state, strategy, seconds=2):
    result = state.fork()
    apply_strategy(result, strategy, seconds)
    result._save_version()
    return result, result.last_solve["seconds"]


def run_do_nothing(state):
    return _run_copy(state, "do_nothing")


def run_cheapest_insertion(state, time_limit_seconds=2):
    return _run_copy(state, "cheapest_insertion", time_limit_seconds)


def run_ortools_replan(state, time_limit_seconds=2):
    return _run_copy(state, "replan", time_limit_seconds)
