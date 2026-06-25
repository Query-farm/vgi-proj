# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.5",
#     "pyproj>=3.6",
#     "pyarrow",
# ]
# ///
"""VGI worker exposing CRS transforms + geodesic distance to DuckDB/SQL.

Assembles the scalar functions in ``vgi_proj`` into a single ``proj`` catalog
and runs the worker over stdio (DuckDB subprocess) or HTTP. It does
coordinate-reference-system (CRS) transformations and accurate ellipsoidal
geodesic distance/bearing via ``pyproj`` (which wraps the PROJ C library; the
``pyproj`` wheel BUNDLES PROJ and its data grids -- no separate native install).

CRS identifiers are EPSG codes/strings (e.g. 'EPSG:4326' = WGS84,
'EPSG:3857' = Web Mercator). All transforms use ``always_xy=True`` so the axis
order is always (x/easting/longitude, y/northing/latitude).

Usage:
    uv run proj_worker.py             # serve over stdio (DuckDB subprocess)

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'proj' (TYPE vgi, LOCATION 'uv run proj_worker.py');

    SELECT proj.transform(-122.42, 37.77, 'EPSG:4326', 'EPSG:3857');  -- STRUCT(x, y)
    SELECT proj.to_webmercator(-122.4194, 37.7749);                   -- STRUCT(x, y)
    SELECT proj.from_webmercator(x, y);                               -- STRUCT(lon, lat)
    SELECT proj.to_utm(-122.42, 37.77);                               -- STRUCT(easting, northing, zone, hemi)
    SELECT proj.to_utm(-122.42, 37.77).zone;                          -- 10
    SELECT proj.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074);-- ~5585000 m
    SELECT proj.geodesic_bearing(-74.006, 40.7128, -0.1276, 51.5074); -- degrees
    SELECT proj.crs_name('EPSG:4326');                                -- 'WGS 84'
    SELECT proj.crs_units('EPSG:3857');                               -- 'metre'
    SELECT proj.proj_version();                                       -- PROJ version
"""

from __future__ import annotations

from typing import Any

from vgi import Worker
from vgi.catalog import Catalog, Schema

from vgi_proj import projection
from vgi_proj.meta import keywords_json
from vgi_proj.scalars import SCALAR_FUNCTIONS

_REPO_URL = "https://github.com/Query-farm/vgi-proj"

_CATALOG_TAGS = {
    "vgi.title": "CRS Transforms & Geodesic Geometry",
    "vgi.keywords": keywords_json(
        [
            "proj",
            "pyproj",
            "crs",
            "coordinate reference system",
            "epsg",
            "transform",
            "reproject",
            "projection",
            "wgs84",
            "web mercator",
            "utm",
            "geodesic",
            "distance",
            "bearing",
            "azimuth",
            "gis",
            "mapping",
            "longitude",
            "latitude",
        ]
    ),
    "vgi.doc_llm": (
        "Coordinate-reference-system (CRS) transforms and accurate ellipsoidal (WGS84) geodesic "
        "geometry for SQL, backed by pyproj/PROJ. Transform (x, y) between any two CRSs by EPSG "
        "code (e.g. EPSG:4326 WGS84 lon/lat to EPSG:3857 Web Mercator), project a WGS84 point into "
        "its auto-selected UTM zone, convert to/from Web Mercator, compute geodesic distance "
        "(metres) and initial bearing (degrees) between two lon/lat points, and look up a CRS's "
        "human-readable name and axis units. Use for reprojection, mapping, and great-circle "
        "distance/bearing in SQL. All transforms use always_xy axis order (x/easting/longitude, "
        "y/northing/latitude)."
    ),
    "vgi.doc_md": (
        "# proj\n\n"
        "CRS transforms and accurate WGS84 geodesic distance/bearing over Apache Arrow, via "
        "pyproj/PROJ (PROJ and its data grids are bundled in the pyproj wheel).\n\n"
        "Scalars: `transform`, `to_utm`, `to_webmercator`, `from_webmercator`, "
        "`geodesic_distance`, `geodesic_bearing`, `crs_name`, `crs_units`, `proj_version`.\n\n"
        "All transforms use `always_xy=True`, so inputs/outputs are "
        "`(x/easting/longitude, y/northing/latitude)` regardless of the CRS's declared axis order."
    ),
    "vgi.author": "Query.Farm",
    "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
    "vgi.license": "MIT",
    "vgi.support_contact": f"{_REPO_URL}/issues",
    "vgi.support_policy_url": f"{_REPO_URL}/blob/main/README.md",
}

_MAIN_SCHEMA_TAGS = {
    "vgi.title": "Proj Transforms & Geodesics",
    "vgi.keywords": keywords_json(
        [
            "transform",
            "to_utm",
            "to_webmercator",
            "from_webmercator",
            "geodesic_distance",
            "geodesic_bearing",
            "crs_name",
            "crs_units",
            "proj_version",
            "crs",
            "epsg",
            "projection",
            "reproject",
            "wgs84",
            "web mercator",
            "utm",
            "geodesic",
        ]
    ),
    # VGI123 classifying tags use BARE keys (NOT vgi.-namespaced).
    "domain": "geospatial",
    "category": "projection",
    "topic": "coordinate-reference-systems",
    # VGI139: vgi.source_url belongs only on the catalog, not on each object.
    "vgi.doc_llm": (
        "CRS transform and geodesic functions: transform (x, y) between CRSs by EPSG code, project "
        "WGS84 lon/lat into UTM, convert to/from Web Mercator, compute ellipsoidal geodesic "
        "distance (metres) and bearing (degrees) between two points, and look up a CRS's name and "
        "axis units. All coordinate I/O uses always_xy order (x/easting/longitude, "
        "y/northing/latitude)."
    ),
    "vgi.doc_md": (
        "# main\n\n"
        "Coordinate-reference-system (CRS) transform and WGS84 geodesic functions, served "
        "over Apache Arrow and backed by pyproj/PROJ (PROJ and its data grids are bundled "
        "in the pyproj wheel, so there is no separate native install).\n\n"
        "## Functions\n\n"
        "- `transform`, `to_utm`, `to_webmercator`, `from_webmercator` -- reproject "
        "coordinates between CRSs by EPSG code, returning STRUCT outputs.\n"
        "- `geodesic_distance`, `geodesic_bearing` -- accurate ellipsoidal (WGS84) "
        "distance in metres and initial bearing in degrees between two lon/lat points.\n"
        "- `crs_name`, `crs_units`, `proj_version` -- CRS metadata lookups and the bundled "
        "PROJ version.\n\n"
        "## Conventions\n\n"
        "All coordinate I/O uses `always_xy` axis order "
        "`(x/easting/longitude, y/northing/latitude)` regardless of the CRS's declared "
        "native axis order. NULL/non-finite (and, where applicable, out-of-range) "
        "coordinates yield NULL; an unknown CRS raises a clear query error."
    ),
    # VGI506 representative example queries for the schema (catalog-qualified SQL).
    "vgi.example_queries": (
        "SELECT proj.main.transform(-122.42, 37.77, 'EPSG:4326', 'EPSG:3857');\n"
        "SELECT proj.main.to_utm(-122.42, 37.77).zone;\n"
        "SELECT proj.main.to_webmercator(-122.4194, 37.7749);\n"
        "SELECT proj.main.from_webmercator(-13627665.27, 4547675.35);\n"
        "SELECT proj.main.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074);\n"
        "SELECT proj.main.geodesic_bearing(-74.006, 40.7128, -0.1276, 51.5074);\n"
        "SELECT proj.main.crs_name('EPSG:4326');\n"
        "SELECT proj.main.crs_units('EPSG:3857');\n"
        "SELECT proj.main.proj_version();"
    ),
}

_PROJ_CATALOG = Catalog(
    name="proj",
    default_schema="main",
    comment="CRS transforms (always_xy) + WGS84 geodesic distance/bearing via pyproj/PROJ.",
    tags=_CATALOG_TAGS,
    source_url=_REPO_URL,
    schemas=[
        Schema(
            name="main",
            comment="CRS transforms (always_xy) + WGS84 geodesic distance/bearing via pyproj/PROJ",
            tags=_MAIN_SCHEMA_TAGS,
            functions=list(SCALAR_FUNCTIONS),
        ),
    ],
)


class ProjWorker(Worker):
    """Worker process hosting the ``proj`` catalog."""

    catalog = _PROJ_CATALOG

    def run(self, otel_config: Any = None) -> None:
        """Warm the common Transformers, then serve.

        Building a ``pyproj.Transformer`` is expensive (PROJ resolves the CRS
        definitions and operation pipeline). Without warming, the first query of
        every ATTACH pays that one-time cost inline -- a window in which a
        worker-pool teardown SIGTERM (or a loaded host) can kill the run
        mid-assertion and record a spurious E2E failure. Warming the common
        WGS84<->WebMercator transformers + the Geod at spawn moves the cost ahead
        of any query, keeping the SQL suite deterministic. Best-effort; never fatal.
        """
        projection.warm_up()
        super().run(otel_config=otel_config)


def main() -> None:
    """Run the proj worker process (stdio or, via flags, HTTP)."""
    ProjWorker.main()


if __name__ == "__main__":
    main()
