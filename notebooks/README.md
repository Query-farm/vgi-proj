# vgi-proj notebooks

<a href="https://query.farm/"><img src="assets/query-farm-strata-sun.svg" alt="Query.Farm — Strata Sun" width="252"></a>

<a href="https://query.farm/vgi/"><img src="assets/vgi-logo.png" alt="VGI — Vector Gateway Interface" width="280"></a>

[Query.Farm](https://query.farm/) · [VGI](https://query.farm/vgi/) · [Haybarn](https://query.farm/products/haybarn/)

| Notebook | Investigation |
|---|---|
| [01 · When is a 10 km map ruler really 10 km?](01_when_a_map_ruler_lies.ipynb) | Measure identical Web Mercator segments at different latitudes, test the spherical scale approximation, and compare a suitable local UTM projection. |

The saved notebook includes executed SQL tables, three charts and result-derived
interpretations. Visible JupySQL connects Haybarn to the real Python worker
with `INSTALL`, `LOAD` and `ATTACH`. SQL creates the synthetic coordinates, calls
the worker, measures distances and computes the comparisons. Folded Python
handles configuration, presentation and validation assertions.

## Install and run

Prerequisites: `uv` and Git on PATH. Copy or download the complete **notebooks/**
directory and run from that directory:

```sh
uv sync --locked
uv run --locked python execute.py
```

The separate notebook environment pins published Haybarn **2.0.0rc11** and locks
its plotting/Jupyter dependencies for Python 3.13–3.14. Select `.venv/bin/python`
in a Jupyter-compatible editor, then **Restart Kernel → Run All**. No global
kernel registration or worker checkout is required.

The literal visible ATTACH installs GitHub release **v0.4.0** through `uvx` and
pins **pyproj 3.8.0** plus **vgi-python 0.51.0**. This release supplies Python
wheel/source assets rather than native platform archives, so `github-auto` is
not used. The wheel bundles PROJ and data; no engine or native PROJ build is
needed. First use downloads the runtime, dependencies and VGI extension; all
coordinate calculations are then local and deterministic.

To upgrade, change the Git release and compatible package pins in the ATTACH
cell, execute the default and latitude experiment, and review numerical checks.
The runner always uses this notebook directory as the kernel working directory.

Python setup, plot implementation, interpretation source and cleanup have
portable Jupyter folding metadata and tags. Parameters and SQL remain expanded;
charts and interpretations remain visible. Editors can retain separate folding
state, so collapse the helper inputs if an editor ignores these preferences.

## One-parameter experiment

Change only `FOCUS_LATITUDE_DEG` from **45.0** to **60.0**. Predict a shorter
geodesic distance for the unchanged map ruler, a larger Web Mercator / geodesic
ratio, and a UTM distance that still remains close to the geodesic. Save a
separate executed copy:

```sh
uv run --locked python execute.py \
  --parameters '{"FOCUS_LATITUDE_DEG":60.0}' \
  --output-dir artifacts/latitude-60
```

Compare the focus summary and final chart; the background latitude sweep should
remain unchanged. Reset `FOCUS_LATITUDE_DEG = 45.0` for the saved default.
Accepted latitude is **0–75° north**, inclusive. `MAP_LENGTH_M` defaults to
**10000.0 metres**, accepts **100–50000 metres**, and stays fixed in this
experiment. Both parameters must be finite numbers, not booleans. The segment
midpoint longitude is fixed at **−123°**, the central meridian of UTM zone 10N,
to keep the local projection comparison controlled.

The runner rejects unknown parameter names and requires a distinct destination
for overrides. It executes in a fresh kernel and atomically replaces a notebook
only after success, preserving the last successful default on failure. Experiment
directories include the relative branding, this guide and helper/environment
companions. To rerun a copy, use the same installation and execution commands
from that copy's directory.
Logs, experiments, extracted review charts, caches and environments are ignored;
the main notebook retains embedded outputs.

## Inputs and limits

All inputs are explicitly **synthetic endpoint pairs**, not surveyed routes or
observations. SQL places east–west segments of equal Web Mercator grid length at
latitudes 0–75° in 1° steps, plus the chosen focus latitude. A smaller set of
landmarks is plotted for readability. There is no live/replay mode, acquisition
timestamp or observation window for these deterministic calculations.

All geographic function arguments use **longitude, latitude in degrees**.
Projected coordinates use **x/easting, y/northing in metres**. The distance
reference is the shortest WGS84 ellipsoidal geodesic between the endpoints,
without terrain, elevation, road or walking-route effects. It is not the arc
length of a constant-latitude parallel. The spherical `1 / cos(latitude)` scale
curve is a useful local approximation; the worker's ellipsoidal measurement
remains the reference, and the notebook displays their discrepancy.

UTM comparisons are limited to these short segments near the central meridian.
Both endpoints' returned EPSG identifiers must agree before their eastings and
northings are subtracted; an incompatible pair stays NULL. The worker selects
zones with its documented longitude-based rule. This walkthrough does not
evaluate special zone exceptions or recommend a single UTM zone for global
data. A general `transform` call requires constant CRS strings.

The explanation references the primary PROJ documentation for
[Web Mercator](https://proj.org/en/stable/operations/projections/webmerc.html) and
[UTM](https://proj.org/en/stable/operations/projections/utm.html). Worker formulas
and coordinate semantics are in `vgi_proj/projection.py` and `vgi_proj/scalars.py`
in the original checkout.

Relationship checks cover completeness, unique latitudes, grid-length recovery,
CRS agreement, round trips, geodesic symmetry, direct-transform agreement,
positive distances and correct NULL handling. They do not require a particular
rounded distance or distortion at the focus latitude.

Repository checks from `CLAUDE.md` / `Makefile` remain:

```sh
uv run --locked pytest -q
make test-sql
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy vgi_proj/
```

`make test-sql` uses the supported published `haybarn-unittest` runner (verified
with **2.0.0rc11**). It exercises the real `ATTACH` and `SELECT` path; passing
pure Python tests alone does not establish VGI interoperability.

Brand assets are unchanged copies of `vgi-perps/notebooks/assets/`. The official
Strata Sun + Query.Farm wordmark originates from the website media kit's
`logo/wordmark-adaptive.svg`; the VGI logo originates from `products/vgi/`.

---

<a href="https://query.farm/"><img src="assets/query-farm-strata-sun.svg" alt="Query.Farm — Strata Sun" width="210"></a>
