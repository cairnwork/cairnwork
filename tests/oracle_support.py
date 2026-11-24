"""A tiny project with a buggy and a fixed ``calc`` and helpers to run a probe against it."""

import json
from pathlib import Path

from experimental.oracle_observer.expected import load_expected
from experimental.oracle_observer.run import ProbeRun, judge, run_probe

BUGGY = "def total(items):\n    return sum(items) - 1\n\n\ndef boom():\n    raise KeyError('x')\n"
FIXED = "def total(items):\n    return sum(items)\n\n\ndef boom():\n    raise KeyError('x')\n"
PROBE = (
    "import calc\n"
    "observe('total', calc.total([100, 200, 300]))\n"
    "observe('names', ['alpha', 'beta'])\n"
    "observe_raises('boom', calc.boom)\n"
)
EXPECTED = {
    "values": {"total": 600, "names": ["alpha", "beta"]},
    "raises": {"boom": "builtins.KeyError"},
}


def expected_bytes(doc=None) -> bytes:
    return json.dumps(doc or EXPECTED).encode()


def lay(root: Path, files: dict, probe: str = PROBE) -> tuple[Path, Path]:
    """``(checkout, probe file)`` under ``root``; ``files`` are written into the checkout."""
    checkout = root / "work" / "checkout"
    checkout.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (checkout / name).parent.mkdir(parents=True, exist_ok=True)
        (checkout / name).write_text(text)
    probe_file = root / "probe_src.py"
    probe_file.write_text(probe)
    return checkout, probe_file


def run(root: Path, calc: str, probe: str = PROBE, *, extra=None, timeout=60) -> ProbeRun:
    checkout, probe_file = lay(root, {"calc.py": calc, **(extra or {})}, probe)
    work = root / "oracle"
    work.mkdir(parents=True, exist_ok=True)
    return run_probe(probe_file, checkout, work, timeout=timeout)


def verdict(root: Path, calc: str, probe: str = PROBE, **kwargs):
    return judge(run(root, calc, probe, **kwargs), load_expected(expected_bytes()))
