"""host_notes: what the host leaves open around the bytecode hook, as a note (never a refusal)."""

from __future__ import annotations

import pytest

from cairnwork import bytecode_hook


@pytest.mark.parametrize(
    ("text", "note"),
    [
        ("0\n", "bytecode hook: ptrace_scope 0"),
        ("", "bytecode hook: ptrace_scope empty"),
        ("1\n", None),
        ("2\n", None),
        ("3\n", None),
    ],
)
def test_linux_notes_an_open_ptrace_scope_only(tmp_path, monkeypatch, text, note):
    scope = tmp_path / "ptrace_scope"
    scope.write_text(text, encoding="ascii")
    monkeypatch.setattr(bytecode_hook, "PTRACE_SCOPE", scope)
    monkeypatch.setattr(bytecode_hook.sys, "platform", "linux")
    assert bytecode_hook.host_notes() == ((note,) if note else ())


def test_linux_notes_an_unreadable_ptrace_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(bytecode_hook, "PTRACE_SCOPE", tmp_path / "missing")
    monkeypatch.setattr(bytecode_hook.sys, "platform", "linux")
    assert bytecode_hook.host_notes() == ("bytecode hook: ptrace_scope unreadable",)


def test_other_platforms_have_no_note(tmp_path, monkeypatch):
    scope = tmp_path / "ptrace_scope"
    scope.write_text("0\n", encoding="ascii")
    monkeypatch.setattr(bytecode_hook, "PTRACE_SCOPE", scope)
    monkeypatch.setattr(bytecode_hook.sys, "platform", "darwin")
    assert bytecode_hook.host_notes() == ()
