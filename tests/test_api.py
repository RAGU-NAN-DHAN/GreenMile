from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import api
from solver import SolveError


@pytest.fixture
def client():
    api.current = None
    api.revision = 0
    api.session_id = ""
    with TestClient(api.app) as test:
        yield test
    api.current = None


def load(client, dataset="R101"):
    response = client.post('/load', json={"dataset": dataset})
    assert response.status_code == 200, response.text
    return response.json()


def event_body(state, **payload):
    return {"session_id": state["session_id"], "revision": state["revision"], **payload}


def test_static_files_are_served_independent_of_working_directory(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert client.get('/').status_code == 200
    assert 'Fleet network' in client.get('/').text
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/static/vendor/plotly.min.js').status_code == 200


def test_unloaded_and_invalid_dataset(client):
    assert client.get('/state?t=0').status_code == 409
    assert client.get('/kpis').status_code == 409
    assert client.post('/load', json={"dataset":"../../secret"}).status_code == 422


@pytest.mark.parametrize("dataset,max_time", [("C101",1236),("R101",230),("RC101",240)])
def test_real_datasets_and_read_only_time(client, dataset, max_time):
    initial = load(client, dataset)
    assert initial["max_time"] == max_time
    assert len(initial["strategies"]["replan"]["customers"]) == 25
    assert len(initial["strategies"]["replan"]["vehicles"]) == 25
    assert client.get('/state?t=50').status_code == 200
    assert client.get('/state?t=5').status_code == 200
    assert api.current.states['replan'].current_time == 0
    assert client.get('/state?t=0').json() == initial
    assert client.get(f'/state?t={max_time+1}').status_code == 422


def test_real_breakdown_new_order_and_history(client):
    initial = load(client)
    mid = client.get('/state?t=90').json()
    victim = next(v['id'] for v in mid['strategies']['replan']['vehicles'] if v['pending_count'] and v['status'] != 'idle')
    request = event_body(initial, event_type='breakdown',time=90,vehicle_id=victim)
    response = client.post('/event', json=request)
    assert response.status_code == 200, response.text
    broken = response.json()
    assert broken['revision'] == 1 and broken['event_count'] == 1
    for name in broken['strategies']:
        vehicle = next(v for v in broken['strategies'][name]['vehicles'] if v['id']==victim)
        assert vehicle['status']=='broken' and vehicle['pending_count']==0
        assert broken['kpis'][name]['served_orders']==mid['kpis'][name]['served_orders']
    assert client.post('/event',json=request).status_code==409
    assert client.get('/state?t=0').json()['strategies']==initial['strategies']
    assert client.get('/state?t=0').json()['latest_event'] is None
    assert client.post('/event',json=event_body(broken,event_type='breakdown',time=89,vehicle_id=0)).status_code==409
    response=client.post('/event',json=event_body(broken,event_type='new_order',time=95,x=40.5,y=40.5,demand=10,ready=100,due=180,service=10))
    assert response.status_code==200,response.text
    added=response.json()
    assert all(k['total_orders']==26 for k in added['kpis'].values())
    assert added['events'][-1]['payload']['x']==40.5
    assert client.get('/kpis?t=95').json()['kpis']==added['kpis']
    assert client.post('/load',json={'dataset':'R101'}).status_code==409
    reset=client.post('/load',json={'dataset':'R101','reset':True}).json()
    assert reset['event_count']==0 and reset['session_id']!=initial['session_id']


def test_vehicle_zero_validation_and_failure_status(client, monkeypatch):
    initial=load(client)
    body=event_body(initial,event_type='breakdown',time=0,vehicle_id=0)
    def fail(*args,**kwargs):
        raise SolveError('forced timeout')
    monkeypatch.setattr('sim.solve_dynamic',fail)
    response=client.post('/event',json=body)
    assert response.status_code==200
    state=response.json()
    assert state['latest_event']['strategies']['replan']['status']=='SOLVE_FAILED'
    assert client.post('/event',json=event_body(state,event_type='breakdown',time=1,vehicle_id=0)).status_code==422


@pytest.mark.parametrize('payload',[
    {'event_type':'typo','time':0},
    {'event_type':'breakdown','time':0},
    {'event_type':'breakdown','time':-1,'vehicle_id':0},
    {'event_type':'breakdown','time':0,'vehicle_id':True},
    {'event_type':'new_order','time':0,'x':1,'y':1,'demand':-1,'ready':0,'due':20},
    {'event_type':'new_order','time':0,'x':1,'y':1,'demand':1,'ready':30,'due':20},
])
def test_event_schema(client,payload):
    assert client.post('/event',json={'session_id':'none','revision':0,**payload}).status_code==422


def test_time_nan_and_negative(client):
    assert client.get('/state?t=nan').status_code==422
    assert client.get('/state?t=-1').status_code==422


def test_failed_load_does_not_erase_session(client,monkeypatch):
    initial=load(client)
    def fail(*args,**kwargs):
        raise SolveError('forced initial failure')
    monkeypatch.setattr(api,'solve_static',fail)
    assert client.post('/load',json={'dataset':'C101'}).status_code==503
    assert client.get('/state?t=0').json()==initial
