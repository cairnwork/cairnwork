import sys

import pytest

from cairnwork import workspace
from cairnwork.changeset import GitChangeSet, ManifestEntry
from cairnwork.workspace import CandidateRejected

HIDDEN = ["a\u202eb", "a\nb", "a\x1b[31mb"]


@pytest.mark.parametrize("name", HIDDEN)
def test_protected_path_detail_is_shown(monkeypatch, name):
    monkeypatch.setattr(workspace, "protected_note", lambda _path: name)
    monkeypatch.setattr(workspace, "_parents", lambda *_args: ["a" * 40])
    monkeypatch.setattr(
        workspace,
        "read_change_set",
        lambda *_args: GitChangeSet(
            "a" * 40,
            "c" * 40,
            "d" * 40,
            "e" * 40,
            (ManifestEntry(name, "MODIFY", "1", "2", "100644", "100644"),),
        ),
    )
    monkeypatch.setattr(workspace, "is_protected", lambda _path: True)
    with pytest.raises(CandidateRejected) as info:
        workspace.check_candidate_commit(
            workspace.Path("/repo"),
            workspace.Attempt("a1", "a" * 40, workspace.Path("/clone"), ()),
            "c" * 40,
            (name,),
            python=sys.executable,
        )
    message = str(info.value)
    assert "\\u202e" in message or "\\x0a" in message or "\\x1b" in message
    assert "\n" not in message and "\x1b" not in message and "\u202e" not in message
