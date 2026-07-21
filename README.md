<p align="center">
  <img src="https://raw.githubusercontent.com/Query-farm/vgi/main/docs/vgi-logo.png" alt="Vector Gateway Interface (VGI)" width="320">
</p>

<p align="center"><em>A <a href="https://query.farm">Query.Farm</a> VGI worker for DuckDB.</em></p>

# CRS Transforms & Geodesic Distance in DuckDB

> **vgi-proj** · a [Query.Farm](https://query.farm) VGI worker · powered by pyproj/PROJ

[![CI](https://github.com/Query-farm/vgi-proj/actions/workflows/ci.yml/badge.svg)](https://github.com/Query-farm/vgi-proj/actions/workflows/ci.yml)

A [VGI](https://query.farm) worker that brings **coordinate-reference-system
(CRS) transformations** and **accurate ellipsoidal geodesic distance** into
DuckDB/SQL. Give it coordinates and it reprojects them between any two CRSs,
projects lon/lat into the right UTM zone, converts to/from Web Mercator, and
measures true WGS84 geodesic distance and bearing — as plain SQL scalar
functions. It is backed by [`pyproj`](https://pypi.org/project/pyproj/)
(**MIT**), which wraps the **PROJ** C library; the `pyproj` wheel **bundles PROJ
and its data grids**, so there is **no separate native install**.

```sql
INSTALL vgi FROM community; LOAD vgi;
ATTACH 'proj' (TYPE vgi, LOCATION 'uv run proj_worker.py');

SELECT proj.transform(-122.42, 37.77, 'EPSG:4326', 'EPSG:3857'); -- STRUCT(x, y)
SELECT proj.to_webmercator(-122.4194, 37.7749);                  -- STRUCT(x, y) metres
SELECT proj.from_webmercator(-13627665, 4547675);                -- STRUCT(lon, lat)
SELECT proj.to_utm(-122.42, 37.77);                              -- STRUCT(easting, northing, zone, hemisphere, epsg)
SELECT proj.to_utm(-122.42, 37.77).zone;                         -- 10
SELECT proj.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074); -- ~5,585,000 m (NYC -> London)
SELECT proj.geodesic_bearing(-74.006, 40.7128, -0.1276, 51.5074);  -- ~51 deg
SELECT proj.crs_name('EPSG:4326');                               -- 'WGS 84'
SELECT proj.crs_units('EPSG:3857');                              -- 'metre'
```

The bundled PROJ and `pyproj` versions are exposed as catalog metadata (the
`proj_library_version` / `pyproj_version` tags on the `proj` catalog), readable
via `vgi_catalogs()` without running a query.

## Axis order: always (x, y) = (lon/easting, lat/northing)

PROJ's native axis order is CRS-defined and, for geographic CRSs like EPSG:4326,
is **latitude, longitude** — the opposite of what most GIS users expect. Every
transform here is built with **`always_xy=True`**, so **inputs and outputs are
always `(x/easting/longitude, y/northing/latitude)`** regardless of the CRS's
declared axis order. So `transform(-122.42, 37.77, 'EPSG:4326', 'EPSG:3857')`
takes `(lon, lat)` and returns `(x, y)`; `to_webmercator(lon, lat)` and
`to_utm(lon, lat)` take longitude first.

## Performance: Transformers are built once and cached

Building a `pyproj.Transformer` is **expensive** (PROJ resolves the CRS
definitions and the operation pipeline). This worker **caches every Transformer
keyed by `(from_crs, to_crs)`** for the process lifetime (an `lru_cache`), so a
per-row transform over a column reuses one Transformer instead of rebuilding it
each row. The common WGS84↔WebMercator transformers and the geodesic engine are
**warmed at worker spawn** so the first query of an `ATTACH` never pays the build
cost inline.

## Scalars only (per-row), positional arguments

Every answer here is per-row, so all functions are **scalars**. VGI/DuckDB
scalar functions take **positional** arguments (`name := value` is a
table-function/macro feature). None of these functions have optional arguments,
so there are no arity overloads — each is a single function.

**NULL / out-of-range semantics.** A NULL or non-finite coordinate — or, for
`to_utm` / `geodesic_*`, one out of range (`|lat| > 90` or `|lon| > 180`) —
yields **NULL** (never an error). For struct-returning functions the whole
STRUCT is NULL.

**Unknown CRS → error.** An unparseable or unknown CRS identifier raises a clear
error (surfaced as a DuckDB query error, e.g. `unknown or invalid CRS 'NOPE'`);
the worker never crashes.

## Function catalog

| Function | Form | Signature | Returns |
| --- | --- | --- | --- |
| `transform` | scalar | `(x, y, from_crs, to_crs)` | `STRUCT(x DOUBLE, y DOUBLE)` |
| `to_utm` | scalar | `(lon, lat)` | `STRUCT(easting DOUBLE, northing DOUBLE, zone INT, hemisphere VARCHAR, epsg INT)` |
| `to_webmercator` | scalar | `(lon, lat)` | `STRUCT(x DOUBLE, y DOUBLE)` — metres |
| `from_webmercator` | scalar | `(x, y)` | `STRUCT(lon DOUBLE, lat DOUBLE)` |
| `geodesic_distance` | scalar | `(lon1, lat1, lon2, lat2)` | `DOUBLE` — metres (WGS84 geodesic) |
| `geodesic_bearing` | scalar | `(lon1, lat1, lon2, lat2)` | `DOUBLE` — initial azimuth, degrees [0,360) |
| `crs_units` | scalar | `(crs)` | `VARCHAR` — first-axis unit (e.g. `'degree'`, `'metre'`) |
| `crs_name` | scalar | `(crs)` | `VARCHAR` — human-readable CRS name |

CRS identifiers are EPSG codes/strings, e.g. `'EPSG:4326'` (WGS84) or
`'EPSG:3857'` (Web Mercator). All coordinates are `DOUBLE`. STRUCT returns are
declared with explicit Arrow types (the SDK cannot infer a struct schema).

### Transform & projection

`transform(x, y, from_crs, to_crs)` is the general case; `to_webmercator` /
`from_webmercator` are shorthands for the EPSG:4326↔3857 pair, and `to_utm`
auto-picks the 6° UTM zone for the point (`floor((lon+180)/6)+1`) and the
hemisphere from the sign of latitude, returning the right WGS84 UTM CRS's
easting/northing:

```sql
SELECT t.x, t.y FROM (SELECT proj.to_webmercator(lon, lat) AS t FROM points);
SELECT u.zone, u.hemisphere FROM (SELECT proj.to_utm(lon, lat) AS u FROM points);
```

### Geodesic distance & bearing

`geodesic_distance` returns the **accurate ellipsoidal (WGS84) geodesic
distance** in **metres** — not a spherical haversine approximation — via
`pyproj.Geod`. `geodesic_bearing` returns the initial forward azimuth in degrees:

```sql
SELECT proj.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074);  -- ~5,585,000 m
```

## Dependencies & licensing

| Component | License | Notes |
| --- | --- | --- |
| `vgi-proj` (this worker) | **MIT** | This repository's own code. |
| [`pyproj`](https://pypi.org/project/pyproj/) | **MIT** | Python wrapper around the PROJ C library. |
| [PROJ](https://proj.org/) | **MIT / X11** | Bundled inside the `pyproj` wheel (library + data grids); no separate install. |
| [`pyarrow`](https://pypi.org/project/pyarrow/) | **Apache-2.0** | Arrow interchange. |
| [`vgi-python`](https://github.com/Query-farm/vgi-python) | Query Farm Source-Available | The VGI SDK. |

`pyproj` and PROJ are both permissively licensed (MIT / X11-style), so
`vgi-proj`'s own MIT code is fine for commercial use with no copyleft caveat.
PROJ and its data are bundled inside the `pyproj` binary wheel — the bundled
versions are surfaced as the `proj_library_version` / `pyproj_version` catalog
tags (readable from `vgi_catalogs()`).

## Local development

```sh
uv sync --extra dev      # create .venv with vgi-python + pyproj + dev tools
make test                # pytest (unit + integration) + SQL end-to-end
make test-unit           # pytest only
make test-sql            # DuckDB sqllogictest files via haybarn-unittest
uv run ruff check .      # lint
uv run mypy vgi_proj/
```

`tests/test_projection.py` covers the pure transform/geodesic logic (known
values with tolerance, round-trips, UTM zone selection, NULL/out-of-range edges,
unknown-CRS errors, Transformer caching); `tests/test_scalars.py` spawns
`proj_worker.py` over the VGI client/RPC stack exactly as DuckDB would after
`ATTACH`. The `test/sql/*.test` files are DuckDB sqllogictest cases run by
[`haybarn-unittest`](https://pypi.org/project/haybarn-unittest/)
(`uv tool install haybarn-unittest`) against a real `ATTACH` + `SELECT`.

## Layout

```
proj_worker.py           entry point; assembles the `proj` catalog (inline uv script metadata);
                         warms the common Transformers at spawn via run()
Makefile                 test / test-unit / test-sql targets
vgi_proj/
  projection.py          pure pyproj/PROJ transform + geodesic logic (no Arrow/VGI); cached Transformers
  scalars.py             per-row scalars; transform/to_utm/to_webmercator/from_webmercator return STRUCTs
  schema_utils.py        Arrow field/comment helper
tests/
  test_projection.py     pure-logic unit + edge tests
  test_scalars.py        per-row scalar lifecycle via vgi.client.Client
test/sql/
  *.test                 DuckDB sqllogictest end-to-end cases (haybarn-unittest)
```

---

## Authorship & License

Written by [Query.Farm](https://query.farm).

Copyright 2026 Query Farm LLC - https://query.farm

