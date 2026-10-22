"""Road-distance mode, tested offline with a fake OSRM table (road = 1.5 x straight line)."""
import math

import pytest
from fastapi.testclient import TestClient

import api
import metric
from metric import RoadMetric, leg_tenths
from parser import find_dataset, load_solomon
from solver import solve_static
from dataclasses import replace


def fake_table(latlngs):
    def straight(a, b):
        return math.hypot((b[0] - a[0]) * 111320, (b[1] - a[1]) * 111320 * math.cos(a[0] * math.pi / 180))
    return [[0.0 if a == b else 1.5 * straight(a, b) for b in latlngs] for a in latlngs]


@pytest.fixture
def problem():
    return load_solomon(find_dataset(api.ROOT / "data" / "solomon", "R101"))


def test_calibration_makes_average_road_leg_equal_average_straight_leg(problem):
    m = RoadMetric.build(problem, "vit", fake_table)
    assert m.ratio == pytest.approx(1.5, rel=0.02)
    a, b = problem.depot, problem.customers[0]
    straight = math.hypot(a.x - b.x, a.y - b.y)
    assert leg_tenths(replace(problem, metric=m), a.x, a.y, b.x, b.y) / 10 == pytest.approx(straight, rel=0.05)


def test_depot_is_placed_exactly_on_the_anchor(problem):
    m = RoadMetric.build(problem, "vit", fake_table, anchor=(12.97012, 79.15876))
    assert m.latlng(problem.depot.x, problem.depot.y) == (12.97012, 79.15876)
    assert m.geo["lat0"] == 12.97012 and m.geo["cx"] == problem.depot.x


def test_solver_plans_on_road_distances(problem):
    m = RoadMetric.build(problem, "vit", fake_table)
    plan = solve_static(replace(problem, metric=m), 1)
    assert plan.dropped_customer_ids == ()
    assert plan.total_distance > 0


def test_api_road_mode_reports_geo_and_depot_anchor(monkeypatch):
    monkeypatch.setattr(metric, "fetch_table", fake_table)
    client = TestClient(api.app)
    state = client.post("/load", json={"dataset": "C101", "reset": True, "routing": "road", "city": "vit",
                                       "depot_lat": 12.97012, "depot_lng": 79.15876}).json()
    assert state["routing"] == "road" and state["geo"]["lat0"] == 12.97012
    assert state["geo"]["metres_per_unit"] == pytest.approx(80 * 1.5, rel=0.02)
    assert state["kpis"]["replan"]["dropped_orders"] == 0


def test_api_road_service_down_is_502(monkeypatch):
    def down(_):
        raise metric.RoadError("offline")
    monkeypatch.setattr(metric, "fetch_table", down)
    assert TestClient(api.app).post("/load", json={"dataset": "C101", "reset": True, "routing": "road"}).status_code == 502


def test_depot_coordinates_must_come_together():
    r = TestClient(api.app).post("/load", json={"dataset": "C101", "reset": True, "depot_lat": 12.9})
    assert r.status_code == 422


def test_vit_defaults_to_the_amazon_point_on_jimmy_carter_road(monkeypatch):
    monkeypatch.setattr(metric, "fetch_table", fake_table)
    state = TestClient(api.app).post("/load", json={"dataset": "C101", "reset": True, "routing": "road", "city": "vit"}).json()
    assert (state["geo"]["lat0"], state["geo"]["lng0"]) == metric.DEFAULT_DEPOT["vit"]
    assert state["geo"]["cx"] == 40 and state["geo"]["cy"] == 50      # C101 depot in Solomon units


def test_markers_use_snapped_on_road_points(monkeypatch):
    def snapping_table(latlngs):
        shifted = [(round(lat + 0.0001, 5), round(lng, 5)) for lat, lng in latlngs]   # "nearest road" is 0.0001 deg north
        return fake_table(latlngs), shifted
    monkeypatch.setattr(metric, "fetch_table", snapping_table)
    client = TestClient(api.app)
    state = client.post("/load", json={"dataset": "C101", "reset": True, "routing": "road", "city": "vit"}).json()
    s = state["strategies"]["replan"]
    placed_lat = state["geo"]["lat0"] + (s["customers"][0]["y"] - state["geo"]["cy"]) * 80 / 111320
    assert s["customers"][0]["lat"] == pytest.approx(placed_lat + 0.0001, abs=2e-5)
    assert "lat" in s["depot"] and all("lat" in v for v in s["vehicles"])
    assert all("al" in leg and "bl" in leg for r in s["routes"] for leg in r["legs"])


def test_straight_line_mode_has_no_snapped_fields():
    client = TestClient(api.app)
    state = client.post("/load", json={"dataset": "C101", "reset": True, "routing": "euclid"}).json()
    s = state["strategies"]["replan"]
    assert "lat" not in s["customers"][0] and "lat" not in s["depot"]
