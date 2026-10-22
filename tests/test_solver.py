import math
from pathlib import Path

import pytest

from models import Customer, Problem
from parser import find_dataset, load_solomon
from solver import distance_matrix, solve_static

DATA = Path(__file__).resolve().parents[1] / "data" / "solomon"


def check_plan(problem, plan):
    lookup = {c.id: c for c in problem.nodes}
    served = []
    distance = 0
    for route in plan.routes:
        assert route.stops[0].customer_id == route.stops[-1].customer_id == 0
        assert 0 <= route.vehicle_id < problem.vehicle_count
        load = 0
        route_distance = 0
        for prev, stop in zip(route.stops, route.stops[1:]):
            a, b = lookup[prev.customer_id], lookup[stop.customer_id]
            leg = math.floor(math.hypot(a.x-b.x, a.y-b.y)*10 + 0.5)/10
            route_distance += leg
            assert stop.arrival == pytest.approx(prev.departure + leg)
            assert stop.service_start >= stop.arrival
            assert b.ready <= stop.service_start <= b.due
            assert stop.departure == pytest.approx(stop.service_start + (math.ceil(b.service*10)/10 if b.id else 0))
            if b.id:
                served.append(b.id)
                load += b.demand
            assert stop.cumulative_demand == load
        assert route.load == load <= problem.vehicle_capacity
        assert route.distance == pytest.approx(route_distance)
        distance += route_distance
    assert len(served) == len(set(served))
    assert set(served).isdisjoint(plan.dropped_customer_ids)
    assert set(served) | set(plan.dropped_customer_ids) == {c.id for c in problem.customers}
    assert plan.total_distance == pytest.approx(distance)
    assert plan.vehicle_count == len(plan.routes)
    assert plan.objective_cost == round(distance*10) + len(plan.dropped_customer_ids)*plan.drop_penalty


@pytest.mark.parametrize("name", ["C101", "R101", "RC101"])
def test_benchmark_constraints(name):
    problem = load_solomon(find_dataset(DATA, name))
    plan = solve_static(problem)
    check_plan(problem, plan)
    assert not plan.dropped_customer_ids
    assert plan.search_limit_seconds == 2
    # Search has a hard configured budget; model creation/OS scheduling adds overhead.
    assert plan.solve_time_seconds < 3.5


def make_problem(customers, capacity=10, due=100, vehicles=1):
    return Problem("TEST", vehicles, capacity, Customer(0, 0, 0, 0, 0, due, 0), tuple(customers))


def test_waiting_and_service():
    problem = make_problem([Customer(42, 3, 4, 2, 20, 25, 7)])
    plan = solve_static(problem, .1)
    check_plan(problem, plan)
    stop = plan.routes[0].stops[1]
    assert (stop.arrival, stop.service_start, stop.departure) == (5, 20, 27)
    assert plan.routes[0].stops[-1].arrival == 32


@pytest.mark.parametrize("customer", [
    Customer(7, 1, 0, 11, 0, 50, 0),  # Too much demand.
    Customer(7, 10, 0, 1, 0, 1, 0),  # Cannot reach in time.
    Customer(7, 1, 0, 1, 101, 110, 0),  # Beyond depot horizon.
    Customer(7, 1, 0, 1, .01, .09, 0),  # Empty scaled window.
    Customer(7, 40, 0, 1, 0, 100, 30),  # Cannot return before depot closes.
])
def test_impossible_order_is_dropped(customer):
    problem = make_problem([customer])
    plan = solve_static(problem, .1)
    check_plan(problem, plan)
    assert plan.dropped_customer_ids == (7,)
    assert not plan.routes


def test_capacity_requires_multiple_routes():
    problem = make_problem([Customer(i, i, 0, 6, 0, 100, 0) for i in (2, 9)], vehicles=2)
    plan = solve_static(problem, .1)
    check_plan(problem, plan)
    assert plan.vehicle_count == 2 and not plan.dropped_customer_ids


def test_capacity_shortage_drops_one():
    problem = make_problem([Customer(i, i, 0, 6, 0, 100, 0) for i in (2, 9)])
    plan = solve_static(problem, .1)
    check_plan(problem, plan)
    assert len(plan.dropped_customer_ids) == 1


def test_empty_problem():
    problem = make_problem([])
    plan = solve_static(problem, .1)
    check_plan(problem, plan)
    assert plan.vehicle_count == 0 and plan.total_distance == 0


def test_integer_distances():
    matrix = distance_matrix(make_problem([Customer(1, 1, 1, 1, 0, 100, 0)]))
    assert matrix == [[0, 14], [14, 0]]
    assert all(type(value) is int for row in matrix for value in row)


@pytest.mark.parametrize("seconds", [0, -1, 3.01, float("nan"), True])
def test_search_budget_validation(seconds):
    with pytest.raises(ValueError, match="at most 3"):
        solve_static(make_problem([]), seconds)
