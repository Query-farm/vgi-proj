"""Per-row scalar CRS-transformation and geodesic functions.

Every function here is a true DuckDB **scalar** -- one row in, one value out --
so it can be used inline in any projection or predicate:

    SELECT proj.transform(x, y, 'EPSG:4326', 'EPSG:3857')      FROM points;
    SELECT proj.to_webmercator(lon, lat).x                     FROM points;
    SELECT proj.to_utm(lon, lat).zone                          FROM points;
    SELECT proj.geodesic_distance(a_lon, a_lat, b_lon, b_lat)  FROM trips;
    SELECT proj.crs_name('EPSG:4326');

A note on argument syntax
-------------------------
VGI / DuckDB *scalar* functions take **positional** arguments (the ``name :=
value`` named-argument syntax is a property of table functions and macros, not
scalars). None of these functions have optional arguments, so there are no
arity overloads -- each is a single class.

Axis order
----------
All coordinate transforms use ``always_xy=True`` under the hood (see
``vgi_proj.projection``): inputs and outputs are always
**(x/easting/longitude, y/northing/latitude)** regardless of the CRS's declared
axis order.

NULL / error semantics
----------------------
A NULL or non-finite coordinate yields NULL output (never an error). An
**unknown / invalid CRS** raises a clear error (surfaced as a DuckDB query
error); the worker never crashes.

STRUCT returns REQUIRE explicit ``Returns(arrow_type=...)`` (the SDK cannot
infer a struct schema), so each struct type is declared once as a module
constant and reused in both the ``compute`` annotation and ``on_bind``.
"""

from __future__ import annotations

from typing import Annotated

import pyarrow as pa
from vgi.arguments import ConstParam, OutputLength, Param, Returns
from vgi.metadata import FunctionExample, NullHandling
from vgi.scalar_function import BindParameters, BindResult, ScalarFunction

from . import projection
from .meta import object_tags
from .schema_utils import field

# VGI509: at least one object ships guaranteed-runnable, catalog-qualified
# examples. Each ``sql`` is self-contained and re-runnable against an attached
# ``proj`` worker; ``expected_result`` is deliberately omitted (the linter only
# needs each query to execute cleanly, and pinning floating-point output is
# brittle for ellipsoidal/PROJ results).
_EXECUTABLE_EXAMPLES = (
    "["
    '{"description": "Transform a WGS84 lon/lat point to Web Mercator metres.",'
    " \"sql\": \"SELECT proj.main.transform(-122.42, 37.77, 'EPSG:4326', 'EPSG:3857') AS xy\"},"
    '{"description": "Project San Francisco into its UTM zone (10N).",'
    ' "sql": "SELECT proj.main.to_utm(-122.42, 37.77) AS utm"},'
    '{"description": "Convert a WGS84 point to Web Mercator via the shorthand.",'
    ' "sql": "SELECT proj.main.to_webmercator(-122.4194, 37.7749) AS xy"},'
    '{"description": "Round-trip Web Mercator metres back to lon/lat.",'
    ' "sql": "SELECT proj.main.from_webmercator(-13627665.27, 4547675.35) AS lonlat"},'
    '{"description": "Geodesic distance New York to London, in metres.",'
    ' "sql": "SELECT proj.main.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074) AS m"},'
    '{"description": "Initial geodesic bearing from New York toward London, degrees.",'
    ' "sql": "SELECT proj.main.geodesic_bearing(-74.006, 40.7128, -0.1276, 51.5074) AS deg"},'
    '{"description": "Human-readable name of the WGS84 CRS.",'
    ' "sql": "SELECT proj.main.crs_name(\'EPSG:4326\') AS name"},'
    '{"description": "Axis units of the Web Mercator CRS.",'
    ' "sql": "SELECT proj.main.crs_units(\'EPSG:3857\') AS units"},'
    '{"description": "Version of the bundled PROJ library.",'
    ' "sql": "SELECT proj.main.proj_version() AS proj_version"}'
    "]"
)

# ---------------------------------------------------------------------------
# STRUCT return types (explicit -- the SDK cannot infer them).
# ---------------------------------------------------------------------------

_XY_TYPE = pa.struct(
    [
        field("x", pa.float64(), "X / easting / longitude in the target CRS."),
        field("y", pa.float64(), "Y / northing / latitude in the target CRS."),
    ]
)

_LONLAT_TYPE = pa.struct(
    [
        field("lon", pa.float64(), "Longitude in degrees (WGS84)."),
        field("lat", pa.float64(), "Latitude in degrees (WGS84)."),
    ]
)

_UTM_TYPE = pa.struct(
    [
        field("easting", pa.float64(), "Easting in metres within the UTM zone."),
        field("northing", pa.float64(), "Northing in metres within the UTM zone."),
        field("zone", pa.int32(), "UTM zone number (1..60)."),
        field("hemisphere", pa.string(), "Hemisphere: 'N' or 'S'."),
    ]
)

_CRS_DOC = "CRS identifier (EPSG code/string, e.g. 'EPSG:4326'). Unknown CRS -> error."


# ---------------------------------------------------------------------------
# transform -- general CRS -> CRS (STRUCT(x, y)).
# ---------------------------------------------------------------------------


class TransformFunction(ScalarFunction):
    """``transform(x, y, from_crs, to_crs)`` -- general CRS->CRS transform."""

    class Meta:
        """Function metadata."""

        name = "transform"
        description = (
            "Transform (x, y) from from_crs to to_crs as STRUCT(x, y); always_xy axis order. "
            "NULL coord -> NULL; unknown CRS -> error"
        )
        categories = ["proj", "transform"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT proj.transform(-122.42, 37.77, 'EPSG:4326', 'EPSG:3857')",
                description="WGS84 lon/lat to Web Mercator metres",
            ),
        ]
        tags = {
            **object_tags(
                title="Transform Coordinates Between CRSs",
                doc_llm=(
                    "# transform\n\n"
                    "Reproject a single `(x, y)` coordinate pair from one coordinate "
                    "reference system (CRS) to another, identified by **EPSG** code/string "
                    "(e.g. `EPSG:4326` WGS84 lon/lat, `EPSG:3857` Web Mercator).\n\n"
                    "**When to use:** any time you need to convert between map projections "
                    "or geographic systems in SQL -- e.g. WGS84 lon/lat to a metric "
                    "projection for distance/area work, or between two national grids.\n\n"
                    "**Inputs:** `x` (longitude/easting in `from_crs`), `y` "
                    "(latitude/northing in `from_crs`), `from_crs`, `to_crs`. The CRS "
                    "arguments are constant-folded at plan time.\n\n"
                    "**Output:** `STRUCT(x DOUBLE, y DOUBLE)` in the target CRS.\n\n"
                    "**Axis order:** always `always_xy` -- inputs/outputs are "
                    "`(x/easting/longitude, y/northing/latitude)` regardless of the CRS's "
                    "declared native axis order.\n\n"
                    "**Edge cases:** a NULL or non-finite coordinate yields a NULL struct; "
                    "an unknown/invalid CRS raises a clear query error."
                ),
                doc_md=(
                    "## transform(x, y, from_crs, to_crs)\n\n"
                    "General CRS-to-CRS reprojection returning `STRUCT(x, y)` in the target "
                    "system.\n\n"
                    "### Usage\n\n"
                    "```sql\n"
                    "SELECT proj.transform(-122.42, 37.77, 'EPSG:4326', 'EPSG:3857');\n"
                    "SELECT proj.transform(lon, lat, 'EPSG:4326', 'EPSG:32610').x FROM pts;\n"
                    "```\n\n"
                    "### Notes\n\n"
                    "- Uses `always_xy` axis order: `(x/easting/longitude, "
                    "y/northing/latitude)`.\n"
                    "- NULL or non-finite coordinates produce a NULL struct.\n"
                    "- An unknown CRS raises a DuckDB query error rather than crashing."
                ),
                keywords=[
                    "transform",
                    "reproject",
                    "projection",
                    "coordinate transform",
                    "crs",
                    "epsg",
                    "wgs84",
                    "web mercator",
                    "4326",
                    "3857",
                    "convert coordinates",
                    "map projection",
                ],
            ),
            "vgi.executable_examples": _EXECUTABLE_EXAMPLES,
        }

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        """Declare the STRUCT output type at plan time."""
        return BindResult(_XY_TYPE)

    @classmethod
    def compute(
        cls,
        x: Annotated[pa.DoubleArray, Param(doc="X / longitude / easting in from_crs.")],
        y: Annotated[pa.DoubleArray, Param(doc="Y / latitude / northing in from_crs.")],
        from_crs: Annotated[str, ConstParam(_CRS_DOC)],
        to_crs: Annotated[str, ConstParam(_CRS_DOC)],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_XY_TYPE)]:
        """Map each input row to its output value."""
        xs = x.to_pylist()
        ys = y.to_pylist()
        out = [
            (lambda r: {"x": r.x, "y": r.y} if r is not None else None)(projection.transform(xv, yv, from_crs, to_crs))
            for xv, yv in zip(xs, ys, strict=True)
        ]
        return pa.array(out, type=_XY_TYPE)


# ---------------------------------------------------------------------------
# to_utm -- auto-pick the UTM zone (STRUCT(easting, northing, zone, hemisphere)).
# ---------------------------------------------------------------------------


class ToUtmFunction(ScalarFunction):
    """``to_utm(lon, lat)`` -- project into the point's auto-selected UTM zone."""

    class Meta:
        """Function metadata."""

        name = "to_utm"
        description = (
            "Project WGS84 (lon, lat) into its auto-selected UTM zone as "
            "STRUCT(easting, northing, zone, hemisphere); NULL/out-of-range -> NULL"
        )
        categories = ["proj", "utm"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT proj.to_utm(-122.42, 37.77)",
                description="UTM for San Francisco (zone 10N)",
            ),
            FunctionExample(
                sql="SELECT proj.to_utm(-122.42, 37.77).zone",
                description="UTM zone number for San Francisco (10)",
            ),
        ]
        tags = object_tags(
            title="Project to UTM Zone",
            doc_llm=(
                "# to_utm\n\n"
                "Project a WGS84 `(lon, lat)` point into its **auto-selected** Universal "
                "Transverse Mercator (UTM) zone, returning the easting/northing in metres "
                "plus the chosen zone and hemisphere.\n\n"
                "**When to use:** when you want a locally-accurate metric coordinate for a "
                "point without picking the zone yourself -- ideal for measuring distances "
                "or areas in metres near a single location.\n\n"
                "**Inputs:** `lon` (-180..180 degrees), `lat` (-90..90 degrees).\n\n"
                "**Output:** `STRUCT(easting DOUBLE, northing DOUBLE, zone INT32, "
                "hemisphere VARCHAR)`; `hemisphere` is `'N'` or `'S'`. The zone is derived "
                "from the longitude (`floor(lon/6)+31`).\n\n"
                "**Edge cases:** NULL, non-finite, or out-of-range (`|lat| > 90` / "
                "`|lon| > 180`) coordinates yield a NULL struct."
            ),
            doc_md=(
                "## to_utm(lon, lat)\n\n"
                "Project WGS84 lon/lat into the point's automatically-selected UTM zone.\n\n"
                "### Usage\n\n"
                "```sql\n"
                "SELECT proj.to_utm(-122.42, 37.77);        -- full struct\n"
                "SELECT proj.to_utm(-122.42, 37.77).zone;   -- 10\n"
                "SELECT proj.to_utm(lon, lat).easting FROM pts;\n"
                "```\n\n"
                "### Notes\n\n"
                "- Returns `STRUCT(easting, northing, zone, hemisphere)` with metres for "
                "easting/northing.\n"
                "- The zone is chosen from the longitude; the hemisphere is `'N'`/`'S'`.\n"
                "- Out-of-range or NULL coordinates produce a NULL struct."
            ),
            keywords=[
                "utm",
                "universal transverse mercator",
                "zone",
                "easting",
                "northing",
                "hemisphere",
                "project",
                "metric coordinates",
                "wgs84",
                "grid",
            ],
        )

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        """Declare the STRUCT output type at plan time."""
        return BindResult(_UTM_TYPE)

    @classmethod
    def compute(
        cls,
        lon: Annotated[pa.DoubleArray, Param(doc="Longitude in degrees (-180..180).")],
        lat: Annotated[pa.DoubleArray, Param(doc="Latitude in degrees (-90..90).")],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_UTM_TYPE)]:
        """Map each input row to its output value."""
        lons = lon.to_pylist()
        lats = lat.to_pylist()
        out = []
        for lo, la in zip(lons, lats, strict=True):
            r = projection.to_utm(lo, la)
            out.append(
                None
                if r is None
                else {
                    "easting": r.easting,
                    "northing": r.northing,
                    "zone": r.zone,
                    "hemisphere": r.hemisphere,
                }
            )
        return pa.array(out, type=_UTM_TYPE)


# ---------------------------------------------------------------------------
# to_webmercator / from_webmercator -- shorthands for the common pair.
# ---------------------------------------------------------------------------


class ToWebMercatorFunction(ScalarFunction):
    """``to_webmercator(lon, lat)`` -- WGS84 -> Web Mercator metres."""

    class Meta:
        """Function metadata."""

        name = "to_webmercator"
        description = "WGS84 (lon, lat) -> Web Mercator STRUCT(x, y) in metres (EPSG:4326->3857)"
        categories = ["proj", "webmercator"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT proj.to_webmercator(-122.4194, 37.7749)",
                description="San Francisco in Web Mercator metres",
            ),
        ]
        tags = object_tags(
            title="Convert to Web Mercator",
            doc_llm=(
                "# to_webmercator\n\n"
                "Shorthand for the most common reprojection: WGS84 `(lon, lat)` "
                "(`EPSG:4326`) to **Web Mercator** `(x, y)` metres (`EPSG:3857`), the CRS "
                "used by web map tiles (Google/OSM/Mapbox slippy maps).\n\n"
                "**When to use:** to place lon/lat points onto a web map or tile grid, or "
                "whenever you need the Web Mercator metric coordinate without spelling out "
                "the EPSG codes.\n\n"
                "**Inputs:** `lon`, `lat` in WGS84 degrees.\n\n"
                "**Output:** `STRUCT(x DOUBLE, y DOUBLE)` in Web Mercator metres.\n\n"
                "**Edge cases:** NULL/non-finite input yields a NULL struct. Web Mercator "
                "is undefined near the poles (|lat| ~> 85.06 deg), where values grow "
                "unbounded -- this is a property of the projection, not an error."
            ),
            doc_md=(
                "## to_webmercator(lon, lat)\n\n"
                "Convenience wrapper for `transform(lon, lat, 'EPSG:4326', 'EPSG:3857')`, "
                "returning `STRUCT(x, y)` in Web Mercator metres.\n\n"
                "### Usage\n\n"
                "```sql\n"
                "SELECT proj.to_webmercator(-122.4194, 37.7749);\n"
                "SELECT proj.to_webmercator(lon, lat).x FROM pts;\n"
                "```\n\n"
                "### Notes\n\n"
                "- Equivalent to a `transform` call into `EPSG:3857`, with `always_xy` "
                "axis order.\n"
                "- The projection is undefined near the poles; expect very large values "
                "above ~85.06 degrees latitude.\n"
                "- NULL or non-finite coordinates produce a NULL struct."
            ),
            keywords=[
                "web mercator",
                "webmercator",
                "3857",
                "4326",
                "tiles",
                "slippy map",
                "google maps",
                "openstreetmap",
                "project",
                "wgs84 to mercator",
            ],
        )

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        """Declare the STRUCT output type at plan time."""
        return BindResult(_XY_TYPE)

    @classmethod
    def compute(
        cls,
        lon: Annotated[pa.DoubleArray, Param(doc="Longitude in degrees (WGS84).")],
        lat: Annotated[pa.DoubleArray, Param(doc="Latitude in degrees (WGS84).")],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_XY_TYPE)]:
        """Map each input row to its output value."""
        lons = lon.to_pylist()
        lats = lat.to_pylist()
        out = []
        for lo, la in zip(lons, lats, strict=True):
            r = projection.to_webmercator(lo, la)
            out.append(None if r is None else {"x": r.x, "y": r.y})
        return pa.array(out, type=_XY_TYPE)


class FromWebMercatorFunction(ScalarFunction):
    """``from_webmercator(x, y)`` -- Web Mercator metres -> WGS84 lon/lat."""

    class Meta:
        """Function metadata."""

        name = "from_webmercator"
        description = "Web Mercator (x, y) metres -> WGS84 STRUCT(lon, lat) (EPSG:3857->4326)"
        categories = ["proj", "webmercator"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT proj.from_webmercator(-13627665.27, 4547675.35)",
                description="Web Mercator metres back to San Francisco lon/lat",
            ),
        ]
        tags = object_tags(
            title="Convert From Web Mercator",
            doc_llm=(
                "# from_webmercator\n\n"
                "Inverse of `to_webmercator`: convert **Web Mercator** `(x, y)` metres "
                "(`EPSG:3857`) back to WGS84 `(lon, lat)` degrees (`EPSG:4326`).\n\n"
                "**When to use:** to turn tile/pixel-derived Web Mercator coordinates back "
                "into geographic lon/lat, e.g. after reading map-tile geometry.\n\n"
                "**Inputs:** `x` (easting metres), `y` (northing metres).\n\n"
                "**Output:** `STRUCT(lon DOUBLE, lat DOUBLE)` in WGS84 degrees.\n\n"
                "**Edge cases:** NULL/non-finite input yields a NULL struct. This pairs "
                "exactly with `to_webmercator` for a round-trip."
            ),
            doc_md=(
                "## from_webmercator(x, y)\n\n"
                "Convenience wrapper for `transform(x, y, 'EPSG:3857', 'EPSG:4326')`, "
                "returning `STRUCT(lon, lat)` in WGS84 degrees.\n\n"
                "### Usage\n\n"
                "```sql\n"
                "SELECT proj.from_webmercator(-13627665.27, 4547675.35);\n"
                "SELECT proj.from_webmercator(x, y).lat FROM tiles;\n"
                "```\n\n"
                "### Notes\n\n"
                "- Inverse of `to_webmercator`; round-trips back to the original lon/lat "
                "(within PROJ precision).\n"
                "- Uses `always_xy` axis order.\n"
                "- NULL or non-finite coordinates produce a NULL struct."
            ),
            keywords=[
                "web mercator",
                "webmercator",
                "3857",
                "4326",
                "inverse",
                "unproject",
                "tiles",
                "mercator to lonlat",
                "lon lat",
                "wgs84",
            ],
        )

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        """Declare the STRUCT output type at plan time."""
        return BindResult(_LONLAT_TYPE)

    @classmethod
    def compute(
        cls,
        x: Annotated[pa.DoubleArray, Param(doc="Web Mercator easting (x) in metres.")],
        y: Annotated[pa.DoubleArray, Param(doc="Web Mercator northing (y) in metres.")],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_LONLAT_TYPE)]:
        """Map each input row to its output value."""
        xs = x.to_pylist()
        ys = y.to_pylist()
        out = []
        for xv, yv in zip(xs, ys, strict=True):
            r = projection.from_webmercator(xv, yv)
            out.append(None if r is None else {"lon": r.x, "lat": r.y})
        return pa.array(out, type=_LONLAT_TYPE)


# ---------------------------------------------------------------------------
# geodesic_distance / geodesic_bearing -- pure WGS84 ellipsoid (DOUBLE).
# ---------------------------------------------------------------------------


class GeodesicDistanceFunction(ScalarFunction):
    """``geodesic_distance(lon1, lat1, lon2, lat2)`` -- metres on WGS84."""

    class Meta:
        """Function metadata."""

        name = "geodesic_distance"
        description = "Accurate ellipsoidal (WGS84) geodesic distance between two points, in metres"
        categories = ["proj", "distance"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT proj.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074)",
                description="New York to London (~5,585,000 m)",
            ),
        ]
        tags = object_tags(
            title="Geodesic Distance Between Points",
            doc_llm=(
                "# geodesic_distance\n\n"
                "Compute the **accurate ellipsoidal (WGS84) geodesic distance** in metres "
                "between two `(lon, lat)` points. Unlike a spherical haversine, this uses "
                "the WGS84 ellipsoid (via PROJ's `Geod`), so it is correct to the metre "
                "over global distances.\n\n"
                "**When to use:** for true great-circle / shortest-path distances on Earth "
                "-- routing, proximity filters, nearest-neighbour queries -- where accuracy "
                "matters more than a flat-plane approximation.\n\n"
                "**Inputs:** `lon1, lat1` (point 1) and `lon2, lat2` (point 2) in degrees.\n\n"
                "**Output:** distance in metres (DOUBLE).\n\n"
                "**Edge cases:** any NULL/non-finite or out-of-range coordinate "
                "(`|lat| > 90` / `|lon| > 180`) yields NULL. The result is ~0.3% different "
                "from a spherical haversine because it is ellipsoidal."
            ),
            doc_md=(
                "## geodesic_distance(lon1, lat1, lon2, lat2)\n\n"
                "Accurate WGS84-ellipsoid geodesic distance, in metres, between two "
                "geographic points.\n\n"
                "### Usage\n\n"
                "```sql\n"
                "SELECT proj.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074);\n"
                "SELECT id FROM places\n"
                " WHERE proj.geodesic_distance(lon, lat, -122.42, 37.77) < 50000;\n"
                "```\n\n"
                "### Notes\n\n"
                "- Ellipsoidal (WGS84), so it differs from spherical haversine by ~0.3%.\n"
                "- NULL, non-finite, or out-of-range coordinates yield NULL.\n"
                "- Returns metres regardless of input units (inputs are always degrees)."
            ),
            keywords=[
                "geodesic",
                "distance",
                "great circle",
                "haversine",
                "ellipsoid",
                "wgs84",
                "meters",
                "metres",
                "proximity",
                "nearest",
                "how far",
                "geod",
            ],
        )

    @classmethod
    def compute(
        cls,
        lon1: Annotated[pa.DoubleArray, Param(doc="Longitude of point 1 (-180..180).")],
        lat1: Annotated[pa.DoubleArray, Param(doc="Latitude of point 1 (-90..90).")],
        lon2: Annotated[pa.DoubleArray, Param(doc="Longitude of point 2 (-180..180).")],
        lat2: Annotated[pa.DoubleArray, Param(doc="Latitude of point 2 (-90..90).")],
    ) -> Annotated[pa.DoubleArray, Returns()]:
        """Map each input row to its output value."""
        a, b, c, d = lon1.to_pylist(), lat1.to_pylist(), lon2.to_pylist(), lat2.to_pylist()
        return pa.array(
            [projection.geodesic_distance(*row) for row in zip(a, b, c, d, strict=True)],
            type=pa.float64(),
        )


class GeodesicBearingFunction(ScalarFunction):
    """``geodesic_bearing(lon1, lat1, lon2, lat2)`` -- initial azimuth degrees."""

    class Meta:
        """Function metadata."""

        name = "geodesic_bearing"
        description = "Initial geodesic bearing (forward azimuth) from point 1 to point 2, degrees [0,360)"
        categories = ["proj", "distance"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT proj.geodesic_bearing(-74.006, 40.7128, -0.1276, 51.5074)",
                description="Initial bearing from New York toward London",
            ),
        ]
        tags = object_tags(
            title="Geodesic Initial Bearing",
            doc_llm=(
                "# geodesic_bearing\n\n"
                "Compute the **initial geodesic bearing** (forward azimuth) in degrees "
                "from point 1 toward point 2 on the WGS84 ellipsoid, normalized to "
                "`[0, 360)` where 0 = north, 90 = east.\n\n"
                "**When to use:** to find the compass direction to travel from one "
                "location to another along the shortest path -- navigation, heading "
                "indicators, directional filters.\n\n"
                "**Inputs:** `lon1, lat1` (origin) and `lon2, lat2` (destination) in "
                "degrees.\n\n"
                "**Output:** initial forward azimuth in degrees, `[0, 360)` (DOUBLE).\n\n"
                "**Edge cases:** NULL/non-finite or out-of-range coordinates yield NULL. "
                "It is the *initial* bearing -- along a geodesic the bearing changes "
                "continuously, so the arrival bearing differs from this value."
            ),
            doc_md=(
                "## geodesic_bearing(lon1, lat1, lon2, lat2)\n\n"
                "Initial forward azimuth (compass heading) in degrees `[0, 360)` from "
                "point 1 toward point 2 on the WGS84 ellipsoid.\n\n"
                "### Usage\n\n"
                "```sql\n"
                "SELECT proj.geodesic_bearing(-74.006, 40.7128, -0.1276, 51.5074);\n"
                "SELECT proj.geodesic_bearing(from_lon, from_lat, to_lon, to_lat)\n"
                " FROM legs;\n"
                "```\n\n"
                "### Notes\n\n"
                "- 0 deg = north, 90 deg = east; result is normalized to `[0, 360)`.\n"
                "- This is the *initial* bearing; along a geodesic it changes en route.\n"
                "- NULL, non-finite, or out-of-range coordinates yield NULL."
            ),
            keywords=[
                "bearing",
                "azimuth",
                "heading",
                "direction",
                "compass",
                "geodesic",
                "forward azimuth",
                "navigation",
                "which way",
                "degrees",
                "wgs84",
            ],
        )

    @classmethod
    def compute(
        cls,
        lon1: Annotated[pa.DoubleArray, Param(doc="Longitude of point 1 (-180..180).")],
        lat1: Annotated[pa.DoubleArray, Param(doc="Latitude of point 1 (-90..90).")],
        lon2: Annotated[pa.DoubleArray, Param(doc="Longitude of point 2 (-180..180).")],
        lat2: Annotated[pa.DoubleArray, Param(doc="Latitude of point 2 (-90..90).")],
    ) -> Annotated[pa.DoubleArray, Returns()]:
        """Map each input row to its output value."""
        a, b, c, d = lon1.to_pylist(), lat1.to_pylist(), lon2.to_pylist(), lat2.to_pylist()
        return pa.array(
            [projection.geodesic_bearing(*row) for row in zip(a, b, c, d, strict=True)],
            type=pa.float64(),
        )


# ---------------------------------------------------------------------------
# CRS metadata -- crs_units / crs_name / proj_version (VARCHAR).
# ---------------------------------------------------------------------------


class CrsUnitsFunction(ScalarFunction):
    """``crs_units(crs)`` -- unit name of the CRS's first axis."""

    class Meta:
        """Function metadata."""

        name = "crs_units"
        description = "Unit name of a CRS's first axis (e.g. 'degree', 'metre'); unknown CRS -> error"
        categories = ["proj", "crs"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT proj.crs_units('EPSG:3857')",
                description="Units of Web Mercator ('metre')",
            ),
        ]
        tags = object_tags(
            title="Look Up CRS Axis Units",
            doc_llm=(
                "# crs_units\n\n"
                "Return the **unit name of a CRS's first axis** -- e.g. `'degree'` for a "
                "geographic CRS like `EPSG:4326`, or `'metre'` for a projected CRS like "
                "`EPSG:3857`.\n\n"
                "**When to use:** to discover whether a CRS is angular (degrees) or "
                "linear (metres/feet) before deciding how to interpret or transform its "
                "coordinates.\n\n"
                "**Inputs:** `crs` -- an EPSG code/string (e.g. `'EPSG:3857'`).\n\n"
                "**Output:** the axis unit name as VARCHAR.\n\n"
                "**Edge cases:** NULL input yields NULL; an unknown/invalid CRS raises a "
                "clear query error."
            ),
            doc_md=(
                "## crs_units(crs)\n\n"
                "Return the unit of measure of a CRS's first axis (e.g. `degree`, "
                "`metre`).\n\n"
                "### Usage\n\n"
                "```sql\n"
                "SELECT proj.crs_units('EPSG:3857');  -- 'metre'\n"
                "SELECT proj.crs_units('EPSG:4326');  -- 'degree'\n"
                "```\n\n"
                "### Notes\n\n"
                "- Tells you whether a CRS is angular or linear.\n"
                "- NULL input yields NULL; an unknown CRS raises a query error."
            ),
            keywords=[
                "crs units",
                "axis units",
                "degree",
                "metre",
                "meter",
                "foot",
                "unit of measure",
                "epsg",
                "projection units",
                "angular",
                "linear",
            ],
        )

    @classmethod
    def compute(cls, crs: Annotated[pa.StringArray, Param(doc=_CRS_DOC)]) -> Annotated[pa.StringArray, Returns()]:
        """Map each input row to its output value."""
        return pa.array([projection.crs_units(c) for c in crs.to_pylist()], type=pa.string())


class CrsNameFunction(ScalarFunction):
    """``crs_name(crs)`` -- human-readable CRS name."""

    class Meta:
        """Function metadata."""

        name = "crs_name"
        description = "Human-readable name of a CRS (e.g. 'WGS 84'); unknown CRS -> error"
        categories = ["proj", "crs"]
        null_handling = NullHandling.SPECIAL
        examples = [
            FunctionExample(
                sql="SELECT proj.crs_name('EPSG:4326')",
                description="Name of WGS84 ('WGS 84')",
            ),
        ]
        tags = object_tags(
            title="Look Up CRS Display Name",
            doc_llm=(
                "# crs_name\n\n"
                "Return the **human-readable name** of a CRS -- e.g. `'WGS 84'` for "
                "`EPSG:4326` or `'WGS 84 / Pseudo-Mercator'` for `EPSG:3857`.\n\n"
                "**When to use:** to label, audit, or display CRS codes in a "
                "user-friendly way, or to confirm that a code resolves to the CRS you "
                "expect.\n\n"
                "**Inputs:** `crs` -- an EPSG code/string (e.g. `'EPSG:4326'`).\n\n"
                "**Output:** the CRS name as VARCHAR.\n\n"
                "**Edge cases:** NULL input yields NULL; an unknown/invalid CRS raises a "
                "clear query error."
            ),
            doc_md=(
                "## crs_name(crs)\n\n"
                "Return the official human-readable name of a coordinate reference "
                "system.\n\n"
                "### Usage\n\n"
                "```sql\n"
                "SELECT proj.crs_name('EPSG:4326');  -- 'WGS 84'\n"
                "SELECT proj.crs_name('EPSG:3857');  -- 'WGS 84 / Pseudo-Mercator'\n"
                "```\n\n"
                "### Notes\n\n"
                "- Useful for labelling CRS codes in reports and UIs.\n"
                "- NULL input yields NULL; an unknown CRS raises a query error."
            ),
            keywords=[
                "crs name",
                "projection name",
                "epsg name",
                "wgs 84",
                "describe crs",
                "lookup crs",
                "identify projection",
                "coordinate system name",
            ],
        )

    @classmethod
    def compute(cls, crs: Annotated[pa.StringArray, Param(doc=_CRS_DOC)]) -> Annotated[pa.StringArray, Returns()]:
        """Map each input row to its output value."""
        return pa.array([projection.crs_name(c) for c in crs.to_pylist()], type=pa.string())


class ProjVersionFunction(ScalarFunction):
    """``proj_version()`` -- version of the underlying PROJ library."""

    class Meta:
        """Function metadata."""

        name = "proj_version"
        description = "Version string of the underlying PROJ library (bundled in the pyproj wheel)"
        categories = ["proj", "crs"]
        examples = [
            FunctionExample(
                sql="SELECT proj.proj_version()",
                description="PROJ library version",
            ),
        ]
        tags = object_tags(
            title="Bundled PROJ Library Version",
            doc_llm=(
                "# proj_version\n\n"
                "Return the version string of the **PROJ** C library bundled inside the "
                "`pyproj` wheel that powers every transform in this worker (a zero-argument "
                "scalar).\n\n"
                "**When to use:** for diagnostics and reproducibility -- to record which "
                "PROJ release produced a set of transformed coordinates, or to confirm the "
                "library is present and bundled (no separate native install).\n\n"
                "**Inputs:** none.\n\n"
                "**Output:** the PROJ version string (e.g. `'9.4.0'`) as VARCHAR, repeated "
                "once per output row.\n\n"
                "**Edge cases:** none -- it always returns a value; it never depends on "
                "input data or a network."
            ),
            doc_md=(
                "## proj_version()\n\n"
                "Return the version of the bundled PROJ library (zero-argument scalar).\n\n"
                "### Usage\n\n"
                "```sql\n"
                "SELECT proj.proj_version();  -- e.g. '9.4.0'\n"
                "```\n\n"
                "### Notes\n\n"
                "- PROJ and its data grids are bundled in the `pyproj` wheel, so this "
                "confirms the bundling and reports the exact release.\n"
                "- Useful for provenance: which PROJ produced these coordinates.\n"
                "- Takes no arguments and never returns NULL."
            ),
            keywords=[
                "proj version",
                "proj library",
                "pyproj",
                "version",
                "diagnostics",
                "provenance",
                "build info",
                "library version",
                "about",
            ],
        )

    @classmethod
    def compute(
        cls,
        _length: Annotated[int, OutputLength()],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Map each input row to its output value."""
        version = projection.proj_version()
        return pa.array([version] * _length, type=pa.string())


SCALAR_FUNCTIONS: list[type] = [
    TransformFunction,
    ToUtmFunction,
    ToWebMercatorFunction,
    FromWebMercatorFunction,
    GeodesicDistanceFunction,
    GeodesicBearingFunction,
    CrsUnitsFunction,
    CrsNameFunction,
    ProjVersionFunction,
]
