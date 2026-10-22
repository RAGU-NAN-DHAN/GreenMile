"""Apply identical events to independently evolving strategy fleets."""
from copy import deepcopy
import math

from baselines import STRATEGIES
from kpis import calculate_kpis, compare_strategies
from sim import FleetState


class Comparison:
    def __init__(self, problem, initial_plan):
        self.states = {name: FleetState(problem, initial_plan) for name in STRATEGIES}
        self.events = []

    def _event(self, method, t, **kwargs):
        candidates, events = {}, {}
        for strategy, state in self.states.items():
            candidate = state.fork()
            events[strategy] = getattr(candidate, method)(t, **kwargs, strategy=strategy)
            candidates[strategy] = candidate
        comparison = compare_strategies(events)
        # Commit all or none, including validation and comparison failures.
        record = {"type": method, "time": t, "payload": deepcopy(next(iter(events.values()))["payload"]),
                  "strategies": comparison, "snapshots": events}
        self.states = candidates
        self.events.append(record)
        return deepcopy(record)

    def break_vehicle(self, t, vehicle_id, time_limit_seconds=2):
        return self._event("break_vehicle", t, vehicle_id=vehicle_id, time_limit_seconds=time_limit_seconds)

    def add_order(self, t, x, y, demand, window, service=10, customer_id=None, time_limit_seconds=2):
        if customer_id is None:
            customer_id = max(max(state.nodes_data) for state in self.states.values())+1
        return self._event("add_order", t, x=x, y=y, demand=demand, window=window,
                           service=service, customer_id=customer_id, time_limit_seconds=time_limit_seconds)

    def advance_time(self, t):
        if not math.isfinite(t) or any(t < s.current_time for s in self.states.values()):
            raise ValueError("Time must be finite and cannot move backward")
        for state in self.states.values():
            state.advance_time(t)

    def snapshot(self, t=None):
        return {name: state.snapshot(t) for name, state in self.states.items()}

    def kpis(self, t=None):
        return {name: calculate_kpis(state.snapshot(t)) for name, state in self.states.items()}
