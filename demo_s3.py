"""Run R101 -> advance -> breakdown -> add order -> finish, with saved evidence."""
import argparse
import json
from pathlib import Path

from parser import find_dataset, load_solomon
from sim import FleetState
from solver import solve_static


def verify(state):
    """Check delivery accounting, capacity, windows, and path continuity."""
    served, assigned = [], []
    for vehicle_id, vehicle in state.vehicles.items():
        served.extend(vehicle.served)
        future = [s for s in vehicle.segments if s.kind == "servicing" and s.end > state.current_time]
        assert vehicle.used_capacity + sum(state.nodes_data[s.customer_id].demand for s in future) <= vehicle.capacity
        if vehicle_id in state.broken_vehicles:
            assert all(s.end <= state.current_time for s in vehicle.segments)
        for previous, segment in zip(vehicle.segments, vehicle.segments[1:]):
            assert previous.end <= segment.start+1e-7
            assert abs(previous.x2-segment.x1) < 1e-7 and abs(previous.y2-segment.y1) < 1e-7
        for segment in vehicle.segments:
            if segment.kind == "servicing":
                customer = state.nodes_data[segment.customer_id]
                assert customer.ready-1e-7 <= segment.start <= customer.due+1e-7
                assert segment.end-segment.start >= customer.service-1e-7
            if segment.kind == "travelling" and segment.customer_id == 0:
                assert segment.end <= state.problem.depot.due+1e-7
        assigned.extend(s.customer_id for s in future)
    assert len(served) == len(set(served))
    assert len(assigned) == len(set(assigned))
    assert not set(served) & set(assigned)
    assert not (set(served) | set(assigned)) & state.unassigned_pool
    assert set(served) | set(assigned) | state.unassigned_pool == set(state.node_status)


def verify_event(event):
    before, after = event["before"], event["after"]
    assert abs(before["distance_travelled"]-after["distance_travelled"]) < 1e-7
    for key, vehicle in before["vehicles"].items():
        assert vehicle["served"] == after["vehicles"][key]["served"]
        assert abs(vehicle["x"]-after["vehicles"][key]["x"]) < 1e-7
        assert abs(vehicle["y"]-after["vehicles"][key]["y"]) < 1e-7
        completed_services = [s for s in vehicle["segments"] if s["kind"] == "servicing" and s["end"] <= event["time"]]
        assert all(s in after["vehicles"][key]["segments"] for s in completed_services)


def run_demo(output: Path):
    root = Path(__file__).parent
    problem = load_solomon(find_dataset(root / "data" / "solomon", "R101"))
    state = FleetState(problem, solve_static(problem))
    checkpoints = {"initial": state.snapshot()}
    historical = state.snapshot(45)
    state.advance_time(90)
    verify(state)
    checkpoints["midday"] = state.snapshot()
    candidates = [v for v in state.vehicles.values() if v.status in ("travelling", "waiting", "servicing")
                  and any(s.kind == "servicing" and s.end > 90 for s in v.segments)]
    victim = max(candidates, key=lambda v: sum(s.kind == "servicing" and s.end > 90 for s in v.segments))
    event = state.break_vehicle(90, victim.vehicle_id)
    assert event["after"]["last_solve"]["status"] != "SOLVE_FAILED"
    verify_event(event)
    verify(state)
    checkpoints["after_breakdown"] = state.snapshot()
    event = state.add_order(95, 40, 40, 10, (100, 180), service=10)
    assert event["after"]["last_solve"]["status"] != "SOLVE_FAILED"
    verify_event(event)
    verify(state)
    checkpoints["after_new_order"] = state.snapshot()
    assert state.snapshot(45) == historical
    state.advance_time(problem.depot.due)
    verify(state)
    checkpoints["end_of_day"] = state.snapshot()
    assert all(status == "served" or i in state.unassigned_pool for i, status in state.node_status.items())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"dataset": "R101", "initial_customers": 25,
                                 "broken_vehicle_id": victim.vehicle_id, "checkpoints": checkpoints,
                                 "events": state.events, "verification": "passed"}, indent=2), encoding="utf-8")
    print("Stage | Time | Served | Unassigned | Travelled | Total planned | Solve seconds")
    for name, snapshot in checkpoints.items():
        print(f"{name} | {snapshot['t']:g} | {sum(s == 'served' for s in snapshot['node_status'].values())} | "
              f"{len(snapshot['unassigned_pool'])} | {snapshot['distance_travelled']:.1f} | "
              f"{snapshot['total_distance']:.1f} | {snapshot['last_solve']['seconds']:.3f}")
    print(f"Broke vehicle {victim.vehicle_id}. All invariants passed. Saved {output}")
    return state


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=Path(__file__).parent / "results" / "s3-demo.json")
    run_demo(cli.parse_args().output)
