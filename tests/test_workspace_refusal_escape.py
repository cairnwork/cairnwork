import sys

import pytest
from conftest import git

from cairnwork.tamper_scan import Finding
from cairnwork.workspace import (
    Attempt,
    CandidateRejected,
    _guarded_refs,
    collect_candidate,
    create_attempt_clone,
)


def test_tamper_refusal_keeps_newline_filename_on_one_line(repo, tmp_path, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    path = "src/a\nb.py"
    clone = create_attempt_clone(repo, base, tmp_path / "attempts", "a1").path
    (clone / path).parent.mkdir(parents=True, exist_ok=True)
    (clone / path).write_text("x = 2\n")
    git(clone, "add", "-A")
    git(
        clone,
        "-c",
        "user.email=w@example.invalid",
        "-c",
        "user.name=w",
        "commit",
        "-q",
        "-m",
        "candidate",
    )
    made = Attempt("a1", base, clone, _guarded_refs(repo, "a1"))
    monkeypatch.setattr(
        "cairnwork.workspace.scan_patch", lambda *a, **k: [Finding(path, 2, "sys_modules_write")]
    )

    with pytest.raises(CandidateRejected, match="VERIFIER_TAMPER_API") as info:
        collect_candidate(repo, made, (path,), python=sys.executable)

    assert "\\x0a" in str(info.value)
    assert len(str(info.value).splitlines()) == 1
