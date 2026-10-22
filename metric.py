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
# Default depot per location: the "amazon" point on Jimmy Carter Road, VIT (opposite Ruby Tower / Perl Research Park).
DEFAULT_DEPOT = {"vit": (12.97234, 79.16561)}
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
    """(distance matrix in metres, snapped on-road lat/lng per point) from the OSRM demo server."""
    coords = ";".join(f"{lng},{lat}" for lat, lng in latlngs)
    try:
        response = httpx.get(OSRM_TABLE.format(coords=coords), timeout=20,
                             headers={"User-Agent": "GreenMile-student-demo"})
        response.raise_for_status()
        body = response.json()
        snapped = [(round(w["location"][1], 5), round(w["location"][0], 5)) for w in body["sources"]]
        return body["distances"], snapped
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise RoadError(str(exc)) from exc


class RoadMetric:
    def __init__(self, city, centre, table=None, origin=None, speed_kmh=None):
        if city not in CITIES:
            raise ValueError(f"Unknown city: {city}")
        self.city, self.centre = city, centre
        self.table = table or fetch_table   # resolved at call time so tests can replace fetch_table
        self.origin = origin or CITIES[city]   # lat/lng that the plane point `centre` maps to
        self.speed_kmh = speed_kmh             # average driving speed; None = calibrate to the Solomon scale
        self.cache = {}
        self.snap = {}                        # placed lat/lng -> nearest on-road lat/lng
        self.lock = RLock()
        self.ratio = 1.4                       # road / straight, replaced by calibration
        self.degraded = False                  # True once any distance had to be estimated

    @property
    def metres_per_unit(self):
        """Road metres per time unit (= one minute). With a speed this is speed/60, so times are realistic."""
        return self.speed_kmh * 1000 / 60 if self.speed_kmh else PLACE_M * self.ratio

    @property
    def geo(self):
        lat0, lng0 = self.origin
        return {"city": self.city, "lat0": lat0, "lng0": lng0, "cx": self.centre[0], "cy": self.centre[1],
                "place_m": PLACE_M, "metres_per_unit": self.metres_per_unit, "speed_kmh": self.speed_kmh}

    def __deepcopy__(self, memo):               # simulator forks share one cache
        return self

    def latlng(self, x, y):
        lat0, lng0 = self.origin
        return (round(lat0 + (y - self.centre[1]) * PLACE_M / 111320, 5),
                round(lng0 + (x - self.centre[0]) * PLACE_M / (111320 * math.cos(lat0 * math.pi / 180)), 5))

    def _table(self, unique):
        """Call the table function; tolerate fakes that return only a matrix (no snapping)."""
        result = self.table(unique)
        if isinstance(result, tuple):
            return result
        return result, list(unique)

    def snapped_latlng(self, x, y):
        """Where this plane point actually sits on the road network (falls back to the placed point)."""
        placed = self.latlng(x, y)
        return self.snap.get(placed, placed)

    def _estimate_metres(self, x1, y1, x2, y2):
        return math.hypot(x2 - x1, y2 - y1) * PLACE_M * self.ratio

    def prefetch(self, points):
        unique = list(dict.fromkeys(self.latlng(x, y) for x, y in points))
        with self.lock:
            if all((a, b) in self.cache for a in unique for b in unique if a != b) or len(unique) > MAX_TABLE:
                return
        try:
            matrix, snapped = self._table(unique)
        except RoadError:
            self.degraded = True
            return
        with self.lock:
            self.snap.update(zip(unique, snapped))
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
    def for_problem(cls, problem, city, table=None, anchor=None, speed_kmh=None):
        """Metric with the grid placed on the city (or the depot on `anchor`), no distances fetched yet."""
        if anchor is not None:
            return cls(city, (problem.depot.x, problem.depot.y), table, origin=tuple(anchor), speed_kmh=speed_kmh)
        customers = problem.customers or (problem.depot,)
        centre = ((min(c.x for c in customers) + max(c.x for c in customers)) / 2,
                  (min(c.y for c in customers) + max(c.y for c in customers)) / 2)
        return cls(city, centre, table, speed_kmh=speed_kmh)

    @classmethod
    def build(cls, problem, city, table=None, anchor=None, speed_kmh=None):
        """Fetch the initial node matrix and calibrate the unit so road ~ straight on average.

        With anchor=(lat, lng) the depot is placed exactly there; otherwise the customers' centre
        sits on the city centre."""
        metric = cls.for_problem(problem, city, table, anchor, speed_kmh)
        points = [(c.x, c.y) for c in problem.nodes]
        unique = list(dict.fromkeys(metric.latlng(x, y) for x, y in points))
        matrix, snapped = metric._table(unique)
        metric.snap.update(zip(unique, snapped))
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
