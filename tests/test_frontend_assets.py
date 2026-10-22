"""Static frontend checks: pages and vendored libraries are served and wired together."""
import re

import pytest
from fastapi.testclient import TestClient

import api


@pytest.fixture
def client():
    return TestClient(api.app)


def test_index_references_served_assets(client):
    html = client.get('/').text
    for path in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
        assert client.get(path).status_code == 200, path


def test_dashboard_ids_used_by_script_exist(client):
    html = client.get('/').text
    script = client.get('/static/app.js').text
    ids = set(re.findall(r"\$\('([a-z0-9-]+)'\)", script))
    generated = {i for i in ids if i.startswith('vc-')}  # buttons built inside the vehicle card template
    missing = {i for i in ids - generated if f'id="{i}"' not in html}
    assert not missing, f"app.js looks up ids missing from index.html: {sorted(missing)}"


def test_street_map_is_illustrative_and_attributed(client):
    html, script = client.get('/').text, client.get('/static/app.js').text
    assert 'Illustrative placement' in html
    assert 'OpenStreetMap contributors' in script
