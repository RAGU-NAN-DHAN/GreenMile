from copy import deepcopy

import pytest

import sim
from baselines import run_cheapest_insertion, run_do_nothing
from comparison import Comparison
from kpis import calculate_kpis
from solver import SolveError
from test_sim import fleet, assert_invariants


def test_do_nothing_does_not_call_solver(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Baseline must not solve")
    monkeypatch.setattr(sim, "solve_dynamic", forbidden)
    state = fleet()
    old_routes = {i: list(v.segments) for i, v in state.vehicles.items()}
    state.add_order(5, 1, 0, 1, (5, 90), strategy="do_nothing")
    assert state.unassigned_pool == {3}
    assert {i: v.segments for i, v in state.vehicles.items()} == old_routes
    state.break_vehicle(10, 0, strategy="do_nothing")
    assert state.unassigned_pool == {1, 2, 3}
    assert state.vehicles[1].segments == old_routes[1]
    assert_invariants(state)


def test_impossible_order_is_not_inserted():
    state = fleet()
    old = {i: list(v.segments) for i, v in state.vehicles.items()}
    state.add_order(0, 200, 0, 1, (0, 1), strategy="cheapest_insertion")
    assert state.unassigned_pool == {3}
    assert {i: v.segments for i, v in state.vehicles.items()} == old
    assert calculate_kpis(state)["dropped_orders"] == 1
    assert_invariants(state)


def test_depot_order_preserves_endpoints_and_zero_distance():
    state = fleet()
    state.add_order(0, 0, 0, 1, (0, 90), service=1, strategy="cheapest_insertion")
    assert not state.unassigned_pool
    vehicle = next(v for v in state.vehicles.values() if any(s.kind == "servicing" and s.customer_id == 3 for s in v.segments))
    assert (vehicle.segments[0].x1, vehicle.segments[0].y1) == (0, 0)
    assert vehicle.segments[-1].customer_id == 0
    assert_invariants(state)


def test_insertion_keeps_original_order_and_served_history():
    state = fleet(1).advance_time(30)
    history = [s for s in state.vehicles[0].segments if s.end <= 30]
    position = state.vehicles[0].x
    distance = state.vehicles[0].distance_travelled
    state.add_order(30, 16, 0, 1, (30, 80), service=1, strategy="cheapest_insertion")
    assert not state.unassigned_pool
    assert state.vehicles[0].served == [1]
    assert state.vehicles[0].x == pytest.approx(position)
    assert state.vehicles[0].distance_travelled == pytest.approx(distance)
    assert state.vehicles[0].segments[:len(history)] == history
    assert [s.customer_id for s in state.vehicles[0].segments if s.kind == "servicing" and s.customer_id != 3] == [1, 2]
    assert_invariants(state)


def test_insertion_reserves_service_and_remaining_capacity():
    state = fleet(1)
    state.add_order(22, 10, 0, 3, (22, 90), strategy="cheapest_insertion")
    assert state.unassigned_pool == {3}  # 4 locked + 4 pending + 3 exceeds 10.
    assert state.node_status[1] == "servicing"
    assert_invariants(state)


def test_insertion_accounts_for_downstream_service_and_depot_return():
    state = fleet(1)
    state.add_order(5, 5, 0, 1, (5, 90), service=100, strategy="cheapest_insertion")
    assert state.unassigned_pool == {3}
    assert_invariants(state)


def test_breakdown_insertion_uses_spare_vehicle():
    state = fleet()
    state.break_vehicle(5, 0, strategy="cheapest_insertion")
    assert not state.unassigned_pool
    assert any(s.kind == "servicing" for s in state.vehicles[1].segments)
    assert_invariants(state)


def test_insertion_leaves_order_when_all_broken():
    state = fleet(1)
    state.break_vehicle(22, 0, strategy="cheapest_insertion")
    state.add_order(23, 1, 0, 1, (23, 90), strategy="cheapest_insertion")
    assert state.unassigned_pool == {1, 2, 3}
    assert_invariants(state)


def test_wrappers_do_not_mutate_input_or_lose_history():
    state = fleet()
    state.add_order(5, 1, 0, 1, (5, 90), strategy="do_nothing")
    before = state.snapshot()
    old = state.snapshot(2)
    copied, _ = run_do_nothing(state)
    inserted, elapsed = run_cheapest_insertion(state)
    assert state.snapshot() == before
    assert copied.unassigned_pool == {3}
    assert not inserted.unassigned_pool and elapsed >= 0
    assert inserted.snapshot(2) == old
    assert_invariants(inserted)


def make_comparison():
    # Derive a deterministic initial Plan from the same fixture used in S3 tests.
    from models import Plan, RouteStop, VehicleRoute
    state = fleet()
    route = VehicleRoute(0, (RouteStop(0, 0, 0, 0, 0), RouteStop(1, 10, 20, 25, 4),
                            RouteStop(2, 35, 35, 40, 8), RouteStop(0, 60, 60, 60, 8)), 40, 8)
    plan = Plan("TEST", (route,), (), 40, 1, 0, 2, 400, 1000, "TEST")
    return Comparison(state.problem, plan)


def test_same_events_independent_states_and_per_strategy_before():
    comparison = make_comparison()
    first = comparison.break_vehicle(5, 0, .1)
    assert first["strategies"]["do_nothing"]["after"]["dropped_orders"] == 2
    assert first["strategies"]["cheapest_insertion"]["after"]["dropped_orders"] == 0
    expected = comparison.snapshot(30)
    second = comparison.add_order(30, 1, 0, 1, (30, 90), service=1, time_limit_seconds=.1)
    for name, event in second["snapshots"].items():
        assert event["payload"]["id"] == 3
        assert event["before"] == expected[name]
        assert_invariants(comparison.states[name])
    assert second["strategies"]["do_nothing"]["before"] != second["strategies"]["cheapest_insertion"]["before"]
    original = comparison.states["replan"].snapshot()
    comparison.states["do_nothing"].vehicles[1].served.append(999)
    assert comparison.states["replan"].snapshot() == original


def test_comparison_invalid_event_is_atomic():
    comparison = make_comparison()
    before = comparison.snapshot()
    with pytest.raises(ValueError):
        comparison.add_order(5, 1, 1, -1, (5, 90))
    assert comparison.snapshot() == before and not comparison.events


def test_failure_is_reported_for_replan_only(monkeypatch):
    comparison = make_comparison()
    def fail(*args, **kwargs):
        raise SolveError("forced timeout")
    monkeypatch.setattr(sim, "solve_dynamic", fail)
    event = comparison.break_vehicle(5, 0, .1)
    assert event["strategies"]["replan"]["status"] == "SOLVE_FAILED"
    assert event["strategies"]["replan"]["after"]["dropped_orders"] == 2
    assert event["strategies"]["cheapest_insertion"]["after"]["dropped_orders"] == 0
    for state in comparison.states.values():
        assert_invariants(state)


def test_unexpected_failure_rolls_back_entire_comparison(monkeypatch):
    comparison = make_comparison()
    before = deepcopy(comparison.snapshot())
    def fail(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(sim, "solve_dynamic", fail)
    with pytest.raises(RuntimeError):
        comparison.break_vehicle(5, 0, .1)
    assert comparison.snapshot() == before and not comparison.events


def test_r101_three_strategy_demo(tmp_path):
    import json
    from demo_s4 import run_demo
    output = tmp_path / "comparison.json"
    result = run_demo(output)
    data = json.loads(output.read_text())
    assert len(result.events) == 2
    assert data["verification"] == "passed" and output.with_suffix(".md").is_file()
    assert set(data["final_kpis"]) == {"do_nothing", "cheapest_insertion", "replan"}
    for kpis in data["final_kpis"].values():
        assert kpis["served_orders"]+kpis["dropped_orders"] == 26
        assert kpis["remaining_distance"] == pytest.approx(0)
    assert all(e["strategies"]["replan"]["status"] != "SOLVE_FAILED" for e in data["events"])
