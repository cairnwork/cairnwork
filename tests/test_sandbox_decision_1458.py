"""Trusted-side sandbox availability checks for Issue #1458."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pra1353_consume as consume
import pytest

import cairnwork_sandbox


def test_availability_does_not_import_candidate_sandbox(monkeypatch):
    fake = types.ModuleType("cairnwork_sandbox")

    def fake_attribute(name):
        if name == "select":
            raise AssertionError("candidate sandbox was consulted")
        return None

    fake.__getattr__ = fake_attribute
    monkeypatch.setitem(sys.modules, "cairnwork_sandbox", fake)
    assert isinstance(consume.sandbox_available(), bool)


def test_availability_does_not_import_candidate_sandbox_in_child(tmp_path):
    marker = tmp_path / "imported"
    fake_dir = tmp_path / "fake"
    package = fake_dir / "cairnwork_sandbox"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    site_paths = [p for p in sys.path if "site-packages" in p or "dist-packages" in p]
    stdlib_paths = [p for p in sys.path if p and p not in site_paths]
    paths = [str(fake_dir), str(root / "tests"), str(root / "src"), *site_paths, *stdlib_paths]
    code = (
        f"import sys; sys.path = {paths!r}; import pra1353_consume as c; "
        "assert isinstance(c.sandbox_available(), bool)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_availability_matches_real_sandbox_package():
    assert consume.sandbox_available() == (
        sys.platform == "darwin" and cairnwork_sandbox.select() is not None
    )


def _patch_macos(monkeypatch, run):
    monkeypatch.setattr(consume.sys, "platform", "darwin")
    monkeypatch.setattr(consume.subprocess, "run", run)


def test_availability_is_false_off_macos_without_probing(monkeypatch):
    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("sandbox probe should not run")

    monkeypatch.setattr(consume.sys, "platform", "linux")
    monkeypatch.setattr(consume.subprocess, "run", unexpected_run)
    assert consume.sandbox_available() is False


@pytest.mark.parametrize("codes", [(1, 1), (0, 0)])
def test_availability_fails_closed_for_bad_probe_exits(monkeypatch, codes):
    results = iter(codes)
    _patch_macos(
        monkeypatch,
        lambda *_args, **_kwargs: SimpleNamespace(returncode=next(results)),
    )
    assert consume.sandbox_available() is False


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("no sandbox-exec"),
        PermissionError("not executable"),
        OSError("probe failed"),
        subprocess.TimeoutExpired("probe", 10),
    ],
)
def test_availability_fails_closed_for_probe_errors(monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error

    _patch_macos(monkeypatch, fail)
    assert consume.sandbox_available() is False


def test_availability_accepts_successful_allow_and_denial(monkeypatch):
    codes = iter((0, 1))
    _patch_macos(
        monkeypatch,
        lambda *_args, **_kwargs: SimpleNamespace(returncode=next(codes)),
    )
    assert consume.sandbox_available() is True
