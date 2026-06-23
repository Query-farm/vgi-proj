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
from .schema_utils import field

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

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        return BindResult(_XY_TYPE)

    @classmethod
    def compute(
        cls,
        x: Annotated[pa.DoubleArray, Param(doc="X / longitude / easting in from_crs.")],
        y: Annotated[pa.DoubleArray, Param(doc="Y / latitude / northing in from_crs.")],
        from_crs: Annotated[str, ConstParam(_CRS_DOC)],
        to_crs: Annotated[str, ConstParam(_CRS_DOC)],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_XY_TYPE)]:
        xs = x.to_pylist()
        ys = y.to_pylist()
        out = [
            (lambda r: {"x": r.x, "y": r.y} if r is not None else None)(
                projection.transform(xv, yv, from_crs, to_crs)
            )
            for xv, yv in zip(xs, ys, strict=True)
        ]
        return pa.array(out, type=_XY_TYPE)


# ---------------------------------------------------------------------------
# to_utm -- auto-pick the UTM zone (STRUCT(easting, northing, zone, hemisphere)).
# ---------------------------------------------------------------------------


class ToUtmFunction(ScalarFunction):
    """``to_utm(lon, lat)`` -- project into the point's auto-selected UTM zone."""

    class Meta:
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

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        return BindResult(_UTM_TYPE)

    @classmethod
    def compute(
        cls,
        lon: Annotated[pa.DoubleArray, Param(doc="Longitude in degrees (-180..180).")],
        lat: Annotated[pa.DoubleArray, Param(doc="Latitude in degrees (-90..90).")],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_UTM_TYPE)]:
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

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        return BindResult(_XY_TYPE)

    @classmethod
    def compute(
        cls,
        lon: Annotated[pa.DoubleArray, Param(doc="Longitude in degrees (WGS84).")],
        lat: Annotated[pa.DoubleArray, Param(doc="Latitude in degrees (WGS84).")],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_XY_TYPE)]:
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

    @classmethod
    def on_bind(cls, params: BindParameters) -> BindResult:
        return BindResult(_LONLAT_TYPE)

    @classmethod
    def compute(
        cls,
        x: Annotated[pa.DoubleArray, Param(doc="Web Mercator easting (x) in metres.")],
        y: Annotated[pa.DoubleArray, Param(doc="Web Mercator northing (y) in metres.")],
    ) -> Annotated[pa.StructArray, Returns(arrow_type=_LONLAT_TYPE)]:
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

    @classmethod
    def compute(
        cls,
        lon1: Annotated[pa.DoubleArray, Param(doc="Longitude of point 1 (-180..180).")],
        lat1: Annotated[pa.DoubleArray, Param(doc="Latitude of point 1 (-90..90).")],
        lon2: Annotated[pa.DoubleArray, Param(doc="Longitude of point 2 (-180..180).")],
        lat2: Annotated[pa.DoubleArray, Param(doc="Latitude of point 2 (-90..90).")],
    ) -> Annotated[pa.DoubleArray, Returns()]:
        a, b, c, d = lon1.to_pylist(), lat1.to_pylist(), lon2.to_pylist(), lat2.to_pylist()
        return pa.array(
            [projection.geodesic_distance(*row) for row in zip(a, b, c, d, strict=True)],
            type=pa.float64(),
        )


class GeodesicBearingFunction(ScalarFunction):
    """``geodesic_bearing(lon1, lat1, lon2, lat2)`` -- initial azimuth degrees."""

    class Meta:
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

    @classmethod
    def compute(
        cls,
        lon1: Annotated[pa.DoubleArray, Param(doc="Longitude of point 1 (-180..180).")],
        lat1: Annotated[pa.DoubleArray, Param(doc="Latitude of point 1 (-90..90).")],
        lon2: Annotated[pa.DoubleArray, Param(doc="Longitude of point 2 (-180..180).")],
        lat2: Annotated[pa.DoubleArray, Param(doc="Latitude of point 2 (-90..90).")],
    ) -> Annotated[pa.DoubleArray, Returns()]:
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

    @classmethod
    def compute(
        cls, crs: Annotated[pa.StringArray, Param(doc=_CRS_DOC)]
    ) -> Annotated[pa.StringArray, Returns()]:
        return pa.array([projection.crs_units(c) for c in crs.to_pylist()], type=pa.string())


class CrsNameFunction(ScalarFunction):
    """``crs_name(crs)`` -- human-readable CRS name."""

    class Meta:
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

    @classmethod
    def compute(
        cls, crs: Annotated[pa.StringArray, Param(doc=_CRS_DOC)]
    ) -> Annotated[pa.StringArray, Returns()]:
        return pa.array([projection.crs_name(c) for c in crs.to_pylist()], type=pa.string())


class ProjVersionFunction(ScalarFunction):
    """``proj_version()`` -- version of the underlying PROJ library."""

    class Meta:
        name = "proj_version"
        description = "Version string of the underlying PROJ library (bundled in the pyproj wheel)"
        categories = ["proj", "crs"]
        examples = [
            FunctionExample(
                sql="SELECT proj.proj_version()",
                description="PROJ library version",
            ),
        ]

    @classmethod
    def compute(
        cls,
        _length: Annotated[int, OutputLength()],
    ) -> Annotated[pa.StringArray, Returns()]:
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
