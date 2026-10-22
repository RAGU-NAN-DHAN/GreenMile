from benchmark import run


def test_static_solutions_close_to_best_known_25_customers():
    for name, ours, best, gap, vehicles, best_vehicles, dropped, _ in run(3):
        assert dropped == 0, name
        assert vehicles <= best_vehicles + 1, name
        assert gap < 2.0, f"{name} is {gap:.2f}% above best-known"
