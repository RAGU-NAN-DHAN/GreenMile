"""Static capacity/time-window routing, using integer tenths of source units."""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
from time import perf_counter

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from models import DynamicPlan, Plan, Problem, RouteStop, VehicleRoute, VehicleStart
from parser import find_dataset, load_solomon

SCALE = 10


class SolveError(RuntimeError):
    """Search did not return a plan; this is not a dropped-order result."""


def distance_matrix(problem: Problem) -> list[list[int]]:
    return [[int(math.floor(math.hypot(a.x - b.x, a.y - b.y) * SCALE + 0.5))
             for b in problem.nodes] for a in problem.nodes]


def solve_static(problem: Problem, time_limit_seconds: float = 2) -> Plan:
    started = perf_counter()
    if isinstance(time_limit_seconds, bool) or not math.isfinite(time_limit_seconds) or not 0 < time_limit_seconds <= 3:
        raise ValueError("time_limit_seconds must be greater than 0 and at most 3")
    nodes = problem.nodes
    if problem.vehicle_count <= 0 or problem.vehicle_capacity <= 0:
        raise ValueError("Vehicle count and capacity must be positive")
    if problem.depot.id != 0 or problem.depot.demand != 0:
        raise ValueError("Depot must have ID 0 and zero demand")
    if len({c.id for c in nodes}) != len(nodes):
        raise ValueError("Customer IDs must be unique")
    for c in nodes:
        if (not all(math.isfinite(v) for v in (c.x, c.y, c.ready, c.due, c.service))
                or c.ready < 0 or c.ready > c.due or c.service < 0
                or not isinstance(c.demand, int) or c.demand < 0):
            raise ValueError(f"Invalid customer values: {c.id}")
    distances = distance_matrix(problem)
    # Inward window rounding and upward service rounding avoid relaxing constraints.
    windows = [(math.ceil(c.ready * SCALE), math.floor(c.due * SCALE)) for c in nodes]
    services = [math.ceil(c.service * SCALE) for c in nodes]
    horizon = windows[0][1]
    if windows[0][0] + services[0] > horizon:
        raise ValueError("Depot window cannot accommodate departure service")
    manager = pywrapcp.RoutingIndexManager(len(nodes), problem.vehicle_count, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(a, b):
        return distances[manager.IndexToNode(a)][manager.IndexToNode(b)]

    distance_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(distance_index)
    demand_index = routing.RegisterUnaryTransitCallback(lambda i: nodes[manager.IndexToNode(i)].demand)
    routing.AddDimensionWithVehicleCapacity(demand_index, 0,
        [problem.vehicle_capacity] * problem.vehicle_count, True, "Capacity")

    def time_callback(a, b):
        i, j = manager.IndexToNode(a), manager.IndexToNode(b)
        return services[i] + distances[i][j]

    time_index = routing.RegisterTransitCallback(time_callback)
    routing.AddDimension(time_index, horizon, horizon, False, "Time")
    time_dimension = routing.GetDimensionOrDie("Time")
    # Exceeds an upper bound on total route distance: serving an additional order
    # always improves the objective, though limited search may miss that solution.
    penalty = int((len(problem.customers) + problem.vehicle_count) *
                  max(max(row) for row in distances) + 1)
    if penalty * max(1, len(problem.customers)) >= 2**62:
        raise ValueError("Problem costs exceed safe integer bounds")
    for i in range(1, len(nodes)):
        index = manager.NodeToIndex(i)
        routing.AddDisjunction([index], penalty)
        low, high = windows[i]
        high = min(high, horizon)
        if low > high:
            # Optional, impossible windows must drop only this stop, not invalidate
            # the entire model through an empty CumulVar range.
            routing.ActiveVar(index).SetValue(0)
        else:
            time_dimension.CumulVar(index).SetRange(low, high)
    for vehicle in range(problem.vehicle_count):
        time_dimension.CumulVar(routing.Start(vehicle)).SetValue(windows[0][0])
        time_dimension.CumulVar(routing.End(vehicle)).SetRange(*windows[0])

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.FromMilliseconds(max(1, int(time_limit_seconds * 1000)))
    solution = routing.SolveWithParameters(parameters)
    status = routing_enums_pb2.RoutingSearchStatus.DESCRIPTOR.enum_types_by_name["Value"].values_by_number[routing.status()].name
    if solution is None:
        raise SolveError(f"No plan returned ({status}); search limit {time_limit_seconds:g}s")
    dropped = tuple(nodes[i].id for i in range(1, len(nodes))
                    if solution.Value(routing.NextVar(manager.NodeToIndex(i))) == manager.NodeToIndex(i))
    routes = []
    for vehicle in range(problem.vehicle_count):
        if not routing.IsVehicleUsed(solution, vehicle):
            continue
        index = routing.Start(vehicle)
        sequence = [0]
        while not routing.IsEnd(index):
            index = solution.Value(routing.NextVar(index))
            sequence.append(manager.IndexToNode(index))
        # Materialize the earliest feasible schedule for the selected sequence.
        # Waiting happens at the destination, so arrival differs from service start.
        start = windows[0][0]
        departure = start + services[0]
        stops = [RouteStop(0, start / SCALE, start / SCALE, departure / SCALE, 0)]
        load = 0
        length = 0
        for previous, current in zip(sequence, sequence[1:]):
            length += distances[previous][current]
            arrival = departure + distances[previous][current]
            service_start = max(arrival, windows[current][0])
            if service_start > windows[current][1]:
                raise SolveError("Extracted schedule violates a time window")
            departure = service_start + (services[current] if current else 0)
            load += nodes[current].demand
            stops.append(RouteStop(nodes[current].id, arrival / SCALE, service_start / SCALE,
                                   departure / SCALE, load))
        routes.append(VehicleRoute(vehicle, tuple(stops), length / SCALE, load))
    return Plan(problem.name, tuple(routes), dropped,
                round(sum(r.distance for r in routes), 1), len(routes),
                perf_counter() - started, float(time_limit_seconds), int(solution.ObjectiveValue()),
                penalty, status)


def solve_dynamic(problem: Problem, starts: tuple[VehicleStart, ...], now: float,
                  time_limit_seconds: float = 2) -> DynamicPlan:
    """Route unlocked orders from current positions; return absolute schedules.

    Synthetic start stops have negative IDs. Empty routes still return to depot.
    """
    began = perf_counter()
    if not math.isfinite(now) or now < 0:
        raise ValueError("now must be finite and nonnegative")
    if isinstance(time_limit_seconds, bool) or not math.isfinite(time_limit_seconds) or not 0 < time_limit_seconds <= 2:
        raise ValueError("Dynamic search limit must be greater than zero and at most 2 seconds")
    if len({s.vehicle_id for s in starts}) != len(starts):
        raise ValueError("Duplicate vehicle IDs")
    for start in starts:
        if (not all(math.isfinite(v) for v in (start.x, start.y, start.available_at))
                or start.available_at < now or not isinstance(start.remaining_capacity, int)
                or start.remaining_capacity < 0):
            raise ValueError("Invalid vehicle start")
    if not starts:
        return DynamicPlan((), tuple(c.id for c in problem.customers), perf_counter()-began,
                           "NO_AVAILABLE_VEHICLES", time_limit_seconds)
    horizon = math.floor((problem.depot.due-now)*SCALE + 1e-8)
    if horizon < 0:
        if any((s.x, s.y) != (problem.depot.x, problem.depot.y) for s in starts):
            raise SolveError("Depot has closed while vehicles are away")
        return DynamicPlan((), tuple(c.id for c in problem.customers), perf_counter()-began,
                           "DEPOT_CLOSED", time_limit_seconds)
    customers = problem.nodes
    coordinates = [(c.x, c.y) for c in customers] + [(s.x, s.y) for s in starts]
    distances = [[int(math.floor(math.hypot(x-a, y-b)*SCALE+.5)) for a, b in coordinates]
                 for x, y in coordinates]
    service = [math.ceil(c.service*SCALE) if c.id else 0 for c in customers] + [0]*len(starts)
    start_nodes = list(range(len(customers), len(coordinates)))
    manager = pywrapcp.RoutingIndexManager(len(coordinates), len(starts), start_nodes, [0]*len(starts))
    routing = pywrapcp.RoutingModel(manager)
    distance_index = routing.RegisterTransitCallback(
        lambda a, b: distances[manager.IndexToNode(a)][manager.IndexToNode(b)])
    routing.SetArcCostEvaluatorOfAllVehicles(distance_index)
    demands = [c.demand for c in customers] + [0]*len(starts)
    demand_index = routing.RegisterUnaryTransitCallback(lambda a: demands[manager.IndexToNode(a)])
    routing.AddDimensionWithVehicleCapacity(demand_index, 0, [s.remaining_capacity for s in starts], True, "Capacity")
    time_index = routing.RegisterTransitCallback(lambda a, b:
        service[manager.IndexToNode(a)] + distances[manager.IndexToNode(a)][manager.IndexToNode(b)])
    routing.AddDimension(time_index, horizon, horizon, False, "Time")
    times = routing.GetDimensionOrDie("Time")
    penalty = int((len(problem.customers)+len(starts))*max(max(row) for row in distances)+1)
    if penalty*max(1, len(problem.customers)) >= 2**62:
        raise ValueError("Problem costs exceed safe integer bounds")
    windows = [(max(0, math.ceil((c.ready-now)*SCALE-1e-8)),
                min(horizon, math.floor((c.due-now)*SCALE+1e-8))) for c in customers]
    for i in range(1, len(customers)):
        index = manager.NodeToIndex(i)
        routing.AddDisjunction([index], penalty)
        lo, hi = windows[i]
        if lo > hi:
            routing.ActiveVar(index).SetValue(0)
        else:
            times.CumulVar(index).SetRange(lo, hi)
    starts_at = []
    for v, start in enumerate(starts):
        available = max(0, math.ceil((start.available_at-now)*SCALE-1e-8))
        if available > horizon or available + distances[start_nodes[v]][0] > horizon:
            raise SolveError(f"Vehicle {start.vehicle_id} cannot return before depot closes")
        starts_at.append(available)
        times.CumulVar(routing.Start(v)).SetValue(available)
        times.CumulVar(routing.End(v)).SetRange(*windows[0])
        routing.SetVehicleUsedWhenEmpty(True, v)
    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.FromMilliseconds(max(1, int(time_limit_seconds*1000)))
    solution = routing.SolveWithParameters(parameters)
    status = routing_enums_pb2.RoutingSearchStatus.DESCRIPTOR.enum_types_by_name["Value"].values_by_number[routing.status()].name
    if solution is None:
        raise SolveError(f"Dynamic solve returned no plan ({status})")
    dropped = tuple(customers[i].id for i in range(1, len(customers))
                    if solution.Value(routing.NextVar(manager.NodeToIndex(i))) == manager.NodeToIndex(i))
    routes = []
    for v, start in enumerate(starts):
        index = routing.Start(v)
        previous = start_nodes[v]
        departure = starts_at[v]
        absolute = now+departure/SCALE
        stops = [RouteStop(-1-start.vehicle_id, absolute, absolute, absolute, 0)]
        load = length = 0
        while not routing.IsEnd(index):
            index = solution.Value(routing.NextVar(index))
            current = manager.IndexToNode(index)
            leg = distances[previous][current]
            length += leg
            arrival = departure+leg
            service_start = max(arrival, windows[current][0])
            if service_start > windows[current][1]:
                raise SolveError("Dynamic extraction violates time window")
            departure = service_start+service[current]
            load += demands[current]
            stops.append(RouteStop(customers[current].id, now+arrival/SCALE,
                                   now+service_start/SCALE, now+departure/SCALE, load))
            previous = current
        routes.append(VehicleRoute(start.vehicle_id, tuple(stops), length/SCALE, load))
    return DynamicPlan(tuple(routes), dropped, perf_counter()-began, status, time_limit_seconds)


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("paths", nargs="*", type=Path)
    cli.add_argument("--seconds", type=float, default=2)
    cli.add_argument("--json", type=Path, help="Save plans for inspection or later simulation")
    args = cli.parse_args()
    data = Path(__file__).parent / "data" / "solomon"
    paths = args.paths or [find_dataset(data, name) for name in ("C101", "R101", "RC101")]
    plans = [solve_static(load_solomon(path), args.seconds) for path in paths]
    print("Dataset | Distance | Vehicles used | Dropped | Elapsed seconds")
    for plan in plans:
        print(f"{plan.problem_name} | {plan.total_distance:.1f} | {plan.vehicle_count} | "
              f"{len(plan.dropped_customer_ids)} | {plan.solve_time_seconds:.3f}")
        for route in plan.routes:
            print(f"  Vehicle {route.vehicle_id}: " + " -> ".join(
                f"{s.customer_id}@{s.service_start:g}" for s in route.stops))
        print(f"  Dropped IDs: {list(plan.dropped_customer_ids)}; status: {plan.status}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps([asdict(p) for p in plans], indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
