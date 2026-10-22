from copy import deepcopy

import pytest

from kpis import calculate_kpis, compare_strategies
from test_sim import fleet


def test_distance_includes_travelled_partial_leg_after_breakdown():
    state = fleet(1)
    state.break_vehicle(5, 0, strategy="do_nothing")
    kpis = calculate_kpis(state)
    assert kpis["distance"] == kpis["distance_travelled"] == 5
    assert kpis["remaining_distance"] == 0
    assert kpis["dropped_orders"] == 2


def test_waiting_is_not_distance_or_lateness():
    state = fleet().advance_time(22)
    values = calculate_kpis(state)
    assert values["distance_travelled"] == 10
    assert values["distance"] == 40
    assert values["served_orders"] == 0
    assert values["late_deliveries"] == 0


def test_lateness_counts_physical_arrival_not_service_start():
    snapshot = fleet().snapshot(22)
    snapshot["customers"]["1"]["due"] = 15  # Arrival=10, service start=20.
    assert calculate_kpis(snapshot)["late_deliveries"] == 0
    snapshot["customers"]["1"]["due"] = 9
    kpis = calculate_kpis(snapshot)
    assert kpis["late_deliveries"] == 1 and kpis["actual_late_deliveries"] == 0
    snapshot["t"] = 26
    assert calculate_kpis(snapshot)["actual_late_deliveries"] == 1


def test_missing_schedule_is_rejected_instead_of_counted_successful():
    snapshot = fleet().snapshot()
    snapshot["vehicles"]["0"]["segments"] = [s for s in snapshot["vehicles"]["0"]["segments"]
                                                if not (s["kind"] == "servicing" and s["customer_id"] == 1)]
    with pytest.raises(ValueError, match="Every order"):
        calculate_kpis(snapshot)


def test_missing_arrival_is_rejected():
    snapshot = fleet().snapshot()
    snapshot["vehicles"]["0"]["segments"] = [s for s in snapshot["vehicles"]["0"]["segments"]
                                                if not (s["kind"] == "travelling" and s["customer_id"] == 1)]
    with pytest.raises(ValueError, match="Missing arrival"):
        calculate_kpis(snapshot)


def test_all_kpis_have_deltas_and_event_cost_starts_at_zero():
    state = fleet()
    event = state.add_order(5, 200, 0, 1, (5, 6), strategy="do_nothing")
    compared = compare_strategies({"do_nothing": event})["do_nothing"]
    assert compared["before"]["solve_time_sec"] == 0
    for name, value in compared["delta"].items():
        assert value == pytest.approx(compared["after"][name]-compared["before"][name])
    assert compared["delta"]["dropped_orders"] == 1
    assert compared["delta"]["total_orders"] == 1
    assert compared["delta"]["distance"] == 0


def test_serialized_snapshot_support_and_no_mutation():
    import json
    state = fleet().advance_time(30)
    snapshot = state.snapshot()
    original = deepcopy(snapshot)
    assert calculate_kpis(json.loads(json.dumps(snapshot))) == calculate_kpis(state)
    assert snapshot == original
