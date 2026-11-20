"""#1454 step 2b: account setup, ownership, cleanup and timeout rules of the trusted workflow.

The workflow is parsed into steps and commands; every rule has a broken variant that must fail.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest
from workflow_yaml import parse_yaml

TRUSTED = Path(__file__).resolve().parents[1] / ".github/workflows/pra1353-trusted.yml"
USER = "pra1353gate"
DATA_COPIES = {"$root/trusted", "$root/head"}
ROOT_ONLY = ("venv", "python", "lock", "base-python.json")


def commands(run: str) -> list[str]:
    """Logical shell commands of a run block: continuations joined, comments and blanks dropped."""
    joined = re.sub(r"\\\n\s*", " ", run)
    return [c.strip() for c in joined.splitlines() if c.strip() and not c.strip().startswith("#")]


def _step(steps: list[dict[str, Any]], needle: str) -> dict[str, Any]:
    found = [s for s in steps if needle in str(s.get("run", ""))]
    assert len(found) == 1, needle
    return found[0]


def _args(command: str) -> list[str]:
    return [t for t in shlex.split(command) if not t.startswith("-")]


def check_setup(run: str) -> None:
    cmds = commands(run)
    (useradd,) = [c for c in cmds if " useradd " in f" {c} "]
    for flag in ("--system", "--no-create-home", "--shell /usr/sbin/nologin"):
        assert flag in useradd
    assert not re.search(r"\s(-G|-g|--groups|--gid|-m|--create-home)\b", useradd)
    assert not any(re.search(r"usermod|adduser|gpasswd|sudoers|visudo|setfacl", c) for c in cmds)
    chattr = [c for c in cmds if " chattr " in f" {c} "]
    immutable = {a for c in chattr if "-R" in c and "+i" in c for a in _args(c)[2:]}
    for command in cmds:
        tokens = shlex.split(command)
        if "chown" in tokens:
            paths = [t for t in tokens[tokens.index("chown") + 1 :] if not t.startswith("-")][1:]
            assert paths and set(paths) <= DATA_COPIES, command
            assert set(paths) <= immutable, command
        if "install" in tokens and "-o" in tokens and tokens[tokens.index("-o") + 1] == USER:
            assert tokens[-1] == "$root/tmp" and "0700" in tokens, command
        if "chmod" in tokens:
            mode = tokens[tokens.index("chmod") + 1 :]
            mode = [t for t in mode if t != "-R"][0]
            assert not re.search(r"[+=][a-zA-Z]*w", mode), command
            assert not re.fullmatch(r"[0-7]*[2367][0-7]?[2367]?", mode) or mode == "700"
            if re.fullmatch(r"[0-7]{3,4}", mode):
                assert mode[-2:] == "00" or int(mode[-2]) & 2 == 0 and int(mode[-1]) & 2 == 0
    # Nothing but the temporary directory is made writable for the account.
    assert not any(c for c in cmds if USER in c and re.search(r"-m 07[0-7][2367]", c))
    assert 'chmod 700 "$HOME"' in cmds
    loop = next(c for c in re.split(r"\n", run) if c.strip().startswith("for path in"))
    for var in ("$HOME", "$RUNNER_TEMP", "_runner_file_commands", "$GITHUB_WORKSPACE"):
        assert var in re.sub(r"\\\n\s*", " ", run[run.index(loop) :].split("; do")[0])
    body = run[run.index(loop) :].split("done")[0]
    assert f"sudo -n -u {USER} sh -c" in body and "exit 1" in body
    for name in ROOT_ONLY:
        assert not any(USER in c and name in c for c in cmds)


def check_gate(step: dict[str, Any], job_minutes: int) -> None:
    minutes = int(step["timeout-minutes"])
    assert minutes <= 20 < 30 and minutes < job_minutes
    run = str(step["run"])
    assert "|| true" not in run and "||true" not in run
    inner = re.search(r"timeout -k (\d+) (\d+) ", run)
    assert inner
    assert (int(inner.group(1)) + int(inner.group(2))) / 60 + 2 <= minutes
    assert 'TMPDIR="$root/tmp"' in run and 'HOME="$root/tmp"' in run
    lines = run.splitlines()
    loop_at = next(i for i, ln in enumerate(lines) if re.match(r'\s*while \[ "\$SECONDS" -lt', ln))
    deadline = re.search(r"SECONDS \+ (\d+)", run)
    assert deadline and int(deadline.group(1)) <= 60
    end_at = next(i for i, ln in enumerate(lines) if i > loop_at and ln.strip() == "done")
    loop = "\n".join(lines[loop_at:end_at])
    assert f"pkill -KILL -u {USER}" in loop and f"pgrep -u {USER}" in loop
    assert 'kill_rc" -gt 1' in loop and "exit 1" in loop and "alive_rc" in loop
    check = "\n".join(lines[end_at + 1 :])
    assert re.search(r'\[ "\$stopped" -ne 1 \]', check) and "exit 1" in check
    # The result is written once, only after the cleanup, from a runner-owned directory.
    writes = [i for i, ln in enumerate(lines) if "gate_rc" in ln and "printf" in ln]
    assert len(writes) == 1 and writes[0] > end_at + 1
    assert all("gate_rc" not in ln for ln in lines[:loop_at])
    assert 'RUNNER_TEMP/gate_rc/gate_rc"' in lines[writes[0]]
    assert "/srv/" not in lines[writes[0]]
    assert all(ln.strip() != "exit 0" for ln in lines)


def check(text: str) -> None:
    jobs = parse_yaml(text)["jobs"]
    analyze, publish = jobs["analyze"], jobs["publish"]
    assert int(analyze["timeout-minutes"]) <= 30 and int(publish["timeout-minutes"]) <= 10
    setup = _step(analyze["steps"], "useradd")
    assert int(setup["timeout-minutes"]) <= 20
    check_setup(str(setup["run"]))
    check_gate(_step(analyze["steps"], "pra1353_gate.py"), int(analyze["timeout-minutes"]))


def _edit(old: str, new: str, count: int = 1) -> str:
    text = TRUSTED.read_text(encoding="utf-8")
    assert old in text, old
    return text.replace(old, new, count)


def test_real_workflow() -> None:
    check(TRUSTED.read_text(encoding="utf-8"))


MUTATIONS: list[tuple[str, str]] = [
    ("--system --no-create-home", "--system --create-home"),
    (
        "            --shell /usr/sbin/nologin pra1353gate",
        "            --shell /bin/bash pra1353gate",
    ),
    ("--system --no-create-home", "--system -G sudo --no-create-home"),
    ('          sudo -n chattr -R +i "$root/trusted" "$root/head"\n', ""),
    (
        '          sudo -n chown -R pra1353gate "$root/trusted" "$root/head"',
        '          sudo -n chown -R pra1353gate "$root/trusted" "$root/head" "$root/venv"',
    ),
    (
        '          sudo -n chmod -R go-w,a+rX "$root/venv"',
        '          sudo -n chmod -R 777 "$root/venv"',
    ),
    (
        '          sudo -n chmod -R go-w,a+rX "$root/venv"',
        '          sudo -n chmod -R go+w "$root/venv"',
    ),
    ("install -d -m 0700 -o pra1353gate", "install -d -m 0755 -o pra1353gate"),
    ('          chmod 700 "$HOME"\n', ""),
    ('"$GITHUB_WORKSPACE" "$(dirname', '"$(dirname'),
    ("_runner_file_commands", "_x"),
    ("pkill -KILL -u pra1353gate || kill_rc=$?", "pkill -KILL -u pra1353gate || true"),
    ('while [ "$SECONDS" -lt "$deadline" ]; do', "for _ in 1; do"),
    ("deadline=$((SECONDS + 20))", "deadline=$((SECONDS + 9999))"),
    ('if [ "$stopped" -ne 1 ]; then', "if false; then"),
    ("timeout-minutes: 30", "timeout-minutes: 9999"),
    ("timeout-minutes: 5\n", "timeout-minutes: 9999\n"),
    ("timeout-minutes: 18", "timeout-minutes: 9999"),
    ("timeout -k 10 840", "timeout -k 10 1100"),
    ('HOME="$root/tmp" TMPDIR="$root/tmp"', 'HOME="$root" TMPDIR="$root/tmp"'),
    (
        'printf \'%s\' "$rc" > "$RUNNER_TEMP/gate_rc/gate_rc"',
        'printf \'%s\' "$rc" > "/srv/gate_rc"',
    ),
]


@pytest.mark.parametrize("old,new", MUTATIONS)
def test_breaking_a_rule_is_caught(old: str, new: str) -> None:
    with pytest.raises((AssertionError, KeyError, ValueError, StopIteration, IndexError)):
        check(_edit(old, new))


def test_result_is_written_before_cleanup_is_caught() -> None:
    text = _edit(
        "          stopped=0\n",
        '          printf \'%s\' "$rc" > "$RUNNER_TEMP/gate_rc/gate_rc"\n          stopped=0\n',
    )
    with pytest.raises(AssertionError):
        check(text)
