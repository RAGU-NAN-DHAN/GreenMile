"""Comparable projected/actual KPIs from executed and planned S3 segments."""
from copy import deepcopy
import math


def calculate_kpis(state, solve_time=None):
    snapshot = state.snapshot() if hasattr(state, "snapshot") else state
    customers = {int(i): c for i, c in snapshot["customers"].items()}
    statuses = {int(i): value for i, value in snapshot["node_status"].items()}
    late = actual_late = 0
    seen = set()
    total_distance = travelled = 0.
    now = snapshot["t"]
    for vehicle in snapshot["vehicles"].values():
        travelled += vehicle["distance_travelled"]
        segments = vehicle["segments"]
        total_distance += sum(s["distance"] for s in segments)
        for index, segment in enumerate(segments):
            if segment["kind"] != "servicing":
                continue
            customer_id = segment["customer_id"]
            if customer_id in seen:
                raise ValueError(f"Duplicate service for order {customer_id}")
            seen.add(customer_id)
            # Ignore abandoned approach legs: count arrivals only for actual/planned
            # services. Depot visits and interrupted services are not deliveries.
            arrival = next((s["end"] for s in reversed(segments[:index])
                            if s["kind"] == "travelling" and s["customer_id"] == customer_id
                            and s["end"] <= segment["start"]+1e-8), None)
            if arrival is None:
                raise ValueError(f"Missing arrival for order {customer_id}")
            if arrival > customers[customer_id]["due"]+1e-8:
                late += 1
                actual_late += segment["end"] <= now
    served = sum(value == "served" for value in statuses.values())
    unassigned = set(snapshot["unassigned_pool"])
    served_ids = {i for i, value in statuses.items() if value == "served"}
    if (seen & unassigned or seen | unassigned != set(statuses) or not served_ids <= seen):
        raise ValueError("Every order must have a service schedule or be explicitly unassigned")
    elapsed = snapshot["last_solve"].get("seconds", 0.) if solve_time is None else solve_time
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("Execution time must be finite and nonnegative")
    return {"distance": total_distance, "distance_travelled": travelled,
            "remaining_distance": max(0., total_distance-travelled),
            "late_deliveries": late, "actual_late_deliveries": actual_late,
            "dropped_orders": len(unassigned), "served_orders": served,
            "assigned_pending_orders": len(statuses)-served-len(unassigned),
            "total_orders": len(statuses), "solve_time_sec": elapsed}


def compare_strategies(strategy_events):
    """Each strategy has its OWN before/after state at this event timestamp."""
    result = {}
    for name, event in strategy_events.items():
        # Before the event, no recovery time has yet been spent on this event.
        before = calculate_kpis(event["before"], solve_time=0.)
        after = calculate_kpis(event["after"])
        result[name] = {"before": before, "after": after,
                        "delta": {key: after[key]-before[key] for key in before},
                        "status": event["after"]["last_solve"]["status"]}
    return deepcopy(result)
