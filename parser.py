"""Read standard Solomon VRPTW text files without changing their units."""
import argparse
import math
from pathlib import Path

from models import Customer, Problem


class SolomonParseError(ValueError):
    """An input file does not contain a valid Solomon problem."""


def load_solomon(path: str | Path, customer_limit: int = 25) -> Problem:
    if isinstance(customer_limit, bool) or not isinstance(customer_limit, int) or customer_limit < 0:
        raise ValueError("customer_limit must be a nonnegative integer")
    path = Path(path)
    rows = [(n, line.strip()) for n, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1) if line.strip()]

    def fail(message: str, line: int | None = None) -> None:
        location = f"{path}:{line}" if line is not None else str(path)
        raise SolomonParseError(f"{location}: {message}")

    if not rows:
        fail("empty file")
    name = rows[0][1]
    section = None
    vehicle = None
    customers = []
    seen = set()
    customer_section = False
    for line_number, text in rows[1:]:
        upper = text.upper()
        if upper == "VEHICLE":
            if section is not None:
                fail("unexpected or duplicate VEHICLE section", line_number)
            section = "vehicle"
            continue
        if upper == "CUSTOMER":
            if vehicle is None or customer_section:
                fail("CUSTOMER must follow vehicle data and appear once", line_number)
            section = "customer"
            customer_section = True
            continue
        if section == "vehicle" and upper.startswith("NUMBER"):
            continue
        if section == "customer" and upper.startswith("CUST"):
            continue
        fields = text.split()
        if section == "vehicle":
            if vehicle is not None or len(fields) != 2:
                fail("expected one vehicle row: NUMBER CAPACITY", line_number)
            try:
                vehicle = tuple(int(value) for value in fields)
            except ValueError:
                fail("vehicle count and capacity must be integers", line_number)
            if any(value <= 0 for value in vehicle):
                fail("vehicle count and capacity must be positive", line_number)
        elif section == "customer":
            if len(fields) != 7:
                fail("expected 7 customer fields: id x y demand ready due service", line_number)
            try:
                customer = Customer(int(fields[0]), float(fields[1]), float(fields[2]),
                                    int(fields[3]), float(fields[4]), float(fields[5]), float(fields[6]))
            except ValueError:
                fail("invalid numeric customer field (id and demand must be integers)", line_number)
            if not all(math.isfinite(v) for v in (customer.x, customer.y, customer.ready, customer.due, customer.service)):
                fail("customer values must be finite", line_number)
            if customer.id < 0 or customer.id in seen:
                fail(f"negative or duplicate customer ID: {customer.id}", line_number)
            if customer.demand < 0 or customer.service < 0:
                fail("demand and service must be nonnegative", line_number)
            if customer.ready > customer.due:
                fail("ready time must not exceed due time", line_number)
            seen.add(customer.id)
            customers.append(customer)
        else:
            fail("expected VEHICLE section", line_number)
    if vehicle is None or not customer_section:
        fail("missing vehicle data or CUSTOMER section")
    depot = next((c for c in customers if c.id == 0), None)
    if depot is None:
        fail("missing depot with ID 0")
    if depot.demand != 0:
        fail("depot demand must be zero")
    # Validate the entire file before selecting the demo subset.
    selected = tuple(c for c in customers if c.id != 0)[:customer_limit]
    return Problem(name, vehicle[0], vehicle[1], depot, selected)


def find_dataset(directory: str | Path, name: str) -> Path:
    """Resolve dataset names case-insensitively; reject ambiguous matches."""
    matches = [p for p in Path(directory).rglob("*")
               if p.is_file() and p.suffix.lower() == ".txt"
               and p.stem.casefold() == Path(name).stem.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one dataset {name!r} in {directory}; found {len(matches)}")
    return matches[0]


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("paths", nargs="*", type=Path)
    cli.add_argument("--limit", type=int, default=25)
    args = cli.parse_args()
    data = Path(__file__).parent / "data" / "solomon"
    paths = args.paths or [find_dataset(data, name) for name in ("C101", "R101", "RC101")]
    print("Dataset | Customers | Vehicles | Capacity | Depot window")
    for path in paths:
        p = load_solomon(path, args.limit)
        print(f"{p.name} | {len(p.customers)} + depot | {p.vehicle_count} | "
              f"{p.vehicle_capacity} | [{p.depot.ready:g}, {p.depot.due:g}]")


if __name__ == "__main__":
    main()
