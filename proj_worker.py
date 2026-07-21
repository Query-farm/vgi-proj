# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.16.0",
#     "pyproj>=3.6",
#     "pyarrow",
# ]
# ///
"""Repo-root entry point for the vgi-proj worker (stdio / HTTP).

The worker itself -- the ``proj`` catalog assembly, the :class:`ProjWorker`
class, and :func:`main` -- lives in the wheel-importable module
:mod:`vgi_proj.worker`. This file is a thin PEP 723 shim that re-exports them so
the historical invocation keeps working unchanged::

    uv run proj_worker.py             # serve over stdio (DuckDB subprocess)
    uv run proj_worker.py --http      # serve over HTTP (/health + VGI RPC)

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'proj' (TYPE vgi, LOCATION 'uv run proj_worker.py');

    SELECT proj.transform(-122.42, 37.77, 'EPSG:4326', 'EPSG:3857');

Installed builds get the same worker via the ``vgi-proj-worker`` console script
(``vgi_proj.worker:main``) or ``vgi-serve vgi_proj.worker:ProjWorker --http``.
"""

from __future__ import annotations

from vgi_proj.worker import ProjWorker, main

__all__ = ["ProjWorker", "main"]


if __name__ == "__main__":
    main()
