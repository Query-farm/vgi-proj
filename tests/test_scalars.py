"""End-to-end tests for the per-row scalar proj functions.

These spawn ``proj_worker.py`` as a subprocess via ``vgi.client.Client`` and
call each scalar exactly as DuckDB would after ``ATTACH``. Coordinate columns
travel in the input batch (``Param`` arguments); the CRS strings on
``transform`` are constant arguments, so they go in ``positional``.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pytest
from vgi import Arguments
from vgi.client import Client

_WORKER = str(Path(__file__).resolve().parent.parent / "proj_worker.py")


@pytest.fixture(scope="module")
def client() -> Iterator[Client]:
    # Current interpreter (deps already installed) + worker_limit=1 so output
    # order matches input order for deterministic per-row assertions.
    with Client(f"{sys.executable} {_WORKER}", worker_limit=1) as c:
        yield c


def _call(
    client: Client,
    name: str,
    cols: dict[str, list],
    *,
    positional: list[pa.Scalar] | None = None,
) -> list:
    batch = pa.RecordBatch.from_pydict({k: pa.array(v, type=pa.float64()) for k, v in cols.items()})
    results = list(
        client.scalar_function(
            function_name=name,
            input=iter([batch]),
            arguments=Arguments(positional=positional or []),
        )
    )
    return results[0]["result"].to_pylist()


def _call_str(client: Client, name: str, values: list[str | None]) -> list:
    batch = pa.RecordBatch.from_pydict({"c": pa.array(values, type=pa.string())})
    results = list(
        client.scalar_function(
            function_name=name,
            input=iter([batch]),
            arguments=Arguments(positional=[]),
        )
    )
    return results[0]["result"].to_pylist()


class TestTransform:
    def test_webmercator_struct(self, client: Client) -> None:
        out = _call(
            client,
            "transform",
            {"x": [-122.42], "y": [37.77]},
            positional=[pa.scalar("EPSG:4326"), pa.scalar("EPSG:3857")],
        )
        rec = out[0]
        assert math.isclose(rec["x"], -13627732.06, abs_tol=50.0)
        assert math.isclose(rec["y"], 4546985.28, abs_tol=50.0)

    def test_matches_to_webmercator(self, client: Client) -> None:
        a = _call(
            client,
            "transform",
            {"x": [-122.42], "y": [37.77]},
            positional=[pa.scalar("EPSG:4326"), pa.scalar("EPSG:3857")],
        )[0]
        b = _call(client, "to_webmercator", {"lon": [-122.42], "lat": [37.77]})[0]
        assert math.isclose(a["x"], b["x"], abs_tol=1e-6)
        assert math.isclose(a["y"], b["y"], abs_tol=1e-6)

    def test_null(self, client: Client) -> None:
        out = _call(
            client,
            "transform",
            {"x": [None], "y": [37.77]},
            positional=[pa.scalar("EPSG:4326"), pa.scalar("EPSG:3857")],
        )
        assert out[0] is None

    def test_unknown_crs_errors(self, client: Client) -> None:
        from vgi.client import ClientError

        with pytest.raises(ClientError):
            _call(
                client,
                "transform",
                {"x": [0.0], "y": [0.0]},
                positional=[pa.scalar("EPSG:4326"), pa.scalar("NOT_A_CRS")],
            )


class TestWebMercator:
    def test_origin(self, client: Client) -> None:
        out = _call(client, "to_webmercator", {"lon": [0.0], "lat": [0.0]})
        rec = out[0]
        assert math.isclose(rec["x"], 0.0, abs_tol=1e-6)
        assert math.isclose(rec["y"], 0.0, abs_tol=1e-6)

    def test_san_francisco(self, client: Client) -> None:
        out = _call(client, "to_webmercator", {"lon": [-122.4194], "lat": [37.7749]})
        rec = out[0]
        assert math.isclose(rec["x"], -13627665.27, abs_tol=50.0)
        assert math.isclose(rec["y"], 4547675.35, abs_tol=50.0)

    def test_round_trip(self, client: Client) -> None:
        fwd = _call(client, "to_webmercator", {"lon": [-122.4194], "lat": [37.7749]})[0]
        back = _call(client, "from_webmercator", {"x": [fwd["x"]], "y": [fwd["y"]]})[0]
        assert math.isclose(back["lon"], -122.4194, abs_tol=1e-6)
        assert math.isclose(back["lat"], 37.7749, abs_tol=1e-6)


class TestUTM:
    def test_san_francisco(self, client: Client) -> None:
        out = _call(client, "to_utm", {"lon": [-122.42], "lat": [37.77]})
        rec = out[0]
        assert rec["zone"] == 10
        assert rec["hemisphere"] == "N"
        assert rec["epsg"] == 32610
        assert math.isclose(rec["easting"], 551081.30, abs_tol=1.0)

    def test_null(self, client: Client) -> None:
        out = _call(client, "to_utm", {"lon": [None], "lat": [37.77]})
        assert out[0] is None


class TestGeodesic:
    def test_nyc_to_london(self, client: Client) -> None:
        out = _call(
            client,
            "geodesic_distance",
            {"a": [-74.006], "b": [40.7128], "c": [-0.1276], "d": [51.5074]},
        )
        assert out[0] is not None
        assert math.isclose(out[0], 5585000.0, abs_tol=5000.0)

    def test_bearing(self, client: Client) -> None:
        out = _call(
            client,
            "geodesic_bearing",
            {"a": [-74.006], "b": [40.7128], "c": [-0.1276], "d": [51.5074]},
        )
        assert out[0] is not None
        assert 0.0 <= out[0] < 360.0

    def test_null(self, client: Client) -> None:
        out = _call(
            client,
            "geodesic_distance",
            {"a": [None], "b": [40.7128], "c": [-0.1276], "d": [51.5074]},
        )
        assert out[0] is None


class TestCrsMetadata:
    def test_crs_name(self, client: Client) -> None:
        assert _call_str(client, "crs_name", ["EPSG:4326"]) == ["WGS 84"]

    def test_crs_units(self, client: Client) -> None:
        assert _call_str(client, "crs_units", ["EPSG:3857"]) == ["metre"]

    def test_unknown_crs_errors(self, client: Client) -> None:
        from vgi.client import ClientError

        with pytest.raises(ClientError):
            _call_str(client, "crs_name", ["NOT_A_CRS"])
