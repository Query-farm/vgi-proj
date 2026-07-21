# CLAUDE.md — vgi-proj

Contributor/agent notes. User-facing docs live in `README.md`; this is the
"how it's built and where the sharp edges are" companion.

## What this is

A [VGI](https://query.farm) worker that does **CRS transformations** and
**accurate WGS84 geodesic distance/bearing** as DuckDB scalar functions, via
`pyproj` (which wraps the PROJ C library; the wheel **bundles PROJ + data**, no
separate native install). `vgi_proj/worker.py` assembles every function into one
`proj` catalog (single `main` schema); `proj_worker.py` at the repo root is a
thin PEP 723 shim that re-exports it so `uv run proj_worker.py` keeps working.
Sibling style/tooling to `vgi-geocode` / `vgi-conform`.

## Layout

```
proj_worker.py         repo-root PEP 723 shim; re-exports ProjWorker/main from vgi_proj.worker (keeps `uv run proj_worker.py`)
vgi_proj/
  worker.py            wheel-importable: assembles the `proj` catalog + ProjWorker (warms Transformers in run()) + main()
  projection.py        pure pyproj/PROJ transform + geodesic logic; no Arrow/VGI; unit-testable; cached Transformers
  scalars.py           per-row scalars; transform/to_utm/to_webmercator/from_webmercator return STRUCTs
  schema_utils.py      pa.Field comment / column-doc helper
bin/vgi-proj-worker    uv-run launch wrapper for a plain filesystem ATTACH LOCATION
Dockerfile             single image serving http (default) + stdio; `pip install .[serve]`
tests/                 pytest: test_projection (pure), test_scalars (Client RPC)
test/sql/*.test        haybarn-unittest sqllogictest — authoritative E2E
Makefile               test / test-unit / test-sql / lint
```

To add a function: implement the logic in `projection.py` (pure, total — returns
`None` for NULL/non-finite/out-of-range; raises `UnknownCRSError` for a bad CRS),
wrap it as a scalar in `scalars.py`, register it in `scalars.SCALAR_FUNCTIONS`
(the worker pulls that list).

## Scalars are positional-only — and STRUCT returns are explicit (read first)

- **All functions are scalars.** The VGI SDK makes scalar functions
  **positional-only** (`name := value` named args are a table-function/macro
  feature). None have optional args, so there are **no arity overloads** — each
  is one class. The CRS strings on `transform` are `ConstParam`s (constant-folded
  at plan time), passed via `Arguments.positional` in the Client tests.
- **`transform`, `to_utm`, `to_webmercator`, `from_webmercator` return STRUCTs,
  which REQUIRE an explicit `Returns(arrow_type=...)`.** The SDK cannot infer a
  struct schema. Each struct type is declared once as a module constant
  (`_XY_TYPE`, `_LONLAT_TYPE`, `_UTM_TYPE`) and reused in both the `compute`
  return annotation **and** `on_bind` (`BindResult(...)`). Wire both places.
- **The bundled PROJ / pyproj versions are catalog metadata, not a function.**
  They live as the `proj_library_version` / `pyproj_version` tags on the `proj`
  catalog (resolved once at import from the installed wheel), readable via
  `vgi_catalogs()` with no query. A parameterless `proj_version()` scalar would
  duplicate catalog metadata (vgi-lint VGI328), so it is intentionally not a
  function. `projection.proj_version()` / `pyproj_version()` remain as the pure
  helpers that populate those tags.

## Sharp edges (learned the hard way)

1. **`haybarn-unittest` skips `require vgi`.** Under haybarn the extension is not
   autoloaded for `require`, so a `.test` using `require vgi` is silently
   SKIPPED. Use an explicit `statement ok` / `LOAD vgi;` instead (every `.test`
   here already does).
2. **`always_xy=True` is the key correctness knob.** PROJ's native axis order for
   geographic CRSs (EPSG:4326) is *lat, lon* — the opposite of the (lon, lat) /
   (x, y) convention. Every Transformer is built with `always_xy=True` in
   `projection._get_transformer`, so all I/O is `(x/easting/lon, y/northing/lat)`.
   Don't remove it; it silently swaps every coordinate.
3. **Building a Transformer is expensive — cache it.** `_get_transformer` and
   `_resolve_crs` are `@lru_cache`'d by their string arguments, so a per-row
   transform reuses one Transformer. `warm_up()` pre-builds WGS84↔WebMercator +
   the `Geod`, and `ProjWorker.run()` calls it at spawn — so the first query of
   every ATTACH doesn't pay the build cost inline (the classic E2E flake window
   where a teardown SIGTERM kills a mid-build run). Don't build Transformers in
   `compute`.
4. **NULL vs out-of-range — both → NULL, never an error.** A NULL/non-finite
   coordinate yields NULL for every function (whole STRUCT NULL for struct
   returns). `to_utm`/`geodesic_*` additionally reject `|lat| > 90` / `|lon| >
   180`. Enforced in `projection._finite` / `_checked_pair`.
5. **Unknown CRS → clear error, never a crash.** `_resolve_crs` catches pyproj's
   `CRSError` and re-raises `UnknownCRSError` with a readable message; the scalar
   layer lets it propagate as a DuckDB query error. Tested in both suites
   (`statement error` in SQL, `pytest.raises` in unit).
6. **Known-value assertions use a tolerance.** PROJ snapshots can shift the last
   metres. SF Web Mercator ≈ (-13627665, 4547675) m (±50 m); NYC→London geodesic
   ≈ 5,585,000 m (±5 km); SF UTM is zone 10N, easting ≈ 551081 m. Keep new
   assertions tolerant; the geodesic distance is *ellipsoidal* (WGS84), so it
   differs from a spherical haversine by ~0.3%.
7. **The unit suite can pass while the RPC path is broken.** `test_projection.py`
   calls pure functions directly; only `test_scalars.py` (real
   `vgi.client.Client` subprocess) and `test/sql/*.test` (real `ATTACH`+`SELECT`)
   exercise the wire. **Run the SQL suite** — it's authoritative.

## pyproj / PROJ licensing (note)

`pyproj` is **MIT** and the **PROJ** C library it wraps is **MIT / X11**-style —
both permissive, no copyleft. PROJ and its data grids are **bundled inside the
`pyproj` binary wheel**, so there is no separate native install and no extra
licensing obligation; `vgi-proj`'s own code stays MIT and is fine for commercial
use. The bundled PROJ / pyproj versions are exposed as catalog metadata (the
`proj_library_version` / `pyproj_version` tags, verifying the bundling claim).
`pyarrow` is Apache-2.0.

## Testing

```sh
uv run pytest -q              # unit: pure logic + Client RPC scalars
make test-sql                 # E2E: haybarn-unittest over test/sql/*  (authoritative)
make test                     # both
uv run ruff check . && uv run mypy vgi_proj/
```

`make test-sql` sets `VGI_PROJ_WORKER` to the project venv interpreter running
the worker (`$(CURDIR)/.venv/bin/python $(CURDIR)/proj_worker.py`, absolute so it
survives haybarn's cd into a staging dir; the venv carries the locked SDK, unlike
`uv run <script>` whose PEP 723 env can cache a stale SDK), puts `~/.local/bin`
on PATH, and runs `haybarn-unittest --test-dir . "test/sql/*"`.
Install the runner once with `uv tool install haybarn-unittest`. CI
(`.github/workflows/ci.yml`) runs unit + lint + a gated `e2e` job.

Everything is pure/offline (no network, no API keys, no model downloads) — PROJ
and its data are bundled in the wheel — so the suite is fast and hermetic.
