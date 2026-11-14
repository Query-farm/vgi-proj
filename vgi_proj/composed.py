"""Composed example queries: realistic SQL that combines several proj functions.

Each SQL is defined exactly once here and referenced from (a) the schema-level
``vgi.example_queries`` list in ``vgi_proj.worker`` and (b) the ``Meta.examples``
of the function that drives the query (within its first five examples, which is
all the vgi-lint agent simulator shows per object). Everything is offline and
deterministic (literal VALUES rows, no network), so they also run as
``vgi.executable_examples``.

Note: these deliberately avoid referencing a CTE that calls ``transform`` more
than once (e.g. self-joining it). DuckDB materialises such a CTE and the plan
round-trip then fails to re-resolve ``transform`` after its constant CRS
arguments were folded away (a vgi extension issue, not a worker one).
"""

from __future__ import annotations

from vgi.metadata import FunctionExample

# to_utm + geodesic_distance: UTM grid distance vs true ellipsoidal distance.
UTM_GRID_VS_GEODESIC = FunctionExample(
    description=(
        "Check straight-line UTM grid distances against the true WGS84 geodesic distance for "
        "three San Francisco survey segments (all in UTM zone 10): the grid figure runs 2.4-3.0 m "
        "short over these 6.6-7.9 km segments because of UTM's scale factor near the central meridian."
    ),
    sql=(
        "WITH segments(segment, lon1, lat1, lon2, lat2) AS (VALUES "
        "('Ferry Building -> Golden Gate Bridge', -122.3937, 37.7955, -122.4783, 37.8199), "
        "('Ferry Building -> Twin Peaks', -122.3937, 37.7955, -122.4477, 37.7544), "
        "('Twin Peaks -> Golden Gate Bridge', -122.4477, 37.7544, -122.4783, 37.8199)), "
        "u AS (SELECT segment, lon1, lat1, lon2, lat2, "
        "proj.main.to_utm(lon1, lat1) AS a, proj.main.to_utm(lon2, lat2) AS b FROM segments) "
        "SELECT segment, a.zone AS utm_zone, "
        "round(sqrt(power(b.easting - a.easting, 2) + power(b.northing - a.northing, 2)), 1) AS utm_grid_m, "
        "round(proj.main.geodesic_distance(lon1, lat1, lon2, lat2), 1) AS geodesic_m "
        "FROM u ORDER BY segment"
    ),
)

# geodesic_distance + geodesic_bearing: a distance/heading sheet from one origin.
DISTANCE_AND_HEADING_FROM_NYC = FunctionExample(
    description=(
        "Distance (km) and initial compass bearing (degrees) from New York to five world "
        "cities, nearest first (London 5,585 km at 51 deg ... Sydney 15,988 km at 266 deg)."
    ),
    sql=(
        "WITH destinations(city, lon, lat) AS (VALUES "
        "('London', -0.1276, 51.5074), ('Paris', 2.3522, 48.8566), ('Tokyo', 139.6917, 35.6895), "
        "('Sydney', 151.2093, -33.8688), ('Sao Paulo', -46.6333, -23.5505)) "
        "SELECT city, "
        "round(proj.main.geodesic_distance(-74.006, 40.7128, lon, lat) / 1000) AS distance_km, "
        "round(proj.main.geodesic_bearing(-74.006, 40.7128, lon, lat)) AS initial_bearing_deg "
        "FROM destinations ORDER BY distance_km"
    ),
)

# from_webmercator + geodesic_distance: true ground length of Web Mercator segments.
WEBMERCATOR_GROUND_DISTANCE = FunctionExample(
    description=(
        "Measure the true ground length of segments stored in Web Mercator (EPSG:3857) metres "
        "by converting their endpoints back to lon/lat: the same 5,000 Mercator metres is "
        "~5,000 m near the equator (Quito) but only ~2,513 m at Oslo (lat 59.9)."
    ),
    sql=(
        "WITH segments(segment, x1, y1, x2, y2) AS (VALUES "
        "('Quito, 5 km east', -8738000.0, -24600.0, -8733000.0, -24600.0), "
        "('Oslo, 5 km east', 1194000.0, 8381000.0, 1199000.0, 8381000.0)), "
        "ll AS (SELECT segment, x1, y1, x2, y2, "
        "proj.main.from_webmercator(x1, y1) AS a, proj.main.from_webmercator(x2, y2) AS b FROM segments) "
        "SELECT segment, round(a.lat, 2) AS lat, "
        "round(sqrt(power(x2 - x1, 2) + power(y2 - y1, 2))) AS mercator_m, "
        "round(proj.main.geodesic_distance(a.lon, a.lat, b.lon, b.lat)) AS geodesic_m "
        "FROM ll ORDER BY segment"
    ),
)

# transform + geodesic_distance: national-grid (British National Grid) points to km.
BNG_SITES_DISTANCE = FunctionExample(
    description=(
        "Reproject British National Grid (EPSG:27700) easting/northing sites to WGS84 lon/lat "
        "and rank them by geodesic distance from the London Eye (Cardiff Castle 212.6 km, "
        "York Minster 281.5 km, Edinburgh Castle 535.0 km)."
    ),
    sql=(
        "WITH eye AS (SELECT proj.main.transform(530600.0, 179700.0, 'EPSG:27700', 'EPSG:4326') AS o), "
        "sites(site, easting, northing) AS (VALUES ('Edinburgh Castle', 325160.0, 673490.0), "
        "('Cardiff Castle', 318080.0, 176540.0), ('York Minster', 460350.0, 452230.0)), "
        "ll AS (SELECT site, proj.main.transform(easting, northing, 'EPSG:27700', 'EPSG:4326') AS p "
        "FROM sites) "
        "SELECT site, round(p.x, 4) AS lon, round(p.y, 4) AS lat, "
        "round(proj.main.geodesic_distance(o.x, o.y, p.x, p.y) / 1000, 1) AS km_from_london_eye "
        "FROM ll CROSS JOIN eye ORDER BY km_from_london_eye"
    ),
)

COMPOSED_EXAMPLES: list[FunctionExample] = [
    BNG_SITES_DISTANCE,
    UTM_GRID_VS_GEODESIC,
    WEBMERCATOR_GROUND_DISTANCE,
    DISTANCE_AND_HEADING_FROM_NYC,
]
