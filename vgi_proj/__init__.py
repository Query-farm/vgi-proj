"""Coordinate-reference-system (CRS) transforms + geodesic distance as a VGI worker.

The implementation is split so each concern stays focused:

- ``projection`` -- pure CRS-transform and geodesic logic over ``pyproj``
  (which wraps PROJ; the wheel bundles PROJ and its data, no separate native
  install). No Arrow or VGI dependency, directly unit-testable. Building a
  ``pyproj.Transformer`` is expensive, so transformers are cached by
  ``(from_crs, to_crs)`` for the process lifetime, and every transformer is
  built with ``always_xy=True`` (axis order is always (x/easting/lon,
  y/northing/lat)).
- ``scalars`` -- per-row VGI scalar functions; the coordinate-pair returns
  (``transform``, ``to_utm``, ``to_webmercator``, ``from_webmercator``) return
  a STRUCT via an explicit ``Returns(arrow_type=...)``.

``proj_worker.py`` at the repo root assembles these into the ``proj`` catalog
and runs the worker over stdio (or HTTP), warming the common transformers at
spawn.
"""

from __future__ import annotations

__version__ = "0.1.0"
