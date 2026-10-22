"""Replay identical R101 events under three strategies and save comparisons."""
import argparse
import json
from pathlib import Path

from comparison import Comparison
from demo_s3 import verify, verify_event
from parser import find_dataset, load_solomon
from solver import solve_static


def print_event(event):
    print(f"\n{event['type']} at t={event['time']:g}")
    print("Strategy | Distance before -> after (delta) | Unassigned before -> after | Late | Recovery seconds")
    for name, values in event["strategies"].items():
        before, after, delta = values["before"], values["after"], values["delta"]
        print(f"{name} | {before['distance']:.1f} -> {after['distance']:.1f} ({delta['distance']:+.1f}) | "
              f"{before['dropped_orders']} -> {after['dropped_orders']} | {after['late_deliveries']} | "
              f"{after['solve_time_sec']:.6f} [{values['status']}]")


def run_demo(output: Path):
    root = Path(__file__).parent
    problem = load_solomon(find_dataset(root / "data" / "solomon", "R101"))
    plan = solve_static(problem)
    comparison = Comparison(problem, plan)
    initial = comparison.snapshot()
    historical = comparison.snapshot(45)
    comparison.advance_time(90)
    candidates = [v for v in comparison.states["replan"].vehicles.values()
                  if v.status in ("travelling", "waiting", "servicing")
                  and any(s.kind == "servicing" and s.end > 90 for s in v.segments)]
    victim = max(candidates, key=lambda v: sum(s.kind == "servicing" and s.end > 90 for s in v.segments))
    first = comparison.break_vehicle(90, victim.vehicle_id)
    for state in comparison.states.values():
        verify(state)
    for event in first["snapshots"].values():
        verify_event(event)
    second = comparison.add_order(95, 40, 40, 10, (100, 180), service=10)
    for state in comparison.states.values():
        verify(state)
    for event in second["snapshots"].values():
        verify_event(event)
    assert comparison.snapshot(45) == historical
    assert first["payload"]["vehicle_id"] == victim.vehicle_id
    assert all(event["payload"] == second["payload"] for event in second["snapshots"].values())
    comparison.advance_time(problem.depot.due)
    for state in comparison.states.values():
        verify(state)
        assert all(value == "served" or i in state.unassigned_pool for i, value in state.node_status.items())
    final = comparison.kpis()
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {"dataset": "R101", "initial_customers": 25, "total_orders_after_events": 26,
            "broken_vehicle_id": victim.vehicle_id, "initial": initial, "events": comparison.events,
            "final_kpis": final, "final_snapshots": comparison.snapshot(), "verification": "passed",
            "definitions": {"distance": "Executed plus remaining planned distance, in source units",
                            "late_deliveries": "Completed or planned services whose physical arrival exceeds due",
                            "dropped_orders": "Currently unassigned, not irrevocably cancelled",
                            "solve_time_sec": "Latest event recovery time; excludes cloning and event advancement"}}
    output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    report = ["# S4 strategy comparison — R101", "",
              f"Started with 25 customers. Broke vehicle {victim.vehicle_id} at t=90 and added order 26 at t=95.", "",
              "Each strategy received the same events and began with the same initial plan.", ""]
    for event in comparison.events:
        print_event(event)
        report.extend([f"## {event['type']} at t={event['time']:g}", "",
                       "| Strategy | Distance before | Distance after | Delta | Unassigned before | Unassigned after | Late | Recovery seconds |",
                       "|---|---:|---:|---:|---:|---:|---:|---:|"])
        for name, values in event["strategies"].items():
            before, after, delta = values["before"], values["after"], values["delta"]
            report.append(f"| {name} | {before['distance']:.1f} | {after['distance']:.1f} | {delta['distance']:+.1f} | "
                          f"{before['dropped_orders']} | {after['dropped_orders']} | {after['late_deliveries']} | {after['solve_time_sec']:.6f} |")
        report.append("")
    print("\nEnd of day (t=230): Strategy | Delivered | Unassigned | Distance | Late")
    report.extend(["## End of day (t=230)", "", "| Strategy | Delivered | Unassigned | Distance | Late |",
                   "|---|---:|---:|---:|---:|"])
    for name, values in final.items():
        row = f"{name} | {values['served_orders']} | {values['dropped_orders']} | {values['distance']:.1f} | {values['late_deliveries']}"
        print(row)
        report.append(f"| {row} |")
    report.extend(["", "All demo checks passed: history locking, vehicle positions, travelled distance, capacity, windows, and order accounting.", "",
                   "Lower distance alone does not mean better performance: a strategy may travel less because it leaves more deliveries unassigned.", "",
                   "Recovery runtime is measured consistently for each strategy and excludes state cloning and event advancement. Before-event runtime is zero for that event. Search is capped at two seconds; preparation adds small overhead.", "",
                   "Late counts include actual and projected deliveries; at end of day they are actual. Orders left unassigned are counted separately. Re-planning is a time-limited heuristic, not guaranteed to outperform insertion on every run.", "",
                   "The capacity/cargo-transfer assumptions are the same as S3. This is a terminal comparison, not the future map/dashboard."])
    output.with_suffix(".md").write_text("\n".join(report)+"\n", encoding="utf-8")
    print(f"\nAll checks passed. Saved {output} and {output.with_suffix('.md')}")
    return comparison


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=Path(__file__).parent / "results" / "s4-comparison.json")
    run_demo(cli.parse_args().output)
