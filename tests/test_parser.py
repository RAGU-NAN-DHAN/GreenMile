from pathlib import Path

import pytest

from parser import SolomonParseError, find_dataset, load_solomon

DATA = Path(__file__).resolve().parents[1] / "data" / "solomon"
BASE = """DEMO
VEHICLE
NUMBER CAPACITY
2 200
CUSTOMER
CUST NO. XCOORD. YCOORD. DEMAND READY TIME DUE DATE SERVICE TIME
0 35 35 0 0 230 0
7 41.5 -49 10 161 171 10
2 35 17 7 50 60 10
"""


def write_input(tmp_path, content=BASE):
    path = tmp_path / "example.txt"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("name,due", [("C101", 1236), ("R101", 230), ("RC101", 240)])
def test_real_datasets(name, due):
    path = find_dataset(DATA, name)
    p = load_solomon(path)
    assert p.name.upper() == name
    assert (p.vehicle_count, p.vehicle_capacity) == (25, 200)
    assert p.depot.id == 0 and p.depot.due == due
    assert [c.id for c in p.customers] == list(range(1, 26))
    assert len(p.nodes) == 26
    assert len(load_solomon(path, 100).customers) == 100


def test_units_order_and_spacing(tmp_path):
    p = load_solomon(write_input(tmp_path, BASE.replace(" ", "\t").replace("\n", "\r\r\n")), 1)
    assert p.customers[0].id == 7
    assert (p.customers[0].x, p.customers[0].y, p.customers[0].ready) == (41.5, -49, 161)
    assert len(load_solomon(write_input(tmp_path), 0).nodes) == 1


@pytest.mark.parametrize("old,new,match", [
    ("2 200", "2 0", "positive"),
    ("2 200", "2.5 200", "integers"),
    ("0 35 35 0 0 230 0", "3 35 35 0 0 230 0", "missing depot"),
    ("2 35 17", "7 35 17", "duplicate"),
    ("7 41.5 -49 10", "7 41.5 -49 -1", "nonnegative"),
    ("161 171 10", "172 171 10", "ready time"),
    ("161 171 10", "161 171 -1", "nonnegative"),
    ("41.5", "nan", "finite"),
    ("41.5", "oops", "numeric"),
    ("161 171 10", "161 171", "7 customer fields"),
])
def test_bad_rows(tmp_path, old, new, match):
    with pytest.raises(SolomonParseError, match=match):
        load_solomon(write_input(tmp_path, BASE.replace(old, new)))


def test_validates_rows_beyond_limit(tmp_path):
    with pytest.raises(SolomonParseError, match="duplicate"):
        load_solomon(write_input(tmp_path, BASE + "2 0 0 0 0 10 0\n"), 1)


@pytest.mark.parametrize("limit", [-1, 1.5, True])
def test_invalid_limit(tmp_path, limit):
    with pytest.raises(ValueError, match="customer_limit"):
        load_solomon(write_input(tmp_path), limit)


def test_empty_file(tmp_path):
    with pytest.raises(SolomonParseError, match="empty file"):
        load_solomon(write_input(tmp_path, ""))


def test_case_insensitive_lookup_and_ambiguity(tmp_path):
    (tmp_path / "R101.TXT").write_text(BASE)
    assert find_dataset(tmp_path, "r101").name == "R101.TXT"
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "r101.txt").write_text(BASE)
    with pytest.raises(ValueError, match="found 2"):
        find_dataset(tmp_path, "R101")
