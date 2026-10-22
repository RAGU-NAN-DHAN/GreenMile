from copy import deepcopy

import pytest

import sim
from models import Customer, Plan, Problem, RouteStop, VehicleRoute, VehicleStart
from sim import FleetState
from solver import SolveError, solve_dynamic


def fleet(vehicles=2):
    problem = Problem("TEST", vehicles, 10, Customer(0, 0, 0, 0, 0, 100, 0), (
        Customer(1, 10, 0, 4, 20, 50, 5), Customer(2, 20, 0, 4, 0, 100, 5)))
    route = VehicleRoute(0, (RouteStop(0, 0, 0, 0, 0), RouteStop(1, 10, 20, 25, 4),
                             RouteStop(2, 35, 35, 40, 8), RouteStop(0, 60, 60, 60, 8)), 40, 8)
    plan = Plan("TEST", (route,), (), 40, 1, 0, 2, 400, 1000, "TEST")
    return FleetState(problem, plan)


def assert_invariants(state):
    assigned = []
    served = []
    for i, v in state.vehicles.items():
        served.extend(v.served)
        future = [s for s in v.segments if s.kind == "servicing" and s.end > state.current_time]
        assert v.used_capacity + sum(state.nodes_data[s.customer_id].demand for s in future) <= v.capacity
        assert v.used_capacity == sum(state.nodes_data[c].demand for c in v.served)
        if i in state.broken_vehicles:
            assert not future
            assert all(s.end <= state.current_time for s in v.segments)
        for a, b in zip(v.segments, v.segments[1:]):
            assert a.end <= b.start + 1e-7
            assert (a.x2, a.y2) == pytest.approx((b.x1, b.y1))
        for s in v.segments:
            if s.kind == "servicing":
                c = state.nodes_data[s.customer_id]
                assert c.ready-1e-7 <= s.start <= c.due+1e-7
                assert s.end-s.start >= c.service-1e-7
            if s.end > state.current_time and s.kind == "travelling" and s.customer_id == 0:
                assert s.end <= state.problem.depot.due+1e-7
        assigned.extend(s.customer_id for s in future)
    assert len(served) == len(set(served))
    assert len(assigned) == len(set(assigned))
    assert not set(served) & set(assigned)
    assert not (set(served) | set(assigned)) & state.unassigned_pool
    assert set(served) | set(assigned) | state.unassigned_pool == set(state.node_status)


@pytest.mark.parametrize("t,x,status,served,distance", [
    (5, 5, "travelling", [], 5), (15, 10, "waiting", [], 10),
    (20, 10, "servicing", [], 10), (24, 10, "servicing", [], 10),
    (25, 10, "travelling", [1], 10), (30, 15, "travelling", [1], 15),
    (40, 20, "travelling", [1, 2], 20), (50, 10, "travelling", [1, 2], 30),
    (60, 0, "idle", [1, 2], 40),
])
def test_clock_and_interpolation(t, x, status, served, distance):
    state = fleet().advance_time(t)
    v = state.vehicles[0]
    assert (v.x, v.y) == pytest.approx((x, 0))
    assert v.status == status and v.served == served
    assert v.distance_travelled == pytest.approx(distance)
    assert_invariants(state)


def test_service_lock_and_capacity_reservation():
    state = fleet()
    state.advance_time(22)
    problem, starts, _ = state.prep_for_ortools()
    assert [c.id for c in problem.customers] == [2]
    assert starts[0].available_at == 25 and starts[0].remaining_capacity == 6
    locked = next(s for s in state.vehicles[0].segments if s.kind == "servicing")
    state.add_order(22, 1, 0, 7, (22, 70), service=0, time_limit_seconds=.1)
    assert locked in state.vehicles[0].segments
    assert state.vehicles[0].served == []
    assert_invariants(state)
    state.advance_time(25)
    assert 1 in state.vehicles[0].served
    assert_invariants(state)


@pytest.mark.parametrize("t", [5, 15, 22, 25, 50])
def test_breakdown_at_different_stages(t):
    state = fleet().advance_time(t)
    old = deepcopy(state.vehicles[0])
    state.break_vehicle(t, 0, .1)
    assert state.vehicles[0].served == old.served
    assert (state.vehicles[0].x, state.vehicles[0].y) == pytest.approx((old.x, old.y))
    assert state.vehicles[0].distance_travelled == pytest.approx(old.distance_travelled)
    assert_invariants(state)
    state.advance_time(100)
    assert state.vehicles[0].status == "broken"
    assert state.vehicles[0].served == old.served
    assert (state.vehicles[0].x, state.vehicles[0].y) == pytest.approx((old.x, old.y))
    assert_invariants(state)


def test_all_broken_and_new_order():
    state = fleet(1)
    state.break_vehicle(22, 0, .1)
    assert state.unassigned_pool == {1, 2}
    state.add_order(23, 1, 1, 1, (23, 90), time_limit_seconds=.1)
    assert state.unassigned_pool == {1, 2, 3}
    assert state.last_solve["status"] == "NO_AVAILABLE_VEHICLES"
    assert_invariants(state)


def test_unused_vehicle_can_take_stranded_orders():
    state = fleet()
    assert state.vehicles[1].status == "idle"
    state.break_vehicle(5, 0, .1)
    assert not state.unassigned_pool
    assert any(s.kind == "servicing" for s in state.vehicles[1].segments)
    assert_invariants(state)


def test_repeated_replan_keeps_served_and_travelled_distance():
    state = fleet().advance_time(30)
    previous = state.snapshot()
    completed = [s for s in state.vehicles[0].segments if s.end <= 30]
    state.replan(30, .1)
    assert state.vehicles[0].segments[:len(completed)] == completed
    assert state.snapshot()["distance_travelled"] == pytest.approx(previous["distance_travelled"])
    state.add_order(31, 12, 1, 1, (31, 80), service=2, time_limit_seconds=.1)
    assert state.vehicles[0].served == [1]
    assert_invariants(state)


def test_snapshots_are_historical_and_read_only():
    state = fleet()
    earlier = state.snapshot(10)
    state.break_vehicle(22, 0, .1)
    state.add_order(23, 1, 1, 1, (23, 80), time_limit_seconds=.1)
    assert state.snapshot(10) == earlier
    current = state.snapshot()
    future = state.snapshot(100)
    assert state.snapshot() == current
    assert 3 not in state.snapshot(22)["node_status"]
    future["vehicles"]["0"]["served"].append(999)
    assert state.snapshot() == current


def test_solver_failure_keeps_healthy_routes(monkeypatch):
    state = fleet()
    healthy = list(state.vehicles[1].segments)
    def fail(*args, **kwargs):
        raise SolveError("test timeout")
    monkeypatch.setattr(sim, "solve_dynamic", fail)
    state.break_vehicle(22, 0, .1)
    assert state.last_solve["status"] == "SOLVE_FAILED"
    assert state.unassigned_pool == {1, 2}
    assert state.vehicles[1].segments == healthy
    assert_invariants(state)


def test_invalid_event_is_atomic():
    state = fleet().advance_time(20)
    before = state.snapshot()
    with pytest.raises(ValueError):
        state.add_order(21, 1, 1, -1, (20, 80))
    with pytest.raises(ValueError):
        state.break_vehicle(19, 0)
    with pytest.raises(ValueError):
        state.break_vehicle(21, 99)
    with pytest.raises(ValueError):
        state.add_order(21, 1, 1, 1, (20, 80), customer_id=1)
    assert state.snapshot() == before


def test_unexpected_solver_error_rolls_back(monkeypatch):
    state = fleet()
    before = state.snapshot()
    def fail(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(sim, "solve_dynamic", fail)
    with pytest.raises(RuntimeError):
        state.break_vehicle(22, 0, .1)
    assert state.snapshot() == before


def test_expired_order_and_depot_closed():
    state = fleet()
    state.add_order(30, 1, 1, 1, (0, 10), time_limit_seconds=.1)
    assert 3 in state.unassigned_pool
    state.add_order(101, 1, 1, 1, (101, 120), time_limit_seconds=.1)
    assert state.last_solve["status"] == "DEPOT_CLOSED"
    assert {3, 4} <= state.unassigned_pool
    assert_invariants(state)


def test_dynamic_original_vehicle_ids_and_absolute_times():
    state = fleet()
    problem = state.problem
    plan = solve_dynamic(problem, (VehicleStart(17, 5, 0, 12, 10),), 10, .1)
    assert plan.routes[0].vehicle_id == 17
    assert plan.routes[0].stops[0].departure == 12
    assert plan.routes[0].stops[1].arrival == 17
    assert plan.routes[0].stops[1].service_start == 20


def test_repeated_breakdown_rejected():
    state = fleet()
    state.break_vehicle(5, 0, .1)
    before = state.snapshot()
    with pytest.raises(ValueError, match="already broken"):
        state.break_vehicle(6, 0, .1)
    assert state.snapshot() == before


def test_no_orders_still_returns_vehicle_to_depot():
    state = fleet().advance_time(45)
    state.replan(45, .1)
    assert not state.unassigned_pool
    assert state.vehicles[0].segments[-1].customer_id == 0
    state.advance_time(100)
    assert state.vehicles[0].x == 0
    assert_invariants(state)


def test_fractional_event_during_service():
    state = fleet()
    state.replan(22.37, .1)
    assert state.last_solve["status"] != "SOLVE_FAILED"
    assert_invariants(state)
    state.advance_time(25)
    assert state.vehicles[0].served == [1]


def test_initially_dropped_order_is_in_pending_problem():
    problem = Problem("EMPTY", 1, 10, Customer(0, 0, 0, 0, 0, 100, 0),
                      (Customer(77, 1, 0, 1, 0, 100, 0),))
    plan = Plan("EMPTY", (), (77,), 0, 0, 0, 2, 1000, 1000, "TEST")
    state = FleetState(problem, plan)
    assert state.unassigned_pool == {77}
    assert state.prep_for_ortools()[0].customers[0].id == 77
    state.replan(0, .1)
    assert not state.unassigned_pool
    state.advance_time(1)
    assert state.node_status[77] == "served"
    assert_invariants(state)


@pytest.mark.parametrize("seconds", [0, -1, 2.1, float("nan"), True])
def test_dynamic_search_budget(seconds):
    with pytest.raises(ValueError):
        fleet().replan(1, seconds)


def test_r101_end_to_end(tmp_path):
    from demo_s3 import run_demo
    state = run_demo(tmp_path / "demo.json")
    assert len(state.nodes_data) == 27  # Depot, 25 original customers, one new order.
    assert len(state.events) == 2 and len(state.broken_vehicles) == 1
    assert_invariants(state)
