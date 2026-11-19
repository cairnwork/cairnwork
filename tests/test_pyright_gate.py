from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import pyright_gate as gate


def _tree(root: Path, source: str) -> Path:
    root.mkdir()
    (root / "m.py").write_text(source, encoding="utf-8")
    return root


def _report(
    root: Path,
    findings: list[tuple[int, str]],
    name: str = "r.json",
    severity: str = "error",
    rule: str = "reportGeneralTypeIssues",
    files: int = 1,
) -> Path:
    diagnostics = [
        {
            "file": str(root / "m.py"),
            "severity": severity,
            "message": message,
            "range": {"start": {"line": line, "character": 0}},
            "rule": rule,
        }
        for line, message in findings
    ]
    path = root.parent / f"{root.name}-{name}"
    data = {"generalDiagnostics": diagnostics, "summary": {"filesAnalyzed": files}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(base: Path, base_report: Path, head: Path, head_report: Path) -> int:
    return gate.main([str(base_report), str(base), str(head_report), str(head)])


def test_line_shift_is_not_new(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", "import os\n\nx: int = 'a'\n")
    code = _run(base, _report(base, [(0, "bad")]), head, _report(head, [(2, "bad")]))
    assert code == 0


def test_edited_line_text_is_new(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", "x: int = 'b'\n")
    code = _run(base, _report(base, [(0, "bad")]), head, _report(head, [(0, "bad")]))
    assert code == 1
    assert "NEW m.py" in capsys.readouterr().out


def test_duplicate_of_old_finding_is_new(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", "x: int = 'a'\nx: int = 'a'\n")
    code = _run(base, _report(base, [(0, "bad")]), head, _report(head, [(0, "bad"), (1, "bad")]))
    assert code == 1


def test_removed_finding_is_fine(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", "x: int = 1\n")
    assert _run(base, _report(base, [(0, "bad")]), head, _report(head, [])) == 0


def test_malformed_json_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    good = _report(head, [])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _run(base, bad, head, good) == 2
    assert _run(base, tmp_path / "missing.json", head, good) == 2
    bad.write_text(json.dumps({"generalDiagnostics": []}), encoding="utf-8")
    assert _run(base, bad, head, good) == 2


def test_zero_files_analyzed_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    empty = _report(head, [], name="empty.json", files=0)
    assert _run(base, _report(base, []), head, empty) == 2
    assert _run(base, empty, head, _report(head, [])) == 2


def test_information_severity_is_ignored(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    info = _report(head, [(0, "hint")], severity="information")
    assert _run(base, _report(base, []), head, info) == 0


def test_rule_is_part_of_the_key(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", "x: int = 'a'\n")
    old = _report(base, [(0, "bad")], rule="reportA")
    assert _run(base, old, head, _report(head, [(0, "bad")], rule="reportB")) == 1


def test_message_is_part_of_the_key(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", "x: int = 'a'\n")
    assert _run(base, _report(base, [(0, "old")]), head, _report(head, [(0, "new")])) == 1


def test_config_change_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    (head / "pyrightconfig.json").write_text("{}", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


def test_pyproject_pyright_table_change_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    (base / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (head / "pyproject.toml").write_text("[tool.pyright]\nstrict = ['.']\n", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2
    (head / "pyproject.toml").write_text("[tool.ruff]\nline-length = 1\n", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 0


def test_extends_in_either_config_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    for side in (base, head):
        for other in (base, head):
            (other / "pyrightconfig.json").unlink(missing_ok=True)
            (other / "pyproject.toml").unlink(missing_ok=True)
        (side / "pyrightconfig.json").write_text('{"extends": "./x.json"}', encoding="utf-8")
        assert _run(base, _report(base, []), head, _report(head, [])) == 2
        (side / "pyrightconfig.json").unlink()
        (side / "pyproject.toml").write_text('[tool.pyright]\nextends = "x.json"\n')
        assert _run(base, _report(base, []), head, _report(head, [])) == 2


def test_identical_extends_on_both_sides_still_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    for root in (base, head):
        (root / "pyrightconfig.json").write_text('{"extends": "./x.json"}', encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2
    for root in (base, head):
        (root / "pyrightconfig.json").unlink()
        (root / "pyproject.toml").write_text('[tool.pyright]\nextends = "x.json"\n')
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


def test_missing_generaldiagnostics_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    bad = tmp_path / "nodiag.json"
    bad.write_text(json.dumps({"summary": {"filesAnalyzed": 3}}), encoding="utf-8")
    assert _run(base, bad, head, _report(head, [])) == 2


@pytest.mark.parametrize(
    "source",
    [
        "# type: ignore\nx: int = 'a'\n",
        "# type: ignore[assignment]\nx: int = 'a'\n",
        "# header # type: ignore\nx: int = 'a'\n",
        "\ufeff# type: ignore\nx: int = 'a'\n",
        '"""docstring"""\n# type: ignore\nx: int = \'a\'\n',
        "import os\r# type: ignore\rx: int = 'a'\n",
    ],
)
def test_file_level_type_ignore_fails_closed(tmp_path: Path, source: str) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", source)
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


@pytest.mark.parametrize(
    "source",
    [
        "# pyright: basic\nx: int = 'a'\n",
        "# pyright: reportGeneralTypeIssues=false\nx: int = 'a'\n",
        "# pyright: ignore\nx: int = 'a'\n",
        "x: int = 1  # pyright: reportGeneralTypeIssues=false\n",
    ],
)
def test_file_level_pyright_directive_fails_closed(tmp_path: Path, source: str) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", source)
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


@pytest.mark.parametrize("space", ["\u00a0", "\u2003", "\u3000", "\f", "\ufeff"])
@pytest.mark.parametrize(
    "template",
    [
        "#{sp}pyright:{sp}reportAssignmentType=false\nx: int = 'a'\n",
        "x: int = 1  #{sp}pyright{sp}:{sp}basic\n",
        "#{sp}type{sp}:{sp}ignore\nx: int = 'a'\n",
        "# type: ignore{sp}\nx: int = 'a'\n",
    ],
)
def test_non_ascii_whitespace_directive_variants_fail_closed(
    tmp_path: Path, template: str, space: str
) -> None:
    base = _tree(tmp_path / "base", "x: int = 'a'\n")
    head = _tree(tmp_path / "head", template.format(sp=space))
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


def test_editing_file_with_existing_file_level_ignore_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "y = 1\n")
    head = _tree(tmp_path / "head", "y = 2\n")
    suppressed = "# pyright: reportArgumentType=false\nx = 1\n"
    (base / "a.py").write_text(suppressed, encoding="utf-8")
    (head / "a.py").write_text(suppressed, encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 0
    (head / "a.py").write_text("# pyright: reportArgumentType=false\nx = 2\n", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


def test_inline_ignore_and_string_literal_are_allowed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(
        tmp_path / "head",
        "x: int = 1  # type: ignore[assignment]\n"
        "y: int = 2  # pyright: ignore[reportAssignmentType]\n"
        "s = '# type: ignore'\n",
    )
    assert _run(base, _report(base, []), head, _report(head, [])) == 0


@pytest.mark.parametrize(
    "rel",
    ["m.pyi", "sub/m.pyi", "typings/m.pyi", "typings/m.py", "pkg-stubs/m.pyi"],
)
def test_stub_addition_fails_closed(tmp_path: Path, rel: str) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    target = head / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x: int\n", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


def test_stub_modification_or_deletion_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    (base / "m.pyi").write_text("x: int\n", encoding="utf-8")
    (head / "m.pyi").write_text("x: int\n", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 0
    (head / "m.pyi").write_text("x: str\n", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2
    (head / "m.pyi").unlink()
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


def test_nested_pyrightconfig_or_invalid_pyproject_fails_closed(tmp_path: Path) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    (head / "sub").mkdir()
    (head / "sub" / "pyrightconfig.json").write_text("{}", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2
    (head / "sub" / "pyrightconfig.json").unlink()
    (head / "pyproject.toml").write_text("[tool.pyright\n", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


def test_prepare_trees_materializes_trusted_config(tmp_path: Path) -> None:
    trusted = _tree(tmp_path / "trusted", "x = 1\n")
    (trusted / "pyrightconfig.json").write_text('{"typeCheckingMode": "strict"}', encoding="utf-8")
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    off = '[tool.pyright]\ntypeCheckingMode = "off"\n'
    (base / "pyproject.toml").write_text(off, encoding="utf-8")
    (head / "pyproject.toml").write_text(off, encoding="utf-8")
    assert gate.main(["--prepare-trees", str(trusted), str(base), str(head)]) == 0
    assert json.loads((base / "pyrightconfig.json").read_text(encoding="utf-8")) == {
        "typeCheckingMode": "strict"
    }
    assert json.loads((head / "pyrightconfig.json").read_text(encoding="utf-8")) == {
        "typeCheckingMode": "strict"
    }
    (head / "m.py").write_text("# type: ignore\nx = 1\n", encoding="utf-8")
    assert gate.main(["--prepare-trees", str(trusted), str(base), str(head)]) == 2
    (head / "m.py").write_text("x = 1\n", encoding="utf-8")
    (head / "m.pyi").write_text("x: int\n", encoding="utf-8")
    assert gate.main(["--prepare-trees", str(trusted), str(base), str(head)]) == 2


def test_walk_error_and_oversized_file_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    locked = head / "locked"
    locked.mkdir()
    (locked / "sub.py").write_text("x = 1\n", encoding="utf-8")
    locked.chmod(0)
    try:
        assert _run(base, _report(base, []), head, _report(head, [])) == 2
    finally:
        locked.chmod(0o700)

    monkeypatch.setattr(gate, "_MAX_FILE_BYTES", 16)
    (head / "big.py").write_text("x = 1\n" * 10, encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2


@pytest.mark.parametrize("rel", ["Typings/m.pyi", "pkg-STUBS/m.pyi", "Typings/m.py"])
def test_case_variant_stub_dirs_fail_closed(tmp_path: Path, rel: str) -> None:
    base = _tree(tmp_path / "base", "x = 1\n")
    head = _tree(tmp_path / "head", "x = 1\n")
    target = head / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x: int\n", encoding="utf-8")
    assert _run(base, _report(base, []), head, _report(head, [])) == 2
