"""Road routes for the street map: cached /road endpoint and per-leg progress in /state."""
import httpx
import pytest
from fastapi.testclient import TestClient

import api


@pytest.fixture
def client(monkeypatch):
    api.road_cache.clear()
    return TestClient(api.app)


def test_road_returns_geometry_and_caches(client, monkeypatch):
    calls = []

    def fake(lat1, lng1, lat2, lng2):
        calls.append((lat1, lng1, lat2, lng2))
        return {"distance_m": 1234.5, "coords": [[lat1, lng1], [lat2, lng2]]}

    monkeypatch.setattr(api, "fetch_road", fake)
    url = "/road?lat1=12.9&lng1=79.1&lat2=12.91&lng2=79.12"
    first, second = client.get(url), client.get(url)
    assert first.status_code == 200 and first.json()["distance_m"] == 1234.5
    assert second.json() == first.json()
    assert len(calls) == 1, "second request should come from the cache"


def test_road_service_failure_is_502(client, monkeypatch):
    def boom(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(api, "fetch_road", boom)
    assert client.get("/road?lat1=12.9&lng1=79.1&lat2=12.91&lng2=79.12").status_code == 502


def test_road_rejects_out_of_range_coordinates(client):
    assert client.get("/road?lat1=999&lng1=79.1&lat2=12.91&lng2=79.12").status_code == 422


def test_state_routes_carry_leg_progress(client):
    assert client.post("/load", json={"dataset": "R101", "reset": True}).status_code == 200
    state = client.get("/state?t=90").json()
    for strategy in state["strategies"].values():
        legs = [leg for route in strategy["routes"] for leg in route["legs"]]
        assert legs
        assert all(0.0 <= leg["p"] <= 1.0 and len(leg["a"]) == 2 and len(leg["b"]) == 2 for leg in legs)
        assert any(leg["p"] == 1.0 for leg in legs) and any(leg["p"] == 0.0 for leg in legs)
