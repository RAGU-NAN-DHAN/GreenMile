"""Distance models. Straight-line (Solomon) is the default; road mode uses OSRM driving distances.

Road mode places the Solomon x/y grid on a real city (80 m per unit, centred on the
customers), asks OSRM for driving distances, and calibrates one "time unit" so the
average road leg equals the average straight leg. That keeps Solomon time windows
roughly as feasible as in the benchmark while every distance, travel time and KPI
becomes a road distance. Real kilometres = units x metres_per_unit / 1000.
"""
import math
from threading import RLock

import httpx

SCALE = 10                # solver works in tenths of a unit
PLACE_M = 80.0            # metres per Solomon unit when placing the grid on a map
MAX_TABLE = 100           # OSRM demo server limit on coordinates per table request
CITIES = {"vit": (12.9697, 79.1607), "vellore": (12.9165, 79.1325), "chennai": (13.0827, 80.2707), "bengaluru": (12.9716, 77.5946)}
OSRM_TABLE = "https://router.project-osrm.org/table/v1/driving/{coords}?annotations=distance"


class RoadError(RuntimeError):
    """The routing service could not provide a distance table."""


def euclid_tenths(x1, y1, x2, y2):
    return int(math.floor(math.hypot(x2 - x1, y2 - y1) * SCALE + 0.5))


def leg_tenths(problem, x1, y1, x2, y2, fetch=True):
    """Distance (= travel time) between two plane points, in tenths of a unit."""
    metric = problem.metric
    return euclid_tenths(x1, y1, x2, y2) if metric is None else metric.leg_tenths(x1, y1, x2, y2, fetch)


def prefetch(problem, points):
    """Load every pairwise distance among points in one request (no-op for straight-line)."""
    if problem.metric is not None:
        problem.metric.prefetch(points)


def fetch_table(latlngs):
    """Driving distance matrix in metres (None where no route exists) from the OSRM demo server."""
    coords = ";".join(f"{lng},{lat}" for lat, lng in latlngs)
    try:
        response = httpx.get(OSRM_TABLE.format(coords=coords), timeout=20,
                             headers={"User-Agent": "GreenMile-student-demo"})
        response.raise_for_status()
        return response.json()["distances"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise RoadError(str(exc)) from exc


class RoadMetric:
    def __init__(self, city, centre, table=None, origin=None):
        if city not in CITIES:
            raise ValueError(f"Unknown city: {city}")
        self.city, self.centre = city, centre
        self.table = table or fetch_table   # resolved at call time so tests can replace fetch_table
        self.origin = origin or CITIES[city]   # lat/lng that the plane point `centre` maps to
        self.cache = {}
        self.lock = RLock()
        self.ratio = 1.4                       # road / straight, replaced by calibration
        self.degraded = False                  # True once any distance had to be estimated

    @property
    def metres_per_unit(self):
        return PLACE_M * self.ratio

    @property
    def geo(self):
        lat0, lng0 = self.origin
        return {"city": self.city, "lat0": lat0, "lng0": lng0, "cx": self.centre[0], "cy": self.centre[1],
                "place_m": PLACE_M, "metres_per_unit": self.metres_per_unit}

    def __deepcopy__(self, memo):               # simulator forks share one cache
        return self

    def latlng(self, x, y):
        lat0, lng0 = self.origin
        return (round(lat0 + (y - self.centre[1]) * PLACE_M / 111320, 5),
                round(lng0 + (x - self.centre[0]) * PLACE_M / (111320 * math.cos(lat0 * math.pi / 180)), 5))

    def _estimate_metres(self, x1, y1, x2, y2):
        return math.hypot(x2 - x1, y2 - y1) * PLACE_M * self.ratio

    def prefetch(self, points):
        unique = list(dict.fromkeys(self.latlng(x, y) for x, y in points))
        with self.lock:
            if all((a, b) in self.cache for a in unique for b in unique if a != b) or len(unique) > MAX_TABLE:
                return
        try:
            matrix = self.table(unique)
        except RoadError:
            self.degraded = True
            return
        with self.lock:
            for i, a in enumerate(unique):
                for j, b in enumerate(unique):
                    if i != j and matrix[i][j] is not None:
                        self.cache[(a, b)] = float(matrix[i][j])

    def leg_tenths(self, x1, y1, x2, y2, fetch=True):
        a, b = self.latlng(x1, y1), self.latlng(x2, y2)
        if a == b:
            return 0
        with self.lock:
            metres = self.cache.get((a, b))
        if metres is None and fetch:
            self.prefetch([(x1, y1), (x2, y2)])
            with self.lock:
                metres = self.cache.get((a, b))
        if metres is None:
            self.degraded = self.degraded or fetch
            metres = self._estimate_metres(x1, y1, x2, y2)
        return int(math.floor(metres / self.metres_per_unit * SCALE + 0.5))

    @classmethod
    def for_problem(cls, problem, city, table=None, anchor=None):
        """Metric with the grid placed on the city (or the depot on `anchor`), no distances fetched yet."""
        if anchor is not None:
            return cls(city, (problem.depot.x, problem.depot.y), table, origin=tuple(anchor))
        customers = problem.customers or (problem.depot,)
        centre = ((min(c.x for c in customers) + max(c.x for c in customers)) / 2,
                  (min(c.y for c in customers) + max(c.y for c in customers)) / 2)
        return cls(city, centre, table)

    @classmethod
    def build(cls, problem, city, table=None, anchor=None):
        """Fetch the initial node matrix and calibrate the unit so road ~ straight on average.

        With anchor=(lat, lng) the depot is placed exactly there; otherwise the customers' centre
        sits on the city centre."""
        metric = cls.for_problem(problem, city, table, anchor)
        points = [(c.x, c.y) for c in problem.nodes]
        unique = list(dict.fromkeys(metric.latlng(x, y) for x, y in points))
        matrix = metric.table(unique)
        road = straight = 0.0
        for i, a in enumerate(unique):
            for j, b in enumerate(unique):
                if i != j and matrix[i][j] is not None:
                    metric.cache[(a, b)] = float(matrix[i][j])
                    road += matrix[i][j]
                    straight += math.hypot((b[0] - a[0]) * 111320,
                                           (b[1] - a[1]) * 111320 * math.cos(a[0] * math.pi / 180))
        if straight > 0 and road > 0:
            metric.ratio = road / straight
        return metric
