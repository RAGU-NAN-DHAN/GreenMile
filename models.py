"""Shared input models. Distances and times remain in source units."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Customer:
    id: int
    x: float
    y: float
    demand: int
    ready: float
    due: float
    service: float


@dataclass(frozen=True)
class Problem:
    name: str
    vehicle_count: int
    vehicle_capacity: int
    depot: Customer
    customers: tuple[Customer, ...]

    @property
    def nodes(self) -> tuple[Customer, ...]:
        return (self.depot, *self.customers)


@dataclass(frozen=True)
class RouteStop:
    customer_id: int
    arrival: float
    service_start: float
    departure: float
    cumulative_demand: int


@dataclass(frozen=True)
class VehicleRoute:
    vehicle_id: int
    stops: tuple[RouteStop, ...]  # Includes initial and final depot.
    distance: float
    load: int


@dataclass(frozen=True)
class Plan:
    problem_name: str
    routes: tuple[VehicleRoute, ...]
    dropped_customer_ids: tuple[int, ...]
    total_distance: float
    vehicle_count: int
    solve_time_seconds: float  # Includes model construction and extraction.
    search_limit_seconds: float
    objective_cost: int
    drop_penalty: int
    status: str
    scale: int = 10


@dataclass(frozen=True)
class VehicleStart:
    vehicle_id: int
    x: float
    y: float
    available_at: float
    remaining_capacity: int


@dataclass(frozen=True)
class DynamicPlan:
    routes: tuple[VehicleRoute, ...]
    dropped_customer_ids: tuple[int, ...]
    solve_time_seconds: float
    status: str
    search_limit_seconds: float
