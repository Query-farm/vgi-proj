"""Pure CRS-transformation and geodesic logic -- no Arrow, no VGI.

Everything here wraps `pyproj` (which itself wraps the PROJ C library; the
`pyproj` wheel **bundles PROJ and its data grids**, so there is no separate
native install). Two concerns live here:

- **CRS -> CRS transforms** via `pyproj.Transformer`. Building a Transformer is
  *expensive* (it resolves CRS definitions and pipeline operations through
  PROJ), so every Transformer is **cached** keyed by `(from_crs, to_crs)` for
  the process lifetime -- see `_get_transformer`. Reuse is essential for
  per-row throughput.
- **Geodesic distance / bearing** on the WGS84 ellipsoid via `pyproj.Geod`
  (a single cached instance).

Axis order (`always_xy=True`)
-----------------------------
PROJ's native axis order is CRS-defined and, for geographic CRSs like
EPSG:4326, is *latitude, longitude* -- the opposite of the (x, y) = (lon, lat)
convention almost every GIS user expects. Every Transformer here is built with
``always_xy=True`` so inputs and outputs are **always (x/easting/longitude,
y/northing/latitude)** regardless of the CRS's declared axis order. This is the
single most important correctness knob in the module.

Robustness
----------
- A NULL or non-finite coordinate yields ``None`` (the scalar layer maps that to
  SQL ``NULL``); the transform is never attempted.
- An **unknown / unparseable CRS** raises ``UnknownCRSError`` (a clear message),
  surfaced to DuckDB as a query error -- the worker never crashes.
"""

from __future__ import annotations

import math
import threading
from functools import lru_cache
from typing import Any, NamedTuple

# pyproj is imported lazily inside the cache builders so that merely importing
# this module is cheap and import errors surface at first use with context.

_GEOD: Any = None
_GEOD_LOCK = threading.Lock()


class UnknownCRSError(ValueError):
    """Raised when a CRS identifier cannot be parsed/resolved by PROJ."""


class XY(NamedTuple):
    """A transformed coordinate pair in the target CRS's (x, y) axis order."""

    x: float
    y: float


class UTMResult(NamedTuple):
    """A point projected into its auto-selected UTM zone."""

    easting: float
    northing: float
    zone: int
    hemisphere: str  # 'N' or 'S'


def _finite(v: float | None) -> bool:
    """True iff ``v`` is present and a finite real number."""
    if v is None:
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return not (math.isnan(f) or math.isinf(f))


# ---------------------------------------------------------------------------
# CRS resolution + Transformer cache (the expensive part -- cache aggressively).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _resolve_crs(crs: str) -> Any:
    """Parse a CRS identifier into a ``pyproj.CRS``; raise ``UnknownCRSError``.

    Cached: parsing a CRS definition is non-trivial and the same handful of
    identifiers recur across every row.
    """
    from pyproj import CRS
    from pyproj.exceptions import CRSError

    try:
        return CRS.from_user_input(crs)
    except CRSError as exc:
        raise UnknownCRSError(f"unknown or invalid CRS {crs!r}: {exc}") from exc


@lru_cache(maxsize=512)
def _get_transformer(from_crs: str, to_crs: str) -> Any:
    """Return a cached ``Transformer`` for ``from_crs -> to_crs``.

    Building a Transformer is the dominant per-call cost; this cache is what
    makes per-row transforms viable. ``always_xy=True`` fixes the axis order to
    (x/easting/lon, y/northing/lat). Unknown CRSs raise ``UnknownCRSError``.
    """
    from pyproj import Transformer
    from pyproj.exceptions import CRSError

    # Resolve each side first so an unknown CRS gives a precise message.
    src = _resolve_crs(from_crs)
    dst = _resolve_crs(to_crs)
    try:
        return Transformer.from_crs(src, dst, always_xy=True)
    except CRSError as exc:  # pragma: no cover - defensive
        raise UnknownCRSError(f"cannot build transform {from_crs!r} -> {to_crs!r}: {exc}") from exc


def _get_geod() -> Any:
    """Return a cached WGS84 ``Geod`` instance (built once)."""
    global _GEOD
    if _GEOD is None:
        with _GEOD_LOCK:
            if _GEOD is None:
                from pyproj import Geod

                _GEOD = Geod(ellps="WGS84")
    return _GEOD


def warm_up() -> None:
    """Pre-build the common transformers + the Geod so the first query is fast.

    Best-effort; never fatal. Warms WGS84<->WebMercator (the most common
    conversions) and the geodesic engine ahead of any query so an ATTACH's
    first call doesn't pay the build cost inline.
    """
    try:
        _get_transformer("EPSG:4326", "EPSG:3857")
        _get_transformer("EPSG:3857", "EPSG:4326")
        _get_geod()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# General transform.
# ---------------------------------------------------------------------------


def transform(
    x: float | None,
    y: float | None,
    from_crs: str,
    to_crs: str,
) -> XY | None:
    """Transform ``(x, y)`` from ``from_crs`` to ``to_crs`` (always_xy order).

    Returns ``None`` if either coordinate is NULL/non-finite. Raises
    ``UnknownCRSError`` for an unknown CRS. A transform that produces a
    non-finite result (e.g. a point outside the target CRS's valid area) yields
    ``None``.
    """
    if not (_finite(x) and _finite(y)):
        return None
    tf = _get_transformer(from_crs, to_crs)
    ox, oy = tf.transform(float(x), float(y))  # type: ignore[arg-type]
    if not (_finite(ox) and _finite(oy)):
        return None
    return XY(ox, oy)


def to_webmercator(lon: float | None, lat: float | None) -> XY | None:
    """WGS84 (lon, lat) -> Web Mercator (x, y) in metres (EPSG:4326 -> 3857)."""
    return transform(lon, lat, "EPSG:4326", "EPSG:3857")


def from_webmercator(x: float | None, y: float | None) -> XY | None:
    """Web Mercator (x, y) metres -> WGS84 (lon, lat) (EPSG:3857 -> 4326)."""
    return transform(x, y, "EPSG:3857", "EPSG:4326")


# ---------------------------------------------------------------------------
# UTM (auto-pick the zone for the point).
# ---------------------------------------------------------------------------


def to_utm(lon: float | None, lat: float | None) -> UTMResult | None:
    """Project WGS84 ``(lon, lat)`` into its auto-selected UTM zone.

    The zone is the standard 6-degree band ``floor((lon+180)/6)+1``; the
    hemisphere is ``'N'`` for ``lat >= 0`` else ``'S'``. Returns ``None`` for
    NULL/non-finite or out-of-range coordinates (``|lat| > 90`` /
    ``|lon| > 180``).
    """
    if not (_finite(lon) and _finite(lat)):
        return None
    flon = float(lon)  # type: ignore[arg-type]
    flat = float(lat)  # type: ignore[arg-type]
    if not (-90.0 <= flat <= 90.0 and -180.0 <= flon <= 180.0):
        return None

    zone = int((flon + 180.0) // 6.0) + 1
    zone = min(max(zone, 1), 60)
    hemisphere = "N" if flat >= 0.0 else "S"
    # WGS84 UTM EPSG codes: northern 326xx, southern 327xx.
    epsg = (32600 if hemisphere == "N" else 32700) + zone
    tf = _get_transformer("EPSG:4326", f"EPSG:{epsg}")
    easting, northing = tf.transform(flon, flat)
    if not (_finite(easting) and _finite(northing)):
        return None
    return UTMResult(easting, northing, zone, hemisphere)


# ---------------------------------------------------------------------------
# Geodesic distance / bearing (WGS84 ellipsoid).
# ---------------------------------------------------------------------------


def geodesic_distance(
    lon1: float | None,
    lat1: float | None,
    lon2: float | None,
    lat2: float | None,
) -> float | None:
    """Ellipsoidal (WGS84) geodesic distance between two points in **metres**.

    Returns ``None`` if any coordinate is NULL/non-finite or out of range.
    """
    pts = _checked_pair(lon1, lat1, lon2, lat2)
    if pts is None:
        return None
    a, b, c, d = pts
    _az, _baz, dist = _get_geod().inv(a, b, c, d)
    return float(dist)


def geodesic_bearing(
    lon1: float | None,
    lat1: float | None,
    lon2: float | None,
    lat2: float | None,
) -> float | None:
    """Initial geodesic azimuth (forward bearing) from point 1 to point 2.

    Degrees clockwise from north, normalised to ``[0, 360)``. Returns ``None``
    for NULL/non-finite or out-of-range input.
    """
    pts = _checked_pair(lon1, lat1, lon2, lat2)
    if pts is None:
        return None
    a, b, c, d = pts
    az, _baz, _dist = _get_geod().inv(a, b, c, d)
    return float(az % 360.0)


def _checked_pair(
    lon1: float | None,
    lat1: float | None,
    lon2: float | None,
    lat2: float | None,
) -> tuple[float, float, float, float] | None:
    """Validate two lon/lat points; return floats or ``None`` if invalid."""
    if not all(_finite(v) for v in (lon1, lat1, lon2, lat2)):
        return None
    a, b, c, d = float(lon1), float(lat1), float(lon2), float(lat2)  # type: ignore[arg-type]
    if not (-90.0 <= b <= 90.0 and -90.0 <= d <= 90.0):
        return None
    if not (-180.0 <= a <= 180.0 and -180.0 <= c <= 180.0):
        return None
    return a, b, c, d


# ---------------------------------------------------------------------------
# CRS metadata.
# ---------------------------------------------------------------------------


def crs_units(crs: str | None) -> str | None:
    """Unit name of a CRS's first axis (e.g. ``'degree'``, ``'metre'``).

    ``None`` if ``crs`` is NULL; raises ``UnknownCRSError`` for an unknown CRS.
    """
    if crs is None:
        return None
    obj = _resolve_crs(crs)
    axes = obj.axis_info
    if not axes:
        return None
    return axes[0].unit_name or None


def crs_name(crs: str | None) -> str | None:
    """Human-readable name of a CRS (e.g. ``'WGS 84'``).

    ``None`` if ``crs`` is NULL; raises ``UnknownCRSError`` for an unknown CRS.
    """
    if crs is None:
        return None
    return _resolve_crs(crs).name or None


def proj_version() -> str:
    """Version string of the underlying PROJ library (bundled in the wheel)."""
    import pyproj

    return str(pyproj.proj_version_str)
