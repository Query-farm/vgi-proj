# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python>=0.8.3",
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
from vgi_proj.scalars import SCALAR_FUNCTIONS

_PROJ_CATALOG = Catalog(
    name="proj",
    default_schema="main",
    schemas=[
        Schema(
            name="main",
            comment="CRS transforms (always_xy) + WGS84 geodesic distance/bearing via pyproj/PROJ",
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
