"""Acceptance casebook #751 step 3c: retain refused run evidence atomically."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from conftest import git
from test_acceptance_casebook_origin import _add, _book

from experimental.acceptance_casebook.cli import main
from experimental.acceptance_casebook.origin import origin_still_holds, read_origin
from experimental.acceptance_casebook.store import list_cases

pytest_plugins = ["test_acceptance_casebook_origin"]


def _run(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        assert isinstance(exc.code, int)
        return exc.code


def _retain_args(lg: dict[str, str], into: Path, **overrides: str) -> list[str]:
    values = {
        "notary-db": lg["db"],
        "record": "a2",
        "repo": lg["repo"],
        "base": lg["base"],
        "candidate": lg["refused"],
        "version": lg["base"],
        "cairnwork-clone": lg["repo"],
        "into": str(into),
    }
    values.update(overrides)
    return ["retain", *[part for key, value in values.items() for part in (f"--{key}", value)]]


def _snapshot(path: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(p.relative_to(path)): (p.read_bytes() if p.is_file() else b"", p.stat().st_mtime_ns)
        for p in path.rglob("*")
    }


def _paths(lg: dict[str, str], repo: Path) -> dict[str, str]:
    return {**lg, "repo": str(repo)}


def _assert_bundle(lg: dict[str, str], case_dir: Path, tmp_path: Path) -> None:
    assert {p.name for p in case_dir.iterdir()} == {
        "notary.sqlite",
        "candidate.bundle",
        "version.json",
        "MANIFEST.json",
    }
    subprocess.run(
        ["git", "-C", str(tmp_path), "bundle", "verify", str(case_dir / "candidate.bundle")],
        check=True,
        capture_output=True,
        text=True,
    )
    original, original_origin = read_origin(Path(lg["db"]), "task-1", "a2")
    copied, copied_origin = read_origin(case_dir / "notary.sqlite", "task-1", "a2")
    assert original == copied and original_origin["row_sha256"] == copied_origin["row_sha256"]
    manifest = json.loads((case_dir / "MANIFEST.json").read_text())
    assert manifest == {
        name: hashlib.sha256((case_dir / name).read_bytes()).hexdigest()
        for name in ("notary.sqlite", "candidate.bundle", "version.json")
    }
    for clone, args in (
        (tmp_path / "bundle-clone", ["git", "clone", "--quiet"]),
        (tmp_path / "direct-bundle-clone", ["git", "clone", "--quiet", "--no-local", "--"]),
    ):
        subprocess.run(
            [*args, str(case_dir / "candidate.bundle"), str(clone)],
            check=True,
            capture_output=True,
            text=True,
        )
        for sha in (lg["base"], lg["refused"]):
            subprocess.run(
                ["git", "-C", str(clone), "cat-file", "-e", f"{sha}^{{commit}}"],
                check=True,
                capture_output=True,
                text=True,
            )


def test_ac6a_retained_evidence_is_usable_after_sources_are_removed(
    lg: dict[str, str], repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lg = _paths(lg, repo)
    into = tmp_path / "casebook"
    capsys.readouterr()
    assert _run(_retain_args(lg, into)) == 0
    assert "RETAINED" in capsys.readouterr().out
    case_dir = into / "cases" / read_origin(Path(lg["db"]), "task-1", "a2")[1]["row_sha256"]
    _assert_bundle(lg, case_dir, tmp_path)
    assert (
        _add(
            {**lg, "db": str(case_dir / "notary.sqlite")},
            lg["refused"],
            "a2",
            "refused_good",
            ev={"attempt_id": "a2"},
        )
        == 0
    )
    case = list_cases(_book(lg))[0]
    assert origin_still_holds(case_dir / "notary.sqlite", case)
    shutil.rmtree(repo)
    Path(lg["db"]).unlink()
    assert origin_still_holds(case_dir / "notary.sqlite", case)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base", "abc"),
        ("candidate", "main"),
        ("candidate", "f" * 40),
        ("base", "bound-base"),
        ("candidate", "bound-candidate"),
    ],
    ids=["short-sha", "branch-name", "sha-absent", "other-base", "other-candidate"],
)
def test_ac6b_invalid_or_unbound_inputs_refuse_without_writing(
    lg: dict[str, str],
    repo: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: str,
) -> None:
    lg = _paths(lg, repo)
    value = {"bound-base": lg["cand"], "bound-candidate": lg["base"]}.get(value, value)
    into = tmp_path / "casebook"
    capsys.readouterr()
    assert _run(_retain_args(lg, into, **{field: value})) == 2
    output = capsys.readouterr().out
    expected = "CASE_RECORD_MISMATCH" if value in (lg["cand"], lg["base"]) else "CASE_INVALID"
    assert expected in output
    assert not (into / "cases").exists()


def test_ac6c_source_and_repo_unchanged_and_bundle_failure_cleans_temp(
    lg: dict[str, str],
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from experimental.acceptance_casebook import retain

    lg = _paths(lg, repo)
    Path(lg["db"] + "-wal").touch()
    Path(lg["db"] + "-shm").touch()
    before_db = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in Path(lg["db"]).parent.glob("notary.sqlite*")}  # fmt: skip
    before_repo = _snapshot(repo)
    refs = git(repo, "show-ref")
    success_into = tmp_path / "success"
    assert _run(_retain_args(lg, success_into)) == 0
    assert before_repo == _snapshot(repo) and refs == git(repo, "show-ref")
    assert before_db == {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in Path(lg["db"]).parent.glob("notary.sqlite*")
    }
    fail_into = tmp_path / "failure"
    monkeypatch.setattr(
        retain,
        "_create_bundle",
        lambda *_: (_ for _ in ()).throw(retain.CasebookError("BUNDLE_FAILED")),
    )
    capsys.readouterr()
    assert _run(_retain_args(lg, fail_into)) == 2
    assert "BUNDLE_FAILED" in capsys.readouterr().out
    assert not (fail_into / "cases").exists()
    assert before_repo == _snapshot(repo) and refs == git(repo, "show-ref")
    after_db = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                for p in Path(lg["db"]).parent.glob("notary.sqlite*")}  # fmt: skip
    assert before_db == after_db


def test_ac6d_second_retain_refuses_and_preserves_existing_bytes(
    lg: dict[str, str],
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lg = _paths(lg, repo)
    into = tmp_path / "casebook"
    assert _run(_retain_args(lg, into)) == 0
    case_dir = into / "cases" / read_origin(Path(lg["db"]), "task-1", "a2")[1]["row_sha256"]
    before = _snapshot(case_dir)
    capsys.readouterr()
    assert _run(_retain_args(lg, into)) == 2
    assert "ALREADY_RETAINED" in capsys.readouterr().out
    assert before == _snapshot(case_dir)
    from experimental.acceptance_casebook import retain

    real_rename = retain._rename_exclusive

    def race_create(source: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "owner-marker").write_text("preserve")
        real_rename(source, destination)

    monkeypatch.setattr(retain, "_rename_exclusive", race_create)
    raced = tmp_path / "race"
    capsys.readouterr()
    assert _run(_retain_args(lg, raced)) == 2
    assert "ALREADY_RETAINED" in capsys.readouterr().out
    race_case = raced / "cases" / case_dir.name
    assert (race_case / "owner-marker").read_text() == "preserve"
    assert not list((raced / "cases").glob(".retain-*"))


def test_relative_into_works_from_unrelated_cwd(
    lg: dict[str, str],
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lg = _paths(lg, repo)
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()
    assert _run(_retain_args(lg, Path("relative-casebook"))) == 0
    assert "RETAINED" in capsys.readouterr().out
    case_dir = (
        tmp_path
        / "relative-casebook"
        / "cases"
        / read_origin(Path(lg["db"]), "task-1", "a2")[1]["row_sha256"]
    )
    assert case_dir.is_dir()


def test_bundle_verification_works_outside_any_git_repo(
    lg: dict[str, str],
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lg = _paths(lg, repo)
    with tempfile.TemporaryDirectory(dir="/tmp") as plain:
        cwd = Path(plain)
        monkeypatch.chdir(cwd)
        probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True)
        assert probe.returncode != 0
        into = tmp_path / "casebook"
        capsys.readouterr()
        assert _run(_retain_args(lg, into)) == 0
        assert "RETAINED" in capsys.readouterr().out
        case_dir = into / "cases" / read_origin(Path(lg["db"]), "task-1", "a2")[1]["row_sha256"]
        _assert_bundle(lg, case_dir, tmp_path)


def test_racing_empty_case_directory_is_preserved_and_temp_is_removed(
    lg: dict[str, str],
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from experimental.acceptance_casebook import retain

    lg = _paths(lg, repo)
    real_rename = retain._rename_exclusive

    def race_empty(source: Path, destination: Path) -> None:
        destination.mkdir()
        real_rename(source, destination)

    monkeypatch.setattr(retain, "_rename_exclusive", race_empty)
    into = tmp_path / "empty-race"
    capsys.readouterr()
    assert _run(_retain_args(lg, into)) == 2
    assert "ALREADY_RETAINED" in capsys.readouterr().out
    cases = into / "cases"
    case_dir = cases / read_origin(Path(lg["db"]), "task-1", "a2")[1]["row_sha256"]
    assert list(case_dir.iterdir()) == []
    assert not list(cases.glob(".retain-*"))


def test_accepted_record_is_not_retained(
    lg: dict[str, str], repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lg = _paths(lg, repo)
    into = tmp_path / "accepted-casebook"
    capsys.readouterr()
    assert _run(_retain_args(lg, into, record="a1", candidate=lg["cand"])) == 2
    assert "CASE_ORIGIN_MISMATCH" in capsys.readouterr().out
    assert not (into / "cases").exists()


def test_unknown_acceptance_version_is_not_retained(
    lg: dict[str, str], repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lg = _paths(lg, repo)
    into = tmp_path / "unknown-version-casebook"
    capsys.readouterr()
    assert _run(_retain_args(lg, into, version="f" * 40)) == 2
    assert "UNKNOWN_ACCEPTANCE_VERSION" in capsys.readouterr().out
    assert not (into / "cases").exists()


@pytest.mark.parametrize("option", ["base", "repo"])
def test_must_block_option_value_starting_with_dash(
    lg: dict[str, str], repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    lg = _paths(lg, repo)
    into = tmp_path / "casebook"
    capsys.readouterr()
    assert _run(_retain_args(lg, into, **{option: "-" + "f" * 40})) == 2
    capsys.readouterr()
    assert not (into / "cases").exists()
