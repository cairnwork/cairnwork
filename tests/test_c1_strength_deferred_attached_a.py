"""Focused mutation pins for deferred attached-test AST analysis."""

import ast
from typing import Any

from cairnwork.deferred_attached import (
    _blind_attribute_call,
    _import_part,
)


def _value(source: str) -> Any:
    stmt: Any = ast.parse(source).body[0]
    return stmt.value


def test_blind_call_classifier_checks_every_call_and_builtin_boundary():
    assert _blind_attribute_call(_value("test_x = globals().get('x')"))
    assert _blind_attribute_call(_value("test_x = len.__self__.eval('x')"))
    assert _blind_attribute_call(_value("test_x = builtins.eval('x')"))
    assert not _blind_attribute_call(_value("test_x = custom.eval('x')"))
    assert not _blind_attribute_call(_value("test_x = unknown('x')"))


def test_import_part_separates_heads_reached_bodies_and_class_methods():
    fn = ast.parse(
        "@decorate(flag)\ndef f(x=default, *, y=kwdefault) -> result:\n    body()\n"
    ).body[0]
    assert {"decorate", "flag", "default", "kwdefault", "result"} <= {
        n.id for part in _import_part(fn) for n in ast.walk(part) if isinstance(n, ast.Name)
    }
    assert "body" not in {
        n.id for p in _import_part(fn) for n in ast.walk(p) if isinstance(n, ast.Name)
    }
    assert "body" in {
        n.id for p in _import_part(fn, reached=True) for n in ast.walk(p) if isinstance(n, ast.Name)
    }

    cls = ast.parse(
        """class C(Base, metaclass=Meta):
    marker = helper()
    def test_x(self): test_body()
    def ordinary(self): ordinary_body()
    invoke = ordinary
"""
    ).body[0]
    names = {n.id for p in _import_part(cls) for n in ast.walk(p) if isinstance(n, ast.Name)}
    assert {"Base", "Meta", "marker", "helper", "ordinary_body"} <= names
    assert "test_body" not in names
    reached = {
        n.id
        for p in _import_part(cls, reached=True)
        for n in ast.walk(p)
        if isinstance(n, ast.Name)
    }
    assert "ordinary_body" in reached and "test_body" not in reached
