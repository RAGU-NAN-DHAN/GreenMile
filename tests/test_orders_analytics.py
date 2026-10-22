"""Orders page data (planned arrivals) and the Analytics time series."""
import pytest
from fastapi.testclient import TestClient

import api


@pytest.fixture
def client():
    c = TestClient(api.app)
    assert c.post("/load", json={"dataset": "R101", "reset": True}).status_code == 200
    return c


def test_assigned_orders_have_planned_arrival_inside_their_window(client):
    customers = client.get("/state?t=0").json()["strategies"]["replan"]["customers"]
    assigned = [c for c in customers if c["vehicle_id"] is not None]
    assert assigned
    for c in assigned:
        assert c["arrival"] is not None and c["service_start"] is not None
        assert c["arrival"] <= c["service_start"] + 1e-6
        assert c["ready"] - 1e-6 <= c["service_start"] <= c["due"] + 1e-6


def test_timeseries_shape_and_consistency(client):
    state = client.get("/state?t=230").json()
    ts = client.get("/timeseries?step=10").json()
    n = len(ts["times"])
    assert ts["times"][0] == 0 and ts["times"][-1] == state["max_time"]
    for name, s in ts["series"].items():
        assert all(len(v) == n for v in s.values())
        assert all(a <= b for a, b in zip(s["served"], s["served"][1:])), "delivered orders never decrease"
        assert s["served"][-1] == state["kpis"][name]["served_orders"]


def test_timeseries_shows_the_breakdown(client):
    state = client.get("/state?t=90").json()
    vid = max(state["strategies"]["replan"]["vehicles"], key=lambda v: v["pending_count"])["id"]
    client.post("/event", json={"event_type": "breakdown", "vehicle_id": vid, "time": 90,
                                "session_id": state["session_id"], "revision": state["revision"]})
    ts = client.get("/timeseries?step=10").json()
    assert ts["events"] and ts["events"][0]["time"] == 90
    for s in ts["series"].values():
        assert s["broken"][-1] == 1 and s["broken"][0] == 0


def test_timeseries_validates_step(client):
    assert client.get("/timeseries?step=0").status_code == 422


def test_geocode_returns_places_and_caches(client, monkeypatch):
    calls = []

    def fake(query, lat, lng):
        calls.append(query)
        return [{"name": "Vellore Institute of Technology", "label": "Vellore Institute of Technology, Katpadi", "lat": 12.9697, "lon": 79.1607}]

    api.place_cache.clear()
    monkeypatch.setattr(api, "fetch_places", fake)
    first = client.get("/geocode?q=vellore institute of technology").json()
    again = client.get("/geocode?q=Vellore Institute of Technology").json()
    assert first[0]["name"].startswith("Vellore") and again == first and len(calls) == 1


def test_geocode_errors(client, monkeypatch):
    import httpx

    def down(*args):
        raise httpx.ConnectError("offline")

    api.place_cache.clear()
    monkeypatch.setattr(api, "fetch_places", down)
    assert client.get("/geocode?q=anything").status_code == 502
    assert client.get("/geocode?q=a").status_code == 422
