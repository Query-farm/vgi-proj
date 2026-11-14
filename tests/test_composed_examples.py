"""Composed examples are published once each, in the schema list and up front on their driver."""

from __future__ import annotations

import json

from vgi_proj import composed, scalars
from vgi_proj.worker import _MAIN_SCHEMA_TAGS

_BY_NAME = {f.Meta.name: f for f in scalars.SCALAR_FUNCTIONS}

# Each composed example -> the function that drives it (the agent simulator only
# shows the first five examples of each function).
_DRIVERS = {
    "transform": composed.BNG_SITES_DISTANCE,
    "to_utm": composed.UTM_GRID_VS_GEODESIC,
    "from_webmercator": composed.WEBMERCATOR_GROUND_DISTANCE,
    "geodesic_distance": composed.DISTANCE_AND_HEADING_FROM_NYC,
}


def test_composed_in_schema_list() -> None:
    sqls = [e["sql"] for e in json.loads(_MAIN_SCHEMA_TAGS["vgi.example_queries"])]
    for ex in composed.COMPOSED_EXAMPLES:
        assert sqls.count(ex.sql) == 1
    assert len(_MAIN_SCHEMA_TAGS["vgi.example_queries"]) <= 8000  # VGI417


def test_composed_on_driver_within_first_five() -> None:
    for name, ex in _DRIVERS.items():
        meta = _BY_NAME[name].Meta
        assert ex in meta.examples[:5], name
        tagged = [e["sql"] for e in json.loads(meta.tags["vgi.example_queries"])]
        assert ex.sql in tagged[:5], name


def test_composed_examples_are_qualified_and_ordered() -> None:
    for ex in composed.COMPOSED_EXAMPLES:
        assert ex.description
        assert "ORDER BY" in ex.sql  # VGI510
        assert "proj.main." in ex.sql  # VGI505
        assert "SELECT *" not in ex.sql  # VGI514


def test_executable_examples_parse_and_fit() -> None:
    entries = json.loads(scalars._EXECUTABLE_EXAMPLES)
    assert all(e["description"] and e["sql"] for e in entries)
    assert len(scalars._EXECUTABLE_EXAMPLES) <= 4000
