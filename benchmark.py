"""Compare 25-customer static solves with published Solomon best-known values.

Reference values are the commonly cited 25-customer results (distance, vehicles).
Verify them against the SINTEF Solomon benchmark page before quoting in a report.
"""
import argparse
from pathlib import Path

from parser import find_dataset, load_solomon
from solver import solve_static

# dataset -> (best-known distance, best-known vehicles), 25 customers only
BEST_KNOWN = {"C101": (191.3, 3), "R101": (617.1, 8), "RC101": (461.1, 4)}


def run(seconds: float = 3):
    data = Path(__file__).parent / "data" / "solomon"
    rows = []
    for name, (best, vehicles) in BEST_KNOWN.items():
        plan = solve_static(load_solomon(find_dataset(data, name), customer_limit=25), seconds)
        gap = (plan.total_distance - best) / best * 100
        rows.append((name, plan.total_distance, best, gap, plan.vehicle_count, vehicles,
                     len(plan.dropped_customer_ids), plan.solve_time_seconds))
    return rows


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--seconds", type=float, default=3)
    args = cli.parse_args()
    print("Dataset | Ours | Best-known | Gap % | Vehicles (ours/best) | Dropped | Seconds")
    for n, d, b, g, v, bv, dr, t in run(args.seconds):
        print(f"{n} | {d:.1f} | {b:.1f} | {g:+.2f} | {v}/{bv} | {dr} | {t:.2f}")


if __name__ == "__main__":
    main()
