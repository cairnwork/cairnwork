"""Layer2 request: the local-relay worker's run_test sees the task's own dependencies."""

import subprocess
import sys

import pytest

from experimental.local_relay import worker_setup


def _fake_venv(tmp_path):
    site = tmp_path / "venv" / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    return tmp_path / "venv", site


def test_share_writes_a_pth_with_the_verifier_site_packages(tmp_path):
    venv, site = _fake_venv(tmp_path)
    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    worker_setup.share_verifier_packages(venv, sys.executable, checkout)
    lines = (site / "cairnwork_verifier.pth").read_text().splitlines()
    assert lines[0] == str(checkout / "src")  # the edited checkout comes first
    assert len(lines) > 1 and all(
        row.startswith("import site; site.addsitedir(") for row in lines[1:]
    )


def test_pth_processes_nested_pth_files_and_prefers_the_checkout(tmp_path):
    venv, site = _fake_venv(tmp_path)
    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    (checkout / "src" / "taskdep_x.py").write_text("VALUE = 'edited'\n")
    deps, real = tmp_path / "deps", tmp_path / "real"
    deps.mkdir()
    real.mkdir()
    (real / "taskdep_x.py").write_text("VALUE = 'original'\n")
    (real / "other_dep.py").write_text("VALUE = 'dep'\n")
    (deps / "editable.pth").write_text(f"{real}\n")  # a nested .pth, as an editable install has
    (site / "cairnwork_verifier.pth").write_text(
        f"{checkout / 'src'}\nimport site; site.addsitedir({str(deps)!r})\n"
    )
    code = (
        f"import site; site.addsitedir({str(site)!r}); "
        "import taskdep_x, other_dep; print(taskdep_x.VALUE, other_dep.VALUE)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert result.stdout.strip() == "edited dep"


def test_share_refuses_when_no_site_packages(tmp_path):
    with pytest.raises(RuntimeError, match="no site-packages"):
        worker_setup.share_verifier_packages(tmp_path / "venv", sys.executable, tmp_path)


def _layout(monkeypatch, tmp_path, env, python):
    root, checkout = tmp_path / "root", tmp_path / "root" / "checkout"
    root.mkdir()
    checkout.mkdir()
    monkeypatch.setattr(worker_setup, "_worker_python", lambda: "/tool/python3.13")
    monkeypatch.setattr(worker_setup.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(worker_setup, "_regroup_tree", lambda *a: None)
    calls = []
    monkeypatch.setattr(worker_setup, "share_verifier_packages", lambda *a: calls.append(a))
    if env is None:
        monkeypatch.delenv(worker_setup.SHARE_ENV, raising=False)
    else:
        monkeypatch.setenv(worker_setup.SHARE_ENV, env)
    worker_setup.prepare_worker_layout(root, checkout, python)
    return calls


def test_sharing_is_off_by_default(monkeypatch, tmp_path):
    assert _layout(monkeypatch, tmp_path, None, "/v/python") == []


def test_sharing_needs_a_verifier_python(monkeypatch, tmp_path):
    assert _layout(monkeypatch, tmp_path, "1", None) == []


def test_sharing_runs_when_enabled_with_a_verifier_python(monkeypatch, tmp_path):
    calls = _layout(monkeypatch, tmp_path, "1", "/v/python")
    assert (
        len(calls) == 1
        and calls[0][1] == "/v/python"
        and calls[0][2] == tmp_path / "root" / "checkout"
    )
