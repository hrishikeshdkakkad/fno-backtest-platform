"""PyYAML availability test — required by specs.loader."""
from __future__ import annotations


def test_yaml_importable():
    import yaml
    doc = yaml.safe_load("foo: 1\nbar: [a, b]")
    assert doc == {"foo": 1, "bar": ["a", "b"]}


def test_yaml_roundtrip():
    import yaml
    src = {"strategy_id": "v3", "strategy_version": "3.0.0"}
    out = yaml.safe_load(yaml.safe_dump(src))
    assert out == src
