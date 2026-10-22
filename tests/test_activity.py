"""Each truck reports what it is doing, for the live fleet panel."""
import pytest
from fastapi.testclient import TestClient

import api

KINDS = {"travelling", "waiting", "servicing", "idle", "broken"}


@pytest.fixture
def client():
    c = TestClient(api.app)
    assert c.post("/load", json={"dataset": "R101", "reset": True}).status_code == 200
    return c


def test_every_truck_has_an_activity(client):
    for t in (0, 45, 90, 200):
        for v in client.get(f"/state?t={t}").json()["strategies"]["replan"]["vehicles"]:
            a = v["activity"]
            assert a["kind"] in KINDS
            assert (a["until"] is None) == (a["customer_id"] is None)


def test_trucks_are_moving_and_working_during_the_day(client):
    kinds = {v["activity"]["kind"] for t in range(0, 230, 10)
             for v in client.get(f"/state?t={t}").json()["strategies"]["replan"]["vehicles"]}
    assert {"travelling", "servicing"} <= kinds and "idle" in kinds


def test_broken_truck_reports_broken(client):
    state = client.get("/state?t=90").json()
    vid = max(state["strategies"]["replan"]["vehicles"], key=lambda v: v["pending_count"])["id"]
    after = client.post("/event", json={"event_type": "breakdown", "vehicle_id": vid, "time": 90,
                                        "session_id": state["session_id"], "revision": state["revision"]}).json()
    truck = next(v for v in after["strategies"]["replan"]["vehicles"] if v["id"] == vid)
    assert truck["activity"]["kind"] == "broken"
