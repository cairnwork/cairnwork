"""cw#1468: variants of #1454 B1 that still ran unpinned code inside the #1353 runner."""

from __future__ import annotations

from pathlib import Path

import pra1353_consume as consume
import pytest
from test_1039_1353_product_reach import PATCH
from test_1039_1353_witnesses import PINS, broker_report  # noqa: F401

# V1: a tracked src/ top-level module with a pinned witness name, removing itself after use.
SHADOW = b"""
import os as _o, sys as _s
_t = None
for _p in _s.path:
    _c = _o.path.join(_p, "pra1353_derive.py")
    if _p.endswith("/tests") and _o.path.exists(_c):
        _t = _c
exec(compile(open(_t).read(), _t, "exec"), globals())
_D = derive
def derive(*a, **k):
    _s.modules.pop("pra1353_derive", None)
    r = _D(*a, **k)
    if isinstance(r, dict):
        r = {**r, "matches_spec": True, "observed": "forged-by-src-shadow"}
    return r
"""

# V2: a product child rewrites the sealed c10 witness the runner loads later, then restores it.
CHILD = """
import sys as _hs, os as _ho, base64 as _hb
for _hr in list(_hs.path):
    _hf = _ho.path.join(_hr, "pra1353_c10.py")
    _hd = _ho.path.join(_ho.path.dirname(_ho.path.dirname(_hr)), "hunt_v2_done")
    if _hr.endswith("/tests") and _ho.path.exists(_hf) and not _ho.path.exists(_hd):
        open(_hd, "w").close()
        _horig = open(_hf, "rb").read()
        _hpay = @@PATCH@@ + (
            "\\nimport os as _oo, base64 as _bb\\n_oo.chmod(%r, 0o644)\\n"
            "open(%r, 'wb').write(_bb.b64decode(%r))\\n_oo.chmod(%r, 0o444)\\n"
        ) % (_hf, _hf, _hb.b64encode(_horig).decode(), _hf)
        _ho.chmod(_hf, 0o644)
        open(_hf, "wb").write(_horig + _hpay.encode())
        _ho.chmod(_hf, 0o444)
        break
""".replace("@@PATCH@@", repr(PATCH))


def _changed(monkeypatch, honest_report, extra_files):
    honest = {r["seq"]: r for r in honest_report["rows"]}
    real = consume._tracked_source_bytes()
    monkeypatch.setattr(
        consume, "_tracked_source_bytes", lambda repo=None: {**real, **extra_files(real)}
    )
    try:
        report = consume.launch(PINS)
    except ValueError:
        return []  # refused outright is also a pass
    forged = {r["seq"]: r for r in report["rows"]}
    return sorted(s for s in honest if honest[s] != forged.get(s))


def test_v1_src_shadow_of_pinned_witness_cannot_change_rows(monkeypatch, broker_report):  # noqa: F811
    changed = _changed(monkeypatch, broker_report, lambda real: {"src/pra1353_derive.py": SHADOW})
    assert not changed, f"V1: src shadow rewrote rows {changed}"


def test_v2_child_rewrite_of_lazily_loaded_witness_cannot_change_rows(monkeypatch, broker_report):  # noqa: F811
    init = "src/cairnwork/__init__.py"
    changed = _changed(monkeypatch, broker_report, lambda real: {init: real[init] + CHILD.encode()})
    assert not changed, f"V2: child file write rewrote rows {changed}"


def test_tracked_src_top_level_module_is_refused(tmp_path: Path) -> None:
    import subprocess

    (tmp_path / "src" / "cairnwork").mkdir(parents=True)
    (tmp_path / "src" / "cairnwork" / "__init__.py").write_text("")
    (tmp_path / "src" / "pra1353_derive.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "src"], check=True)
    with pytest.raises(ValueError, match="invalid source path|not a product package"):
        consume._tracked_source_bytes(tmp_path)


@pytest.mark.parametrize("missing", consume._RUNNER_WITNESSES)
def test_every_runner_witness_must_be_reported_from_sealed_tests(tmp_path: Path, missing) -> None:
    modules = [
        {"name": n, "file": str(tmp_path / "tests" / f"{n}.py")}
        for n in consume._RUNNER_WITNESSES
        if n != missing
    ]
    with pytest.raises(ValueError, match=missing):
        consume._require_runner_witnesses(modules, tmp_path)
    modules.append({"name": missing, "file": str(tmp_path / "src" / f"{missing}.py")})
    with pytest.raises(ValueError, match=missing):
        consume._require_runner_witnesses(modules, tmp_path)
    modules[-1]["file"] = str(tmp_path / "tests" / f"{missing}.py")
    consume._require_runner_witnesses(modules, tmp_path)
