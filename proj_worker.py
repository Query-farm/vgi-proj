# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.9.0",
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

import json
from typing import Any

from vgi import Worker
from vgi.catalog import Catalog, Schema

from vgi_proj import projection
from vgi_proj.meta import keywords_json
from vgi_proj.scalars import SCALAR_FUNCTIONS

_REPO_URL = "https://github.com/Query-farm/vgi-proj"

# VGI413/VGI410: an ordered navigation registry for the `main` schema. Each entry
# is {"name","description"}; every function declares a matching `vgi.category`
# (see vgi_proj.meta.object_tags), so the schema's objects group into these
# sections for listing/SEO/navigation.
_SCHEMA_CATEGORIES = json.dumps(
    [
        {
            "name": "transform",
            "description": "General coordinate reprojection between any two CRSs identified by EPSG code.",
        },
        {
            "name": "webmercator",
            "description": "Conversions to and from the Web Mercator (EPSG:3857) web-map tile projection.",
        },
        {
            "name": "utm",
            "description": "Projection of a WGS84 lon/lat point into its automatically selected UTM zone.",
        },
        {
            "name": "geodesic",
            "description": "Accurate WGS84-ellipsoid geodesic distance (metres) and initial bearing (degrees).",
        },
        {
            "name": "crs",
            "description": "CRS metadata lookups (name, axis units) and the bundled PROJ library version.",
        },
    ]
)

# VGI152/VGI920: a fixed agent-suitability suite. Each task's `prompt` is the only
# text the analyst LLM sees; `reference_sql` is the grader-only canonical answer.
# All references are deterministic (stable strings, integer zones, or rounded
# metres/degrees/coordinates) and `ignore_column_names` is set so grading matches
# on values regardless of how the analyst aliases its output column.
_AGENT_TEST_TASKS = json.dumps(
    [
        {
            "name": "crs_display_name",
            "prompt": (
                "What is the official human-readable name of the coordinate reference "
                "system identified by EPSG code 4326?"
            ),
            "reference_sql": "SELECT proj.main.crs_name('EPSG:4326')",
            "success_criteria": "Answers 'WGS 84', the display name of EPSG:4326.",
            "ignore_column_names": True,
        },
        {
            "name": "crs_axis_units",
            "prompt": ("What are the linear axis units of the Web Mercator coordinate reference system, EPSG:3857?"),
            "reference_sql": "SELECT proj.main.crs_units('EPSG:3857')",
            "success_criteria": "Answers 'metre' (the axis unit of EPSG:3857).",
            "ignore_column_names": True,
        },
        {
            "name": "utm_zone_for_point",
            "prompt": (
                "Which UTM zone number contains the WGS84 location at longitude -122.42, "
                "latitude 37.77? Return just the zone number."
            ),
            "reference_sql": "SELECT proj.main.to_utm(-122.42, 37.77).zone",
            "success_criteria": "Answers 10 (the UTM zone for that longitude).",
            "ignore_column_names": True,
        },
        {
            "name": "geodesic_distance_km",
            "prompt": (
                "Using the accurate WGS84 ellipsoid, what is the geodesic distance in whole "
                "kilometres (rounded to the nearest kilometre) between New York at longitude "
                "-74.006, latitude 40.7128 and London at longitude -0.1276, latitude 51.5074?"
            ),
            "reference_sql": ("SELECT round(proj.main.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074) / 1000)"),
            "success_criteria": "Answers about 5585 kilometres.",
            "ignore_column_names": True,
        },
        {
            "name": "geodesic_bearing_deg",
            "prompt": (
                "What is the initial geodesic bearing in whole degrees (rounded to the "
                "nearest degree) from New York at longitude -74.006, latitude 40.7128 toward "
                "London at longitude -0.1276, latitude 51.5074?"
            ),
            "reference_sql": ("SELECT round(proj.main.geodesic_bearing(-74.006, 40.7128, -0.1276, 51.5074))"),
            "success_criteria": "Answers about 51 degrees (initial forward azimuth).",
            "ignore_column_names": True,
        },
        {
            "name": "to_web_mercator_x",
            "prompt": (
                "Convert the WGS84 point at longitude -122.4194, latitude 37.7749 into Web "
                "Mercator (EPSG:3857) and give its x coordinate in metres, rounded to the "
                "nearest metre."
            ),
            "reference_sql": "SELECT round(proj.main.to_webmercator(-122.4194, 37.7749).x)",
            "success_criteria": "Answers about -13627665 metres for the x coordinate.",
            "ignore_column_names": True,
        },
    ]
)

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
        "# Coordinate Reference System Transforms & Geodesic Distance in SQL\n\n"
        "![PROJ logo](https://raw.githubusercontent.com/OSGeo/PROJ/master/docs/images/logo.png)\n\n"
        "**Reproject coordinates between any two CRSs and measure accurate ellipsoidal "
        "distance and bearing directly in DuckDB SQL** -- EPSG transforms, UTM and Web "
        "Mercator projection, and WGS84 geodesic geometry, powered by "
        "[PROJ](https://proj.org/) through [pyproj](https://pyproj4.github.io/pyproj/stable/).\n\n"
        "The `proj` extension brings industry-standard geospatial coordinate math into your "
        "queries, so you never have to export points to a GIS tool or a Python notebook just "
        "to change projection or compute a great-circle distance. It is built for analysts, "
        "data engineers, and mapping developers who work with longitude/latitude points, "
        "EPSG-coded geometries, tile coordinates, or survey data and need correct, "
        "repeatable coordinate transformations next to the rest of their SQL. Every "
        "transformation is identified by a standard EPSG code or CRS string (for example "
        "`EPSG:4326` for WGS84 longitude/latitude and `EPSG:3857` for Web Mercator), so "
        "results match what PostGIS, QGIS, and other PROJ-based tools produce.\n\n"
        "Under the hood the extension calls [pyproj]"
        "(https://github.com/pyproj4/pyproj), the Python binding for the "
        "[PROJ](https://github.com/OSGeo/PROJ) C library that is the de-facto standard for "
        "cartographic projections and datum transformations. The pyproj binary wheel bundles "
        "PROJ together with its data grids, so there is no separate native install and no "
        "external services or API keys -- the worker is fully offline and hermetic. Building "
        "a PROJ transformer is comparatively expensive, so transformers are cached and the "
        "common WGS84 <-> Web Mercator pipelines are warmed at startup, keeping per-row "
        "transforms fast. All coordinate input and output uses `always_xy` axis order "
        "`(x/easting/longitude, y/northing/latitude)` regardless of a CRS's declared native "
        "axis order, eliminating the classic lon/lat swap.\n\n"
        "## Capabilities\n\n"
        "The worker's functions fall into a few capability areas: general CRS-to-CRS "
        "reprojection identified by EPSG code; convenience conversions to and from the Web "
        "Mercator tile projection and into a point's automatically selected UTM zone; "
        "accurate WGS84-ellipsoid geodesic distance and initial bearing between two "
        "longitude/latitude points; and lookups of a CRS's human-readable name and axis "
        "units alongside the bundled PROJ library version. Reprojection results come back "
        "as typed STRUCT coordinates, while the geodesic and metadata functions return "
        "plain metres, degrees, or strings. List the schema to discover the exact functions "
        "and their signatures.\n\n"
        "NULL or non-finite (and, where applicable, out-of-range) coordinates yield NULL, "
        "while an unknown CRS raises a clear query error."
    ),
    "vgi.author": "Query.Farm",
    "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
    "vgi.license": "MIT",
    "vgi.support_contact": f"{_REPO_URL}/issues",
    "vgi.support_policy_url": f"{_REPO_URL}/blob/main/README.md",
    # VGI152/VGI920: fixed agent-suitability task suite (see _AGENT_TEST_TASKS).
    "vgi.agent_test_tasks": _AGENT_TEST_TASKS,
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
    # VGI413/VGI410: ordered category registry; each function names one via vgi.category.
    "vgi.categories": _SCHEMA_CATEGORIES,
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
        "## Capabilities\n\n"
        "Reproject coordinates between CRSs identified by EPSG code -- including shorthands "
        "for the Web Mercator tile projection and a point's auto-selected UTM zone -- "
        "returning typed STRUCT outputs. Measure accurate ellipsoidal (WGS84) geodesic "
        "distance in metres and initial bearing in degrees between two longitude/latitude "
        "points. Look up CRS metadata such as a system's display name and axis units, plus "
        "the bundled PROJ library version. List the schema to see each function and its "
        "signature.\n\n"
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
