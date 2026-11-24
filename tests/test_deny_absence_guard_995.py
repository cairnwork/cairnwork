"""#995 step 2: no verdict reads the deny channel, and nothing reads a missing line as "clear".

Deny evidence proves a denial, never its absence (a busy host can lose the run's own line). The
list below is every place in ``src/`` that may touch it today, and for what. A new reader fails
here on purpose: ask ``Evidence.denial_of(operation, target)`` (DENIED or UNKNOWN) and add the
file to the list with a reason, in the same change that lets a verdict read the channel.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "cairnwork"
OWN = {"deny_channel.py", "deny_judge.py", "deny_sweep.py"}
# token -> files allowed to mention it, each a recorder, a carrier or a log line, never a verdict
ALLOWED = {
    r"\bdeny_denials\b": {"verifiers.py", "verifier_run.py"},  # carried on the run, not hashed
    r"\bdeny_evidence\b": {"verifiers.py", "verifier_run.py", "pipeline_judge.py"},  # logs only
    r"\.denied\(": set(),  # answer through denial_of, never the bool
    r"\bdeny_judge\b|\bdeny_channel\b": {
        "verifier_exec.py",  # tags the policy, records, returns the evidence
        "verifiers.py",
        "verifier_run.py",
        "pipeline_judge.py",
        "candidate_recognise_names.py",
    },
}
FIX = (
    "deny evidence proves a denial, never its absence (#995): read it only through "
    "Evidence.denial_of(operation, target) (DENIED or UNKNOWN) and add the file to ALLOWED here "
    "with the reason"
)


def _sources():
    return [p for p in sorted(SRC.rglob("*.py")) if p.name not in OWN]


def test_only_listed_files_touch_the_deny_channel():
    seen = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for token, allowed in ALLOWED.items():
            if re.search(token, text) and path.name not in allowed:
                seen.append(f"{path.relative_to(SRC.parent)}: {token}")
    assert not seen, f"{seen}: {FIX}"


def test_no_listed_file_branches_on_a_clean_deny_state():
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if not re.search(r"\bdeny_(channel|judge|evidence)\b", text):
            continue
        assert not re.search(r"""state\s*[!=]=\s*["']CLEAN["']""", text), f"{path.name}: {FIX}"


def test_the_guard_sees_a_reader_it_should_refuse(tmp_path):
    reader = "if evidence.state == 'CLEAN' and not evidence.denied(*t): ok()\n"
    assert re.search(r"\.denied\(", reader)
    assert re.search(r"""state\s*[!=]=\s*["']CLEAN["']""", reader)
    assert "denial_of" in FIX
