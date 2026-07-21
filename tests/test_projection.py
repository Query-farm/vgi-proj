"""Unit tests for the pure CRS-transform / geodesic logic (no Arrow / VGI).

These call ``vgi_proj.projection`` directly. Known-value assertions use a
tolerance because PROJ snapshots can shift the last metres; the reference
numbers below were captured from PROJ 9.x via pyproj 3.7 and are stable to well
within the tolerances used.
"""

from __future__ import annotations

import math

import pytest

from vgi_proj import projection
from vgi_proj.projection import UnknownCRSError


class TestWebMercator:
    def test_origin(self) -> None:
        r = projection.to_webmercator(0.0, 0.0)
        assert r is not None
        assert math.isclose(r.x, 0.0, abs_tol=1e-6)
        assert math.isclose(r.y, 0.0, abs_tol=1e-6)

    def test_san_francisco(self) -> None:
        # SF (-122.4194, 37.7749) -> Web Mercator metres.
        r = projection.to_webmercator(-122.4194, 37.7749)
        assert r is not None
        assert math.isclose(r.x, -13627665.27, abs_tol=50.0)
        assert math.isclose(r.y, 4547675.35, abs_tol=50.0)

    def test_round_trip(self) -> None:
        for lon, lat in [(-122.4194, 37.7749), (2.3522, 48.8566), (139.6917, 35.6895)]:
            fwd = projection.to_webmercator(lon, lat)
            assert fwd is not None
            back = projection.from_webmercator(fwd.x, fwd.y)
            assert back is not None
            assert math.isclose(back.x, lon, abs_tol=1e-6)
            assert math.isclose(back.y, lat, abs_tol=1e-6)

    def test_null(self) -> None:
        assert projection.to_webmercator(None, 37.0) is None
        assert projection.to_webmercator(-122.0, None) is None
        assert projection.from_webmercator(float("nan"), 0.0) is None


class TestTransform:
    def test_matches_to_webmercator(self) -> None:
        a = projection.transform(-122.42, 37.77, "EPSG:4326", "EPSG:3857")
        b = projection.to_webmercator(-122.42, 37.77)
        assert a is not None and b is not None
        assert math.isclose(a.x, b.x, abs_tol=1e-6)
        assert math.isclose(a.y, b.y, abs_tol=1e-6)

    def test_identity(self) -> None:
        r = projection.transform(10.0, 20.0, "EPSG:4326", "EPSG:4326")
        assert r is not None
        assert math.isclose(r.x, 10.0, abs_tol=1e-9)
        assert math.isclose(r.y, 20.0, abs_tol=1e-9)

    def test_unknown_crs_raises(self) -> None:
        with pytest.raises(UnknownCRSError):
            projection.transform(0.0, 0.0, "EPSG:4326", "NOT_A_CRS")
        with pytest.raises(UnknownCRSError):
            projection.transform(0.0, 0.0, "EPSG:999999", "EPSG:3857")

    def test_null(self) -> None:
        assert projection.transform(None, 0.0, "EPSG:4326", "EPSG:3857") is None


class TestUTM:
    def test_san_francisco_zone(self) -> None:
        r = projection.to_utm(-122.42, 37.77)
        assert r is not None
        assert r.zone == 10
        assert r.hemisphere == "N"
        assert r.epsg == 32610  # WGS84 / UTM zone 10N
        assert math.isclose(r.easting, 551081.30, abs_tol=1.0)
        assert math.isclose(r.northing, 4180454.90, abs_tol=1.0)

    def test_southern_hemisphere(self) -> None:
        # Sydney (151.2093, -33.8688) -> zone 56 S.
        r = projection.to_utm(151.2093, -33.8688)
        assert r is not None
        assert r.zone == 56
        assert r.hemisphere == "S"
        assert r.epsg == 32756  # WGS84 / UTM zone 56S

    def test_out_of_range_and_null(self) -> None:
        assert projection.to_utm(0.0, 91.0) is None
        assert projection.to_utm(181.0, 0.0) is None
        assert projection.to_utm(None, 0.0) is None


class TestGeodesic:
    def test_nyc_to_london_distance(self) -> None:
        d = projection.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074)
        assert d is not None
        # WGS84 geodesic ~5,585,000 m.
        assert math.isclose(d, 5585000.0, abs_tol=5000.0)

    def test_zero_distance(self) -> None:
        d = projection.geodesic_distance(10.0, 20.0, 10.0, 20.0)
        assert d is not None
        assert math.isclose(d, 0.0, abs_tol=1e-3)

    def test_symmetry(self) -> None:
        a = projection.geodesic_distance(-74.006, 40.7128, -0.1276, 51.5074)
        b = projection.geodesic_distance(-0.1276, 51.5074, -74.006, 40.7128)
        assert a is not None and b is not None
        assert math.isclose(a, b, abs_tol=1e-3)

    def test_bearing_range_and_known(self) -> None:
        # NYC -> London initial bearing is roughly NE (~51 deg).
        az = projection.geodesic_bearing(-74.006, 40.7128, -0.1276, 51.5074)
        assert az is not None
        assert 0.0 <= az < 360.0
        assert math.isclose(az, 51.2, abs_tol=2.0)

    def test_due_east_bearing(self) -> None:
        az = projection.geodesic_bearing(0.0, 0.0, 1.0, 0.0)
        assert az is not None
        assert math.isclose(az, 90.0, abs_tol=1e-6)

    @pytest.mark.parametrize(
        "args",
        [
            (None, 0.0, 0.0, 0.0),
            (0.0, None, 0.0, 0.0),
            (0.0, 0.0, None, 0.0),
            (0.0, 0.0, 0.0, None),
            (0.0, 91.0, 0.0, 0.0),
            (181.0, 0.0, 0.0, 0.0),
            (float("nan"), 0.0, 0.0, 0.0),
        ],
    )
    def test_null_or_out_of_range(self, args) -> None:
        assert projection.geodesic_distance(*args) is None
        assert projection.geodesic_bearing(*args) is None


class TestCrsMetadata:
    def test_crs_name(self) -> None:
        assert projection.crs_name("EPSG:4326") == "WGS 84"

    def test_crs_units(self) -> None:
        assert projection.crs_units("EPSG:4326") == "degree"
        assert projection.crs_units("EPSG:3857") == "metre"

    def test_unknown_crs_raises(self) -> None:
        with pytest.raises(UnknownCRSError):
            projection.crs_name("NOPE")
        with pytest.raises(UnknownCRSError):
            projection.crs_units("NOPE")

    def test_null_crs(self) -> None:
        assert projection.crs_name(None) is None
        assert projection.crs_units(None) is None

    def test_proj_version(self) -> None:
        v = projection.proj_version()
        assert isinstance(v, str) and v
        # Looks like a dotted version, e.g. "9.5.1".
        assert v[0].isdigit()


class TestTransformerCache:
    def test_same_transformer_instance_is_cached(self) -> None:
        t1 = projection._get_transformer("EPSG:4326", "EPSG:3857")
        t2 = projection._get_transformer("EPSG:4326", "EPSG:3857")
        assert t1 is t2  # cached -- not rebuilt
